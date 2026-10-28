using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using SpaceAnalyzer.App.Web;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Influx;

/// <summary>
/// Export of InfluxDB series to CSV, Parquet, FDA DAS4 or RBF, in background. Every original point of the chosen series and period is read from the
/// server in time slices (so memory stays bounded whatever the period), merged in time order and written like a recording of this application.
/// FDA and RBF are produced by the recording exporters from an intermediate Parquet, deleted at the end.
/// </summary>
public sealed class InfluxExports(InfluxService influx, Func<string> defaultDirectory)
{
    /// <summary>Points read from the server per slice (all series together): bounds the memory of one slice.</summary>
    public const long SlicePoints = 250_000;

    readonly object gate = new();
    readonly Dictionary<string, Dictionary<string, object?>> jobs = new();
    readonly List<string> order = new();
    CancellationTokenSource? running;
    bool closed;

    sealed record Job(string Ident, List<InfluxService.ExportSeries> Series, string Format, double Start, double End, string Directory, string Stem, string Server);

    public Dictionary<string, object?> Start(JsonNode? data)
    {
        var format = data.Get("format").Str() ?? "";
        if (!Conversions.Targets.ContainsKey(format)) throw new UserError("Formato di destinazione non valido: scegliere CSV, Parquet, FDA DAS4 o RBF");
        var series = InfluxService.ExportPlan(data.Get("series"));
        if (format is "das" or "rbf" && series.Count > Conversions.MaxLegacySignals)
            throw new UserError($"{Conversions.Targets[format]}: selezionare al massimo {Conversions.MaxLegacySignals} serie (selezionate {series.Count})");
        var start = data.Get("start_ms").Float();
        var end = data.Get("end_ms").Float();
        if (start is not double s0 || end is not double e0 || !double.IsFinite(s0) || !double.IsFinite(e0)) throw new UserError("Periodo da esportare non valido");
        if (!(e0 > s0)) throw new UserError("L'intervallo di tempo non è valido: la fine deve seguire l'inizio.");
        var directory = data.Get("directory").Str()?.Trim() ?? "";
        if (directory.Length == 0) directory = defaultDirectory();
        else if (!Path.IsPathRooted(directory)) throw new UserError("Indicare un percorso completo per la cartella di destinazione");
        directory = Path.GetFullPath(directory);
        var name = data.Get("name").Str()?.Trim();
        var stem = Conversions.SafeStem(string.IsNullOrEmpty(name) ? "InfluxDB" : Path.GetFileNameWithoutExtension(name));
        lock (gate)
        {
            if (closed) throw new UserError("Servizio di esportazione arrestato");
            if (jobs.Values.Any(j => (string?)j["state"] == "running")) throw new UserError("Un'esportazione è già in corso: attendere il completamento o annullarla");
            var ident = Convert.ToHexString(RandomNumberGenerator.GetBytes(12)).ToLowerInvariant();
            while (order.Count > 15) { jobs.Remove(order[0]); order.RemoveAt(0); }
            jobs[ident] = new Dictionary<string, object?>
            {
                ["id"] = ident, ["state"] = "running", ["phase"] = "count", ["format"] = format, ["format_label"] = Conversions.Targets[format],
                ["signal_count"] = (long)series.Count, ["rows_read"] = 0L, ["rows_total"] = null, ["files"] = new List<object?>(),
                ["directory"] = directory, ["start_ms"] = s0, ["end_ms"] = e0, ["started_at"] = Values.PythonIso(DateTime.UtcNow),
            };
            order.Add(ident);
            var cancel = running = new CancellationTokenSource();
            var job = new Job(ident, series, format, s0, e0, directory, stem, influx.ServerUrl);
            new Thread(() => Run(job, cancel.Token)) { IsBackground = true, Name = "influx-export" }.Start();
            return Status(ident);
        }
    }

    public Dictionary<string, object?> Status(string ident)
    {
        lock (gate)
        {
            if (!jobs.TryGetValue(ident, out var job)) throw new UserError("Esportazione non trovata");
            var copy = new Dictionary<string, object?>(job);
            copy["files"] = ((List<object?>)job["files"]!).ToList();
            return copy;
        }
    }

    public Dictionary<string, object?> Cancel(string ident)
    {
        lock (gate)
        {
            if (!jobs.TryGetValue(ident, out var job)) throw new UserError("Esportazione non trovata");
            if ((string?)job["state"] == "running") running?.Cancel();
        }
        return Status(ident);
    }

