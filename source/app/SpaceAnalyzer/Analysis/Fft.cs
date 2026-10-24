using System.Globalization;
using System.Numerics;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Calculations;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;

namespace SpaceAnalyzer.App.Analysis;

/// <summary>Original-sample block FFT with separate coherent and energy normalization (fft_engine.py).</summary>
public static class Fft
{
    public readonly record struct Point(double T, double V, bool Good, long? FileTime);

    static readonly SemaphoreSlim Busy = new(1, 1);

    public static Complex[] Transform(double[] values)
    {
        var a = values.Select(v => new Complex(v, 0)).ToArray();
        var n = a.Length;
        var j = 0;
        for (var i = 1; i < n; i++)
        {
            var bit = n >> 1;
            while ((j & bit) != 0) { j ^= bit; bit >>= 1; }
            j ^= bit;
            if (i < j) (a[i], a[j]) = (a[j], a[i]);
        }
        for (var size = 2; size <= n; size *= 2)
        {
            var root = Complex.Exp(new Complex(0, -2 * Math.PI / size));
            for (var b = 0; b < n; b += size)
            {
                var twiddle = Complex.One;
                for (var k = 0; k < size / 2; k++)
                {
                    var u = a[b + k];
                    var v = twiddle * a[b + k + size / 2];
                    a[b + k] = u + v;
                    a[b + k + size / 2] = u - v;
                    twiddle *= root;
                }
            }
        }
        return a[..(n / 2 + 1)];
    }

    static double Number(JsonObject o, string key, double fallback, double low, double high)
    {
        double v;
        if (!o.TryGetPropertyValue(key, out var node)) v = fallback;
        else v = node.Float() ?? throw new UserError("Parametro FFT non valido: " + key);
        if (!double.IsFinite(v) || !(low <= v && v <= high)) throw new UserError($"{key}: valore tra {Py(low)} e {Py(high)}");
        return v;
    }

    static string Py(double v) => v % 1 == 0 && Math.Abs(v) < 1e16 ? ((long)v).ToString(CultureInfo.InvariantCulture) : Engine.Core.Values.PythonRepr(v);
    static string PyNum(double v) => Engine.Core.Values.PythonRepr(v);

    static string Str(JsonObject o, string key, string fallback) => o.TryGetPropertyValue(key, out var n) ? RowValues.Str(Infrastructure.Py.Plain(n)) : fallback;

    static Dictionary<string, object?> Harmonics(List<double> power, List<double> values, double df, double fs, JsonObject options)
    {
        var mode = Str(options, "fundamental_mode", "auto");
        if (mode is not ("auto" or "manual")) throw new UserError("Modalita fondamentale non valida");
        var tolerance = Number(options, "harmonic_tolerance_percent", 3, 0, 20) / 100;
        var strongest = 1;
        for (var k = 2; k < power.Count; k++) if (power[k] > power[strongest]) strongest = k;
        var f1 = mode == "manual" ? Number(options, "fundamental_hz", df, df, fs / 2) : strongest * df;
        var note = mode == "auto"
            ? "Automatica: F1 e il picco AC dominante, che puo essere un armonico. Impostare F1 manualmente quando si conosce la frequenza fondamentale."
            : "F1 impostata manualmente.";
        note += " Frequenze misurate sulle righe FFT; incertezza di risoluzione delta-f. I picchi in banda sono candidati, non prova di una relazione armonica.";
        if (power[strongest] <= 0)
            return new() { ["fundamental_hz"] = null, ["mode"] = mode, ["rows"] = new List<object?>(), ["note"] = "Nessuna componente AC misurabile." };
        var local = Enumerable.Range(1, power.Count - 1).Where(k => power[k] > power[strongest] * 1e-12 && power[k] > power[k - 1] && (k == power.Count - 1 || power[k] >= power[k + 1])).ToList();
        var results = new List<object?>();
        for (var order = 1; order <= 5; order++)
        {
            var target = order * f1;
            var radius = Math.Min(Math.Max(2 * df, target * tolerance), .4 * f1);
            var row = new Dictionary<string, object?>
            {
                ["order"] = (long)order, ["target_hz"] = target, ["measured_hz"] = null, ["amplitude"] = null, ["bin_index"] = null,
                ["deviation_hz"] = null, ["search_half_width_hz"] = radius,
            };
            if (target > fs / 2 + df * 1e-9) row["status"] = "Oltre Nyquist";
            else if (f1 < 2 * df) row["status"] = "Risoluzione insufficiente";
            else
            {
                var candidates = local.Where(k => Math.Abs(k * df - target) <= radius + df * 1e-9).ToList();
                if (candidates.Count > 0)
                {
                    var k = candidates[0];
                    foreach (var c in candidates) if (power[c] > power[k]) k = c;
                    row["measured_hz"] = k * df; row["amplitude"] = values[k]; row["bin_index"] = (long)k; row["deviation_hz"] = k * df - target;
                    row["status"] = "Picco in banda";
                }
                else row["status"] = "Nessun picco in banda";
            }
            results.Add(row);
        }
        return new()
        {
            ["fundamental_hz"] = f1, ["mode"] = mode, ["rows"] = results, ["note"] = note,
            ["tolerance_percent"] = tolerance * 100, ["numerical_power_floor_relative"] = 1e-12,
        };
    }

