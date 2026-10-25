using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Analysis;
using SpaceAnalyzer.App.Calculations;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using SpaceAnalyzer.App.Web;
using Xunit;

namespace SpaceAnalyzer.Tests;

public sealed class TempDir : IDisposable
{
    public string Path { get; } = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "sa-tests-" + Guid.NewGuid().ToString("N"));
    public TempDir() => Directory.CreateDirectory(Path);
    public void Dispose() { try { Directory.Delete(Path, true); } catch (IOException) { } }
}

public class FftTests
{
    const double Base = 1700000000000;

    static List<Row> Sine(int count, double hz, double fs = 1000, int? gapAt = null)
    {
        var rows = new List<Row>();
        for (var i = 0; i < count; i++)
        {
            var t = Base + i * 1000 / fs + (gapAt != null && i >= gapAt ? 50 : 0);
            rows.Add(new Row { ["node_id"] = "x", ["timestamp_ms"] = t, ["value_numeric"] = Math.Sin(2 * Math.PI * hz * i / fs), ["quality"] = "Good" });
        }
        return rows;
    }

    [Fact]
    public void PeakAtSineFrequencyWithoutResampling()
    {
        var result = Fft.Analyze(Sine(256, 125), "x", Base, Base + 1000);
        Assert.Equal(1000.0, (double)result["sample_hz"]!, 6);
        Assert.Equal(125.0, (double)result["peak_hz"]!, 6);
        Assert.Equal(1.0, (double)result["peak_amplitude"]!, 2);
        Assert.False((bool)result["resampled"]!);
        Assert.Equal(256L, result["samples_original"]);
    }

    [Fact]
    public void TimingBreakIsRejectedUnlessSkipped()
    {
        var error = Assert.Throws<UserError>(() => Fft.Analyze(Sine(512, 125, gapAt: 300), "x", Base, Base + 2000));
        Assert.Contains("buco temporale", error.Message);
        var skipped = Fft.Analyze(Sine(512, 125, gapAt: 300), "x", Base, Base + 2000, options: new JsonObject { ["gap_policy"] = "skip", ["block_size"] = 128 });
        Assert.True((long)skipped["timing_breaks"]! >= 1);
        Assert.Equal(125.0, (double)skipped["peak_hz"]!, 6);
    }

    [Fact]
    public void TooFewSamples()
    {
        Assert.Throws<UserError>(() => Fft.Analyze(Sine(10, 125), "x", Base, Base + 1000));
    }
}

public class RecordingTests
{
    static IEnumerable<Row> Rows(int count, string nid, Func<int, double?> value, string quality = "Good")
    {
        for (var i = 0; i < count; i++)
            yield return new Row
            {
                ["timestamp_utc"] = SpaceAnalyzer.Engine.Core.Values.IsoFromMs(1700000000000 + i * 10), ["timestamp_ms"] = 1700000000000.0 + i * 10, ["elapsed_ms"] = i * 10.0,
                ["cycle"] = (long)i + 1, ["node_id"] = nid, ["signal_name"] = "Segnale, \"A\"", ["value_numeric"] = value(i), ["value_text"] = value(i)?.ToString(),
                ["data_type"] = "Double", ["status"] = quality, ["good"] = quality == "Good", ["sample_ms"] = 10L, ["quality"] = quality,
            };
    }

    [Theory]
    [InlineData("csv")]
    [InlineData("parquet")]
    public void ExportDescribeLoadAndMarkers(string format)
    {
        using var dir = new TempDir();
        var path = Path.Combine(dir.Path, "rec." + format);
        var count = Export.ExportRows(Rows(1000, "ns=2;s=A", i => i), path, format);
        Assert.Equal(1000, count);
        var library = new RecordingLibrary(dir.Path);
        var description = library.Describe("rec." + format);
        Assert.Equal(format, description["format"]);
        Assert.Equal(9.99, (double)description["duration_seconds"]!, 6);
        var signal = Assert.Single((List<object?>)description["signals"]!);
        Assert.Equal("ns=2;s=A", ((Dictionary<string, object?>)signal!)["nodeid"]);
        var load = library.Load("rec." + format);
        var series = (List<object?[]>)((Dictionary<string, object?>)load["series"]!)["ns=2;s=A"]!;
        Assert.Equal(1000, series.Count);
        Assert.Equal(999.0, (double)series[^1][1]!);
        var markers = library.Markers("rec." + format, new JsonArray("ns=2;s=A"), JsonValue.Create(1700000000004.0), JsonValue.Create(1700000000500.0));
        var first = (Dictionary<string, object?>)((List<object?>)markers["signals"]!)[0]!;
        Assert.Equal(0.0, (double)((Dictionary<string, object?>)first["x1"]!)["value"]!);
        Assert.Equal(50.0, (double)((Dictionary<string, object?>)first["x2"]!)["value"]!);
    }

