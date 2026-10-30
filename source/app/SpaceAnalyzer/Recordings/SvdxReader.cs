using System.Buffers.Binary;
using System.Globalization;
using System.Xml;
using System.Xml.Linq;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// Reader of Beckhoff TwinCAT Scope projects (.svdx) that contain their data (SaveOption IncludeDataInSVDX).
/// Beckhoff publishes no specification: the layout below was derived from a reference file (94 REAL32 ADS channels, 1 ms, 19 s) and is checked while reading.
/// File: [u64 xml offset][u64 xml size][u32 blocks][blocks x (u64 offset, u64 size, u32 file handle)], the blocks, then the project XML.
/// Block: "01.00.00.40", start and end FILETIME, a table of overview levels (min/max pyramid, not needed) and, at the offset of level 0,
/// the raw chunks [u64 FILETIME][u32 n][n x (u32 time from the chunk start in 100 ns, value)].
/// </summary>
public static class SvdxReader
{
    const long UnixEpochFileTime = 116_444_736_000_000_000;
    const int Preamble = 11, LevelsAt = 43, SamplesPerChunkAt = 51, PeriodAt = 59, FirstLevelAt = 83, LevelStride = 56;

    sealed record TypeInfo(string DataType, int Size, bool Integer, bool Signed);

    static readonly Dictionary<string, TypeInfo> Types = new(StringComparer.OrdinalIgnoreCase)
    {
        ["BIT"] = new("Boolean", 1, true, false), ["BOOL"] = new("Boolean", 1, true, false),
        ["INT8"] = new("SByte", 1, true, true), ["UINT8"] = new("Byte", 1, true, false),
        ["INT16"] = new("Int16", 2, true, true), ["UINT16"] = new("UInt16", 2, true, false),
        ["INT32"] = new("Int32", 4, true, true), ["UINT32"] = new("UInt32", 4, true, false),
        ["INT64"] = new("Int64", 8, true, true), ["UINT64"] = new("UInt64", 8, true, false),
        ["REAL32"] = new("Float32", 4, false, true), ["REAL64"] = new("Double", 8, false, true),
    };

    public sealed record Segment(long DataOffset, long Chunks, long Samples, long FirstTicks, long LastTicks);

    public sealed class Channel
    {
        public int Handle { get; init; }
        public string NodeId { get; init; } = "";
        public string Name { get; init; } = "";
        public string Symbol { get; init; } = "";
        public string Unit { get; init; } = "";
        public string DataType { get; init; } = "";
        public string SourceType { get; init; } = "";
        public int ValueSize { get; init; }
        public long PeriodTicks { get; init; }
        public int SamplesPerChunk { get; set; }
        public List<Segment> Segments { get; } = new();
        public long Samples => Segments.Sum(s => s.Samples);
        public long? FirstTicks => Segments.Count == 0 ? null : Segments.Min(s => s.FirstTicks);
        public long? LastTicks => Segments.Count == 0 ? null : Segments.Max(s => s.LastTicks);
    }

    public sealed class Header
    {
        public List<Channel> Channels { get; } = new();
        public string Project { get; init; } = "";
        public long Samples => Channels.Sum(c => c.Samples);
        public double? StartMs => Channels.Any(c => c.FirstTicks != null) ? Channels.Where(c => c.FirstTicks != null).Min(c => ToMs(c.FirstTicks!.Value)) : null;
        public double? EndMs => Channels.Any(c => c.LastTicks != null) ? Channels.Where(c => c.LastTicks != null).Max(c => ToMs(c.LastTicks!.Value)) : null;
        public double SampleMs => Channels.Count == 0 ? 0 : Channels.Min(c => c.PeriodTicks) / 10000.0;
    }

