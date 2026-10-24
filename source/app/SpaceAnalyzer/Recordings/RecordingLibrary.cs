using System.Collections.Concurrent;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>Read-only access to the recordings of a folder (recording_reader.py).</summary>
public sealed class RecordingLibrary
{
    public static readonly Dictionary<string, string> Supported = new(StringComparer.OrdinalIgnoreCase)
    {
        [".csv"] = "csv", [".parquet"] = "parquet", [".das"] = "das", [".rbf"] = "rbf", [".dat"] = "dat", [".pda3seq"] = "dat",
    };
    public const int MaxSignals = 64;
    public const int MaxPointsPerSignal = 100_000;

    public static readonly ReplayCache Views = new();
    public static readonly ReplayCache MarkerCache = new(200, 64);
    public static readonly ReplayCache Descriptions = new(256, 256);
    static readonly ConcurrentDictionary<string, Dictionary<string, object?>> Summaries = new();
    static readonly ConcurrentDictionary<string, string> OriginalHashes = new();

    public string Directory { get; }

    public RecordingLibrary(string directory) => Directory = Path.GetFullPath(directory);

    static string Ext(string path) => Path.GetExtension(path).ToLowerInvariant();

    public string Relative(string path) => Path.GetRelativePath(Directory, path).Replace('\\', '/');

