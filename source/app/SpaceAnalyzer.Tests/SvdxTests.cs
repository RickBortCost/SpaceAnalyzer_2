using System.Buffers.Binary;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using Xunit;

namespace SpaceAnalyzer.Tests;

/// <summary>TwinCAT Scope .svdx: file built with the layout derived from the reference project (94 REAL32 channels, 1 ms).</summary>
public class SvdxTests
{
    const long Start = 134238281553100000; // 2026-05-21 09:09:15.310 UTC
    const int Spc = 4, Period = 10000, Samples = 10;

    static byte[] Block(int valueSize, Func<int, byte[]> value, long start = Start, int samples = Samples, int spc = Spc, int levels = 3)
    {
        var chunks = (samples + spc - 1) / spc;
        var head = new MemoryStream();
        void U64(long v) { var b = new byte[8]; BinaryPrimitives.WriteInt64LittleEndian(b, v); head.Write(b); }
        void U32(uint v) { var b = new byte[4]; BinaryPrimitives.WriteUInt32LittleEndian(b, v); head.Write(b); }
        head.Write(Encoding.ASCII.GetBytes("01.00.00.40"));
        U64(start); U64(1234567); U64(start); U64(start + samples * (long)Period + 2592);
        U64(levels); U64(spc); U64(Period);
        // level descriptors, 56 bytes each from offset 83 (the last one is level 0, the raw chunks)
        var dataOffset = 83 + 56 * levels + 16 * 8;
        for (var k = 0; k < levels; k++)
        {
            var level = levels - 1 - k;
            U64(level == 0 ? chunks : 1); U64(level == 0 ? chunks : 1);
            U32((uint)level); U32((uint)(level == 0 ? dataOffset : 83 + 56 * levels));
            U64(0); U64(0); U64(0); U64(0);
        }
        head.Write(new byte[dataOffset - head.Length]);
        var sampleSize = 4 + valueSize;
        for (var c = 0; c < chunks; c++)
        {
            var n = Math.Min(spc, samples - c * spc);
            U64(start + c * (long)spc * Period); U32((uint)n);
            for (var i = 0; i < n; i++)
            {
                U32((uint)(i * Period));
                head.Write(value(c * spc + i));
            }
        }
        head.Write(new byte[64]);
        return head.ToArray();
    }

    static byte[] F32(float v) => BitConverter.GetBytes(v);
    static byte[] I16(short v) => BitConverter.GetBytes(v);

    static string Acquisition(int handle, string name, string symbol, string type, string guid, string unit = "") =>
        $"<AdsAcquisition><DataType>{type}</DataType><FileHandle>{handle}</FileHandle><Guid>{guid}</Guid><Name>{name}</Name><BaseSampleTime>{Period}</BaseSampleTime>" +
        $"<RawUnit><Unit><Symbol>{unit}</Symbol></Unit></RawUnit><SymbolName>{symbol}</SymbolName></AdsAcquisition>";

    static string Channel(string name, string guid) =>
        $"<Channel><Name>{name}</Name><SubMember><AcquisitionInterpreter><AcquisitionGUID>{guid}</AcquisitionGUID></AcquisitionInterpreter></SubMember></Channel>";

    /// <summary>Two REAL32 channels and one INT16; the first REAL32 is renamed in the project ("Accel X"), the last has no samples in the file.</summary>
    static string Build(string path, Func<List<byte[]>, List<byte[]>>? tamper = null, int samples = Samples)
    {
        var blocks = new List<byte[]>
        {
            Block(4, i => F32(i * 0.5f), samples: samples),
            Block(4, i => F32(-i), samples: samples),
            Block(2, i => I16((short)(i * 100)), samples: samples),
        };
        if (tamper != null) blocks = tamper(blocks);
        var handles = new[] { 1, 2, 5 };
        var xml = "<?xml version=\"1.0\" encoding=\"utf-8\"?><ScopeProject><Title>Prova</Title><SubMember>" +
            Acquisition(1, "fX", "Main.Acc.fX", "REAL32", "g1", "m/s2") + Acquisition(2, "fY", "Main.Acc.fY", "REAL32", "g2") +
            Acquisition(5, "nCount", "Main.nCount", "INT16", "g3") + Acquisition(9, "vuoto", "Main.vuoto", "REAL32", "g4") +
            Channel("Accel X", "g1") + "</SubMember></ScopeProject>";
        var xmlBytes = new UTF8Encoding(true).GetBytes(xml);
        var headerSize = 20 + 20 * blocks.Count;
        var offsets = new List<long>();
        var cursor = (long)headerSize;
        foreach (var b in blocks) { offsets.Add(cursor); cursor += b.Length; }
        using var file = new MemoryStream();
        void U64(long v) { var b = new byte[8]; BinaryPrimitives.WriteInt64LittleEndian(b, v); file.Write(b); }
        void U32(uint v) { var b = new byte[4]; BinaryPrimitives.WriteUInt32LittleEndian(b, v); file.Write(b); }
        U64(cursor); U64(xmlBytes.Length); U32((uint)blocks.Count);
        for (var i = 0; i < blocks.Count; i++) { U64(offsets[i]); U64(blocks[i].Length); U32((uint)handles[i]); }
        foreach (var b in blocks) file.Write(b);
        file.Write(xmlBytes);
        File.WriteAllBytes(path, file.ToArray());
        return path;
    }

