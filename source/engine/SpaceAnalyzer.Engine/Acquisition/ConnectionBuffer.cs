using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Acquisition;

/// <summary>The Python generation tuple (selection_revision, buffer_epoch).</summary>
public readonly record struct Generation(long Revision, long Epoch);

/// <summary>One driver delivery: shared timestamp, rows, recorder period hint and selection generation.</summary>
public sealed record Batch(double T, List<Sample> Rows, int Period, Generation? Generation);

/// <summary>Bounded per-connection handoff; overflow is terminal and attributable (acquisition_buffers.py).</summary>
public sealed class ConnectionBuffer(int maxSamples = 32768, long maxBytes = 32L * 1024 * 1024, int maxBatches = 4096)
{
    readonly Queue<(long serial, Batch item, int count, long size, long queued)> items = new();
    readonly object gate = new();
    long samples, bytes, accepted, completed, lost, highWater;
    bool failed;

    public int MaxSamples => maxSamples;
    public long MaxBytes => maxBytes;
    public bool Failed { get { lock (gate) return failed; } }

    public bool HasRoom(int count, long size)
    {
        lock (gate) return !failed && samples + count <= maxSamples && bytes + size <= maxBytes && items.Count < maxBatches;
    }

    public bool Put(Batch item, int count, long size)
    {
        lock (gate)
        {
            if (failed) { lost += count; return false; }
            if (samples + count > maxSamples || bytes + size > maxBytes || items.Count >= maxBatches)
            {
                failed = true; lost += count; return false;
            }
            accepted++;
            items.Enqueue((accepted, item, count, size, Clock.MonoNs()));
            samples += count; bytes += size; highWater = Math.Max(highWater, samples);
            return true;
        }
    }

    public (long serial, Batch item, int count, long queued)? Get()
    {
        lock (gate)
        {
            if (items.Count == 0) return null;
            var (serial, item, count, size, queued) = items.Dequeue();
            samples -= count; bytes -= size;
            return (serial, item, count, queued);
        }
    }

    public void Done(long serial) { lock (gate) completed = Math.Max(completed, serial); }

    public (long accepted, long completed) Progress() { lock (gate) return (accepted, completed); }

    public Dictionary<string, object?> Snapshot()
    {
        lock (gate)
            return new()
            {
                ["samples"] = samples, ["bytes"] = bytes, ["capacity_samples"] = (long)maxSamples, ["capacity_bytes"] = maxBytes,
                ["capacity_batches"] = (long)maxBatches, ["accepted_batches"] = accepted, ["completed_batches"] = completed,
                ["lost_samples"] = lost, ["failed"] = failed, ["high_water"] = highWater,
            };
    }

    /// <summary>Conservative byte budget as acquisition_buffers.size_hint (+256 per row as in submit).</summary>
    public static long SizeHint(IReadOnlyList<Sample> rows)
    {
        long size = 128;
        foreach (var r in rows)
        {
            size += 512 + 256;
            size += 4L * ((r.NodeId?.Length ?? 0) + (r.Status?.Length ?? 0) + (r.DataType?.Length ?? 0) + (r.SourceTs?.Length ?? 0)
                + (r.ServerTs?.Length ?? 0) + (r.LocalTs?.Length ?? 0) + (r.Error?.Length ?? 0) + (r.SourceEpoch?.Length ?? 0));
            if (r.Value is string s) size += 4L * s.Length;
            if (r.SourceValue is string t) size += 4L * t.Length;
            else if (r.SourceValue is byte[] b) size += b.Length;
            if (r.SourceBinary != null) size += r.SourceBinary.Length;
        }
        return size;
    }
}
