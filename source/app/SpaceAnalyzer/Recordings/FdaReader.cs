using System.Buffers.Binary;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>Reader of the FDA DAS4 subset (fda_das.py): Int16 scaled and Float32 channels.</summary>
public static class FdaReader
{
    public sealed record Channel(string Name, string Description, string Unit, int Index, int Enabled, int Kind, int Type, int Size, double Gain, double Offset);

    public sealed class Header
    {
        public List<Channel> Channels { get; } = new();
        public long Epoch { get; init; }
        public int RecordSize { get; init; }
        public long DataOffset { get; set; }
        public long Frames { get; set; }
    }

    public sealed record Mapping(string NodeId, string DisplayName, string Unit, int ValueChannel, int? QualityChannel);

    static readonly string[] Qualities = ["Bad", "Bad", "Uncertain", "Good"];
    public static string Quality(int q) => q is >= 0 and <= 3 ? Qualities[q] : "Bad";

    static string ReadText(Stream stream)
    {
        var n = stream.ReadByte();
        if (n < 0) throw new UserError("Intestazione FDA troncata");
        var size = n;
        if (size == 255)
        {
            var extra = new byte[2];
            if (stream.ReadAtLeast(extra, 2, false) != 2) throw new UserError("Stringa FDA troncata");
            size = BinaryPrimitives.ReadUInt16LittleEndian(extra);
            if (size >= 65534) throw new UserError("Variante stringhe FDA non supportata");
        }
        var raw = new byte[size];
        if (stream.ReadAtLeast(raw, size, false) != size) throw new UserError("Stringa FDA troncata");
        return Legacy.Cp1252Replace.GetString(raw);
    }

