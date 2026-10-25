using System.Diagnostics;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// Append-only primary capture. Enqueue never touches the disk: a bounded queue
/// feeds one writer thread that writes CRC-checked blocks and acknowledges
/// durability only after flush + fsync. Overflow is a terminal, counted failure.
/// </summary>
public sealed class PrimaryRecorder : IDisposable
{
    sealed record Item(byte Kind, IReadOnlyList<Sample>? Rows, double Timestamp, int Period, long QueuedTicks, int Count, long Budget, byte[]? Payload);

    readonly string directory;
    readonly int flushMs;
    readonly int maxSamples;
    readonly long maxBytes;
    readonly int maxBatches;
    readonly Action<string, Action<Utf8JsonWriter>>? emit;
    readonly object gate = new();
    readonly Queue<Item> queue = new();
    readonly SemaphoreSlim signal = new(0);
    readonly List<(double ms, int count)> latencies = new();
    readonly RateWindow rates = new();
    Dictionary<string, int> channels = new();
    readonly Dictionary<string, long> sequences = new();
    Thread? writer;
    FileStream? stream, ownership;
    string? statusPath;
    long queuedSamples, queuedBytes, received, lost, highWater, backpressureWaits;
    double backpressureMs;
    long samplesWritten, blocks, durable, bytesWritten, annotations, annotationsLost;
    string? lastFlushNs;
    string error = "";
    bool accepting, stopRequested, started;
    string integrity = "recording", state = "recording";
    readonly ManualResetEventSlim done = new(false);
    long startTicks;

    public string Identity { get; } = Guid.NewGuid().ToString("N");
    public string PathPart => System.IO.Path.Combine(directory, Identity + ".sac.part");
    public string FinalPath { get; private set; } = "";
    public bool Accepting { get { lock (gate) return accepting; } }
    public string Error { get { lock (gate) return error; } }
    public bool Done => done.IsSet;
    public IReadOnlyDictionary<string, int> Channels => channels;
    public Action<string>? FailureCallback { get; set; }

    public PrimaryRecorder(string directory, int flushMs = 250, int maxSamples = 65536, long maxBytes = 32L * 1024 * 1024,
        int maxBatches = 4096, Action<string, Action<Utf8JsonWriter>>? emit = null)
    {
        if (flushMs is < 10 or > 10000 || maxSamples is < 1 or > 1_000_000 || maxBytes < 1024 || maxBytes > 256L * 1024 * 1024)
            throw new ArgumentException("Limiti recorder primario non validi");
        this.directory = directory; this.flushMs = flushMs; this.maxSamples = maxSamples; this.maxBytes = maxBytes;
        this.maxBatches = maxBatches; this.emit = emit;
    }

