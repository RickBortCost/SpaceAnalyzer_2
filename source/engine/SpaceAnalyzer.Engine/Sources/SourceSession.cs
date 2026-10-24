using System.Globalization;
using System.Text.Json;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Sources;

/// <summary>
/// Independent lifecycle of one protocol (port of source_sessions.SourceSession and the
/// single-source sampler loops of app.py): connect, acquire, detect loss, reconnect.
/// Callbacks carry the session token: an obsolete client cannot enter a new session.
/// </summary>
public sealed class SourceSession
{
    readonly SourceController owner;
    readonly Func<JsonElement, SourceDriver> factory;
    readonly object gate = new();
    CancellationTokenSource? cancel;
    Task? task;
    Dictionary<string, object?>? reportedLoss;
    readonly Queue<double> periods = new();

    public string Protocol { get; }
    public JsonElement Cfg { get; }
    public SourceDriver? Driver { get; private set; }
    public bool Enabled { get; private set; } = true;
    public string State { get; private set; } = "connecting";
    public string Error { get; set; } = "";
    public long Reconnections { get; private set; }
    public long Received { get; private set; }
    public long LateSamples { get; private set; }
    public long Token { get; private set; }
    public bool EverOpened { get; private set; }
    public string Mode { get; set; } = "";
    public double LastCycleMs { get; private set; }
    public double ActualPeriodMs { get; private set; }
    public long SkippedTicks { get; private set; }
    public long Cycles { get; private set; }
    double? lastTime;
    Dictionary<string, object?> diagnostics = new();
    readonly ManualResetEventSlim idle = new(true);
    TaskCompletionSource<Exception?>? firstOpen;

    public SourceSession(SourceController owner, string protocol, JsonElement cfg, Func<JsonElement, SourceDriver> factory)
    {
        this.owner = owner; Protocol = protocol; Cfg = cfg; this.factory = factory;
    }

    public int Period
    {
        get
        {
            var selection = owner.CurrentSelection;
            if (Cfg.TryGetProperty("source_intervals", out var intervals) && intervals.ValueKind == JsonValueKind.Object
                && intervals.TryGetProperty(Protocol, out var p) && p.ValueKind == JsonValueKind.Number)
                return Math.Max(1, (int)p.GetDouble());
            return owner.Multi ? Math.Max(Protocol == "ads" ? 1 : 10, selection.Period) : Math.Max(1, selection.Period);
        }
    }

    public void Emit(string kind, Action<Utf8JsonWriter> details) => owner.EmitFor(Protocol, kind, details);

    public void Fatal(string message) => owner.Fatal(message);

    public Selection Selection()
    {
        var all = owner.CurrentSelection;
        return new Selection(all.Ids.Where(n => Sample.ProtocolOf(n) == Protocol).ToList(), Period, all.Generation);
    }

    public Task<Exception?> Start()
    {
        cancel = new CancellationTokenSource();
        firstOpen = new TaskCompletionSource<Exception?>(TaskCreationOptions.RunContinuationsAsynchronously);
        Enabled = true;
        task = Task.Factory.StartNew(() => Run(cancel.Token), CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default).Unwrap();
        return firstOpen.Task;
    }

    /// <summary>Rows accepted from the driver: one shared PC UTC arrival axis; exact source clocks kept.</summary>
    public bool Accept(long token, double originalMs, List<Sample> rows, int period, Generation? generation, double backpressure = 0)
    {
        if (!Enabled || token != Token || rows.Count == 0) return false;
        var nowMono = Clock.MonoNs(); var nowNs = Clock.UtcNs(); var now = nowNs / 1e6;
        var groups = new SortedDictionary<double, List<Sample>>();
        var order = new List<double>();
        foreach (var row in rows)
        {
            if (row.ReceivedNs == 0) row.ReceivedNs = nowNs;
            if (row.ReceivedMonoNs == 0) row.ReceivedMonoNs = nowMono;
            if (row.AcquisitionOrigin == "subscription_snapshot") { row.ReceivedNs = nowNs; row.ReceivedMonoNs = nowMono; }
            row.SourceProtocol = Protocol;
            row.SourceEpoch = Token.ToString(CultureInfo.InvariantCulture) + ":" + (row.SourceEpoch ?? "0");
            row.AcquisitionTimestampMs ??= originalMs;
            // A single source keeps its own time axis (MQTT payload time, PLC UDP time).
            var t = owner.Multi ? row.ReceivedMs ?? now : originalMs;
            row.LocalTs ??= Values.IsoFromMs(t);
            if (!groups.TryGetValue(t, out var batch)) { groups[t] = batch = new List<Sample>(); order.Add(t); }
            batch.Add(row);
        }
        var accepted = true;
        foreach (var t in order)
        {
            var batch = groups[t];
            lock (gate)
            {
                if (lastTime is double last && t < last) LateSamples += batch.Count;
                lastTime = lastTime is double l ? Math.Max(l, t) : t;
                Received += batch.Count;
            }
            accepted &= owner.Sink(Protocol, t, batch, period, generation, backpressure);
        }
        return accepted;
    }

