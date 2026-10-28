using System.Text.Json;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>
/// Cyclic ADS notifications (port of ads_notifications.NotificationAcquisition): the router
/// callback only copies bytes and clocks into a bounded queue; this session decodes and
/// delivers every sample, detects missing PLC cycles from FILETIME deltas and watches each
/// signal. The browser never clocks acquisition.
/// </summary>
public sealed class AdsNotifyDriver : AdsDriver, IAcquisitionLoop
{
    const double GapFactor = 1.5;
    const int GapWarmup = 4;
    const uint BlockExcludedFlags = 0x2 | 0x4 | 0x10 | 0x40 | 0x80;
    const int MaxBlock = 65536;
    /// <summary>Automatic notification batching: the router may hold samples up to this long and send them together.</summary>
    public const int AutoBatchMs = 50;

    sealed record Member(string? NodeId, AdsSymbol? Symbol, Block? Block);
    sealed record Block(uint Group, uint Start, uint Length, List<(string nid, AdsSymbol symbol, uint offset)> Members, string Label);
    sealed record Queued(Generation? Generation, Member Member, long Ticks, long ReceivedNs, long ReceivedMono, long Sequence, long Epoch, byte[] Data);

    readonly object gate = new();
    readonly Queue<Queued> queue = new();
    readonly Dictionary<uint, Member> members = new();
    readonly List<uint> subscriptions = new();
    readonly Dictionary<string, long> sampleSequences = new(), lastReceived = new(), lastSource = new();
    readonly Dictionary<string, long> lastArrival = new();
    readonly Dictionary<string, string> watchKey = new();
    readonly Dictionary<string, (long period, long count)> cycleState = new();
    readonly Queue<double> periods = new(), arrivals = new(), latencies = new(), queueDelays = new();
    readonly Queue<Dictionary<string, object?>> lostRanges = new(), sourceGaps = new();
    readonly int baseCapacity;
    readonly long baseByteLimit;
    readonly double headroom;
    int capacity;
    long byteLimit, queuedBytes;
    bool accepting, started;
    string? callbackFault;
    long received, accepted, dropped, processed, clockBackwards, timestampErrors, decodeErrors, highWater, restarts, sequence, sourceEpoch;
    long sourceGapSamples, sourceGapEvents, controlTimeouts;
    string? controlError;
    double? controlLastOk;
    double controlMs;
    bool pausedNotRun;
    string? reference, referenceKey;
    Generation? generation;
    int sampleMs = 10;
    readonly bool grouping;
    readonly int batchMs;
    int maxDelayMs;
    Dictionary<string, object?> layout = new() { ["blocks"] = 0L, ["block_members"] = 0L, ["singles"] = 0L, ["fallback_members"] = 0L };
    Dictionary<string, object?> reported = new();

    public AdsNotifyDriver(JsonElement cfg) : base(cfg)
    {
        baseCapacity = capacity = int.Parse(Environment.GetEnvironmentVariable("SPACE_ADS_QUEUE_SAMPLES") ?? "32768");
        baseByteLimit = byteLimit = long.Parse(Environment.GetEnvironmentVariable("SPACE_ADS_QUEUE_MB") ?? "16") * 1024 * 1024;
        if (capacity is < 1 or > 1000000 || byteLimit is < 1024 or > 256L * 1024 * 1024) throw new SourceException("Limiti buffer ADS non validi");
        headroom = Math.Max(.1, double.Parse(Environment.GetEnvironmentVariable("SPACE_ADS_QUEUE_SECONDS") ?? "2", System.Globalization.CultureInfo.InvariantCulture));
        var value = cfg.TryGetProperty("ads_notify_grouping", out var g) ? (g.ValueKind == JsonValueKind.True ? "1" : g.ValueKind == JsonValueKind.String ? g.GetString() ?? "0" : g.ValueKind == JsonValueKind.Number ? g.GetRawText() : "0")
            : Environment.GetEnvironmentVariable("SPACE_ADS_NOTIFY_GROUPING") ?? "0";
        grouping = value.Trim().ToLowerInvariant() is "1" or "true" or "on" or "yes";
        batchMs = Math.Clamp(Int("ads_notify_batch_ms", 0), 0, 1000);
        Client.Transport.OnNotification = Callback;
    }

