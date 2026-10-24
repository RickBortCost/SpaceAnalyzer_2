using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Ipc;

namespace SpaceAnalyzer.Engine.Acquisition;

/// <summary>
/// Calculations and display in the UI process receive a copy of each dispatched batch.
/// Originals are already with the recorder: if the UI falls behind, only its copy is
/// skipped, the skip is counted and reported (never silent).
/// </summary>
public sealed class PythonChannel
{
    readonly Host host;
    readonly EventSink events;
    readonly Queue<(Batch batch, int count, long size)> items = new();
    readonly object gate = new();
    readonly AutoResetEvent wake = new(false);
    readonly int capacity;
    readonly long maxBytes;
    long samples, bytes, written, enqueued, skipped;
    bool reportedSkip;
    Thread? thread;
    volatile bool closed;
    string error = "";

    public PythonChannel(Host host, EventSink events, int capacity = 262144, long maxBytes = 64L * 1024 * 1024)
    {
        this.host = host; this.events = events; this.capacity = capacity; this.maxBytes = maxBytes;
    }

    public bool Put(double t, List<Sample> rows, int period, Generation? generation)
    {
        // OPC UA notifications stay in the primary archive; the UI uses the periodic snapshots.
        var display = rows.Where(r => r.AcquisitionOrigin != "notification").ToList();
        if (display.Count == 0) return true;
        var size = ConnectionBuffer.SizeHint(display);
        lock (gate)
        {
            if (closed) return false;
            if (samples + display.Count > capacity || bytes + size > maxBytes)
            {
                skipped += display.Count;
                if (!reportedSkip)
                {
                    reportedSkip = true;
                    var count = display.Count;
                    ThreadPool.QueueUserWorkItem(_ => events("display_delivery_skipped", null, w =>
                    {
                        w.WriteNumber("samples", count);
                        w.WriteString("policy", "originals already with the recorder; calculated channels and Live view incomplete");
                    }));
                }
                return false;
            }
            items.Enqueue((new Batch(t, display, period, generation), display.Count, size));
            samples += display.Count; bytes += size; enqueued++;
            if (thread == null) { thread = new Thread(Run) { Name = "python-rows", IsBackground = true }; thread.Start(); }
        }
        wake.Set();
        return true;
    }

    void Run()
    {
        while (!closed)
        {
            (Batch batch, int count, long size) item;
            lock (gate)
            {
                if (items.Count == 0) item = default;
                else { item = items.Dequeue(); samples -= item.count; bytes -= item.size; }
            }
            if (item.batch == null) { wake.WaitOne(20); continue; }
            try
            {
                var b = item.batch;
                host.Send(Host.Rows, w =>
                {
                    w.WriteStartObject();
                    w.WritePropertyName("t"); Values.WriteDouble(w, b.T);
                    w.WriteNumber("period", b.Period);
                    if (b.Generation is Generation g) { w.WriteStartArray("generation"); w.WriteNumberValue(g.Revision); w.WriteNumberValue(g.Epoch); w.WriteEndArray(); }
                    else w.WriteNull("generation");
                    w.WriteStartArray("rows");
                    foreach (var row in b.Rows) row.WriteDisplay(w);
                    w.WriteEndArray();
                    w.WriteEndObject();
                });
            }
            catch (Exception exc) { lock (gate) error = exc.Message; }
            lock (gate) written++;
        }
    }

    /// <summary>Every batch queued so far has been written to the UI pipe.</summary>
    public bool Flush(TimeSpan timeout)
    {
        long target;
        lock (gate) target = enqueued;
        var deadline = DateTime.UtcNow + timeout;
        while (true)
        {
            lock (gate) if (written >= target) return true;
            if (DateTime.UtcNow >= deadline) return false;
            wake.Set(); Thread.Sleep(1);
        }
    }

    public Dictionary<string, object?> Status()
    {
        lock (gate)
            return new()
            {
                ["backlog_samples"] = samples, ["backlog_bytes"] = bytes, ["skipped_samples"] = skipped, ["error"] = error,
                ["policy"] = "skip UI copy only; originals already submitted to recorder",
            };
    }

    public void ResetSkip() { lock (gate) reportedSkip = false; }

    public void Close() { closed = true; wake.Set(); }
}
