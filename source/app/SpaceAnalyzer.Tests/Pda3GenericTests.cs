using System.Buffers.Binary;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using Xunit;

namespace SpaceAnalyzer.Tests;

/// <summary>PDA3 DAT outside the validated model (ibaPDA layout): Double RLE values, interleaved blocks, unreadable header and index.</summary>
public class Pda3GenericTests
{
    const int Samples = 3000;
    const long PeriodTicks = 100000; // 10 ms
    const ulong StartTicks = 638930350534600000; // 2025-09-09 17:17:33.460 (internal calendar)

    static double Ramp(int i) => i * 0.01;
    static double Wave(int i) => 10 + 0.5 * Math.Sin(i / 50.0);

    static byte[] Literal(IEnumerable<double> values)
    {
        var data = values.ToArray();
        using var stream = new MemoryStream();
        for (var i = 0; i < data.Length; i += 127)
        {
            var count = Math.Min(127, data.Length - i);
            stream.WriteByte((byte)(128 + count));
            for (var j = 0; j < count; j++)
            {
                var buffer = new byte[8];
                BinaryPrimitives.WriteDoubleLittleEndian(buffer, data[i + j]);
                stream.Write(buffer);
            }
        }
        return stream.ToArray();
    }

    static byte[] Constant(double value, int count)
    {
        var record = new byte[11];
        record[0] = 127;
        BinaryPrimitives.WriteDoubleLittleEndian(record.AsSpan(1), value);
        BinaryPrimitives.WriteUInt16LittleEndian(record.AsSpan(9), (ushort)count);
        return record;
    }

