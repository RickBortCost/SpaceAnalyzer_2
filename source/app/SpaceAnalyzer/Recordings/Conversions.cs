using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Web;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// Conversion of one recording (CSV, Parquet, FDA DAS4, RBF, iba DAT) into CSV, Parquet, FDA DAS4 or RBF, in background.
/// Every original sample of the selected signals and interval is written; missing FDA/RBF frame slots are not samples and are skipped.
/// FDA and RBF are produced by the recording exporters from an intermediate Parquet, deleted at the end.
/// The output goes to the "Conversioni" folder of the recordings, so it is listed on the Recordings page.
/// </summary>
public sealed class Conversions(Func<string> directory)
{
    public static readonly Dictionary<string, string> Targets = new()
    {
        ["csv"] = "CSV", ["parquet"] = "Apache Parquet", ["das"] = "FDA DAS4", ["rbf"] = "RBF v3",
    };
    public const int MaxLegacySignals = 64;
    public const string Folder = "Conversioni";

    readonly object gate = new();
    readonly Dictionary<string, Dictionary<string, object?>> jobs = new();
    readonly List<string> order = new();
    CancellationTokenSource? running;
    bool closed;

    sealed record Job(string Ident, RecordingLibrary Library, string Path, string Name, string Format, List<Dictionary<string, object?>> Signals,
        double? Start, double? End, long? Total, JsonObject Metadata);

    public Dictionary<string, object?> Start(JsonNode? data, Func<string, RecordingLibrary> resolver)
    {
        var source = data.Get("source").Truthy() ? data.Get("source").Str()! : "repository";
        if (source == "primary") return StartPrimary(data);
        var library = resolver(source);
        var name = data.Get("name").Str();
        var path = library.PathOf(name);
        var format = data.Get("format").Str() ?? "";
        if (!Targets.ContainsKey(format)) throw new UserError("Formato di destinazione non valido: scegliere CSV, Parquet, FDA DAS4 o RBF");
        var meta = library.Metadata(path);
        if (meta["state"].Str() is "recording" or "stopping") throw new UserError("Registrazione in corso: premere Stop prima di convertire il file");
        var descriptor = library.Describe(name!, source: source);
        var available = ((List<object?>)descriptor["signals"]!).OfType<Dictionary<string, object?>>().ToList();
        var signals = available;
        if (data.Get("signal_ids") is JsonArray list && list.Count > 0)
        {
            var wanted = list.Select(n => RowValues.Str(Py.Plain(n))).ToHashSet();
            signals = available.Where(s => wanted.Contains((string)s["nodeid"]!)).ToList();
            if (signals.Count == 0) throw new UserError("Nessuno dei segnali selezionati è presente nel file");
        }
        if (signals.Count == 0) throw new UserError("Il file non contiene segnali leggibili");
        if (format is "das" or "rbf" && signals.Count > MaxLegacySignals)
            throw new UserError($"{Targets[format]}: selezionare al massimo {MaxLegacySignals} segnali (selezionati {signals.Count})");
        var start = Bound(data.Get("start_ms"));
        var end = Bound(data.Get("end_ms"));
        if (start != null && end != null && end < start) throw new UserError("L'intervallo deve avere una fine non precedente all'inizio");
        long? total = EstimateTotal(descriptor, signals.Count, available.Count, start, end);
        var estimated = start != null || end != null || signals.Count != available.Count;
        lock (gate)
        {
            if (closed) throw new UserError("Servizio di conversione arrestato");
            if (jobs.Values.Any(j => (string?)j["state"] == "running")) throw new UserError("Una conversione è già in corso: attendere il completamento o annullarla");
            var ident = Convert.ToHexString(RandomNumberGenerator.GetBytes(12)).ToLowerInvariant();
            while (order.Count > 15) { jobs.Remove(order[0]); order.RemoveAt(0); }
            jobs[ident] = new Dictionary<string, object?>
            {
                ["id"] = ident, ["state"] = "running", ["phase"] = "read", ["name"] = name, ["source"] = source,
                ["source_format"] = descriptor.GetValueOrDefault("format"), ["format"] = format, ["format_label"] = Targets[format],
                ["signal_count"] = (long)signals.Count, ["rows_read"] = 0L, ["rows_total"] = total, ["rows_total_estimated"] = estimated, ["percent"] = total != null ? 0.0 : null, ["files"] = new List<object?>(),
                ["directory"] = directory(), ["started_at"] = Values.PythonIso(DateTime.UtcNow),
            };
            order.Add(ident);
            var cancel = running = new CancellationTokenSource();
            var job = new Job(ident, library, path, name!, format, signals, start, end, total, meta);
            new Thread(() => Run(job, cancel.Token)) { IsBackground = true, Name = "recording-conversion" }.Start();
            return Status(ident);
        }
    }