    [Fact]
    public void BadQualityKeepsTimestampWithoutValue()
    {
        using var dir = new TempDir();
        Export.ExportRows(Rows(20, "x", i => i).Concat(Rows(5, "x", _ => 1, "Bad")), Path.Combine(dir.Path, "q.parquet"), "parquet");
        var rows = ParquetRows.Read(Path.Combine(dir.Path, "q.parquet")).ToList();
        Assert.Equal(25, rows.Count);
        Assert.Equal(5, rows.Count(r => (string?)r["quality"] == "Bad"));
        var load = new RecordingLibrary(dir.Path).Load("q.parquet");
        var series = (List<object?[]>)((Dictionary<string, object?>)load["series"]!)["x"]!;
        Assert.All(series.Where(p => (string?)p[2] == "Bad"), p => Assert.Null(p[1]));
    }

    [Theory]
    [InlineData("csv")]
    [InlineData("parquet")]
    public void FullResolutionKeepsEverySampleInTimeOrder(string format)
    {
        using var dir = new TempDir();
        // 120.000 samples (above every former display budget), written in reverse order, with Bad samples.
        var rows = Rows(120000, "ns=2;s=A", i => i == 4321 ? 1e6 : i).Concat(Rows(3, "ns=2;s=A", _ => 7, "Bad")).Reverse();
        Export.ExportRows(rows, Path.Combine(dir.Path, "full." + format), format);
        var view = new RecordingLibrary(dir.Path).LoadFull("full." + format, new JsonArray("ns=2;s=A"));
        var s = Assert.Single(view.Series);
        Assert.Equal(120003, s.Count);
        for (var i = 1; i < s.Count; i++) Assert.True(s.T[i] >= s.T[i - 1]);
        Assert.Equal(1e6, s.V.ToArray().Max());
        Assert.Equal(3, s.Q.ToArray().Count(q => q == SampleColumns.Bad));
        Assert.All(Enumerable.Range(0, s.Count).Where(i => s.Q[i] == SampleColumns.Bad), i => Assert.True(double.IsNaN(s.V[i])));
        Assert.Equal(false, view.Info["decimated"]);
        var slice = new RecordingLibrary(dir.Path).LoadFull("full." + format, new JsonArray("ns=2;s=A"), JsonValue.Create(1700000000000.0 + 100), JsonValue.Create(1700000000000.0 + 190));
        Assert.Equal(10, slice.Series[0].Count);
    }

    [Fact]
    public void SamplePayloadLayout()
    {
        var column = new SampleColumns("x");
        column.Add(1, 2.5, SampleColumns.Good);
        column.Add(2, null, SampleColumns.Bad);
        column.Add(3, 4, SampleColumns.Uncertain);
        using var buffer = new MemoryStream();
        new SamplePayload(new Dictionary<string, object?> { ["signals"] = new List<object?> { new Dictionary<string, object?> { ["nodeid"] = "x", ["points_returned"] = 3L } } }, [column])
            .WriteAsync(buffer, CancellationToken.None).GetAwaiter().GetResult();
        var bytes = buffer.ToArray();
        Assert.Equal("SAS1", System.Text.Encoding.ASCII.GetString(bytes, 0, 4));
        var headerLength = (int)BitConverter.ToUInt32(bytes, 4);
        var offset = 8 + headerLength + (8 - headerLength % 8) % 8;
        Assert.Equal(new[] { 1.0, 2, 3 }, Enumerable.Range(0, 3).Select(i => BitConverter.ToDouble(bytes, offset + i * 8)));
        offset += 24;
        Assert.Equal(2.5, BitConverter.ToDouble(bytes, offset));
        Assert.True(double.IsNaN(BitConverter.ToDouble(bytes, offset + 8)));
        Assert.True(double.IsNaN(BitConverter.ToDouble(bytes, offset + 16)));
        Assert.Equal(new byte[] { 0, 2, 1 }, bytes[(offset + 24)..(offset + 27)]);
        Assert.Equal(offset + 32, bytes.Length);
    }

    [Fact]
    public void ExportOfEmptyIntervalFails()
    {
        using var dir = new TempDir();
        Assert.Throws<UserError>(() => Export.ExportRows(Array.Empty<Row>(), Path.Combine(dir.Path, "e.csv"), "csv"));
        Assert.False(File.Exists(Path.Combine(dir.Path, "e.csv")));
    }

    [Fact]
    public void PathsOutsideTheFolderAreRefused()
    {
        using var dir = new TempDir();
        var library = new RecordingLibrary(dir.Path);
        Assert.Throws<UserError>(() => library.PathOf("../x.csv"));
        Assert.Throws<UserError>(() => library.PathOf("a\\b.csv"));
        Assert.Throws<UserError>(() => library.PathOf("missing.parquet"));
    }

    [Fact]
    public void PeakSeriesKeepsExtremaAndQualityWithinBudget()
    {
        var series = new PeakSeries(1000);
        for (var i = 0; i < 200000; i++)
            series.Add(i, i == 123456 ? 1e6 : i % 7, i == 150000 ? "Bad" : "Good");
        var points = series.Result();
        Assert.True(points.Count <= 1100, points.Count.ToString());
        Assert.Contains(points, p => (double?)p[1] == 1e6);
        Assert.Contains(points, p => (string?)p[2] == "Bad");
        Assert.Equal(0.0, (double)points[0][0]!);
        Assert.Equal(199999.0, (double)points[^1][0]!);
        Assert.Equal(200000, series.Count);
    }
}