    /// <summary>
    /// Ramp and wave flush 1300-sample blocks in time order (ramp first, then wave), then final blocks in channel order:
    /// 1 wave (400), 2 constant 5 (3000), 3 ramp (400), 4 zeros (3000).
    /// </summary>
    static string Write(TempDir dir)
    {
        var blocks = new List<byte[]>
        {
            Literal(Enumerable.Range(0, 1300).Select(Ramp)), Literal(Enumerable.Range(0, 1300).Select(Wave)),
            Literal(Enumerable.Range(1300, 1300).Select(Ramp)), Literal(Enumerable.Range(1300, 1300).Select(Wave)),
            Literal(Enumerable.Range(2600, 400).Select(Wave)), Constant(5, Samples), Literal(Enumerable.Range(2600, 400).Select(Ramp)), Constant(0, Samples),
        };
        const int headerExtra = 64;
        var dataStart = 80 + headerExtra;
        var footer = dataStart + blocks.Sum(b => 4 + b.Length);
        var file = new byte[footer + 16 + 16 * blocks.Count];
        "PDA3"u8.CopyTo(file);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(8), 64);
        BinaryPrimitives.WriteUInt64LittleEndian(file.AsSpan(16), (ulong)footer);
        BinaryPrimitives.WriteUInt64LittleEndian(file.AsSpan(40), StartTicks);
        BinaryPrimitives.WriteUInt64LittleEndian(file.AsSpan(48), StartTicks + Samples * PeriodTicks);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(72), headerExtra);
        new Random(7).NextBytes(file.AsSpan(80, headerExtra)); // proprietary header: not interpreted
        var cursor = dataStart;
        foreach (var block in blocks)
        {
            BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(cursor), (uint)block.Length);
            block.CopyTo(file, cursor + 4);
            cursor += 4 + block.Length;
        }
        BinaryPrimitives.WriteUInt64LittleEndian(file.AsSpan(footer), 0);
        BinaryPrimitives.WriteUInt64LittleEndian(file.AsSpan(footer + 8), (ulong)blocks.Count);
        new Random(9).NextBytes(file.AsSpan(footer + 16)); // proprietary index: not interpreted
        var path = Path.Combine(dir.Path, "ibapda.dat");
        File.WriteAllBytes(path, file);
        return path;
    }

    [Fact]
    public void RebuildsChannelsFromBlockStructure()
    {
        using var dir = new TempDir();
        var path = Write(dir);
        var reader = Pda3.Reader(path);
        Assert.Equal(8, reader.ElementSize);
        var layout = Assert.IsType<Pda3Layout>(reader.Generic);
        Assert.Equal(4, layout.Channels);
        Assert.Equal(Samples, layout.Samples);
        Assert.Equal(10.0, layout.PeriodMs);
        Assert.Equal([[1, 3, 4], [5], [0, 2, 6], [7]], layout.ChannelBlocks);
        Assert.True(layout.Strict);
        Assert.True(layout.Exhaustive);
        Assert.Null(layout.SecondCost);

        var meta = new JsonObject { ["pda3_timezone_offset_minutes"] = 120 };
        var info = Pda3.Details(path, meta);
        Assert.Equal(Samples * 4L, info["rows_written"]!.GetValue<long>());
        Assert.Equal(30.0, info["duration_seconds"]!.GetValue<double>());
        Assert.Equal("2025-09-09T15:17:33.460000+00:00", info["started_at"]!.GetValue<string>());
        Assert.Equal(["Canale 01", "Canale 02", "Canale 03", "Canale 04"], info["signals"]!.AsArray().Select(s => s!["display_name"]!.GetValue<string>()));

        var rows = Pda3.IterRows(path, meta).ToList();
        Assert.Equal(Samples * 4, rows.Count);
        var start = info["real_start_ms"]!.GetValue<double>();
        double[] Channel(string id) => rows.Where(r => (string)r["node_id"]! == id).OrderBy(r => (double)r["timestamp_ms"]!).Select(r => (double)r["value_numeric"]!).ToArray();
        Assert.Equal(Enumerable.Range(0, Samples).Select(Wave), Channel("pda3:ch01"));
        Assert.All(Channel("pda3:ch02"), v => Assert.Equal(5.0, v));
        Assert.Equal(Enumerable.Range(0, Samples).Select(Ramp), Channel("pda3:ch03"));
        Assert.All(Channel("pda3:ch04"), v => Assert.Equal(0.0, v));
        var ramp = rows.Where(r => (string)r["node_id"]! == "pda3:ch03").Select(r => (double)r["timestamp_ms"]!).OrderBy(t => t).ToArray();
        Assert.Equal(start, ramp[0]);
        Assert.Equal(start + (Samples - 1) * 10.0, ramp[^1]);

        // Interval selection, as used by the conversion.
        var part = Pda3.IterRows(path, meta, new HashSet<string> { "pda3:ch03" }, start + 13000, start + 13090).ToList();
        Assert.Equal(10, part.Count);
        Assert.Equal(Ramp(1300), (double)part[0]["value_numeric"]!, 9);
    }

    [Fact]
    public void ConvertsGenericDatToParquet()
    {
        using var dir = new TempDir();
        Write(dir);
        Py.AtomicPlain(Path.Combine(dir.Path, "ibapda.metadata.json"), new Dictionary<string, object?> { ["pda3_timezone_offset_minutes"] = 0 });
        var library = new RecordingLibrary(dir.Path);
        var conversions = new Conversions(() => Path.Combine(dir.Path, Conversions.Folder));
        var job = conversions.Start(new JsonObject { ["name"] = "ibapda.dat", ["format"] = "parquet" }, _ => library);
        var deadline = DateTime.UtcNow.AddSeconds(60);
        while ((string?)job["state"] == "running" && DateTime.UtcNow < deadline) { Thread.Sleep(20); job = conversions.Status((string)job["id"]!); }
        Assert.True((string?)job["state"] == "ready", (string?)job.GetValueOrDefault("error"));
        Assert.Equal(Samples * 4L, job["rows_read"]);
        var name = (string)((List<object?>)job["files"]!).Cast<Dictionary<string, object?>>().Single()["name"]!;
        var values = library.IterRows(library.PathOf(name)).Where(r => (string)r["node_id"]! == "pda3:ch03")
            .OrderBy(r => (double)r["timestamp_ms"]!).Select(r => (double)r["value_numeric"]!).ToArray();
        Assert.Equal(Enumerable.Range(0, Samples).Select(Ramp), values);
    }
}