    [Fact]
    public void ReadsChannelsNamesUnitsAndTimeRange()
    {
        using var dir = new TempDir();
        var h = SvdxReader.Inspect(Build(Path.Combine(dir.Path, "p.svdx")));
        Assert.Equal(["Accel X", "fY", "nCount"], h.Channels.Select(c => c.Name));
        Assert.Equal(["Float32", "Float32", "Int16"], h.Channels.Select(c => c.DataType));
        Assert.Equal("m/s2", h.Channels[0].Unit);
        Assert.Equal(3 * Samples, h.Samples);
        Assert.Equal(1.0, h.SampleMs);
        Assert.Equal(SvdxReader.ToMs(Start), h.StartMs);
        Assert.Equal(SvdxReader.ToMs(Start + 9L * Period), h.EndMs);
    }

    [Fact]
    public void RowsAreOrderedInTimeAndCarryValuesAndAbsoluteTimestamps()
    {
        using var dir = new TempDir();
        var rows = SvdxReader.IterRows(Build(Path.Combine(dir.Path, "p.svdx"))).ToList();
        Assert.Equal(3 * Samples, rows.Count);
        Assert.Equal(rows.Select(r => (double)r["timestamp_ms"]!).OrderBy(x => x), rows.Select(r => (double)r["timestamp_ms"]!));
        var x = rows.Where(r => (string)r["signal_name"]! == "Accel X").ToList();
        Assert.Equal(Enumerable.Range(0, Samples).Select(i => i * 0.5), x.Select(r => (double)r["value_numeric"]!));
        Assert.Equal("2026-05-21T09:09:15.310000000+00:00", x[0]["timestamp_utc"]);
        Assert.Equal("2026-05-21T09:09:15.319000000+00:00", x[^1]["timestamp_utc"]);
        Assert.Equal(Enumerable.Range(1, Samples).Select(i => (long)i), x.Select(r => (long)r["cycle"]!));
        Assert.All(rows, r => Assert.Equal("Good", r["quality"]));
        var counts = rows.Where(r => (string)r["signal_name"]! == "nCount").Select(r => (double)r["value_numeric"]!).ToList();
        Assert.Equal(Enumerable.Range(0, Samples).Select(i => i * 100d), counts);
    }

    [Fact]
    public void SignalAndIntervalSelection()
    {
        using var dir = new TempDir();
        var path = Build(Path.Combine(dir.Path, "p.svdx"));
        var h = SvdxReader.Inspect(path);
        var only = SvdxReader.IterRows(path, null, new HashSet<string> { h.Channels[1].NodeId }, SvdxReader.ToMs(Start + 2L * Period), SvdxReader.ToMs(Start + 5L * Period)).ToList();
        Assert.Equal([-2d, -3d, -4d, -5d], only.Select(r => (double)r["value_numeric"]!));
    }

