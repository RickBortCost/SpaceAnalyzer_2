using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.S7;
using Xunit;

public class S7PlcTimeTests
{
    // 2026-10-21T12:30:45.123456Z in microseconds since 1970.
    const long Micros = 1792585845123456L;

    static Sample Row(string id, object? value, string quality = "Good", string error = "") =>
        S7Rows.ValueRow(id, "LINT", value, error, quality);

    [Fact]
    public void Time_tag_in_microseconds_or_nanoseconds_becomes_the_source_timestamp()
    {
        foreach (var (unit, raw) in new[] { ("us", Micros), ("ns", Micros * 1000 + 789) })
        {
            var data = Row("s7:DB1.DBD0:REAL", 1.5);
            S7PlcTime.Apply([data], Row("t", raw), null, unit);
            Assert.Equal("2026-10-21T12:30:45.123456+00:00", data.SourceTs);
            Assert.Equal(S7PlcTime.Origin, data.TimestampOrigin);
            Assert.Equal("Good", data.Quality);
        }
    }

    [Fact]
    public void Group_spanning_two_plc_cycles_is_uncertain()
    {
        var data = Row("a", 1.0);
        S7PlcTime.Apply([data], Row("t", Micros), Row("t", Micros + 1000), "us");
        Assert.Equal("Uncertain", data.Quality);
        Assert.False(data.Good);
        Assert.Contains("stesso ciclo PLC", data.Status);
        Assert.Equal("2026-10-21T12:30:45.123456+00:00", data.SourceTs);   // the time read before the values is kept
    }

    [Fact]
    public void Same_time_before_and_after_is_coherent()
    {
        var data = Row("a", 1.0);
        S7PlcTime.Apply([data], Row("t", Micros), Row("t", Micros), "us");
        Assert.Equal("Good", data.Quality);
    }

    [Theory]
    [InlineData(0L)]                      // 1970: the PLC program has not written the tag
    [InlineData(1792585845123456L * 1000)] // nanoseconds read as microseconds: year > 2199
    public void Unusable_time_leaves_the_local_clock_and_flags_the_rows(long raw)
    {
        var data = Row("a", 1.0);
        S7PlcTime.Apply([data], Row("t", raw), null, "us");
        Assert.Null(data.SourceTs);
        Assert.Equal("Uncertain", data.Quality);
        Assert.Contains("Timestamp PLC non utilizzabile", data.Status);
    }

    [Fact]
    public void Time_tag_read_error_and_wrong_type_are_reported_without_hiding_the_values()
    {
        var failed = Row("t", null, "Bad", "Errore lettura S7");
        var data = Row("a", 2.0);
        S7PlcTime.Apply([data], failed, null, "us");
        Assert.Equal(2.0, data.Value);
        Assert.Equal("Uncertain", data.Quality);
        Assert.Contains("Errore lettura S7", data.Status);

        var real = Row("a", 2.0);
        S7PlcTime.Apply([real], Row("t", 1.5), null, "us");
        Assert.Contains("intero a 64 bit", real.Status);
    }

    [Fact]
    public void A_bad_value_keeps_its_own_quality()
    {
        var bad = Row("a", null, "Bad", "Errore");
        S7PlcTime.Apply([bad], Row("t", Micros + 1), Row("t", Micros), "us");
        Assert.Equal("Bad", bad.Quality);
    }

    [Fact]
    public void Plan_reads_the_time_tag_first_and_last_and_collect_restores_the_requested_order()
    {
        var plan = S7PlcTime.Plan(["x", "t", "y"], "t", true);
        Assert.Equal(["t", "x", "y", "t"], plan);
        var read = plan.Select(id => Row(id, 0L)).ToList();
        Assert.Equal(["x", "t", "y"], S7PlcTime.Collect(["x", "t", "y"], "t", read).Select(r => r.NodeId));
        Assert.Equal(["t", "x"], S7PlcTime.Plan(["x"], "t", false));
    }

    [Fact]
    public void Unit_defaults_to_microseconds()
    {
        Assert.Equal("us", S7PlcTime.Unit(""));
        Assert.Equal("ns", S7PlcTime.Unit("ns"));
    }
}
