using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Runtime;

/// <summary>
/// One acquiring session plus detached finalizers (recording_manager.py) over the engine's
/// RecordingSession: primary capture first, selected format best effort.
/// </summary>
public sealed class RecordingManager(string directory, string version, Action<string, Action<Utf8JsonWriter>> emit, RecordGate gate)
{
    readonly object sync = new();
    readonly List<(string id, object record)> sessions = new();   // RecordingSession or a final status dictionary
    RecordingSession? active;
    string? latestId;
    bool starting, closed;
    long revision;
    const int MaxPending = 32;

    public string Directory { get; } = Path.GetFullPath(directory);

    public bool Acquiring
    {
        get { lock (sync) return starting || active is { Acquiring: true }; }
    }

    public bool Busy
    {
        get
        {
            lock (sync) return starting || sessions.Any(s => s.record is RecordingSession r && r.Busy);
        }
    }

    public RecordingSession? Active { get { lock (sync) return active; } }

    public Dictionary<string, object?> Start(string format, IReadOnlyList<NodeSpec> nodes, string endpoint, int sampleMs, JsonElement options,
        IReadOnlyDictionary<string, string> connections, bool arm = true)
    {
        lock (sync)
        {
            if (closed) throw new UserError("Servizio registrazione in chiusura");
            if (starting || active is { Acquiring: true }) throw new UserError("Una registrazione è già in corso");
            if (sessions.Count(s => s.record is RecordingSession r && r.Busy) >= MaxPending) throw new UserError("Troppi salvataggi in attesa: attendere il completamento di un file");
            starting = true;
        }
        try
        {
            var session = new RecordingSession(Directory, version, emit);
            try { session.Start(format, nodes, endpoint, sampleMs, options, connections); }
            catch (ArgumentException exc) { throw new UserError(exc.Message); }
            catch (InvalidOperationException exc) { throw new UserError(exc.Message); }
            var ident = Guid.NewGuid().ToString("N");
            lock (sync)
            {
                sessions.Add((ident, session));
                active = session;
                latestId = ident;
                revision++;
            }
            if (arm) gate.Arm(session);
        }
        finally { lock (sync) starting = false; }
        return Status();
    }

    public bool Enqueue(double t, List<Sample> rows, int period)
    {
        RecordingSession? rec;
        lock (sync) rec = active;
        return rec != null && rec.Enqueue(t, rows, period);
    }

    public bool Annotate(Dictionary<string, object?> evt)
    {
        RecordingSession? rec;
        lock (sync) rec = active;
        if (rec == null) return false;
        var details = evt["details"] as Dictionary<string, object?> ?? new();
        return rec.Annotate((string)evt["kind"]!, w =>
        {
            foreach (var (k, v) in details) { w.WritePropertyName(k); JsonOut.Write(w, v); }
            w.WritePropertyName("connection_id"); JsonOut.Write(w, evt["connection_id"]);
            w.WritePropertyName("at_ns"); JsonOut.Write(w, evt["at_ns"]);
        });
    }

    public void Fail(string message, string reason = "source_acquisition_error")
    {
        RecordingSession? rec;
        lock (sync) rec = active;
        rec?.Fail(message, reason);
    }

    public void SourceDiagnostics(object? diagnostics)
    {
        RecordingSession? rec;
        lock (sync) rec = active;
        if (rec == null) return;
        using var doc = JsonDocument.Parse(SacFormat.Json(w => JsonOut.Write(w, diagnostics)));
        rec.SourceDiagnostics(doc.RootElement);
    }

    public Dictionary<string, object?> Stop(string reason = "user", double timeout = 5)
    {
        RecordingSession? rec;
        lock (sync) { rec = active; active = null; }
        if (rec != null)
        {
            gate.Fence();
            gate.Disarm(rec);
            lock (sync) revision++;
            rec.Stop(reason, TimeSpan.Zero);
        }
        if (timeout > 0)
        {
            var deadline = DateTime.UtcNow.AddSeconds(timeout);
            while (Busy && DateTime.UtcNow < deadline) Thread.Sleep(10);
        }
        return Status();
    }

    static Dictionary<string, object?> SessionStatus(RecordingSession session) =>
        (Dictionary<string, object?>)Py.Plain(JsonNode.Parse(SacFormat.Json(session.WriteStatus)))!;

    public Dictionary<string, object?> Status()
    {
        List<(string id, object record)> items;
        RecordingSession? current;
        string? latest;
        long rev;
        bool start;
        lock (sync) { items = sessions.ToList(); current = active; latest = latestId; rev = revision; start = starting; }
        var jobs = new List<Dictionary<string, object?>>();
        var finished = new Dictionary<string, Dictionary<string, object?>>();
        Dictionary<string, object?>? result = null;
        long pending = 0;
        foreach (var (ident, record) in items)
        {
            Dictionary<string, object?> state;
            if (record is Dictionary<string, object?> saved) state = new(saved);
            else
            {
                var rec = (RecordingSession)record;
                state = SessionStatus(rec);
                state["recording_id"] = ident;
                if (state["busy"] is false && rec != current) finished[ident] = state;
            }
            if (ident == latest) result = new(state);
            if (record != current || current is not { Acquiring: true })
            {
                pending += state["busy"] is true ? 1 : 0;
                jobs.Add(state);
            }
        }
        lock (sync)
        {
            for (var i = 0; i < sessions.Count; i++)
                if (finished.TryGetValue(sessions[i].id, out var state) && sessions[i].record != active) sessions[i] = (sessions[i].id, state);
            var removable = sessions.Where(s => s.record is Dictionary<string, object?>).Select(s => s.id).ToList();
            foreach (var ident in removable.Take(Math.Max(0, removable.Count - 64))) sessions.RemoveAll(s => s.id == ident);
        }
        result ??= new()
        {
            ["state"] = "idle", ["active"] = false, ["busy"] = false, ["format"] = "", ["filename"] = "", ["path"] = "", ["error"] = "",
            ["rows_written"] = 0L, ["files"] = new List<object?>(), ["nodes"] = new List<object?>(), ["directory"] = Directory,
        };
        var locked = start || current is { Acquiring: true };
        result["revision"] = rev;
        result["selection_locked"] = locked;
        result["can_start"] = !locked && !closed;
        result["busy"] = locked || pending > 0;
        result["finalizing_count"] = pending;
        result["finalizations"] = jobs.Where(j => j["busy"] is true).Concat(jobs.Where(j => j["busy"] is not true).TakeLast(16))
            .OrderBy(j => j.TryGetValue("started_at", out var s) && s is string text ? text : "", StringComparer.Ordinal).Cast<object?>().ToList();
        return result;
    }

    public void Close()
    {
        List<RecordingSession> records;
        lock (sync) { closed = true; records = sessions.Select(s => s.record).OfType<RecordingSession>().ToList(); }
        Stop("application_close", 0);
        foreach (var rec in records)
        {
            var deadline = DateTime.UtcNow.AddSeconds(60);
            while (rec.Busy && DateTime.UtcNow < deadline) rec.Stop("application_close", TimeSpan.FromMilliseconds(250));
        }
    }
}
