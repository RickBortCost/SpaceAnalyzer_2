using System.Diagnostics;
using System.Runtime;
using System.Runtime.InteropServices;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>
/// Windows scheduling aids while sources acquire: process priority (AboveNormal by default, SPACE_PROCESS_PRIORITY=normal|abovenormal|high),
/// low-latency garbage collection, MMCSS "Pro Audio" for the threads that receive samples, and a report of the active power plan.
/// Nothing runs in kernel mode and nothing changes system settings; on other platforms every call is a no-op.
/// </summary>
public static class SystemTuning
{
    static readonly object Gate = new();
    static bool acquiring;
    static ProcessPriorityClass? previousPriority;
    static GCLatencyMode? previousLatency;
    static string priority = "normal", threadBoost = "nessuno";
    static long boostedThreads;
    [ThreadStatic] static bool boosted;

    /// <summary>Raises process priority and GC latency mode while any source acquires; restores them afterwards.</summary>
    public static void SetAcquiring(bool active)
    {
        lock (Gate)
        {
            if (active == acquiring) return;
            acquiring = active;
            try
            {
                if (active)
                {
                    previousLatency = GCSettings.LatencyMode;
                    GCSettings.LatencyMode = GCLatencyMode.SustainedLowLatency;
                    if (!OperatingSystem.IsWindows()) return;
                    var target = (Environment.GetEnvironmentVariable("SPACE_PROCESS_PRIORITY") ?? "abovenormal").Trim().ToLowerInvariant() switch
                    {
                        "normal" => ProcessPriorityClass.Normal,
                        "high" => ProcessPriorityClass.High,
                        _ => ProcessPriorityClass.AboveNormal,
                    };
                    using var process = Process.GetCurrentProcess();
                    previousPriority = process.PriorityClass;
                    process.PriorityClass = target;
                    priority = target.ToString();
                }
                else
                {
                    if (previousLatency is GCLatencyMode latency) GCSettings.LatencyMode = latency;
                    if (!OperatingSystem.IsWindows() || previousPriority is not ProcessPriorityClass original) return;
                    using var process = Process.GetCurrentProcess();
                    process.PriorityClass = original;
                    priority = original.ToString();
                }
            }
            catch (Exception exc) { EngineLog.Info("Priorità di acquisizione non applicata: " + exc.Message); }
        }
    }

    /// <summary>
    /// Once per thread: registers the calling thread with MMCSS ("Pro Audio", priority high), or raises it to Highest when MMCSS is not available.
    /// Meant for threads that only copy samples (the TcAdsDll callback thread); it is never reverted.
    /// </summary>
    public static void BoostCurrentThread()
    {
        if (boosted || !OperatingSystem.IsWindows()) return;
        boosted = true;
        try
        {
            uint index = 0;
            var handle = AvSetMmThreadCharacteristicsW("Pro Audio", ref index);
            string result;
            if (handle != IntPtr.Zero)
            {
                AvSetMmThreadPriority(handle, 1); // AVRT_PRIORITY_HIGH
                result = "MMCSS Pro Audio";
            }
            else
            {
                SetThreadPriority(GetCurrentThread(), 2); // THREAD_PRIORITY_HIGHEST
                result = "priorità thread Highest (MMCSS non disponibile)";
            }
            lock (Gate) { threadBoost = result; boostedThreads++; }
        }
        catch (Exception exc) { EngineLog.Info("Priorità del thread di ricezione non applicata: " + exc.Message); }
    }

    static readonly Dictionary<Guid, string> PowerPlans = new()
    {
        [new Guid("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")] = "Prestazioni elevate",
        [new Guid("e9a42b02-d5df-448d-aa00-03f14749eb61")] = "Prestazioni ottimali",
        [new Guid("381b4222-f694-41f0-9685-ff5bb260df2e")] = "Bilanciato",
        [new Guid("a1841308-3541-4fab-bc81-f71556f20b4a")] = "Risparmio energia",
    };

    /// <summary>Active power plan: (name, true when it is High or Ultimate performance); null outside Windows.</summary>
    public static (string name, bool performance)? PowerPlan()
    {
        if (!OperatingSystem.IsWindows()) return null;
        try
        {
            if (PowerGetActiveScheme(IntPtr.Zero, out var pointer) != 0 || pointer == IntPtr.Zero) return null;
            try
            {
                var guid = Marshal.PtrToStructure<Guid>(pointer);
                var name = PowerPlans.GetValueOrDefault(guid) ?? "personalizzato (" + guid + ")";
                return (name, guid == new Guid("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c") || guid == new Guid("e9a42b02-d5df-448d-aa00-03f14749eb61"));
            }
            finally { LocalFree(pointer); }
        }
        catch (Exception) { return null; }
    }

    public static Dictionary<string, object?> Report()
    {
        var plan = PowerPlan();
        lock (Gate)
            return new()
            {
                ["process_priority"] = priority, ["gc_latency"] = GCSettings.LatencyMode.ToString(), ["receive_thread"] = threadBoost,
                ["boosted_threads"] = boostedThreads, ["power_plan"] = plan?.name, ["power_plan_performance"] = plan?.performance,
            };
    }

    [DllImport("avrt.dll", CharSet = CharSet.Unicode)] static extern IntPtr AvSetMmThreadCharacteristicsW(string task, ref uint index);
    [DllImport("avrt.dll")] static extern bool AvSetMmThreadPriority(IntPtr handle, int priority);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentThread();
    [DllImport("kernel32.dll")] static extern bool SetThreadPriority(IntPtr thread, int priority);
    [DllImport("powrprof.dll")] static extern uint PowerGetActiveScheme(IntPtr root, out IntPtr scheme);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
}
