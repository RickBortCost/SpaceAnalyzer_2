using System.Diagnostics;

namespace SpaceAnalyzer.Engine.Core;

public static class Clock
{
    static readonly long UnixEpochTicks = DateTime.UnixEpoch.Ticks;

    /// <summary>UTC nanoseconds since 1970 (precise system time, 100 ns resolution).</summary>
    public static long UtcNs() => (DateTime.UtcNow.Ticks - UnixEpochTicks) * 100;

    /// <summary>Monotonic nanoseconds of this process; only for ordering and fences.</summary>
    public static long MonoNs() => (long)(Stopwatch.GetTimestamp() * (1e9 / Stopwatch.Frequency));

    public static DateTime FromUnixMs(double ms) => DateTime.UnixEpoch.AddTicks((long)Math.Round(ms * TimeSpan.TicksPerMillisecond));
}
