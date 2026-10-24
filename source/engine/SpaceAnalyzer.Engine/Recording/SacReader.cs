using System.Buffers.Binary;
using System.IO.Compression;
using System.IO.Hashing;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>Verified sequential reader of a primary capture (as primary_format.iter_blocks/iter_samples).</summary>
public sealed class SacReader : IDisposable
{
    readonly FileStream stream;
    public JsonDocument Header { get; }
    public IReadOnlyList<NodeSpec> Channels { get; }

    public SacReader(string path)
    {
        stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        Span<byte> prefix = stackalloc byte[20];
        if (stream.Read(prefix) != 20) throw new InvalidDataException("Header primario troncato");
        if (!prefix[..8].SequenceEqual(SacFormat.Magic) || BinaryPrimitives.ReadUInt32LittleEndian(prefix[8..]) != SacFormat.Version)
            throw new InvalidDataException("Formato/versione primario non supportato");
        var size = (int)BinaryPrimitives.ReadUInt32LittleEndian(prefix[12..]);
        if (size <= 0 || size > SacFormat.MaxHeader) throw new InvalidDataException("Header primario non valido");
        var payload = new byte[size];
        stream.ReadExactly(payload);
        if (Crc32.HashToUInt32(payload) != BinaryPrimitives.ReadUInt32LittleEndian(prefix[16..])) throw new InvalidDataException("Checksum header primario non valido");
        Header = JsonDocument.Parse(payload);
        Channels = Header.RootElement.GetProperty("channels").EnumerateArray().Select(NodeSpec.FromJson).ToList();
    }

    public sealed record Block(byte Kind, ulong Sequence, ulong First, uint Count, JsonDocument Value);

    public IEnumerable<Block> Blocks()
    {
        ulong expected = 1, next = 1;
        var prefix = new byte[36];
        var checksum = new byte[4];
        while (true)
        {
            var read = stream.ReadAtLeast(prefix, 36, throwOnEndOfStream: false);
            if (read == 0) yield break;
            if (read != 36) throw new InvalidDataException("Header blocco incompleto");
            var kind = prefix[4];
            var flags = prefix[5];
            var sequence = BinaryPrimitives.ReadUInt64LittleEndian(prefix.AsSpan(8));
            var first = BinaryPrimitives.ReadUInt64LittleEndian(prefix.AsSpan(16));
            var count = BinaryPrimitives.ReadUInt32LittleEndian(prefix.AsSpan(24));
            var stored = (int)BinaryPrimitives.ReadUInt32LittleEndian(prefix.AsSpan(28));
            var raw = (int)BinaryPrimitives.ReadUInt32LittleEndian(prefix.AsSpan(32));
            if (!prefix.AsSpan(0, 4).SequenceEqual("SAB1"u8) || kind is < 1 or > 3 || flags > 1 || prefix[6] != 0 || prefix[7] != 0 || sequence != expected || first != next)
                throw new InvalidDataException("Sequenza/schema blocco non valido");
            if (stored <= 0 || stored > SacFormat.MaxPayload || raw <= 0 || raw > SacFormat.MaxPayload) throw new InvalidDataException("Dimensione blocco non valida");
            stream.ReadExactly(checksum);
            var body = new byte[stored];
            stream.ReadExactly(body);
            var crc = new Crc32();
            crc.Append(prefix);
            crc.Append(body);
            if (crc.GetCurrentHashAsUInt32() != BinaryPrimitives.ReadUInt32LittleEndian(checksum)) throw new InvalidDataException("Checksum blocco non valido");
            if (flags == 1)
            {
                using var zlib = new ZLibStream(new MemoryStream(body), CompressionMode.Decompress);
                using var inflated = new MemoryStream(raw);
                zlib.CopyTo(inflated);
                body = inflated.ToArray();
            }
            if (body.Length != raw) throw new InvalidDataException("Lunghezza blocco non valida");
            yield return new Block(kind, sequence, first, count, JsonDocument.Parse(body));
            expected++;
            next += count;
        }
    }

    /// <summary>Samples in capture order with their acquisition timestamp and period.</summary>
    public IEnumerable<(double t, int period, Sample sample)> Samples()
    {
        foreach (var block in Blocks())
        {
            using (block.Value)
            {
                if (block.Kind != SacFormat.Data) continue;
                foreach (var row in block.Value.RootElement.EnumerateArray())
                    yield return (row[0].GetDouble(), row[21].GetInt32(), ToSample(row));
            }
        }
    }

    Sample ToSample(JsonElement row)
    {
        string? S(int i) => row[i].ValueKind == JsonValueKind.Null ? null : row[i].ValueKind == JsonValueKind.String ? row[i].GetString() : row[i].GetRawText();
        long? L(int i) => row[i].ValueKind == JsonValueKind.Number && row[i].TryGetInt64(out var v) ? v : null;
        var value = Values.FromJson(row[8]);
        var source = Values.FromJson(row[17]);
        var quality = S(10);
        var sample = new Sample
        {
            NodeId = Channels[row[2].GetInt32()].NodeId,
            ReceivedNs = long.Parse(row[1].GetString()!, System.Globalization.CultureInfo.InvariantCulture),
            ConnectionId = S(3), SampleSequence = L(4), ConnectionSequence = L(5), SourceEpoch = S(6), SourceSequence = L(7),
            Value = value, DataType = S(9), Quality = quality, Status = S(11), StatusCode = L(12), SourceTs = S(13), ServerTs = S(14),
            SourceFiletime = S(15), LocalTs = S(16), HasSourceValue = true, SourceValue = source, SourcePicoseconds = L(18),
            ServerPicoseconds = L(19), AcquisitionOrigin = S(20), ClockDiscontinuity = row[22].ValueKind == JsonValueKind.True,
            AcquisitionTimestampMs = row[23].ValueKind == JsonValueKind.Number ? row[23].GetDouble() : null,
            SourceBinary = Values.FromJson(row[24]) as byte[], Good = quality == "Good",
        };
        // As primary_tools export: plot value from the original value.
        var original = source ?? value;
        // Python bool is an int: True/False plot as 1.0/0.0.
        sample.Numeric = original is bool || Values.IsNumeric(original);
        sample.PlotValue = original is bool flag ? (flag ? 1.0 : 0.0) : sample.Numeric ? Values.AsDouble(original) : null;
        return sample;
    }

    public void Dispose()
    {
        Header.Dispose();
        stream.Dispose();
    }
}