    public void Close()
    {
        lock (gate) { closed = true; running?.Cancel(); }
    }

    void Update(string ident, params (string key, object? value)[] values)
    {
        lock (gate) foreach (var (k, v) in values) jobs[ident][k] = v;
    }

    sealed class Stats
    {
        public long Rows;
        public double First = double.PositiveInfinity, Last = double.NegativeInfinity;
        public double? SampleMs;
    }

    void Run(Job job, CancellationToken cancel)
    {
        string? work = null;
        var written = new List<string>();
        try
        {
            Directory.CreateDirectory(job.Directory);
            var total = 0L;
            var counts = new long[job.Series.Count];
            for (var i = 0; i < job.Series.Count; i++)
            {
                counts[i] = influx.CountPoints(job.Series[i], job.Start, job.End + 1, cancel).GetAwaiter().GetResult();
                total += counts[i];
            }
            if (total == 0) throw new UserError("Nessun dato nel periodo scelto per le serie selezionate");
            Update(job.Ident, ("phase", "read"), ("rows_total", total));
            var stem = job.Stem + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
            var stats = new Stats();
            var rows = Rows(job, total, stats, cancel);
            List<Dictionary<string, object?>> files;
            if (job.Format is "csv" or "parquet")
            {
                var final = Conversions.Unique(Path.Combine(job.Directory, stem + "." + job.Format));
                written.Add(final + ".part");
                Export.ExportRows(rows, final + ".part", job.Format);
                File.Move(final + ".part", final);
                written.Add(final);
                Py.AtomicPlain(RecordingLibrary.MetadataPath(final), Sidecar(job, stats, job.Format));
                files = [Entry(final, stats.Rows)];
            }
            else
            {
                work = Path.Combine(job.Directory, ".esportazione_" + job.Ident[..8]);
                Directory.CreateDirectory(work);
                var original = Path.Combine(work, stem + ".parquet");
                Export.ExportRows(rows, original + ".part", "parquet");
                File.Move(original + ".part", original);
                Py.AtomicPlain(RecordingLibrary.MetadataPath(original), Sidecar(job, stats, "parquet"));
                Update(job.Ident, ("phase", "write"));
                cancel.ThrowIfCancellationRequested();
                var nodes = job.Series.Select(x => Conversions.Node(new Dictionary<string, object?> { ["nodeid"] = x.Key, ["display_name"] = x.Name, ["unit"] = "" })).ToList();
                var parts = job.Format == "das" ? FdaExporter.Export(original, nodes, 0) : RbfExporter.Export(original, nodes, 0);
                files = [];
                foreach (var part in parts)
                {
                    var produced = Path.Combine(work, (string)part["name"]!);
                    var final = Conversions.Unique(Path.Combine(job.Directory, (string)part["name"]!));
                    var sidecar = RecordingLibrary.MetadataPath(produced);
                    if (File.Exists(sidecar)) File.WriteAllText(RecordingLibrary.MetadataPath(final), Retarget(File.ReadAllText(sidecar), job));
                    File.Move(produced, final);
                    written.Add(final);
                    files.Add(Entry(final, part["rows"] is long r ? r : 0));
                }
            }
            Update(job.Ident, ("state", "ready"), ("phase", "done"), ("rows_read", stats.Rows), ("files", files.Cast<object?>().ToList()),
                ("finished_at", Values.PythonIso(DateTime.UtcNow)));
            Log.Info($"Esportazione InfluxDB -> {job.Format}: {stats.Rows} campioni, {files.Count} file");
        }
        catch (Exception exc)
        {
            foreach (var file in written)
            {
                try { File.Delete(file); File.Delete(RecordingLibrary.MetadataPath(file)); } catch (Exception) { }
            }
            var cancelled = exc is OperationCanceledException;
            Update(job.Ident, ("state", cancelled ? "cancelled" : "error"), ("error", cancelled ? "Esportazione annullata" : Http.Map(exc).message));
            if (!cancelled) Log.Warning($"Esportazione InfluxDB -> {job.Format} non riuscita: {exc.Message}");
        }
        finally
        {
            if (work != null)
            {
                try { Directory.Delete(work, recursive: true); } catch (Exception) { }
            }
            lock (gate) running = null;
        }
    }