    public long Processed { get { lock (gate) return processed; } }
    public long Accepted { get { lock (gate) return accepted; } }

    public override async Task ConnectAsync(CancellationToken ct)
    {
        await base.ConnectAsync(ct);
        Client.Info["read_mode"] = "ADS notifiche cicliche";
        Client.Info["timestamps"] = "ADS FILETIME UTC + ricezione locale";
        Sync();
    }

    // Router/reader thread: copy bytes and clocks only.
    void Callback(uint user, long ticks, byte[] data, long receivedNs, long receivedMono)
    {
        try { Enqueue(user, ticks, data, receivedNs, receivedMono); }
        catch (Exception exc) { lock (gate) callbackFault ??= "Callback ADS: " + exc.Message; }
    }

    void Enqueue(uint user, long ticks, byte[] data, long receivedNs, long receivedMono)
    {
        lock (gate)
        {
            if (!accepting || !members.TryGetValue(user, out var member)) return;
            var key = member.NodeId ?? member.Block!.Label;
            var count = member.NodeId != null ? 1 : member.Block!.Members.Count;
            var expected = member.NodeId != null ? member.Symbol!.Size : member.Block!.Length;
            received += count;
            var seq = sampleSequences[key] = sampleSequences.GetValueOrDefault(key) + 1;
            if (data.Length != expected || data.Length > MaxBlock)
            {
                dropped += count; Lost(key, seq, "invalid_size");
                callbackFault = "Dimensione notifica ADS incoerente: " + key;
                return;
            }
            if (queue.Count >= capacity || queuedBytes + data.Length > byteLimit)
            {
                dropped += count; Lost(key, seq, "overflow");
                callbackFault = "Overflow coda ADS: campioni persi; acquisizione interrotta";
                return;
            }
            queue.Enqueue(new Queued(generation, member, ticks, receivedNs, receivedMono, seq, sourceEpoch, data));
            queuedBytes += data.Length; accepted += count; lastReceived[key] = receivedMono;
            highWater = Math.Max(highWater, queue.Count);
        }
    }

    void Lost(string key, long seq, string reason)
    {
        lostRanges.Enqueue(new() { ["key"] = key, ["first"] = seq, ["last"] = seq, ["reason"] = reason });
        while (lostRanges.Count > 128) lostRanges.Dequeue();
    }

    /// <summary>Queue sized for about headroom seconds of traffic; configured limits stay a floor.</summary>
    void ScaleQueue(int count, int periodMs)
    {
        var rate = count * 1000.0 / Math.Max(1.0, periodMs);
        lock (gate)
        {
            capacity = (int)Math.Min(1000000, Math.Max(baseCapacity, (long)(rate * headroom)));
            byteLimit = Math.Min(256L * 1024 * 1024, Math.Max(baseByteLimit, capacity * 64L));
        }
    }

