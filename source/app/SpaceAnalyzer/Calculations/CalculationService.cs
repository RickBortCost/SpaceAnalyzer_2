using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Calculations;

/// <summary>
/// Bounded live computation and original-sample replay, independent of acquisition
/// (calculation_service.py + calculation_worker.py, in-process on a dedicated thread).
/// </summary>
public sealed class CalculationService
{
    public delegate void ResultSink(List<Dictionary<string, object?>> rows, long generation, int sampleMs);

    abstract record Item;
    sealed record BatchItem(long Generation, double T, List<FormulaInput> Rows, int Period) : Item;
    sealed record Barrier(ManualResetEventSlim Done) : Item;

    readonly string directory;
    readonly ResultSink callback;
    readonly object gate = new();
    readonly BlockingCollection<Item> queue;
    readonly int capacity;
    readonly Thread thread;
    readonly object historyLock = new();
    long generation;
    (List<JsonObject> formulas, string? protocol)? config;
    HashSet<string> rawIds = new();
    List<string> ids = new();
    (long generation, double t, int period)? overflow;
    long dropped, late;
    double elapsedMs;
    string error = "";
    volatile bool closed;

    public Action<string, long>? FaultCallback { get; set; }

    public CalculationService(string directory, ResultSink callback, int capacity = 512)
    {
        this.directory = directory;
        this.callback = callback;
        this.capacity = capacity;
        queue = new BlockingCollection<Item>(capacity);
        thread = new Thread(Loop) { IsBackground = true, Name = "FormulaController" };
        thread.Start();
    }

    public List<string> Ids { get { lock (gate) return ids.ToList(); } }
    public long Generation { get { lock (gate) return generation; } }

    public long Configure(List<JsonObject> formulas, string? protocol)
    {
        var plan = new Plan(formulas, "live", protocol: protocol);
        lock (gate)
        {
            generation++;
            config = (formulas, protocol);
            rawIds = new HashSet<string>(plan.RawIds);
            ids = plan.Ids.ToList();
            error = "";
            overflow = null;
            while (queue.TryTake(out var item)) if (item is Barrier b) b.Done.Set();
            return generation;
        }
    }

    public void Submit(double t, IEnumerable<Sample> rows, int sampleMs)
    {
        lock (gate)
        {
            if (ids.Count == 0 || closed) return;
            var normalized = rows.Where(r => rawIds.Contains(r.NodeId))
                .Select(r => new FormulaInput(r.NodeId, r.PlotValue is double v && double.IsFinite(v) ? v : null, r.Quality is { Length: > 0 } q ? q : r.Good ? "Good" : "Bad")).ToList();
            if (!queue.TryAdd(new BatchItem(generation, t, normalized, sampleMs)))
            {
                dropped++;
                overflow = (generation, t, sampleMs);
                error = "Coda calcoli piena: campioni calcolati persi; acquisizione originale continua";
                FaultCallback?.Invoke(error, dropped);
            }
        }
    }

    public Dictionary<string, object?> Status()
    {
        lock (gate)
            return new()
            {
                ["active_ids"] = ids.Cast<object?>().ToList(), ["queue"] = (long)queue.Count, ["capacity"] = (long)capacity, ["dropped_batches"] = dropped,
                ["late_samples"] = late, ["processing_ms"] = elapsedMs, ["error"] = error, ["generation"] = generation,
            };
    }

    public void Flush(double timeout = 10)
    {
        var done = new ManualResetEventSlim(false);
        if (!queue.TryAdd(new Barrier(done), TimeSpan.FromSeconds(timeout))) throw new UserError("Calcoli ancora occupati: riprovare Stop");
        if (!done.Wait(TimeSpan.FromSeconds(timeout))) throw new UserError("Attesa calcoli scaduta: riprovare Stop");
    }

    public void Close()
    {
        closed = true;
        thread.Join(6000);
    }

    /// <summary>Engine with a 20 ms watermark: originals of different sources are merged by time.</summary>
    sealed class LiveWorker(Plan plan)
    {
        readonly FormulaEngine engine = new(plan);
        readonly PriorityQueue<(double t, List<FormulaInput> rows), (double, long)> pending = new();
        long sequence;
        double? high;
        public long Late => engine.LateSamples;