    /// <summary>_path: a supported file inside the library folder.</summary>
    public string PathOf(string? name)
    {
        if (string.IsNullOrEmpty(name) || name.Contains('\\') || Path.IsPathRooted(name) || name.Split('/').Contains("..") || name.Contains(':'))
            throw new UserError("Nome registrazione non valido");
        var path = Path.GetFullPath(Path.Combine(Directory, name));
        var root = Directory.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!path.StartsWith(root, OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal) || !Supported.ContainsKey(Ext(path)))
            throw new UserError("Sono ammessi file CSV, Parquet, FDA DAS4, RBF v3 o iba DAT nella cartella Recordings");
        if (!File.Exists(path)) throw new UserError("Registrazione non trovata");
        return path;
    }

    public static string MetadataPath(string path) => Path.ChangeExtension(path, ".metadata.json");

    public JsonObject Metadata(string path)
    {
        var meta = MetadataPath(path);
        if (!File.Exists(meta) && Ext(path) == ".dat") meta = Path.ChangeExtension(path, ".json");
        if (!File.Exists(meta)) return new JsonObject();
        return Py.ReadObject(meta) ?? new JsonObject();
    }

    public static string Fingerprint(string path, JsonObject metadata)
    {
        var paths = new List<string> { path, MetadataPath(path) };
        if (Ext(path) == ".dat") paths.Add(Path.ChangeExtension(path, ".json"));
        if (metadata["original_archive"].Str() is { Length: > 0 } original && !original.Contains('/') && !original.Contains('\\'))
            paths.Add(Path.Combine(Path.GetDirectoryName(path)!, original));
        var sb = new StringBuilder();
        foreach (var item in paths)
        {
            var info = new FileInfo(item);
            sb.Append(item).Append('|');
            if (info.Exists) sb.Append(info.Length).Append('|').Append(info.LastWriteTimeUtc.Ticks);
            sb.Append(';');
        }
        return sb.ToString();
    }

    static string Iso(DateTime utc) => Values.PythonIso(utc);
    static string IsoSeconds(double seconds) => Values.IsoFromMs(seconds * 1000);

    public Dictionary<string, object?> Describe(string name, string? displayName = null, string source = "repository")
    {
        var path = PathOf(name);
        var key = Fingerprint(path, Metadata(path));
        var cached = Ext(path) == ".pda3seq" ? null : Descriptions.Get(key);
        if (cached == null)
        {
            cached = DescribeUncached(name);
            if (Ext(path) != ".pda3seq") Descriptions.Put(key, cached);
        }
        cached["source"] = source;
        if (displayName != null) cached["display_name"] = displayName;
        return cached;
    }

    Dictionary<string, object?> DescribeUncached(string name)
    {
        var path = PathOf(name);
        var stat = new FileInfo(path);
        var meta = Metadata(path);
        var ext = Ext(path);
        if (ext == ".pda3seq") meta = Pda3.SequenceInfo(this, path).info;
        else if (ext == ".dat" && Pda3.IsPda3(path)) meta = Pda3.Details(path, meta);
        if (ext == ".das")
        {
            var h = FdaReader.Inspect(path);
            if (!meta["signals"].Truthy())
            {
                var channels = FdaReader.ChannelMap(h);
                var copy = (JsonObject)meta.DeepClone();
                copy["signals"] = new JsonArray(channels.Select(c => (JsonNode)new JsonObject { ["nodeid"] = c.NodeId, ["display_name"] = c.DisplayName, ["unit"] = c.Unit }).ToArray());
                copy["rows_written"] = h.Frames * channels.Count;
                copy["started_at"] = Iso(DateTime.UnixEpoch.AddSeconds(h.Epoch));
                copy["storage"] = new JsonObject
                {
                    ["format"] = "das",
                    ["label"] = h.Channels.Any(c => c.Type == 4) ? "FDA DAS4 · Float32 / Int16" : "FDA DAS4 · Int16 scalato",
                    ["lossless"] = false,
                };
                meta = copy;
            }
            if (h.Frames > 0)
            {
                using var stream = File.OpenRead(path);
                var buffer = new byte[4];
                stream.Seek(h.DataOffset, SeekOrigin.Begin); stream.ReadExactly(buffer);
                var first = BitConverter.ToUInt32(buffer);
                stream.Seek(h.DataOffset + (h.Frames - 1) * h.RecordSize, SeekOrigin.Begin); stream.ReadExactly(buffer);
                var last = BitConverter.ToUInt32(buffer);
                meta = (JsonObject)meta.DeepClone();
                meta["started_at"] = IsoSeconds(h.Epoch + first / 1000.0);
                meta["ended_at"] = IsoSeconds(h.Epoch + last / 1000.0);
                meta["duration_seconds"] = ((double)last - first) / 1000;
            }
        }
        if (ext == ".rbf")
        {
            var h = RbfReader.Inspect(path);
            var mapped = RbfReader.ChannelMap(h, meta);
            meta = (JsonObject)meta.DeepClone();
            meta["signals"] = new JsonArray(mapped.Select(c => (JsonNode)new JsonObject { ["nodeid"] = c.NodeId, ["display_name"] = c.DisplayName, ["unit"] = c.Unit, ["data_type"] = c.DataType }).ToArray());
            meta["rows_written"] = h.Frames * mapped.Count;
            meta["initial_sample_ms"] = h.SampleUs / 1000.0;
            meta["storage"] = new JsonObject { ["format"] = "rbf", ["label"] = "RBF v3", ["lossless"] = false };
            if (h.Frames > 0)
            {
                meta["start_ms"] = h.StartMs; meta["end_ms"] = h.EndMs;
                meta["started_at"] = Values.IsoFromMs(h.StartMs!.Value);
                meta["ended_at"] = Values.IsoFromMs(h.EndMs!.Value);
                meta["duration_seconds"] = (h.EndMs - h.StartMs) / 1000;
            }
        }
        var storage = meta["storage"] as JsonObject ?? new JsonObject();
        if (ext is ".csv" or ".parquet" && (!meta["signals"].Truthy() || !meta["started_at"].Truthy() || meta["duration_seconds"].IsNull()))
        {
            var summary = Summary(path);
            var merged = (JsonObject)Py.ToNode(summary)!;
            foreach (var (k, v) in meta) merged[k] = v?.DeepClone();
            meta = merged;
            if (!meta["signals"].Truthy()) meta["signals"] = Py.ToNode(summary["signals"]);
            if (!meta["started_at"].Truthy() && summary["start_ms"] is double s) meta["started_at"] = Values.IsoFromMs(s);
            if (!meta["ended_at"].Truthy() && summary["end_ms"] is double e) meta["ended_at"] = Values.IsoFromMs(e);
            if (meta["duration_seconds"].IsNull()) meta["duration_seconds"] = Py.ToNode(summary["duration_seconds"]);
        }
        if (ext == ".parquet" && storage.Count == 0)
        {
            try
            {
                if (ParquetRows.KeyValues(path).TryGetValue("storage", out var text) && JsonNode.Parse(text) is JsonObject footer) storage = footer;
            }
            catch (Exception) { }
        }
        var signals = new List<object?>();
        foreach (var row in meta["signals"] as JsonArray ?? new JsonArray())
        {
            if (row is not JsonObject o || !o["nodeid"].Truthy()) continue;
            var nid = RowValues.Str(Py.Plain(o["nodeid"]));
            signals.Add(new Dictionary<string, object?>
            {
                ["nodeid"] = nid,
                ["display_name"] = o["display_name"].Truthy() ? RowValues.Str(Py.Plain(o["display_name"])) : nid,
                ["unit"] = o["unit"].Truthy() ? RowValues.Str(Py.Plain(o["unit"])) : "",
                ["calculated"] = o["calculated"].Truthy() || nid.StartsWith("calc:", StringComparison.Ordinal),
            });
        }
        var format = Supported[ext];
        object? M(string key) => Py.Plain(meta[key]);
        return new Dictionary<string, object?>
        {
            ["name"] = Relative(path),
            ["display_name"] = meta["display_name"].Truthy() ? RowValues.Str(M("display_name")) : Path.GetFileName(path),
            ["source"] = "repository",
            ["format"] = format,
            ["storage"] = Py.Plain(storage),
            ["format_label"] = storage["label"].Truthy() ? Py.Plain(storage["label"]) : format.ToUpperInvariant(),
            ["size_bytes"] = stat.Length,
            ["modified_at"] = Iso(stat.LastWriteTimeUtc),
            ["started_at"] = M("started_at"),
            ["ended_at"] = M("ended_at"),
            ["start_ms"] = M("start_ms"),
            ["end_ms"] = M("end_ms"),
            ["duration_seconds"] = M("duration_seconds"),
            ["rows_written"] = M("rows_written"),
            ["sample_ms"] = M("initial_sample_ms"),
            ["endpoint"] = meta["endpoint"].Truthy() ? M("endpoint") : "",
            ["signals"] = signals,
            ["view_source_note"] = meta.ContainsKey("view_source_note") ? M("view_source_note") : "",
            ["comparison"] = M("comparison"),
            ["session"] = new[] { "session_name", "machine", "operator", "description", "session_id", "part", "cycles_in_file" }.ToDictionary(k => k, k => M(k)),
        };
    }

    Dictionary<string, object?> Summary(string path)
    {
        var info = new FileInfo(path);
        var sidecar = new FileInfo(MetadataPath(path));
        var key = path + "|" + info.Length + "|" + info.LastWriteTimeUtc.Ticks + "|" + (sidecar.Exists ? sidecar.LastWriteTimeUtc.Ticks : 0);
        if (Summaries.TryGetValue(key, out var cached)) return (Dictionary<string, object?>)RowValues.DeepCopy(cached)!;
        IEnumerable<Row> rows = Ext(path) == ".parquet"
            ? ParquetRows.Read(path, ["timestamp_ms", "node_id", "value_numeric", "signal_name", "data_type", "sample_ms"])
            : IterRows(path);
        var result = Scan(path, rows, null, allowEmpty: true, allowUnordered: true);
        if (Summaries.Count >= 64) Summaries.Clear();
        Summaries[key] = result;
        return (Dictionary<string, object?>)RowValues.DeepCopy(result)!;
    }

    /// <summary>scan_recording of recording_compare.</summary>
    public Dictionary<string, object?> Scan(string path, IEnumerable<Row>? rows = null, Action<long>? progress = null, bool allowEmpty = false, bool allowUnordered = false)
    {
        var metadata = Metadata(path);
        allowUnordered = allowUnordered || metadata["source_protocol"].Str() is "mqtt" or "mixed" || metadata["timestamp_order"].Str() == "arrival";
        var byId = new Dictionary<string, JsonObject>();
        foreach (var s in metadata["signals"] as JsonArray ?? new JsonArray())
            if (s is JsonObject o && o["nodeid"].Truthy()) byId[RowValues.Str(Py.Plain(o["nodeid"]))] = o;
        var signals = new Dictionary<string, Dictionary<string, object?>>();
        var lastMs = new Dictionary<string, double>();
        double start = double.PositiveInfinity, end = double.NegativeInfinity;
        long count = 0;
        foreach (var row in rows ?? IterRows(path))
        {
            var nid = RowValues.StrOr(row.Value("node_id"));
            var t = RowValues.Finite(row.Value("timestamp_ms"));
            if (nid.Length == 0 || t == null) throw new UserError("Timestamp o identificativo segnale mancante/non valido");
            if (!signals.TryGetValue(nid, out var info))
            {
                byId.TryGetValue(nid, out var source);
                info = new Dictionary<string, object?>
                {
                    ["nodeid"] = nid,
                    ["display_name"] = source?["display_name"].Truthy() == true ? RowValues.Str(Py.Plain(source["display_name"])) : RowValues.StrOr(row.Value("signal_name"), nid),
                    ["unit"] = source?["unit"].Truthy() == true ? RowValues.Str(Py.Plain(source["unit"])) : "",
                    ["data_type"] = RowValues.StrOr(row.Value("data_type"), "Double"),
                    ["calculated"] = source?["calculated"].Truthy() == true || nid.StartsWith("calc:", StringComparison.Ordinal),
                    ["rows"] = 0L, ["start_ms"] = t, ["end_ms"] = t, ["sample_ms"] = null, ["time_gaps"] = 0L, ["out_of_order_rows"] = 0L,
                };
                signals[nid] = info;
            }
            double? previous = lastMs.TryGetValue(nid, out var p) ? p : null;
            if (previous != null && t < previous)
            {
                if (!allowUnordered) throw new UserError("Timestamp non ordinati per il segnale " + info["display_name"]);
                info["out_of_order_rows"] = (long)info["out_of_order_rows"]! + 1;
            }
            var sm = RowValues.Finite(row.Value("sample_ms"));
            if (sm is > 0) info["sample_ms"] = info["sample_ms"] is double cur ? Math.Min(sm.Value, cur) : sm.Value;
            if (previous != null && info["sample_ms"] is double period && period != 0 && t - previous > period * 1.5)
                info["time_gaps"] = (long)info["time_gaps"]! + 1;
            lastMs[nid] = t.Value;
            info["end_ms"] = Math.Max((double)info["end_ms"]!, t.Value);
            info["start_ms"] = Math.Min((double)info["start_ms"]!, t.Value);
            info["rows"] = (long)info["rows"]! + 1;
            start = Math.Min(start, t.Value); end = Math.Max(end, t.Value); count++;
            if (signals.Count > Comparisons.MaxSignals) throw new UserError("Troppi segnali nel file (massimo 2048)");
            if (progress != null && count % 65536 == 0) progress(count);
        }
        if (count == 0 && allowEmpty)
            return new() { ["signals"] = new List<object?>(), ["start_ms"] = null, ["end_ms"] = null, ["rows_written"] = 0L, ["duration_seconds"] = 0L };
        if (count == 0) throw new UserError("Nessun campione leggibile nella registrazione");
        return new()
        {
            ["signals"] = signals.Values.Cast<object?>().ToList(), ["start_ms"] = start, ["end_ms"] = end, ["rows_written"] = count,
            ["duration_seconds"] = (end - start) / 1000,
        };
    }

    public List<Dictionary<string, object?>> List(string source = "repository")
    {
        System.IO.Directory.CreateDirectory(Directory);
        var files = new List<Dictionary<string, object?>>();
        var entries = new DirectoryInfo(Directory).EnumerateFiles("*", SearchOption.AllDirectories)
            .Select(f => { try { return (f, mtime: f.LastWriteTimeUtc); } catch (IOException) { return (f, mtime: DateTime.MinValue); } })
            .OrderByDescending(x => x.mtime).Select(x => x.f);
        foreach (var file in entries)
        {
            var name = Relative(file.FullName);
            if (name.Split('/').Any(p => p.StartsWith(".pda3_", StringComparison.Ordinal))) continue;
            if (!Supported.TryGetValue(file.Extension.ToLowerInvariant(), out var format)) continue;
            try { files.Add(Describe(name, source: source)); }
            catch (Exception exc) when (exc is UserError or IOException or UnauthorizedAccessException or InvalidDataException or ParquetSharp.ParquetException)
            {
                long size = 0;
                try { size = file.Length; } catch (IOException) { }
                files.Add(new() { ["name"] = name, ["display_name"] = name, ["source"] = source, ["format"] = format, ["size_bytes"] = size,
                    ["signals"] = new List<object?>(), ["read_error"] = exc.Message, ["view_source_note"] = exc.Message });
            }
        }
        return files;
    }

    /// <summary>_iter_rows: original rows of every supported format.</summary>
    public IEnumerable<Row> IterRows(string path, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        switch (Ext(path))
        {
            case ".pda3seq": return Pda3.IterSequence(this, path, signalIds, startMs, endMs);
            case ".dat": return Pda3.IsPda3(path) ? Pda3.IterRows(path, Metadata(path), signalIds, startMs, endMs) : IterDatOriginal(path);
            case ".das": return FdaReader.IterRows(path, Metadata(path), signalIds, startMs, endMs);
            case ".rbf": return RbfReader.IterRows(path, Metadata(path), signalIds, startMs, endMs);
            case ".csv": return CsvRows.Read(path);
            default: return ParquetRows.Read(path, null, signalIds, startMs, endMs);
        }
    }

    public (JsonObject meta, string original, double[][] bounds, HashSet<string> ids) DatSource(string path)
    {
        var meta = Metadata(path);
        var name = meta["original_archive"].Str();
        if (string.IsNullOrEmpty(name) || name.Contains('/') || name.Contains('\\') || Path.GetFileName(name) != name || Ext(name) != ".parquet")
            throw new UserError("DAT: archivio originale associato non valido; non e un lettore DAT esterno universale");
        var folder = Path.GetDirectoryName(Path.GetFullPath(path))!;
        var original = Path.GetFullPath(Path.Combine(folder, name));
        if (Path.GetDirectoryName(original) != folder || !File.Exists(original)) throw new UserError("DAT: conservare nella stessa cartella il Parquet originale associato");
        var info = new FileInfo(original);
        var key = original + "|" + info.Length + "|" + info.LastWriteTimeUtc.Ticks;
        if (!OriginalHashes.TryGetValue(key, out var digest))
        {
            using var stream = File.OpenRead(original);
            digest = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
            if (OriginalHashes.Count >= 32) OriginalHashes.Clear();
            OriginalHashes[key] = digest;
        }
        if (digest != meta["original_sha256"].Str()) throw new UserError("DAT: checksum del Parquet originale non corrispondente");
        if (meta["original_range_keys"] is not JsonArray bounds || bounds.Count != 2) throw new UserError("DAT: intervallo originale mancante");
        var keys = new double[2][];
        for (var i = 0; i < 2; i++)
        {
            if (bounds[i] is not JsonArray k || k.Count != 3 || k.Any(v => !v.IsNumber() || !double.IsFinite(v!.GetValue<double>())))
                throw new UserError("DAT: intervallo originale non valido");
            keys[i] = k.Select(v => v!.GetValue<double>()).ToArray();
        }
        if (Compare(keys[0], keys[1]) > 0) throw new UserError("DAT: intervallo invertito");
        var ids = new HashSet<string>();
        foreach (var n in meta["signals"] as JsonArray ?? new JsonArray())
            if (n is JsonObject o && o["nodeid"].Truthy()) ids.Add(RowValues.Str(Py.Plain(o["nodeid"])));
        return (meta, original, keys, ids);
    }

    static int Compare(double[] a, double[] b)
    {
        for (var i = 0; i < 3; i++) { var c = a[i].CompareTo(b[i]); if (c != 0) return c; }
        return 0;
    }

    IEnumerable<Row> IterDatOriginal(string path)
    {
        var (_, original, bounds, ids) = DatSource(path);
        long ordinal = -1;
        foreach (var row in ParquetRows.Read(original))
        {
            ordinal++;
            if (!ids.Contains(RowValues.StrOr(row.Value("node_id")))) continue;
            var key = new[] { RowValues.Float(row.Value("timestamp_ms")) ?? double.NaN, RowValues.Truthy(row.Value("cycle")) ? (double)(long)RowValues.Float(row.Value("cycle"))! : 0, ordinal };
            if (Compare(bounds[0], key) <= 0 && Compare(key, bounds[1]) <= 0) yield return row;
        }
    }

    // ---- Display previews -------------------------------------------------

    public IEnumerable<PlotRow> PlotRows(string path, IList<string>? signalIds = null, double? start = null, double? end = null)
    {
        var wanted = signalIds == null ? null : new HashSet<string>(signalIds);
        if (Ext(path) == ".das")
        {
            foreach (var r in FdaReader.PlotRows(path, Metadata(path), wanted, start, end)) yield return r;
            yield break;
        }
        var rows = Ext(path) == ".parquet"
            ? ParquetRows.Read(path, ["timestamp_ms", "node_id", "value_numeric", "quality", "good", "status", "signal_name", "data_type", "sample_ms"], wanted, start, end)
            : IterRows(path, wanted, start, end);
        foreach (var row in rows)
        {
            var nid = RowValues.StrOr(row.Value("node_id"));
            if (nid.Length == 0 || wanted != null && !wanted.Contains(nid)) continue;
            var t = RowValues.Float(row.Value("timestamp_ms"));
            if (t is not double tt || !double.IsFinite(tt) || start != null && tt < start || end != null && tt > end) continue;
            var value = RowValues.Float(row.Value("value_numeric"));
            if (value is double v && !double.IsFinite(v)) value = null;
            var good = Str(row.TryGetValue("good", out var g) ? g : true).ToLowerInvariant() is "true" or "1" or "yes" or "good";
            var quality = RowValues.Truthy(row.Value("quality")) ? RowValues.Str(row.Value("quality"))
                : good ? "Good" : RowValues.Str(row.Value("status") ?? "").StartsWith("Uncertain", StringComparison.Ordinal) ? "Uncertain" : "Bad";
            yield return new PlotRow(nid, tt, quality == "Good" ? value : null, quality, RowValues.StrOr(row.Value("signal_name"), nid), RowValues.StrOr(row.Value("data_type"), "Double"), row.Value("sample_ms"));
        }
    }

    static string Str(object? v) => RowValues.Str(v);

    static double? FiniteParam(JsonNode? node) => node.Float() is double d && double.IsFinite(d) ? d : null;

    public Dictionary<string, object?> Load(string name, JsonNode? signalIds = null, JsonNode? startMs = null, JsonNode? endMs = null, JsonNode? maxPoints = null)
    {
        var path = PathOf(name);
        List<string>? ids = null;
        if (signalIds != null && !signalIds.IsNull())
        {
            if (signalIds is not JsonArray list || list.Count > MaxSignals) throw new UserError("Selezionare al massimo 64 segnali");
            ids = list.Select(n => RowValues.Str(Py.Plain(n))).Where(s => s.Length > 0).Distinct().ToList();
        }
        var start = startMs.IsNull() ? null : FiniteParam(startMs);
        var end = endMs.IsNull() ? null : FiniteParam(endMs);
        if (start != null && end != null && end < start) throw new UserError("L'intervallo temporale deve avere una fine non precedente all'inizio");
        int limit;
        var requested = maxPoints == null ? 25000 : maxPoints.IsNumber() ? maxPoints!.GetValue<double>() : maxPoints.IsString() && double.TryParse(maxPoints.Str(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed) ? parsed : double.NaN;
        limit = double.IsFinite(requested) ? (int)Math.Max(1000, Math.Min(MaxPointsPerSignal, Math.Truncate(requested))) : 25000;
        var meta = Metadata(path);
        var selected = ids?.Count ?? (meta["signals"] as JsonArray)?.Count ?? 0;
        limit = Math.Min(limit, Math.Max(1000, 250000 / Math.Max(1, selected)));
        var identity = Fingerprint(path, meta);
        var key = identity + "#" + (ids == null ? "*" : string.Join("\u0001", ids)) + "#" + start + "#" + end + "#" + limit;
        var seq = Ext(path) == ".pda3seq";
        if (!seq && Views.Get(key) is { } cached) return cached;
        var result = meta["comparison"].Truthy() ? LoadLegacy(path, ids, start, end, limit) : Preview(path, ids, start, end, limit);
        if (!seq && identity == Fingerprint(path, Metadata(path))) Views.Put(key, result);
        return result;
    }

    Dictionary<string, object?> Preview(string path, List<string>? ids, double? start, double? end, int limit)
    {
        var series = new Dictionary<string, PeakSeries>();
        var names = new Dictionary<string, string>();
        var types = new Dictionary<string, string>();
        long numeric = 0, rows = 0;
        double low = double.PositiveInfinity, high = double.NegativeInfinity;
        var periods = new List<double>();
        foreach (var r in PlotRows(path, ids, start, end))
        {
            if (!series.TryGetValue(r.NodeId, out var s)) series[r.NodeId] = s = new PeakSeries(limit);
            s.Add(r.T, r.Value, r.Quality);
            names[r.NodeId] = r.Name; types[r.NodeId] = r.DataType;
            numeric += r.Value != null && r.Quality == "Good" ? 1 : 0;
            rows++; low = Math.Min(low, r.T); high = Math.Max(high, r.T);
            if (periods.Count < 1000 && RowValues.Float(r.Period) is double ms && double.IsFinite(ms)) periods.Add(ms);
        }
        var meta = Metadata(path);
        var metaNames = new Dictionary<string, string>();
        foreach (var s in meta["signals"] as JsonArray ?? new JsonArray())
            if (s is JsonObject o && o["nodeid"].Truthy()) metaNames[RowValues.Str(Py.Plain(o["nodeid"]))] = o.ContainsKey("display_name") ? RowValues.Str(Py.Plain(o["display_name"])) : RowValues.Str(Py.Plain(o["nodeid"]));
        if (ids != null)
            foreach (var nid in ids)
            {
                if (!series.ContainsKey(nid)) series[nid] = new PeakSeries(limit);
                if (!names.ContainsKey(nid)) names[nid] = metaNames.TryGetValue(nid, out var n) ? n : nid;
            }
        var returned = series.ToDictionary(k => k.Key, k => k.Value.Result());
        return new()
        {
            ["name"] = Relative(path),
            ["format"] = Supported[Ext(path)],
            ["series"] = returned.ToDictionary(k => k.Key, k => (object?)k.Value),
            ["signals"] = returned.Select(k => (object?)new Dictionary<string, object?>
            {
                ["nodeid"] = k.Key, ["display_name"] = names[k.Key], ["data_type"] = types.TryGetValue(k.Key, out var t) ? t : "Double",
                ["points_original"] = series[k.Key].Count, ["points_returned"] = k.Value.Count,
            }).ToList(),
            ["rows_scanned"] = rows, ["numeric_rows"] = numeric,
            ["start_ms"] = double.IsInfinity(low) ? null : low, ["end_ms"] = double.IsInfinity(high) ? null : high,
            ["max_points_per_signal"] = limit,
            ["sample_ms"] = periods.Count > 0 ? periods.Sum() / periods.Count : null,
            ["decimated"] = returned.Any(k => series[k.Key].Count > k.Value.Count),
        };
    }

    static List<object?[]> Decimate(List<object?[]> points, int limit)
    {
        var n = points.Count;
        if (n <= limit || limit < 4) return points;
        var step = Math.Max(1, (int)Math.Ceiling(n / (double)Math.Max(1, limit / 6)));
        var result = new List<object?[]>();
        for (var offset = 0; offset < n; offset += step)
        {
            var chunk = points.GetRange(offset, Math.Min(step, n - offset));
            var picks = new SortedSet<int> { 0, chunk.Count - 1 };
            var valid = Enumerable.Range(0, chunk.Count).Where(i => chunk[i][1] != null).ToList();
            if (valid.Count > 0)
            {
                picks.Add(valid.MinBy(i => (double)chunk[i][1]!));
                picks.Add(valid.MaxBy(i => (double)chunk[i][1]!));
            }
            foreach (var quality in new[] { "Bad", "Uncertain" })
            {
                var i = chunk.FindIndex(p => p.Length > 2 && (string?)p[2] == quality);
                if (i >= 0) picks.Add(i);
            }
            result.AddRange(picks.Select(i => chunk[i]));
        }
        return result;
    }

    /// <summary>_load_legacy: comparison files, with display-only gap markers.</summary>
    Dictionary<string, object?> LoadLegacy(string path, List<string>? ids, double? start, double? end, int limit)
    {
        var selected = ids == null ? null : new HashSet<string>(ids);
        var series = new Dictionary<string, List<object?[]>>();
        var names = new Dictionary<string, string>();
        var types = new Dictionary<string, string>();
        long scanned = 0, numeric = 0;
        double low = double.PositiveInfinity, high = double.NegativeInfinity;
        var samples = new List<double>();
        foreach (var row in IterRows(path, selected, start, end))
        {
            scanned++;
            var nid = RowValues.StrOr(row.Value("node_id"));
            if (nid.Length == 0 || selected != null && !selected.Contains(nid)) continue;
            var t = RowValues.Finite(row.Value("timestamp_ms"));
            if (t == null || start != null && t < start || end != null && t > end) continue;
            var value = RowValues.Finite(row.Value("value_numeric"));
            var quality = RowValues.Quality(row);
            if (!series.TryGetValue(nid, out var list)) series[nid] = list = new();
            list.Add([t.Value, quality == "Good" ? value : null, quality]);
            names[nid] = RowValues.StrOr(row.Value("signal_name"), nid);
            types[nid] = RowValues.StrOr(row.Value("data_type"), "Double");
            if (RowValues.Finite(row.Value("sample_ms")) is double sm && samples.Count < 1000) samples.Add(sm);
            numeric += value != null && quality == "Good" ? 1 : 0;
            low = Math.Min(low, t.Value); high = Math.Max(high, t.Value);
        }
        var meta = Metadata(path);
        if (selected != null)
        {
            var metaNames = new Dictionary<string, string>();
            foreach (var n in meta["signals"] as JsonArray ?? new JsonArray())
                if (n is JsonObject o && o["nodeid"].Truthy())
                    metaNames[RowValues.Str(Py.Plain(o["nodeid"]))] = o["display_name"].Truthy() ? RowValues.Str(Py.Plain(o["display_name"])) : RowValues.Str(Py.Plain(o["nodeid"]));
            foreach (var nid in selected)
            {
                if (!series.ContainsKey(nid)) series[nid] = new();
                if (!names.ContainsKey(nid)) names[nid] = metaNames.TryGetValue(nid, out var n) ? n : nid;
                if (!types.ContainsKey(nid)) types[nid] = "Double";
            }
        }
        var counts = series.ToDictionary(k => k.Key, k => k.Value.Count);
        foreach (var points in series.Values) points.Sort((a, b) => ((double)a[0]!).CompareTo((double)b[0]!));
        var returned = series.ToDictionary(k => k.Key, k => Decimate(k.Value, limit));
        if (meta["comparison"].Truthy())
        {
            var info = new Dictionary<string, JsonObject>();
            foreach (var s in meta["signals"] as JsonArray ?? new JsonArray())
                if (s is JsonObject o && o["nodeid"].Str() is string id) info[id] = o;
            foreach (var (nid, points) in series)
            {
                var period = info.TryGetValue(nid, out var sig) ? FiniteParam(sig["sample_ms"]) : null;
                if (period is not > 0) continue;
                var gaps = new List<(double a, double b)>();
                for (var i = 0; i + 1 < points.Count; i++)
                    if ((double)points[i + 1][0]! - (double)points[i][0]! > period * 1.5) gaps.Add(((double)points[i][0]!, (double)points[i + 1][0]!));
                if (gaps.Count == 0) continue;
                var reduced = Decimate(points, Math.Max(4, limit / 2));
                var gapIndex = 0;
                var display = new List<object?[]>();
                foreach (var point in reduced)
                {
                    (double a, double b)? firstGap = null;
                    while (gapIndex < gaps.Count && gaps[gapIndex].b <= (double)point[0]!)
                    {
                        firstGap ??= gaps[gapIndex];
                        gapIndex++;
                    }
                    if (firstGap is { } fg) display.Add([(fg.a + fg.b) / 2, null, "Bad"]);
                    display.Add(point);
                }
                returned[nid] = display;
            }
        }
        return new()
        {
            ["name"] = Relative(path),
            ["format"] = Supported[Ext(path)],
            ["series"] = returned.ToDictionary(k => k.Key, k => (object?)k.Value),
            ["signals"] = returned.Select(k => (object?)new Dictionary<string, object?>
            {
                ["nodeid"] = k.Key, ["display_name"] = names.TryGetValue(k.Key, out var n) ? n : k.Key, ["data_type"] = types.TryGetValue(k.Key, out var t) ? t : "Double",
                ["points_original"] = counts.TryGetValue(k.Key, out var c) ? c : 0, ["points_returned"] = k.Value.Count,
            }).ToList(),
            ["rows_scanned"] = scanned, ["numeric_rows"] = numeric,
            ["start_ms"] = double.IsInfinity(low) ? null : low, ["end_ms"] = double.IsInfinity(high) ? null : high,
            ["max_points_per_signal"] = limit,
            ["sample_ms"] = samples.Count > 0 ? samples.Sum() / samples.Count : null,
            ["decimated"] = returned.Any(k => (counts.TryGetValue(k.Key, out var c) ? c : 0) > k.Value.Count),
        };
    }

    public Dictionary<string, object?> Markers(string name, JsonNode? signalIds, JsonNode? x1, JsonNode? x2)
    {
        var path = PathOf(name);
        if (signalIds is not JsonArray list || list.Count < 1 || list.Count > MaxSignals) throw new UserError("Selezionare almeno un segnale valido");
        var a = FiniteParam(x1);
        var b = FiniteParam(x2);
        if (a == null || b == null) throw new UserError("Marker temporali non validi");
        var ids = list.Select(n => RowValues.Str(Py.Plain(n))).ToList();
        var identity = Fingerprint(path, Metadata(path));
        var key = identity + "#" + string.Join("\u0001", ids) + "#" + a + "#" + b;
        var seq = Ext(path) == ".pda3seq";
        if (!seq && MarkerCache.Get(key) is { } cached) return cached;
        var wanted = new HashSet<string>(ids);
        var targets = new[] { ("x1", a.Value), ("x2", b.Value) };
        var best = wanted.ToDictionary(n => n, _ => new Dictionary<string, object?> { ["x1"] = null, ["x2"] = null });
        var distance = wanted.ToDictionary(n => n, _ => new Dictionary<string, double> { ["x1"] = double.PositiveInfinity, ["x2"] = double.PositiveInfinity });
        var names = wanted.ToDictionary(n => n, n => n);
        foreach (var r in PlotRows(path, ids))
        {
            if (!wanted.Contains(r.NodeId) || r.Value == null || r.Quality != "Good") continue;
            names[r.NodeId] = r.Name;
            foreach (var (k, target) in targets)
            {
                var d = Math.Abs(r.T - target);
                if (d < distance[r.NodeId][k])
                {
                    distance[r.NodeId][k] = d;
                    best[r.NodeId][k] = new Dictionary<string, object?> { ["timestamp_ms"] = r.T, ["value"] = r.Value, ["offset_ms"] = r.T - target };
                }
            }
        }
        var metadata = Metadata(path);
        if (metadata["comparison"].Truthy())
        {
            var info = new Dictionary<string, JsonObject>();
            foreach (var s in metadata["signals"] as JsonArray ?? new JsonArray())
                if (s is JsonObject o && o["nodeid"].Str() is string id) info[id] = o;
            foreach (var nid in wanted)
            {
                var signal = info.TryGetValue(nid, out var o) ? o : new JsonObject();
                var period = FiniteParam(signal["sample_ms"]);
                var low = signal["start_ms"].Float() ?? double.NegativeInfinity;
                var high = signal["end_ms"].Float() ?? double.PositiveInfinity;
                foreach (var (k, target) in targets)
                    if (target < low || target > high || period is double p && p != 0 && distance[nid][k] > p * .75) best[nid][k] = null;
            }
        }
        var result = new Dictionary<string, object?>
        {
            ["x1"] = a, ["x2"] = b,
            ["signals"] = ids.Where(best.ContainsKey).Select(nid => (object?)new Dictionary<string, object?>
                { ["nodeid"] = nid, ["display_name"] = names[nid], ["x1"] = best[nid]["x1"], ["x2"] = best[nid]["x2"] }).ToList(),
        };
        if (!seq && identity == Fingerprint(path, Metadata(path))) MarkerCache.Put(key, result);
        return result;
    }
}

