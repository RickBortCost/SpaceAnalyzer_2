using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.RegularExpressions;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// Long-layout CSV/Parquet recording: one row per channel per acquisition cycle
/// (port of recordings.Recorder; files and sidecars are read by recording_reader.py).
/// </summary>
public sealed partial class TableRecorder : IFormatRecorder, IDisposable
{
    public static readonly string[] Columns =
    [
        "timestamp_utc", "timestamp_ms", "elapsed_ms", "cycle", "node_id", "signal_name", "value_numeric", "value_text",
        "data_type", "status", "good", "source_timestamp", "server_timestamp", "sample_ms", "quality", "status_code",
        "local_timestamp", "ads_filetime", "source_protocol", "acquisition_timestamp_ms", "acquisition_origin",
    ];

    sealed record Cycle(double Timestamp, double Elapsed, long Number, List<Sample> Rows, int Period);

    readonly string directory;
    readonly string version;
    readonly object gate = new();
    readonly Queue<Cycle> queue = new();
    readonly SemaphoreSlim signal = new(0);
    StorageProfile storage = null!;
    string format = "";
    int capacity;
    long maxBytes;
    double maxDuration;
    long started;
    Dictionary<string, string> names = new();
    List<NodeSpec> nodes = new();
    Thread? worker;
    volatile bool stopRequested;
    Dictionary<string, object?> metadata = new();
    Dictionary<string, object?> state = new();
    readonly List<Dictionary<string, object?>> partFiles = new();
    string sessionBase = "";
    string finalPath = "", partialPath = "";
    FileStream? stream;
    StreamWriter? csv;
    ParquetPart? parquet;
    int segment;
    long segmentRows, segmentCycles, pendingRows;
    long? segmentLastCycle;
    double? segmentFirst, segmentLast, segmentElapsed;
    long parquetBound;
    readonly List<Row> parquetPending = new();
    int currentPeriod;
    string endpoint = "";
    JsonElement? acquisitionInfo;

    public TableRecorder(string directory, string version, int queueCapacity = 2000)
    {
        this.directory = Path.GetFullPath(directory);
        this.version = version;
        capacity = queueCapacity;
    }

    public bool Busy { get { lock (gate) return state.GetValueOrDefault("state") is "recording" or "stopping"; } }
    public string Error { get { lock (gate) return state.GetValueOrDefault("error") as string ?? ""; } }
    public string FinalPath => finalPath;
    public IReadOnlyList<Dictionary<string, object?>> PartFiles { get { lock (gate) return partFiles.ToList(); } }
    public string Format => format;

