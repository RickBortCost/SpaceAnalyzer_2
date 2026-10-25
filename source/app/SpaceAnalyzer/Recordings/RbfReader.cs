using System.Buffers.Binary;
using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>Reader of RBF v3 frames (rbf.py), as demonstrated by the reference file.</summary>
public static class RbfReader
{
    public const string QualityMarker = "SpaceAnalyzer RBF3 quality 0=missing,1=Bad,2=Uncertain,3=Good";
    static readonly Dictionary<int, (int size, string type)> Types = new() { [0] = (1, "Boolean"), [2] = (2, "Int16"), [7] = (4, "Float32"), [9] = (81, "String") };

    public sealed record Channel(string Name, int Type, int Size, string DataType, string NodeId);

    public sealed class Header
    {
        public List<Channel> Channels { get; } = new();
        public long DataBytes { get; init; }
        public long Frames { get; init; }
        public long DataOffset { get; init; }
        public int FrameSize { get; init; }
        public long SampleUs { get; init; }
        public bool QualityPairs { get; set; }
        public double? StartMs { get; set; }
        public double? EndMs { get; set; }
    }

    public sealed record Mapping(string NodeId, string DisplayName, string Unit, int ValueChannel, int? QualityChannel, string DataType);

    static byte[] ReadExact(Stream stream, int size)
    {
        var raw = new byte[size];
        if (stream.ReadAtLeast(raw, size, false) != size) throw new UserError("File RBF troncato");
        return raw;
    }

    public static Header ReadHeader(Stream stream)
    {
        var head = ReadExact(stream, 20);
        uint version = BinaryPrimitives.ReadUInt32LittleEndian(head), dataBytes = BinaryPrimitives.ReadUInt32LittleEndian(head.AsSpan(4)),
            count = BinaryPrimitives.ReadUInt32LittleEndian(head.AsSpan(8)), period = BinaryPrimitives.ReadUInt32LittleEndian(head.AsSpan(12));
        if (version != 3 || count is < 1 or > 4096 || period < 1)
            throw new UserError("Variante RBF non supportata: atteso RBF v3 del campione verificato");
        var channels = new List<Channel>();
        for (var i = 0; i < count; i++)
        {
            var size = ReadExact(stream, 1)[0];
            if (size == 0) throw new UserError("Nome canale RBF vuoto");
            var name = Legacy.Cp1252Replace.GetString(ReadExact(stream, size));
            var t = ReadExact(stream, 4);
            if (!Types.TryGetValue(t[0], out var type) || t[1] != type.size || t[2] != 1 || t[3] != 10)
                throw new UserError($"Layout RBF non supportato nel canale {name}: tipo {t[0]}, {t[1]} byte");
            channels.Add(new Channel(name, t[0], t[1], type.type, $"rbf:{name}:{i}"));
        }
        var reserved = ReadExact(stream, 12 * (int)count);
        if (reserved.AsSpan().IndexOfAnyExcept((byte)0) >= 0) throw new UserError("RBF: metadati canali differenti dal profilo verificato");
        var frame = 8 + channels.Sum(c => c.Size);
        if (dataBytes % frame != 0) throw new UserError("RBF: dimensione dati non multipla di un campione completo");
        var h = new Header { DataBytes = dataBytes, Frames = dataBytes / frame, DataOffset = stream.Position, FrameSize = frame, SampleUs = period };
        h.Channels.AddRange(channels);
        return h;
    }

    public static double Timestamp(uint sec, uint ns)
    {
        if (ns >= 1_000_000_000) throw new UserError("Timestamp RBF non valido: nanosecondi fuori intervallo");
        return sec * 1000.0 + ns / 1_000_000.0;
    }

    public static string Stamp(uint sec, uint ns) =>
        DateTime.UnixEpoch.AddSeconds(sec).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + ns.ToString("000000000", CultureInfo.InvariantCulture) + "+00:00";

    public static Header Inspect(string path)
    {
        using var stream = File.OpenRead(path);
        var h = ReadHeader(stream);
        var end = h.DataOffset + h.DataBytes;
        if (end > stream.Length) throw new UserError("File RBF troncato: campioni mancanti");
        stream.Seek(end, SeekOrigin.Begin);
        var footer = new byte[Math.Min(1_048_577, stream.Length - end)];
        stream.ReadAtLeast(footer, footer.Length, false);
        if (footer.Length > 1_048_576 || !footer.AsSpan().StartsWith("\r\nPlotter = "u8) || !footer.AsSpan().EndsWith("END SAVESTATE\r\n"u8))
            throw new UserError("RBF: configurazione finale assente, troncata o non supportata");
        h.QualityPairs = footer.AsSpan().IndexOf(Encoding.ASCII.GetBytes(QualityMarker)) >= 0;
        if (h.Frames > 0)
        {
            stream.Seek(h.DataOffset, SeekOrigin.Begin);
            var a = ReadExact(stream, 8);
            h.StartMs = Timestamp(BinaryPrimitives.ReadUInt32LittleEndian(a), BinaryPrimitives.ReadUInt32LittleEndian(a.AsSpan(4)));
            stream.Seek(end - h.FrameSize, SeekOrigin.Begin);
            var b = ReadExact(stream, 8);
            h.EndMs = Timestamp(BinaryPrimitives.ReadUInt32LittleEndian(b), BinaryPrimitives.ReadUInt32LittleEndian(b.AsSpan(4)));
            if (h.EndMs < h.StartMs) throw new UserError("RBF: intervallo temporale invertito");
        }
        return h;
    }