    /// <summary>plan_blocks: contiguous absolute-address symbols of one index group share one notification.</summary>
    static (List<(uint group, uint start, uint length, List<(string, AdsSymbol, uint)> members)> blocks, List<(string, AdsSymbol)> singles) PlanBlocks(List<(string nid, AdsSymbol symbol)> entries)
    {
        var singles = new List<(string, AdsSymbol)>();
        var candidates = new List<(uint group, uint offset, uint size, string nid, AdsSymbol symbol)>();
        foreach (var (nid, s) in entries)
        {
            if (s.Group is not uint group || s.Offset is not uint offset || group is 0xF005 or 0xF003 || (s.Flags & BlockExcludedFlags) != 0 || s.Size is 0 or > MaxBlock)
            { singles.Add((nid, s)); continue; }
            candidates.Add((group, offset, s.Size, nid, s));
        }
        candidates = candidates.OrderBy(c => c.group).ThenBy(c => c.offset).ToList();
        var planned = new List<(uint group, uint start, uint length, List<(string, AdsSymbol, uint)> members)>();
        foreach (var (group, offset, size, nid, s) in candidates)
        {
            if (planned.Count > 0 && planned[^1].group == group && offset + size - planned[^1].start <= MaxBlock)
            {
                var b = planned[^1];
                b.members.Add((nid, s, offset - b.start));
                planned[^1] = (b.group, b.start, Math.Max(b.length, offset + size - b.start), b.members);
            }
            else planned.Add((group, offset, size, [(nid, s, 0u)]));
        }
        var blocks = new List<(uint, uint, uint, List<(string, AdsSymbol, uint)>)>();
        foreach (var p in planned)
        {
            if (p.members.Count < 2) singles.AddRange(p.members.Select(m => (m.Item1, m.Item2)));
            else blocks.Add(p);
        }
        return (blocks, singles);
    }

    async Task RemoveSubscriptions(SourceSession session, CancellationToken ct)
    {
        lock (gate) accepting = false;
        foreach (var handle in subscriptions.ToList())
        {
            try { await Client.Transport.DeleteNotification(handle, ct); }
            catch (Exception)
            {
                subscriptions.Clear();
                // Close the port before symbol handles or callback storage can be reused.
                await Client.Transport.Abort();
                throw;
            }
        }
        subscriptions.Clear();
        // Deliver already copied samples before releasing handles or reconfiguring.
        while (true)
        {
            lock (gate) if (queue.Count == 0) break;
            Drain(session, sampleMs);
        }
        await Client.ReleaseHandles(ct);
        lock (gate) { queue.Clear(); queuedBytes = 0; members.Clear(); }
    }

    async Task Subscribe(SourceSession session, IReadOnlyList<string> nids, int period, Generation? gen, CancellationToken ct)
    {
        await RemoveSubscriptions(session, ct);
        if (started) restarts++;
        sampleMs = period;
        ScaleQueue(nids.Count, period);
        lock (gate)
        {
            sourceEpoch++; generation = gen; lastReceived.Clear(); lastSource.Clear(); lastArrival.Clear(); cycleState.Clear();
            periods.Clear(); arrivals.Clear(); latencies.Clear(); queueDelays.Clear();
            reference = nids.Count > 0 ? nids[0] : null; watchKey.Clear();
            accepting = true;
        }
        var entries = new List<(string, AdsSymbol)>();
        foreach (var nid in nids)
        {
            var name = AdsProtocol.SymbolName(nid);
            var symbol = await Client.GetSymbol(name, ct);
            if (symbol.Size is 0 or > 65536) throw new SourceException("Notifiche ADS: selezionare membri di dimensione 1..65536 byte: " + name);
            entries.Add((nid, symbol));
        }
        var (blocks, singles) = grouping ? PlanBlocks(entries) : (new(), entries);
        long blockCount = 0, blockMembers = 0, singleCount = 0, fallback = 0;
        // Native units are 100 ns; SERVERCYCLE = 3, samples batched by the router up to the maximum delay.
        var cycle = (uint)(period * 10000);
        maxDelayMs = MaxDelayMs(batchMs, period);
        var maxDelay = (uint)(maxDelayMs * 10000);
        foreach (var (group, start, length, list) in blocks)
        {
            try
            {
                // The range must be readable as one area before it replaces per-symbol handles.
                if ((await Client.Read(group, start, length, ct)).Length != length) throw new InvalidDataException("lettura parziale");
            }
            catch (Exception exc) when (exc is AdsException or InvalidDataException)
            {
                singles.AddRange(list.Select(m => (m.Item1, m.Item2))); fallback += list.Count;
                continue;
            }
            var label = $"block:0x{group:X}+{start}[{length}]";
            var block = new Block(group, start, length, list, label);
            uint user;
            lock (gate)
            {
                user = (uint)++sequence;
                members[user] = new Member(null, null, block);
                foreach (var (nid, _, _) in list) watchKey[nid] = label;
            }
            subscriptions.Add(await Client.Transport.AddNotification(group, start, length, maxDelay, cycle, user, ct));
            blockCount++; blockMembers += list.Count;
        }
        foreach (var (nid, symbol) in singles)
        {
            var handle = await Client.Handle(AdsProtocol.SymbolName(nid), ct);
            uint user;
            lock (gate) { user = (uint)++sequence; members[user] = new Member(nid, symbol, null); }
            subscriptions.Add(await Client.Transport.AddNotification(0xF005, handle, symbol.Size, maxDelay, cycle, user, ct));
            singleCount++;
        }
        layout = new() { ["blocks"] = blockCount, ["block_members"] = blockMembers, ["singles"] = singleCount, ["fallback_members"] = fallback };
        lock (gate) referenceKey = reference != null ? watchKey.GetValueOrDefault(reference, reference) : null;
        started = true;
    }