    public void Start(string fmt, IReadOnlyList<NodeSpec> selected, string endpoint, int sampleMs, JsonElement options)
    {
        format = fmt.ToLowerInvariant();
        storage = StorageProfile.For(format, options);
        if (selected.Count == 0) throw new ArgumentException("Selezionare almeno una variabile prima di avviare Record");
        double Limit(string key, double fallback, double low, double high)
        {
            if (!options.TryGetProperty(key, out var value) || value.ValueKind == JsonValueKind.Null || (value.ValueKind == JsonValueKind.String && value.GetString() == "")) return fallback;
            var number = value.ValueKind == JsonValueKind.String ? double.Parse(value.GetString()!, CultureInfo.InvariantCulture) : value.GetDouble();
            if (!double.IsFinite(number) || number < low || number > high) throw new ArgumentException("Valore non valido: " + key);
            return number;
        }
        var hasOptions = options.ValueKind == JsonValueKind.Object && options.EnumerateObject().Any();
        maxBytes = (long)(Limit("max_file_mb", 1024, 0, 102400) * 1024 * 1024);
        maxDuration = Limit("max_duration_s", 0, 0, 86400 * 7);
        var bufferSeconds = Limit("buffer_seconds", 10, 1, 120);
        if (hasOptions) capacity = (int)Math.Max(1, Math.Min(Math.Min(50000, 200000 / selected.Count), Math.Ceiling(bufferSeconds * 1000 / Math.Max(1, sampleMs))));
        if (options.ValueKind == JsonValueKind.Object && options.TryGetProperty("_queue_capacity_cycles", out var forced))
            capacity = Math.Max(1, Math.Min(Math.Min(50000, 200000 / selected.Count), forced.GetInt32()));
        var labels = new Dictionary<string, string>();
        foreach (var (key, size) in new[] { ("session_name", 100), ("machine", 100), ("operator", 100), ("description", 2000) })
        {
            var text = options.ValueKind == JsonValueKind.Object && options.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
            labels[key] = text.Length > size ? text[..size] : text;
        }
        nodes = selected.ToList();
        names = nodes.ToDictionary(n => n.NodeId, n => n.Name);
        this.endpoint = endpoint;
        acquisitionInfo = options.ValueKind == JsonValueKind.Object && options.TryGetProperty("acquisition_info", out var parquetInfo) && parquetInfo.ValueKind == JsonValueKind.Object ? parquetInfo.Clone() : null;
        Directory.CreateDirectory(directory);
        var now = DateTime.UtcNow;
        var name = Prefix(endpoint) + now.ToString("yyyyMMdd_HHmmss_ffffff", CultureInfo.InvariantCulture) + "_" + Guid.NewGuid().ToString("N")[..8];
        var prefix = SafeName().Replace(labels["session_name"], "_").Trim('_');
        if (prefix.Length > 60) prefix = prefix[..60];
        sessionBase = (prefix.Length > 0 ? prefix + "_" : "") + name;
        segment = 1;
        OpenPart();
        EngineLog.Info($"Recording started: format={fmt} signals={selected.Count}");
        started = Stopwatch.GetTimestamp();
        currentPeriod = sampleMs;
        state = new()
        {
            ["state"] = "recording", ["format"] = format, ["filename"] = Path.GetFileName(finalPath), ["path"] = finalPath,
            ["rows_written"] = 0L, ["cycles_received"] = 0L, ["dropped_cycles"] = 0L, ["error"] = "",
            ["started_at"] = PythonIso(now), ["ended_at"] = null, ["reason"] = "", ["duration_seconds"] = 0.0,
        };
        var protocol = SourceProtocol(endpoint);
        metadata = new()
        {
            ["version"] = version, ["format"] = format, ["started_at"] = PythonIso(now), ["endpoint"] = endpoint,
            ["initial_sample_ms"] = sampleMs, ["signals"] = nodes.Select(n => (object?)(Action<Utf8JsonWriter>)n.Write).ToList(),
            ["source_protocol"] = protocol,
            ["quality_semantics"] = endpoint.StartsWith("s7://") ? "S7 ReadVar result; CPU RUN/STOP state not queried" : endpoint.StartsWith("ads://") ? "ADS result code; Uncertain when runtime not RUN" : "OPC UA StatusCode",
            ["timestamp_semantics"] = endpoint.StartsWith("s7://") ? "Local read completion; source/server timestamps unavailable in S7 ReadVar; reads are not a PLC-cycle snapshot" : endpoint.StartsWith("ads://") ? "Local acquisition time; source/server timestamps unavailable in ADS Read" : "OPC UA source/server plus local acquisition time",
            ["layout"] = "long", ["timestamps"] = "UTC", ["source"] = "acquisition before chart decimation",
            ["storage"] = storage.ToDictionary(), ["format_label"] = storage.Label,
            ["session_name"] = labels["session_name"], ["machine"] = labels["machine"], ["operator"] = labels["operator"], ["description"] = labels["description"],
            ["session_id"] = sessionBase, ["max_file_bytes"] = maxBytes, ["max_duration_s"] = maxDuration, ["queue_capacity_cycles"] = capacity,
            ["writer"] = "Space Fieldbus Analyzer engine (C#)",
        };
        if (options.ValueKind == JsonValueKind.Object && options.TryGetProperty("calculation_formulas", out var formulas) && formulas.ValueKind == JsonValueKind.Array && formulas.GetArrayLength() > 0)
        {
            metadata["calculation_formulas"] = formulas.Clone();
            metadata["calculation_timestamp_semantics"] = "SourceTimestamp = calculation timeline; local timestamp = computation time; ServerTimestamp unavailable";
        }
        if (options.ValueKind == JsonValueKind.Object && options.TryGetProperty("acquisition_info", out var info) && info.ValueKind == JsonValueKind.Object)
        {
            metadata["acquisition_info"] = info.Clone();
            if (info.TryGetProperty("protocols", out var protocols) && protocols.ValueKind == JsonValueKind.Array && protocols.GetArrayLength() > 0)
            {
                metadata["timestamp_order"] = "arrival";
                metadata["quality_semantics"] = "Per-row protocol quality and status code; no cross-protocol quality synthesis";
                metadata["timestamp_semantics"] = "PC UTC arrival shared axis; original transport/source/server clocks in each row";
            }
            if (info.TryGetProperty("mode", out var mode) && mode.GetString() is "symbolic" or "udp")
            {
                if (info.TryGetProperty("quality_origin", out var q)) metadata["quality_semantics"] = q.GetString();
                if (info.TryGetProperty("timestamps", out var ts)) metadata["timestamp_semantics"] = ts.GetString();
            }
        }
        SaveMetadata(false);
        worker = new Thread(Run) { Name = "table-recorder", IsBackground = true };
        worker.Start();
    }

