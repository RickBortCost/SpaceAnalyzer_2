using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// Export of a primary capture (.sac, .sac.part, .incomplete.sac, .recovered.sac) to CSV, Apache Parquet, FDA DAS4 or RBF.
/// The primary capture holds every original sample of a recording; this is the way back to a readable file when the format
/// written during Record is missing or incomplete (crash, error). A damaged or truncated tail ends the export instead of failing it:
/// every sample of the valid blocks is written and the result carries a warning.
/// </summary>
public static class PrimaryExport
{
    public static readonly string[] Formats = ["csv", "parquet", "das", "rbf"];

    public sealed record Result(string Format, long Samples, int Channels, List<Dictionary<string, object?>> Files, bool Finalized, string? Warning, double? First, double? Last);

    /// <summary>A file name (no folders) of a primary capture.</summary>
    public static bool IsPrimaryName(string? name) =>
        !string.IsNullOrEmpty(name) && name == Path.GetFileName(name) && (name.EndsWith(".sac", StringComparison.Ordinal) || name.EndsWith(".sac.part", StringComparison.Ordinal));

    /// <summary>Entry of the Conversion page list: header information only (a capture can be tens of GB, so the content is not scanned).</summary>
    public static Dictionary<string, object?> Describe(string path)
    {
        var info = new FileInfo(path);
        var name = info.Name;
        var state = name.EndsWith(".incomplete.sac", StringComparison.Ordinal) ? "incomplete" : name.EndsWith(".recovered.sac", StringComparison.Ordinal) ? "recovered"
            : name.EndsWith(".sac.part", StringComparison.Ordinal) ? "part" : "complete";
        var label = state switch
        {
            "incomplete" => "Archivio primario incompleto (.sac)", "part" => "Archivio primario interrotto (.sac.part)", "recovered" => "Archivio primario recuperato (.sac)",
            _ => "Archivio primario (.sac)",
        };
        var entry = new Dictionary<string, object?>
        {
            ["source"] = "primary", ["name"] = name, ["display_name"] = name, ["format"] = "sac", ["format_label"] = label, ["state"] = state,
            ["size_bytes"] = info.Length, ["signals"] = new List<object?>(),
        };
        try
        {
            using var reader = new SacReader(path);
            var header = reader.Header.RootElement;
            entry["signals"] = reader.Channels.Select(c => (object?)new Dictionary<string, object?>
            {
                ["nodeid"] = c.NodeId, ["display_name"] = c.Name,
                ["unit"] = c.Raw is JsonElement raw && raw.TryGetProperty("unit", out var unit) && unit.ValueKind == JsonValueKind.String ? unit.GetString() : "",
            }).ToList();
            if (header.TryGetProperty("started_ns", out var started) && long.TryParse(started.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var ns))
                entry["started_at"] = Values.IsoFromMs(ns / 1_000_000.0);
            if (header.TryGetProperty("session", out var session) && session.ValueKind == JsonValueKind.Object
                && session.TryGetProperty("session_name", out var sessionName) && sessionName.ValueKind == JsonValueKind.String && sessionName.GetString() is { Length: > 0 } title)
                entry["display_name"] = title + " · " + name;
        }
        catch (Exception exc) when (exc is InvalidDataException or IOException or JsonException or KeyNotFoundException)
        {
            entry["read_error"] = "Archivio primario non leggibile: " + exc.Message;
        }
        return entry;
    }

    /// <summary>Folder of the captures for a recordings folder (or the folder of an arbitrary capture).</summary>
    public static string DefaultOutput(string capturePath)
    {
        var folder = Path.GetDirectoryName(Path.GetFullPath(capturePath))!;
        if (Path.GetFileName(folder) == ".primary") folder = Path.GetDirectoryName(folder)!;
        return Path.Combine(folder, Conversions.Folder);
    }