    public static Header ReadHeader(Stream stream)
    {
        var raw = new byte[22 + 5 + 4 + 8 + 8 + 4 + 8 + 2 + 4 + 2];
        if (stream.ReadAtLeast(raw, raw.Length, false) != raw.Length) throw new UserError("File FDA troppo breve");
        var magic = raw.AsSpan(22, 5);
        var epoch = BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(27));
        var stride = BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(47));
        var count = BinaryPrimitives.ReadUInt16LittleEndian(raw.AsSpan(65));
        if (raw.AsSpan(0, 22).IndexOfAnyExcept((byte)0) >= 0 || !magic.SequenceEqual("DAS4\0"u8) || count is < 1 or > 4096 || stride is < 4 or > 65536)
            throw new UserError("Variante FDA non supportata: atteso DAS4");
        var h = new Header { Epoch = epoch, RecordSize = (int)stride, DataOffset = 0 };
        var offset = 0;
        for (var i = 0; i < count; i++)
        {
            var tag = new byte[2];
            stream.ReadAtLeast(tag, 2, false);
            if (i == 0)
            {
                var a = new byte[4]; var b = new byte[8];
                if (!tag.AsSpan().SequenceEqual(new byte[] { 0xff, 0xff }) || stream.ReadAtLeast(a, 4, false) != 4 || !a.AsSpan().SequenceEqual(new byte[] { 2, 0, 8, 0 })
                    || stream.ReadAtLeast(b, 8, false) != 8 || !b.AsSpan().SequenceEqual("CDasChan"u8))
                    throw new UserError("Schema canali FDA non supportato");
            }
            else if (!tag.AsSpan().SequenceEqual(new byte[] { 0x01, 0x80 })) throw new UserError("Classe canale FDA non supportata");
            var name = ReadText(stream);
            var description = ReadText(stream);
            var unit = ReadText(stream);
            var c = new byte[2 + 2 + 1 + 1 + 2 + 2 + 8 + 8 + 4];
            if (stream.ReadAtLeast(c, c.Length, false) != c.Length) throw new UserError("Canale FDA troncato");
            int idx = BinaryPrimitives.ReadUInt16LittleEndian(c), enabled = BinaryPrimitives.ReadUInt16LittleEndian(c.AsSpan(2));
            int kind = c[4], dtype = c[5], size = BinaryPrimitives.ReadUInt16LittleEndian(c.AsSpan(6)), items = BinaryPrimitives.ReadUInt16LittleEndian(c.AsSpan(8));
            var gain = BinaryPrimitives.ReadDoubleLittleEndian(c.AsSpan(10));
            var bias = BinaryPrimitives.ReadDoubleLittleEndian(c.AsSpan(18));
            var pos = BinaryPrimitives.ReadUInt32LittleEndian(c.AsSpan(26));
            if (items != 1 || pos != offset || !double.IsFinite(gain) || !double.IsFinite(bias) || gain <= 0)
                throw new UserError("Layout o scala canale FDA non supportati");
            if (i == 0 && (kind, dtype, size) != (255, 0, 4) || i > 0 && (kind, dtype, size) is not ((0, 2, 2) or (0, 4, 4)))
                throw new UserError($"Tipo numerico FDA non supportato nel canale {name}: tipo {dtype}, {size} byte; supportati Int16 e Float32");
            h.Channels.Add(new Channel(name, description, unit, idx, enabled, kind, dtype, size, gain, bias));
            offset += size;
        }
        var tail = new byte[6];
        if (stream.ReadAtLeast(tail, 6, false) != 6) throw new UserError("Intestazione FDA troncata");
        var reserved = BinaryPrimitives.ReadUInt16LittleEndian(tail);
        var start = BinaryPrimitives.ReadUInt32LittleEndian(tail.AsSpan(2));
        if (reserved != 0 || start != stream.Position || stride != offset) throw new UserError("Dimensioni intestazione FDA incoerenti");
        h.DataOffset = start;
        return h;
    }

    public static Header Inspect(string path)
    {
        Header h;
        using (var stream = File.OpenRead(path)) h = ReadHeader(stream);
        var payload = new FileInfo(path).Length - h.DataOffset;
        if (payload < 0 || payload % h.RecordSize != 0) throw new UserError("File FDA troncato o dati di dimensione incoerente");
        h.Frames = payload / h.RecordSize;
        return h;
    }

    public static List<Mapping> ChannelMap(Header h)
    {
        var result = new List<Mapping>();
        var i = 1;
        while (i < h.Channels.Count)
        {
            var c = h.Channels[i];
            var q = i + 1 < h.Channels.Count ? h.Channels[i + 1] : null;
            var paired = q != null && q.Name == c.Name + "_Q" && q.Description == "Quality: 0=missing, 1=Bad, 2=Uncertain, 3=Good" && q.Gain == 1.0 && q.Offset == 0.0;
            var nodeid = paired && c.Description.Length is > 0 and < 251 ? c.Description : "das:" + c.Name + ":" + i;
            result.Add(new Mapping(nodeid, c.Name, c.Unit, i, paired ? i + 1 : null));
            i += paired ? 2 : 1;
        }
        return result;
    }

    /// <summary>fda_channels of the sidecar, else the recovered map.</summary>
    static List<Mapping> Definitions(Header h, JsonObject meta)
    {
        if (meta["fda_channels"] is JsonArray mapped && mapped.Count > 0)
        {
            var list = new List<Mapping>();
            foreach (var item in mapped)
            {
                var vc = item.Get("value_channel");
                var qc = item.Get("quality_channel");
                int? value = vc.IsNumber() && !vc.IsBool() && vc!.GetValue<double>() % 1 == 0 ? (int)vc.GetValue<double>() : null;
                int? quality = qc.IsNull() ? null : qc.IsNumber() && qc!.GetValue<double>() % 1 == 0 ? (int)qc.GetValue<double>() : -1;
                list.Add(new Mapping(item.Get("nodeid").Str() ?? "", item.Get("display_name").Str() ?? "", "", value ?? -1, quality));
            }
            return list;
        }
        return ChannelMap(h);
    }

    static int[] Offsets(Header h)
    {
        var offsets = new int[h.Channels.Count];
        var position = 4;
        for (var i = 1; i < h.Channels.Count; i++) { offsets[i] = position; position += h.Channels[i].Size; }
        return offsets;
    }

    static double Value(Header h, int[] offsets, byte[] frame, int start, int channel) =>
        h.Channels[channel].Type == 4 ? BinaryPrimitives.ReadSingleLittleEndian(frame.AsSpan(start + offsets[channel])) : BinaryPrimitives.ReadInt16LittleEndian(frame.AsSpan(start + offsets[channel]));

    public static IEnumerable<Row> IterRows(string path, JsonObject? metadata = null, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        var meta = metadata ?? new JsonObject();
        using var stream = File.OpenRead(path);
        var h = ReadHeader(stream);
        var offsets = Offsets(h);
        var definitions = Definitions(h, meta).Where(s => signalIds == null || signalIds.Contains(s.NodeId)).ToList();
        double? previous = null;
        long cycle = 0;
        var frame = new byte[h.RecordSize];
        var initial = meta["initial_sample_ms"];
        while (true)
        {
            var read = stream.ReadAtLeast(frame, frame.Length, false);
            if (read == 0) break;
            if (read != frame.Length) throw new UserError("Record FDA troncato");
            double t = h.Epoch * 1000 + BinaryPrimitives.ReadUInt32LittleEndian(frame);
            cycle++;
            var period = previous != null && t > previous ? t - previous.Value : 0;
            previous = t;
            if (startMs != null && t < startMs || endMs != null && t > endMs) continue;
            var stamp = IsoMilli(t);
            foreach (var s in definitions)
            {
                Check(h, s);
                var c = h.Channels[s.ValueChannel];
                var q = s.QualityChannel is int qi ? (int)Value(h, offsets, frame, 0, qi) : 3;
                var quality = Quality(q);
                var number = Value(h, offsets, frame, 0, s.ValueChannel) * c.Gain + c.Offset;
                if (!double.IsFinite(number)) quality = "Bad";
                yield return new Row
                {
                    ["timestamp_ms"] = t, ["timestamp_utc"] = stamp, ["node_id"] = s.NodeId,
                    ["signal_name"] = string.IsNullOrEmpty(s.DisplayName) ? c.Name : s.DisplayName,
                    ["value_numeric"] = quality == "Good" ? number : null, ["value_text"] = PyJson.Str(number), ["data_type"] = "Double",
                    ["good"] = quality == "Good", ["quality"] = quality, ["status"] = q == 0 ? "FDA_Missing" : "FDA_" + quality,
                    ["sample_ms"] = initial.Truthy() ? Box(initial) : period, ["cycle"] = cycle, ["source_timestamp"] = null, ["server_timestamp"] = null,
                };
            }
        }
    }

    static object? Box(JsonNode? n) => Py.Plain(n);

    static void Check(Header h, Mapping s)
    {
        if (s.ValueChannel < 1 || s.ValueChannel >= h.Channels.Count || s.QualityChannel is int q && (q < 1 || q >= h.Channels.Count))
            throw new UserError("Mappa canali FDA non valida");
    }

    /// <summary>isoformat(timespec='milliseconds') of a UTC timestamp.</summary>
    public static string IsoMilli(double ms)
    {
        var dt = TableRecorder.PythonFromTimestamp(ms);
        return dt.ToString("yyyy-MM-dd'T'HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) + "." + (dt.Ticks % TimeSpan.TicksPerSecond / TimeSpan.TicksPerMillisecond).ToString("000") + "+00:00";
    }

    /// <summary>Packed plot tuples (nid, t, value, quality, name, type, period) without building rows.</summary>
    public static IEnumerable<PlotRow> PlotRows(string path, JsonObject? metadata = null, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        var meta = metadata ?? new JsonObject();
        using var stream = File.OpenRead(path);
        var h = ReadHeader(stream);
        var offsets = Offsets(h);
        var payload = stream.Length - h.DataOffset;
        if (payload < 0 || payload % h.RecordSize != 0) throw new UserError("Record FDA troncato");
        var definitions = new List<(string nid, int i, int? qi, double gain, double bias, string name)>();
        foreach (var s in Definitions(h, meta))
        {
            if (signalIds != null && !signalIds.Contains(s.NodeId)) continue;
            Check(h, s);
            var c = h.Channels[s.ValueChannel];
            definitions.Add((s.NodeId, s.ValueChannel, s.QualityChannel, c.Gain, c.Offset, string.IsNullOrEmpty(s.DisplayName) ? c.Name : s.DisplayName));
        }
        if (definitions.Count == 0) yield break;
        long first = 0, last = payload / h.RecordSize;
        if (meta["fda_chronological"].Truthy())
        {
            var buffer = new byte[4];
            long Lower(double bound, bool inclusive)
            {
                long lo = 0, hi = last;
                while (lo < hi)
                {
                    var mid = (lo + hi) / 2;
                    stream.Seek(h.DataOffset + mid * h.RecordSize, SeekOrigin.Begin);
                    stream.ReadExactly(buffer);
                    double t = h.Epoch * 1000 + BinaryPrimitives.ReadUInt32LittleEndian(buffer);
                    if (t < bound || inclusive && t == bound) lo = mid + 1; else hi = mid;
                }
                return lo;
            }
            if (startMs != null) first = Lower(startMs.Value, false);
            if (endMs != null) last = Lower(endMs.Value, true);
        }
        stream.Seek(h.DataOffset + first * h.RecordSize, SeekOrigin.Begin);
        var remaining = Math.Max(0, last - first);
        double? previous = null;
        var initial = meta["initial_sample_ms"];
        object? fixedPeriod = initial.Truthy() ? Box(initial) : null;
        while (remaining > 0)
        {
            var count = (int)Math.Min(Math.Min(8192, Math.Max(1, 4 * 1024 * 1024 / h.RecordSize)), remaining);
            var raw = new byte[count * h.RecordSize];
            if (stream.ReadAtLeast(raw, raw.Length, false) != raw.Length) throw new UserError("Record FDA troncato");
            remaining -= count;
            for (var f = 0; f < count; f++)
            {
                var start = f * h.RecordSize;
                double t = h.Epoch * 1000 + BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(start));
                object period = fixedPeriod ?? (previous != null && t > previous ? t - previous.Value : 0.0);
                previous = t;
                if (startMs != null && t < startMs || endMs != null && t > endMs) continue;
                foreach (var (nid, i, qi, gain, bias, name) in definitions)
                {
                    var q = Quality(qi is int qq ? (int)Value(h, offsets, raw, start, qq) : 3);
                    var v = Value(h, offsets, raw, start, i) * gain + bias;
                    if (!double.IsFinite(v)) q = "Bad";
                    yield return new PlotRow(nid, t, q == "Good" ? v : null, q, name, "Double", period);
                }
            }
        }
    }
}

/// <summary>plot_rows tuple of recording_preview.</summary>
public readonly record struct PlotRow(string NodeId, double T, double? Value, string Quality, string Name, string DataType, object? Period);