    static string Prefix(string endpoint) =>
        endpoint.StartsWith("mixed://") ? "MIXED_" : endpoint.StartsWith("mqtt://") || endpoint.StartsWith("mqtts://") ? "MQTT_" : endpoint.StartsWith("s7://") ? "S7_" : endpoint.StartsWith("ads://") ? "ADS_" : "OPCUA_";

    public static string SourceProtocol(string endpoint) =>
        endpoint.StartsWith("mixed://") ? "mixed" : endpoint.StartsWith("mqtt://") || endpoint.StartsWith("mqtts://") ? "mqtt" : endpoint.StartsWith("s7://") ? "s7" : endpoint.StartsWith("ads://") ? "ads" : "opcua";

    [GeneratedRegex("[^A-Za-z0-9_-]+")]
    private static partial Regex SafeName();

    void OpenPart()
    {
        finalPath = Path.Combine(directory, sessionBase + "_p" + segment.ToString("0000", CultureInfo.InvariantCulture) + "." + format);
        partialPath = finalPath + ".part";
        stream = new FileStream(partialPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read, 1 << 16);
        if (format == "csv")
        {
            csv = new StreamWriter(stream, new UTF8Encoding(encoderShouldEmitUTF8Identifier: true), 1 << 16) { NewLine = "\r\n", AutoFlush = false };
            csv.Write(string.Join(",", Columns) + "\r\n");
            csv.Flush();
        }
        else parquet = new ParquetPart(stream, storage, ParquetMetadata());
        parquetBound = 0;
    }

    Dictionary<string, string> ParquetMetadata()
    {
        var meta = new Dictionary<string, string>
        {
            ["application"] = "OPC UA Web Client", ["version"] = version, ["layout"] = "long: one row per node per acquisition cycle",
            ["time_zone"] = "UTC", ["source_protocol"] = SourceProtocol(endpoint),
        };
        // json.dumps(info, ensure_ascii=False) with Python separators.
        if (acquisitionInfo is JsonElement info) meta["acquisition_info"] = PythonDumps(info);
        meta["storage"] = storage.CompactJson();
        meta["lossless"] = "true";
        meta["sample_policy"] = "all original acquisition samples";
        return meta;
    }