    /// <summary>Original points in time order. The period is read in slices of about <see cref="SlicePoints"/> points; rows of one slice are merged and sorted by time.</summary>
    IEnumerable<Row> Rows(Job job, long total, Stats stats, CancellationToken cancel)
    {
        var end = job.End + 1;
        var span = end - job.Start;
        var slice = total <= SlicePoints ? span : Math.Max(1000.0, span * SlicePoints / total);
        var ordinal = new long[job.Series.Count];
        var lastTime = new double[job.Series.Count];
        Array.Fill(lastTime, double.NaN);
        for (var from = job.Start; from < end; from += slice)
        {
            cancel.ThrowIfCancellationRequested();
            var to = Math.Min(end, from + slice);
            var batch = new List<(double Time, int Series, long Cycle, double Value)>();
            for (var i = 0; i < job.Series.Count; i++)
            {
                foreach (var (t, v) in influx.RawPoints(job.Series[i], from, to, cancel).GetAwaiter().GetResult())
                    batch.Add((t, i, ordinal[i]++, v));
            }
            batch.Sort((a, b) => a.Time != b.Time ? a.Time.CompareTo(b.Time) : a.Series.CompareTo(b.Series));
            foreach (var (t, i, cycle, v) in batch)
            {
                cancel.ThrowIfCancellationRequested();
                var series = job.Series[i];
                if (!double.IsNaN(lastTime[i]) && t - lastTime[i] > 0) stats.SampleMs = stats.SampleMs is double m ? Math.Min(m, t - lastTime[i]) : t - lastTime[i];
                lastTime[i] = t;
                stats.First = Math.Min(stats.First, t);
                stats.Last = Math.Max(stats.Last, t);
                stats.Rows++;
                if (stats.Rows % 65536 == 0) Update(job.Ident, ("rows_read", stats.Rows));
                yield return new Row
                {
                    ["timestamp_utc"] = Values.IsoFromMs(t), ["timestamp_ms"] = t, ["elapsed_ms"] = t - job.Start, ["cycle"] = cycle,
                    ["node_id"] = series.Key, ["signal_name"] = series.Name, ["value_numeric"] = v, ["value_text"] = "",
                    ["data_type"] = series.DataType, ["status"] = "Good", ["good"] = true, ["quality"] = "Good", ["status_code"] = 0L,
                    ["source_protocol"] = "influxdb",
                };
            }
        }
    }

    Dictionary<string, object?> Sidecar(Job job, Stats stats, string format)
    {
        var meta = new Dictionary<string, object?>
        {
            ["endpoint"] = job.Server, ["source_protocol"] = "influxdb", ["timestamp_order"] = "time",
            ["format"] = format,
            ["storage"] = new Dictionary<string, object?> { ["format"] = format, ["label"] = format == "csv" ? "CSV" : "Apache Parquet", ["lossless"] = true },
            ["state"] = "complete", ["exported_from"] = "InfluxDB", ["converted_at"] = Values.PythonIso(DateTime.UtcNow),
            ["signals"] = job.Series.Select(s => (object?)new Dictionary<string, object?> { ["nodeid"] = s.Key, ["display_name"] = s.Name, ["unit"] = "" }).ToList(),
            ["rows_written"] = stats.Rows,
        };
        if (stats.SampleMs is double ms && ms > 0) meta["initial_sample_ms"] = ms;
        if (stats.Rows > 0)
        {
            meta["start_ms"] = stats.First; meta["end_ms"] = stats.Last;
            meta["started_at"] = Values.IsoFromMs(stats.First);
            meta["ended_at"] = Values.IsoFromMs(stats.Last);
            meta["duration_seconds"] = (stats.Last - stats.First) / 1000;
        }
        return meta;
    }

    static string Retarget(string json, Job job)
    {
        var node = JsonNode.Parse(json) as JsonObject ?? new JsonObject();
        node.Remove("original_archive");
        node["exported_from"] = "InfluxDB";
        node["endpoint"] = job.Server;
        node["converted_at"] = Values.PythonIso(DateTime.UtcNow);
        return node.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
    }

    static Dictionary<string, object?> Entry(string path, long rows) => new()
    {
        ["file"] = Path.GetFileName(path), ["path"] = path,
        ["bytes"] = new FileInfo(path).Length, ["rows"] = rows, ["format"] = Path.GetExtension(path).TrimStart('.').ToLowerInvariant(),
    };
}