    /// <summary>
    /// Maximum delay of the notifications (nMaxDelay): the TwinCAT router samples every cycle and may send the samples of up to this
    /// interval together, each with its own PLC timestamp. Fewer, larger messages are far less exposed to Windows scheduling delays.
    /// 0 = automatic (50 ms, at least one period); otherwise the configured value, at least one period.
    /// </summary>
    public static int MaxDelayMs(int batchMs, int period) => Math.Max(period, batchMs > 0 ? batchMs : AutoBatchMs);

    /// <summary>Samples missing from a cyclic stream (one channel or one block), learned per stream.</summary>
    long CheckCycle(string key, long ticks, long previous, int period, int count)
    {
        var delta = ticks - previous;
        if (delta <= 0) return 0;
        var state = cycleState.TryGetValue(key, out var s) ? (Math.Min(s.period, delta), s.count + 1) : (delta, 0L);
        cycleState[key] = state;
        var learned = Math.Max(period * 10000L, state.Item1);
        if (state.Item2 <= GapWarmup || delta <= learned * GapFactor) return 0;
        var cycles = Math.Max(1, PyJson.Round((double)delta / learned) - 1);
        var missing = cycles * count;
        sourceGapSamples += missing; sourceGapEvents++;
        sourceGaps.Enqueue(new()
        {
            ["nodeid"] = key, ["after_filetime"] = previous.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["before_filetime"] = ticks.ToString(System.Globalization.CultureInfo.InvariantCulture), ["missing_samples"] = missing,
            ["missing_cycles"] = cycles, ["members"] = (long)count, ["period_ms"] = learned / 10000.0, ["plc_not_run"] = pausedNotRun,
        });
        while (sourceGaps.Count > 128) sourceGaps.Dequeue();
        return missing;
    }

