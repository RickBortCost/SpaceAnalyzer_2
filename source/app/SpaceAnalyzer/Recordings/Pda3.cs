using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// Viewing of iba DAT PDA3 files of the single validated 64-channel model (pda3/*.py).
/// Only reading: DAT recording was removed in 3.0.
/// </summary>
public sealed class Pda3Reader
{
    public const long MaxFile = 256L * 1024 * 1024;
    const int MaxBlocks = 100000;
    const long MaxSamplesPerBlock = 2000000;

    public readonly record struct Token(int Offset, int Payload, int End, int Count, bool Literal);
    public readonly record struct Block(int Index, int Page, int Offset, int Size, long Samples);
    public readonly record struct Page(int Offset, int End, long NextOffset, long Count);

    public byte[] Raw { get; }
    public string Sha256 { get; }
    public List<Block> Blocks { get; } = new();
    public List<Page> Pages { get; } = new();
    public ulong StartTicks { get; }
    public ulong EndTicks { get; }
    public int DataStart { get; }

    public Pda3Reader(string path)
    {
        if (new FileInfo(path).Length > MaxFile) throw new UserError("PDA3: file oltre il limite di analisi di 256 MiB");
        var raw = File.ReadAllBytes(path);
        if (raw.Length < 96 || raw.Length > MaxFile || !raw.AsSpan(0, 4).SequenceEqual("PDA3"u8)) throw new UserError("PDA3: firma o dimensione non supportata");
        if (BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(8)) != 64) throw new UserError("PDA3: variante header non supportata");
        Raw = raw;
        Sha256 = Convert.ToHexString(SHA256.HashData(raw)).ToLowerInvariant();
        StartTicks = BinaryPrimitives.ReadUInt64LittleEndian(raw.AsSpan(40));
        EndTicks = BinaryPrimitives.ReadUInt64LittleEndian(raw.AsSpan(48));
        if (EndTicks <= StartTicks) throw new UserError("PDA3: intervallo temporale non valido");
        DataStart = checked(80 + (int)BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(72)));
        var footer = (long)BinaryPrimitives.ReadUInt64LittleEndian(raw.AsSpan(16));
        long cursor = DataStart;
        var seen = new HashSet<long>();
        if (!(80 <= cursor && cursor < footer && footer < raw.Length)) throw new UserError("PDA3: offset iniziali non validi");
        while (true)
        {
            if (seen.Contains(footer) || footer + 16 > raw.Length) throw new UserError("PDA3: catena indici ciclica o troncata");
            seen.Add(footer);
            var next = (long)BinaryPrimitives.ReadUInt64LittleEndian(raw.AsSpan((int)footer));
            var count = (long)BinaryPrimitives.ReadUInt64LittleEndian(raw.AsSpan((int)footer + 8));
            if (count < 1 || count > 256) throw new UserError("PDA3: dimensione pagina indice non supportata");
            var end = footer + 16 + count * 16;
            if (end > raw.Length) throw new UserError("PDA3: pagina indice troncata");
            var number = 0;
            while (cursor < footer)
            {
                if (cursor + 4 > footer || Blocks.Count >= MaxBlocks) throw new UserError("PDA3: catena blocchi non valida");
                var length = BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan((int)cursor));
                if (length == 0 || cursor + 4 + length > footer) throw new UserError("PDA3: confine blocco non valido");
                var block = new Block(Blocks.Count, Pages.Count, (int)cursor, (int)length, 0);
                long total = 0;
                foreach (var token in Tokens(block))
                {
                    total += token.Count;
                    if (total > MaxSamplesPerBlock) throw new UserError("PDA3: espansione RLE oltre limite");
                }
                Blocks.Add(block with { Samples = total });
                number++;
                cursor += 4 + length;
            }
            if (cursor != footer || number != count) throw new UserError("PDA3: numero blocchi e indice non corrispondenti");
            Pages.Add(new Page((int)footer, (int)end, next, count));
            cursor = end;
            if (next == 0)
            {
                if (end != raw.Length) throw new UserError("PDA3: dati non riconosciuti dopo l’ultimo indice");
                break;
            }
            if (!(end < next && next < raw.Length)) throw new UserError("PDA3: riferimento pagina successiva non valido");
            footer = next;
        }
    }

    public IEnumerable<Token> Tokens(Block block)
    {
        var p = block.Offset + 4;
        var end = p + block.Size;
        while (p < end)
        {
            var offset = p;
            var op = Raw[p];
            p++;
            var payload = p;
            int count;
            bool literal;
            if (op == 127)
            {
                if (p + 6 > end) throw new UserError("PDA3: RLE16 troncato");
                count = BinaryPrimitives.ReadUInt16LittleEndian(Raw.AsSpan(p + 4));
                p += 6; literal = false;
            }
            else if (op is >= 1 and <= 126) { count = op; p += 4; literal = false; }
            else if (op >= 129) { count = op - 128; p += 4 * count; literal = true; }
            else throw new UserError($"PDA3: record RLE 0x{op:x2} non supportato all’offset {offset}");
            if (count == 0 || p > end) throw new UserError("PDA3: record RLE non valido o troncato");
            yield return new Token(offset, payload, p, count, literal);
        }
        if (p != end) throw new UserError("PDA3: confine RLE non rispettato");
    }

    public IEnumerable<float> Values(Block block)
    {
        foreach (var t in Tokens(block))
        {
            if (t.Literal)
                for (var i = t.Payload; i < t.End; i += 4) yield return BinaryPrimitives.ReadSingleLittleEndian(Raw.AsSpan(i));
            else
            {
                var v = BinaryPrimitives.ReadSingleLittleEndian(Raw.AsSpan(t.Payload));
                for (var i = 0; i < t.Count; i++) yield return v;
            }
        }
    }
}

