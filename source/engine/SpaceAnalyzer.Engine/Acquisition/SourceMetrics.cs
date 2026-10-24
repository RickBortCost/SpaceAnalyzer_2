using System.Diagnostics;
using System.Globalization;
using System.Text.RegularExpressions;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Acquisition;

/// <summary>Per-connection counters (acquisition_health.SourceMetrics).</summary>
public sealed class SourceMetrics(string connectionId, string protocol)
{
    public readonly object Lock = new();
    readonly long start = Stopwatch.GetTimestamp();
    public string ConnectionId { get; } = connectionId;
    public string Protocol { get; } = protocol;
    public long RecordingRejected, BackpressureWaits, ReceiveClockBackwards, ReceiveReordered;
    public double BackpressureMs;
    public (long utc, long mono)? LastReceive;
    public long Received, Dispatched, Lost, RejectedAfterFailure, DisplaySkipped;
    public long ClockBackwards, Duplicates, SequenceGaps;
    public readonly Dictionary<string, long> LastTime = new();
    public readonly Dictionary<string, (string? epoch, long sequence)> LastSourceSequence = new();
    public readonly Dictionary<string, long> Sequences = new();
    public readonly Queue<double> QueueLatency = new();
    public string Error = "";
    readonly RateWindow rates = new();

    public void AddLatency(double ms)
    {
        QueueLatency.Enqueue(ms);
        while (QueueLatency.Count > 4096) QueueLatency.Dequeue();
    }

    public void Observe() => rates.Observe(("received", Received), ("dispatched", Dispatched), ("lost", Lost),
        ("recording_rejected", RecordingRejected), ("display_skipped", DisplaySkipped));

    static Dictionary<string, object?> Quantiles(IEnumerable<double> values)
    {
        var ordered = values.OrderBy(v => v).ToList();
        object? At(double p) => ordered.Count == 0 ? null : ordered[Math.Min(ordered.Count - 1, (int)(p * (ordered.Count - 1)))];
        return new() { ["p95"] = At(.95), ["p99"] = At(.99) };
    }

    public Dictionary<string, object?> Snapshot(Dictionary<string, object?> buffer)
    {
        lock (Lock)
        {
            Observe();
            var seconds = Math.Max(.001, Stopwatch.GetElapsedTime(start).TotalSeconds);
            return new()
            {
                ["connection_id"] = ConnectionId, ["protocol"] = Protocol, ["received"] = Received, ["dispatched"] = Dispatched,
                ["lost"] = Lost, ["receive_clock_backwards"] = ReceiveClockBackwards, ["receive_reordered"] = ReceiveReordered,
                ["backpressure_waits"] = BackpressureWaits, ["backpressure_ms"] = BackpressureMs,
                ["recording_rejected_samples"] = RecordingRejected, ["rejected_after_failure"] = RejectedAfterFailure,
                ["display_skipped"] = DisplaySkipped, ["received_per_second"] = Received / seconds,
                ["clock_backwards"] = ClockBackwards, ["duplicates"] = Duplicates, ["sequence_gaps"] = SequenceGaps,
                ["backlog"] = buffer["samples"], ["backlog_bytes"] = buffer["bytes"], ["high_water"] = buffer["high_water"],
                ["error"] = Error, ["queue_latency_ms"] = Quantiles(QueueLatency),
                ["recent"] = (Action<System.Text.Json.Utf8JsonWriter>)rates.Write,
                ["rate_semantics"] = "received_per_second: lifetime average; recent: sliding window",
                ["buffer"] = buffer,
            };
        }
    }

    static readonly Regex Fraction = new(@"\.(\d+)", RegexOptions.Compiled);

    /// <summary>acquisition_health.source_clock_ns: FILETIME first, then an ISO timestamp with time zone.</summary>
    public static long? SourceClockNs(Sample row)
    {
        if (row.SourceFiletime != null && long.TryParse(row.SourceFiletime, NumberStyles.Integer, CultureInfo.InvariantCulture, out var ft))
            return (ft - 116444736000000000L) * 100;
        var value = row.SourceTs;
        if (string.IsNullOrEmpty(value)) return null;
        try
        {
            var text = value.Replace("Z", "+00:00");
            // Python fromisoformat requires an explicit offset to compare clocks.
            if (!Regex.IsMatch(text, @"[+-]\d{2}:\d{2}$")) return null;
            var match = Fraction.Match(text);
            var whole = match.Success ? text.Remove(match.Index, match.Length) : text;
            var dt = DateTimeOffset.ParseExact(whole, "yyyy-MM-dd'T'HH:mm:sszzz", CultureInfo.InvariantCulture);
            var ns = match.Success ? long.Parse((match.Groups[1].Value + "000000000")[..9], CultureInfo.InvariantCulture) : 0;
            return dt.ToUnixTimeSeconds() * 1_000_000_000L + ns;
        }
        catch (FormatException) { return null; }
    }
}