        public List<Dictionary<string, object?>> Run(List<(double t, List<FormulaInput> rows)> events, bool flush)
        {
            foreach (var (t, rows) in events)
            {
                sequence++;
                pending.Enqueue((t, rows), (t, sequence));
                high = high == null ? t : Math.Max(t, high.Value);
            }
            var watermark = flush ? double.PositiveInfinity : high != null ? high.Value - 20 : double.NegativeInfinity;
            var result = new List<Dictionary<string, object?>>();
            double? current = null;
            var group = new List<FormulaInput>();
            while (pending.TryPeek(out var item, out _) && item.t <= watermark)
            {
                pending.Dequeue();
                if (current != null && item.t != current.Value)
                {
                    result.AddRange(engine.Process(current.Value, group));
                    group = new List<FormulaInput>();
                }
                current = item.t;
                group.AddRange(item.rows);
            }
            if (current != null) result.AddRange(engine.Process(current.Value, group));
            if (pending.Count > 4096) throw new UserError("Buffer temporale calcoli pieno");
            return result;
        }
    }

    void Deliver(List<Dictionary<string, object?>> rows, long gen, int sampleMs)
    {
        if (rows.Count > 0 && gen == Generation && !closed) callback(rows, gen, sampleMs);
    }

    void Loop()
    {
        long current = -1;
        var sampleMs = 0;
        double? lastEventT = null;
        LiveWorker? worker = null;
        List<string> activeIds = new();
        (List<JsonObject> formulas, string? protocol)? cfg = null;
        List<Dictionary<string, object?>> Request(List<(double, List<FormulaInput>)> events, bool flush)
        {
            var started = System.Diagnostics.Stopwatch.GetTimestamp();
            var rows = worker!.Run(events, flush);
            lock (gate)
            {
                elapsedMs = System.Diagnostics.Stopwatch.GetElapsedTime(started).TotalMilliseconds;
                late = worker.Late;
                if (overflow == null) error = "";
            }
            return rows;
        }
        void Restart() => worker = activeIds.Count > 0 && cfg != null ? new LiveWorker(new Plan(cfg.Value.formulas, "live", protocol: cfg.Value.protocol)) : null;
        bool Sync()
        {
            lock (gate)
            {
                if (generation == current) return false;
                current = generation; cfg = config; activeIds = ids.ToList();
            }
            lastEventT = null;
            Restart();
            return true;
        }
        while (!closed)
        {
            ManualResetEventSlim? barrier = null;
            try
            {
                Sync();
                if (!queue.TryTake(out var item, 50))
                {
                    if (worker != null) Deliver(Request(new(), true), current, sampleMs);
                    continue;
                }
                Sync();
                if (item is Barrier b)
                {
                    barrier = b.Done;
                    if (worker != null) Deliver(Request(new(), true), current, sampleMs);
                    barrier.Set();
                    continue;
                }
                var batches = new List<BatchItem> { (BatchItem)item };
                for (var i = 0; i < 31 && queue.TryTake(out var next); i++)
                {
                    if (next is Barrier nb) { barrier = nb.Done; break; }
                    batches.Add((BatchItem)next);
                }
                var events = new List<(double, List<FormulaInput>)>();
                foreach (var batch in batches)
                {
                    if (batch.Generation != current) continue;
                    sampleMs = batch.Period;
                    lastEventT = lastEventT == null ? batch.T : Math.Max(batch.T, lastEventT.Value);
                    events.Add((batch.T, batch.Rows));
                }
                if (events.Count > 0 && worker != null) Deliver(Request(events, false), current, sampleMs);
                (long generation, double t, int period)? lost;
                lock (gate) { lost = overflow; overflow = null; }
                if (lost is { } o && o.generation == current)
                {
                    Deliver(activeIds.Select(n => Gap(n, o.t, "CalculationQueueOverflow")).ToList(), current, o.period);
                    // Reset held values: discarded source changes must never be used as if current.
                    Restart();
                }
                if (barrier != null)
                {
                    if (worker != null) Deliver(Request(new(), true), current, sampleMs);
                    barrier.Set();
                }
            }
            catch (Exception exc)
            {
                lock (gate) error = exc.Message.Length > 0 ? exc.Message : exc.GetType().Name;
                if (lastEventT is double t) Deliver(activeIds.Select(n => Gap(n, t, "CalculationWorkerError: " + exc.Message)).ToList(), current, sampleMs);
                worker = null;
                current = -1;
                barrier?.Set();
                Thread.Sleep(100);
            }
        }
    }