public static class Pda3
{
    static readonly byte[] ModelEndTicks = Convert.FromHexString("20e1dd2b6a2fdf08");
    static readonly Lazy<JsonObject> ProfileData = new(() =>
    {
        using var stream = typeof(Pda3).Assembly.GetManifestResourceStream("pda3_profile.json")!;
        return (JsonObject)JsonNode.Parse(stream)!;
    });
    static readonly ConcurrentDictionary<string, Pda3Reader> Readers = new();
    static readonly ConcurrentDictionary<string, JsonObject> PartInfo = new();

    public static JsonObject Profile => ProfileData.Value;

    public static bool IsPda3(string path)
    {
        using var stream = File.OpenRead(path);
        var head = new byte[4];
        return stream.ReadAtLeast(head, 4, false) == 4 && head.AsSpan().SequenceEqual("PDA3"u8);
    }

    public static long ValidSampleCount(Pda3Reader reader)
    {
        var delta = reader.EndTicks - reader.StartTicks;
        var count = delta / 100000;
        if (delta % 100000 != 0 || count < 1 || count > 60000) throw new UserError("PDA3: durata fuori dal profilo 10 ms / massimo 600 s");
        return (long)count;
    }

    static string Hex(byte[] data) => Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();

    public static void ValidateProfile(Pda3Reader reader)
    {
        var p = Profile;
        ValidSampleCount(reader);
        var prefix = reader.Raw.AsSpan(0, reader.DataStart).ToArray();
        ModelEndTicks.CopyTo(prefix, 48);
        using var index = new MemoryStream();
        foreach (var page in reader.Pages) index.Write(reader.Raw, page.Offset, page.End - page.Offset);
        if (Hex(prefix) != p["metadata_sha256"].Str() || Hex(index.ToArray()) != p["index_sha256"].Str())
            throw new UserError("PDA3: metadati/indice non corrispondono al modello 64 canali validato; variante non supportata");
        var sizes = p["block_sizes"]!.AsArray().Select(n => n!.AsLong()).ToList();
        var samples = p["block_samples"]!.AsArray().Select(n => n!.AsLong()).ToList();
        if (!reader.Blocks.Select(b => (long)b.Size).SequenceEqual(sizes) || !reader.Blocks.Select(b => b.Samples).SequenceEqual(samples))
            throw new UserError("PDA3: blocchi o campioni diversi dal modello validato");
    }