    public static bool IsSvdx(string path)
    {
        try
        {
            using var stream = File.OpenRead(path);
            if (stream.Length < 40) return false;
            var head = new byte[20];
            stream.ReadExactly(head);
            var xmlOffset = BinaryPrimitives.ReadInt64LittleEndian(head);
            var xmlSize = BinaryPrimitives.ReadInt64LittleEndian(head.AsSpan(8));
            if (xmlOffset < 20 || xmlSize < 16 || xmlOffset + xmlSize > stream.Length) return false;
            stream.Seek(xmlOffset, SeekOrigin.Begin);
            var probe = new byte[Math.Min(64, xmlSize)];
            stream.ReadExactly(probe);
            return System.Text.Encoding.UTF8.GetString(probe).TrimStart('﻿').StartsWith("<?xml", StringComparison.Ordinal);
        }
        catch (IOException) { return false; }
    }

    public static double ToMs(long fileTime) => (fileTime - UnixEpochFileTime) / 10000.0;

    public static string Stamp(long fileTime)
    {
        var unixTicks = fileTime - UnixEpochFileTime;
        var seconds = Math.DivRem(unixTicks, 10_000_000L, out var rest);
        return DateTime.UnixEpoch.AddSeconds(seconds).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + (rest * 100).ToString("000000000", CultureInfo.InvariantCulture) + "+00:00";
    }

    static string Fail(string detail) => "File Scope .svdx non supportato: " + detail;

    public static Header Inspect(string path)
    {
        using var stream = File.OpenRead(path);
        var length = stream.Length;
        if (length < 40) throw new UserError(Fail("file troppo corto"));
        var head = new byte[20];
        stream.ReadExactly(head);
        long xmlOffset = BinaryPrimitives.ReadInt64LittleEndian(head), xmlSize = BinaryPrimitives.ReadInt64LittleEndian(head.AsSpan(8));
        var count = BinaryPrimitives.ReadUInt32LittleEndian(head.AsSpan(16));
        if (xmlOffset < 20 || xmlSize < 16 || xmlSize > 256L * 1024 * 1024 || xmlOffset + xmlSize > length || count > 100_000)
            throw new UserError(Fail("intestazione non riconosciuta, il file potrebbe essere incompleto"));
        var table = new byte[checked(20 * (int)count)];
        stream.ReadExactly(table);
        var blocks = new List<(long offset, long size, int handle)>();
        for (var i = 0; i < count; i++)
        {
            var o = BinaryPrimitives.ReadInt64LittleEndian(table.AsSpan(20 * i));
            var s = BinaryPrimitives.ReadInt64LittleEndian(table.AsSpan(20 * i + 8));
            var h = BinaryPrimitives.ReadInt32LittleEndian(table.AsSpan(20 * i + 16));
            if (o < 20 + 20 * (long)count || s < FirstLevelAt + LevelStride || o + s > xmlOffset) throw new UserError(Fail("indice dei blocchi non valido"));
            blocks.Add((o, s, h));
        }
        var xml = new byte[xmlSize];
        stream.Seek(xmlOffset, SeekOrigin.Begin);
        stream.ReadExactly(xml);
        XDocument doc;
        try
        {
            using var reader = XmlReader.Create(new MemoryStream(xml), new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null });
            doc = XDocument.Load(reader);
        }
        catch (XmlException) { throw new UserError(Fail("progetto XML illeggibile")); }

