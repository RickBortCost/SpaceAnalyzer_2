using System.Collections.Concurrent;
using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.Data.Sqlite;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Live;

/// <summary>A frozen view of the archive: points up to a sequence watermark inside a time range.</summary>
public sealed record ArchiveReference(LiveArchive Archive, long Generation, long Sequence, IReadOnlyList<string> Ids, double Start, double End, long Count);

/// <summary>
/// Temporary original-sample history of the Live session (live_archive.LiveArchive): one SQLite
/// writer, independent WAL readers, bounded queue and disk usage. An incomplete cache is never
/// used as complete analysis input.
/// </summary>
public sealed class LiveArchive : IDisposable
{
    abstract record Item;
    sealed record Point(long Generation, long Seq, string Nid, LivePoint P, long Size) : Item;
    sealed record Reset(long Generation, ManualResetEventSlim Done) : Item;
    sealed record Barrier(ManualResetEventSlim Done) : Item;
    sealed record Stop : Item;

    readonly string directory;
    readonly long maxBytes, maxPendingBytes;
    readonly BlockingCollection<Item> queue;
    readonly object gate = new();
    public long Generation { get; private set; }
    long sequence, pendingBytes;
    public Dictionary<string, long> Counts { get; } = new();
    public string Error { get; private set; } = "";
    string fatal = "";
    public bool Active { get; private set; }
    Thread? thread;
    string? path;
    bool closed;