    async Task Run(CancellationToken ct)
    {
        try
        {
            var autoReconnect = !(Cfg.TryGetProperty("auto_reconnect", out var ar) && ar.ValueKind == JsonValueKind.False);
            while (Enabled && !ct.IsCancellationRequested)
            {
                try
                {
                    if (Driver == null)
                    {
                        await Open(ct);
                        if (EverOpened) Reconnections++;
                        EverOpened = true;
                        firstOpen?.TrySetResult(null);
                    }
                    await Acquire(ct);
                }
                catch (OperationCanceledException) when (ct.IsCancellationRequested) { break; }
                catch (Exception exc)
                {
                    Error = exc.Message.Length > 0 ? exc.Message : exc.GetType().Name;
                    if (firstOpen is { Task.IsCompleted: false } && !EverOpened && owner.WaitFirst)
                    {
                        // A single-source connect reports the failure to the user instead of retrying.
                        await Close();
                        State = "disconnected"; Enabled = false;
                        firstOpen.TrySetResult(exc);
                        owner.StateChanged();
                        return;
                    }
                    var token = Token;
                    owner.EmitFor(Protocol, "source_disconnected", w => { w.WriteString("protocol", Protocol); w.WriteNumber("epoch", token); w.WriteString("error", Error); });
                    var lostDriver = Driver;
                    await Close();
                    var selection = Selection();
                    var now = Clock.UtcNs() / 1e6;
                    var lost = selection.Ids.Select(n => lostDriver != null ? lostDriver.LostRow(n, Error, now) : DriverFactory.LostRow(Protocol, n, Error, now)).ToList();
                    Token++;
                    Accept(Token, now, lost, selection.Period, selection.Generation);
                    State = autoReconnect ? "reconnecting" : "disconnected";
                    owner.StateChanged();
                    if (!autoReconnect) { owner.ConnectionLost(this); break; }
                    try { await Task.Delay(1000, ct); } catch (OperationCanceledException) { break; }
                }
            }
        }
        finally
        {
            await Close();
            firstOpen?.TrySetResult(null);
        }
    }

    async Task Open(CancellationToken ct)
    {
        State = EverOpened ? "reconnecting" : "connecting";
        owner.StateChanged();
        var driver = owner.Create(Protocol, Cfg);
        try { await driver.ConnectAsync(ct); }
        catch
        {
            try { await driver.DisconnectAsync(); } catch (Exception) { }
            throw;
        }
        Driver = driver;
        Token++;
        var token = Token;
        diagnostics = new();
        State = "connected"; Error = "";
        var reconnections = Reconnections;
        owner.EmitFor(Protocol, "source_connected", w => { w.WriteString("protocol", Protocol); w.WriteNumber("epoch", token); w.WriteNumber("reconnections", reconnections); });
        owner.StateChanged();
        if (driver.Streaming) driver.ConfigureStream(Selection, (t, rows, period, generation) => Accept(token, t, rows, period, generation, Protocol == "mqtt" ? .5 : 0));
    }

    async Task Acquire(CancellationToken ct)
    {
        var driver = Driver!;
        if (driver is IAcquisitionLoop loop)
        {
            await loop.RunAsync(this, ct);
            return;
        }
        if (driver.Streaming)
        {
            while (!ct.IsCancellationRequested && Driver == driver)
            {
                await driver.ReadStateAsync(ct);
                CheckLoss();
                Mode = driver.Info.GetValueOrDefault("read_mode") as string ?? Mode;
                await Task.Delay(100, ct);
            }
            return;
        }
        await PollLoop(driver, ct);
    }

    /// <summary>Shared asynchronous polling (ADS Read, S7 ReadVar, S7 symbolic): skipped periods are counted.</summary>
    async Task PollLoop(SourceDriver driver, CancellationToken ct)
    {
        double? lastStart = null;
        var heartbeat = double.NegativeInfinity;
        var deadline = Seconds();
        using var timer = HighResolutionTimer.Begin();
        while (!ct.IsCancellationRequested && Driver == driver)
        {
            var start = Seconds();
            var selection = Selection();
            var interval = selection.Ids.Count > 0 ? selection.Period / 1000.0 : .5;
            if (start - heartbeat >= 1)
            {
                await driver.ReadStateAsync(ct);
                await driver.CheckSymbolsAsync(ct);
                heartbeat = start;
            }
            if (selection.Ids.Count > 0)
            {
                var rows = await driver.ReadValuesAsync(selection.Ids, ct);
                Mode = driver.Info.GetValueOrDefault("read_mode") as string ?? "Read";
                var now = Clock.UtcNs() / 1e6;
                Accept(Token, now, rows, selection.Period, selection.Generation);
                ObserveCycle(start, lastStart);
                lastStart = start;
            }
            CheckLoss();
            deadline += interval;
            var nowSeconds = Seconds();
            if (deadline < nowSeconds - interval)
            {
                // Overrun: whole periods are skipped. Count them instead of hiding the loss.
                SkippedTicks += (long)((nowSeconds - deadline) / interval);
                driver.Info["polling_skipped_ticks"] = SkippedTicks;
                deadline = nowSeconds + interval;
            }
            await HighResolutionTimer.DelayUntil(deadline, ct);
        }
    }

