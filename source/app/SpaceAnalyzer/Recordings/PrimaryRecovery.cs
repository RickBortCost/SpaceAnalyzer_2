using System.Buffers.Binary;
using System.IO.Compression;
using System.IO.Hashing;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>Startup recovery of interrupted primary captures and their bounded catalog (primary_recovery.py).</summary>
public sealed class PrimaryRecovery(string directory, EventStore events)
{
    readonly object gate = new();
    List<object?> results = new();
    bool running;

    public bool Start()
    {
        lock (gate)
        {
            if (running) return false;
            running = true;
        }
        new Thread(Run) { Name = "primary-recovery", IsBackground = true }.Start();
        return true;
    }

    void Run()
    {
        try
        {
            var parts = Directory.Exists(directory) ? Directory.GetFiles(directory, "*.sac.part").OrderBy(p => p, StringComparer.Ordinal).ToList() : new List<string>();
            foreach (var path in parts)
            {
                Dictionary<string, object?> result;
                try
                {
                    result = Recover(path);
                    if (result.TryGetValue("recovered", out var r) && r is true) events.Emit("primary_recovered", null, result);
                }
                catch (Exception exc)
                {
                    result = new() { ["source"] = path, ["recovered"] = false, ["integrity"] = "unrecoverable_header_or_io_error", ["error"] = exc.Message };
                    events.Emit("primary_recovery_failed", null, result);
                }
                lock (gate) results = results.Append(result).TakeLast(256).ToList();
            }
        }
        finally { lock (gate) running = false; }
    }

    public Dictionary<string, object?> Status()
    {
        lock (gate) return new() { ["running"] = running, ["results"] = results.ToList() };
    }

    public List<object?> Catalog(int limit = 100)
    {
        if (!Directory.Exists(directory)) return new();
        return new DirectoryInfo(directory).EnumerateFiles("*.sac*").OrderByDescending(f => f.LastWriteTimeUtc)
            .Where(f => f.Name.EndsWith(".sac", StringComparison.Ordinal) || f.Name.EndsWith(".part", StringComparison.Ordinal))
            .Select(f => (object?)new Dictionary<string, object?> { ["filename"] = f.Name, ["bytes"] = f.Length, ["incomplete"] = f.Name.EndsWith(".part", StringComparison.Ordinal) })
            .Take(limit).ToList();
    }

    sealed record Report(long Samples, long Blocks, long ValidBytes, long FileBytes, string Error, long EventsCount, bool Finalized, JsonNode? Footer, long? FooterOffset);

