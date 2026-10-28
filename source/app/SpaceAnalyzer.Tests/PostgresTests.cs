using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Databases;
using SpaceAnalyzer.App.Infrastructure;
using Xunit;

namespace SpaceAnalyzer.Tests;

public class PostgresTests
{
    static JsonObject Item(string column = "temperature", string type = "float8", string? label = null) => new()
    {
        ["schema"] = "plant", ["table"] = "line \"A\"", ["time_column"] = "ts", ["time_type"] = "timestamptz", ["column"] = column, ["column_type"] = type,
        ["label_column"] = label, ["label"] = label == null ? null : "x'; DROP TABLE t; --",
    };

    [Fact]
    public void IdentifiersAreQuotedAndLabelsAreParameters()
    {
        Assert.Equal("\"a\"\"b\"", PostgresService.Ident("a\"b"));
        var s = PostgresService.Describe(Item(label: "sensor"));
        Assert.Equal("\"plant\".\"line \"\"A\"\"\"", s.From);
        var where = s.Where("@s", "@e");
        Assert.Contains("\"sensor\"::text = @label", where);
        Assert.DoesNotContain("DROP", where);
        Assert.Equal("pg:plant.line \"A\"[ts]/temperature{sensor=x'; DROP TABLE t; --}", s.Key);
    }

    [Theory]
    [InlineData("timestamptz", "to_timestamp(@s::float8 / 1000)")]
    [InlineData("timestamp", "(to_timestamp(@s::float8 / 1000))::timestamp")]
    [InlineData("date", "(to_timestamp(@s::float8 / 1000))::date")]
    public void TimeBoundsKeepTheColumnType(string type, string expected) => Assert.Equal(expected, PostgresService.Bound(type, "@s"));

    [Fact]
    public void DescribeNamesSeriesAndRejectsUnsupportedTypes()
    {
        var s = PostgresService.Describe(Item());
        Assert.Equal("line \"A\".temperature", s.Name);
        Assert.Equal("Double", s.DataType);
        Assert.Equal("Boolean", PostgresService.Describe(Item("running", "bool")).DataType);
        Assert.Equal("(CASE WHEN \"running\" THEN 1.0 ELSE 0.0 END)", PostgresService.ValueExpression("\"running\"", "bool"));
        Assert.Throws<UserError>(() => PostgresService.Describe(Item("note", "text")));
        var badTime = Item(); badTime["time_type"] = "interval";
        Assert.Throws<UserError>(() => PostgresService.Describe(badTime));
        var missing = Item(); missing.Remove("column");
        Assert.Throws<UserError>(() => PostgresService.Describe(missing));
    }

    [Fact]
    public void AggregatesAndExportPlan()
    {
        Assert.Equal("avg(v)", PostgresService.AggregateExpression("mean", "v", "t"));
        Assert.Equal("(array_agg(v ORDER BY t DESC))[1]", PostgresService.AggregateExpression("last", "v", "t"));
        Assert.Throws<UserError>(() => PostgresService.AggregateExpression("drop", "v", "t"));
        using var dir = new TempDir();
        var service = new PostgresService(dir.Path);
        var plan = service.Plan(new JsonArray { Item(), Item("running", "bool") });
        Assert.Equal(2, plan.Count);
        Assert.Equal("Boolean", plan[1].DataType);
        var many = new JsonArray();
        for (var i = 0; i <= PostgresService.MaxExportSeries; i++) many.Add(Item("c" + i));
        Assert.Throws<UserError>(() => service.Plan(many));
        Assert.Throws<UserError>(() => service.Plan(new JsonArray()));
        Assert.Equal("UTC", PostgresService.ZoneName("utc"));
    }

    [Fact]
    public void PasswordIsNeverReturnedAndCanBeForgotten()
    {
        using var dir = new TempDir();
        File.WriteAllText(Path.Combine(dir.Path, "postgres.json"), "{\"host\":\"h\",\"port\":5433,\"database\":\"d\",\"user\":\"u\",\"password\":\"segreta\",\"ssl\":\"require\",\"timezone\":\"local\"}");
        var service = new PostgresService(dir.Path);
        var settings = service.PublicSettings();
        Assert.Equal(true, settings["password_saved"]);
        Assert.DoesNotContain("segreta", System.Text.Json.JsonSerializer.Serialize(settings));
        Assert.Equal("postgresql://u@h:5433/d", service.ServerUrl);
        service.Forget();
        Assert.Equal(false, service.PublicSettings()["password_saved"]);
        Assert.Equal("h", service.PublicSettings()["host"]);
    }
}
