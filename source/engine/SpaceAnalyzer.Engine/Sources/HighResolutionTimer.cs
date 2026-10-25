using System.Runtime.InteropServices;

namespace SpaceAnalyzer.Engine.Sources;

/// <summary>1 ms system timer on Windows while a polling session runs (default ~15.6 ms).</summary>
public sealed class HighResolutionTimer : IDisposable
{
    [DllImport("winmm.dll")] static extern uint timeBeginPeriod(uint period);
    [DllImport("winmm.dll")] static extern uint timeEndPeriod(uint period);

    bool active;

    public static HighResolutionTimer Begin()
    {
        var timer = new HighResolutionTimer();
        if (OperatingSystem.IsWindows())
        {
            try { timer.active = timeBeginPeriod(1) == 0; } catch (Exception) { }
        }
        return timer;
    }

    public void Dispose()
    {
        if (active) { try { timeEndPeriod(1); } catch (Exception) { } active = false; }
    }

    /// <summary>Wait until a monotonic deadline in seconds (SourceSession.Seconds).</summary>
    public static Task DelayUntil(double deadline, CancellationToken ct)
    {
        var remaining = (deadline - SourceSession.Seconds()) * 1000;
        return remaining >= .5 ? Task.Delay(TimeSpan.FromMilliseconds(remaining), ct) : Task.Yield().AsTask(ct);
    }
}

static class YieldExtensions
{
    public static async Task AsTask(this System.Runtime.CompilerServices.YieldAwaitable awaitable, CancellationToken ct)
    {
        ct.ThrowIfCancellationRequested();
        await awaitable;
    }
}