    static Dictionary<string, object?> Gap(string nid, double t, string status) => new()
    {
        ["node_id"] = nid, ["timestamp_ms"] = t, ["value_numeric"] = null, ["quality"] = "Bad", ["good"] = false, ["status"] = status,
        ["data_type"] = "Double", ["source_timestamp"] = FormulaEngine.Stamp(t),
    };

    public static List<Dictionary<string, object?>> Available(List<JsonObject> formulas, IEnumerable<string> physicalIds)
    {
        var physical = new HashSet<string>(physicalIds);
        var plan = new Plan(formulas, "recorded", externalIds: physical);
        var result = new List<Dictionary<string, object?>>();
        foreach (var nid in plan.Ids)
        {
            var f = plan.Formulas[nid];
            if (!physical.Contains(nid) && plan.RawSources(nid).All(s => physical.Contains(s["nodeid"].Str() ?? "")))
                result.Add(new()
                {
                    ["nodeid"] = nid, ["display_name"] = f["name"].Str(),
                    ["unit"] = f["unit"].Truthy() ? f["unit"].Str() : f["unit_hint"].Str(), ["calculated"] = true, ["expression"] = f["expression"].Str(),
                });
        }
        return result;
    }

    /// <summary>Materialized recording with the formula outputs (cache keyed by file, formulas and interval).</summary>
    public (RecordingLibrary library, string name, Dictionary<string, object?> descriptor) History(RecordingLibrary library, string? name, IList<string>? requested,
        List<JsonObject> formulas, double? start = null, double? end = null, string? destination = null, string fmt = "parquet")
    {
        var original = library.PathOf(name);
        var descriptor = library.Describe(name!);
        var physical = new HashSet<string>(((List<object?>)descriptor["signals"]!).Cast<Dictionary<string, object?>>().Select(s => (string)s["nodeid"]!));
        if (physical.Count == 0)
            physical = new HashSet<string>(((List<object?>)library.Load(name!, maxPoints: JsonValue.Create(100))["signals"]!).Cast<Dictionary<string, object?>>().Select(s => (string)s["nodeid"]!));
        var wanted = (requested is { Count: > 0 } ? requested : physical.ToList()).Distinct().ToList();
        if (wanted.Count > 64) throw new UserError("Massimo 64 segnali per elaborazione");
        var virtualIds = wanted.Where(n => !physical.Contains(n)).ToHashSet();
        var available = Available(formulas, physical).Select(s => (string)s["nodeid"]!).ToHashSet();
        if (!virtualIds.IsSubsetOf(available)) throw new UserError("Formula non disponibile: controllare modalità, stato e sorgenti presenti nel file");
        var stat = new FileInfo(original);
        string? datSignature = null;
        var ext = Path.GetExtension(original).ToLowerInvariant();
        if (ext == ".pda3seq")
        {
            var (parts, info) = Pda3.SequenceInfo(library, original);
            datSignature = Py.Dumps(info) + string.Join("|", parts.Select(p => p.name + ":" + Pda3.Reader(library.PathOf(p.name)).Sha256 + ":" + Py.Dumps(p.info)));
        }
        else if (ext == ".dat")
            datSignature = Pda3.IsPda3(original) ? Py.Dumps(Pda3.Details(original, library.Metadata(original))) : Py.Dumps(library.DatSource(original).meta);
        var keyText = Py.DumpsPlain(new List<object?>
        {
            original, stat.Length, stat.LastWriteTimeUtc.Ticks, datSignature, wanted.Cast<object?>().ToList(), formulas.Select(f => (object?)f).ToList(), start, end,
        });
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(keyText))).ToLowerInvariant();
        Directory.CreateDirectory(directory);
        var output = destination ?? Path.Combine(directory, key + ".parquet");
        var meta = Path.ChangeExtension(output, ".metadata.json");
        lock (historyLock)
        {
            if (destination == null && File.Exists(output) && File.Exists(meta))
            {
                File.SetLastWriteTimeUtc(output, DateTime.UtcNow);
                return (new RecordingLibrary(Path.GetDirectoryName(output)!), Path.GetFileName(output), descriptor);
            }
            var temporary = output + "." + Guid.NewGuid().ToString("N") + ".part";
            try
            {
                var job = new HistoryJob(original, wanted, physical, formulas, start, end, temporary, fmt, RowValues.Float(descriptor["sample_ms"]) ?? 0);
                var task = Task.Run(() => CalculationHistory.Calculate(job));
                if (!task.Wait(TimeSpan.FromSeconds(180))) throw new UserError("Calcolo storico scaduto: ridurre intervallo o numero di segnali");
                var metadata = task.GetAwaiter().GetResult();
                metadata["format"] = fmt;
                metadata["version"] = 1L;
                metadata["started_at"] = FormulaEngine.Stamp((double)metadata["start_ms"]!);
                metadata["ended_at"] = FormulaEngine.Stamp((double)metadata["end_ms"]!);
                Directory.CreateDirectory(Path.GetDirectoryName(output)!);
                File.Move(temporary, output, true);
                Py.AtomicPlain(meta, metadata);
                if (destination == null)
                {
                    var caches = new DirectoryInfo(directory).GetFiles("*.parquet").OrderByDescending(f => f.LastWriteTimeUtc).Skip(8);
                    foreach (var old in caches)
                        if (old.FullName != Path.GetFullPath(output) && (DateTime.UtcNow - old.LastWriteTimeUtc).TotalSeconds > 86400)
                        {
                            try { old.Delete(); File.Delete(Path.ChangeExtension(old.FullName, ".metadata.json")); } catch (IOException) { }
                        }
                }
            }
            catch (AggregateException exc) when (exc.InnerException != null) { throw exc.InnerException; }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        return (new RecordingLibrary(Path.GetDirectoryName(output)!), Path.GetFileName(output), descriptor);
    }

    public sealed record HistoryJob(string Path, List<string> SignalIds, HashSet<string> Materialized, List<JsonObject> Formulas, double? StartMs, double? EndMs, string Output, string Format, double SampleMs);

    /// <summary>calculation_service.preview_live: last 200 outputs of one formula over captured live points.</summary>
    public static List<Dictionary<string, object?>> PreviewLive(List<JsonObject> formulas, string nid, Dictionary<string, List<(double t, double? value, string quality)>> points, double? start = null, double? end = null)
    {
        var plan = new Plan(formulas, "live", [nid]);
        var engine = new FormulaEngine(plan);
        var ordered = points.Where(k => plan.RawIds.Count == 0 || plan.RawIds.Contains(k.Key))
            .SelectMany(k => k.Value.Select(p => (p.t, key: k.Key, p))).OrderBy(x => x.t).ThenBy(x => x.key, StringComparer.Ordinal)
            .ThenBy(x => x.p.value ?? double.NegativeInfinity).ThenBy(x => x.p.quality, StringComparer.Ordinal).ToList();
        var result = new List<Dictionary<string, object?>>();
        var i = 0;
        while (i < ordered.Count)
        {
            var t = ordered[i].t;
            if (end != null && t > end) break;
            var rows = new List<FormulaInput>();
            while (i < ordered.Count && ordered[i].t == t) { rows.Add(new FormulaInput(ordered[i].key, ordered[i].p.value is double v && double.IsFinite(v) ? v : null, ordered[i].p.quality)); i++; }
            var output = engine.Process(t, rows);
            if (start == null || t >= start) result.AddRange(output);
        }
        return result.TakeLast(200).ToList();
    }

    public List<Dictionary<string, object?>> Preview(List<JsonObject> formulas, string nid, Dictionary<string, List<(double t, double? value, string quality)>> points, double? start, double? end)
    {
        var task = Task.Run(() => PreviewLive(formulas, nid, points, start, end));
        if (!task.Wait(TimeSpan.FromSeconds(60))) throw new UserError("Anteprima scaduta: ridurre numero di sorgenti o complessità");
        return task.GetAwaiter().GetResult();
    }
}