    /// <summary>primary_format.inspect: every valid block up to the first damaged one.</summary>
    static Report Inspect(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var prefix = new byte[20];
        if (stream.ReadAtLeast(prefix, 20, false) != 20) throw new UserError("Header primario troncato");
        var size = BinaryPrimitives.ReadUInt32LittleEndian(prefix.AsSpan(12));
        if (!prefix.AsSpan(0, 8).SequenceEqual(SacFormat.Magic) || BinaryPrimitives.ReadUInt32LittleEndian(prefix.AsSpan(8)) != SacFormat.Version || size == 0 || size > SacFormat.MaxHeader)
            throw new UserError("Formato/versione primario non supportato");
        var header = new byte[size];
        if (stream.ReadAtLeast(header, (int)size, false) != size || Crc32.HashToUInt32(header) != BinaryPrimitives.ReadUInt32LittleEndian(prefix.AsSpan(16)))
            throw new UserError("Checksum header primario non valido");
        var meta = JsonNode.Parse(header) as JsonObject;
        if (meta?["channels"] is not JsonArray channels) throw new UserError("Schema primario non valido");
        long count = 0, blocks = 0, valid = stream.Position, events = 0;
        var error = "";
        var finished = false;
        JsonNode? footer = null;
        long? footerOffset = null;
        ulong expected = 1, next = 1;
        try
        {
            var block = new byte[36];
            var crcBytes = new byte[4];
            while (true)
            {
                var offset = stream.Position;
                var read = stream.ReadAtLeast(block, 36, false);
                if (read == 0) break;
                if (read != 36) throw new UserError("Header blocco incompleto a " + offset);
                var kind = block[4];
                var flags = block[5];
                var sequence = BinaryPrimitives.ReadUInt64LittleEndian(block.AsSpan(8));
                var first = BinaryPrimitives.ReadUInt64LittleEndian(block.AsSpan(16));
                var samples = BinaryPrimitives.ReadUInt32LittleEndian(block.AsSpan(24));
                var stored = BinaryPrimitives.ReadUInt32LittleEndian(block.AsSpan(28));
                var raw = BinaryPrimitives.ReadUInt32LittleEndian(block.AsSpan(32));
                if (!block.AsSpan(0, 4).SequenceEqual("SAB1"u8) || kind is < 1 or > 3 || flags > 1 || block[6] != 0 || block[7] != 0 || sequence != expected || first != next)
                    throw new UserError("Sequenza/schema blocco non valido a " + offset);
                if (stored == 0 || stored > SacFormat.MaxPayload || raw == 0 || raw > SacFormat.MaxPayload) throw new UserError("Dimensione blocco non valida");
                var body = new byte[stored];
                if (stream.ReadAtLeast(crcBytes, 4, false) != 4 || stream.ReadAtLeast(body, (int)stored, false) != stored) throw new UserError("Payload blocco incompleto a " + offset);
                var crc = new Crc32();
                crc.Append(block);
                crc.Append(body);
                if (crc.GetCurrentHashAsUInt32() != BinaryPrimitives.ReadUInt32LittleEndian(crcBytes)) throw new UserError("Checksum blocco non valido a " + offset);
                if (flags == 1)
                {
                    using var z = new ZLibStream(new MemoryStream(body), CompressionMode.Decompress);
                    using var inflated = new MemoryStream();
                    z.CopyTo(inflated);
                    body = inflated.ToArray();
                }
                if (body.Length != raw) throw new UserError("Lunghezza blocco non valida");
                var value = JsonNode.Parse(body);
                if (finished) throw new UserError("Dati dopo finalizzazione");
                if (kind == SacFormat.Data)
                {
                    if (value is not JsonArray rows || rows.Count != samples) throw new UserError("Conteggio/schema campioni non valido");
                    foreach (var row in rows)
                    {
                        var channel = row is JsonArray r && r.Count > 2 ? r[2] : null;
                        if (channel == null || !channel.IsNumber() || channel.ToJsonString().Contains('.') || channel.AsDouble() < 0 || channel.AsDouble() >= channels.Count)
                            throw new UserError("Canale primario non valido");
                    }
                }
                else if (samples != 0) throw new UserError("Blocco di controllo con campioni");
                if (kind == SacFormat.Event) events++;
                finished = kind == SacFormat.Finish;
                footer = finished ? value : null;
                footerOffset = finished ? offset : null;
                blocks++; count += samples; valid = stream.Position;
                expected++; next += samples;
            }
        }
        catch (Exception exc) when (exc is UserError or InvalidDataException or System.Text.Json.JsonException) { error = exc.Message; }
        var length = new FileInfo(path).Length;
        return new Report(count, blocks, valid, length, error, events, finished, footer, footerOffset);
    }

    /// <summary>primary_tools inspect: integrity report plus the original source values (bounded).</summary>
    public static Dictionary<string, object?> InspectReport(string path, int maxValues = 100000)
    {
        var report = Inspect(path);
        var values = new List<object?>();
        if (report.Error.Length == 0)
        {
            using var reader = new SacReader(path);
            foreach (var (_, _, sample) in reader.Samples())
            {
                if (values.Count >= maxValues) break;
                values.Add(sample.HasSourceValue ? sample.SourceValue : sample.Value);
            }
        }
        return new()
        {
            ["samples"] = report.Samples, ["blocks"] = report.Blocks, ["valid_bytes"] = report.ValidBytes, ["file_bytes"] = report.FileBytes,
            ["discarded_bytes"] = report.FileBytes - report.ValidBytes, ["error"] = report.Error, ["events_count"] = report.EventsCount,
            ["finalized"] = report.Finalized, ["footer"] = report.Footer?.DeepClone(), ["source_values"] = values,
        };
    }

