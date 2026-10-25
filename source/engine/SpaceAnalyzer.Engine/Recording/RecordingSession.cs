using System.Text.Json;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// One Record session: the primary capture takes every accepted sample first;
/// the selected format runs behind its own bounded queue. If that format falls
/// behind or fails, the primary keeps going and the format is rebuilt from it
/// after Stop (port of durable_recording.DurableRecorder).
/// </summary>
public sealed class RecordingSession
{
    readonly string directory;
    readonly string version;
    readonly Action<string, Action<Utf8JsonWriter>> emit;
    readonly object gate = new();
    readonly Queue<(double t, List<Sample> rows, int period, long count)> pending = new();
    readonly SemaphoreSlim signal = new(0);
    const long PumpCapacity = 65536;
    long pendingSamples;
    Thread? pump;
    readonly ManualResetEventSlim pumpDone = new(true);
    IFormatRecorder? external;
    PrimaryRecorder? primary;
    (string format, List<NodeSpec> nodes, string endpoint, int sampleMs, JsonElement options) spec;
    string externalError = "";
    long externalLost, logicalRows, logicalCycles;
    bool primaryOnly;
    JsonElement? finalDiagnostics;
    bool stopRequested, starting, rebuildRunning;
    string rebuildFailed = "";
    string stopReason = "user";
    JsonElement? rebuilt;
    long rejectAfter = long.MaxValue;   // test-only fault injection (option _test_external_reject_after)
    long pumped;

    public string Id { get; } = Guid.NewGuid().ToString("N");

    public RecordingSession(string directory, string version, Action<string, Action<Utf8JsonWriter>> emit)
    {
        this.directory = directory;
        this.version = version;
        this.emit = emit;
    }

    public bool Acquiring { get { lock (gate) return starting || primary is { Accepting: true }; } }

    public bool Busy
    {
        get
        {
            lock (gate)
                return starting || primary is { Accepting: true } || primary is { Done: false } || !pumpDone.IsSet || (external?.Busy ?? false) || rebuildRunning;
        }
    }

    void Event(string kind, Action<Utf8JsonWriter> details) => emit(kind, w =>
    {
        w.WriteString("session_id", primary?.Identity);
        details(w);
    });

    public void Start(string format, IReadOnlyList<NodeSpec> nodes, string endpoint, int sampleMs, JsonElement options, IReadOnlyDictionary<string, string> connections)
    {
        lock (gate) starting = true;
        spec = (format, nodes.ToList(), endpoint, sampleMs, options.Clone());
        try
        {
            if (options.ValueKind == JsonValueKind.Object && options.TryGetProperty("_test_external_reject_after", out var reject)) rejectAfter = reject.GetInt64();
            external = FormatRecorders.Create(format, directory, version);
            external.Start(format, nodes, endpoint, sampleMs, options);
            int Option(string key, int fallback) => options.ValueKind == JsonValueKind.Object && options.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : fallback;
            primary = new PrimaryRecorder(Path.Combine(directory, ".primary"), Option("primary_flush_ms", 250),
                Option("primary_queue_samples", 65536), Option("primary_queue_mb", 32) * 1024L * 1024, emit: emit);
            primary.FailureCallback = message =>
            {
                lock (gate) stopRequested = true;
                Event("recording_integrity_failure", w => w.WriteString("error", message));
                signal.Release();
            };
            primary.Start(nodes, w =>
            {
                w.WriteString("application_version", version);
                w.WriteString("selected_format", format);
                w.WriteString("endpoint", endpoint);
                foreach (var key in new[] { "acquisition_info", "calculation_formulas" })
                    if (options.ValueKind == JsonValueKind.Object && options.TryGetProperty(key, out var value)) { w.WritePropertyName(key); value.WriteTo(w); }
                w.WriteStartObject("session");
                foreach (var key in new[] { "session_name", "machine", "operator", "description" })
                    w.WriteString(key, options.ValueKind == JsonValueKind.Object && options.TryGetProperty(key, out var label) && label.ValueKind == JsonValueKind.String ? label.GetString() : "");
                w.WriteEndObject();
            }, connections);
            pumpDone.Reset();
            Event("recording_started", w => { w.WriteString("format", format); w.WriteNumber("channels", nodes.Count); });
            pump = new Thread(Pump) { Name = "format-pump", IsBackground = true };
            pump.Start();
        }
        catch
        {
            external?.Stop("primary_start_failed", TimeSpan.FromSeconds(5));
            primary?.Dispose();
            throw;
        }
        finally { lock (gate) starting = false; }
    }

    /// <summary>Notifications kept only in the primary capture (the format receives periodic snapshots).</summary>
    public bool EnqueuePrimary(double t, IReadOnlyList<Sample> rows, int period)
    {
        lock (gate) primaryOnly = true;
        var p = primary;
        return p is { Accepting: true } && p.Enqueue(t, rows, period, backpressureSeconds: .5);
    }

