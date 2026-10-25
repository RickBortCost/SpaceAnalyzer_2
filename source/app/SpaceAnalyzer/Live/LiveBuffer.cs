using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Live;

/// <summary>
/// One Live point: [t, plot_value, quality, status_code, source_ts, server_ts, str(value), data_type,
/// status, period, local_ts, source_filetime] as in the 2.x runtime.samples lists.
/// </summary>
public sealed record LivePoint(double T, double? Value, string? Quality, long? StatusCode = null, string? SourceTs = null, string? ServerTs = null,
    string? Text = null, string? DataType = null, string? Status = null, double? Period = null, string? LocalTs = null, string? Filetime = null)
{
    /// <summary>Position in the session stream of its signal (increasing; the Live archive sequence when archived).</summary>
    public long Seq { get; init; }

    public void Write(Utf8JsonWriter w, int fields = 12)
    {
        w.WriteStartArray();
        Values.WriteDouble(w, T);
        if (fields > 1) { if (Value is double v) Values.WriteDouble(w, v); else w.WriteNullValue(); }
        if (fields > 2) Str(w, Quality);
        if (fields > 3) { if (StatusCode is long c) w.WriteNumberValue(c); else w.WriteNullValue(); }
        if (fields > 4) Str(w, SourceTs);
        if (fields > 5) Str(w, ServerTs);
        if (fields > 6) Str(w, Text);
        if (fields > 7) Str(w, DataType);
        if (fields > 8) Str(w, Status);
        if (fields > 9) { if (Period is double p) Values.WriteDouble(w, p); else w.WriteNullValue(); }
        if (fields > 10) Str(w, LocalTs);
        if (fields > 11) Str(w, Filetime);
        w.WriteEndArray();
    }

    static void Str(Utf8JsonWriter w, string? s) { if (s == null) w.WriteNullValue(); else w.WriteStringValue(s); }

    public JsonArray ToJson() => new(
        T, Value, Quality, StatusCode, SourceTs, ServerTs, Text, DataType, Status, Period, LocalTs, Filetime);

    public static LivePoint FromJson(JsonArray a)
    {
        JsonNode? At(int i) => i < a.Count ? a[i] : null;
        string? S(int i) => At(i) is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;
        double? D(int i) => At(i) is JsonValue v && v.TryGetValue<double>(out var d) ? d : null;
        long? L(int i) => At(i) is JsonValue v && v.TryGetValue<long>(out var l) ? l : null;
        return new LivePoint(D(0) ?? 0, D(1), S(2), L(3), S(4), S(5), S(6), S(7), S(8), D(9), S(10), S(11));
    }
}

/// <summary>
/// Bounded full-session display history (live_overview.Overview): first/last samples, extrema
/// and quality/gap representatives survive compaction. Display only, never analysis input.
/// </summary>
public sealed class Overview
{
    readonly int limit;
    int width = 1, activeCount;
    List<List<LivePoint>> buckets = new();
    Bucket active = new();
    LivePoint? pending;
    public long Count { get; private set; }

    public Overview(int bucketLimit = 256)
    {
        if (bucketLimit < 2 || bucketLimit % 2 == 1) throw new ArgumentException("Even bucket limit required");
        limit = bucketLimit;
    }

    sealed class Bucket
    {
        LivePoint? first, last, low, high, gap, bad, uncertain;

        public void Append(LivePoint p)
        {
            first ??= p;
            last = p;
            if (p.Value is double v && double.IsFinite(v))
            {
                if (low == null || v < low.Value) low = p;
                if (high == null || v > high.Value) high = p;
            }
            else gap ??= p;
            if (p.Quality == "Bad") bad ??= p;
            if (p.Quality == "Uncertain") uncertain ??= p;
        }

        public List<LivePoint> Points() => Unique(new[] { first, low, high, gap, bad, uncertain, last }.Where(p => p != null)!);
    }

    /// <summary>{p[0]: p} then sorted by time: the last point of each timestamp wins.</summary>
    static List<LivePoint> Unique(IEnumerable<LivePoint?> points)
    {
        var map = new Dictionary<double, LivePoint>();
        foreach (var p in points) if (p != null) map[p.T] = p;
        return map.Values.OrderBy(p => p.T).ToList();
    }