    /// <summary>Never waits for file I/O: a full cycle queue stops the recording explicitly.</summary>
    public bool Enqueue(double timestamp, IReadOnlyList<Sample> rows, int period)
    {
        lock (gate)
        {
            if ((string?)state["state"] != "recording") return false;
            currentPeriod = period;
            var selected = rows.Where(r => names.ContainsKey(r.NodeId)).ToList();
            if (selected.Count == 0) return true;
            var number = (long)state["cycles_received"]! + 1;
            if (queue.Count >= capacity)
            {
                state["dropped_cycles"] = (long)state["dropped_cycles"]! + 1;
                state["error"] = "Disco troppo lento: buffer di registrazione pieno. Registrazione interrotta; controllare il file parziale.";
                state["state"] = "stopping"; state["reason"] = "buffer_overflow";
                stopRequested = true;
                signal.Release();
                return false;
            }
            queue.Enqueue(new Cycle(timestamp, Stopwatch.GetElapsedTime(started).TotalMilliseconds, number, selected, period));
            state["cycles_received"] = number;
        }
        signal.Release();
        return true;
    }

    public void Fail(string message, string reason = "source_acquisition_error")
    {
        lock (gate)
        {
            if ((string?)state.GetValueOrDefault("state") != "recording") return;
            state["error"] = message; state["state"] = "stopping"; state["reason"] = reason;
            stopRequested = true;
        }
        signal.Release();
    }

    public void SourceDiagnostics(JsonElement diagnostics)
    {
        lock (gate) metadata["acquisition_final"] = diagnostics.Clone();
    }

    public void Stop(string reason, TimeSpan timeout)
    {
        lock (gate)
        {
            if ((string?)state.GetValueOrDefault("state") == "recording") { state["state"] = "stopping"; state["reason"] = reason; }
            stopRequested = true;
        }
        signal.Release();
        if (worker != null && worker != Thread.CurrentThread) worker.Join(timeout);
    }

    void Run()
    {
        var batch = new List<Row>();
        var lastFlush = Stopwatch.GetTimestamp();
        var failed = false;
        try
        {
            while (true)
            {
                signal.Wait(100);
                Cycle? cycle;
                bool stopping;
                lock (gate)
                {
                    while (queue.TryDequeue(out cycle)) batch.AddRange(Rows(cycle));
                    pendingRows = batch.Count;
                    stopping = stopRequested && queue.Count == 0;
                }
                if (batch.Count > 0 && (batch.Count >= storage.RowGroupRows || Stopwatch.GetElapsedTime(lastFlush).TotalSeconds >= storage.FlushSeconds))
                {
                    Flush(batch);
                    batch = new List<Row>();
                    lock (gate) pendingRows = 0;
                    lastFlush = Stopwatch.GetTimestamp();
                }
                if (stopping) break;
            }
            Flush(batch);
            lock (gate) pendingRows = 0;
        }
        catch (Exception exc)
        {
            failed = true;
            lock (gate)
            {
                state["error"] = "Errore di scrittura: " + exc.Message;
                state["state"] = "stopping"; state["reason"] = "write_error";
            }
        }
        finally { Finalize(failed); }
    }

    void Finalize(bool failed)
    {
        try
        {
            if (format == "parquet") { FlushParquet(); parquet?.Dispose(); }
            else csv?.Flush();
            stream?.Flush(true);
        }
        catch (Exception exc)
        {
            failed = true;
            lock (gate) if (string.IsNullOrEmpty((string?)state["error"])) state["error"] = "Impossibile finalizzare il file: " + exc.Message;
        }
        finally
        {
            try { csv?.Dispose(); stream?.Dispose(); }
            catch (Exception exc)
            {
                failed = true;
                lock (gate) if (string.IsNullOrEmpty((string?)state["error"])) state["error"] = "Impossibile chiudere il file: " + exc.Message;
            }
        }
        lock (gate)
        {
            var error = (string?)state["error"];
            if (!failed && string.IsNullOrEmpty(error) && maxBytes > 0 && new FileInfo(partialPath).Length > maxBytes)
                state["error"] = error = "Limite file superato durante la finalizzazione; file parziale conservato";
            if (!failed && string.IsNullOrEmpty(error))
            {
                try
                {
                    File.Move(partialPath, finalPath, overwrite: true);
                    partFiles.Add(new() { ["name"] = Path.GetFileName(finalPath), ["rows"] = segmentRows, ["bytes"] = new FileInfo(finalPath).Length, ["part"] = segment });
                    EngineLog.Info($"Record part finalized: {Path.GetFileName(finalPath)} rows={segmentRows}");
                }
                catch (IOException exc) { state["error"] = "Impossibile finalizzare il nome del file: " + exc.Message; }
            }
            if (!string.IsNullOrEmpty((string?)state["error"])) { state["state"] = "error"; state["path"] = partialPath; }
            else state["state"] = "stopped";
            state["ended_at"] = PythonIso(DateTime.UtcNow);
            state["duration_seconds"] = Math.Round(Stopwatch.GetElapsedTime(started).TotalSeconds, 3);
            try { SaveMetadata(false); }
            catch (IOException exc)
            {
                if (string.IsNullOrEmpty((string?)state["error"])) state["error"] = "File dati salvato; metadati non aggiornati: " + exc.Message;
                state["state"] = "error";
            }
        }
    }

