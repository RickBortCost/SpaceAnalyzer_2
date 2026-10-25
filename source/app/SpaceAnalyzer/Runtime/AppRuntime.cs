using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Calculations;
using SpaceAnalyzer.App.Config;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Live;
using SpaceAnalyzer.App.Recordings;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.Mqtt;
using SpaceAnalyzer.Engine.Recording;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.App.Runtime;

/// <summary>
/// The application state of app.py: one connection of independent protocol sessions (engine),
/// the selection, Live buffers and history, calculations and the recording manager.
/// </summary>
public sealed class AppRuntime : IDisposable
{
    public AppPaths Paths { get; }
    public ConfigStore Config { get; }
    public Certificates Certs { get; }
    public FormulaStore Formulas { get; }
    public EventStore Events { get; }
    public AcquisitionEngine Acquisition { get; }
    public RecordGate Gate { get; } = new();
    public SourceController Sources { get; }
    public RecordingManager Recording { get; }
    public CalculationService Calculations { get; }
    public LiveArchive Archive { get; }
    public Snapshots Snapshots { get; } = new();
    public RecordingLibrary Library { get; private set; }
    public RecordingLibrary Imported { get; }
    public Comparisons Comparisons { get; }
    public PrimaryRecovery Recovery { get; private set; }

    /// <summary>Serializes connect, selection, Record and formula edits (runtime.command_lock).</summary>
    public SemaphoreSlim CommandLock { get; } = new(1, 1);
    public readonly object Lock = new();

    // Selection and Live state (guarded by Lock).
    public List<string> Selected { get; private set; } = new();
    public long SelectionRevision { get; private set; }
    public long BufferEpoch { get; private set; }
    Dictionary<string, LiveBuffer> samples = new();
    Dictionary<string, Sample> latest = new();
    Dictionary<string, Sample> calculatedLatest = new();
    public long CalculationGeneration { get; private set; }
    public JsonObject? ActiveConfig { get; private set; }
    public bool WantConnected { get; private set; }
    public int SampleMs { get; private set; } = 500;
    public double WindowSeconds { get; private set; } = 60;
    public long MaxSamples { get; private set; } = Defaults.MaxSamples;
    public string BufferMode { get; private set; } = "time";
    public string AcquisitionMode { get; private set; } = "Read polling";
    long readCount, notificationCount;
    double? lastSampleAt;
    string lastError = "";

    // Recording fences of calculated rows (enqueue_recording).
    bool recordingAccept = true;
    double? recordingBegin, multiBegin, multiEnd;
    bool stopping;

    // Display distribution (acquisition.live): bounded, loss reported, never blocks the dispatcher.
    readonly Queue<(double t, List<Sample> rows, int period, object? generation)> live = new();
    readonly object liveGate = new();
    readonly AutoResetEvent liveWake = new(false);
    long liveSamples;
    const long LiveCapacity = 262144;
    readonly Thread liveThread;
    volatile bool closed;

    // Discovery job (runtime.scan).
    public Dictionary<string, object?> Scan { get; private set; } = new()
    {
        ["id"] = "", ["state"] = "idle", ["nodes"] = new List<object?>(), ["visited"] = 0L, ["pending"] = 0L, ["errors"] = new List<object?>(), ["error_count"] = 0L, ["root"] = "",
    };
    CancellationTokenSource? scanCancel;
    volatile Tuple<Dictionary<string, object?>, Dictionary<string, object?>>? closingState;
    (Dictionary<string, object?> status, Dictionary<string, object?> summary)? closing
    {
        get => closingState is { } c ? (c.Item1, c.Item2) : null;
        set => closingState = value is { } v ? Tuple.Create(v.status, v.summary) : null;
    }

    public Dictionary<string, object?> SourceStatus() => closing?.status ?? Sources.Status();
    public Dictionary<string, object?> SourceSummary() => closing?.summary ?? Sources.Summary();
    Task? scanTask;

    public AppRuntime(AppPaths paths)
    {
        Paths = paths;
        Config = new ConfigStore(paths);
        Certs = new Certificates(paths, Config);
        Formulas = new FormulaStore(Path.Combine(paths.DataDir, "formulas.json"));
        Events = new EventStore(Path.Combine(paths.DataDir, "acquisition_events.sqlite3"));
        Acquisition = new AcquisitionEngine(EngineEvent);
        Sources = new SourceController(Acquisition, EngineEvent, Gate);
        Acquisition.Record = Gate.Record;
        Acquisition.Forward = Forward;
        Acquisition.Fail = Gate.Fail;
        Recording = new RecordingManager(paths.RecordingsDir, Defaults.Version, (kind, details) => EngineEvent(kind, null, details), Gate);
        Events.Listener = evt => Recording.Annotate(evt);
        Calculations = new CalculationService(Path.Combine(paths.DataDir, "calculation_cache"), CalculatedResult);
        Calculations.FaultCallback = (error, dropped) => Events.Emit("calculation_overflow", null, new() { ["protocol"] = "calculated", ["error"] = error, ["lost_batches"] = dropped });
        Archive = new LiveArchive(Path.Combine(paths.DataDir, "live_analysis_cache"));
        Library = new RecordingLibrary(paths.RecordingsDir);
        Imported = new RecordingLibrary(paths.ImportedRecordingsDir);
        Comparisons = new Comparisons(paths.ImportedRecordingsDir);
        Recovery = new PrimaryRecovery(Path.Combine(paths.RecordingsDir, ".primary"), Events);
        liveThread = new Thread(LiveLoop) { IsBackground = true, Name = "live-display" };
        liveThread.Start();
    }