    public void ObserveCycle(double start, double? lastStart)
    {
        lock (gate)
        {
            Cycles++;
            LastCycleMs = (Seconds() - start) * 1000;
            ActualPeriodMs = lastStart is double l ? (start - l) * 1000 : 0;
            if (lastStart != null) { periods.Enqueue(ActualPeriodMs); while (periods.Count > 100) periods.Dequeue(); }
        }
    }

    public void SetPeriod(double? periodMs)
    {
        lock (gate)
        {
            periods.Clear();
            if (periodMs is double p) { periods.Enqueue(p); ActualPeriodMs = p; }
        }
    }

    public static double Seconds() => Clock.MonoNs() / 1e9;

    /// <summary>Loss already visible in the driver counters fails the recording and is documented once.</summary>
    public void CheckLoss(SourceDriver? client = null)
    {
        var c = client ?? Driver;
        if (c?.Diagnostics() is { } d) diagnostics = d;
        static bool Positive(object? v) => v switch { long l => l > 0, int i => i > 0, double x => x > 0, _ => false };
        if (new[] { "queue_dropped", "dropped", "lost", "timestamp_errors" }.Any(k => Positive(diagnostics.GetValueOrDefault(k))))
        {
            if (!SameCounters(diagnostics, reportedLoss))
            {
                var snapshot = new Dictionary<string, object?>(diagnostics);
                owner.EmitFor(Protocol, "source_loss", w => { w.WriteString("protocol", Protocol); w.WritePropertyName("diagnostics"); JsonOut.Write(w, snapshot); });
                reportedLoss = snapshot;
            }
            owner.Fatal(Protocol.ToUpperInvariant() + ": perdita campioni rilevata; originali parziali conservati");
        }
        if (c != null && c.Error.Length > 0 && Protocol == "mqtt") owner.Fatal(c.Error);
    }

    static bool SameCounters(Dictionary<string, object?> a, Dictionary<string, object?>? b) =>
        b != null && new[] { "queue_dropped", "dropped", "lost", "timestamp_errors" }.All(k => Equals(a.GetValueOrDefault(k), b.GetValueOrDefault(k)));

    async Task Close()
    {
        CheckLoss();
        idle.Reset();
        try
        {
            var driver = Driver;
            Driver = null;
            if (driver != null)
            {
                try { await driver.DisconnectAsync().WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
                CheckLoss(driver);
            }
        }
        finally { idle.Set(); }
    }

    public async Task Stop()
    {
        Enabled = false;
        cancel?.Cancel();
        if (task != null) { try { await task; } catch (Exception) { } }
        else await Close();
        State = "disconnected";
        owner.StateChanged();
    }

    /// <summary>Drain the client queue (Stop fence); false when originals may be missing.</summary>
    public bool Flush(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        if (!idle.Wait(timeout)) { owner.Fatal(Protocol.ToUpperInvariant() + ": chiusura sorgente non completata alla chiusura della registrazione"); return false; }
        var driver = Driver;
        var ok = true;
        if (driver != null && !driver.Flush(deadline - DateTime.UtcNow > TimeSpan.Zero ? deadline - DateTime.UtcNow : TimeSpan.Zero))
        {
            owner.Fatal(Protocol.ToUpperInvariant() + ": coda non acquisita completamente alla chiusura");
            ok = false;
        }
        CheckLoss();
        return ok;
    }

    public Dictionary<string, object?> Status()
    {
        double? average;
        lock (gate) average = periods.Count > 0 ? periods.Average() : null;
        return new()
        {
            ["enabled"] = Enabled, ["state"] = State, ["error"] = Error, ["reconnections"] = Reconnections,
            ["received_rows"] = Received, ["late_samples"] = LateSamples, ["sample_ms"] = (long)Period,
            ["diagnostics"] = new Dictionary<string, object?>(Driver?.Diagnostics() ?? diagnostics),
            ["info"] = new Dictionary<string, object?>(Driver?.Info ?? new()),
            ["mode"] = Mode, ["last_cycle_ms"] = LastCycleMs, ["actual_period_ms"] = ActualPeriodMs,
            ["average_period_ms"] = average, ["polling_skipped_ticks"] = SkippedTicks, ["cycles"] = Cycles,
        };
    }
}

/// <summary>Drivers with their own acquisition schedule (OPC UA subscriptions, ADS notifications).</summary>
public interface IAcquisitionLoop
{
    Task RunAsync(SourceSession session, CancellationToken ct);
}