    /// <summary>Recorder._rows: the long-layout values of one cycle.</summary>
    IEnumerable<Row> Rows(Cycle cycle)
    {
        var stamp = PythonFromTimestamp(cycle.Timestamp);
        foreach (var r in cycle.Rows)
        {
            double? numeric = r.Good && r.PlotValue is double p && double.IsFinite(p) ? p : null;
            yield return new Row
            {
                TimestampUtc = stamp, TimestampMs = cycle.Timestamp, ElapsedMs = cycle.Elapsed, Cycle = cycle.Number,
                NodeId = r.NodeId, SignalName = names[r.NodeId], ValueNumeric = numeric, ValueText = Values.ValueText(r.Value),
                DataType = string.IsNullOrEmpty(r.DataType) ? "Null" : r.DataType, Status = !string.IsNullOrEmpty(r.Status) ? r.Status : !string.IsNullOrEmpty(r.Error) ? r.Error : "Unknown",
                Good = r.Good, SourceTimestamp = r.SourceTs, ServerTimestamp = r.ServerTs, SampleMs = cycle.Period,
                Quality = !string.IsNullOrEmpty(r.Quality) ? r.Quality : r.QualityOrDerived, StatusCode = r.StatusCode is long code ? (uint)code : null,
                LocalTimestamp = r.LocalTs ?? PythonIsoMicro(stamp), AdsFiletime = r.SourceFiletime,
                SourceProtocol = r.SourceProtocol ?? Sample.ProtocolOf(r.NodeId),
                AcquisitionTimestampMs = r.AcquisitionTimestampMs ?? cycle.Timestamp,
                AcquisitionOrigin = string.IsNullOrEmpty(r.AcquisitionOrigin) ? "acquisition" : r.AcquisitionOrigin,
            };
        }
    }

    void CommitRows(IReadOnlyList<Row> rows)
    {
        if (rows.Count == 0) return;
        if (segmentFirst == null) { segmentFirst = rows[0].TimestampMs; segmentElapsed = rows[0].ElapsedMs; }
        var min = rows.Min(r => r.TimestampMs);
        var max = rows.Max(r => r.TimestampMs);
        segmentFirst = Math.Min(segmentFirst.Value, min);
        segmentLast = segmentLast is double last ? Math.Max(last, max) : max;
        segmentRows += rows.Count;
        foreach (var row in rows)
            if (row.Cycle != segmentLastCycle) { segmentCycles++; segmentLastCycle = row.Cycle; }
        lock (gate)
        {
            state["rows_written"] = (long)state["rows_written"]! + rows.Count;
            pendingRows = Math.Max(0, pendingRows - rows.Count);
        }
    }

