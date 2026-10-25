using System.Text.Json;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Sources;

/// <summary>
/// All protocol sessions of one connection: they share only the selection and the
/// acquisition engine. Healthy sources stay usable while a failed one retries.
/// </summary>
public sealed class SourceController
{
    public static readonly string[] Protocols = ["opcua", "ads", "mqtt", "s7"];

    readonly AcquisitionEngine acquisition;
    readonly EventSink events;
    readonly RecordGate gate;
    readonly object sync = new();
    Dictionary<string, SourceSession> sessions = new();
    Selection selection = new(Array.Empty<string>(), 500, null);
    long stateSerial;

    public SourceController(AcquisitionEngine acquisition, EventSink events, RecordGate gate)
    {
        this.acquisition = acquisition; this.events = events; this.gate = gate;
        gate.CurrentRevision = () => CurrentSelection.Generation?.Revision ?? 0;
    }

    public Selection CurrentSelection { get { lock (sync) return selection; } }
    public bool Multi { get { lock (sync) return sessions.Count > 1; } }
    public bool WaitFirst { get; private set; }
    public IReadOnlyDictionary<string, SourceSession> Sessions { get { lock (sync) return new Dictionary<string, SourceSession>(sessions); } }
    public bool Enabled { get { lock (sync) return sessions.Count > 0; } }

    public void Select(IReadOnlyList<string> ids, int period, Generation? generation)
    {
        lock (sync) selection = new Selection(ids.ToList(), period, generation);
    }

    /// <summary>
    /// Start one session per protocol. With a single source the first connection error is
    /// returned to the user (as the 2.x connect); several sources keep retrying independently.
    /// </summary>
    public async Task<string?> Start(JsonElement cfg, IReadOnlyList<string> protocols, bool waitFirst)
    {
        await Stop();
        WaitFirst = waitFirst;
        Dictionary<string, SourceSession> created;
        lock (sync)
        {
            created = Protocols.Where(protocols.Contains).ToDictionary(p => p, p => new SourceSession(this, p, cfg, c => DriverFactory.Create(p, c)));
            sessions = created;
        }
        var first = created.Values.Select(s => s.Start()).ToList();
        if (!waitFirst) { StateChanged(); return null; }
        var results = await Task.WhenAll(first);
        var failure = results.FirstOrDefault(e => e != null);
        if (failure != null) { await Stop(); return failure.Message.Length > 0 ? failure.Message : failure.GetType().Name; }
        return null;
    }

    public async Task Stop()
    {
        List<SourceSession> current;
        lock (sync) { current = sessions.Values.ToList(); sessions = new(); }
        await Task.WhenAll(current.Select(s => s.Stop()));
        StateChanged();
    }

    /// <summary>Connect or disconnect one protocol of a running multi-source connection.</summary>
    public async Task Control(string protocol, string action)
    {
        SourceSession? session;
        lock (sync) sessions.TryGetValue(protocol, out session);
        if (session == null || action is not ("connect" or "disconnect")) throw new ArgumentException("Sorgente o comando non valido");
        await session.Stop();
        var s = session.Selection();
        var now = Clock.UtcNs() / 1e6;
        var rows = s.Ids.Select(n => new Sample { NodeId = n, Value = null, PlotValue = null, Good = false, Quality = "Bad", Status = "Disconnected", DataType = "Null" }).ToList();
        if (rows.Count > 0) Sink(protocol, now, rows, s.Period, s.Generation, 0);
        if (action == "connect")
        {
            var replacement = new SourceSession(this, protocol, session.Cfg, c => DriverFactory.Create(protocol, c));
            lock (sync) sessions[protocol] = replacement;
            _ = replacement.Start();
        }
        StateChanged();
    }

    public SourceDriver Create(string protocol, JsonElement cfg) => DriverFactory.Create(protocol, cfg);

    public bool Sink(string protocol, double t, List<Sample> rows, int period, Generation? generation, double backpressure) =>
        acquisition.Submit(protocol, protocol, t, rows, period, generation, backpressure);

    public void Emit(string kind, Action<Utf8JsonWriter> details) => events(kind, null, details);

    public void EmitFor(string protocol, string kind, Action<Utf8JsonWriter> details) => events(kind, acquisition.ConnectionId(protocol), details);

    public void Fatal(string message) => gate.Fail(message);

    public void StateChanged()
    {
        var serial = Interlocked.Increment(ref stateSerial);
        var summary = Summary();
        events("sources_state", null, w =>
        {
            w.WriteNumber("serial", serial);
            w.WritePropertyName("summary"); JsonOut.Write(w, summary);
        });
    }

    /// <summary>A source without automatic reconnection went down: the UI closes the recording.</summary>
    public void ConnectionLost(SourceSession session)
    {
        var anyConnected = Sessions.Values.Any(s => s != session && s.State == "connected");
        events("source_stopped", null, w => { w.WriteString("protocol", session.Protocol); w.WriteBoolean("others_connected", anyConnected); });
    }

    public Dictionary<string, object?> Summary()
    {
        var current = Sessions.Values.ToList();
        var connected = current.Any(s => s.State == "connected");
        return new()
        {
            ["enabled"] = current.Count > 0,
            ["connected"] = connected,
            ["state"] = connected ? "connected" : current.Any(s => s.State is "connecting" or "reconnecting") ? "reconnecting" : "disconnected",
            ["error"] = string.Join(" · ", current.Where(s => s.Error.Length > 0).Select(s => s.Protocol.ToUpperInvariant() + ": " + s.Error)),
            ["protocols"] = current.ToDictionary(s => s.Protocol, s => (object?)new Dictionary<string, object?> { ["state"] = s.State, ["error"] = s.Error, ["mode"] = s.Mode }),
        };
    }

    public Dictionary<string, object?> Status() => Sessions.ToDictionary(p => p.Key, p => (object?)p.Value.Status());

    /// <summary>Stop fence: drain every client queue, then the acquisition engine.</summary>
    public bool Flush(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        var ok = true;
        foreach (var session in Sessions.Values)
            ok &= session.Flush(Remaining(deadline));
        if (!acquisition.Flush(Remaining(deadline))) { Fatal("Coda Acquisition Engine non drenata alla chiusura"); ok = false; }
        return ok;
    }

    static TimeSpan Remaining(DateTime deadline) => deadline > DateTime.UtcNow ? deadline - DateTime.UtcNow : TimeSpan.Zero;

    public SourceDriver Client(string protocol)
    {
        SourceSession? session;
        lock (sync) sessions.TryGetValue(protocol, out session);
        if (session?.Driver is not { } driver || session.State != "connected") throw new SourceException(protocol.ToUpperInvariant() + ": sorgente non connessa");
        return driver;
    }
}
