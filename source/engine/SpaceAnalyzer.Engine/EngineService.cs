using System.Reflection;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Ipc;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.Engine;

/// <summary>Request dispatcher. Every request is answered; failures carry the message the UI shows.</summary>
public sealed class EngineService
{
    readonly Host host;
    readonly Dictionary<string, RecordingSession> sessions = new();
    readonly object gate = new();
    volatile bool running = true;

    public static string Version => typeof(EngineService).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0] ?? "0";

    public EngineService(Host host) => this.host = host;

    public int Run()
    {
        while (running)
        {
            var frame = host.Read();
            if (frame == null) break;
            var (type, payload) = frame.Value;
            if (type != Host.Request) continue;
            using var document = JsonDocument.Parse(payload);
            var request = document.RootElement;
            var id = request.GetProperty("id").GetInt64();
            var op = request.GetProperty("op").GetString() ?? "";
            var args = request.TryGetProperty("args", out var a) ? a : default;
            try
            {
                var result = Dispatch(op, args);
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
                var message = exc is TargetInvocationException { InnerException: { } inner } ? inner.Message : exc.Message;
                host.Send(Host.Response, w =>
                {
                    w.WriteStartObject();
                    w.WriteNumber("id", id);
                    w.WriteBoolean("ok", false);
                    w.WriteString("error", message);
                    w.WriteString("type", exc.GetType().Name);
                    w.WriteEndObject();
                });
            }
        }
        Shutdown();
        return 0;
    }

    object? Dispatch(string op, JsonElement args)
    {
        switch (op)
        {
            case "hello":
                return new Dictionary<string, object?> { ["engine"] = "SpaceAnalyzer.Engine", ["version"] = Version, ["runtime"] = Environment.Version.ToString(), ["pid"] = Environment.ProcessId };
            case "record.start":
            {
                var session = new RecordingSession(args.GetProperty("directory").GetString()!, args.GetProperty("version").GetString()!, Emit);
                var nodes = args.GetProperty("nodes").EnumerateArray().Select(NodeSpec.FromJson).ToList();
                var connections = args.TryGetProperty("connections", out var c) && c.ValueKind == JsonValueKind.Object
                    ? c.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetString() ?? "") : new Dictionary<string, string>();
                session.Start(args.GetProperty("format").GetString()!.ToLowerInvariant(), nodes, args.GetProperty("endpoint").GetString() ?? "",
                    args.GetProperty("sample_ms").GetInt32(), args.TryGetProperty("options", out var o) ? o : default, connections);
                lock (gate) sessions[session.Id] = session;
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
        List<RecordingSession> open;
        lock (gate) open = sessions.Values.ToList();
        foreach (var session in open) session.Stop("application_close", TimeSpan.FromSeconds(30));
    }
}
