using System.Reflection;
using System.Text.Json;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Ipc;
using SpaceAnalyzer.Engine.Recording;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine;

/// <summary>
/// Request dispatcher. Every request is answered; failures carry the message the UI shows.
/// Recording requests are handled in arrival order; source and catalog requests run concurrently.
/// </summary>
public sealed class EngineService
{
    readonly Host host;
    readonly Dictionary<string, RecordingSession> sessions = new();
    readonly object gate = new();
    readonly AcquisitionEngine acquisition;
    readonly RecordGate recordGate = new();
    readonly PythonChannel channel;
    readonly SourceController sources;
    readonly SemaphoreSlim catalogLock = new(1, 1);
    volatile bool running = true;

    public static string Version => typeof(EngineService).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0] ?? "0";

    public EngineService(Host host)
    {
        this.host = host;
        acquisition = new AcquisitionEngine(host.Emit);
        channel = new PythonChannel(host, host.Emit);
        acquisition.Record = recordGate.Record;
        acquisition.Forward = channel.Put;
        acquisition.Fail = recordGate.Fail;
        sources = new SourceController(acquisition, host.Emit, recordGate);
    }

    public int Run()
    {
        while (running)
        {
            var frame = host.Read();
            if (frame == null) break;
            var (type, payload) = frame.Value;
            if (type != Host.Request) continue;
            var document = JsonDocument.Parse(payload);
            var request = document.RootElement;
            var id = request.GetProperty("id").GetInt64();
            var op = request.GetProperty("op").GetString() ?? "";
            var args = request.TryGetProperty("args", out var a) ? a : default;
            if (op.StartsWith("record.", StringComparison.Ordinal) || op == "shutdown" || op == "hello")
            {
                Respond(id, () => Task.FromResult(DispatchRecording(op, args))).GetAwaiter().GetResult();
                document.Dispose();
            }
            else
            {
                _ = Task.Run(async () =>
                {
                    try { await Respond(id, () => DispatchAsync(op, args)); }
                    finally { document.Dispose(); }
                });
            }
        }
        Shutdown();
        return 0;
    }

    async Task Respond(long id, Func<Task<object?>> work)
    {
        try
        {
            var result = await work();
            host.Send(Host.Response, w =>
            {
                w.WriteStartObject();
                w.WriteNumber("id", id);
                w.WriteBoolean("ok", true);
                w.WritePropertyName("result");
                JsonOut.Write(w, result);
                w.WriteEndObject();
            });
        }
        catch (Exception exc)
        {
            var inner = exc is TargetInvocationException { InnerException: { } i } ? i : exc is AggregateException { InnerException: { } j } ? j : exc;
            host.Send(Host.Response, w =>
            {
                w.WriteStartObject();
                w.WriteNumber("id", id);
                w.WriteBoolean("ok", false);
                w.WriteString("error", inner.Message);
                w.WriteString("type", inner.GetType().Name);
                w.WriteEndObject();
            });
        }
    }

    object? DispatchRecording(string op, JsonElement args)
    {
        switch (op)
        {
            case "hello":
                return new Dictionary<string, object?> { ["engine"] = "SpaceAnalyzer.Engine", ["version"] = Version, ["runtime"] = Environment.Version.ToString(), ["pid"] = Environment.ProcessId };
            case "record.start":
            {
                var arm = args.TryGetProperty("arm", out var armed) && armed.ValueKind == JsonValueKind.True;
                var session = new RecordingSession(args.GetProperty("directory").GetString()!, args.GetProperty("version").GetString()!, Emit);
                var nodes = args.GetProperty("nodes").EnumerateArray().Select(NodeSpec.FromJson).ToList();
                var connections = args.TryGetProperty("connections", out var c) && c.ValueKind == JsonValueKind.Object
                    ? c.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetString() ?? "") : new Dictionary<string, string>();
                if (arm)
                {
                    // Originals of a previous failed session never enter the new one.
                    acquisition.Flush(TimeSpan.FromSeconds(5));
                    acquisition.ResetFailed();
                    foreach (var protocol in nodes.Select(n => Sample.ProtocolOf(n.NodeId)).Where(p => p != "calculated").Distinct())
                        acquisition.Connection(protocol, protocol);
                    foreach (var (protocol, identity) in acquisition.Catalog()) connections[protocol] = identity;
                    channel.ResetSkip();
                }
                session.Start(args.GetProperty("format").GetString()!.ToLowerInvariant(), nodes, args.GetProperty("endpoint").GetString() ?? "",
                    args.GetProperty("sample_ms").GetInt32(), args.TryGetProperty("options", out var o) ? o : default, connections);
                lock (gate) sessions[session.Id] = session;
                if (arm) recordGate.Arm(session);
                return Status(session);
            }
            case "record.enqueue":
            {
                var session = Session(args);
                var rows = args.GetProperty("rows").EnumerateArray().Select(Sample.FromPython).ToList();
                return session.Enqueue(args.GetProperty("t").GetDouble(), rows, args.GetProperty("period").GetInt32());
            }
            case "record.stop":
            {
                var session = Session(args);
                recordGate.Fence();
                recordGate.Disarm(session);
                session.Stop(args.TryGetProperty("reason", out var r) ? r.GetString() ?? "user" : "user",
                    TimeSpan.FromSeconds(args.TryGetProperty("timeout", out var t) ? t.GetDouble() : 0));
                return Status(session);
            }
            case "record.fail":
                Session(args).Fail(args.GetProperty("message").GetString() ?? "", args.TryGetProperty("reason", out var reason) ? reason.GetString() ?? "source_acquisition_error" : "source_acquisition_error");
                return true;
            case "record.diagnostics":
                Session(args).SourceDiagnostics(args.GetProperty("diagnostics"));
                return true;
            case "record.annotate":
            {
                var details = args.GetProperty("details");
                return Session(args).Annotate(args.GetProperty("kind").GetString() ?? "event", w => { foreach (var p in details.EnumerateObject()) p.WriteTo(w); });
            }
            case "record.status":
                return Status(Session(args));
            case "record.release":
                lock (gate) sessions.Remove(args.GetProperty("session").GetString()!);
                return true;
            case "shutdown":
                running = false;
                return true;
            default:
                throw new ArgumentException("Operazione motore sconosciuta: " + op);
        }
    }

    async Task<object?> DispatchAsync(string op, JsonElement args)
    {
        switch (op)
        {
            case "acq.connect":
            {
                var cfg = args.GetProperty("cfg").Clone();
                var protocols = args.GetProperty("protocols").EnumerateArray().Select(p => p.GetString()!).ToList();
                var waitFirst = args.TryGetProperty("wait_first", out var w) && w.ValueKind == JsonValueKind.True;
                var error = await sources.Start(cfg, protocols, waitFirst);
                if (error != null) throw new SourceException(error);
                return Status();
            }
            case "acq.disconnect":
                await sources.Stop();
                return Status();
            case "acq.select":
                sources.Select(args.GetProperty("ids").EnumerateArray().Select(i => i.GetString()!).ToList(), args.GetProperty("period").GetInt32(), ParseGeneration(args));
                return true;
            case "acq.control":
                await sources.Control(args.GetProperty("protocol").GetString()!, args.GetProperty("action").GetString()!);
                return Status();
            case "acq.status":
                return Status();
            case "acq.fence":
            {
                // Stop fence: later arrivals belong to no recording; drain everything already received.
                recordGate.Fence();
                var timeout = TimeSpan.FromSeconds(args.TryGetProperty("timeout", out var t) ? t.GetDouble() : 5);
                var ok = await Task.Run(() => sources.Flush(timeout));
                var session = recordGate.Session;
                if (session != null)
                {
                    var diagnostics = SacFormat.Json(w => JsonOut.Write(w, sources.Status()));
                    using var doc = JsonDocument.Parse(diagnostics);
                    session.SourceDiagnostics(doc.RootElement);
                }
                var delivered = await Task.Run(() => channel.Flush(timeout));
                return new Dictionary<string, object?>
                {
                    ["ok"] = ok, ["delivered"] = delivered, ["raw_last_t"] = recordGate.RawLastT,
                    ["acquisition"] = acquisition.Status(), ["sources"] = sources.Status(),
                };
            }
            case "acq.reset_failed":
                acquisition.ResetFailed();
                return true;
            case "catalog.inspect":
            case "catalog.browse":
            case "catalog.scan":
                return await Catalog(op[8..], args);
            case "source.test":
            {
                var protocol = args.GetProperty("protocol").GetString()!;
                var cfg = args.GetProperty("cfg").Clone();
                await using var driver = DriverFactory.Create(protocol, cfg);
                using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(30));
                await driver.ConnectAsync(cancel.Token);
                await driver.ReadStateAsync(cancel.Token);
                return new Dictionary<string, object?>(driver.Info);
            }
            default:
                throw new ArgumentException("Operazione motore sconosciuta: " + op);
        }
    }

    async Task<object?> Catalog(string operation, JsonElement args)
    {
        var protocol = args.GetProperty("protocol").GetString()!;
        var nodeId = args.GetProperty("nodeid").GetString()!;
        var separate = args.TryGetProperty("separate", out var s) && s.ValueKind == JsonValueKind.True;
        using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(args.TryGetProperty("timeout", out var t) ? t.GetDouble() : 120));
        async Task<object?> Apply(SourceDriver driver) => operation switch
        {
            "inspect" => await driver.InspectAsync(nodeId, cancel.Token),
            "browse" => await driver.BrowseAsync(nodeId, cancel.Token),
            _ => await driver.ScanItemsAsync(nodeId, cancel.Token),
        };
        if (!separate) return await Apply(sources.Client(protocol));
        // Symbol upload has its own transport and never serializes acquisition reads.
        var cfg = args.TryGetProperty("cfg", out var c) ? c.Clone() : sources.Sessions[protocol].Cfg;
        await catalogLock.WaitAsync(cancel.Token);
        try
        {
            await using var driver = DriverFactory.Create(protocol, cfg, catalog: true);
            await driver.ConnectAsync(cancel.Token);
            return await Apply(driver);
        }
        finally { catalogLock.Release(); }
    }

    static Generation? ParseGeneration(JsonElement args) =>
        args.TryGetProperty("generation", out var g) && g.ValueKind == JsonValueKind.Array && g.GetArrayLength() == 2
            && g[0].ValueKind == JsonValueKind.Number && g[1].ValueKind == JsonValueKind.Number
            ? new Generation(g[0].GetInt64(), g[1].GetInt64()) : null;

    Dictionary<string, object?> Status() => new()
    {
        ["summary"] = sources.Summary(),
        ["sources"] = sources.Status(),
        ["acquisition"] = acquisition.Status(),
        ["live"] = channel.Status(),
        ["selection"] = sources.CurrentSelection.Ids.Count,
    };

    RecordingSession Session(JsonElement args)
    {
        lock (gate)
            return sessions.TryGetValue(args.GetProperty("session").GetString() ?? "", out var session)
                ? session : throw new ArgumentException("Sessione di registrazione sconosciuta");
    }

    static Action<Utf8JsonWriter> Status(RecordingSession session) => w =>
    {
        using var doc = JsonDocument.Parse(SacFormat.Json(session.WriteStatus));
        w.WriteStartObject();
        foreach (var property in doc.RootElement.EnumerateObject()) property.WriteTo(w);
        w.WriteString("engine_session", session.Id);
        w.WriteEndObject();
    };

    void Emit(string kind, Action<Utf8JsonWriter> details) => host.Emit(kind, null, details);

    void Shutdown()
    {
        try { sources.Stop().Wait(TimeSpan.FromSeconds(15)); } catch (Exception) { }
        acquisition.Close();
        channel.Close();
        List<RecordingSession> open;
        lock (gate) open = sessions.Values.ToList();
        foreach (var session in open) session.Stop("application_close", TimeSpan.FromSeconds(30));
    }
}