    /// <summary>
    /// Removes Live originals left by a previous run that did not close normally (each session can reach
    /// several GB). Safe at startup: the instance lock guarantees no other service uses this data folder.
    /// </summary>
    public static long RemoveStale(string directory)
    {
        long freed = 0;
        if (!Directory.Exists(directory)) return 0;
        foreach (var session in Directory.GetDirectories(directory, "session-*"))
        {
            try
            {
                freed += new DirectoryInfo(session).EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length);
                Directory.Delete(session, true);
            }
            catch (Exception exc) when (exc is IOException or UnauthorizedAccessException) { }
        }
        return freed;
    }

    public LiveArchive(string directory, int maxPending = 32768, long maxBytes = 8L * 1024 * 1024 * 1024, long maxPendingBytes = 32L * 1024 * 1024)
    {
        this.directory = directory; this.maxBytes = maxBytes; this.maxPendingBytes = maxPendingBytes;
        queue = new BlockingCollection<Item>(maxPending);
    }

    public long NewSession()
    {
        long generation;
        lock (gate)
        {
            Generation++; generation = Generation;
            sequence = 0; Counts.Clear(); Error = fatal;
            if (closed || fatal.Length > 0) return generation;
            if (thread == null)
            {
                try
                {
                    Directory.CreateDirectory(directory);
                    var session = Path.Combine(directory, "session-" + Guid.NewGuid().ToString("N")[..8]);
                    Directory.CreateDirectory(session);
                    path = Path.Combine(session, "originals.sqlite");
                    thread = new Thread(Run) { Name = "live-originals", IsBackground = true };
                    thread.Start();
                }
                catch (IOException exc) { Error = exc.Message; return generation; }
            }
            Active = true;
        }
        var done = new ManualResetEventSlim();
        queue.TryAdd(new Reset(generation, done), 10000);
        if (!done.Wait(10000)) Fail("Timeout inizializzazione storico Live");
        return generation;
    }

    void Fail(string message) { lock (gate) if (Error.Length == 0) Error = message; }

    public void SetError(string message) => Fail(message);

    /// <summary>Queues one original; returns its archive sequence, 0 when not archived.</summary>
    public long Append(long generation, string nid, LivePoint point)
    {
        lock (gate)
        {
            if (!Active || closed || generation != Generation || Error.Length > 0) return 0;
            var seq = sequence + 1;
            var size = 160 + 4L * ((point.Quality?.Length ?? 0) + (point.SourceTs?.Length ?? 0) + (point.ServerTs?.Length ?? 0) + (point.Text?.Length ?? 0)
                + (point.DataType?.Length ?? 0) + (point.Status?.Length ?? 0) + (point.LocalTs?.Length ?? 0) + (point.Filetime?.Length ?? 0));
            if (pendingBytes + size > maxPendingBytes || !queue.TryAdd(new Point(generation, seq, nid, point, size)))
            {
                Error = "Storico Live incompleto: coda di scrittura piena";
                return 0;
            }
            sequence = seq; Counts[nid] = Counts.GetValueOrDefault(nid) + 1; pendingBytes += size;
            return seq;
        }
    }

    /// <summary>
    /// Originals of one signal archived before a sequence, newest <paramref name="limit"/> at most, in time order
    /// (calculated revisions of one timestamp keep their last value). truncated: older originals were left out.
    /// </summary>
    public List<LivePoint> Before(string nid, long before, int limit, out bool truncated)
    {
        var reference = Reference([nid], double.NegativeInfinity, double.PositiveInfinity);
        using var c = Reader(reference);
        using var transaction = c.BeginTransaction();
        using var command = c.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = "SELECT seq,point FROM points WHERE generation=$g AND nid=$n AND seq<$b AND seq<=$s ORDER BY stamp DESC,seq DESC LIMIT $l";
        command.Parameters.AddWithValue("$g", reference.Generation); command.Parameters.AddWithValue("$n", nid);
        command.Parameters.AddWithValue("$b", before); command.Parameters.AddWithValue("$s", reference.Sequence);
        command.Parameters.AddWithValue("$l", limit + 1);
        var points = new List<LivePoint>();
        using (var reader = command.ExecuteReader())
            while (reader.Read()) points.Add(LivePoint.FromJson((JsonArray)Py.Parse(reader.GetString(1))!) with { Seq = reader.GetInt64(0) });
        truncated = points.Count > limit;
        if (truncated) points.RemoveAt(points.Count - 1);
        points.Reverse();
        if (nid.StartsWith("calc:", StringComparison.Ordinal))
            points = points.Where((p, i) => i + 1 == points.Count || points[i + 1].T != p.T).ToList();
        lock (gate) Check(reference.Generation);
        return points;
    }

    public ArchiveReference Reference(IEnumerable<string> ids, double start, double end)
    {
        lock (gate)
        {
            Check(Generation);
            var list = ids.ToList();
            return new ArchiveReference(this, Generation, sequence, list, start, end, list.Sum(n => Counts.GetValueOrDefault(n)));
        }
    }

    void Check(long generation)
    {
        if (Error.Length > 0) throw new UserError("Storico originale Live non disponibile: " + Error);
        if (!Active || closed || generation != Generation) throw new UserError("Acquisizione Live cambiata: riposizionare i marker o riprendere Play");
    }

    void Flush(long generation)
    {
        lock (gate) Check(generation);
        var done = new ManualResetEventSlim();
        queue.TryAdd(new Barrier(done), 10000);
        if (!done.Wait(10000)) throw new UserError("Storico Live occupato: riprovare il calcolo");
        lock (gate) Check(generation);
    }

    SqliteConnection Reader(ArchiveReference reference)
    {
        Flush(reference.Generation);
        var connection = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Mode = SqliteOpenMode.ReadOnly, DefaultTimeout = 10 }.ToString());
        connection.Open();
        using (var pragma = connection.CreateCommand()) { pragma.CommandText = "PRAGMA cache_size=-8192"; pragma.ExecuteNonQuery(); }
        return connection;
    }

    /// <summary>Original points in time order; calculated revisions of one timestamp keep their last value.</summary>
    public IEnumerable<(string nid, LivePoint p)> Points(ArchiveReference reference, IEnumerable<string> ids, double start, double end)
    {
        var wanted = ids.Where(reference.Ids.Contains).ToList();
        if (wanted.Count == 0) yield break;
        start = Math.Max(start, reference.Start); end = Math.Min(end, reference.End);
        using var c = Reader(reference);
        using var transaction = c.BeginTransaction();
        using var command = c.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = "SELECT nid,point FROM points WHERE generation=$g AND seq<=$s AND stamp>=$a AND stamp<=$b AND nid IN (" +
            string.Join(',', wanted.Select((_, i) => "$n" + i)) + ") ORDER BY stamp,seq";
        command.Parameters.AddWithValue("$g", reference.Generation); command.Parameters.AddWithValue("$s", reference.Sequence);
        command.Parameters.AddWithValue("$a", start); command.Parameters.AddWithValue("$b", end);
        for (var i = 0; i < wanted.Count; i++) command.Parameters.AddWithValue("$n" + i, wanted[i]);
        using var reader = command.ExecuteReader();
        double? stamp = null;
        var revisions = new Dictionary<string, LivePoint>();
        while (reader.Read())
        {
            var nid = reader.GetString(0);
            var p = LivePoint.FromJson((JsonArray)Py.Parse(reader.GetString(1))!);
            if (stamp != null && p.T != stamp)
            {
                foreach (var r in revisions) yield return (r.Key, r.Value);
                revisions.Clear();
            }
            stamp = p.T;
            if (nid.StartsWith("calc:", StringComparison.Ordinal)) revisions[nid] = p;
            else yield return (nid, p);
        }
        foreach (var r in revisions) yield return (r.Key, r.Value);
        lock (gate) Check(reference.Generation);
    }

    public JsonArray Markers(ArchiveReference reference, IEnumerable<string> ids, double x1, double x2)
    {
        var result = new JsonArray();
        using var c = Reader(reference);
        foreach (var nid in ids)
        {
            var row = new JsonObject { ["nodeid"] = nid, ["x1"] = null, ["x2"] = null };
            if (!reference.Ids.Contains(nid)) { result.Add(row); continue; }
            foreach (var (key, t) in new[] { ("x1", x1), ("x2", x2) })
            {
                var candidates = new List<LivePoint>();
                foreach (var (op, direction) in new[] { ("<=", "DESC"), (">=", "ASC") })
                {
                    using var command = c.CreateCommand();
                    command.CommandText = $"SELECT point FROM points WHERE generation=$g AND nid=$n AND seq<=$s AND stamp>=$a AND stamp<=$b AND stamp {op} $t ORDER BY stamp {direction},seq DESC LIMIT 1";
                    command.Parameters.AddWithValue("$g", reference.Generation); command.Parameters.AddWithValue("$n", nid);
                    command.Parameters.AddWithValue("$s", reference.Sequence); command.Parameters.AddWithValue("$a", reference.Start);
                    command.Parameters.AddWithValue("$b", reference.End); command.Parameters.AddWithValue("$t", t);
                    if (command.ExecuteScalar() is string hit) candidates.Add(LivePoint.FromJson((JsonArray)Py.Parse(hit)!));
                }
                if (candidates.Count == 0) continue;
                var p = candidates.MinBy(p => Math.Abs(p.T - t))!;
                var period = p.Period is double pp && pp != 0 ? pp : 500;
                if (Math.Abs(p.T - t) > 3 * Math.Max(1, period) || p.Quality != "Good" || p.Value == null) continue;
                row[key] = new JsonObject { ["timestamp_ms"] = p.T, ["value"] = p.Value };
            }
            result.Add(row);
        }
        return result;
    }

    void Run()
    {
        SqliteConnection? c = null;
        try
        {
            c = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Mode = SqliteOpenMode.ReadWriteCreate }.ToString());
            c.Open();
            Exec(c, "PRAGMA journal_mode=WAL"); Exec(c, "PRAGMA synchronous=NORMAL");
            Exec(c, "CREATE TABLE points(generation INTEGER,seq INTEGER,stamp REAL,nid TEXT,point TEXT)");
            Exec(c, "CREATE INDEX source_time ON points(nid,stamp,seq)");
            Exec(c, "CREATE INDEX time_order ON points(stamp,seq)");
            while (true)
            {
                var item = queue.Take();
                if (item is Stop) break;
                if (item is Reset reset)
                {
                    try { Exec(c, "DELETE FROM points"); Exec(c, "PRAGMA wal_checkpoint(TRUNCATE)"); }
                    catch (Exception exc) { Fail(exc.Message); }
                    finally { reset.Done.Set(); }
                    continue;
                }
                if (item is Barrier barrier) { barrier.Done.Set(); continue; }
                var batch = new List<Point> { (Point)item };
                // Control messages keep their FIFO position.
                while (batch.Count < 1024 && queue.TryTake(out var next))
                {
                    if (next is Point p) { batch.Add(p); continue; }
                    WriteBatch(c, batch); batch.Clear();
                    if (next is Stop) return;
                    if (next is Reset r2) { try { Exec(c, "DELETE FROM points"); Exec(c, "PRAGMA wal_checkpoint(TRUNCATE)"); } catch (Exception exc) { Fail(exc.Message); } finally { r2.Done.Set(); } }
                    if (next is Barrier b2) b2.Done.Set();
                    break;
                }
                if (batch.Count > 0) WriteBatch(c, batch);
            }
        }
        catch (Exception exc)
        {
            fatal = exc.Message;
            Fail(exc.Message);
            // Unblock control callers after a startup or storage failure.
            foreach (var item in queue.GetConsumingEnumerable())
            {
                if (item is Stop) break;
                if (item is Reset r) r.Done.Set();
                if (item is Barrier b) b.Done.Set();
            }
        }
        finally { c?.Dispose(); }
    }

    void WriteBatch(SqliteConnection c, List<Point> batch)
    {
        lock (gate) pendingBytes -= batch.Sum(p => p.Size);
        if (Error.Length > 0) return;
        using var transaction = c.BeginTransaction();
        try
        {
            using var command = c.CreateCommand();
            command.Transaction = transaction;
            command.CommandText = "INSERT INTO points VALUES($g,$s,$t,$n,$p)";
            var g = command.Parameters.Add("$g", SqliteType.Integer); var s = command.Parameters.Add("$s", SqliteType.Integer);
            var t = command.Parameters.Add("$t", SqliteType.Real); var n = command.Parameters.Add("$n", SqliteType.Text); var pp = command.Parameters.Add("$p", SqliteType.Text);
            var written = 0;
            foreach (var item in batch)
            {
                if (item.Generation != Generation) continue;
                if (!double.IsFinite(item.P.T)) throw new InvalidDataException("Timestamp originale non finito");
                var p = item.P.Value is double v && !double.IsFinite(v) ? item.P with { Value = null } : item.P;
                g.Value = item.Generation; s.Value = item.Seq; t.Value = p.T; n.Value = item.Nid; pp.Value = p.ToJson().ToJsonString(Py.Options);
                command.ExecuteNonQuery();
                written++;
            }
            transaction.Commit();
            if (written > 0 && new DirectoryInfo(Path.GetDirectoryName(path!)!).GetFiles().Sum(f => f.Length) > maxBytes)
                throw new InvalidDataException("Limite spazio storico Live raggiunto");
        }
        catch (Exception exc)
        {
            try { transaction.Rollback(); } catch (Exception) { }
            Fail(exc.Message);
        }
    }

    static void Exec(SqliteConnection c, string sql)
    {
        using var command = c.CreateCommand();
        command.CommandText = sql;
        command.ExecuteNonQuery();
    }

    public void Dispose()
    {
        if (closed) return;
        closed = true; Active = false;
        if (thread != null) { queue.TryAdd(new Stop(), 5000); thread.Join(10000); }
        SqliteConnection.ClearAllPools();
        if (path != null && thread is not { IsAlive: true })
            try { Directory.Delete(Path.GetDirectoryName(path)!, true); } catch (Exception) { }
    }
}