    void Drain(SourceSession session, int period)
    {
        List<Queued> batch;
        lock (gate)
        {
            batch = new List<Queued>(Math.Min(4096, queue.Count));
            while (batch.Count < 4096 && queue.Count > 0) batch.Add(queue.Dequeue());
            queuedBytes -= batch.Sum(b => (long)b.Data.Length);
        }
        if (batch.Count == 0) return;
        var groups = new Dictionary<(Generation?, long), List<Sample>>();
        var order = new List<(Generation?, long)>();
        var iso = new Dictionary<long, string>();
        var nowMono = Clock.MonoNs();
        foreach (var item in batch)
        lock (gate)
        {
            // Clocks and continuity belong to the notification: a block shares one PLC timestamp.
            var key = item.Member.NodeId ?? item.Member.Block!.Label;
            var count = item.Member.NodeId != null ? 1 : item.Member.Block!.Members.Count;
            if (item.Ticks < AdsProtocol.FiletimeEpoch) timestampErrors += count;
            var hasPrevious = lastSource.TryGetValue(key, out var previous);
            var backwards = hasPrevious && item.Ticks < previous;
            if (backwards) clockBackwards += count;
            if (hasPrevious)
            {
                CheckCycle(key, item.Ticks, previous, period, count);
                if (key == referenceKey)
                {
                    periods.Enqueue((item.Ticks - previous) / 10000.0); Trim(periods);
                    arrivals.Enqueue((item.ReceivedMono - lastArrival[key]) / 1e6); Trim(arrivals);
                }
            }
            lastSource[key] = item.Ticks; lastArrival[key] = item.ReceivedMono;
            var ts = (item.Ticks - AdsProtocol.FiletimeEpoch) / 10000.0;
            if (!iso.TryGetValue(item.Ticks, out var sourceTs) && item.Ticks >= AdsProtocol.FiletimeEpoch) iso[item.Ticks] = sourceTs = AdsProtocol.FiletimeIso(item.Ticks);
            latencies.Enqueue(item.ReceivedNs / 1e6 - ts); Trim(latencies);
            queueDelays.Enqueue((nowMono - item.ReceivedMono) / 1e6); Trim(queueDelays);
            var groupKey = (item.Generation, item.Ticks);
            if (!groups.TryGetValue(groupKey, out var rows)) { groups[groupKey] = rows = new(); order.Add(groupKey); }
            var parts = item.Member.NodeId != null
                ? [(item.Member.NodeId, item.Member.Symbol!, item.Data)]
                : item.Member.Block!.Members.Select(m => (m.nid, m.symbol, item.Data[(int)m.offset..(int)(m.offset + m.symbol.Size)])).ToList();
            foreach (var (nid, symbol, payload) in parts)
            {
                Sample row;
                try { row = Client.Row(nid, symbol, payload); }
                catch (Exception exc)
                {
                    decodeErrors++; callbackFault = "ADS decode: " + exc.Message;
                    row = new Sample { NodeId = nid, Value = null, PlotValue = null, DataType = symbol.Type, Quality = "Bad", Good = false, Status = "ADSDecodeError", Error = exc.Message, SourceBinary = payload, HasSourceValue = true };
                }
                row.SourceTs = sourceTs; row.ServerTs = null; row.SourceFiletime = item.Ticks.ToString(System.Globalization.CultureInfo.InvariantCulture);
                row.SourceSequence = item.Sequence; row.SourceEpoch = item.Epoch.ToString(System.Globalization.CultureInfo.InvariantCulture);
                row.ReceivedMonoNs = item.ReceivedMono; row.ClockDiscontinuity = backwards || item.Ticks < AdsProtocol.FiletimeEpoch;
                row.ReceivedNs = item.ReceivedNs; row.LocalTs = AdsProtocol.MicroIso(item.ReceivedNs); row.ReceivedMs = item.ReceivedNs / 1e6;
                row.TimestampOrigin = "ADS notification";
                rows.Add(row);
            }
        }
        foreach (var key in order)
            session.Accept(session.Token, (key.Item2 - AdsProtocol.FiletimeEpoch) / 10000.0, groups[key], period, key.Item1);
        lock (gate) processed += batch.Sum(b => (long)(b.Member.NodeId != null ? 1 : b.Member.Block!.Members.Count));
    }

    static void Trim(Queue<double> q) { while (q.Count > 2048) q.Dequeue(); }

