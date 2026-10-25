using System.Buffers;
using System.Text.Encodings.Web;
using System.Text.Json;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Live;
using Xunit;

namespace SpaceAnalyzer.Tests;

public class LivePointJsonTests
{
    static string Fast(LivePoint p)
    {
        var buffer = new ArrayBufferWriter<byte>();
        using (var w = new Utf8JsonWriter(buffer, new JsonWriterOptions { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping })) p.WriteJson(w);
        return System.Text.Encoding.UTF8.GetString(buffer.WrittenSpan);
    }

    public static IEnumerable<object[]> Points()
    {
        yield return [new LivePoint(1_700_000_000_000.5, 12.5, "Good")];
        yield return [new LivePoint(1_700_000_000_000d, null, null)];
        yield return [new LivePoint(1e-9, -3.0e300, "Bad", 2147483648L, "2026-10-25T10:00:00.123456", "s", "testo \"con\" virgolette, à ñ € \n e 日本語 <tag> &", "Double", "Good · 0", 0.5, "loc", "133000000000000000")];
        yield return [new LivePoint(0, 5e-324, "Uncertain", -1, "", "", "", "", "", 1e21, "", "")];
        yield return [new LivePoint(double.MaxValue, double.Epsilon, "Good")];
    }

    [Theory]
    [MemberData(nameof(Points))]
    public void FastWriterMatchesTheJsonTree(LivePoint point)
    {
        Assert.Equal(point.ToJson().ToJsonString(Py.Options), Fast(point));
        var back = LivePoint.FromJson((System.Text.Json.Nodes.JsonArray)Py.Parse(Fast(point))!);
        Assert.Equal(point with { Seq = 0 }, back);
    }
}
