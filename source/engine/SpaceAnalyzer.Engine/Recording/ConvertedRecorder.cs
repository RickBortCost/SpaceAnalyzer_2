using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// FDA (.das) and RBF: lossless Parquet original during Record; each finalized part is
/// converted in the background, one conversion at a time (port of converted_recording.py).
/// </summary>
public sealed class ConvertedRecorder(string directory, string version) : IFormatRecorder
{
    static readonly SemaphoreSlim ExportSlot = new(1, 1);

    readonly TableRecorder original = new(directory, version);
    readonly object gate = new();
    readonly List<Dictionary<string, object?>> files = new();
    Thread? worker;
    string format = "", title = "", suffix = "", phaseKey = "";
    string error = "", phase = "idle";
    List<NodeSpec> nodes = new();
    long maxBytes;

    public bool Busy => original.Busy || worker is { IsAlive: true };
    public string Error { get { lock (gate) return error.Length > 0 ? error : original.Error; } }

    public void Start(string format, IReadOnlyList<NodeSpec> selected, string endpoint, int sampleMs, JsonElement options)
    {
        this.format = format;
        (title, suffix, phaseKey) = format == "das" ? ("FDA DAS4", "_FDA0001.das", "fda_phase") : ("RBF v3", "_RBF0001.rbf", "rbf_phase");
        if (format == "rbf" && selected.Count > 64) throw new ArgumentException("RBF: massimo 64 variabili selezionate");
        nodes = selected.ToList();
        var merged = options.ValueKind == JsonValueKind.Object ? JsonNode.Parse(options.GetRawText())!.AsObject() : new JsonObject();
        var empty = merged.Count == 0;
        merged["parquet_profile"] = "fast";
        if (empty) merged["_queue_capacity_cycles"] = 2000;
        using var document = JsonDocument.Parse(merged.ToJsonString());
        original.Start("parquet", selected, endpoint, sampleMs, document.RootElement.Clone());
        maxBytes = document.RootElement.TryGetProperty("max_file_mb", out var mb) && mb.ValueKind == JsonValueKind.Number ? (long)(mb.GetDouble() * 1024 * 1024) : 1024L * 1024 * 1024;
        phase = "recording";
        worker = new Thread(Run) { Name = title + "-Finalizer", IsBackground = true };
        worker.Start();
    }

    public bool Enqueue(double timestamp, IReadOnlyList<Sample> rows, int period) => original.Enqueue(timestamp, rows, period);
    public void Fail(string message, string reason = "source_acquisition_error") => original.Fail(message, reason);
    public void SourceDiagnostics(JsonElement diagnostics) => original.SourceDiagnostics(diagnostics);

    public void Stop(string reason, TimeSpan timeout)
    {
        var started = DateTime.UtcNow;
        original.Stop(reason, timeout);
        // Timeout.InfiniteTimeSpan is -1 ms: never clamp it to zero.
        var left = timeout == Timeout.InfiniteTimeSpan ? Timeout.InfiniteTimeSpan : TimeSpan.FromTicks(Math.Max(0, (timeout - (DateTime.UtcNow - started)).Ticks));
        if (worker != null && worker != Thread.CurrentThread) worker.Join(left);
    }

    List<Dictionary<string, object?>> Export(string path) =>
        format == "das" ? FdaExporter.Export(path, nodes, maxBytes) : RbfExporter.Export(path, nodes, maxBytes);

    void Run()
    {
        var seen = new HashSet<string>();
        try
        {
            // Convert rotated, finalized parts during acquisition; never read a .part.
            while (true)
            {
                foreach (var part in original.PartFiles)
                {
                    var name = (string)part["name"]!;
                    if (seen.Contains(name)) continue;
                    lock (gate) phase = "queued";
                    ExportSlot.Wait();
                    try
                    {
                        lock (gate) phase = "converting";
                        var result = Export(Path.Combine(directory, name));
                        lock (gate) files.AddRange(result);
                    }
                    finally { ExportSlot.Release(); }
                    seen.Add(name);
                }
                if (!original.Busy)
                {
                    if (original.Error.Length > 0) throw new InvalidDataException("Acquisizione originale incompleta: " + original.Error + "; file parziale conservato");
                    if (original.PartFiles.Any(p => !seen.Contains((string)p["name"]!))) continue;
                    break;
                }
                lock (gate) phase = "recording";
                Thread.Sleep(50);
            }
            lock (gate) phase = "ready";
        }
        catch (Exception exc)
        {
            lock (gate) { error = "Errore generazione " + title + ": " + exc.Message + "; archivio Parquet originale conservato"; phase = "error"; }
        }
    }

    public void WriteStatus(Utf8JsonWriter w)
    {
        using var document = JsonDocument.Parse(SacFormat.Json(original.WriteStatus));
        var state = document.RootElement;
        var result = new Dictionary<string, object?>();
        foreach (var property in state.EnumerateObject()) result[property.Name] = property.Value.Clone();
        List<Dictionary<string, object?>> converted;
        string currentError, currentPhase;
        lock (gate) { converted = files.ToList(); currentError = error; currentPhase = phase; }
        var active = original.Busy;
        var busy = active || worker is { IsAlive: true };
        var originalPath = state.GetProperty("path").GetString() ?? "";
        var originalState = state.GetProperty("state").GetString();
        var originalError = state.GetProperty("error").GetString() ?? "";
        var filename = converted.Count > 0 ? (string)converted[^1]["name"]! : "";
        result["format"] = format;
        result["format_label"] = title + " + Parquet originali";
        result["storage"] = new Dictionary<string, object?> { ["format"] = format, ["label"] = title + " + Parquet originali", ["lossless"] = false, ["original_archive_lossless"] = true };
        result["original_files"] = state.GetProperty("files").Clone();
        result["original_path"] = originalPath;
        result["files"] = converted;
        result["filename"] = filename.Length > 0 ? filename : Path.GetFileNameWithoutExtension(originalPath) + suffix;
        result["path"] = filename.Length > 0 ? Path.Combine(directory, filename) : originalPath;
        result["state"] = originalState == "recording" ? "recording" : busy ? "stopping" : currentError.Length > 0 || originalError.Length > 0 ? "error" : "stopped";
        result["busy"] = busy;
        result["active"] = originalState == "recording";
        result["error"] = currentError.Length > 0 ? currentError : originalError;
        result["conversion_phase"] = currentPhase;
        result["status_note"] = currentPhase == "recording" && originalState == "recording" ? "Originali in acquisizione; parti chiuse convertite in background."
            : currentPhase == "recording" ? "Salvataggio originali in background."
            : currentPhase == "converting" ? "Generazione " + title + " in corso; archivio originale gia salvato."
            : currentPhase == "ready" ? title + " generato; conservare anche archivio Parquet e metadati." : "";
        result[phaseKey] = currentPhase;
        JsonOut.Write(w, result);
    }
}