    public Dictionary<string, object?> Stats()
    {
        List<double> values, arrivalList, latencyList, delayList;
        List<Dictionary<string, object?>> lost, gaps;
        int pending; long size;
        lock (gate)
        {
            pending = queue.Count; size = queuedBytes;
            values = periods.OrderBy(v => v).ToList(); arrivalList = arrivals.ToList();
            latencyList = latencies.ToList(); delayList = queueDelays.ToList();
            lost = lostRanges.ToList(); gaps = sourceGaps.ToList();
        }
        var result = new Dictionary<string, object?>
        {
            ["mode"] = "ADS notifiche cicliche", ["received"] = received, ["accepted"] = accepted, ["dropped"] = dropped,
            ["lost_ranges"] = lost, ["clock_backwards"] = clockBackwards, ["timestamp_errors"] = timestampErrors,
            ["queue_samples"] = (long)pending, ["queue_bytes"] = size, ["queue_capacity"] = (long)capacity, ["plc_not_run"] = pausedNotRun,
            ["reference_signal"] = reference, ["processed"] = processed, ["decode_errors"] = decodeErrors, ["queue_high_water"] = highWater,
            ["control_error"] = controlError, ["control_timeouts"] = controlTimeouts, ["control_last_ok"] = controlLastOk,
            ["source_period_ms"] = values.Count > 0 ? values.Average() : null, ["source_min_ms"] = values.Count > 0 ? values[0] : null,
            ["source_max_ms"] = values.Count > 0 ? values[^1] : null,
            ["source_p95_ms"] = values.Count > 0 ? values[Math.Min(values.Count - 1, (int)(.95 * (values.Count - 1)))] : null,
            ["arrival_period_ms"] = arrivalList.Count > 0 ? arrivalList.Average() : null,
            ["clock_delta_ms"] = latencyList.Count > 0 ? latencyList.Average() : null, ["queue_delay_ms"] = delayList.Count > 0 ? delayList.Max() : null,
            ["notify_grouping"] = grouping, ["notify_layout"] = new Dictionary<string, object?>(layout),
            ["source_gap_samples"] = sourceGapSamples, ["source_gap_events"] = sourceGapEvents, ["source_gaps"] = gaps,
            ["source_gap_policy"] = "missing PLC cycles counted per channel from FILETIME deltas; recording continues and the gap is documented",
            ["control_ms"] = controlMs, ["subscription_restarts"] = restarts,
            ["notify_max_delay_ms"] = (long)maxDelayMs, ["notify_batch_setting_ms"] = (long)batchMs, ["system"] = SystemTuning.Report(),
        };
        foreach (var (k, v) in Client.Info) result[k] = v;
        return result;
    }

    public override Dictionary<string, object?>? Diagnostics() => Stats();

    public override bool Flush(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        long target;
        lock (gate) target = accepted;
        while (Processed < target)
        {
            if (DateTime.UtcNow >= deadline) return false;
            Thread.Sleep(1);
        }
        return true;
    }

    /// <summary>New upstream gaps between two snapshots are documented once, as ads_source_gap.</summary>
    void Report(SourceSession session)
    {
        var current = Stats();
        long Get(Dictionary<string, object?> d, string k) => d.GetValueOrDefault(k) is long l ? l : 0;
        var events = Get(current, "source_gap_events") - Get(reported, "source_gap_events");
        if (events > 0)
        {
            var missing = Get(current, "source_gap_samples") - Get(reported, "source_gap_samples");
            var gaps = ((List<Dictionary<string, object?>>)current["source_gaps"]!).TakeLast((int)Math.Min(events, 128)).ToList();
            session.Emit("ads_source_gap", w =>
            {
                w.WriteNumber("missing_samples", missing);
                w.WritePropertyName("gaps"); JsonOut.Write(w, gaps);
                w.WriteString("policy", "notifications missing upstream of the callback; recording continues and the gap is documented");
            });
        }
        reported = current;
        session.Mode = "ADS notifiche cicliche · timestamp PLC";
        session.SetPeriod(current["source_period_ms"] as double?);
    }

