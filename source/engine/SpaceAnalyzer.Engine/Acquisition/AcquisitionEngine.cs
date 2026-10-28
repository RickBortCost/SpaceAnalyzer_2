using System.Text.Json;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Acquisition;

public delegate void EventSink(string kind, string? connectionId, Action<Utf8JsonWriter> details);

/// <summary>
/// Recorder-first handoff (port of acquisition_engine.AcquisitionEngine): one bounded
/// queue per connection, fair draining, originals to the recorder before calculations
/// and display. Overflow of originals stops the affected recording and is attributable.
/// </summary>
public sealed class AcquisitionEngine
{
    /// <summary>Returns true/false when a recording is armed, null when nothing records.</summary>
    public Func<double, List<Sample>, int, Generation?, bool?> Record { get; set; } = (_, _, _, _) => null;
    /// <summary>Calculations and display (the Python UI); false when that consumer skipped the batch.</summary>
    public Func<double, List<Sample>, int, Generation?, bool> Forward { get; set; } = (_, _, _, _) => true;
    public Action<string> Fail { get; set; } = _ => { };

    readonly EventSink events;
    readonly int maxSamples;
    readonly long maxBytes;
    readonly object gate = new();
    readonly Dictionary<string, (ConnectionBuffer buffer, SourceMetrics metrics)> sources = new();
    readonly AutoResetEvent wake = new(false);
    Thread? thread;
    volatile bool closed;
    public double? LastTimestamp { get; private set; }

    public AcquisitionEngine(EventSink events, int? maxSamples = null, long? maxBytes = null)
    {
        this.events = events;
        this.maxSamples = maxSamples ?? int.Parse(Environment.GetEnvironmentVariable("SPACE_ACQ_QUEUE_SAMPLES") ?? "32768");
        this.maxBytes = maxBytes ?? long.Parse(Environment.GetEnvironmentVariable("SPACE_ACQ_QUEUE_MB") ?? "32") * 1024 * 1024;
        if (this.maxSamples is < 1 or > 1000000 || this.maxBytes is < 1024 or > 256L * 1024 * 1024)
            throw new ArgumentException("Limiti buffer acquisizione non validi");
    }

    public (ConnectionBuffer buffer, SourceMetrics metrics) Connection(string key, string protocol)
    {
        lock (gate)
        {
            if (!sources.TryGetValue(key, out var entry))
            {
                var identity = Guid.NewGuid().ToString("N");
                entry = (new ConnectionBuffer(maxSamples, maxBytes), new SourceMetrics(identity, protocol));
                sources[key] = entry;
                events("connection_registered", identity, w => { w.WriteString("source", key); w.WriteString("protocol", protocol); });
            }
            return entry;
        }
    }

    public string ConnectionId(string protocol) => Connection(protocol, protocol).metrics.ConnectionId;

