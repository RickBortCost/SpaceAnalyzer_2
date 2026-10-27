using SpaceAnalyzer.App.Influx;
using SpaceAnalyzer.App.Infrastructure;
using Xunit;

namespace SpaceAnalyzer.Tests;

public class InfluxTests
{
    // Result of a real-looking query: two blocks (different columns), tags with commas and quotes, an error block.
    const string Annotated =
        "#group,false,false,true,true,false,false,true,true,true\r\n" +
        "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string,string\r\n" +
        "#default,_result,,,,,,,,\r\n" +
        ",result,table,_start,_stop,_time,_value,_field,_measurement,host\r\n" +
        ",,0,2026-10-20T08:00:00Z,2026-10-20T09:00:00Z,2026-10-20T08:00:01.123456789Z,12.5,usage,cpu,\"server,1\"\r\n" +
        ",,1,2026-10-20T08:00:00Z,2026-10-20T09:00:00Z,2026-10-20T08:00:02Z,13,usage,cpu,\"say \"\"hi\"\"\"\r\n" +
        "\r\n" +
        "#group,false,false,true,true,false,false,true,true\r\n" +
        "#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,boolean,string,string\r\n" +
        "#default,_result,,,,,,,\r\n" +
        ",result,table,_start,_stop,_time,_value,_field,_measurement\r\n" +
        ",,2,2026-10-20T08:00:00Z,2026-10-20T09:00:00Z,2026-10-20T08:00:03Z,true,alarm,temp\r\n" +
        "\r\n";

    static List<FluxRow> Rows(string text) => FluxCsv.Read(new StringReader(text)).ToList();

    [Fact]
    public void ParsesBlocksTypesQuotedFieldsAndTags()
    {
        var rows = Rows(Annotated);
        Assert.Equal(3, rows.Count);
        Assert.Equal("server,1", rows[0].Get("host"));
        Assert.Equal("say \"hi\"", rows[1].Get("host"));
        Assert.Equal("double", rows[0].TypeOf("_value"));
        Assert.Equal("boolean", rows[2].TypeOf("_value"));
        Assert.Equal(-1, rows[2].Index("host"));
        Assert.Equal(["host"], rows[0].Columns.Where(c => !FluxCsv.Reserved.Contains(c)));
    }

    [Fact]
    public void ReportsFluxErrorBlocks()
    {
        const string error = "#datatype,string,string\r\n#group,true,true\r\n#default,,\r\n,error,reference\r\n,\"failed to create physical plan: bucket not found\",897\r\n";
        var exc = Assert.Throws<UserError>(() => Rows(error));
        Assert.Contains("bucket not found", exc.Message);
    }

    [Fact]
    public void EmptyResultHasNoRows() => Assert.Empty(Rows("\r\n"));

    [Theory]
    [InlineData("2026-10-20T08:00:00Z", 1_792_483_200_000.0)]
    [InlineData("2026-10-20T08:00:01.5Z", 1_792_483_201_500.0)]
    [InlineData("2026-10-20T08:00:00.123456789Z", 1_792_483_200_123.456789)]
    [InlineData("2026-10-20T10:00:00+02:00", 1_792_483_200_000.0)]
    public void ParsesRfc3339(string text, double expected) => Assert.Equal(expected, FluxCsv.ParseTimeMs(text), 5);

    [Theory]
    [InlineData("12.5", 12.5)]
    [InlineData("true", 1.0)]
    [InlineData("false", 0.0)]
    [InlineData("-3", -3.0)]
    public void ParsesPlottedValues(string text, double expected) => Assert.Equal(expected, FluxCsv.ParseValue(text));

    [Theory]
    [InlineData("run")]
    [InlineData("")]
    [InlineData("NaN")]
    public void NonNumericValuesAreSkipped(string text) => Assert.Null(FluxCsv.ParseValue(text));

    [Theory]
    [InlineData("plain", "\"plain\"")]
    [InlineData("a\"b", "\"a\\\"b\"")]
    [InlineData("a\\b", "\"a\\\\b\"")]
    [InlineData("x ${y} z", "\"x \\${y} z\"")]
    [InlineData("cost $5", "\"cost $5\"")]
    [InlineData("line\nbreak", "\"line\\nbreak\"")]
    public void QuotesFluxStrings(string value, string expected) => Assert.Equal(expected, Flux.Str(value));

    [Fact]
    public void InjectionAttemptStaysInsideTheLiteral()
    {
        var quoted = Flux.Str("x\") |> drop() //");
        Assert.Equal("\"x\\\") |> drop() //\"", quoted);
    }

    [Theory]
    [InlineData(3600, 4000, 1)]        // 1 h in 4000 points: 1 s windows
    [InlineData(86400, 4000, 30)]      // 24 h: 21.6 s -> 30 s
    [InlineData(7 * 86400, 4000, 300)] // 7 d: 151 s -> 300 s
    [InlineData(30 * 86400, 1000, 3600)]
    [InlineData(10, 4000, 1)]
    public void WindowIsTheSmallestRoundDurationUnderTheLimit(double range, int points, double expected) => Assert.Equal(expected, InfluxService.WindowSeconds(range, points));

    [Theory]
    [InlineData("localhost:8086", "http://localhost:8086")]
    [InlineData("https://influx.example.com/", "https://influx.example.com")]
    [InlineData("  http://10.0.0.5:8086  ", "http://10.0.0.5:8086")]
    public void NormalizesTheServerUrl(string input, string expected) => Assert.Equal(expected, InfluxService.NormalizeUrl(input));

    [Theory]
    [InlineData("")]
    [InlineData("ftp://host")]
    public void RejectsInvalidUrls(string input) => Assert.Throws<UserError>(() => InfluxService.NormalizeUrl(input));

    [Fact]
    public void SeriesKeyIsStableRegardlessOfTagOrder()
    {
        var a = InfluxService.SeriesKey("b", "m", "f", new Dictionary<string, string> { ["z"] = "1", ["a"] = "2" });
        var b = InfluxService.SeriesKey("b", "m", "f", new Dictionary<string, string> { ["a"] = "2", ["z"] = "1" });
        Assert.Equal(a, b);
        Assert.Equal("influx:b/m/f{a=2,z=1}", a);
    }
}