    /// <summary>Primary first (bounded backpressure); the selected format is best effort.</summary>
    public bool Enqueue(double t, IReadOnlyList<Sample> rows, int period)
    {
        var p = primary;
        if (p == null || !p.Accepting) return false;
        if (!p.Enqueue(t, rows, period, backpressureSeconds: .5)) return false;
        var selected = rows.Where(r => p.Channels.ContainsKey(r.NodeId)).ToList();
        lock (gate)
        {
            if (selected.Count > 0) { logicalCycles++; logicalRows += selected.Count; }
            if (externalError.Length > 0) { externalLost += selected.Count; return true; }
            if (pendingSamples + rows.Count > PumpCapacity)
            {
                externalLost += rows.Count;
                externalError = "Formato esterno in ritardo: originale primario conservato";
                var lostNow = rows.Count;
                ThreadPool.QueueUserWorkItem(_ => Event("external_writer_overflow", w => { w.WriteNumber("samples", lostNow); w.WriteString("error", "Formato esterno in ritardo: originale primario conservato"); }));
                return true;
            }
            pending.Enqueue((t, rows.ToList(), period, rows.Count));
            pendingSamples += rows.Count;
        }
        signal.Release();
        return true;
    }

    void Pump()
    {
        try
        {
            while (true)
            {
                signal.Wait(50);
                while (true)
                {
                    (double t, List<Sample> rows, int period, long count) item;
                    lock (gate)
                    {
                        if (!pending.TryDequeue(out item)) break;
                        pendingSamples -= item.count;
                    }
                    if (externalError.Length > 0) { lock (gate) externalLost += item.count; continue; }
                    if (++pumped > rejectAfter || !external!.Enqueue(item.t, item.rows, item.period))
                    {
                        lock (gate)
                        {
                            externalLost += item.count;
                            externalError = external.Error is { Length: > 0 } e ? e : "Formato esterno non disponibile; originale primario conservato";
                        }
                        Event("external_writer_failure", w => w.WriteString("error", externalError));
                    }
                }
                bool done;
                lock (gate) done = (stopRequested || primary!.Error.Length > 0) && pending.Count == 0;
                if (done) break;
                if (externalError.Length > 0) external!.Fail(externalError);
            }
            if (primary!.Error.Length > 0) external!.Fail("Integrità registrazione primaria: " + primary.Error);
            else if (externalError.Length > 0) external!.Fail(externalError);
            external!.Stop(stopReason, Timeout.InfiniteTimeSpan);
            var finalError = external.Error;
            if (finalError.Length > 0 && externalError.Length == 0)
            {
                externalError = finalError;
                Event("external_finalization_failed", w => w.WriteString("error", finalError));
            }
            if (externalError.Length > 0 && primary.Error.Length == 0) SafeRebuild();
        }
        catch (Exception exc)
        {
            externalError = exc.Message;
            external?.Fail(exc.Message);
            Event("external_writer_failure", w => w.WriteString("error", exc.Message));
            if (primary != null && primary.Error.Length == 0) SafeRebuild();
        }
        finally
        {
            pumpDone.Set();
            Event("external_writer_closed", w =>
            {
                w.WriteString("error", rebuildFailed.Length > 0 ? rebuildFailed : rebuilt != null ? "" : externalError);
                w.WriteNumber("unrecovered_format_samples", rebuilt != null ? 0 : externalLost);
                w.WriteBoolean("rebuilt", rebuilt != null);
            });
        }
    }

    void SafeRebuild()
    {
        try { Rebuild(); }
        catch (Exception exc)
        {
            rebuildFailed = exc.Message;
            Event("external_rebuild_failed", w => w.WriteString("error", exc.Message));
        }
        finally { lock (gate) rebuildRunning = false; }
    }