    [Fact]
    public void ServesAsRecordingAndConvertsToCsv()
    {
        using var dir = new TempDir();
        Build(Path.Combine(dir.Path, "prova.svdx"));
        var library = new RecordingLibrary(dir.Path);
        var info = library.Describe("prova.svdx");
        Assert.Equal("svdx", info["format"]);
        Assert.Equal(3L * Samples, Convert.ToInt64(info["rows_written"]));
        Assert.Equal(3, ((List<object?>)info["signals"]!).Count);
        var conversions = new Conversions(() => Path.Combine(dir.Path, Conversions.Folder));
        var job = conversions.Start(new JsonObject { ["name"] = "prova.svdx", ["format"] = "csv" }, _ => library);
        var deadline = DateTime.UtcNow.AddSeconds(60);
        while ((string?)job["state"] == "running" && DateTime.UtcNow < deadline) { Thread.Sleep(20); job = conversions.Status((string)job["id"]!); }
        Assert.True((string?)job["state"] == "ready", "conversione non riuscita: " + job.GetValueOrDefault("error"));
        var file = Assert.Single(((List<object?>)job["files"]!).Cast<Dictionary<string, object?>>());
        var converted = library.Describe((string)file["name"]!);
        Assert.Equal(3L * Samples, Convert.ToInt64(converted["rows_written"]));
    }

    [Fact]
    public void RejectsFilesThatAreNotScopeProjects()
    {
        using var dir = new TempDir();
        var path = Path.Combine(dir.Path, "x.svdx");
        File.WriteAllBytes(path, new byte[200]);
        Assert.False(SvdxReader.IsSvdx(path));
        Assert.Throws<UserError>(() => SvdxReader.Inspect(path));
        var truncated = Build(Path.Combine(dir.Path, "t.svdx"));
        File.WriteAllBytes(truncated, File.ReadAllBytes(truncated).AsSpan(0, 300).ToArray());
        Assert.Throws<UserError>(() => SvdxReader.Inspect(truncated));
    }

    [Fact]
    public void ChunkLargerThanDeclaredIsRefusedInsteadOfMisread()
    {
        using var dir = new TempDir();
        var path = Build(Path.Combine(dir.Path, "p.svdx"), blocks =>
        {
            // first chunk of the first block claims 9 samples while the block declares 4 per chunk
            var offset = 83 + 56 * 3 + 16 * 8 + 8;
            BinaryPrimitives.WriteUInt32LittleEndian(blocks[0].AsSpan(offset), 9);
            return blocks;
        });
        Assert.Throws<UserError>(() => SvdxReader.Inspect(path));
    }

    /// <summary>With the reference project (SVDX_SAMPLE=path): 94 REAL32 channels of 19000 samples each, first values taken from the file with an independent script.</summary>
    [Fact]
    public void ReferenceProject()
    {
        var sample = Environment.GetEnvironmentVariable("SVDX_SAMPLE");
        if (string.IsNullOrEmpty(sample) || !File.Exists(sample)) return;
        var h = SvdxReader.Inspect(sample);
        Assert.Equal(94, h.Channels.Count);
        Assert.All(h.Channels, c => Assert.Equal(19000, c.Samples));
        Assert.Equal(1.0, h.SampleMs);
        var first = SvdxReader.IterRows(sample, null, new HashSet<string> { h.Channels[0].NodeId }).Take(2).ToList();
        Assert.Equal(-0.5173373818397522, (double)first[0]["value_numeric"]!);
        var total = SvdxReader.IterRows(sample).LongCount();
        Assert.Equal(94L * 19000, total);
        // Full conversions of the reference project: all 94 channels to CSV/Parquet, the first 64 to FDA and RBF.
        var dir = Path.Combine(Path.GetTempPath(), "svdx_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        try
        {
            File.Copy(sample, Path.Combine(dir, "scope.svdx"));
            var library = new RecordingLibrary(dir);
            var conversions = new Conversions(() => Path.Combine(dir, Conversions.Folder));
            foreach (var (format, count, expected) in new[] { ("csv", 94, 94L * 19000), ("parquet", 94, 94L * 19000), ("das", 64, 64L * 19000), ("rbf", 64, 64L * 19000) })
            {
                var request = new JsonObject { ["name"] = "scope.svdx", ["format"] = format, ["signal_ids"] = new JsonArray(h.Channels.Take(count).Select(c => (JsonNode)JsonValue.Create(c.NodeId)!).ToArray()) };
                var job = conversions.Start(request, _ => library);
                var deadline = DateTime.UtcNow.AddMinutes(5);
                while ((string?)job["state"] == "running" && DateTime.UtcNow < deadline) { Thread.Sleep(50); job = conversions.Status((string)job["id"]!); }
                Assert.True((string?)job["state"] == "ready", format + ": " + job.GetValueOrDefault("error"));
                Assert.Equal(expected, Convert.ToInt64(job["rows_read"]));
            }
        }
        finally { Directory.Delete(dir, true); }
    }
}