        var names = new Dictionary<string, string>();
        foreach (var channel in doc.Descendants().Where(e => e.Name.LocalName == "Channel"))
        {
            var guid = channel.Descendants().FirstOrDefault(e => e.Name.LocalName == "AcquisitionGUID")?.Value;
            var name = channel.Elements().FirstOrDefault(e => e.Name.LocalName == "Name")?.Value;
            if (!string.IsNullOrEmpty(guid) && !string.IsNullOrWhiteSpace(name)) names.TryAdd(guid, name.Trim());
        }
        var header = new Header { Project = doc.Root?.Elements().FirstOrDefault(e => e.Name.LocalName == "Title")?.Value ?? "" };
        var used = new HashSet<string>(StringComparer.Ordinal);
        foreach (var acq in doc.Descendants().Where(e => e.Name.LocalName.EndsWith("Acquisition", StringComparison.Ordinal) && e.Elements().Any(c => c.Name.LocalName == "FileHandle")))
        {
            string? Child(string n) => acq.Elements().FirstOrDefault(e => e.Name.LocalName == n)?.Value;
            if (!int.TryParse(Child("FileHandle"), NumberStyles.Integer, CultureInfo.InvariantCulture, out var handle)) continue;
            var mine = blocks.Where(b => b.handle == handle).ToList();
            if (mine.Count == 0) continue;
            var typeName = (Child("DataType") ?? "").Trim();
            if (!Types.TryGetValue(typeName, out var type)) throw new UserError(Fail($"tipo di dato {typeName} del canale {Child("Name")} non gestito"));
            var guid = Child("Guid") ?? "";
            var baseName = (Child("Name") ?? "").Trim();
            var display = names.TryGetValue(guid, out var given) && given.Length > 0 ? given : baseName;
            if (display.Length == 0) display = "canale " + handle;
            var unique = display;
            for (var n = 2; !used.Add(unique); n++) unique = display + " (" + n + ")";
            var symbol = Child("SymbolName") ?? baseName;
            var channel = new Channel
            {
                Handle = handle, NodeId = "svdx:" + handle.ToString(CultureInfo.InvariantCulture) + ":" + (symbol.Length > 0 ? symbol : baseName),
                Name = unique, Symbol = symbol, DataType = type.DataType, SourceType = typeName, ValueSize = type.Size,
                Unit = UnitOf(acq),
                PeriodTicks = long.TryParse(Child("BaseSampleTime"), NumberStyles.Integer, CultureInfo.InvariantCulture, out var period) && period > 0 ? period : 10_000,
            };
            header.Channels.Add(channel);
        }
        if (header.Channels.Count == 0) throw new UserError(Fail("nessun canale con dati nel file; salvare il progetto con «Include data in SVDX»"));