/// <summary>Bounded immutable references to original samples of paused Live charts (live_analysis.Snapshots).</summary>
public sealed class Snapshots(int maxItems = 8, long maxPoints = 2000000)
{
    readonly LinkedList<(string key, object value, long count)> items = new();
    readonly object gate = new();

    public (string key, long count) Create(Dictionary<string, List<LivePoint>> points)
    {
        var count = points.Values.Sum(v => (long)v.Count);
        if (count == 0) throw new UserError("Nessun campione originale disponibile per congelare questo grafico; riprendere Play e attendere dati");
        if (count > maxPoints) throw new UserError("Vista congelata troppo grande; ridurre segnali o buffer");
        lock (gate)
        {
            while (items.Count > 0 && (items.Count >= maxItems || items.Sum(i => i.count) + count > maxPoints)) items.RemoveFirst();
            var key = Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();
            items.AddLast((key, points, count));
            return (key, count);
        }
    }

    public (string key, long count) CreateReference(ArchiveReference reference)
    {
        lock (gate)
        {
            while (items.Count >= maxItems) items.RemoveFirst();
            var key = Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();
            items.AddLast((key, reference, 0));
            return (key, reference.Count);
        }
    }

    public object Get(string? key)
    {
        lock (gate)
            return items.FirstOrDefault(i => i.key == key).value
                ?? throw new UserError("Campioni della vista congelata non piu disponibili (riavvio o limite memoria). Riprendere Play e congelare nuovamente, oppure aprire la registrazione");
    }