    /// <summary>The durable primary is the spool: re-export it with the selected writer.</summary>
    void Rebuild()
    {
        Event("external_rebuild_waiting", w => { w.WriteString("format", spec.format); w.WriteString("reason", externalError); });
        primary!.WaitDone(Timeout.InfiniteTimeSpan);
        if (primary.Error.Length > 0) return;
        lock (gate) rebuildRunning = true;
        Event("external_rebuild_started", w => { w.WriteString("format", spec.format); w.WriteNumber("samples", logicalRows); });
        var writer = FormatRecorders.Create(spec.format, directory, version);
        writer.Start(spec.format, spec.nodes, spec.endpoint, spec.sampleMs, spec.options);
        bool excludeNotifications;
        lock (gate) excludeNotifications = primaryOnly;
        if (finalDiagnostics is JsonElement diagnostics) writer.SourceDiagnostics(diagnostics);
        long rows = 0;
        using (var reader = new SacReader(primary.FinalPath))
        {
            double? current = null;
            var group = new List<Sample>();
            var period = spec.sampleMs;
            void FlushGroup()
            {
                if (group.Count == 0) return;
                while (!writer.Enqueue(current!.Value, group, period))
                {
                    if (writer.Error.Length > 0) throw new InvalidOperationException(writer.Error);
                    Thread.Sleep(5);
                }
                rows += group.Count;
                group = new List<Sample>();
            }
            foreach (var (t, samplePeriod, sample) in reader.Samples())
            {
                if (excludeNotifications && sample.AcquisitionOrigin == "notification") continue;
                if (current != t) { FlushGroup(); current = t; period = samplePeriod; }
                group.Add(sample);
            }
            FlushGroup();
        }
        writer.Stop("primary_export", Timeout.InfiniteTimeSpan);
        if (writer.Error.Length > 0) throw new InvalidOperationException(writer.Error);
        if (rows != logicalRows) throw new InvalidDataException("Conteggio ricostruzione differente dagli originali: " + rows + " / " + logicalRows);
        rebuilt = writer.StatusJson();
        Event("external_rebuild_finished", w => { w.WriteString("format", spec.format); w.WriteNumber("rows", rows); });
    }

    public void Fail(string message, string reason = "source_acquisition_error")
    {
        primary?.Fail(message);
        external?.Fail(message, reason);
        lock (gate) { stopReason = reason; stopRequested = true; }
        signal.Release();
    }

    public void SourceDiagnostics(JsonElement diagnostics)
    {
        finalDiagnostics = diagnostics.Clone();
        external?.SourceDiagnostics(diagnostics);
    }

    public bool Annotate(string kind, Action<Utf8JsonWriter> details) => primary is { Accepting: true } p && p.Annotate(kind, details);

    public void Stop(string reason, TimeSpan wait)
    {
        lock (gate) { stopReason = reason; stopRequested = true; }
        primary?.RequestStop();
        signal.Release();
        if (wait > TimeSpan.Zero) pumpDone.Wait(wait);
    }

    public void WriteStatus(Utf8JsonWriter w)
    {
        using var external = JsonDocument.Parse(SacFormat.Json(x => this.external!.WriteStatus(x)));
        using var primaryDoc = JsonDocument.Parse(SacFormat.Json(x => { if (primary != null) primary.WriteStatus(x); else { x.WriteStartObject(); x.WriteEndObject(); } }));
        var result = new Dictionary<string, object?>();
        var source = rebuilt ?? external.RootElement;
        foreach (var property in source.EnumerateObject()) result[property.Name] = property.Value.Clone();
        bool deferred, acquiring = Acquiring, busy = Busy;
        lock (gate)
        {
            deferred = externalError.Length > 0 && (primary?.Error.Length ?? 0) == 0 && rebuilt == null && rebuildFailed.Length == 0;
            if (deferred)
            {
                var phase = rebuildRunning ? "converting" : "queued";
                result["cycles_received"] = logicalCycles; result["error"] = ""; result["conversion_phase"] = phase; result["status_note"] = "";
                foreach (var key in new[] { "fda_phase", "rbf_phase" }) if (result.ContainsKey(key)) result[key] = acquiring ? "recording" : phase;
            }
            result["primary"] = primaryDoc.RootElement.Clone();
            result["external_buffer"] = new Dictionary<string, object?> { ["samples"] = pendingSamples, ["capacity_samples"] = PumpCapacity };
            result["primary_original"] = primary?.FinalPath is { Length: > 0 } final ? final : primary?.PathPart ?? "";
            result["active"] = acquiring;
            result["busy"] = busy;
            result["external_error"] = rebuildFailed.Length > 0 ? rebuildFailed : deferred || rebuilt != null ? "" : externalError;
            result["external_warning"] = externalError;
            result["external_lost_samples"] = rebuilt != null ? 0 : externalLost;
            result["external_rebuilt_from_primary"] = rebuilt != null;
            result["deferred_export_pending"] = deferred;
            result["deferred_export_error"] = rebuildFailed;
            if (acquiring) result["state"] = "recording";
            else if (busy) result["state"] = "stopping";
            var primaryError = primary?.Error ?? "";
            if (primaryError.Length > 0) { result["error"] = primaryError; result["state"] = busy ? "stopping" : "error"; }
            else if (rebuildFailed.Length > 0) { result["error"] = rebuildFailed; result["state"] = busy ? "stopping" : "error"; }
            else if (externalError.Length > 0 && !deferred && rebuilt == null) result["error"] = externalError;
            result["engine"] = "C#";
        }
        JsonOut.Write(w, result);
    }
}