    public bool Submit(string key, string protocol, double t, List<Sample> rows, int period, Generation? generation, double backpressureSeconds = 0)
    {
        if (rows.Count == 0 || closed) return false;
        var (buffer, metrics) = Connection(key, protocol);
        var now = Clock.UtcNs();
        var mono = Clock.MonoNs();
        foreach (var row in rows)
        {
            if (row.ReceivedNs == 0) row.ReceivedNs = row.ReceivedMs is double ms ? (long)(ms * 1_000_000) : now;
            if (row.ReceivedMonoNs == 0) row.ReceivedMonoNs = mono;
        }
        var count = rows.Count;
        var size = ConnectionBuffer.SizeHint(rows);
        var started = Clock.MonoNs();
        var deadline = started + (long)(backpressureSeconds * 1e9);
        while (backpressureSeconds > 0 && count <= buffer.MaxSamples && size <= buffer.MaxBytes && !buffer.Failed
               && !buffer.HasRoom(count, size) && Clock.MonoNs() < deadline)
        {
            wake.Set(); Thread.Sleep(1);
        }
        var waited = (Clock.MonoNs() - started) / 1e9;
        bool accepted;
        lock (metrics.Lock)
        {
            if (waited >= .001) { metrics.BackpressureWaits++; metrics.BackpressureMs += waited * 1000; }
            metrics.Received += count;
            accepted = buffer.Put(new Batch(t, rows, period, generation), count, size);
            if (!accepted)
            {
                metrics.Lost += count;
                if (metrics.Error.Length > 0) metrics.RejectedAfterFailure += count;
                else
                {
                    metrics.Error = "Overflow buffer acquisizione: sessione arrestata; " + count + " campioni rifiutati";
                    var snapshot = buffer.Snapshot();
                    events("acquisition_overflow", metrics.ConnectionId, w =>
                    {
                        w.WriteString("source", key); w.WriteNumber("lost_samples", count);
                        w.WritePropertyName("buffer"); JsonOut.Write(w, snapshot);
                    });
                    Fail(metrics.Error);
                }
            }
        }
        if (accepted)
        {
            lock (gate)
            {
                if (thread == null) { thread = new Thread(Run) { Name = "acquisition-engine", IsBackground = true, Priority = ThreadPriority.AboveNormal }; thread.Start(); }
            }
            wake.Set();
        }
        return accepted;
    }

    void Run()
    {
        while (!closed)
        {
            var work = false;
            List<KeyValuePair<string, (ConnectionBuffer buffer, SourceMetrics metrics)>> current;
            lock (gate) current = sources.ToList();
            foreach (var (key, (buffer, m)) in current)
            {
                var entry = buffer.Get();
                if (entry is not var (serial, batch, count, queued)) continue;
                work = true;
                try
                {
                    lock (m.Lock) Annotate(m, batch.Rows, queued);
                    // Neither consumer may wait for the UI.
                    var accepted = Record(batch.T, batch.Rows, batch.Period, batch.Generation);
                    if (accepted == false) lock (m.Lock) m.RecordingRejected += count;
                    LastTimestamp = LastTimestamp is double last ? Math.Max(last, batch.T) : batch.T;
                    var forwarded = Forward(batch.T, batch.Rows, batch.Period, batch.Generation);
                    lock (m.Lock)
                    {
                        m.Dispatched += count;
                        if (!forwarded) m.DisplaySkipped += count;
                        m.Observe();
                    }
                }
                catch (Exception exc)
                {
                    lock (m.Lock) { m.Lost += count; m.Error = exc.Message; }
                    events("acquisition_dispatch_error", m.ConnectionId, w => { w.WriteNumber("samples", count); w.WriteString("error", exc.Message); });
                    Fail(exc.Message);
                }
                finally { buffer.Done(serial); }
            }
            if (!work) wake.WaitOne(10);
        }
    }