    public void Delete(string? key)
    {
        lock (gate)
        {
            var node = items.First;
            while (node != null) { var next = node.Next; if (node.Value.key == key) items.Remove(node); node = next; }
        }
    }
}

/// <summary>live_archive.point_rows: Live points as recording-like rows for analysis and export.</summary>
public static class PointRows
{
    public static IEnumerable<Recordings.Row> Rows(IEnumerable<(string nid, LivePoint p)> points, double defaultPeriod, IReadOnlyDictionary<string, string>? names = null)
    {
        foreach (var (nid, p) in points)
        {
            var stamp = Engine.Core.Values.IsoMicro(Engine.Recording.TableRecorder.PythonFromTimestamp(p.T));
            var quality = p.Quality is { Length: > 0 } q ? q : p.Value != null ? "Good" : "Bad";
            yield return new Recordings.Row
            {
                ["timestamp_ms"] = p.T, ["timestamp_utc"] = stamp, ["node_id"] = nid, ["signal_name"] = names?.GetValueOrDefault(nid) ?? nid,
                ["value_numeric"] = p.Value, ["value_text"] = p.Text, ["data_type"] = p.DataType, ["good"] = quality == "Good", ["quality"] = quality,
                ["status_code"] = p.StatusCode, ["status"] = p.Status is { Length: > 0 } s ? s : quality, ["source_timestamp"] = p.SourceTs,
                ["server_timestamp"] = p.ServerTs, ["sample_ms"] = p.Period is double period && period != 0 ? period : defaultPeriod,
                ["local_timestamp"] = p.LocalTs is { Length: > 0 } l ? l : stamp, ["ads_filetime"] = p.Filetime,
            };
        }
    }
}