    static double Median(List<double> values)
    {
        var sorted = values.OrderBy(v => v).ToList();
        var n = sorted.Count;
        return n % 2 == 1 ? sorted[n / 2] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
    }

    static double PStdev(List<double> values)
    {
        var mean = Expression.FSum(values) / values.Count;
        var ss = Expression.FSum(values.Select(v => (v - mean) * (v - mean)));
        return Math.Sqrt(ss / values.Count);
    }

    /// <summary>fft_service.calculate: collects the original points, then analyses them (one calculation at a time).</summary>
    public static Dictionary<string, object?> Calculate(IEnumerable<Row> rows, string nodeid, double start, double end, string window, bool detrend, JsonObject options)
    {
        if (!Busy.Wait(0)) throw new UserError("Un calcolo FFT e gia in corso");
        try
        {
            var points = new List<(double t, object? v, string q, object? ft)>();
            double? availableStart = null, availableEnd = null;
            foreach (var r in rows)
            {
                if (RowValues.Str(r.Value("node_id")) != nodeid || r.Value("node_id") == null) continue;
                if (RowValues.Float(r.Value("timestamp_ms")) is not double t) continue;
                availableStart = availableStart == null ? t : Math.Min(availableStart.Value, t);
                availableEnd = availableEnd == null ? t : Math.Max(availableEnd.Value, t);
                if (!(start <= t && t <= end)) continue;
                var q = RowValues.Truthy(r.Value("quality")) ? RowValues.Str(r.Value("quality")) : RowValues.TrueOne(r.TryGetValue("good", out var g) ? g : true) ? "Good" : "Bad";
                points.Add((t, r.Value("value_numeric"), q, r.Value("ads_filetime")));
                if (points.Count > 262144) throw new UserError("FFT: massimo 262.144 campioni; ridurre intervallo");
            }
            if (points.Count < 16)
            {
                var coverage = availableStart == null ? " Nessun campione disponibile per questo segnale."
                    : " Campioni disponibili da " + FdaReader.IsoMilli(availableStart.Value) + " a " + FdaReader.IsoMilli(availableEnd!.Value) + ".";
                throw new UserError($"FFT: nell’intervallo richiesto ci sono {points.Count} campioni originali (minimo 16)." + coverage +
                    " Se la vista live e precedente al buffer, riprendere Play oppure analizzare la registrazione.");
            }
            var analysed = new List<Row>(points.Count);
            foreach (var p in points)
                analysed.Add(new Row { ["node_id"] = nodeid, ["timestamp_ms"] = p.t, ["value_numeric"] = p.v, ["quality"] = p.q, ["ads_filetime"] = p.ft });
            var task = Task.Run(() => Analyze(analysed, nodeid, start, end, window, detrend, options));
            if (!task.Wait(TimeSpan.FromSeconds(30))) throw new UserError("FFT: superati 30 secondi; ridurre numero blocchi");
            return task.GetAwaiter().GetResult();
        }
        finally { Busy.Release(); }
    }