public class ParsingTests
{
    [Fact]
    public void CsvExcelDialect()
    {
        var text = "a,b,c\r\n1,\"x, \"\"y\"\"\",\"multi\nline\"\r\n\r\n2,,\r\n";
        var records = CsvRows.Records(new StringReader(text)).Where(r => r.Length > 0).ToList();
        Assert.Equal(3, records.Count);
        Assert.Equal(new[] { "1", "x, \"y\"", "multi\nline" }, records[1]);
        Assert.Equal(new[] { "2", "", "" }, records[2]);
    }

    [Theory]
    [InlineData(" 1_000.5 ", 1000.5)]
    [InlineData("-inf", double.NegativeInfinity)]
    [InlineData("1e3", 1000)]
    public void PythonFloat(string text, double expected)
    {
        Assert.True(Py.TryFloat(text, out var value));
        Assert.Equal(expected, value);
    }

    [Theory]
    [InlineData("1__0")]
    [InlineData("1,5")]
    [InlineData("")]
    public void PythonFloatRejects(string text) => Assert.False(Py.TryFloat(text, out _));

    [Fact]
    public void TemplateIncludesAndEscapes()
    {
        using var dir = new TempDir();
        File.WriteAllText(Path.Combine(dir.Path, "page.html"), "<b>{{ name }}</b>{% include 'part.html' %}");
        File.WriteAllText(Path.Combine(dir.Path, "part.html"), "<i>{{ name }}</i>");
        var html = Templates.Render(dir.Path, "page.html", new Dictionary<string, string> { ["name"] = "<x&y>" });
        Assert.Equal("<b>&lt;x&amp;y&gt;</b><i>&lt;x&amp;y&gt;</i>", html);
    }
}

public class FormulaTests
{
    [Fact]
    public void CausalEvaluationWithAgeLimit()
    {
        var f = FormulaStore.Normalize(new JsonObject
        {
            ["name"] = "Doppio", ["expression"] = "[A]*2+[B]", ["max_age_ms"] = 100,
            ["bindings"] = new JsonObject { ["A"] = new JsonObject { ["nodeid"] = "a" }, ["B"] = new JsonObject { ["nodeid"] = "b" } },
        });
        var plan = new Plan([f], "live");
        var engine = new FormulaEngine(plan);
        Assert.DoesNotContain(engine.Process(0, [new FormulaInput("a", 1, "Good")]), r => (string)r["quality"]! == "Good");
        var ok = Assert.Single(engine.Process(10, [new FormulaInput("b", 3, "Good")]));
        Assert.Equal(5.0, ok["value_numeric"]);
        var stale = Assert.Single(engine.Process(500, [new FormulaInput("b", 3, "Good")]));
        Assert.Equal("Bad", stale["quality"]);
        Assert.Contains("fuori tolleranza", (string)stale["status"]!);
        var division = FormulaStore.Normalize(new JsonObject { ["name"] = "Div", ["expression"] = "[A]/0", ["bindings"] = new JsonObject { ["A"] = new JsonObject { ["nodeid"] = "a" } } });
        var result = Assert.Single(new FormulaEngine(new Plan([division], "live")).Process(0, [new FormulaInput("a", 1, "Good")]));
        Assert.Equal("Divisione per zero", result["status"]);
    }

    [Fact]
    public void LateSamplesAreCountedNotUsed()
    {
        var f = FormulaStore.Normalize(new JsonObject { ["name"] = "Copia", ["expression"] = "[A]", ["bindings"] = new JsonObject { ["A"] = new JsonObject { ["nodeid"] = "a" } } });
        var engine = new FormulaEngine(new Plan([f], "live"));
        engine.Process(100, [new FormulaInput("a", 1, "Good")]);
        Assert.Empty(engine.Process(50, [new FormulaInput("a", 2, "Good")]));
        Assert.Equal(1, engine.LateSamples);
    }

    [Fact]
    public void IntervalStatisticsOverOriginalRows()
    {
        var rows = Enumerable.Range(0, 11).Select(i => new Row
        {
            ["node_id"] = "x", ["timestamp_ms"] = 1000.0 + i * 100, ["value_numeric"] = (double)i, ["quality"] = "Good", ["sample_ms"] = 100L,
        });
        var result = (Dictionary<string, object?>)IntervalStatistics.Summarize(rows, ["x"], 1000, 2000, 1000, 2000).Single()!;
        var stats = (Dictionary<string, object?>)result["statistics"]!;
        Assert.Equal(11L, result["count"]);
        Assert.Equal(5.0, (double)stats["mean"]!, 9);
        Assert.Equal(10.0, (double)stats["delta"]!, 9);
        Assert.Equal(10.0, (double)stats["slope"]!, 9);
        Assert.Equal(5.0, (double)stats["integral"]!, 9);
    }
}