    void Flush(List<Row> batch)
    {
        // Never split a complete acquisition cycle between files.
        if (format == "csv" && maxDuration == 0 && batch.Count > 0)
        {
            var block = CsvText(batch);
            if (maxBytes == 0 || stream!.Position + Encoding.UTF8.GetByteCount(block) <= maxBytes)
            {
                csv!.Write(block); CommitRows(batch); csv.Flush(); return;
            }
        }
        foreach (var group in batch.GroupBy(r => r.Cycle).Select(g => g.ToList()))
        {
            var elapsed = group[0].ElapsedMs;
            var origin = segmentElapsed ?? (parquetPending.Count > 0 ? parquetPending[0].ElapsedMs : null);
            if (origin is double o && maxDuration > 0 && elapsed - o >= maxDuration * 1000)
            {
                if (format == "parquet") FlushParquet();
                if (segmentRows > 0) Rotate();
            }
            if (format == "csv")
            {
                var block = CsvText(group);
                var size = Encoding.UTF8.GetByteCount(block);
                csv!.Flush();
                if (maxBytes > 0 && stream!.Position + size > maxBytes)
                {
                    if (segmentRows > 0) Rotate();
                    if (stream!.Position + size > maxBytes) throw new InvalidDataException("Limite file inferiore a un ciclo completo CSV; aumentare la dimensione");
                }
                csv!.Write(block);
                CommitRows(group);
            }
            else
            {
                parquetPending.AddRange(group);
                if (parquetPending.Count >= storage.RowGroupRows) FlushParquet();
            }
        }
        if (format == "parquet") FlushParquet();
        csv?.Flush();
        stream?.Flush();
    }

    void FlushParquet()
    {
        if (parquetPending.Count == 0) return;
        var rows = parquetPending.ToList();
        parquetPending.Clear();
        WriteParquetRows(rows);
        stream!.Flush();
    }

    void WriteParquetRows(List<Row> rows)
    {
        long encoded = 0;
        var reserve = Math.Max(16L * 1024 * 1024, 4L * rows.Count * 512 + 65536);
        if (maxBytes > 64L * 1024 * 1024 && stream!.Position + reserve < maxBytes)
        {
            parquet!.Write(rows);
            parquetBound = stream.Position + reserve;
            CommitRows(rows);
            return;
        }
        if (maxBytes > 0)
        {
            // Size a complete standalone row group, schema and footer included.
            encoded = ParquetPart.StandaloneSize(rows, storage, ParquetMetadata());
            if (encoded > maxBytes)
            {
                var cycles = Enumerable.Range(1, rows.Count - 1).Where(i => rows[i].Cycle != rows[i - 1].Cycle).ToList();
                if (cycles.Count == 0) throw new InvalidDataException("Limite Parquet inferiore a un ciclo completo e al suo footer; aumentare la dimensione");
                var split = cycles.MinBy(i => Math.Abs(i - rows.Count / 2.0));
                WriteParquetRows(rows[..split]);
                WriteParquetRows(rows[split..]);
                return;
            }
            var bound = segmentRows == 0 ? encoded : parquetBound + encoded + 4096;
            if (segmentRows > 0 && bound > maxBytes) Rotate();
        }
        parquet!.Write(rows);
        parquetBound = segmentRows == 0 ? encoded : parquetBound + encoded + 4096;
        CommitRows(rows);
    }

    void Rotate()
    {
        FinishPart();
        segment++;
        segmentRows = 0; segmentCycles = 0; segmentLastCycle = null;
        segmentFirst = segmentLast = segmentElapsed = null;
        OpenPart();
        lock (gate) { state["filename"] = Path.GetFileName(finalPath); state["path"] = finalPath; }
        SaveMetadata(false);
    }

    void FinishPart()
    {
        if (format == "parquet") { FlushParquet(); parquet!.Dispose(); }
        else csv!.Flush();
        stream!.Flush(true);
        csv?.Dispose(); stream.Dispose();
        if (maxBytes > 0 && new FileInfo(partialPath).Length > maxBytes)
            throw new InvalidDataException("Limite file superato durante la finalizzazione; file parziale conservato");
        File.Move(partialPath, finalPath);
        SaveMetadata(true);
        EngineLog.Info($"Record part finalized: {Path.GetFileName(finalPath)} rows={segmentRows}");
        partFiles.Add(new() { ["name"] = Path.GetFileName(finalPath), ["rows"] = segmentRows, ["bytes"] = new FileInfo(finalPath).Length, ["part"] = segment });
    }