    public static Result Export(string path, string format, string output, string version, IReadOnlyCollection<string>? signalIds = null, double? start = null, double? end = null,
        Action<long>? progress = null, CancellationToken cancel = default)
    {
        if (!Formats.Contains(format)) throw new UserError("Formato di destinazione non valido: scegliere csv, parquet, das o rbf");
        if (!File.Exists(path)) throw new UserError("Archivio primario non trovato: " + path);
        using var reader = new SacReader(path);
        var header = reader.Header.RootElement;
        var channels = reader.Channels.Where(c => signalIds == null || signalIds.Contains(c.NodeId)).ToList();
        if (channels.Count == 0) throw new UserError("Nessuno dei segnali selezionati è presente nell'archivio");
        if (format is "das" or "rbf" && channels.Count > Conversions.MaxLegacySignals)
            throw new UserError($"{Conversions.Targets[format]}: selezionare al massimo {Conversions.MaxLegacySignals} segnali (presenti {channels.Count}); per più segnali usare CSV o Parquet");
        var keep = channels.Select(c => c.NodeId).ToHashSet();
        var endpoint = header.TryGetProperty("endpoint", out var ep) && ep.ValueKind == JsonValueKind.String ? ep.GetString() ?? "" : "";
        var options = Options(header);
        var sourceName = Path.GetFileName(path);
        Directory.CreateDirectory(output);
        var work = Path.Combine(output, ".esportazione_" + Guid.NewGuid().ToString("N")[..8]);
        Directory.CreateDirectory(work);
        var moved = new List<string>();
        try
        {
            IFormatRecorder? writer = null;
            long rows = 0;
            double? first = null, last = null, current = null;
            string? damage = null;
            var group = new List<Sample>();
            var period = 1;

            void Flush()
            {
                if (group.Count == 0) return;
                // The reader is faster than the disk: wait for room (a live recording would drop here and fail).
                while (!writer!.HasRoom)
                {
                    if (writer.Error.Length > 0) throw new InvalidOperationException(writer.Error);
                    cancel.ThrowIfCancellationRequested();
                    Thread.Sleep(2);
                }
                if (!writer.Enqueue(current!.Value, group, period)) throw new InvalidOperationException(writer.Error.Length > 0 ? writer.Error : "Scrittura non riuscita");
                rows += group.Count;
                group = new List<Sample>();
                progress?.Invoke(rows);
            }

            foreach (var (t, samplePeriod, sample) in reader.TolerantSamples(message => damage = message))
            {
                cancel.ThrowIfCancellationRequested();
                if (!keep.Contains(sample.NodeId)) continue;
                if (start != null && t < start || end != null && t > end) continue;
                // Notifications are kept only in the primary capture; FDA and RBF are fixed-rate formats.
                if (format is "das" or "rbf" && sample.AcquisitionOrigin == "notification") continue;
                if (writer == null)
                {
                    writer = FormatRecorders.Create(format, work, version);
                    writer.Start(format, channels, endpoint, Math.Max(1, samplePeriod), options);
                }
                if (current != t) { Flush(); current = t; period = samplePeriod; }
                group.Add(sample);
                first ??= t;
                last = t;
            }
            if (writer == null) throw new UserError(damage != null
                ? "Nessun campione leggibile nell'archivio primario: " + damage
                : "Nessun campione da esportare nella selezione (segnali e intervallo)");
            Flush();
            writer.Stop("primary_export", Timeout.InfiniteTimeSpan);
            if (writer.Error.Length > 0) throw new InvalidOperationException(writer.Error);
            var status = writer.StatusJson();
            var produced = status.GetProperty("files").EnumerateArray().Select(f => (name: f.GetProperty("name").GetString()!, rows: f.TryGetProperty("rows", out var r) && r.TryGetInt64(out var n) ? n : 0L)).ToList();
            if (produced.Count == 0) throw new InvalidDataException("L'esportazione non ha prodotto file");
            var files = new List<Dictionary<string, object?>>();
            foreach (var (name, fileRows) in produced)
            {
                var source = Path.Combine(work, name);
                var target = Conversions.Unique(Path.Combine(output, name));
                var sidecar = RecordingLibrary.MetadataPath(source);
                if (File.Exists(sidecar)) File.WriteAllText(RecordingLibrary.MetadataPath(target), Retarget(File.ReadAllText(sidecar), sourceName));
                File.Move(source, target);
                moved.Add(target);
                files.Add(new()
                {
                    ["name"] = Conversions.Folder + "/" + Path.GetFileName(target), ["file"] = Path.GetFileName(target), ["path"] = target,
                    ["bytes"] = new FileInfo(target).Length, ["rows"] = fileRows, ["format"] = Path.GetExtension(target).TrimStart('.').ToLowerInvariant(),
                });
            }
            string? warning = null;
            if (damage != null) warning = $"Archivio troncato o danneggiato dopo il campione {rows:N0}: esportati i campioni validi ({damage})";
            else if (!reader.Finalized) warning = "Archivio non chiuso correttamente (manca il blocco finale): esportati tutti i campioni leggibili";
            else if (reader.Footer is JsonElement footer && footer.TryGetProperty("integrity", out var integrity) && integrity.GetString() != "complete")
            {
                var reason = footer.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String ? error.GetString() : "";
                var lost = footer.TryGetProperty("lost_samples", out var lostSamples) && lostSamples.TryGetInt64(out var n) ? n : 0;
                warning = $"Archivio chiuso con errore ({reason}): {lost:N0} campioni non sono arrivati all'archivio; esportati i {rows:N0} presenti";
            }
            return new Result(format, rows, channels.Count, files, reader.Finalized, warning, first, last);
        }
        catch
        {
            foreach (var file in moved)
            {
                try { File.Delete(file); File.Delete(RecordingLibrary.MetadataPath(file)); } catch (Exception) { }
            }
            throw;
        }
        finally
        {
            try { Directory.Delete(work, recursive: true); } catch (Exception) { }
        }
    }

    /// <summary>Session labels, acquisition information and formulas of the capture header, as recording options.</summary>
    static JsonElement Options(JsonElement header)
    {
        var options = new JsonObject();
        if (header.TryGetProperty("session", out var session) && session.ValueKind == JsonValueKind.Object)
            foreach (var key in new[] { "session_name", "machine", "operator", "description" })
                if (session.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String) options[key] = value.GetString();
        foreach (var key in new[] { "acquisition_info", "calculation_formulas" })
            if (header.TryGetProperty(key, out var value)) options[key] = JsonNode.Parse(value.GetRawText());
        options["_queue_capacity_cycles"] = 5000;   // write queue: the export waits for room, so only the burst absorbed in memory is bounded
        using var document = JsonDocument.Parse(options.ToJsonString());
        return document.RootElement.Clone();
    }

    /// <summary>The sidecar refers to the primary capture it was exported from; an intermediate Parquet is never kept.</summary>
    static string Retarget(string json, string sourceName)
    {
        var node = JsonNode.Parse(json) as JsonObject ?? new JsonObject();
        node.Remove("original_archive");
        node["converted_from"] = sourceName;
        node["converted_at"] = Values.PythonIso(DateTime.UtcNow);
        return node.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
    }
}