    public void Initialize()
    {
        Formulas.Load(Config.LoadWorkspace()["calculations"] ?? Config.LoadSettings()["calculations"]);
        var cfg = Config.Validated(new JsonObject());
        Certs.EnsureClient(cfg["application_uri"].Str());
        Config.Save(cfg);
        ApplyLiveConfig(cfg);
        try { ApplySelectionCore(Validation.Nodes(Config.LoadSettings()["selected_nodes"] ?? new JsonArray())); }
        catch (UserError exc) { Log.Warning("Selezione salvata non applicata: " + exc.Message); }
        lock (Lock) ResetCalculations(cfg["source"].Str());
        Recovery.Start();
    }

    // ---- Engine events -----------------------------------------------------------------

    void EngineEvent(string kind, string? connectionId, Action<Utf8JsonWriter> details)
    {
        if (kind == "sources_state") return;
        if (kind == "source_stopped")
        {
            var json = JsonNode.Parse(SacFormat.Json(w => { w.WriteStartObject(); details(w); w.WriteEndObject(); }));
            if (json.Get("others_connected").Bool() != true) ThreadPool.QueueUserWorkItem(_ => FinishRecording("connection_lost", 5));
            return;
        }
        Events.EmitEngine(kind, connectionId, details);
    }

    public static string ProtocolOf(string nid) => Sample.ProtocolOf(nid);

    public static HashSet<string> AllowedSources(JsonObject cfg) => Validation.ConfiguredSources(cfg);

    // ---- Live buffers -------------------------------------------------------------------

    public int SampleLimit()
    {
        lock (Lock) return (int)Math.Max(1000, Math.Min(MaxSamples, Defaults.MaxTotalSamples / Math.Max(1, Selected.Count + Calculations.Ids.Count)));
    }

    LiveBuffer NewBuffer(string nid, IEnumerable<LivePoint>? initial = null, Overview? previous = null)
    {
        var generation = Archive.Generation;
        return new LiveBuffer(initial ?? Array.Empty<LivePoint>(), SampleLimitUnlocked(), generation, point => Archive.Append(generation, nid, point), previous);
    }

    int SampleLimitUnlocked() => (int)Math.Max(1000, Math.Min(MaxSamples, Defaults.MaxTotalSamples / Math.Max(1, Selected.Count + Calculations.Ids.Count)));

    public Dictionary<string, LiveBuffer> Samples { get { lock (Lock) return new(samples); } }

    /// <summary>Called under Lock: replace only derived buffers and invalidate old callbacks.</summary>
    void ResetCalculations(string? protocol = null)
    {
        var cfg = ActiveConfig ?? Config.Load();
        protocol ??= cfg["source"].Str() ?? "opcua";
        if (AllowedSources(cfg).Count > 1) protocol = null;
        CalculationGeneration = Calculations.Configure(Formulas.Formulas(), protocol);
        samples = samples.Where(k => !k.Key.StartsWith("calc:", StringComparison.Ordinal)).ToDictionary(k => k.Key, k => k.Value);
        foreach (var nid in Calculations.Ids) samples[nid] = NewBuffer(nid);
        calculatedLatest = new();
    }

    public void ResetCalculationsLocked() { lock (Lock) ResetCalculations(); }

    public void ApplyLiveConfig(JsonObject cfg)
    {
        lock (Lock)
        {
            SampleMs = (int)(cfg["sample_ms"].Num() ?? 500);
            WindowSeconds = cfg["window_seconds"].Num() ?? 60;
            MaxSamples = (long)(cfg["max_samples"].Num() ?? Defaults.MaxSamples);
            BufferMode = cfg["buffer_mode"].Str() ?? "time";
            samples = samples.ToDictionary(k => k.Key, k => NewBuffer(k.Key, k.Value.Points, k.Value.Overview));
            if (ActiveConfig != null)
                foreach (var key in new[] { "sample_ms", "window_seconds", "auto_reconnect", "max_samples", "buffer_mode" })
                    ActiveConfig[key] = cfg[key]?.DeepClone();
        }
    }

    // ---- Distribution ---------------------------------------------------------------------

    bool Forward(double t, List<Sample> rows, int period, Generation? generation)
    {
        var display = rows.Where(r => r.AcquisitionOrigin != "notification").ToList();
        if (display.Count == 0) return true;
        if (generation is not Generation g || g.Revision == SelectionRevision) Calculations.Submit(t, display, period);
        return PutLive(t, display, period, generation);
    }

    bool PutLive(double t, List<Sample> rows, int period, object? generation)
    {
        lock (liveGate)
        {
            if (closed) return false;
            if (liveSamples + rows.Count > LiveCapacity)
            {
                LiveLoss(generation, rows.Count);
                return false;
            }
            live.Enqueue((t, rows, period, generation));
            liveSamples += rows.Count;
        }
        liveWake.Set();
        return true;
    }

    void LiveLoss(object? generation, int count)
    {
        var current = generation is Generation g ? g.Revision == SelectionRevision && g.Epoch == BufferEpoch
            : generation is (string, long gen) && gen == CalculationGeneration;
        if (current && Archive.Error.Length == 0)
        {
            Archive.SetError("Storico Live incompleto: visualizzazione in ritardo; usare la registrazione primaria per gli originali");
            Events.Emit("live_distribution_skipped", null, new() { ["samples"] = (long)count });
        }
    }

    void LiveLoop()
    {
        while (!closed)
        {
            (double t, List<Sample> rows, int period, object? generation) item;
            lock (liveGate)
            {
                if (live.Count == 0) item = default;
                else { item = live.Dequeue(); liveSamples -= item.rows.Count; }
            }
            if (item.rows == null) { liveWake.WaitOne(20); continue; }
            try { Display(item.t, item.rows, item.period, item.generation); }
            catch (Exception exc) { Log.Error("Visualizzazione Live", exc); }
        }
    }