    void SaveMetadata(bool finalized)
    {
        Dictionary<string, object?> meta;
        lock (gate)
        {
            meta = new Dictionary<string, object?>(metadata);
            foreach (var (k, v) in state) meta[k] = v;
            meta["part"] = segment;
            meta["session_rows"] = state.GetValueOrDefault("rows_written");
            meta["rows_written"] = segmentRows;
            meta["cycles_in_file"] = segmentCycles;
            if (segmentFirst is double first && segmentLast is double last)
            {
                meta["started_at"] = PythonIso(PythonFromTimestamp(first));
                meta["ended_at"] = PythonIso(PythonFromTimestamp(last));
                meta["duration_seconds"] = (last - first) / 1000;
            }
            if (finalized) { meta["state"] = "stopped"; meta["path"] = finalPath; meta["reason"] = Busy ? "rotation" : state["reason"]; }
        }
        var path = Path.ChangeExtension(finalPath, ".metadata.json");
        var temporary = Path.ChangeExtension(path, ".tmp");
        using (var file = new FileStream(temporary, FileMode.Create, FileAccess.Write))
        using (var w = new Utf8JsonWriter(file, new JsonWriterOptions { Indented = true, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping }))
            JsonOut.Write(w, meta);
        File.Move(temporary, path, overwrite: true);
    }

    public void WriteStatus(Utf8JsonWriter w)
    {
        lock (gate)
        {
            var result = new Dictionary<string, object?>(state)
            {
                ["part"] = segment,
                ["files"] = partFiles.ToList(),
                ["queue_capacity_cycles"] = capacity,
                ["buffer_seconds_effective"] = capacity * currentPeriod / 1000.0,
                ["queue_cycles"] = queue.Count,
                ["session"] = new Dictionary<string, object?> { ["session_name"] = metadata.GetValueOrDefault("session_name") ?? "", ["machine"] = metadata.GetValueOrDefault("machine") ?? "", ["operator"] = metadata.GetValueOrDefault("operator") ?? "", ["description"] = metadata.GetValueOrDefault("description") ?? "" },
                ["directory"] = directory,
                ["storage"] = storage.ToDictionary(),
                ["format_label"] = storage.Label,
                ["pending_rows"] = pendingRows,
                ["node_ids"] = nodes.Select(n => n.NodeId).ToList(),
                ["nodes"] = nodes.Select(n => (object?)new Dictionary<string, object?> { ["nodeid"] = n.NodeId, ["display_name"] = names[n.NodeId], ["node_class"] = "Variable" }).ToList(),
            };
            var busy = (string?)state["state"] is "recording" or "stopping";
            result["active"] = (string?)state["state"] == "recording";
            result["busy"] = busy;
            if (busy) result["duration_seconds"] = Math.Round(Stopwatch.GetElapsedTime(started).TotalSeconds, 3);
            var current = busy ? partialPath : state["path"] as string;
            try { result["bytes_written"] = current != null && File.Exists(current) ? new FileInfo(current).Length : 0L; }
            catch (IOException) { result["bytes_written"] = 0L; }
            JsonOut.Write(w, result);
        }
    }

    string CsvText(IEnumerable<Row> rows)
    {
        var sb = new StringBuilder();
        foreach (var row in rows)
        {
            var fields = new[]
            {
                PythonIsoMilli(row.TimestampUtc), Values.PythonRepr(row.TimestampMs), Values.PythonRepr(row.ElapsedMs),
                row.Cycle.ToString(CultureInfo.InvariantCulture), row.NodeId, row.SignalName,
                row.ValueNumeric is double v ? Values.PythonRepr(v) : "", row.ValueText ?? "", row.DataType, row.Status,
                row.Good ? "True" : "False", row.SourceTimestamp ?? "", row.ServerTimestamp ?? "",
                row.SampleMs.ToString(CultureInfo.InvariantCulture), row.Quality,
                row.StatusCode?.ToString(CultureInfo.InvariantCulture) ?? "", row.LocalTimestamp ?? "", row.AdsFiletime ?? "",
                row.SourceProtocol, Values.PythonRepr(row.AcquisitionTimestampMs), row.AcquisitionOrigin,
            };
            for (var i = 0; i < fields.Length; i++)
            {
                if (i > 0) sb.Append(',');
                AppendCsv(sb, fields[i]);
            }
            sb.Append("\r\n");
        }
        return sb.ToString();
    }

