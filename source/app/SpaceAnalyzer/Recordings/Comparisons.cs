using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// Durable, lossless comparisons on a common time axis (recording_compare.py).
/// Inputs stay untouched; every file has its own signal namespace.
/// </summary>
public sealed class Comparisons(string directory)
{
    public const int MaxFiles = 32;
    public const int MaxSignals = 2048;

    readonly object gate = new();
    readonly Dictionary<string, Dictionary<string, object?>> jobs = new();
    readonly List<string> order = new();
    CancellationTokenSource? running;
    bool closed;

    public static List<string> Fingerprint(string path)
    {
        var result = new List<string>();
        foreach (var candidate in new[] { path, Path.ChangeExtension(path, ".metadata.json"), Path.ChangeExtension(path, ".json") })
        {
            var info = new FileInfo(candidate);
            if (info.Exists) result.Add(candidate + "|" + info.Length + "|" + info.LastWriteTimeUtc.Ticks);
        }
        return result;
    }

    public Dictionary<string, object?> Start(JsonNode? data, Func<string, RecordingLibrary> resolver)
    {
        if (data.Get("files") is not JsonArray items || items.Count < 2 || items.Count > MaxFiles) throw new UserError("Selezionare da 2 a 32 file per il confronto");
        var mode = data.Get("mode") == null ? "absolute" : data.Get("mode").Str();
        var coverage = data.Get("coverage") == null ? "union" : data.Get("coverage").Str();
        if (mode is not ("absolute" or "starts") || coverage is not ("union" or "intersection")) throw new UserError("Modalità di sincronizzazione non valida");
        var files = new List<Dictionary<string, object?>>();
        var seen = new HashSet<string>();
        foreach (var item in items)
        {
            if (item is not JsonObject o) throw new UserError("Selezione file non valida");
            var source = o["source"] == null ? "repository" : o["source"].Str() ?? "";
            var library = resolver(source);
            var path = library.PathOf(o["name"].Str());
            if (!seen.Add(path)) throw new UserError("File duplicato nella selezione");
            var offset = o["offset_ms"] == null ? 0 : o["offset_ms"].Float();
            if (offset is not double off || !double.IsFinite(off) || Math.Abs(off) > 315576000000) throw new UserError("Correzione orario non valida");
            var meta = library.Metadata(path);
            if (meta["state"].Str() is "recording" or "stopping") throw new UserError("Attendere Stop prima di confrontare il file");
            files.Add(new()
            {
                ["path"] = path, ["name"] = o["name"].Str(), ["source"] = source, ["offset_ms"] = off,
                ["format"] = Path.GetExtension(path)[1..], ["display_name"] = meta["display_name"].Truthy() ? RowValues.Str(Py.Plain(meta["display_name"])) : Path.GetFileName(path),
            });
        }
        lock (gate)
        {
            if (closed) throw new UserError("Servizio confronti arrestato");
            if (jobs.Values.Any(j => (string?)j["state"] == "running")) throw new UserError("Un confronto è già in preparazione; attendere il completamento");
            Directory.CreateDirectory(directory);
            var ident = Convert.ToHexString(RandomNumberGenerator.GetBytes(12)).ToLowerInvariant();
            var output = Path.Combine(directory, "Confronto_" + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss") + "_" + ident[..8] + ".parquet");
            while (order.Count > 15) { jobs.Remove(order[0]); order.RemoveAt(0); }
            var job = new Dictionary<string, object?> { ["id"] = ident, ["state"] = "running", ["phase"] = "scan", ["file_count"] = (long)files.Count, ["rows_written"] = 0L };
            jobs[ident] = job;
            order.Add(ident);
            var cancel = running = new CancellationTokenSource();
            var request = new Job(files, output, mode!, coverage!);
            new Thread(() => Run(ident, request, cancel.Token)) { IsBackground = true, Name = "recording-comparison" }.Start();
            return Status(ident);
        }
    }

    sealed record Job(List<Dictionary<string, object?>> Files, string Output, string Mode, string Coverage);

    void Run(string ident, Job job, CancellationToken cancel)
    {
        try
        {
            var recording = Build(job, state => { lock (gate) foreach (var (k, v) in state) jobs[ident][k] = v; }, cancel);
            lock (gate) { jobs[ident]["state"] = "ready"; jobs[ident]["recording"] = recording; }
        }
        catch (Exception exc)
        {
            lock (gate) { jobs[ident]["state"] = "error"; jobs[ident]["error"] = exc.Message; }
        }
        finally
        {
            lock (gate) running = null;
        }
    }

    static Dictionary<string, object?> Build(Job job, Action<Dictionary<string, object?>> update, CancellationToken cancel)
    {
        var output = job.Output;
        var partial = Path.ChangeExtension(output, ".parquet.part");
        var scans = new List<Dictionary<string, object?>>();
        var snapshots = new List<List<string>>();
        try
        {
            for (var index = 0; index < job.Files.Count; index++)
            {
                var item = job.Files[index];
                var path = Path.GetFullPath((string)item["path"]!);
                var lib = new RecordingLibrary(Path.GetDirectoryName(path)!);
                lib.PathOf(Path.GetFileName(path));
                if (lib.Metadata(path)["state"].Str() is "recording" or "stopping") throw new UserError("Attendere Stop per confrontare una registrazione ancora in corso");
                snapshots.Add(Fingerprint(path));
                var display = (string)item["display_name"]!;
                update(new() { ["phase"] = "scan", ["file_index"] = (long)index + 1, ["file_count"] = (long)job.Files.Count, ["file_name"] = display, ["rows_written"] = 0L });
                Dictionary<string, object?> scan;
                try
                {
                    var i = index;
                    scan = lib.Scan(path, null, n =>
                    {
                        cancel.ThrowIfCancellationRequested();
                        update(new() { ["phase"] = "scan", ["file_index"] = (long)i + 1, ["file_count"] = (long)job.Files.Count, ["file_name"] = display, ["rows_scanned"] = n });
                    });
                }
                catch (Exception exc) when (exc is UserError or IOException) { throw new UserError(display + ": " + exc.Message); }
                var offset = (double)item["offset_ms"]!;
                if (job.Mode == "starts" && scans.Count > 0) offset += (double)scans[0]["start_ms"]! - (double)scan["start_ms"]!;
                scan["offset_ms"] = offset;
                scan["path"] = path;
                scan["display_name"] = display;
                scan["source"] = item["source"];
                scan["original_name"] = item["name"] ?? Path.GetFileName(path);
                scan["format"] = item["format"];
                scans.Add(scan);
            }
            if (scans.Sum(s => ((List<object?>)s["signals"]!).Count) > MaxSignals) throw new UserError("Confronto: massimo 2048 segnali complessivi; selezionare meno file");
            var starts = scans.Select(s => (double)s["start_ms"]! + (double)s["offset_ms"]!).ToList();
            var ends = scans.Select(s => (double)s["end_ms"]! + (double)s["offset_ms"]!).ToList();
            var start = job.Coverage == "intersection" ? starts.Max() : starts.Min();
            var end = job.Coverage == "intersection" ? ends.Min() : ends.Max();
            if (start > end) throw new UserError("I file non hanno un intervallo temporale comune. Scegliere “Intera estensione” oppure correggere gli orari.");
            var columns = Export.Columns.Concat(new[] { "original_timestamp_ms", "original_node_id", "source_file" }).ToArray();
            var floats = new HashSet<string> { "timestamp_ms", "elapsed_ms", "value_numeric", "original_timestamp_ms", "acquisition_timestamp_ms" };
            var integers = new HashSet<string> { "cycle", "sample_ms", "status_code" };
            var kinds = columns.Select(c => floats.Contains(c) ? Export.Kind.Double : integers.Contains(c) ? Export.Kind.Long : c == "good" ? Export.Kind.Bool : Export.Kind.String).ToArray();
            var signals = new List<object?>();
            long total = 0;
            var totalRows = scans.Sum(s => (long)s["rows_written"]!);
            update(new() { ["phase"] = "write", ["file_index"] = 1L, ["file_count"] = (long)job.Files.Count, ["rows_written"] = 0L, ["total_rows"] = totalRows });
            using (var writer = new Export.TableWriter(partial, columns, kinds, Export.Parquet with { RowGroupRows = 32768 }))
            {
                for (var index = 0; index < scans.Count; index++)
                {
                    var scan = scans[index];
                    var path = (string)scan["path"]!;
                    var display = (string)scan["display_name"]!;
                    if (!Fingerprint(path).SequenceEqual(snapshots[index])) throw new UserError(display + ": il file è cambiato durante la preparazione; ripetere il confronto");
                    var ns = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(index + "\0" + path))).ToLowerInvariant()[..12];
                    var ids = new Dictionary<string, string>();
                    var labels = new Dictionary<string, string>();
                    var offset = (double)scan["offset_ms"]!;
                    foreach (Dictionary<string, object?> original in (List<object?>)scan["signals"]!)
                    {
                        var oid = (string)original["nodeid"]!;
                        var nid = "cmp:" + ns + ":" + oid;
                        ids[oid] = nid;
                        labels[oid] = original["display_name"] + " · " + display;
                        var signal = new Dictionary<string, object?>(original)
                        {
                            ["nodeid"] = nid, ["display_name"] = labels[oid], ["original_node_id"] = oid, ["source_file"] = display,
                            ["time_offset_ms"] = offset, ["start_ms"] = (double)original["start_ms"]! + offset, ["end_ms"] = (double)original["end_ms"]! + offset,
                        };
                        signals.Add(signal);
                    }
                    var library = new RecordingLibrary(Path.GetDirectoryName(path)!);
                    var pending = 0;
                    foreach (var row in library.IterRows(path))
                    {
                        var originalTime = RowValues.Finite(row.Value("timestamp_ms"));
                        var oid = RowValues.StrOr(row.Value("node_id"));
                        if (originalTime == null || !ids.ContainsKey(oid)) throw new UserError(display + ": dati cambiati o timestamp non valido");
                        var t = originalTime.Value + offset;
                        if (!(start <= t && t <= end)) continue;
                        var clean = new object?[columns.Length];
                        for (var c = 0; c < Export.Columns.Length; c++) clean[c] = Export.Clean(row.Value(columns[c]), kinds[c], finiteFloats: true);
                        var quality = RowValues.Truthy(row.Value("quality")) ? RowValues.Str(row.Value("quality")) : RowValues.Truth(row.TryGetValue("good", out var g) ? g : true) ? "Good" : "Bad";
                        void Set(string key, object? value) => clean[Array.IndexOf(columns, key)] = value;
                        Set("timestamp_ms", t);
                        Set("timestamp_utc", Values.IsoFromMs(t));
                        Set("elapsed_ms", t - start);
                        Set("node_id", ids[oid]);
                        Set("signal_name", labels[oid]);
                        Set("quality", quality);
                        Set("good", quality == "Good");
                        Set("original_timestamp_ms", originalTime.Value);
                        Set("original_node_id", oid);
                        Set("source_file", display);
                        writer.Add(clean);
                        total++;
                        if (++pending >= 32768)
                        {
                            pending = 0;
                            cancel.ThrowIfCancellationRequested();
                            update(new() { ["phase"] = "write", ["file_index"] = (long)index + 1, ["file_count"] = (long)job.Files.Count, ["file_name"] = display, ["rows_written"] = total, ["total_rows"] = totalRows });
                        }
                    }
                    if (!Fingerprint(path).SequenceEqual(snapshots[index])) throw new UserError(display + ": il file è cambiato durante la lettura; confronto annullato");
                }
            }
            if (total == 0) throw new UserError("Nessun campione nell’intervallo selezionato");
            var labelsFormats = scans.Select(s => ((string)s["format"]!).ToUpperInvariant()).Distinct();
            var storage = Export.Parquet.ToDictionary();
            storage["label"] = "Confronto · " + string.Join(" + ", labelsFormats);
            var metadata = new Dictionary<string, object?>
            {
                ["display_name"] = "Confronto · " + scans.Count + " file",
                ["timestamp_order"] = scans.Any(s => ((List<object?>)s["signals"]!).Any(x => (long)((Dictionary<string, object?>)x!)["out_of_order_rows"]! > 0)) ? "arrival" : "per-signal",
                ["signals"] = signals, ["rows_written"] = total,
                ["started_at"] = Values.IsoFromMs(start), ["ended_at"] = Values.IsoFromMs(end), ["duration_seconds"] = (end - start) / 1000,
                ["storage"] = storage, ["state"] = "stopped",
                ["comparison"] = new Dictionary<string, object?>
                {
                    ["schema"] = 1L, ["mode"] = job.Mode, ["coverage"] = job.Coverage,
                    ["files"] = scans.Select(s => (object?)s.Where(k => k.Key is not ("signals" or "path")).ToDictionary(k => k.Key, k => k.Value)).ToList(),
                    ["resampled"] = false, ["time_basis"] = "timestamp_ms originale + correzione esplicita",
                    ["common_seconds"] = Math.Max(0, (ends.Min() - starts.Max()) / 1000),
                },
                ["view_source_note"] = "Segnali distinti per file; campionamenti originali. " +
                    (job.Mode == "starts" ? "Inizi allineati al primo file: orari traslati per il confronto." : "Orari assoluti, con le eventuali correzioni indicate."),
            };
            Py.AtomicPlain(Path.ChangeExtension(output, ".metadata.json"), metadata, indent: false);
            File.Move(partial, output, true);
            return new RecordingLibrary(Path.GetDirectoryName(output)!).Describe(Path.GetFileName(output), source: "local");
        }
        catch
        {
            foreach (var p in new[] { partial, output, Path.ChangeExtension(output, ".metadata.json") })
                try { File.Delete(p); } catch (IOException) { }
            throw;
        }
    }

    public Dictionary<string, object?> Status(string ident)
    {
        lock (gate)
        {
            if (!jobs.TryGetValue(ident, out var job)) throw new UserError("Preparazione confronto non trovata");
            return new Dictionary<string, object?>(job);
        }
    }

    public void Close()
    {
        lock (gate) { closed = true; running?.Cancel(); }
    }
}
