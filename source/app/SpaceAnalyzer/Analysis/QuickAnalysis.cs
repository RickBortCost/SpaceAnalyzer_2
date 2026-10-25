using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Calculations;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;

namespace SpaceAnalyzer.App.Analysis;

/// <summary>X1–X2 interval helpers (analysis_tools.py).</summary>
public static class Interval
{
    public static (double start, double end) Of(JsonNode? data)
    {
        var a = data.Get("x1").Float();
        var b = data.Get("x2").Float();
        if (a == null || b == null) throw new UserError("Posizionare X1 e X2 prima di analizzare o esportare");
        if (!double.IsFinite(a.Value) || !double.IsFinite(b.Value) || a == b) throw new UserError("Intervallo X1–X2 non valido");
        return (Math.Min(a.Value, b.Value), Math.Max(a.Value, b.Value));
    }

    public static IEnumerable<Row> SelectedRows(IEnumerable<Row> rows, IEnumerable<string> ids, double start, double end)
    {
        var set = new HashSet<string>(ids);
        if (set.Count == 0 || set.Count > 64) throw new UserError("Selezionare da 1 a 64 segnali");
        return Iterate();
        IEnumerable<Row> Iterate()
        {
            foreach (var row in rows)
            {
                if (RowValues.Float(row.Value("timestamp_ms")) is not double t) continue;
                if (row.Value("node_id") is string n && set.Contains(n) && start <= t && t <= end) yield return row;
            }
        }
    }
}

/// <summary>Streaming interval statistics (quick_analysis.py).</summary>
public sealed class IntervalStatistics(double x1, double x2)
{
    Dictionary<string, object?>? a, b;
    double da = double.PositiveInfinity, db = double.PositiveInfinity;
    long count, excluded;
    double scale, mean, m2, squares, low = double.PositiveInfinity, high = double.NegativeInfinity, integral, duration;
    double? lastValue;
    Row? previous;
    string status = "";

    static double? Valid(Row r)
    {
        var quality = r.TryGetValue("quality", out var q) ? q : "Good";
        if (!Equals(quality, "Good")) return null;
        return r.Value("value_numeric") switch { double d when double.IsFinite(d) => d, long l => l, _ => null };
    }

    static double Period(Row r) => RowValues.Truthy(r.Value("sample_ms")) ? RowValues.Float(r.Value("sample_ms")) ?? 0 : 0;

    public void Add(Row r)
    {
        var t = RowValues.Float(r.Value("timestamp_ms")) ?? double.NaN;
        var v = Valid(r);
        var good = v != null;
        status = RowValues.Str(r.TryGetValue("status", out var s) ? s : "");
        foreach (var key in new[] { 'a', 'b' })
        {
            var target = key == 'a' ? x1 : x2;
            var distance = Math.Abs(t - target);
            if (distance <= (key == 'a' ? da : db))
            {
                var point = good ? new Dictionary<string, object?> { ["timestamp_ms"] = r.Value("timestamp_ms"), ["value"] = r.Value("value_numeric") } : null;
                if (key == 'a') { da = distance; a = point; } else { db = distance; b = point; }
            }
        }
        if (previous != null)
        {
            var left = previous;
            var dt = (t - (RowValues.Float(left.Value("timestamp_ms")) ?? double.NaN)) / 1000;
            var limit = 3 * Math.Max(Period(left), Period(r)) / 1000;
            if (good && Valid(left) is double lv && 0 < dt && dt <= limit) { integral += (lv / 2 + v!.Value / 2) * dt; duration += dt; }
        }
        previous = r;
        if (!good) { excluded++; return; }
        var x0 = v!.Value;
        count++;
        lastValue = x0;
        low = Math.Min(low, x0); high = Math.Max(high, x0);
        var newScale = Math.Max(Math.Max(scale, Math.Abs(x0)), 1e-300);
        if (newScale != scale)
        {
            var ratio = scale / newScale;
            mean *= ratio; m2 *= ratio * ratio; squares *= ratio * ratio; scale = newScale;
        }
        var x = x0 / scale;
        var delta = x - mean;
        mean += delta / count;
        m2 += delta * (x - mean);
        squares += x * x;
    }

    public Dictionary<string, object?> Result(string nid)
    {
        var stats = new Dictionary<string, object?>();
        if (count > 0)
        {
            double? delta = a != null && b != null ? RowValues.Float(b["value"]) - RowValues.Float(a["value"]) : null;
            stats["last"] = lastValue; stats["min"] = low; stats["max"] = high; stats["mean"] = mean * scale;
            stats["rms"] = Math.Sqrt(Math.Max(0, squares / count)) * scale;
            stats["std"] = Math.Sqrt(Math.Max(0, m2 / count)) * scale;
            stats["p2p"] = high - low;
            stats["delta"] = delta;
            stats["slope"] = delta != null && x2 != x1 ? delta / ((x2 - x1) / 1000) : null;
            stats["integral"] = duration != 0 ? integral : null;
        }
        return new()
        {
            ["nodeid"] = nid, ["x1"] = a, ["x2"] = b, ["count"] = count, ["excluded"] = excluded, ["last_status"] = status,
            ["statistics"] = stats.ToDictionary(k => k.Key, k => k.Value is double d && double.IsFinite(d) ? (object?)d : null),
        };
    }

    public static List<object?> Summarize(IEnumerable<Row> rows, IList<string> ids, double start, double end, double x1, double x2)
    {
        var groups = new Dictionary<string, IntervalStatistics>();
        foreach (var nid in ids) groups[nid] = new IntervalStatistics(x1, x2);
        foreach (var row in rows)
        {
            if (row.Value("node_id") is not string n || !groups.TryGetValue(n, out var g)) continue;
            if (RowValues.Float(row.Value("timestamp_ms")) is double t && start <= t && t <= end) g.Add(row);
        }
        return groups.Select(k => (object?)k.Value.Result(k.Key)).ToList();
    }

    /// <summary>formula_rows: causal evaluation of one formula over ordered rows.</summary>
    public static IEnumerable<Row> FormulaRows(IEnumerable<Row> rows, Plan plan, string nid, double start, double end, double period)
    {
        var engine = new FormulaEngine(plan);
        var group = new List<FormulaInput>();
        double? current = null;
        IEnumerable<Row> Flush(double t)
        {
            var output = engine.Process(t, group);
            if (start <= t && t <= end)
                foreach (var row in output)
                    if (Equals(row["node_id"], nid))
                    {
                        var result = new Row();
                        foreach (var (k, v) in row) result[k] = v;
                        result["sample_ms"] = period;
                        yield return result;
                    }
        }
        foreach (var row in rows)
        {
            var t = RowValues.Float(row.Value("timestamp_ms")) ?? double.NaN;
            if (current != null && !t.Equals(current.Value))
            {
                foreach (var r in Flush(current.Value)) yield return r;
                group = new List<FormulaInput>();
            }
            current = t;
            group.Add(Input(row));
        }
        if (current != null) foreach (var r in Flush(current.Value)) yield return r;
    }

    /// <summary>Engine input of a row: number() value, explicit quality else good flag.</summary>
    public static FormulaInput Input(Row row) => new(RowValues.Str(row.Value("node_id")), RowValues.Finite(row.Value("value_numeric")),
        RowValues.Truthy(row.Value("quality")) ? RowValues.Str(row.Value("quality")) : RowValues.TrueOne(row.TryGetValue("good", out var g) ? g : true) ? "Good" : "Bad");
}