    /// <summary>Opens the capture; <paramref name="metadata"/> writes extra header fields.</summary>
    public void Start(IReadOnlyList<NodeSpec> nodes, Action<Utf8JsonWriter> metadata, IReadOnlyDictionary<string, string> connections)
    {
        if (started) throw new InvalidOperationException("Un recorder primario gestisce una sola sessione");
        if (nodes.Count is < 1 or > 10000 || nodes.Select(n => n.NodeId).Distinct().Count() != nodes.Count)
            throw new ArgumentException("Canali primari non validi");
        started = true;
        Directory.CreateDirectory(directory);
        channels = nodes.Select((n, i) => (n.NodeId, i)).ToDictionary(x => x.NodeId, x => x.i);
        var path = PathPart;
        statusPath = path + ".status.json";
        ownership = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
        stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read, 1 << 20);
        var startedNs = Clock.UtcNs();
        var header = SacFormat.Json(w =>
        {
            w.WriteStartObject();
            metadata(w);
            w.WriteString("session_id", Identity);
            w.WriteString("started_ns", startedNs.ToString(System.Globalization.CultureInfo.InvariantCulture));
            w.WriteStartArray("channels");
            foreach (var node in nodes) node.Write(w);
            w.WriteEndArray();
            w.WriteStartObject("connections");
            foreach (var (protocol, id) in connections) w.WriteString(protocol, id);
            w.WriteEndObject();
            w.WriteString("timestamp_policy", "Original source clocks preserved; received_ns is integer UTC, no resampling");
            w.WriteString("format", "Space Analyzer Primary Capture");
            w.WriteNumber("version", SacFormat.Version);
            w.WriteStartArray("fields");
            foreach (var field in SacFormat.Fields) w.WriteStringValue(field);
            w.WriteEndArray();
            w.WriteEndObject();
        });
        SacFormat.WriteHeader(stream, header);
        stream.Flush(true);
        bytesWritten = stream.Position;
        WriteStatus();
        startTicks = Stopwatch.GetTimestamp();
        accepting = true;
        writer = new Thread(Run) { Name = "primary-writer", IsBackground = true, Priority = ThreadPriority.AboveNormal };
        writer.Start();
    }

    public bool Enqueue(double timestamp, IReadOnlyList<Sample> rows, int period, double backpressureSeconds = 0)
    {
        var selected = rows.Where(r => channels.ContainsKey(r.NodeId)).ToList();
        if (selected.Count == 0) return Accepting;
        long budget = 0;
        foreach (var row in selected) budget += Estimate(row);
        var deadline = Stopwatch.GetTimestamp() + (long)(backpressureSeconds * Stopwatch.Frequency);
        var waited = false;
        var waitStart = Stopwatch.GetTimestamp();
        while (true)
        {
            lock (gate)
            {
                if (!accepting) return false;
                var room = queuedSamples + selected.Count <= maxSamples && queuedBytes + budget <= maxBytes && queue.Count < maxBatches;
                if (room || backpressureSeconds <= 0 || Stopwatch.GetTimestamp() >= deadline || selected.Count > maxSamples)
                {
                    if (waited) { backpressureWaits++; backpressureMs += Stopwatch.GetElapsedTime(waitStart).TotalMilliseconds; }
                    if (!room)
                    {
                        lost += selected.Count;
                        FailLocked("Overflow recorder primario: " + selected.Count + " campioni non accettati");
                        return false;
                    }
                    foreach (var row in selected)
                    {
                        // Per-channel progressive when the acquisition did not assign one.
                        row.SampleSequence ??= sequences[row.NodeId] = sequences.GetValueOrDefault(row.NodeId) + 1;
                    }
                    queue.Enqueue(new Item(SacFormat.Data, selected, timestamp, period, Stopwatch.GetTimestamp(), selected.Count, budget, null));
                    received += selected.Count;
                    queuedSamples += selected.Count;
                    queuedBytes += budget;
                    highWater = Math.Max(highWater, queuedSamples);
                    break;
                }
            }
            waited = true;
            Thread.Sleep(1);
        }
        signal.Release();
        return true;
    }

    /// <summary>Integrity event placed after the samples already accepted; never blocks or fails the session.</summary>
    public bool Annotate(string kind, Action<Utf8JsonWriter> details)
    {
        lock (gate)
        {
            if (!accepting) return false;
            if (queue.Count >= maxBatches) { annotationsLost++; return false; }
            var after = received;
            var payload = SacFormat.Json(w =>
            {
                w.WriteStartObject();
                w.WriteString("kind", kind);
                w.WriteString("at_ns", Clock.UtcNs().ToString(System.Globalization.CultureInfo.InvariantCulture));
                w.WriteNumber("after_sample", after);
                w.WriteStartObject("details");
                details(w);
                w.WriteEndObject();
                w.WriteEndObject();
            });
            queue.Enqueue(new Item(SacFormat.Event, null, 0, 0, Stopwatch.GetTimestamp(), 0, 0, payload));
            annotations++;
        }
        signal.Release();
        return true;
    }

    static long Estimate(Sample row)
    {
        long size = 256;
        if (row.Value is string s) size += s.Length * 3;
        if (row.SourceValue is string o) size += o.Length * 3;
        if (row.SourceBinary is byte[] b) size += b.Length * 2;
        return size;
    }

    public void Fail(string message)
    {
        lock (gate) FailLocked(message);
        signal.Release();
    }

    void FailLocked(string message)
    {
        var first = error.Length == 0;
        if (first) error = message;
        accepting = false;
        stopRequested = true;
        if (!first) return;
        var uncommitted = Math.Max(0, received - durable);
        var acceptedNow = received;
        var durableNow = durable;
        ThreadPool.QueueUserWorkItem(_ =>
        {
            emit?.Invoke("primary_failure", w =>
            {
                w.WriteString("session_id", Identity);
                w.WriteString("error", message);
                w.WriteNumber("accepted_samples", acceptedNow);
                w.WriteNumber("durable_samples", durableNow);
                w.WriteNumber("uncommitted_samples", uncommitted);
            });
            FailureCallback?.Invoke(message);
        });
    }

    public void RequestStop()
    {
        lock (gate) { accepting = false; stopRequested = true; }
        signal.Release();
    }

    public bool WaitDone(TimeSpan timeout) => done.Wait(timeout);

    /// <summary>True once every accepted sample is durable (or the session failed).</summary>
    public bool Flush(TimeSpan timeout)
    {
        long target;
        lock (gate) target = received;
        var deadline = Stopwatch.GetTimestamp() + (long)(timeout.TotalSeconds * Stopwatch.Frequency);
        while (Stopwatch.GetTimestamp() < deadline)
        {
            lock (gate)
            {
                if (durable >= target) return true;
                if (error.Length > 0) return false;
            }
            Thread.Sleep(5);
        }
        return false;
    }

    void Run()
    {
        var pending = new List<(long ticks, int count)>();
        var lastFlush = Stopwatch.GetTimestamp();
        var interval = flushMs * Stopwatch.Frequency / 1000;
        try
        {
            while (true)
            {
                var remaining = interval - (Stopwatch.GetTimestamp() - lastFlush);
                signal.Wait(TimeSpan.FromMilliseconds(Math.Max(1, remaining * 1000.0 / Stopwatch.Frequency)));
                List<Item> batch;
                bool stopping;
                lock (gate)
                {
                    batch = new List<Item>(queue.Count);
                    while (queue.Count > 0) batch.Add(queue.Dequeue());
                    stopping = stopRequested;
                }
                WriteBatch(batch, pending);
                if (pending.Count > 0 && (Stopwatch.GetTimestamp() - lastFlush >= interval || stopping))
                {
                    FlushDurable(pending);
                    lastFlush = Stopwatch.GetTimestamp();
                }
                else if (pending.Count == 0) lastFlush = Stopwatch.GetTimestamp();
                if (stopping)
                {
                    lock (gate) if (queue.Count > 0) continue;
                    Finish(pending);
                    return;
                }
            }
        }
        catch (Exception exc)
        {
            lock (gate)
            {
                if (error.Length == 0) FailLocked("Writer primario: " + exc.Message);
                integrity = "incomplete"; state = "error";
            }
            try { WriteStatus(); } catch (IOException) { }
            try { stream?.Dispose(); } catch (IOException) { }
            ownership?.Dispose();
            FinalPath = PathPart;
            done.Set();
        }
    }

    void WriteBatch(List<Item> batch, List<(long ticks, int count)> pending)
    {
        var rows = new List<(Item item, Sample row)>();
        void FlushRows()
        {
            if (rows.Count == 0) return;
            var first = (ulong)samplesWritten + 1;
            var payload = SacFormat.Json(w =>
            {
                w.WriteStartArray();
                foreach (var (item, row) in rows) WriteRow(w, item, row);
                w.WriteEndArray();
            });
            blocks++;
            SacFormat.WriteBlock(stream!, SacFormat.Data, (ulong)blocks, first, (uint)rows.Count, payload, compress: true);
            samplesWritten += rows.Count;
            rows.Clear();
        }
        foreach (var item in batch)
        {
            if (item.Kind == SacFormat.Event)
            {
                FlushRows();
                blocks++;
                SacFormat.WriteBlock(stream!, SacFormat.Event, (ulong)blocks, (ulong)samplesWritten + 1, 0, item.Payload!, compress: false);
                continue;
            }
            foreach (var row in item.Rows!)
            {
                rows.Add((item, row));
                if (rows.Count >= 4096) FlushRows();
            }
            pending.Add((item.QueuedTicks, item.Count));
            lock (gate) { queuedSamples -= item.Count; queuedBytes -= item.Budget; }
        }
        FlushRows();
    }

    void WriteRow(Utf8JsonWriter w, Item item, Sample row)
    {
        var invariant = System.Globalization.CultureInfo.InvariantCulture;
        w.WriteStartArray();
        Values.WriteDouble(w, item.Timestamp);
        w.WriteStringValue((row.ReceivedNs != 0 ? row.ReceivedNs : Clock.UtcNs()).ToString(invariant));
        w.WriteNumberValue(channels[row.NodeId]);
        WriteNullable(w, row.ConnectionId);
        if (row.SampleSequence is long ss) w.WriteNumberValue(ss); else w.WriteNullValue();
        if (row.ConnectionSequence is long cs) w.WriteNumberValue(cs); else w.WriteNullValue();
        WriteNullable(w, row.SourceEpoch);
        if (row.SourceSequence is long sq) w.WriteNumberValue(sq); else w.WriteNullValue();
        Values.WriteTyped(w, row.Value);
        WriteNullable(w, row.SourceDataType ?? row.DataType);
        w.WriteStringValue(row.Quality ?? (row.Good ? "Good" : "Bad"));
        WriteNullable(w, row.Status);
        if (row.StatusCode is long code) w.WriteNumberValue(code); else w.WriteNullValue();
        WriteNullable(w, row.SourceTs);
        WriteNullable(w, row.ServerTs);
        WriteNullable(w, row.SourceFiletime);
        WriteNullable(w, row.LocalTs);
        Values.WriteTyped(w, row.HasSourceValue ? row.SourceValue : row.Value);
        if (row.SourcePicoseconds is long sp) w.WriteNumberValue(sp); else w.WriteNullValue();
        if (row.ServerPicoseconds is long vp) w.WriteNumberValue(vp); else w.WriteNullValue();
        WriteNullable(w, row.AcquisitionOrigin);
        w.WriteNumberValue(item.Period);
        w.WriteBooleanValue(row.ClockDiscontinuity);
        if (row.AcquisitionTimestampMs is double acquired) Values.WriteDouble(w, acquired); else w.WriteNullValue();
        Values.WriteTyped(w, row.SourceBinary);
        w.WriteEndArray();
    }

    static void WriteNullable(Utf8JsonWriter w, string? value)
    {
        if (value == null) w.WriteNullValue(); else w.WriteStringValue(value);
    }

    void FlushDurable(List<(long ticks, int count)> pending)
    {
        stream!.Flush(true);
        var now = Stopwatch.GetTimestamp();
        lock (gate)
        {
            durable = samplesWritten;
            bytesWritten = stream.Position;
            lastFlushNs = Clock.UtcNs().ToString(System.Globalization.CultureInfo.InvariantCulture);
            foreach (var (ticks, count) in pending) latencies.Add(((now - ticks) * 1000.0 / Stopwatch.Frequency, count));
            if (latencies.Count > 4096) latencies.RemoveRange(0, latencies.Count - 4096);
        }
        pending.Clear();
        rates.Observe(("durable_samples", durable), ("bytes_written", bytesWritten));
        WriteStatus();
    }

    void Finish(List<(long ticks, int count)> pending)
    {
        string footerIntegrity;
        long accepted, lostNow;
        string errorNow;
        lock (gate) { footerIntegrity = error.Length > 0 ? "incomplete" : "complete"; accepted = received; lostNow = lost; errorNow = error; }
        if (accepted != samplesWritten) throw new InvalidDataException("Conteggio finale campioni non valido");
        var footer = SacFormat.Json(w =>
        {
            w.WriteStartObject();
            w.WriteString("integrity", footerIntegrity);
            w.WriteNumber("accepted_samples", accepted);
            w.WriteNumber("lost_samples", lostNow);
            w.WriteString("error", errorNow);
            w.WriteString("ended_ns", Clock.UtcNs().ToString(System.Globalization.CultureInfo.InvariantCulture));
            w.WriteEndObject();
        });
        blocks++;
        SacFormat.WriteBlock(stream!, SacFormat.Finish, (ulong)blocks, (ulong)samplesWritten + 1, 0, footer, compress: false);
        FlushDurable(pending);
        stream!.Dispose();
        var part = PathPart;
        var final = footerIntegrity == "complete" ? part[..^".part".Length] : part[..^".sac.part".Length] + ".incomplete.sac";
        File.Move(part, final);
        SacFormat.SyncDirectory(directory);
        lock (gate) { integrity = footerIntegrity; state = footerIntegrity == "complete" ? "stopped" : "error"; FinalPath = final; }
        WriteStatus();
        ownership?.Dispose();
        done.Set();
    }

    void WriteStatus()
    {
        if (statusPath == null) return;
        Dictionary<string, object?> snapshot;
        lock (gate)
        {
            snapshot = new()
            {
                ["state"] = state, ["pid"] = Environment.ProcessId, ["path"] = FinalPath.Length > 0 ? FinalPath : PathPart,
                ["samples"] = samplesWritten, ["blocks"] = blocks, ["durable_samples"] = durable, ["bytes_written"] = bytesWritten,
                ["last_flush_ns"] = lastFlushNs, ["error"] = error, ["integrity"] = integrity,
            };
        }
        SacFormat.AtomicJson(statusPath, w => JsonOut.Write(w, snapshot));
    }

    public void WriteStatus(Utf8JsonWriter w)
    {
        lock (gate)
        {
            var elapsed = startTicks == 0 ? 0 : Math.Max(.001, Stopwatch.GetElapsedTime(startTicks).TotalSeconds);
            w.WriteStartObject();
            w.WriteString("state", state);
            w.WriteNumber("pid", Environment.ProcessId);
            w.WriteString("path", FinalPath.Length > 0 ? FinalPath : PathPart);
            w.WriteNumber("samples", samplesWritten);
            w.WriteNumber("blocks", blocks);
            w.WriteNumber("durable_samples", durable);
            w.WriteNumber("bytes_written", bytesWritten);
            if (lastFlushNs == null) w.WriteNull("last_flush_ns"); else w.WriteString("last_flush_ns", lastFlushNs);
            w.WriteString("integrity", integrity);
            w.WriteString("recording_id", Identity);
            w.WriteBoolean("active", accepting);
            w.WriteBoolean("busy", started && !done.IsSet);
            w.WriteNumber("received_samples", received);
            w.WriteNumber("lost_samples", lost);
            w.WriteNumber("inflight_samples", Math.Max(0, received - durable));
            w.WriteNumber("queue_samples", queuedSamples);
            w.WriteNumber("queue_bytes", queuedBytes);
            w.WriteNumber("queue_capacity_samples", maxSamples);
            w.WriteNumber("queue_capacity_bytes", maxBytes);
            w.WriteNumber("queue_high_water", highWater);
            w.WriteNumber("backpressure_waits", backpressureWaits);
            w.WriteNumber("backpressure_ms", backpressureMs);
            w.WriteString("error", error);
            w.WriteNumber("uncommitted_at_failure", error.Length > 0 ? Math.Max(0, received - durable) : 0);
            w.WriteString("latency_measurement", "sample-weighted batch upper bound: ingress to durable fsync acknowledgement");
            w.WriteStartObject("latency_ms");
            var (p95, p99) = WeightedPercentiles(latencies);
            if (p95 is double a) w.WriteNumber("p95", a); else w.WriteNull("p95");
            if (p99 is double b) w.WriteNumber("p99", b); else w.WriteNull("p99");
            w.WriteNumber("receipt_window", latencies.Count);
            w.WriteEndObject();
            w.WriteNumber("samples_per_second", elapsed > 0 ? durable / elapsed : 0);
            w.WriteNumber("bytes_per_second", elapsed > 0 ? bytesWritten / elapsed : 0);
            w.WritePropertyName("recent");
            rates.Write(w);
            w.WriteNumber("annotations", annotations);
            w.WriteNumber("annotations_lost", annotationsLost);
            w.WriteString("writer", "C# engine thread");
            w.WriteEndObject();
        }
    }

    static (double?, double?) WeightedPercentiles(List<(double ms, int count)> values)
    {
        if (values.Count == 0) return (null, null);
        var ordered = values.OrderBy(v => v.ms).ToList();
        double total = ordered.Sum(v => v.count);
        double? Pick(double p)
        {
            double seen = 0;
            foreach (var (ms, count) in ordered)
            {
                seen += count;
                if (seen >= total * p) return ms;
            }
            return ordered[^1].ms;
        }
        return (Pick(.95), Pick(.99));
    }

    public void Dispose()
    {
        RequestStop();
        done.Wait(TimeSpan.FromSeconds(10));
        stream?.Dispose();
        ownership?.Dispose();
    }
}