/// <summary>Original-row calculation before display decimation (calculation_history.py).</summary>
public static class CalculationHistory
{
    const long MaxInputRows = 2000000;
    const long MaxOutputRows = 2000000;

    public static Dictionary<string, object?> Calculate(CalculationService.HistoryJob job)
    {
        var path = job.Path;
        var library = new RecordingLibrary(Path.GetDirectoryName(path)!);
        var originalMeta = library.Metadata(path);
        var wanted = job.SignalIds.Distinct().ToList();
        var materialized = job.Materialized;
        var calculated = wanted.Where(n => n.StartsWith("calc:", StringComparison.Ordinal) && !materialized.Contains(n)).ToList();
        var plan = new Plan(job.Formulas, "recorded", calculated, externalIds: materialized);
        var rawWanted = wanted.Except(calculated).ToHashSet();
        var rawIds = new HashSet<string>(rawWanted.Concat(plan.RawIds));
        if (plan.Order.Count > 0 && plan.RawIds.Count == 0 && materialized.Count > 0) rawIds.Add(materialized.OrderBy(n => n, StringComparer.Ordinal).First());
        var start = job.StartMs;
        var end = job.EndMs;
        var ages = new Dictionary<string, double>();
        foreach (var n in plan.Order)
        {
            var f = plan.Formulas[n];
            var inner = (f["bindings"] as JsonObject ?? new JsonObject()).Select(b => ages.TryGetValue(b.Value.Get("nodeid").Str() ?? "", out var a) ? a : 0).DefaultIfEmpty(0).Max();
            ages[n] = (f["max_age_ms"].Num() ?? 0) + inner;
        }
        var age = ages.Values.DefaultIfEmpty(0).Max();
        var inputs = new List<(double t, string nid, double? value, string quality)>();
        var names = rawWanted.ToDictionary(n => n, n => n);
        static string Quality(Row row)
        {
            var q = RowValues.Truthy(row.Value("quality")) ? RowValues.Str(row.Value("quality"))
                : RowValues.TrueOne(row.TryGetValue("good", out var g) ? g : true) ? "Good" : RowValues.Str(row.Value("status") ?? "").StartsWith("Uncertain", StringComparison.Ordinal) ? "Uncertain" : "Bad";
            return q;
        }
        foreach (var row in library.IterRows(path))
        {
            var nid = row.Value("node_id") as string;
            if (nid == null || !rawIds.Contains(nid)) continue;
            var t = RowValues.Finite(row.Value("timestamp_ms"));
            if (t == null || end != null && t > end || start != null && t < start - age) continue;
            var quality = Quality(row);
            quality = quality == "Good" ? "Good" : quality == "Uncertain" ? "Uncertain" : "Bad";
            inputs.Add((t.Value, nid, RowValues.Finite(row.Value("value_numeric")), quality));
            if (rawWanted.Contains(nid)) names[nid] = RowValues.StrOr(row.Value("signal_name"), nid);
            if (inputs.Count > MaxInputRows) throw new UserError("Calcolo storico: massimo 2 milioni di campioni sorgente; ridurre l’intervallo o i segnali");
        }
        var sorted = inputs.Select((x, i) => (x, i)).OrderBy(p => p.x.t).ThenBy(p => p.i).Select(p => p.x).ToList();
        var engine = new FormulaEngine(plan);
        var counts = new Dictionary<string, long> { ["Good"] = 0, ["Uncertain"] = 0, ["Bad"] = 0 };
        double low = double.PositiveInfinity, high = double.NegativeInfinity;
        long outputCount = 0;
        IEnumerable<Row> Rows()
        {
            foreach (var row in library.IterRows(path))
            {
                var t = RowValues.Finite(row.Value("timestamp_ms"));
                if (row.Value("node_id") is not string nid || !rawWanted.Contains(nid) || t == null) continue;
                if (start != null && t < start || end != null && t > end) continue;
                if (++outputCount > MaxOutputRows) throw new UserError("Massimo 2 milioni di righe risultato; ridurre l’intervallo");
                low = Math.Min(low, t.Value); high = Math.Max(high, t.Value);
                var quality = RowValues.Truthy(row.Value("quality")) ? RowValues.Str(row.Value("quality"))
                    : RowValues.Truth(row.TryGetValue("good", out var g) ? g : true) ? "Good" : RowValues.Str(row.Value("status") ?? "").StartsWith("Uncertain", StringComparison.Ordinal) ? "Uncertain" : "Bad";
                counts[counts.ContainsKey(quality) ? quality : "Bad"]++;
                yield return row;
            }
            var i = 0;
            while (i < sorted.Count)
            {
                var t = sorted[i].t;
                var current = new List<FormulaInput>();
                while (i < sorted.Count && sorted[i].t == t) { current.Add(new FormulaInput(sorted[i].nid, sorted[i].value, sorted[i].quality)); i++; }
                foreach (var output in engine.Process(t, current))
                {
                    if (start != null && t < start) continue;
                    var row = new Row();
                    foreach (var (k, v) in output) row[k] = v;
                    row["sample_ms"] = job.SampleMs % 1 == 0 ? (object)(long)job.SampleMs : job.SampleMs;
                    counts[(string)row["quality"]!]++;
                    if (++outputCount > MaxOutputRows) throw new UserError("Massimo 2 milioni di righe risultato; ridurre l’intervallo o le formule");
                    low = Math.Min(low, t); high = Math.Max(high, t);
                    yield return row;
                }
            }
        }
        var count = Export.ExportRows(Rows(), job.Output, job.Format);
        var originalSignals = new Dictionary<string, JsonObject>();
        foreach (var s in originalMeta["signals"] as JsonArray ?? new JsonArray())
            if (s is JsonObject o && o["nodeid"].Str() is string id) originalSignals[id] = o;
        var signals = wanted.Where(rawWanted.Contains).Select(n => (object?)new Dictionary<string, object?>
        {
            ["nodeid"] = n, ["display_name"] = names.TryGetValue(n, out var nm) ? nm : n,
            ["unit"] = originalSignals.TryGetValue(n, out var o) ? o["unit"].Str() ?? "" : "", ["calculated"] = n.StartsWith("calc:", StringComparison.Ordinal),
        }).ToList();
        signals.AddRange(calculated.Select(n => (object?)new Dictionary<string, object?>
        {
            ["nodeid"] = n, ["display_name"] = plan.Formulas[n]["name"].Str(),
            ["unit"] = plan.Formulas[n]["unit"].Truthy() ? plan.Formulas[n]["unit"].Str() : plan.Formulas[n]["unit_hint"].Str(), ["calculated"] = true,
        }));
        var versions = new Dictionary<string, object?>();
        foreach (var f in originalMeta["calculation_formulas"] as JsonArray ?? new JsonArray())
            if (f.Get("nodeid").Str() is string id && rawIds.Contains(id)) versions[id] = f!.DeepClone();
        foreach (var n in plan.Order) if (!materialized.Contains(n)) versions[n] = plan.Formulas[n].DeepClone();
        return new()
        {
            ["signals"] = signals, ["rows_written"] = count, ["start_ms"] = low, ["end_ms"] = high, ["duration_seconds"] = (high - low) / 1000,
            ["quality_counts"] = counts.ToDictionary(k => k.Key, k => (object?)k.Value), ["calculated_ids"] = calculated.Cast<object?>().ToList(),
            ["calculation_formulas"] = versions.Values.ToList(), ["original"] = Path.GetFileName(path), ["calculation_mode"] = "materialized",
            ["description"] = "Serie calcolate dai campioni originali; allineamento causale senza interpolazione",
            ["session_name"] = "Dati calcolati", ["initial_sample_ms"] = job.SampleMs % 1 == 0 ? (object)(long)job.SampleMs : job.SampleMs,
        };
    }
}