/// <summary>Peak/quality preserving bounded display series (recording_preview.PeakSeries).</summary>
public sealed class PeakSeries(int limit)
{
    readonly record struct Point(long Ordinal, double T, double? Value, string Quality);

    readonly int capacity = Math.Max(2, (limit / 7 - 2) / 2 * 2);
    List<Point>? raw = new();
    List<List<Point>> buckets = new();
    List<Point> pending = new();
    long width = 1;
    Point? earliest, latest;
    public long Count { get; private set; }

    public void Add(double t, double? value, string quality)
    {
        var point = new Point(Count, t, value, quality);
        Count++;
        if (earliest == null || t < earliest.Value.T) earliest = point;
        if (latest == null || t >= latest.Value.T) latest = point;
        if (raw != null)
        {
            raw.Add(point);
            if (raw.Count <= limit) return;
            var all = raw;
            raw = null;
            foreach (var p in all) Bucket(p);
        }
        else Bucket(point);
    }

    static List<Point> Envelope(List<Point> points)
    {
        var picks = new SortedDictionary<long, Point> { [points[0].Ordinal] = points[0], [points[^1].Ordinal] = points[^1] };
        Point? low = null, high = null;
        foreach (var p in points)
        {
            if (p.Value is not double v) continue;
            if (low == null || v < low.Value.Value) low = p;
            if (high == null || v > high.Value.Value) high = p;
        }
        if (low != null) { picks[low.Value.Ordinal] = low.Value; picks[high!.Value.Ordinal] = high.Value; }
        foreach (var quality in new[] { "Bad", "Uncertain" })
            foreach (var p in points)
                if (p.Quality == quality) { picks[p.Ordinal] = p; break; }
        foreach (var p in points)
            if (p.Value == null) { picks[p.Ordinal] = p; break; }
        return picks.Values.ToList();
    }

