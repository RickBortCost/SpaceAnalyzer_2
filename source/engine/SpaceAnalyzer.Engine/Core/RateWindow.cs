using System.Diagnostics;
using System.Text.Json;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>Instantaneous rates of increasing counters over a sliding window (as acquisition_health.RateWindow).</summary>
public sealed class RateWindow(double seconds = 10, double interval = .25)
{
    readonly LinkedList<(double t, (string name, long value)[] counters)> points = new();
    readonly object gate = new();

    public void Observe(params (string name, long value)[] counters)
    {
        var now = Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency;
        lock (gate)
        {
            if (points.Count > 0 && now - points.Last!.Value.t < interval) points.Last.Value = (points.Last.Value.t, counters);
            else points.AddLast((now, counters));
            while (points.Count > 2 && now - points.First!.Value.t > seconds) points.RemoveFirst();
        }
    }

    public void Write(Utf8JsonWriter w)
    {
        lock (gate)
        {
            w.WriteStartObject();
            if (points.Count < 2) { w.WriteNumber("window_s", 0.0); w.WriteEndObject(); return; }
            var first = points.First!.Value;
            var last = points.Last!.Value;
            var span = Stopwatch.GetTimestamp() / (double)Stopwatch.Frequency - first.t;
            w.WriteNumber("window_s", span);
            foreach (var (name, value) in last.counters)
            {
                var start = first.counters.FirstOrDefault(c => c.name == name).value;
                w.WriteNumber(name + "_per_second", span > 0 ? (value - start) / span : 0);
            }
            w.WriteEndObject();
        }
    }
}