    public static List<Mapping> ChannelMap(Header h, JsonObject? metadata = null)
    {
        var result = new List<Mapping>();
        var supplied = metadata?["rbf_channels"];
        if (supplied.Truthy())
        {
            if (supplied is not JsonArray list) throw new UserError("Mappa canali RBF non valida");
            foreach (var s in list)
            {
                if (s is not JsonObject o) throw new UserError("Mappa canali RBF non valida");
                var vc = o["value_channel"];
                var qc = o["quality_channel"];
                if (!IsInt(vc) || !o["nodeid"].Truthy()) throw new UserError("Mappa canali RBF non valida");
                var i = (int)vc!.AsDouble();
                if (i < 0 || i >= h.Channels.Count) throw new UserError("Mappa canali RBF non valida");
                int? q = null;
                if (!qc.IsNull())
                {
                    if (!IsInt(qc)) throw new UserError("Mappa qualita RBF non valida");
                    q = (int)qc!.AsDouble();
                    if (q < 0 || q >= h.Channels.Count || h.Channels[q.Value].Type != 2) throw new UserError("Mappa qualita RBF non valida");
                }
                result.Add(new Mapping(RowValues.Str(Py.Plain(o["nodeid"])), o["display_name"].Str() ?? "", o["unit"].Str() ?? "", i, q, o["data_type"].Str() ?? ""));
            }
            return result;
        }
        var k = 0;
        while (k < h.Channels.Count)
        {
            var c = h.Channels[k];
            var q = k + 1 < h.Channels.Count ? h.Channels[k + 1] : null;
            var paired = h.QualityPairs && q != null && q.Name == c.Name + "_Q" && q.Type == 2;
            result.Add(new Mapping(c.NodeId, c.Name, "", k, paired ? k + 1 : null, c.DataType));
            k += paired ? 2 : 1;
        }
        return result;
    }

    static bool IsInt(JsonNode? n) => n.IsNumber() && !n!.ToJsonString().Contains('.') && !n.ToJsonString().Contains('e');

    static (object raw, double? number) Read(Channel c, byte[] frame, int offset)
    {
        switch (c.Type)
        {
            case 0: { var v = frame[offset]; return ((long)v, v); }
            case 2: { var v = BinaryPrimitives.ReadInt16LittleEndian(frame.AsSpan(offset)); return ((long)v, v); }
            case 7: { double v = BinaryPrimitives.ReadSingleLittleEndian(frame.AsSpan(offset)); return (v, v); }
            default:
            {
                var span = frame.AsSpan(offset, 81);
                var zero = span.IndexOf((byte)0);
                return (Legacy.Cp1252Replace.GetString(zero < 0 ? span : span[..zero]), null);
            }
        }
    }

    public static IEnumerable<Row> IterRows(string path, JsonObject? metadata = null, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        var h = Inspect(path);
        var meta = metadata ?? new JsonObject();
        var selected = ChannelMap(h, meta).Where(s => signalIds == null || signalIds.Contains(s.NodeId)).ToList();
        var offsets = new int[h.Channels.Count];
        var position = 8;
        for (var i = 0; i < h.Channels.Count; i++) { offsets[i] = position; position += h.Channels[i].Size; }
        object sampleMs = meta["initial_sample_ms"].Truthy() ? Py.Plain(meta["initial_sample_ms"])! : h.SampleUs / 1000.0;
        using var stream = File.OpenRead(path);
        stream.Seek(h.DataOffset, SeekOrigin.Begin);
        long? previous = null;
        var frame = new byte[h.FrameSize];
        for (long cycle = 1; cycle <= h.Frames; cycle++)
        {
            if (stream.ReadAtLeast(frame, frame.Length, false) != frame.Length) throw new UserError("File RBF troncato");
            uint sec = BinaryPrimitives.ReadUInt32LittleEndian(frame), ns = BinaryPrimitives.ReadUInt32LittleEndian(frame.AsSpan(4));
            var t = Timestamp(sec, ns);
            var ticks = sec * 1_000_000_000L + ns;
            if (previous != null && ticks < previous) throw new UserError("RBF: timestamp non ordinati");
            previous = ticks;
            if (startMs != null && t < startMs || endMs != null && t > endMs) continue;
            var text = Stamp(sec, ns);
            foreach (var s in selected)
            {
                var c = h.Channels[s.ValueChannel];
                var (raw, number) = Read(c, frame, offsets[s.ValueChannel]);
                var q = s.QualityChannel is int qi ? (int)BinaryPrimitives.ReadInt16LittleEndian(frame.AsSpan(offsets[qi])) : 3;
                var quality = FdaReader.Quality(q);
                if (number is double d && (!double.IsFinite(d) || c.Type == 0 && raw is long b && b is not (0 or 1))) quality = "Bad";
                yield return new Row
                {
                    ["timestamp_ms"] = t, ["timestamp_utc"] = text, ["node_id"] = s.NodeId,
                    ["signal_name"] = string.IsNullOrEmpty(s.DisplayName) ? c.Name : s.DisplayName,
                    ["value_numeric"] = quality == "Good" ? number : null, ["value_text"] = RowValues.Str(raw), ["data_type"] = c.DataType,
                    ["good"] = quality == "Good", ["quality"] = quality, ["status"] = q == 0 ? "RBF_Missing" : "RBF_" + quality,
                    ["sample_ms"] = sampleMs, ["cycle"] = cycle, ["source_timestamp"] = text, ["server_timestamp"] = null,
                };
            }
        }
    }
}