    public static Dictionary<string, object?> Recover(string path, string? output = null)
    {
        FileStream ownership;
        try { ownership = new FileStream(path + ".lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException exc) { return new() { ["source"] = path, ["recovered"] = false, ["state"] = "writer_active", ["error"] = exc.Message }; }
        using (ownership)
        {
            var report = Inspect(path);
            var integrity = report.Finalized && report.Error.Length == 0 ? report.Footer?["integrity"]?.ToString() ?? "unknown" : "recovered_incomplete";
            var name = Path.GetFileName(path);
            output ??= Path.Combine(Path.GetDirectoryName(path)!, (name.EndsWith(".part", StringComparison.Ordinal) ? name[..^5] : name) + ".recovered.sac");
            var marker = path + ".recovery.json";
            var info = new FileInfo(path);
            var fingerprint = new List<object?> { info.Length, info.LastWriteTimeUtc.Ticks };
            if (File.Exists(marker) && Py.ReadObject(marker) is JsonObject previous
                && previous["source_fingerprint"]?.ToJsonString() == Py.DumpsPlain(fingerprint) && File.Exists(previous["output"].Str() ?? ""))
                return (Dictionary<string, object?>)Py.Plain(previous)!;
            if (File.Exists(output)) output = Path.Combine(Path.GetDirectoryName(output)!, Path.GetFileNameWithoutExtension(output) + "_" + Guid.NewGuid().ToString("N")[..8] + Path.GetExtension(output));
            var temporary = output + ".recovery_" + Guid.NewGuid().ToString("N") + ".part";
            try
            {
                using (var source = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var target = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write))
                {
                    var remaining = report.Finalized && report.Error.Length > 0 ? report.FooterOffset!.Value : report.ValidBytes;
                    var buffer = new byte[1 << 20];
                    while (remaining > 0)
                    {
                        var n = source.Read(buffer, 0, (int)Math.Min(buffer.Length, remaining));
                        if (n == 0) throw new UserError("Sorgente modificata durante recovery");
                        target.Write(buffer, 0, n);
                        remaining -= n;
                    }
                    if (!report.Finalized || report.Error.Length > 0)
                    {
                        var payload = SacFormat.Json(w =>
                        {
                            w.WriteStartObject();
                            w.WriteString("integrity", integrity);
                            w.WriteString("reason", "crash_or_incomplete_tail");
                            w.WriteNumber("recovered_samples", report.Samples);
                            w.WriteNumber("discarded_bytes", report.FileBytes - report.ValidBytes);
                            w.WriteBoolean("unknown_uncommitted_loss", true);
                            w.WriteEndObject();
                        });
                        SacFormat.WriteBlock(target, SacFormat.Finish, (ulong)(report.Blocks + (report.Finalized ? 0 : 1)), (ulong)report.Samples + 1, 0, payload, false);
                    }
                    target.Flush(true);
                }
                var after = new FileInfo(path);
                if (after.Length != info.Length || after.LastWriteTimeUtc.Ticks != info.LastWriteTimeUtc.Ticks) throw new UserError("Sorgente modificata durante recovery");
                File.Move(temporary, output);
                var result = new Dictionary<string, object?>
                {
                    ["samples"] = report.Samples, ["blocks"] = report.Blocks, ["valid_bytes"] = report.ValidBytes, ["file_bytes"] = report.FileBytes,
                    ["discarded_bytes"] = report.FileBytes - report.ValidBytes, ["error"] = report.Error, ["events_count"] = report.EventsCount,
                    ["finalized"] = report.Finalized, ["footer_offset"] = report.FooterOffset,
                    ["source"] = path, ["output"] = output, ["source_fingerprint"] = fingerprint, ["integrity"] = integrity,
                    ["unknown_uncommitted_loss"] = integrity != "complete", ["recovered"] = true,
                };
                Py.AtomicPlain(marker, result);
                return result;
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }
}