    public static Pda3Reader Reader(string path)
    {
        var info = new FileInfo(path);
        var key = Path.GetFullPath(path) + "|" + info.Length + "|" + info.LastWriteTimeUtc.Ticks;
        if (Readers.TryGetValue(key, out var cached)) return cached;
        var reader = new Pda3Reader(path);
        ValidateProfile(reader);
        if (Readers.Count >= 4) Readers.Clear();
        Readers[key] = reader;
        return reader;
    }

    static string Calendar(ulong ticks)
    {
        try { return new DateTime((long)(ticks / 10 * 10)).ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff", CultureInfo.InvariantCulture); }
        catch (ArgumentOutOfRangeException) { throw new UserError("PDA3: timestamp fuori intervallo"); }
    }

    static double Ms(DateTime utc) => (utc.Ticks - DateTime.UnixEpoch.Ticks) / 10 / 1e6 * 1000;

    public static JsonObject Details(string path, JsonObject? metadata)
    {
        var r = Reader(path);
        var p = Profile;
        var meta = (JsonObject)(metadata?.DeepClone() ?? new JsonObject());
        var count = ValidSampleCount(r);
        double start;
        var signals = new JsonArray();
        string note;
        if (meta["pda3_profile"].Truthy())
        {
            if (meta["pda3_profile"].Str() != p["id"].Str() || meta["dat_sha256"].Str() != r.Sha256)
                throw new UserError("PDA3: manifest non associato al DAT (hash/profilo diverso)");
            foreach (var key in new[] { "valid_samples_per_channel", "expected_samples_per_channel" })
                if (meta[key] != null && meta[key].Float() != count) throw new UserError("PDA3: durata/campioni del manifest diversi dal DAT");
            start = meta["real_start_ms"].Float() ?? throw new UserError("PDA3: inizio reale non valido");
            if (!double.IsFinite(start)) throw new UserError("PDA3: inizio reale non valido");
            if (meta["channel_mapping"] is not JsonArray supplied || supplied.Count is < 1 or > 64) throw new UserError("PDA3: mappatura manifest non valida");
            var seen = new HashSet<long>();
            var ids = new HashSet<string>();
            foreach (var n in supplied)
            {
                var index = n.Get("template_index");
                var nid = n.Get("nodeid");
                if (!index.IsNumber() || index!.ToJsonString().Contains('.') || index.AsDouble() is var ix && (ix < 0 || ix >= 64) || seen.Contains((long)ix)
                    || !nid.IsString() || string.IsNullOrEmpty(nid.Str()) || ids.Contains(nid.Str()!))
                    throw new UserError("PDA3: canali manifest duplicati/non validi");
                seen.Add((long)ix);
                ids.Add(nid.Str()!);
                var o = (JsonObject)n!.DeepClone();
                o["nodeid"] = nid.Str();
                o["display_name"] = n.Get("display_name").Truthy() ? RowValues.Str(Py.Plain(n.Get("display_name"))) : nid.Str();
                o["unit"] = n.Get("unit").Truthy() ? RowValues.Str(Py.Plain(n.Get("unit"))) : "";
                signals.Add(o);
            }
            note = "Tempo reale dal manifest; valori Float32 letti direttamente dal DAT. ibaAnalyzer mantiene il calendario del modello.";
        }
        else
        {
            var offsetNode = meta["pda3_timezone_offset_minutes"];
            if (offsetNode.IsNull()) throw new UserError("PDA3: manca il manifest; importare il DAT scegliendo esplicitamente il fuso orario del calendario interno");
            var offset = offsetNode.Float() ?? throw new UserError("PDA3: fuso orario non valido");
            if (!double.IsFinite(offset) || offset < -840 || offset > 840) throw new UserError("PDA3: fuso orario non valido");
            var calendar = DateTime.Parse(Calendar(r.StartTicks), CultureInfo.InvariantCulture);
            var utc = calendar - TimeSpan.FromMinutes(offset);
            start = Ms(utc);
            var channels = p["channels"]!.AsArray();
            for (var i = 0; i < channels.Count; i++)
                signals.Add(new JsonObject { ["nodeid"] = "pda3:" + channels[i].Get("id").Str(), ["display_name"] = channels[i].Get("display_name")?.DeepClone(), ["unit"] = channels[i].Get("unit")?.DeepClone(), ["template_index"] = i });
            note = $"Valori Float32 letti dal DAT; calendario interno interpretato con offset UTC {PyG(offset)} minuti scelto all’importazione. Qualita PLC originale non disponibile.";
        }
        var result = (JsonObject)meta.DeepClone();
        result["format"] = "dat";
        result["pda3"] = true;
        result["started_at"] = Values.IsoFromMs(start);
        result["ended_at"] = Values.IsoFromMs(start + (count - 1) * 10);
        result["duration_seconds"] = count / 100.0;
        result["rows_written"] = count * signals.Count;
        result["initial_sample_ms"] = 10;
        result["signals"] = signals;
        result["real_start_ms"] = start;
        result["valid_samples_per_channel"] = count;
        result["real_end_exclusive_ms"] = start + count * 10;
        result["storage"] = new JsonObject { ["format"] = "pda3", ["label"] = "iba DAT PDA3 · Float32 · 100 Hz", ["lossless"] = false };
        result["view_source_note"] = note;
        result["timestamp_semantics"] = note;
        result["quality_semantics"] = "Validita numerica del campione decodificato; per qualita PLC e timestamp originali usare il Parquet associato.";
        return result;
    }

    /// <summary>Python format(x, '+g').</summary>
    static string PyG(double x)
    {
        var s = x.ToString("G6", CultureInfo.InvariantCulture);
        if (s.Contains('E'))
        {
            var parts = s.Split('E');
            var exp = int.Parse(parts[1], CultureInfo.InvariantCulture);
            s = parts[0] + "e" + (exp < 0 ? "-" : "+") + Math.Abs(exp).ToString("00", CultureInfo.InvariantCulture);
        }
        return (x >= 0 ? "+" : "") + s;
    }

    public static IEnumerable<Row> IterRows(string path, JsonObject? meta, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        var r = Reader(path);
        var info = Details(path, meta);
        var start = info["real_start_ms"]!.AsDouble();
        var p = Profile;
        var count = info["valid_samples_per_channel"]!.AsLong();
        foreach (var signal in info["signals"]!.AsArray())
        {
            var nid = signal.Get("nodeid").Str()!;
            if (signalIds != null && !signalIds.Contains(nid)) continue;
            var name = RowValues.Str(Py.Plain(signal.Get("display_name")));
            var channel = p["channels"]![(int)signal.Get("template_index")!.AsDouble()]!;
            long position = 0;
            foreach (var bi in channel["blocks"]!.AsArray())
            {
                if (position >= count) break;
                var block = r.Blocks[(int)bi!.AsLong()];
                if (startMs != null && start + (position + block.Samples - 1) * 10 < startMs || endMs != null && start + position * 10 > endMs)
                {
                    position += block.Samples;
                    continue;
                }
                foreach (var value in r.Values(block))
                {
                    if (position >= count) break;
                    var t = start + position * 10;
                    var good = float.IsFinite(value);
                    position++;
                    if (startMs != null && t < startMs || endMs != null && t > endMs) continue;
                    double v = value;
                    yield return new Row
                    {
                        ["timestamp_ms"] = t, ["timestamp_utc"] = Values.IsoFromMs(t), ["elapsed_ms"] = (position - 1) * 10, ["cycle"] = position,
                        ["node_id"] = nid, ["signal_name"] = name, ["value_numeric"] = good ? v : null, ["value_text"] = PyJson.Str(v), ["data_type"] = "Float",
                        ["good"] = good, ["quality"] = good ? "Good" : "Bad", ["status"] = good ? "PDA3DecodedNumeric" : "PDA3NonFinite", ["sample_ms"] = 10L,
                        ["source_timestamp"] = null, ["server_timestamp"] = null, ["local_timestamp"] = null, ["ads_filetime"] = null,
                    };
                }
            }
            if (Math.Min(position, count) != count) throw new UserError("PDA3: conteggio canale non valido");
        }
    }

    public static (List<(string name, JsonObject info)> parts, JsonObject info) SequenceInfo(RecordingLibrary library, string path)
    {
        JsonNode? document;
        try { document = JsonNode.Parse(File.ReadAllText(path)); }
        catch (System.Text.Json.JsonException) { throw new UserError("PDA3: sequenza non valida"); }
        if (document.Get("parts") is not JsonArray names || names.Count is < 2 or > 256 || names.Any(n => !n.IsString() || n.Str()!.EndsWith(".pda3seq")))
            throw new UserError("PDA3: sequenza non valida");
        var parts = new List<(string name, JsonObject info)>();
        foreach (var n in names)
        {
            var name = n.Str()!;
            var part = library.PathOf(name);
            if (!IsPda3(part)) throw new UserError("PDA3: la sequenza ammette solo DAT del profilo noto");
            var stat = new FileInfo(part);
            var meta = library.Metadata(part);
            var key = part + "|" + stat.Length + "|" + stat.LastWriteTimeUtc.Ticks + "|" + Py.Dumps(meta);
            if (!PartInfo.TryGetValue(key, out var info))
            {
                info = Details(part, meta);
                if (PartInfo.Count >= 512) PartInfo.Clear();
                PartInfo[key] = info;
            }
            parts.Add((name, info));
        }
        parts = parts.OrderBy(x => x.info["real_start_ms"]!.AsDouble()).ToList();
        var ids = parts[0].info["signals"]!.AsArray().Select(s => s.Get("nodeid").Str()).ToList();
        for (var i = 0; i + 1 < parts.Count; i++)
        {
            var (a, pa) = parts[i];
            var (b, pb) = parts[i + 1];
            if (!pb["signals"]!.AsArray().Select(s => s.Get("nodeid").Str()).SequenceEqual(ids)) throw new UserError("PDA3: segnali differenti nella sequenza");
            var delta = pb["real_start_ms"]!.AsDouble() - pa["real_end_exclusive_ms"]!.AsDouble();
            if (Math.Abs(delta) > .001) throw new UserError($"PDA3: sequenza non continua; gap/sovrapposizione di {PyG(delta).TrimStart('+')} ms tra {a} e {b}");
        }
        var first = parts[0].info;
        var last = parts[^1].info;
        var merged = (JsonObject)first.DeepClone();
        foreach (var (k, v) in library.Metadata(path)) merged[k] = v?.DeepClone();
        merged["format"] = "dat";
        merged["ended_at"] = last["ended_at"]!.DeepClone();
        merged["real_end_exclusive_ms"] = last["real_end_exclusive_ms"]!.DeepClone();
        merged["duration_seconds"] = parts.Sum(p => p.info["duration_seconds"]!.AsDouble());
        merged["rows_written"] = parts.Sum(p => p.info["rows_written"]!.AsLong());
        merged["view_source_note"] = "Sequenza PDA3 con continuita verificata. " + first["view_source_note"].Str();
        return (parts, merged);
    }

    public static IEnumerable<Row> IterSequence(RecordingLibrary library, string path, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        var (parts, _) = SequenceInfo(library, path);
        foreach (var (name, meta) in parts)
            foreach (var row in IterRows(library.PathOf(name), meta, signalIds, startMs, endMs)) yield return row;
    }
}
