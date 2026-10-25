using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.Engine.Acquisition;

/// <summary>
/// Which originals belong to the armed recording (acquisition_record of app.py):
/// current selection only, inside the monotonic receive fences of Record and Stop.
/// </summary>
public sealed class RecordGate
{
    readonly object gate = new();
    RecordingSession? session;
    long beginMono;
    long? endMono;
    double? rawLastT;
    public Func<long> CurrentRevision { get; set; } = () => 0;

    public RecordingSession? Session { get { lock (gate) return session; } }
    public double? RawLastT { get { lock (gate) return rawLastT; } }

    public void Arm(RecordingSession recording)
    {
        lock (gate) { session = recording; beginMono = Clock.MonoNs(); endMono = null; rawLastT = null; }
    }

    /// <summary>Stop fence: later arrivals belong to no recording.</summary>
    public void Fence() { lock (gate) endMono ??= Clock.MonoNs(); }

    public void Disarm(RecordingSession recording)
    {
        lock (gate) if (session == recording) session = null;
    }

    public void Fail(string message)
    {
        RecordingSession? current;
        lock (gate) current = session;
        current?.Fail(message);
    }

    public bool? Record(double t, List<Sample> rows, int period, Generation? generation)
    {
        RecordingSession? current; long begin; long? end;
        lock (gate) { current = session; begin = beginMono; end = endMono; }
        if (current == null) return null;
        if (generation is Generation g && g.Revision != CurrentRevision()) return false;
        var selected = rows.Where(r => r.ReceivedMonoNs >= begin && (end == null || r.ReceivedMonoNs <= end)).ToList();
        if (selected.Count == 0) return false;
        var accepted = selected[0].AcquisitionOrigin == "notification"
            ? current.EnqueuePrimary(t, selected, period)
            : current.Enqueue(t, selected, period);
        if (accepted) lock (gate) rawLastT = rawLastT is double last ? Math.Max(last, t) : t;
        return accepted;
    }
}