    public static Dictionary<string, object?> Analyze(IEnumerable<Row> rows, string nodeid, double start, double end, string window = "hann", bool detrend = true, JsonObject? options = null)
    {
        var o = options ?? new JsonObject();
        var points = new List<Point>();
        var bad = 0;
        var policy = Str(o, "gap_policy", "reject");
        if (policy is not ("reject" or "skip")) throw new UserError("Politica qualita FFT non valida");
        foreach (var r in rows)
        {
            if (!Equals(r.Value("node_id"), nodeid)) continue;
            if (RowValues.Float(r.Value("timestamp_ms")) is not double t) continue;
            if (!double.IsFinite(t) || !(start <= t && t <= end)) continue;
            var v = RowValues.Float(r.Value("value_numeric")) ?? double.NaN;
            var q = RowValues.Truthy(r.Value("quality")) ? RowValues.Str(r.Value("quality")) : RowValues.TrueOne(r.TryGetValue("good", out var g) ? g : true) ? "Good" : "Bad";
            var good = q == "Good" && double.IsFinite(v);
            if (!good) bad++;
            long? ft = null;
            var raw = r.Value("ads_filetime");
            if (RowValues.Truthy(raw))
            {
                if (raw is long l) ft = l;
                else if (raw is double d && double.IsFinite(d)) ft = (long)d;
                else if (raw is string s && long.TryParse(s.Trim(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var parsed)) ft = parsed;
                else throw new UserError("Timestamp ADS non valido");
            }
            points.Add(new Point(t, v, good, ft == 0 ? null : ft));
            if (points.Count > 262144) throw new UserError("FFT: massimo 262.144 campioni; ridurre intervallo");
        }
        if (points.Count < 16) throw new UserError("FFT: servono almeno 16 campioni");
        if (bad > 0 && policy == "reject") throw new UserError("FFT: campioni Bad/Uncertain o non numerici");
        var deltas = new List<double>(points.Count - 1);
        for (var i = 0; i + 1 < points.Count; i++)
        {
            var a = points[i]; var b = points[i + 1];
            deltas.Add(a.FileTime is long fa && b.FileTime is long fb ? (fb - fa) / 10000.0 : b.T - a.T);
        }
        if (deltas.Min() <= 0) throw new UserError("FFT: timestamp non strettamente crescenti");
        var dt = Median(deltas);
        var fs = 1000 / dt;
        var tolerance = Number(o, "timing_tolerance_percent", 5, .01, 25) / 100;
        var gaps = new HashSet<int>();
        for (var i = 0; i < deltas.Count; i++) if (Math.Abs(deltas[i] - dt) > Math.Max(dt * tolerance, .001)) gaps.Add(i + 1);
        if (gaps.Count > 0 && policy == "reject") throw new UserError("FFT: buco temporale o campionamento irregolare; nessun ricampionamento automatico");
        var mode = Str(o, "resolution_mode", "samples");
        double desired = mode switch
        {
            "samples" => Number(o, "block_size", 0, 0, 65536),
            "hz" => fs / Number(o, "resolution_value", .1, .000001, fs / 2),
            "seconds" => fs * Number(o, "resolution_value", 10, .000001, 86400),
            _ => throw new UserError("Modalita risoluzione non valida"),
        };
        int n;
        if (desired == 0) n = Math.Min(2048, 1 << (BitLength(points.Count) - 1));
        else
        {
            var exponent = Math.Ceiling(Math.Log2(Math.Max(1, desired)));
            if (exponent > 30) throw new UserError("FFT: massimo 65536 campioni per blocco");
            n = Math.Max(16, 1 << (int)exponent);
        }
        if (n > 65536) throw new UserError("FFT: massimo 65536 campioni per blocco");
        if (points.Count < n) throw new UserError($"FFT: servono {n} campioni ({(n / fs).ToString("F3", CultureInfo.InvariantCulture)} s); disponibili {points.Count}");
        var coefficients = new Dictionary<string, double[]>
        {
            ["rectangular"] = [1.0], ["hann"] = [.5, -.5], ["blackmanharris"] = [.35875, -.48829, .14128, -.01168],
            ["flattop"] = [.21557895, -.41663158, .277263158, -.083578947, .006947368],
        };
        if (!coefficients.TryGetValue(window, out var coeff)) throw new UserError("Finestra FFT non valida");
        var weights = new double[n];
        for (var i = 0; i < n; i++)
        {
            double sum = 0;
            for (var j = 0; j < coeff.Length; j++) sum += coeff[j] * Math.Cos(2 * Math.PI * j * i / n);
            weights[i] = sum;
        }
        double sw = 0, sw2 = 0;
        foreach (var w in weights) { sw += w; sw2 += w * w; }
        var df = fs / n;
        var enbw = fs * sw2 / (sw * sw);
        var hop = Math.Max(1, (int)Math.Round(n * (1 - Number(o, "overlap_percent", 50, 0, 90) / 100), MidpointRounding.ToEven));
        var average = Str(o, "averaging", "energy");
        var output = Str(o, "output", "peak");
        if (average is not ("none" or "energy" or "exponential" or "maxhold")) throw new UserError("Media FFT non valida");
        if (output is not ("peak" or "rms" or "power" or "psd" or "asd")) throw new UserError("Formato FFT non valido");
        var count = Number(o, "average_count", 0, 0, 512);
        if (count % 1 != 0) throw new UserError("Numero medie intero richiesto");
        var tau = Number(o, "tau_seconds", 5, .01, 3600);
        var invalid = new int[points.Count + 1];
        var discontinuities = new int[points.Count + 1];
        for (var i = 0; i < points.Count; i++)
        {
            invalid[i + 1] = invalid[i] + (points[i].Good ? 0 : 1);
            discontinuities[i + 1] = discontinuities[i] + (gaps.Contains(i) ? 1 : 0);
        }
        var starts = new List<int>();
        for (var first = 0; first <= points.Count - n; first += hop) starts.Add(first);
        var valid = new List<int>();
        var excluded = 0;
        foreach (var first in starts)
        {
            if (invalid[first + n] != invalid[first] || discontinuities[first + n] != discontinuities[first + 1]) excluded++;
            else valid.Add(first);
        }
        if (valid.Count == 0) throw new UserError($"FFT: nessun blocco continuo Good valido; {excluded} blocchi esclusi");
        if (count > 0) valid = valid.Skip(Math.Max(0, valid.Count - (int)count)).ToList();
        if (valid.Count > 512 || valid.Count * n * Math.Log2(n) > 16000000) throw new UserError("FFT: limitare numero medie, intervallo o overlap");
        double[][]? result = null, hold = null, last = null;
        double previous = 0;
        var used = new HashSet<int>();
        for (var ordinal = 0; ordinal < valid.Count; ordinal++)
        {
            var first = valid[ordinal];
            var values = new double[n];
            for (var i = 0; i < n; i++) values[i] = points[first + i].V;
            var mean = Expression.FSum(values) / n;
            var windowed = new double[n];
            for (var i = 0; i < n; i++) windowed[i] = (values[i] - mean) * weights[i];
            var transformed = Transform(windowed);
            transformed[0] = new Complex(detrend ? 0 : Math.Abs(mean) * sw, 0);
            var power = new double[transformed.Length];
            var psd = new double[transformed.Length];
            for (var k = 0; k < transformed.Length; k++)
            {
                var factor = k == 0 || k == n / 2 ? 1 : 2;
                var magnitude = Math.Pow(Complex.Abs(transformed[k]), 2);
                power[k] = factor * magnitude / (sw * sw);
                psd[k] = factor * magnitude / (fs * sw2);
            }
            psd[0] = power[0] / df;
            last = [power, psd];
            if (result == null)
            {
                result = [(double[])power.Clone(), (double[])psd.Clone()];
                hold = [(double[])power.Clone(), (double[])psd.Clone()];
            }
            else
            {
                var alpha = 1 - Math.Exp(-(points[first].T - previous) / 1000 / tau);
                for (var field = 0; field < 2; field++)
                    for (var k = 0; k < last[field].Length; k++)
                    {
                        var v = last[field][k];
                        hold![field][k] = Math.Max(hold[field][k], v);
                        if (average == "energy") result[field][k] += (v - result[field][k]) / (ordinal + 1);
                        else if (average == "exponential") result[field][k] += alpha * (v - result[field][k]);
                        else result[field][k] = v;
                    }
            }
            previous = points[first].T;
            for (var i = first; i < first + n; i++) used.Add(i);
        }
        if (average == "maxhold") result = hold;
        if (average == "none") result = last;
        List<double> Convert(double[][] s)
        {
            if (output == "psd") return s[1].ToList();
            if (output == "asd") return s[1].Select(v => Math.Sqrt(Math.Max(0, v))).ToList();
            if (output == "power") return s[0].ToList();
            return s[0].Select((v, k) => Math.Sqrt(Math.Max(0, v)) * (output == "peak" && k != 0 && k != n / 2 ? Math.Sqrt(2) : 1)).ToList();
        }
        var y = Convert(result!);
        var current = Convert(last!);
        var maximum = Convert(hold!);
        var frequencies = Enumerable.Range(0, y.Count).Select(k => k * df).ToList();
        var low = Number(o, "band_min_hz", 0, 0, fs / 2);
        var high = Math.Min(fs / 2, Number(o, "band_max_hz", fs / 2, 0, 1e9));
        var band = Enumerable.Range(0, frequencies.Count).Where(k => low <= frequencies[k] && frequencies[k] <= high).ToList();
        if (band.Count == 0) throw new UserError("Nessuna riga FFT nella banda RMS richiesta");
        var peak = 1;
        for (var k = 2; k < y.Count; k++) if (y[k] > y[peak]) peak = k;
        var peaks = Enumerable.Range(1, Math.Max(0, y.Count - 2)).Where(i => y[i] > y[i - 1] && y[i] >= y[i + 1])
            .OrderByDescending(i => y[i]).Take(10).ToList();
        var unit = Str(o, "unit", "u");
        unit = unit.Length > 40 ? unit[..40] : unit;
        unit = unit.Trim();
        if (unit.Length == 0) unit = "u";
        var units = new Dictionary<string, string> { ["peak"] = unit + " peak", ["rms"] = unit + " RMS", ["power"] = unit + "²", ["psd"] = unit + "²/Hz", ["asd"] = unit + "/√Hz" };
        var harmonics = Harmonics(result![0].ToList(), y, df, fs, o);
        List<object?> Pairs(List<double> values) => frequencies.Select((f, k) => (object?)new List<object?> { f, values[k] }).ToList();
        return new()
        {
            ["harmonics"] = harmonics, ["bins"] = Pairs(y), ["current_bins"] = Pairs(current), ["maxhold_bins"] = Pairs(maximum),
            ["samples_original"] = (long)points.Count, ["samples_fft"] = (long)n, ["samples_used"] = (long)used.Count, ["samples_unused"] = (long)(points.Count - used.Count),
            ["sample_hz"] = fs, ["nyquist_hz"] = fs / 2, ["resolution_hz"] = df, ["block_seconds"] = n / fs, ["hop_seconds"] = hop / fs,
            ["overlap_percent"] = 100 * (1 - (double)hop / n), ["window"] = window, ["detrend"] = detrend, ["averaging"] = average, ["output"] = output,
            ["blocks_total"] = (long)starts.Count, ["blocks_used"] = (long)valid.Count, ["blocks_excluded"] = (long)excluded, ["bad_samples"] = (long)bad,
            ["timing_breaks"] = (long)gaps.Count, ["jitter_percent"] = PStdev(deltas) / dt * 100, ["timing_tolerance_percent"] = tolerance * 100,
            ["noise_bandwidth_hz"] = enbw, ["coherent_gain"] = sw / n, ["peak_hz"] = frequencies[peak], ["peak_amplitude"] = y[peak],
            ["amplitude_unit"] = units[output], ["signal_unit"] = unit,
            ["band_min_hz"] = frequencies[band[0]], ["band_max_hz"] = frequencies[band[^1]],
            ["band_rms"] = Math.Sqrt(band.Sum(k => result[1][k]) * df), ["overall_rms"] = Math.Sqrt(result[1].Sum() * df),
            ["rms_kind"] = average == "maxhold" ? "inviluppo Max Hold (non RMS temporale)" : "da PSD selezionata",
            ["peaks"] = peaks.Select(k => (object?)new Dictionary<string, object?> { ["hz"] = frequencies[k], ["value"] = y[k] }).ToList(),
            ["resampled"] = false,
            ["time_basis"] = points.Any(p => p.FileTime != null) ? "ADS FILETIME originale" : "timestamp registrati",
            ["first_timestamp_ms"] = points[valid[0]].T, ["last_timestamp_ms"] = points[valid[^1] + n - 1].T,
            ["note"] = "Campioni originali, senza interpolazione o decimazione temporale. Blocchi completi validi; Max Hold e medie riferiti al solo intervallo analizzato.",
        };
    }

    static int BitLength(int value) => 32 - BitOperations.LeadingZeroCount((uint)value);
}