    static List<LivePoint> Envelope(IEnumerable<LivePoint> points)
    {
        var ordered = Unique(points);
        if (ordered.Count == 0) return ordered;
        var chosen = new SortedSet<int> { 0, ordered.Count - 1 };
        var numeric = Enumerable.Range(0, ordered.Count).Where(i => ordered[i].Value is double v && double.IsFinite(v)).ToList();
        if (numeric.Count > 0)
        {
            chosen.Add(numeric.MinBy(i => ordered[i].Value!.Value)!);
            chosen.Add(numeric.MaxBy(i => ordered[i].Value!.Value)!);
        }
        foreach (var quality in new[] { "Bad", "Uncertain" })
        {
            var i = ordered.FindIndex(p => p.Quality == quality);
            if (i >= 0) chosen.Add(i);
        }
        var gap = ordered.FindIndex(p => p.Value == null);
        if (gap >= 0) chosen.Add(gap);
        return chosen.Select(i => ordered[i]).ToList();
    }

    public void Append(LivePoint point)
    {
        var p = point with { };
        if (p.Quality == null) p = p with { Quality = p.Value != null ? "Good" : "Bad" };
        if (!double.IsFinite(p.T)) return;
        // Formula workers may revise their newest timestamp: aggregate it at the next timestamp.
        if (pending != null && pending.T == p.T) { pending = p; return; }
        if (pending != null) Finalize(pending);
        pending = p;
        Count++;
    }

    void Finalize(LivePoint p)
    {
        active.Append(p);
        activeCount++;
        if (activeCount >= width)
        {
            buckets.Add(active.Points());
            active = new Bucket();
            activeCount = 0;
            if (buckets.Count >= limit)
            {
                var merged = new List<List<LivePoint>>();
                for (var i = 0; i < buckets.Count; i += 2) merged.Add(Envelope(buckets[i].Concat(i + 1 < buckets.Count ? buckets[i + 1] : [])));
                buckets = merged;
                width *= 2;
            }
        }
    }

    public List<LivePoint> Points()
    {
        var points = buckets.SelectMany(b => b).Concat(active.Points()).ToList();
        if (pending != null) points.Add(pending);
        return Unique(points);
    }
}

/// <summary>The bounded original FIFO of one Live signal plus its display envelope (live_overview.LiveBuffer).</summary>
public sealed class LiveBuffer
{
    static long nextId;
    readonly LinkedList<LivePoint> items = new();
    public int MaxLength { get; }
    public Overview Overview { get; }
    public long ArchiveGeneration { get; }
    /// <summary>Identity of this FIFO: a client mirroring it starts over when it changes.</summary>
    public long Id { get; } = Interlocked.Increment(ref nextId);
    readonly Func<LivePoint, long>? onAppend;
    long lastSeq;

    public LiveBuffer(IEnumerable<LivePoint> initial, int maxLength, long archiveGeneration, Func<LivePoint, long>? onAppend, Overview? previous = null)
    {
        MaxLength = maxLength; ArchiveGeneration = archiveGeneration; this.onAppend = onAppend;
        foreach (var p in initial)
        {
            var point = p.Seq > lastSeq ? p : p with { Seq = lastSeq + 1 };
            lastSeq = point.Seq;
            items.AddLast(point);
            if (items.Count > maxLength) items.RemoveFirst();
        }
        Overview = previous ?? new Overview();
        if (previous == null) foreach (var p in items) Overview.Append(p);
    }

    public int Count => items.Count;
    public LivePoint? First => items.First?.Value;
    public LivePoint? Last => items.Last?.Value;
    public IEnumerable<LivePoint> Points => items;

    LivePoint Sequenced(LivePoint point)
    {
        lastSeq = Math.Max(lastSeq + 1, onAppend?.Invoke(point) ?? 0);
        return point with { Seq = lastSeq };
    }

    public void Append(LivePoint point)
    {
        point = Sequenced(point);
        items.AddLast(point);
        if (items.Count > MaxLength) items.RemoveFirst();
        Overview.Append(point);
    }

    /// <summary>points[-1] = point (a calculated revision of the newest timestamp); the revision gets a new sequence.</summary>
    public void ReplaceLast(LivePoint point)
    {
        if (items.Last == null) { Append(point); return; }
        point = Sequenced(point);
        items.Last.Value = point;
        Overview.Append(point);
    }

    public void RemoveFirst() { if (items.Count > 0) items.RemoveFirst(); }

    public List<LivePoint> ToList() => items.ToList();

    /// <summary>
    /// Points after the client's last sequence. dropLast: that point was replaced by a revision (resent here);
    /// gap: it left the FIFO, so points between it and the oldest one kept here are not available from this buffer.
    /// </summary>
    public List<LivePoint> After(long cursor, out bool dropLast, out bool gap)
    {
        var newer = new List<LivePoint>();
        dropLast = gap = false;
        var node = items.Last;
        while (node != null && node.Value.Seq > cursor) { newer.Add(node.Value); node = node.Previous; }
        if (cursor > 0)
        {
            if (node == null) gap = true;
            else if (node.Value.Seq < cursor) dropLast = true;
        }
        newer.Reverse();
        return newer;
    }
}