        var withData = new List<Channel>();
        foreach (var info in header.Channels)
        {
            foreach (var (offset, size, _) in blocks.Where(b => b.handle == info.Handle))
            {
                var buffer = new byte[Math.Min(size, 4096)];
                stream.Seek(offset, SeekOrigin.Begin);
                stream.ReadExactly(buffer);
                var seg = ReadBlock(stream, offset, size, buffer, info.ValueSize, out var spc);
                if (seg == null) continue;
                if (info.Segments.Count > 0 && info.SamplesPerChunk != spc) throw new UserError(Fail($"blocchi del canale {info.Name} con dimensione dei chunk diversa"));
                info.SamplesPerChunk = spc;
                info.Segments.Add(seg);
            }
            if (info.Segments.Count > 0) withData.Add(info);
        }
        if (withData.Count == 0) throw new UserError(Fail("nessun campione nei blocchi"));
        var result = new Header { Project = header.Project };
        result.Channels.AddRange(withData);
        return result;
    }

    static string UnitOf(XElement acq)
    {
        foreach (var path in new[] { new[] { "RawUnit", "UserUnit", "Symbol" }, new[] { "RawUnit", "Unit", "Symbol" }, new[] { "RawUnit", "Unit", "BaseUnitString" } })
        {
            XElement? e = acq;
            foreach (var n in path) e = e?.Elements().FirstOrDefault(x => x.Name.LocalName == n);
            var text = e?.Value.Trim();
            if (!string.IsNullOrEmpty(text) && text != "1") return text;
        }
        return "";
    }

    /// <summary>Validates one block and returns its raw segment; <c>head</c> holds at least the first 4096 bytes (or the whole block).</summary>
    static Segment? ReadBlock(Stream stream, long offset, long size, byte[] head, int valueSize, out int samplesPerChunk)
    {
        samplesPerChunk = 0;
        if (head.Length < FirstLevelAt + LevelStride || head[0] != (byte)'0' || head[2] != (byte)'.' || head[5] != (byte)'.')
            throw new UserError(Fail("intestazione di blocco non riconosciuta"));
        var levels = BinaryPrimitives.ReadInt64LittleEndian(head.AsSpan(LevelsAt));
        var spc = BinaryPrimitives.ReadInt64LittleEndian(head.AsSpan(SamplesPerChunkAt));
        var period = BinaryPrimitives.ReadInt64LittleEndian(head.AsSpan(PeriodAt));
        if (levels is < 1 or > 32 || spc is < 1 or > 1 << 20 || period < 1 || FirstLevelAt + LevelStride * levels > head.Length)
            throw new UserError(Fail("tabella dei livelli non valida"));
        var p = FirstLevelAt + LevelStride * (int)(levels - 1);
        var level = BinaryPrimitives.ReadUInt32LittleEndian(head.AsSpan(p));
        var dataOffset = BinaryPrimitives.ReadUInt32LittleEndian(head.AsSpan(p + 4));
        var chunks = BinaryPrimitives.ReadInt64LittleEndian(head.AsSpan(p - 16));
        if (level != 0 || dataOffset >= size || chunks < 0) throw new UserError(Fail("livello dei dati grezzi non trovato"));
        samplesPerChunk = (int)spc;
        if (chunks == 0) return null;
        var sampleSize = 4 + valueSize;
        var chunkSize = 12 + spc * sampleSize;
        var lastAt = dataOffset + (chunks - 1) * chunkSize;
        if (lastAt + 12 > size) throw new UserError(Fail("blocco dati troncato"));
        var first = new byte[16];
        stream.Seek(offset + dataOffset, SeekOrigin.Begin);
        stream.ReadExactly(first);
        var firstFt = BinaryPrimitives.ReadInt64LittleEndian(first);
        var firstN = BinaryPrimitives.ReadUInt32LittleEndian(first.AsSpan(8));
        var last = new byte[12];
        stream.Seek(offset + lastAt, SeekOrigin.Begin);
        stream.ReadExactly(last);
        var lastFt = BinaryPrimitives.ReadInt64LittleEndian(last);
        var lastN = BinaryPrimitives.ReadUInt32LittleEndian(last.AsSpan(8));
        if (firstN < 1 || firstN > spc || lastN < 1 || lastN > spc || lastAt + 12 + lastN * sampleSize > size || firstFt < UnixEpochFileTime || lastFt < firstFt)
            throw new UserError(Fail("disposizione dei campioni diversa da quella verificata"));
        var lastTime = new byte[4];
        stream.Seek(offset + lastAt + 12 + (lastN - 1) * sampleSize, SeekOrigin.Begin);
        stream.ReadExactly(lastTime);
        var samples = (chunks - 1) * spc + lastN;
        return new Segment(offset + dataOffset, chunks, samples, firstFt, lastFt + BinaryPrimitives.ReadUInt32LittleEndian(lastTime));
    }

    public static List<(string NodeId, string Name, string Unit, string DataType)> Signals(Header h) =>
        h.Channels.Select(c => (c.NodeId, c.Name, c.Unit, c.DataType)).ToList();

    sealed class Cursor : IDisposable
    {
        readonly FileStream stream;
        readonly Channel channel;
        readonly byte[] chunk;
        int segment = -1, remainingChunks, count, at;
        long chunkStart;
        public long Ticks;
        public object Raw = 0L;
        public double Number;
        public long Index;

        public Channel Channel => channel;

        public Cursor(string path, Channel c)
        {
            channel = c;
            stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite, 1 << 16, FileOptions.SequentialScan);
            chunk = new byte[c.SamplesPerChunk * (4 + c.ValueSize)];
        }

        public bool Next()
        {
            while (at >= count)
            {
                if (remainingChunks == 0)
                {
                    if (++segment >= channel.Segments.Count) return false;
                    stream.Seek(channel.Segments[segment].DataOffset, SeekOrigin.Begin);
                    remainingChunks = (int)channel.Segments[segment].Chunks;
                }
                Span<byte> hd = stackalloc byte[12];
                stream.ReadExactly(hd);
                chunkStart = BinaryPrimitives.ReadInt64LittleEndian(hd);
                var n = BinaryPrimitives.ReadUInt32LittleEndian(hd[8..]);
                if (n < 1 || n > channel.SamplesPerChunk) throw new UserError(Fail($"blocco di campioni non valido nel canale {channel.Name}"));
                stream.ReadExactly(chunk, 0, (int)n * (4 + channel.ValueSize));
                count = (int)n; at = 0; remainingChunks--;
            }
            var size = 4 + channel.ValueSize;
            var dt = BinaryPrimitives.ReadUInt32LittleEndian(chunk.AsSpan(at * size));
            var v = chunk.AsSpan(at * size + 4, channel.ValueSize);
            Ticks = chunkStart + dt;
            switch (channel.SourceType.ToUpperInvariant())
            {
                case "REAL32": { var f = BinaryPrimitives.ReadSingleLittleEndian(v); Raw = (double)f; Number = f; break; }
                case "REAL64": { var f = BinaryPrimitives.ReadDoubleLittleEndian(v); Raw = f; Number = f; break; }
                case "BIT" or "BOOL": { Raw = (long)(v[0] != 0 ? 1 : 0); Number = v[0] != 0 ? 1 : 0; break; }
                case "INT8": { Raw = (long)(sbyte)v[0]; Number = (sbyte)v[0]; break; }
                case "UINT8": { Raw = (long)v[0]; Number = v[0]; break; }
                case "INT16": { var x = BinaryPrimitives.ReadInt16LittleEndian(v); Raw = (long)x; Number = x; break; }
                case "UINT16": { var x = BinaryPrimitives.ReadUInt16LittleEndian(v); Raw = (long)x; Number = x; break; }
                case "INT32": { var x = BinaryPrimitives.ReadInt32LittleEndian(v); Raw = (long)x; Number = x; break; }
                case "UINT32": { var x = BinaryPrimitives.ReadUInt32LittleEndian(v); Raw = (long)x; Number = x; break; }
                case "INT64": { var x = BinaryPrimitives.ReadInt64LittleEndian(v); Raw = x; Number = x; break; }
                default: { var x = BinaryPrimitives.ReadUInt64LittleEndian(v); Raw = x > long.MaxValue ? x.ToString(CultureInfo.InvariantCulture) : (long)x; Number = x; break; }
            }
            at++; Index++;
            return true;
        }

        public void Dispose() => stream.Dispose();
    }

    public static IEnumerable<Row> IterRows(string path, System.Text.Json.Nodes.JsonObject? metadata = null, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        var h = Inspect(path);
        var selected = h.Channels.Where(c => signalIds == null || signalIds.Contains(c.NodeId)).ToList();
        var cursors = new List<Cursor>();
        try
        {
            var queue = new PriorityQueue<Cursor, (long, int)>();
            foreach (var c in selected)
            {
                var cursor = new Cursor(path, c);
                cursors.Add(cursor);
                if (cursor.Next()) queue.Enqueue(cursor, (cursor.Ticks, cursors.Count));
            }
            while (queue.TryDequeue(out var cursor, out var key))
            {
                var t = ToMs(cursor.Ticks);
                if ((startMs == null || t >= startMs) && (endMs == null || t <= endMs))
                {
                    var c = cursor.Channel;
                    var finite = double.IsFinite(cursor.Number);
                    var text = Stamp(cursor.Ticks);
                    yield return new Row
                    {
                        ["timestamp_ms"] = t, ["timestamp_utc"] = text, ["node_id"] = c.NodeId, ["signal_name"] = c.Name,
                        ["value_numeric"] = finite ? cursor.Number : null, ["value_text"] = RowValues.Str(cursor.Raw), ["data_type"] = c.DataType,
                        ["good"] = finite, ["quality"] = finite ? "Good" : "Bad", ["status"] = finite ? "SVDX_Good" : "SVDX_Bad",
                        ["sample_ms"] = c.PeriodTicks / 10000.0, ["cycle"] = cursor.Index, ["source_timestamp"] = text, ["server_timestamp"] = null,
                    };
                }
                else if (endMs != null && t > endMs) { /* this channel is past the interval: stop reading it */ continue; }
                if (cursor.Next()) queue.Enqueue(cursor, (cursor.Ticks, key.Item2));
            }
        }
        finally { foreach (var c in cursors) c.Dispose(); }
    }
}