    void Bucket(Point point)
    {
        pending.Add(point);
        if (pending.Count > 14) pending = Envelope(pending);
        if ((point.Ordinal + 1) % width == 0)
        {
            buckets.Add(Envelope(pending));
            pending = new();
            if (buckets.Count >= capacity)
            {
                var merged = new List<List<Point>>();
                for (var i = 0; i < buckets.Count; i += 2)
                    merged.Add(Envelope(i + 1 < buckets.Count ? buckets[i].Concat(buckets[i + 1]).ToList() : buckets[i]));
                buckets = merged;
                width *= 2;
            }
        }
    }

    public List<object?[]> Result()
    {
        List<Point> points;
        if (raw != null) points = raw;
        else
        {
            points = buckets.SelectMany(b => b).ToList();
            if (pending.Count > 0) points.AddRange(Envelope(pending));
            if (earliest != null)
            {
                var chosen = new Dictionary<long, Point>();
                foreach (var p in points) chosen[p.Ordinal] = p;
                chosen[earliest.Value.Ordinal] = earliest.Value;
                chosen[latest!.Value.Ordinal] = latest.Value;
                points = chosen.Values.ToList();
            }
        }
        return points.OrderBy(p => p.T).ThenBy(p => p.Ordinal).Select(p => new object?[] { p.T, p.Value, p.Quality }).ToList();
    }
}