    /// <summary>csv.QUOTE_MINIMAL of the excel dialect.</summary>
    static void AppendCsv(StringBuilder sb, string field)
    {
        if (field.IndexOfAny([',', '"', '\r', '\n']) < 0) { sb.Append(field); return; }
        sb.Append('"').Append(field.Replace("\"", "\"\"")).Append('"');
    }

    /// <summary>datetime.fromtimestamp(ms/1000, utc): microseconds rounded half-to-even from the float fraction.</summary>
    public static DateTime PythonFromTimestamp(double ms)
    {
        var seconds = ms / 1000;
        var whole = Math.Truncate(seconds);
        var micro = Math.Round((seconds - whole) * 1e6, MidpointRounding.ToEven);
        if (micro >= 1e6) { whole += 1; micro -= 1e6; }
        else if (micro < 0) { whole -= 1; micro += 1e6; }
        return DateTime.UnixEpoch.AddTicks((long)whole * TimeSpan.TicksPerSecond + (long)micro * 10);
    }

    public static string PythonIso(DateTime dt) => Values.PythonIso(dt);

    static string PythonIsoMilli(DateTime dt) =>
        dt.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + (dt.Ticks % TimeSpan.TicksPerSecond / TimeSpan.TicksPerMillisecond).ToString("000", CultureInfo.InvariantCulture) + "+00:00";

    static string PythonIsoMicro(DateTime dt) =>
        dt.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + (dt.Ticks % TimeSpan.TicksPerSecond / 10).ToString("000000", CultureInfo.InvariantCulture) + "+00:00";

    static string PythonDumps(JsonElement value)
    {
        var sb = new StringBuilder();
        void Write(JsonElement e)
        {
            switch (e.ValueKind)
            {
                case JsonValueKind.Object:
                    sb.Append('{');
                    var first = true;
                    foreach (var p in e.EnumerateObject())
                    {
                        if (!first) sb.Append(", ");
                        first = false;
                        sb.Append(JsonSerializer.Serialize(p.Name, Unescaped)).Append(": ");
                        Write(p.Value);
                    }
                    sb.Append('}');
                    break;
                case JsonValueKind.Array:
                    sb.Append('[');
                    var firstItem = true;
                    foreach (var item in e.EnumerateArray())
                    {
                        if (!firstItem) sb.Append(", ");
                        firstItem = false;
                        Write(item);
                    }
                    sb.Append(']');
                    break;
                case JsonValueKind.String: sb.Append(JsonSerializer.Serialize(e.GetString(), Unescaped)); break;
                case JsonValueKind.Number:
                    var raw = e.GetRawText();
                    sb.Append(raw.IndexOfAny(['.', 'e', 'E']) >= 0 ? Values.PythonRepr(e.GetDouble()) : raw);
                    break;
                default: sb.Append(e.GetRawText()); break;
            }
        }
        Write(value);
        return sb.ToString();
    }

    static readonly JsonSerializerOptions Unescaped = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    public void Dispose()
    {
        Stop("dispose", TimeSpan.FromSeconds(10));
    }

    public sealed class Row
    {
        public DateTime TimestampUtc;
        public double TimestampMs, ElapsedMs;
        public long Cycle;
        public string NodeId = "", SignalName = "";
        public double? ValueNumeric;
        public string? ValueText;
        public string DataType = "", Status = "";
        public bool Good;
        public string? SourceTimestamp, ServerTimestamp;
        public int SampleMs;
        public string Quality = "";
        public uint? StatusCode;
        public string? LocalTimestamp, AdsFiletime;
        public string SourceProtocol = "";
        public double AcquisitionTimestampMs;
        public string AcquisitionOrigin = "";
    }
}