    public bool FlushLive(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            lock (liveGate) if (live.Count == 0) return true;
            Thread.Sleep(2);
        }
        return false;
    }

    static string PyStr(object? value) => value switch
    {
        null => "None",
        bool b => b ? "True" : "False",
        double d => PyJson.Str(d),
        long or int => Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture)!,
        string s => s,
        _ => Engine.Core.Values.ValueText(value) ?? "None",
    };

    /// <summary>acquisition_display: the only consumer touching the Live FIFO/history.</summary>
    void Display(double t, List<Sample> rows, int period, object? generation)
    {
        lock (Lock)
        {
            var calculated = generation is (string, long);
            if (calculated) { if (((string, long))generation! is var (_, gen) && gen != CalculationGeneration) return; }
            else if (generation is Generation g && (g.Revision != SelectionRevision || g.Epoch != BufferEpoch)) return;
            foreach (var row in rows)
            {
                if (!samples.TryGetValue(row.NodeId, out var points)) continue;
                if (points.Last is { } lastPoint && t < lastPoint.T) continue;
                if (calculated) calculatedLatest[row.NodeId] = row; else latest[row.NodeId] = row;
                var point = new LivePoint(t, row.PlotValue is double p && double.IsFinite(p) ? p : null, row.QualityOrDerived, row.StatusCode, row.SourceTs, row.ServerTs,
                    PyStr(row.Value), row.DataType, row.Status, period, row.LocalTs, row.SourceFiletime);
                if (calculated && points.Last is { } previous && previous.T == t) points.ReplaceLast(point);
                else points.Append(point);
                while (BufferMode == "time" && points.First is { } first && first.T < t - WindowSeconds * 1000) points.RemoveFirst();
            }
            if (!calculated)
            {
                readCount++;
                notificationCount += rows.Count;
                lastSampleAt = Math.Max(t, lastSampleAt ?? t);
            }
        }
    }

    /// <summary>calculated_result: formula outputs go to the recording and to the Live view.</summary>
    void CalculatedResult(List<Dictionary<string, object?>> rows, long generation, int sampleMs)
    {
        if (generation != CalculationGeneration) return;
        foreach (var group in rows.GroupBy(r => (double)r["timestamp_ms"]!))
        {
            var batch = group.Select(r =>
            {
                var quality = (string)r["quality"]!;
                var value = r.TryGetValue("value_numeric", out var v) ? v as double? : null;
                return new Sample
                {
                    NodeId = (string)r["node_id"]!, Value = value, PlotValue = value, Quality = quality, Good = quality == "Good", Numeric = value != null,
                    Status = (string?)r["status"], DataType = "Double", SourceTs = r.TryGetValue("source_timestamp", out var s) ? s as string : null,
                    LocalTs = r.TryGetValue("local_timestamp", out var l) ? l as string : null, Calculated = true,
                    Error = quality == "Good" ? "" : (string?)r["status"], ReceivedNs = Clock.UtcNs(), ReceivedMonoNs = Clock.MonoNs(), SourceProtocol = "calculated",
                };
            }).ToList();
            EnqueueRecording(group.Key, batch, sampleMs);
            PutLive(group.Key, batch, sampleMs, ("calculated", generation));
        }
    }

    bool EnqueueRecording(double t, List<Sample> rows, int sampleMs)
    {
        if (stopping)
        {
            var last = Gate.RawLastT;
            if (last == null || t > last) return false;
        }
        if (multiBegin == null || t < multiBegin || multiEnd != null && t > multiEnd) return false;
        if (!recordingAccept || recordingBegin != null && t <= recordingBegin) return false;
        return Recording.Enqueue(t, rows, sampleMs);
    }

    // ---- Connection -----------------------------------------------------------------------

    public string State
    {
        get
        {
            if (closing is { } c) return (string)c.summary["state"]!;
            if (!Sources.Enabled) return "disconnected";
            return (string)Sources.Summary()["state"]!;
        }
    }

    public bool Connected => closing is { } c ? (bool)c.summary["connected"]! : Sources.Enabled && (bool)Sources.Summary()["connected"]!;

    public string LastError => closing is { } c ? (string)c.summary["error"]! : Sources.Enabled ? (string)Sources.Summary()["error"]! : lastError;

    JsonElement EngineConfig(JsonObject cfg)
    {
        var copy = (JsonObject)cfg.DeepClone();
        copy["_client_cert"] = Paths.CertFile;
        copy["_client_key"] = Paths.KeyFile;
        copy["_server_cert"] = Paths.ServerCertFile;
        using var doc = JsonDocument.Parse(copy.ToJsonString());
        return doc.RootElement.Clone();
    }

    public void S7SelectionGuard(JsonObject cfg, IEnumerable<string> ids)
    {
        var mode = cfg["s7_mode"].Str() ?? "classic";
        foreach (var n in ids.Where(n => ProtocolOf(n) == "s7"))
        {
            string tagMode;
            try { tagMode = Engine.Drivers.S7.S7Types.ParseNode(n).Mode; }
            catch (ArgumentException exc) { throw new UserError(exc.Message); }
            if (tagMode != mode) throw new UserError("La selezione contiene segnali di un’altra modalità S7. Cambiare modalità nel popup Connessioni.");
        }
    }

    public async Task Connect(JsonObject cfg)
    {
        await CommandLock.WaitAsync();
        try
        {
            List<string> selected;
            lock (Lock) selected = Selected.ToList();
            var allowed = AllowedSources(cfg);
            if (selected.Any(n => !allowed.Contains(ProtocolOf(n))))
                throw new UserError("La selezione contiene segnali di una fonte diversa: cambiare fonte e selezione prima di connettere");
            S7SelectionGuard(cfg, selected);
            await DisconnectCore();
            if (allowed.Contains("opcua")) Certs.EnsureClient(cfg["application_uri"].Str());
            ActiveConfig = (JsonObject)cfg.DeepClone();
            ApplyLiveConfig(cfg);
            WantConnected = true;
            Archive.NewSession();
            lock (Lock)
            {
                samples = Selected.ToDictionary(n => n, n => NewBuffer(n));
                ResetCalculations(cfg["source"].Str());
                latest = new();
                readCount = notificationCount = 0;
                lastSampleAt = null;
                BufferEpoch++;
                AcquisitionMode = "OPC UA / ADS / MQTT / S7 · PC UTC arrival";
                Sources.Select(Selected.ToList(), SampleMs, new Generation(SelectionRevision, BufferEpoch));
            }
            var protocols = Defaults.Protocols.Where(allowed.Contains).ToList();
            var error = await Sources.Start(EngineConfig(cfg), protocols, protocols.Count == 1);
            if (error != null)
            {
                WantConnected = false;
                lastError = error;
                throw new SourceException(error);
            }
            lastError = "";
            Log.Info("Sorgenti avviate: " + string.Join(", ", protocols));
        }
        finally { CommandLock.Release(); }
    }

    public async Task Disconnect()
    {
        await CommandLock.WaitAsync();
        try { await DisconnectCore(); }
        finally { CommandLock.Release(); }
    }

    async Task DisconnectCore()
    {
        WantConnected = false;
        // Fence and drain only a recording that exists; an idle disconnect returns at once.
        if (Recording.Active != null) await Task.Run(() => FinishRecording("disconnect", 5));
        if (Sources.Enabled)
        {
            // Until every session is closed the UI keeps seeing the previous state (2.x semantics).
            closing = (Sources.Status(), Sources.Summary());
            var started = System.Diagnostics.Stopwatch.GetTimestamp();
            try { await Sources.Stop(); }
            finally { closing = null; }
            Log.Info($"Sorgenti chiuse in {System.Diagnostics.Stopwatch.GetElapsedTime(started).TotalMilliseconds:F0} ms");
        }
        if (scanTask != null) await StopScan();
        lastError = "";
        Log.Info("Data source disconnected");
    }

    public async Task SourceControl(string protocol, string action)
    {
        if (!Sources.Sessions.ContainsKey(protocol) || action is not ("connect" or "disconnect")) throw new UserError("Sorgente o comando non valido");
        await CommandLock.WaitAsync();
        try { await Sources.Control(protocol, action); }
        finally { CommandLock.Release(); }
    }

    /// <summary>Changes the recordings folder (null: default). Refused while Record is acquiring.</summary>
    public async Task<string> SetRecordingsDirectory(string? path)
    {
        await CommandLock.WaitAsync();
        try
        {
            if (Recording.Acquiring) throw new UserError("Premere Stop prima di cambiare la cartella delle registrazioni");
            Paths.SetRecordingsDir(path);
            Recording.SetDirectory(Paths.RecordingsDir);
            Library = new RecordingLibrary(Paths.RecordingsDir);
            Recovery = new PrimaryRecovery(Path.Combine(Paths.RecordingsDir, ".primary"), Events);
            Recovery.Start();
            Log.Info("Cartella registrazioni: " + Paths.RecordingsDir);
            return Paths.RecordingsDir;
        }
        finally { CommandLock.Release(); }
    }

    public void ConnectionGuard(JsonObject cfg)
    {
        var active = ActiveConfig;
        if (!WantConnected || active == null) return;
        if (!AllowedSources(cfg).SetEquals(AllowedSources(active))) throw new UserError("Disconnettere prima di cambiare i protocolli attivi");
        var mutable = new HashSet<string> { "source", "ui_language", "window_seconds", "max_samples", "buffer_mode", "root_nodeid", "ads_root", "mqtt_root", "s7_root",
            "s7_symbolic_root", "s7_udp_root", "scan_limit", "mqtt_password", "s7_symbolic_password", "auto_reconnect" };
        foreach (var (key, _) in Defaults.Config())
            if (!mutable.Contains(key) && !JsonNode.DeepEquals(cfg[key], active[key])) throw new UserError("Disconnettere prima di modificare i parametri delle sorgenti");
    }

    // ---- Selection --------------------------------------------------------------------------

    public async Task ApplySelection(JsonArray nodes)
    {
        await CommandLock.WaitAsync();
        try { ApplySelectionCore(nodes); }
        finally { CommandLock.Release(); }
    }

    public void ApplySelectionCore(JsonArray nodes)
    {
        var ids = nodes.Select(n => n.Get("nodeid").Str()!).ToList();
        if (WantConnected && ActiveConfig != null)
        {
            var allowed = AllowedSources(ActiveConfig);
            if (ids.Any(n => !allowed.Contains(ProtocolOf(n)))) throw new UserError("I segnali selezionati non appartengono alla fonte connessa");
            S7SelectionGuard(ActiveConfig, ids);
        }
        lock (Lock)
        {
            if (Recording.Acquiring && !ids.ToHashSet().SetEquals(Selected)) throw new UserError("Premere Stop prima di cambiare i segnali della registrazione");
            if (ids.SequenceEqual(Selected)) return;
            Selected = ids;
            SelectionRevision++;
            samples = ids.ToDictionary(n => n, n => NewBuffer(n, samples.TryGetValue(n, out var old) ? old.Points : null));
            ResetCalculations((ActiveConfig ?? Config.Load())["source"].Str());
            latest = latest.Where(k => ids.Contains(k.Key)).ToDictionary(k => k.Key, k => k.Value);
            Sources.Select(Selected.ToList(), SampleMs, new Generation(SelectionRevision, BufferEpoch));
        }
    }

    public void ClearSamples()
    {
        Archive.NewSession();
        lock (Lock)
        {
            BufferEpoch++;
            samples = Selected.ToDictionary(n => n, n => NewBuffer(n));
            ResetCalculations();
            Sources.Select(Selected.ToList(), SampleMs, new Generation(SelectionRevision, BufferEpoch));
        }
    }

    // ---- Values -------------------------------------------------------------------------------

    static List<LivePoint> Compress(List<LivePoint> points, int budget = 1200)
    {
        if (points.Count <= budget) return points;
        var step = (int)Math.Ceiling(points.Count / (double)(budget / 5));
        var output = new List<LivePoint>();
        for (var start = 0; start < points.Count; start += step)
        {
            var bucket = points.GetRange(start, Math.Min(step, points.Count - start));
            var indices = new SortedSet<int> { 0, bucket.Count - 1 };
            var numeric = Enumerable.Range(0, bucket.Count).Where(i => bucket[i].Value != null).ToList();
            if (numeric.Count > 0) { indices.Add(numeric.MinBy(i => bucket[i].Value!.Value)); indices.Add(numeric.MaxBy(i => bucket[i].Value!.Value)); }
            var gap = bucket.FindIndex(p => p.Value == null);
            if (gap >= 0) indices.Add(gap);
            foreach (var quality in new[] { "Bad", "Uncertain" })
            {
                var match = bucket.FindIndex(p => p.Quality == quality);
                if (match >= 0) indices.Add(match);
            }
            int? previous = null;
            foreach (var i in indices)
            {
                if (previous is int prev && bucket[prev].Value != null && bucket[i].Value != null)
                {
                    var hidden = Enumerable.Range(prev + 1, Math.Max(0, i - prev - 1)).Cast<int?>().FirstOrDefault(j => bucket[j!.Value].Value == null);
                    if (hidden is int h) output.Add(bucket[h]);
                }
                output.Add(bucket[i]);
                previous = i;
            }
        }
        return output;
    }

    public Action<Utf8JsonWriter> Values(string view)
    {
        if (view is not ("all" or "window")) throw new UserError("Vista Live non valida");
        var sessions = SourceStatus();
        var summary = SourceSummary();
        Dictionary<string, List<LivePoint>> points;
        List<Sample> values;
        Dictionary<string, object?> diag;
        lock (Lock)
        {
            points = samples.ToDictionary(k => k.Key, k => view == "all" ? k.Value.Overview.Points() : k.Value.ToList());
            var extent = points.Values.Where(v => v.Count > 0).SelectMany(v => new[] { v[0].T, v[^1].T }).ToList();
            values = Selected.Where(latest.ContainsKey).Select(n => latest[n]).Concat(calculatedLatest.Values).ToList();
            var connected = sessions.Values.Cast<Dictionary<string, object?>>().ToList();
            double Stat(string key) => connected.Select(s => s[key] is double d ? d : 0).DefaultIfEmpty(0).Max();
            var averages = connected.Select(s => s["average_period_ms"]).OfType<double>().ToList();
            diag = new()
            {
                ["requested_ms"] = (long)SampleMs, ["read_cycle_ms"] = Math.Round(Stat("last_cycle_ms"), 3), ["actual_period_ms"] = Math.Round(Stat("actual_period_ms"), 3),
                ["read_count"] = readCount, ["mean_period_ms"] = averages.Count > 0 ? Math.Round(averages.Average(), 3) : null,
                ["last_sample_at"] = lastSampleAt, ["window_seconds"] = WindowSeconds, ["buffer_mode"] = BufferMode, ["buffer_limit_per_signal"] = (long)SampleLimitUnlocked(),
                ["buffer_samples"] = samples.ToDictionary(k => k.Key, k => (object?)(long)k.Value.Count), ["acquisition_mode"] = AcquisitionMode,
                ["notification_count"] = notificationCount, ["live_view"] = view,
                ["display_range"] = extent.Count > 0 ? new Dictionary<string, object?> { ["xmin"] = extent.Min(), ["xmax"] = extent.Max() } : null,
                ["display_summary"] = view == "all", ["analysis_history_error"] = Archive.Error,
                ["session_samples"] = samples.ToDictionary(k => k.Key, k => (object?)k.Value.Overview.Count),
                ["last_notification_at"] = lastSampleAt, ["read_fallback_count"] = 0L, ["subscription_bad"] = new Dictionary<string, object?>(),
                ["individual_nodes"] = new List<object?>(),
            };
        }
        Dictionary<string, object?> Session(string protocol) => sessions.TryGetValue(protocol, out var s) && s is Dictionary<string, object?> d ? d : new();
        Dictionary<string, object?> Sub(Dictionary<string, object?> d, string key) => d.TryGetValue(key, out var v) && v is Dictionary<string, object?> x ? x : new();
        var mqtt = Session("mqtt");
        var mqttDiag = new Dictionary<string, object?>(Sub(mqtt, "diagnostics"));
        foreach (var key in new[] { "late_samples", "reconnections" }) mqttDiag[key] = mqtt.TryGetValue(key, out var v) ? v : 0L;
        var cfg = ActiveConfig ?? new JsonObject();
        diag["mqtt"] = mqttDiag;
        diag["s7_udp"] = cfg["s7_mode"].Str() == "udp" ? Sub(Session("s7"), "diagnostics") : new();
        diag["ads_notifications"] = cfg["ads_mode"].Str() == "notify" ? Sub(Session("ads"), "diagnostics") : new();
        var source = (ActiveConfig ?? Config.Load())["source"].Str() ?? "opcua";
        var recording = Recording.Status();
        var calculations = Calculations.Status();
        var state = (string)summary["state"]!;
        var connectedNow = (bool)summary["connected"]!;
        var error = closing != null || Sources.Enabled ? (string)summary["error"]! : lastError;
        return w =>
        {
            w.WriteStartObject();
            w.WriteBoolean("ok", true);
            w.WriteBoolean("connected", connectedNow);
            w.WriteString("state", state);
            w.WriteString("error", error);
            w.WriteStartArray("values");
            foreach (var row in values) row.WriteDisplay(w);
            w.WriteEndArray();
            w.WriteStartObject("samples");
            foreach (var (nid, list) in points)
            {
                w.WriteStartArray(nid);
                foreach (var p in Compress(list)) p.Write(w, 3);
                w.WriteEndArray();
            }
            w.WriteEndObject();
            w.WritePropertyName("diagnostics"); JsonOut.Write(w, diag);
            w.WriteString("source", source);
            w.WritePropertyName("ads"); JsonOut.Write(w, Sub(Session("ads"), "info"));
            w.WritePropertyName("s7"); JsonOut.Write(w, Sub(Session("s7"), "info"));
            w.WritePropertyName("mqtt"); JsonOut.Write(w, Sub(mqtt, "info"));
            w.WritePropertyName("sources"); JsonOut.Write(w, sessions);
            w.WritePropertyName("parallel_mqtt"); JsonOut.Write(w, sessions.TryGetValue("mqtt", out var m) ? m : new Dictionary<string, object?> { ["enabled"] = false });
            w.WritePropertyName("recording"); JsonOut.Write(w, recording);
            w.WritePropertyName("calculations"); JsonOut.Write(w, calculations);
            w.WriteEndObject();
        };
    }

    // ---- Recording ----------------------------------------------------------------------------

    public static string MqttEndpoint(JsonObject cfg) => MqttDriver.Endpoint(JsonDocument.Parse(cfg.ToJsonString()).RootElement);

    public async Task<Dictionary<string, object?>> StartRecording(JsonNode? data)
    {
        await CommandLock.WaitAsync();
        try
        {
            if (!Connected) throw new UserError("Client non connesso");
            if (Recording.Acquiring) throw new UserError("Una registrazione è già in corso o in fase di chiusura");
            var format = data.Get("format").Str();
            var options = data.Get("options") as JsonObject ?? new JsonObject();
            if (format?.ToLowerInvariant() is not ("das" or "rbf"))
            {
                try { StorageProfile.For(format?.ToLowerInvariant() ?? "", JsonDocument.Parse(options.ToJsonString()).RootElement); }
                catch (ArgumentException exc) { throw new UserError(exc.Message); }
            }
            if (data.Get("selected_nodes") is not JsonArray supplied || supplied.Count > 64) throw new UserError("Record: massimo 64 segnali acquisiti e calcolati complessivi");
            var derived = supplied.Where(n => n is JsonObject o && (o["nodeid"].Str() ?? "").StartsWith("calc:", StringComparison.Ordinal)).ToList();
            var nodes = Validation.Nodes(new JsonArray(supplied.Where(n => !derived.Contains(n)).Select(n => n?.DeepClone()).ToArray()));
            List<string> selected;
            lock (Lock) selected = Selected.ToList();
            if (nodes.Count == 0 || !nodes.Select(n => n.Get("nodeid").Str()!).ToHashSet().SetEquals(selected))
                throw new UserError("La selezione è cambiata: chiudere e riaprire la finestra Record");
            var definitions = Formulas.Formulas().ToDictionary(f => f["nodeid"].Str()!, f => f);
            foreach (var node in derived)
            {
                var nid = node.Get("nodeid").Str()!;
                if (!definitions.TryGetValue(nid, out var f) || !Calculations.Ids.Contains(nid)) throw new UserError("Serie calcolata non attiva in Live");
                nodes.Add(new JsonObject { ["nodeid"] = nid, ["display_name"] = f["name"]?.DeepClone(), ["unit"] = f["unit"].Truthy() ? f["unit"]!.DeepClone() : f["unit_hint"]?.DeepClone(), ["calculated"] = true });
            }
            await Task.Run(() => Calculations.Flush());
            await Task.Run(() => Acquisition.Flush(TimeSpan.FromSeconds(5)));
            Acquisition.ResetFailed();
            options = (JsonObject)options.DeepClone();
            options["conversion_isolated"] = true;
            foreach (var node in nodes)
            {
                var protocol = node.Get("nodeid").Str()!.StartsWith("calc:", StringComparison.Ordinal) ? "calculated" : ProtocolOf(node.Get("nodeid").Str()!);
                if (protocol != "calculated") Acquisition.Connection(protocol, protocol);
            }
            var connections = Acquisition.Catalog();
            options["primary_connections"] = Py.ToNode(connections.ToDictionary(k => k.Key, k => (object?)k.Value));
            if (derived.Count > 0) options["calculation_formulas"] = new JsonArray(Formulas.Formulas().Select(f => (JsonNode?)f).ToArray());
            var sessions = Sources.Sessions;
            foreach (var session in sessions.Values) session.CheckLoss();
            options["acquisition_info"] = new JsonObject
            {
                ["protocols"] = new JsonArray(sessions.Keys.Select(k => (JsonNode?)k).ToArray()), ["time_basis"] = "PC UTC arrival",
                ["source_intervals"] = new JsonObject(sessions.Select(s => KeyValuePair.Create(s.Key, (JsonNode?)s.Value.Period))),
                ["original_clocks"] = "source_timestamp, server_timestamp, ads_filetime, acquisition_timestamp_ms",
                ["signal_protocols"] = new JsonObject(nodes.Select(n => KeyValuePair.Create(n.Get("nodeid").Str()!, (JsonNode?)(n.Get("nodeid").Str()!.StartsWith("calc:", StringComparison.Ordinal) ? "calculated" : ProtocolOf(n.Get("nodeid").Str()!))))),
            };
            var period = sessions.Count > 0 ? sessions.Values.Min(s => s.Period) : SampleMs;
            recordingAccept = false;
            recordingBegin = null;
            Dictionary<string, object?> state;
            try
            {
                var specs = nodes.Select(n => NodeSpec.FromJson(JsonDocument.Parse(n!.ToJsonString()).RootElement)).ToList();
                using var optionsDoc = JsonDocument.Parse(options.ToJsonString());
                var endpoint = "mixed://" + string.Join("/", sessions.Keys);
                stopping = false;
                state = await Task.Run(() => Recording.Start(format!.ToLowerInvariant(), specs, endpoint, period, optionsDoc.RootElement, connections));
            }
            finally
            {
                if (derived.Count > 0) recordingBegin = Acquisition.LastTimestamp;
                multiBegin = Clock.UtcNs() / 1e6;
                multiEnd = null;
                recordingAccept = true;
            }
            return state;
        }
        finally { CommandLock.Release(); }
    }

    /// <summary>finish_recording: fence arrivals, drain the queues, then stop the session.</summary>
    public Dictionary<string, object?> FinishRecording(string reason = "user", double timeout = 5)
    {
        stopping = true;
        multiEnd = Clock.UtcNs() / 1e6;
        Gate.Fence();
        var span = TimeSpan.FromSeconds(timeout);
        if (Sources.Enabled)
        {
            Sources.Flush(span);
            Recording.SourceDiagnostics(Sources.Status());
        }
        else if (!Acquisition.Flush(span)) Recording.Fail("Coda Acquisition Engine non drenata alla chiusura");
        try { Calculations.Flush(); } catch (UserError exc) { Log.Warning(exc.Message); }
        FlushLive(TimeSpan.FromSeconds(1));
        Events.Emit("recording_stop_fence", null, new()
        {
            ["reason"] = reason, ["received_end_ns"] = Clock.UtcNs().ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["acquisition"] = Acquisition.Status(), ["calculations"] = Calculations.Status(),
        });
        return Recording.Stop(reason, reason == "user" ? .15 : 0);
    }

    public async Task<Dictionary<string, object?>> StopRecording(string? ident)
    {
        await CommandLock.WaitAsync();
        try
        {
            if (!string.IsNullOrEmpty(ident) && Recording.Status()["recording_id"] as string != ident)
                throw new UserError("Registrazione cambiata: aggiornare lo stato prima di premere Stop");
            return await Task.Run(() => FinishRecording());
        }
        finally { CommandLock.Release(); }
    }

    // ---- Catalog ------------------------------------------------------------------------------

    public SourceDriver RequireConnection(string protocol)
    {
        // A session still opening (OPC UA discovery and session take longer than MQTT) is awaited briefly.
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (DateTime.UtcNow < deadline && Sources.Sessions.TryGetValue(protocol, out var session) && session.Enabled
               && session.State is "connecting" or "reconnecting" && !session.EverOpened)
            Thread.Sleep(50);
        try { return Sources.Client(protocol); }
        catch (SourceException exc) { throw new UserError(Sources.Enabled ? exc.Message : "Client non connesso"); }
    }

    bool SeparateCatalog(string protocol) => protocol == "ads" || protocol == "s7" && ActiveConfig?["s7_mode"].Str() == "symbolic";

    async Task<object?> CatalogOperation(string operation, string nodeid, CancellationToken ct)
    {
        var protocol = ProtocolOf(nodeid);
        async Task<object?> Apply(SourceDriver driver) => operation switch
        {
            "inspect" => await driver.InspectAsync(nodeid, ct),
            "browse" => await driver.BrowseAsync(nodeid, ct),
            _ => await driver.ScanItemsAsync(nodeid, ct),
        };
        if (!SeparateCatalog(protocol)) return await Apply(RequireConnection(protocol));
        if (!Sources.Sessions.TryGetValue(protocol, out var session)) throw new UserError("Client non connesso");
        await using var driver = DriverFactory.Create(protocol, session.Cfg, catalog: true);
        await driver.ConnectAsync(ct);
        return await Apply(driver);
    }

    public async Task<object?> Inspect(string nodeid)
    {
        using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(180));
        try { return await CatalogOperation("inspect", nodeid, cancel.Token); }
        catch (OperationCanceledException) { throw new UserError("Operazione scaduta: controllare endpoint, rete e server."); }
    }

    public async Task<List<object?>> Browse(string nodeid)
    {
        using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(180));
        try { return (await CatalogOperation("browse", nodeid, cancel.Token) as System.Collections.IEnumerable)?.Cast<object?>().ToList() ?? new(); }
        catch (OperationCanceledException) { throw new UserError("Operazione scaduta: controllare endpoint, rete e server."); }
    }

    public async Task<string> StartScan(JsonObject cfg)
    {
        var protocol = cfg["source"].Str() ?? "opcua";
        var client = RequireConnection(protocol);
        var root = protocol switch
        {
            "mqtt" => cfg["mqtt_root"].Str(),
            "s7" => cfg[(cfg["s7_mode"].Str() ?? "classic") switch { "symbolic" => "s7_symbolic_root", "udp" => "s7_udp_root", _ => "s7_root" }].Str(),
            "ads" => cfg["ads_root"].Str(),
            _ => cfg["root_nodeid"].Str(),
        } ?? "";
        var limit = (int)(cfg["scan_limit"].Num() ?? 50000);
        await StopScan();
        var job = new Dictionary<string, object?>
        {
            ["id"] = Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator.GetBytes(8)).ToLowerInvariant(), ["state"] = "running", ["root"] = root,
            ["limit"] = (long)limit, ["nodes"] = new List<object?>(), ["visited"] = 0L, ["pending"] = 1L, ["errors"] = new List<object?>(), ["error_count"] = 0L,
        };
        lock (Lock) Scan = job;
        var cancel = scanCancel = new CancellationTokenSource();
        scanTask = Task.Run(() => ScanSources(job, root, limit, cancel.Token));
        return (string)job["id"]!;
    }

    async Task ScanSources(Dictionary<string, object?> job, string root, int limit, CancellationToken ct)
    {
        try
        {
            var protocol = ProtocolOf(root);
            List<object?> items;
            if (protocol is "mqtt" or "s7")
                items = (await CatalogOperation("scan", root, ct) as System.Collections.IEnumerable)?.Cast<object?>().ToList() ?? new();
            else
            {
                var pending = new Queue<string>([root]);
                var seen = new HashSet<string>();
                items = new();
                while (pending.Count > 0 && items.Count < limit)
                {
                    ct.ThrowIfCancellationRequested();
                    var nid = pending.Dequeue();
                    if (!seen.Add(nid)) continue;
                    var item = (Dictionary<string, object?>)(await CatalogOperation("inspect", nid, ct))!;
                    items.Add(item);
                    if (item.GetValueOrDefault("has_children") is true || item.GetValueOrDefault("node_class") as string != "Variable")
                        foreach (var child in (await CatalogOperation("browse", nid, ct) as System.Collections.IEnumerable)?.Cast<Dictionary<string, object?>>() ?? [])
                            pending.Enqueue((string)child["nodeid"]!);
                    lock (Lock) job["visited"] = (long)items.Count;
                }
            }
            lock (Lock)
            {
                job["nodes"] = items.Take(limit).ToList();
                job["visited"] = (long)Math.Min(items.Count, limit);
                job["state"] = items.Count >= limit ? "limited" : "completed";
            }
        }
        catch (OperationCanceledException) { lock (Lock) job["state"] = "cancelled"; }
        catch (Exception exc) { lock (Lock) { job["state"] = "failed"; job["error"] = exc.Message; } }
        finally { lock (Lock) { job["pending"] = 0L; job["finished_at"] = Py.NowIso(); } }
    }

    public async Task StopScan()
    {
        var task = scanTask;
        scanCancel?.Cancel();
        if (task != null) { try { await task; } catch (Exception) { } }
        scanTask = null;
    }

    public Dictionary<string, object?> ScanStatus(string? expected, int offset, int count)
    {
        lock (Lock)
        {
            var job = Scan;
            if (!string.IsNullOrEmpty(expected) && expected != (string?)job["id"]) throw new HttpError(409, "La scansione è stata sostituita da una nuova richiesta");
            var nodes = (List<object?>)job["nodes"]!;
            var output = job.Where(k => k.Key != "nodes").ToDictionary(k => k.Key, k => k.Value);
            output["nodes"] = nodes.Skip(offset).Take(count).ToList();
            output["total"] = (long)nodes.Count;
            output["next_offset"] = (long)Math.Min(offset + count, nodes.Count);
            return output;
        }
    }

    // ---- Live analysis inputs --------------------------------------------------------------

    public ArchiveReference? LiveReference(IList<string> ids, double start = double.NegativeInfinity, double end = double.PositiveInfinity)
    {
        lock (Lock)
        {
            if (Archive.Active && ids.All(n => samples.TryGetValue(n, out var b) ? b.ArchiveGeneration == Archive.Generation : Archive.Counts.GetValueOrDefault(n) > 0))
                return Archive.Reference(ids, start, end);
            return null;
        }
    }

    public Dictionary<string, List<LivePoint>> Captured(IEnumerable<string> ids, double start = double.NegativeInfinity, double end = double.PositiveInfinity)
    {
        lock (Lock)
            return ids.Distinct().ToDictionary(n => n, n => samples.TryGetValue(n, out var b) ? b.Points.Where(p => start <= p.T && p.T <= end).ToList() : new List<LivePoint>());
    }

    public IEnumerable<string> StoredCalculated()
    {
        lock (Lock) return samples.Keys.Concat(Archive.Counts.Keys).Where(n => n.StartsWith("calc:", StringComparison.Ordinal)).Distinct().ToList();
    }

    public IEnumerable<string> AllLiveIds()
    {
        lock (Lock) return samples.Keys.Concat(Archive.Counts.Keys).Distinct().ToList();
    }

    public void Dispose()
    {
        closed = true;
        try { Sources.Stop().Wait(TimeSpan.FromSeconds(15)); } catch (Exception) { }
        try { FinishRecording("application_close", 5); } catch (Exception) { }
        Recording.Close();
        Acquisition.Close();
        Calculations.Close();
        Comparisons.Close();
        Events.Close();
        Archive.Dispose();
    }
}