    void Annotate(SourceMetrics m, List<Sample> rows, long queued)
    {
        // Rows of one notification block share their clocks: an identical pair is checked once.
        (long, long)? checkedKey = null;
        var clocks = new Dictionary<(string?, string?), long?>();
        foreach (var row in rows)
        {
            var receive = (row.ReceivedNs, row.ReceivedMonoNs);
            if (receive != checkedKey)
            {
                checkedKey = receive;
                var previous = m.LastReceive;
                if (previous is var (prevUtc, prevMono))
                {
                    if (receive.ReceivedMonoNs < prevMono) m.ReceiveReordered++;
                    else if (receive.ReceivedNs < prevUtc)
                    {
                        m.ReceiveClockBackwards++; row.ClockDiscontinuity = true; checkedKey = null;
                        var p = prevUtc; var r = receive.ReceivedNs;
                        events("receive_clock_backwards", m.ConnectionId, w =>
                        {
                            w.WriteString("previous_ns", p.ToString(System.Globalization.CultureInfo.InvariantCulture));
                            w.WriteString("received_ns", r.ToString(System.Globalization.CultureInfo.InvariantCulture));
                            w.WriteString("policy", "recording interval uses monotonic clock; original UTC preserved");
                        });
                    }
                }
                if (previous == null || receive.ReceivedMonoNs >= previous.Value.mono) m.LastReceive = (receive.ReceivedNs, receive.ReceivedMonoNs);
            }
            var nid = row.NodeId;
            var sequence = m.Sequences[nid] = m.Sequences.GetValueOrDefault(nid) + 1;
            row.ConnectionId = m.ConnectionId; row.SampleSequence = sequence; row.SourceProtocol = m.Protocol;
            var clockKey = (row.SourceFiletime, row.SourceTs);
            if (!clocks.TryGetValue(clockKey, out var sourceTime)) clocks[clockKey] = sourceTime = SourceMetrics.SourceClockNs(row);
            if (sourceTime is long st)
            {
                if (m.LastTime.TryGetValue(nid, out var prior))
                {
                    if (st < prior) { m.ClockBackwards++; row.ClockDiscontinuity = true; }
                    if (st == prior) m.Duplicates++;
                }
                m.LastTime[nid] = st;
            }
            if (row.SourceSequence is long seq)
            {
                if (m.LastSourceSequence.TryGetValue(nid, out var prev) && prev.epoch == row.SourceEpoch && seq > prev.sequence + 1)
                {
                    var missing = seq - prev.sequence - 1; m.SequenceGaps += missing;
                    var first = prev.sequence + 1; var lastMissing = seq - 1;
                    events("source_sequence_gap", m.ConnectionId, w => { w.WriteString("nodeid", nid); w.WriteNumber("first", first); w.WriteNumber("last", lastMissing); });
                    Fail("Sequenza sorgente incompleta: " + nid);
                }
                m.LastSourceSequence[nid] = (row.SourceEpoch, seq);
            }
        }
        m.AddLatency((Clock.MonoNs() - queued) / 1e6);
    }

    public bool Flush(TimeSpan timeout)
    {
        List<(ConnectionBuffer buffer, long target)> targets;
        lock (gate) targets = sources.Values.Select(s => (s.buffer, s.buffer.Progress().accepted)).ToList();
        var deadline = DateTime.UtcNow + timeout;
        while (targets.Any(t => t.buffer.Progress().completed < t.target))
        {
            if (DateTime.UtcNow >= deadline) return false;
            wake.Set(); Thread.Sleep(1);
        }
        return true;
    }

    public Dictionary<string, object?> Status()
    {
        List<KeyValuePair<string, (ConnectionBuffer buffer, SourceMetrics metrics)>> current;
        lock (gate) current = sources.ToList();
        var connections = new Dictionary<string, object?>();
        foreach (var (key, (buffer, metrics)) in current) connections[key] = metrics.Snapshot(buffer.Snapshot());
        return new()
        {
            ["connections"] = connections,
            ["policy"] = "bounded; stop recording on original overflow; recorder before calculations and display",
        };
    }

    /// <summary>A failed connection can be re-armed only after its accepted batches are drained.</summary>
    public void ResetFailed()
    {
        if (!Flush(TimeSpan.FromSeconds(5))) throw new InvalidOperationException("Buffer non drenati: impossibile riarmare acquisizione");
        lock (gate)
        {
            foreach (var (key, (buffer, metrics)) in sources.ToList())
            {
                if (!buffer.Failed) continue;
                var lost = metrics.Lost;
                events("connection_rearmed", metrics.ConnectionId, w => { w.WriteString("source", key); w.WriteNumber("lost_samples", lost); });
                sources.Remove(key);
            }
        }
    }

    public Dictionary<string, string> Catalog()
    {
        lock (gate) return sources.Values.GroupBy(s => s.metrics.Protocol).ToDictionary(g => g.Key, g => g.Last().metrics.ConnectionId);
    }

    public void Close()
    {
        Flush(TimeSpan.FromSeconds(5));
        closed = true; wake.Set();
        thread?.Join(5000);
    }
}