    public async Task RunAsync(SourceSession session, CancellationToken ct)
    {
        (string[] ids, int period, Generation? gen)? current = null;
        double heartbeat = 0, configured = SourceSession.Seconds(), reportedAt = 0, watched = 0;
        byte[]? version = null;
        Task<byte[]>? monitor = null;
        try
        {
            while (!ct.IsCancellationRequested)
            {
                var selection = session.Selection();
                var wanted = (selection.Ids.ToArray(), selection.Period, selection.Generation);
                if (current is not { } c || !c.ids.SequenceEqual(wanted.Item1) || c.period != wanted.Period || c.gen != wanted.Generation)
                {
                    if (monitor != null) { lock (gate) accepting = false; try { await monitor; } catch (Exception) { } monitor = null; }
                    Drain(session, sampleMs);
                    await Subscribe(session, wanted.Item1, wanted.Period, wanted.Generation, ct);
                    current = wanted; configured = SourceSession.Seconds();
                }
                string? fault;
                lock (gate) fault = callbackFault;
                if (fault != null) throw new SourceException(fault);
                // Drain what is already copied in one time slice.
                var sliceEnd = SourceSession.Seconds() + .02;
                while (true)
                {
                    Drain(session, sampleMs);
                    bool pending;
                    lock (gate) { pending = queue.Count > 0; fault = callbackFault; }
                    if (!pending || fault != null || SourceSession.Seconds() >= sliceEnd) break;
                }
                var now = SourceSession.Seconds();
                if (monitor is { IsCompleted: true })
                {
                    var finished = monitor; monitor = null; heartbeat = now;
                    try
                    {
                        var newVersion = await finished;
                        controlError = null; controlLastOk = Clock.UtcNs() / 1e6;
                        if (version != null && !newVersion.AsSpan().SequenceEqual(version))
                        {
                            await RemoveSubscriptions(session, ct);
                            Client.Symbols.Clear();
                            current = null;
                            await Client.CheckSymbols(ct);
                        }
                        version = newVersion;
                        Sync();
                    }
                    catch (AdsException exc) when (exc.Code is 0x745 or 0x719)
                    {
                        // A control timeout is not proof of notification loss: the per-signal watchdog decides.
                        controlTimeouts++; controlError = exc.Message;
                    }
                }
                if (monitor == null && current != null && now - heartbeat >= (controlError != null ? 5 : 1)) monitor = Monitor(ct);
                if (current is { } active && active.ids.Length > 0 && now - watched >= .1)
                {
                    watched = now;
                    var state = Client.State;
                    if (state != null && state != 5)
                    {
                        // PLC not in RUN: silence is expected; restart the watchdog baseline until RUN returns.
                        var nowMono = Clock.MonoNs();
                        lock (gate) foreach (var n in active.ids) lastReceived[watchKey.GetValueOrDefault(n, n)] = nowMono;
                        configured = SourceSession.Seconds(); pausedNotRun = true;
                    }
                    else
                    {
                        pausedNotRun = false;
                        var limit = Math.Max(5, active.period / 1000.0 * 5);
                        var nowMono = Clock.MonoNs() / 1e9;
                        bool expired;
                        lock (gate) expired = active.ids.Any(n => nowMono - (lastReceived.TryGetValue(watchKey.GetValueOrDefault(n, n), out var t) ? t / 1e9 : configured) > limit);
                        if (expired) throw new SourceException("Watchdog ADS: flusso notifiche assente per almeno un segnale");
                    }
                }
                if (now - reportedAt >= .25) { Report(session); reportedAt = now; }
                bool more;
                lock (gate) more = queue.Count > 0;
                if (more) await Task.Yield(); else await Task.Delay(1, ct);
            }
        }
        finally
        {
            lock (gate) accepting = false;
            if (monitor != null) { try { await monitor; } catch (Exception) { } }
            try
            {
                if (Client.Transport.IsOpen) await RemoveSubscriptions(session, CancellationToken.None);
            }
            finally { Report(session); }
        }
    }

    async Task<byte[]> Monitor(CancellationToken ct)
    {
        // The consumer never awaits control-request latency while notifications keep arriving.
        var start = SourceSession.Seconds();
        try
        {
            await Client.ReadState(ct);
            return await Client.Read(0xF008, 0, 4, ct);
        }
        finally { controlMs = (SourceSession.Seconds() - start) * 1000; }
    }
}