    // ---- primary captures (.sac) --------------------------------------------------------------------------------------------

    /// <summary>Folder of the primary captures: ".primary" next to the "Conversioni" folder of the recordings.</summary>
    string PrimaryDirectory() => Path.Combine(Path.GetDirectoryName(Path.GetFullPath(directory()))!, ".primary");

    /// <summary>Captures outside ".primary" chosen by the operator (copied from another PC or disk); only these paths can be exported besides the folder.</summary>
    string ExternalListPath() => Path.Combine(PrimaryDirectory(), "origini_esterne.json");

    List<string> ExternalPrimary()
    {
        try
        {
            if (!File.Exists(ExternalListPath())) return new();
            return (JsonNode.Parse(File.ReadAllText(ExternalListPath())) as JsonArray)?.Select(n => n?.GetValue<string>() ?? "").Where(PathIsPrimary).ToList() ?? new();
        }
        catch (Exception exc) when (exc is IOException or JsonException or InvalidOperationException) { return new(); }
    }

    static bool PathIsPrimary(string path) => path.Length > 0 && Path.IsPathRooted(path) && PrimaryExport.IsPrimaryName(Path.GetFileName(path)) && File.Exists(path);

    /// <summary>Registers a capture chosen by path (not uploaded: captures can be tens of GB) and returns its list entry.</summary>
    public Dictionary<string, object?> AddPrimary(string? path)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new UserError("Percorso dell'archivio primario mancante");
        var full = Path.GetFullPath(path.Trim().Trim('"'));
        if (!PrimaryExport.IsPrimaryName(Path.GetFileName(full))) throw new UserError("Selezionare un archivio primario (.sac o .sac.part)");
        if (!File.Exists(full)) throw new UserError("Archivio primario non trovato: " + full);
        var inside = string.Equals(Path.GetDirectoryName(full), PrimaryDirectory(), StringComparison.OrdinalIgnoreCase);
        lock (gate)
        {
            if (!inside)
            {
                var list = ExternalPrimary();
                if (!list.Contains(full, StringComparer.OrdinalIgnoreCase))
                {
                    list.Add(full);
                    Directory.CreateDirectory(PrimaryDirectory());
                    Py.AtomicPlain(ExternalListPath(), list.TakeLast(50).Cast<object?>().ToList());
                }
            }
        }
        var entry = PrimaryExport.Describe(full);
        if (!inside) { entry["name"] = full; entry["external"] = true; }
        return entry;
    }

    /// <summary>The primary captures that can be exported, newest first (header information only).</summary>
    public List<object?> PrimaryFiles(int limit = 200)
    {
        var folder = PrimaryDirectory();
        var result = new List<object?>();
        if (Directory.Exists(folder))
            result.AddRange(new DirectoryInfo(folder).EnumerateFiles("*.sac*").Where(f => PrimaryExport.IsPrimaryName(f.Name)).OrderByDescending(f => f.LastWriteTimeUtc)
                .Take(limit).Select(f => (object?)PrimaryExport.Describe(f.FullName)));
        foreach (var external in ExternalPrimary())
        {
            var entry = PrimaryExport.Describe(external);
            entry["name"] = external; entry["external"] = true;
            result.Add(entry);
        }
        return result;
    }

    sealed record PrimaryJob(string Ident, string Path, string Name, string Format, List<string>? Signals, double? Start, double? End, double SplitMinutes);

    Dictionary<string, object?> StartPrimary(JsonNode? data)
    {
        var name = data.Get("name").Str();
        string path;
        if (PrimaryExport.IsPrimaryName(name)) path = Path.Combine(PrimaryDirectory(), name!);
        else if (name != null && ExternalPrimary().Contains(name, StringComparer.OrdinalIgnoreCase)) path = name;
        else throw new UserError("Selezionare un archivio primario (.sac)");
        if (!File.Exists(path)) throw new UserError("Archivio primario non trovato");
        name = Path.GetFileName(path);
        var format = data.Get("format").Str() ?? "";
        if (!Targets.ContainsKey(format)) throw new UserError("Formato di destinazione non valido: scegliere CSV, Parquet, FDA DAS4 o RBF");
        var described = PrimaryExport.Describe(path);
        if (described.GetValueOrDefault("read_error") is string unreadable) throw new UserError(unreadable);
        var available = ((List<object?>)described["signals"]!).OfType<Dictionary<string, object?>>().Select(s => (string)s["nodeid"]!).ToList();
        List<string>? signals = null;
        if (data.Get("signal_ids") is JsonArray list && list.Count > 0)
        {
            var wanted = list.Select(n => RowValues.Str(Py.Plain(n))).ToHashSet();
            signals = available.Where(wanted.Contains).ToList();
            if (signals.Count == 0) throw new UserError("Nessuno dei segnali selezionati è presente nell'archivio");
        }
        var count = signals?.Count ?? available.Count;
        if (format is "das" or "rbf" && count > MaxLegacySignals) throw new UserError($"{Targets[format]}: selezionare al massimo {MaxLegacySignals} segnali (selezionati {count})");
        var start = Bound(data.Get("start_ms"));
        var end = Bound(data.Get("end_ms"));
        if (start != null && end != null && end < start) throw new UserError("L'intervallo deve avere una fine non precedente all'inizio");
        var split = data.Get("split_minutes").IsNull() ? PrimaryExport.DefaultSplitMinutes : data.Get("split_minutes").Float() ?? double.NaN;
        if (!double.IsFinite(split) || split < 0 || split > PrimaryExport.MaxSplitMinutes) throw new UserError("Durata dei file non valida: da 0 (un solo file) a 1440 minuti");
        lock (gate)
        {
            if (closed) throw new UserError("Servizio di conversione arrestato");
            if (jobs.Values.Any(j => (string?)j["state"] == "running")) throw new UserError("Una conversione è già in corso: attendere il completamento o annullarla");
            var ident = Convert.ToHexString(RandomNumberGenerator.GetBytes(12)).ToLowerInvariant();
            while (order.Count > 15) { jobs.Remove(order[0]); order.RemoveAt(0); }
            jobs[ident] = new Dictionary<string, object?>
            {
                ["split_minutes"] = split, ["id"] = ident, ["state"] = "running", ["phase"] = "read", ["name"] = name, ["source"] = "primary", ["source_format"] = "sac",
                ["format"] = format, ["format_label"] = Targets[format], ["signal_count"] = (long)count, ["rows_read"] = 0L, ["rows_total"] = null,
                ["files"] = new List<object?>(), ["directory"] = directory(), ["started_at"] = Values.PythonIso(DateTime.UtcNow), ["warning"] = null,
            };
            order.Add(ident);
            var cancel = running = new CancellationTokenSource();
            var job = new PrimaryJob(ident, path, name!, format, signals, start, end, split);
            new Thread(() => RunPrimary(job, cancel.Token)) { IsBackground = true, Name = "primary-export" }.Start();
            return Status(ident);
        }
    }

    void RunPrimary(PrimaryJob job, CancellationToken cancel)
    {
        try
        {
            var result = PrimaryExport.Export(job.Path, job.Format, directory(), Config.Defaults.Version, job.Signals, job.Start, job.End,
                rows => Update(job.Ident, ("rows_read", rows)), cancel, job.SplitMinutes);
            Update(job.Ident, ("state", "ready"), ("phase", "done"), ("rows_read", result.Samples), ("files", result.Files.Cast<object?>().ToList()),
                ("warning", result.Warning), ("finished_at", Values.PythonIso(DateTime.UtcNow)));
            Log.Info($"Esportazione archivio primario {job.Name} -> {job.Format}: {result.Samples} campioni, {result.Files.Count} file" + (result.Warning != null ? " (" + result.Warning + ")" : ""));
        }
        catch (Exception exc)
        {
            var cancelled = exc is OperationCanceledException;
            Update(job.Ident, ("state", cancelled ? "cancelled" : "error"), ("error", cancelled ? "Conversione annullata" : Http.Map(exc).message));
            if (!cancelled) Log.Warning($"Esportazione archivio primario {job.Name} -> {job.Format} non riuscita: {exc.Message}");
        }
        finally { lock (gate) running = null; }
    }

    static double? Bound(JsonNode? value)
    {
        if (value.IsNull()) return null;
        var number = value.Float();
        if (number is not double d || !double.IsFinite(d)) throw new UserError("Intervallo temporale non valido");
        return d;
    }

    public Dictionary<string, object?> Status(string ident)
    {
        lock (gate)
        {
            if (!jobs.TryGetValue(ident, out var job)) throw new UserError("Conversione non trovata");
            var copy = new Dictionary<string, object?>(job);
            copy["files"] = ((List<object?>)job["files"]!).ToList();
            return copy;
        }
    }

    public Dictionary<string, object?> Cancel(string ident)
    {
        lock (gate)
        {
            if (!jobs.TryGetValue(ident, out var job)) throw new UserError("Conversione non trovata");
            if ((string?)job["state"] == "running") running?.Cancel();
        }
        return Status(ident);
    }

    public void Close()
    {
        lock (gate) { closed = true; running?.Cancel(); }
    }

    /// <summary>
    /// Rows the job is expected to read: exact for the whole file; for a subset of signals or an interval the file total is scaled by the share of signals and of the time span
    /// (a close estimate for regular sampling, only used for the percentage, which is capped below 100 until the job ends).
    /// </summary>
    public static long? EstimateTotal(Dictionary<string, object?> descriptor, int selected, int available, double? start, double? end)
    {
        if (RowValues.Float(descriptor.GetValueOrDefault("rows_written")) is not double rows || rows <= 0 || available <= 0) return null;
        var share = (double)selected / available;
        if (start != null || end != null)
        {
            if (RowValues.Float(descriptor.GetValueOrDefault("start_ms")) is not double first || RowValues.Float(descriptor.GetValueOrDefault("end_ms")) is not double last || last <= first) return null;
            var from = Math.Max(first, start ?? first);
            var to = Math.Min(last, end ?? last);
            share *= Math.Clamp((to - from) / (last - first), 0, 1);
        }
        return Math.Max(1, (long)Math.Round(rows * share));
    }

    /// <summary>Overall completion 0..100 of a running job: CSV and Parquet only read; FDA and RBF read (40%), stage the Parquet original (30%) and write the frames (30%).</summary>
    public static double? Percent(string format, long? expected, string stage, long count)
    {
        if (expected is not long total || total <= 0) return null;
        var done = Math.Min(1.0, (double)count / total);
        var (offset, weight) = format is "csv" or "parquet" ? (0.0, 1.0) : stage switch { "read" => (0.0, 0.4), "stage" => (0.4, 0.3), _ => (0.7, 0.3) };
        return Math.Min(99.0, Math.Round((offset + weight * done) * 100, 1));
    }

    void Update(string ident, params (string key, object? value)[] values)
    {
        lock (gate) foreach (var (k, v) in values) jobs[ident][k] = v;
    }

    void Run(Job job, CancellationToken cancel)
    {
        var output = directory();
        string? work = null;
        var written = new List<string>();
        try
        {
            Directory.CreateDirectory(output);
            var stem = SafeStem(Path.GetFileNameWithoutExtension(job.Path)) + "_" + DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
            var stats = new Stats();
            var rows = Selected(job, stats, cancel);
            List<Dictionary<string, object?>> files;
            if (job.Format is "csv" or "parquet")
            {
                var final = Unique(Path.Combine(output, stem + "." + job.Format));
                written.Add(final + ".part");
                Write(rows, final + ".part", job.Format, stats);
                File.Move(final + ".part", final);
                written.Add(final);
                Py.AtomicPlain(RecordingLibrary.MetadataPath(final), Sidecar(job, stats, job.Format));
                files = [Entry(final, stats.Rows)];
            }
            else
            {
                work = Path.Combine(output, ".conversione_" + job.Ident[..8]);
                Directory.CreateDirectory(work);
                var original = Path.Combine(work, stem + ".parquet");
                Write(rows, original + ".part", "parquet", stats);
                File.Move(original + ".part", original);
                Py.AtomicPlain(RecordingLibrary.MetadataPath(original), Sidecar(job, stats, "parquet"));
                Update(job.Ident, ("phase", "stage"), ("rows_read", stats.Rows), ("percent", Percent(job.Format, job.Total, "stage", 0)));
                cancel.ThrowIfCancellationRequested();
                var nodes = job.Signals.Select(Node).ToList();
                // The exporter reads the Parquet original (stage) and then writes the frames (write): both counted in rows of the original.
                var total = stats.Rows;
                void Progress(string stage, long count)
                {
                    cancel.ThrowIfCancellationRequested();
                    Update(job.Ident, ("phase", stage), ("percent", Percent(job.Format, total, stage, count)));
                }
                var parts = job.Format == "das" ? FdaExporter.Export(original, nodes, 0, Progress) : RbfExporter.Export(original, nodes, 0, Progress);
                files = new List<Dictionary<string, object?>>();
                foreach (var part in parts)
                {
                    var produced = Path.Combine(work, (string)part["name"]!);
                    var final = Unique(Path.Combine(output, (string)part["name"]!));
                    var sidecar = RecordingLibrary.MetadataPath(produced);
                    if (File.Exists(sidecar)) File.WriteAllText(RecordingLibrary.MetadataPath(final), Retarget(File.ReadAllText(sidecar), job));
                    File.Move(produced, final);
                    written.Add(final);
                    files.Add(Entry(final, part["rows"] is long r ? r : 0));
                }
            }
            Update(job.Ident, ("state", "ready"), ("phase", "done"), ("percent", 100.0), ("rows_read", stats.Rows), ("files", files.Cast<object?>().ToList()),
                ("finished_at", Values.PythonIso(DateTime.UtcNow)));
            Log.Info($"Conversione {job.Name} -> {job.Format}: {stats.Rows} campioni, {files.Count} file");
        }
        catch (Exception exc)
        {
            foreach (var file in written)
            {
                try { File.Delete(file); File.Delete(RecordingLibrary.MetadataPath(file)); } catch (Exception) { }
            }
            var cancelled = exc is OperationCanceledException;
            Update(job.Ident, ("state", cancelled ? "cancelled" : "error"), ("error", cancelled ? "Conversione annullata" : Http.Map(exc).message));
            if (!cancelled) Log.Warning($"Conversione {job.Name} -> {job.Format} non riuscita: {exc.Message}");
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

    sealed class Stats
    {
        public long Rows;
        public double First = double.PositiveInfinity, Last = double.NegativeInfinity;
        public double? SampleMs;
    }

    /// <summary>Original rows of the selection; progress every 64k rows, cancellation between rows.</summary>
    IEnumerable<Row> Selected(Job job, Stats stats, CancellationToken cancel)
    {
        var ids = job.Signals.Select(s => (string)s["nodeid"]!).ToHashSet();
        foreach (var row in job.Library.IterRows(job.Path, ids, job.Start, job.End))
        {
            cancel.ThrowIfCancellationRequested();
            if (!ids.Contains(RowValues.StrOr(row.Value("node_id")))) continue;
            if (row.Value("status") is string status && status.EndsWith("_Missing", StringComparison.Ordinal)) continue;
            var t = RowValues.Finite(row.Value("timestamp_ms"));
            if (t == null) throw new UserError("Timestamp mancante o non valido nel file di origine");
            if (job.Start != null && t < job.Start || job.End != null && t > job.End) continue;
            if (RowValues.Finite(row.Value("sample_ms")) is > 0 and var period) stats.SampleMs = stats.SampleMs is double s ? Math.Min(s, period) : period;
            stats.First = Math.Min(stats.First, t.Value);
            stats.Last = Math.Max(stats.Last, t.Value);
            stats.Rows++;
            if (stats.Rows % 65536 == 0) Update(job.Ident, ("rows_read", stats.Rows), ("percent", Percent(job.Format, job.Total, "read", stats.Rows)));
            yield return row;
        }
    }

    static void Write(IEnumerable<Row> rows, string path, string format, Stats stats)
    {
        try { Export.ExportRows(rows, path, format); }
        catch (UserError) when (stats.Rows == 0)
        {
            throw new UserError("Nessun campione da convertire nella selezione (segnali e intervallo)");
        }
    }

    Dictionary<string, object?> Sidecar(Job job, Stats stats, string format)
    {
        var meta = new Dictionary<string, object?>();
        foreach (var key in new[] { "endpoint", "source_protocol", "session_name", "machine", "operator", "description", "timestamp_order" })
            if (job.Metadata[key].Truthy()) meta[key] = Py.Plain(job.Metadata[key]);
        var sample = stats.SampleMs ?? job.Metadata["initial_sample_ms"].Float();
        meta["format"] = format;
        meta["storage"] = new Dictionary<string, object?> { ["format"] = format, ["label"] = format == "csv" ? "CSV" : "Apache Parquet", ["lossless"] = true };
        meta["state"] = "complete";
        meta["converted_from"] = job.Name;
        meta["converted_at"] = Values.PythonIso(DateTime.UtcNow);
        meta["signals"] = job.Signals.Select(s => (object?)new Dictionary<string, object?>
        {
            ["nodeid"] = s["nodeid"], ["display_name"] = s.GetValueOrDefault("display_name") ?? s["nodeid"], ["unit"] = s.GetValueOrDefault("unit") ?? "",
        }).ToList();
        meta["rows_written"] = stats.Rows;
        if (sample is double ms && ms > 0) meta["initial_sample_ms"] = ms;
        if (stats.Rows > 0)
        {
            meta["start_ms"] = stats.First; meta["end_ms"] = stats.Last;
            meta["started_at"] = Values.IsoFromMs(stats.First);
            meta["ended_at"] = Values.IsoFromMs(stats.Last);
            meta["duration_seconds"] = (stats.Last - stats.First) / 1000;
        }
        return meta;
    }

    /// <summary>FDA/RBF sidecar: the intermediate Parquet is deleted, so the file refers to its real origin.</summary>
    static string Retarget(string json, Job job)
    {
        var node = JsonNode.Parse(json) as JsonObject ?? new JsonObject();
        node.Remove("original_archive");
        node["converted_from"] = job.Name;
        node["converted_at"] = Values.PythonIso(DateTime.UtcNow);
        return node.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
    }

    internal static NodeSpec Node(Dictionary<string, object?> signal)
    {
        var raw = JsonSerializer.SerializeToElement(new Dictionary<string, object?>
        {
            ["nodeid"] = signal["nodeid"], ["display_name"] = signal.GetValueOrDefault("display_name") ?? signal["nodeid"], ["unit"] = signal.GetValueOrDefault("unit") ?? "",
        });
        return NodeSpec.FromJson(raw);
    }

    Dictionary<string, object?> Entry(string path, long rows) => new()
    {
        ["name"] = Folder + "/" + Path.GetFileName(path), ["file"] = Path.GetFileName(path), ["path"] = path,
        ["bytes"] = new FileInfo(path).Length, ["rows"] = rows, ["format"] = Path.GetExtension(path).TrimStart('.').ToLowerInvariant(),
    };

    internal static string SafeStem(string stem)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var clean = new string(stem.Select(c => invalid.Contains(c) || char.IsControl(c) ? '_' : c).ToArray()).Trim().TrimEnd('.');
        if (clean.Length > 80) clean = clean[..80];
        return clean.Length == 0 ? "Conversione" : clean;
    }

    internal static string Unique(string path)
    {
        if (!File.Exists(path)) return path;
        var folder = Path.GetDirectoryName(path)!;
        var stem = Path.GetFileNameWithoutExtension(path);
        var ext = Path.GetExtension(path);
        for (var i = 2; ; i++)
        {
            var candidate = Path.Combine(folder, $"{stem}_{i}{ext}");
            if (!File.Exists(candidate)) return candidate;
        }
    }
}
