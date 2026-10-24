using System.Collections.Concurrent;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Infrastructure;

/// <summary>Bounded diagnostics and persistent critical events (acquisition_health.EventStore).</summary>
public sealed class EventStore
{
    readonly string path;
    readonly BlockingCollection<Dictionary<string, object?>?> queue;
    readonly object gate = new();
    readonly LinkedList<Dictionary<string, object?>> recent = new();
    Thread? thread;
    bool closed;
    long reportedOverflow;

    public string Error { get; private set; } = "";
    public long Overflow { get; private set; }
    /// <summary>Optional tee of every event (the acquiring primary capture); must not block.</summary>
    public Action<Dictionary<string, object?>>? Listener { get; set; }
    public string ListenerError { get; private set; } = "";

    public EventStore(string path, int capacity = 2048)
    {
        this.path = path;
        queue = new BlockingCollection<Dictionary<string, object?>?>(capacity);
    }

    public bool Emit(string kind, string? connectionId = null, Dictionary<string, object?>? details = null)
    {
        var evt = new Dictionary<string, object?>
        {
            ["at_ns"] = Clock.UtcNs().ToString(System.Globalization.CultureInfo.InvariantCulture), ["kind"] = kind,
            ["connection_id"] = connectionId, ["details"] = details ?? new Dictionary<string, object?>(),
        };
        try { Listener?.Invoke(evt); }
        catch (Exception exc) { ListenerError = exc.Message; }
        lock (gate)
        {
            recent.AddLast(evt);
            if (recent.Count > 256) recent.RemoveFirst();
            if (closed) return false;
            if (thread == null)
            {
                thread = new Thread(Run) { Name = "acquisition-events", IsBackground = true };
                thread.Start();
            }
            if (queue.TryAdd(evt)) return true;
            Overflow++;
            return false;
        }
    }

    /// <summary>Engine events carry their details as a JSON writer callback.</summary>
    public void EmitEngine(string kind, string? connectionId, Action<Utf8JsonWriter> details)
    {
        var json = Engine.Recording.SacFormat.Json(w => { w.WriteStartObject(); details(w); w.WriteEndObject(); });
        var plain = Py.Plain(System.Text.Json.Nodes.JsonNode.Parse(json)) as Dictionary<string, object?>;
        Emit(kind, connectionId, plain);
    }

    void Run()
    {
        SqliteConnection? db = null;
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            db = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Pooling = false }.ToString());
            db.Open();
            Exec(db, "PRAGMA journal_mode=WAL");
            Exec(db, "PRAGMA synchronous=FULL");
            Exec(db, "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at_ns TEXT,kind TEXT,connection_id TEXT,details TEXT)");
            while (true)
            {
                var item = queue.Take();
                if (item == null) break;
                var batch = new List<Dictionary<string, object?>> { item };
                var stop = false;
                while (batch.Count < 128 && queue.TryTake(out var next))
                {
                    if (next == null) { stop = true; break; }
                    batch.Add(next);
                }
                long overflow;
                lock (gate) overflow = Overflow;
                if (overflow > reportedOverflow)
                {
                    batch.Add(new()
                    {
                        ["at_ns"] = Clock.UtcNs().ToString(System.Globalization.CultureInfo.InvariantCulture), ["kind"] = "event_queue_overflow", ["connection_id"] = null,
                        ["details"] = new Dictionary<string, object?> { ["lost_events"] = overflow - reportedOverflow, ["total_lost_events"] = overflow },
                    });
                    reportedOverflow = overflow;
                }
                using (var transaction = db.BeginTransaction())
                {
                    foreach (var e in batch)
                    {
                        using var command = db.CreateCommand();
                        command.CommandText = "INSERT INTO events(at_ns,kind,connection_id,details) VALUES($a,$k,$c,$d)";
                        command.Parameters.AddWithValue("$a", e["at_ns"]);
                        command.Parameters.AddWithValue("$k", e["kind"]);
                        command.Parameters.AddWithValue("$c", (object?)e["connection_id"] ?? DBNull.Value);
                        command.Parameters.AddWithValue("$d", Py.DumpsPlain(e["details"]));
                        command.ExecuteNonQuery();
                    }
                    transaction.Commit();
                }
                if (stop) break;
            }
        }
        catch (Exception exc) { Error = exc.Message; }
        finally { db?.Dispose(); }
    }

    static void Exec(SqliteConnection db, string sql)
    {
        using var command = db.CreateCommand();
        command.CommandText = sql;
        command.ExecuteNonQuery();
    }

    public List<object?> History(object? limitValue)
    {
        var limit = 100;
        if (limitValue is string s)
            limit = int.TryParse(s.Trim(), out var parsed) ? parsed : throw new UserError($"invalid literal for int() with base 10: '{s}'");
        limit = Math.Max(1, Math.Min(1000, limit));
        if (File.Exists(path))
        {
            try
            {
                using var db = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Mode = SqliteOpenMode.ReadOnly, Pooling = false, DefaultTimeout = 1 }.ToString());
                db.Open();
                using var command = db.CreateCommand();
                command.CommandText = "SELECT id,at_ns,kind,connection_id,details FROM events ORDER BY id DESC LIMIT $l";
                command.Parameters.AddWithValue("$l", limit);
                using var reader = command.ExecuteReader();
                var result = new List<object?>();
                while (reader.Read())
                    result.Add(new Dictionary<string, object?>
                    {
                        ["id"] = reader.GetInt64(0), ["at_ns"] = reader.IsDBNull(1) ? null : reader.GetString(1), ["kind"] = reader.IsDBNull(2) ? null : reader.GetString(2),
                        ["connection_id"] = reader.IsDBNull(3) ? null : reader.GetString(3),
                        ["details"] = reader.IsDBNull(4) ? null : Py.Plain(System.Text.Json.Nodes.JsonNode.Parse(reader.GetString(4))),
                    });
                return result;
            }
            catch (SqliteException) { }
        }
        lock (gate) return recent.Reverse().Take(limit).Cast<object?>().ToList();
    }

    public void Close()
    {
        Thread? t;
        lock (gate) { closed = true; t = thread; }
        if (t != null && t.IsAlive)
        {
            if (!queue.TryAdd(null, 5000)) Error = Error.Length > 0 ? Error : "Timeout chiusura eventi: coda non drenata";
            t.Join(5000);
        }
    }
}
