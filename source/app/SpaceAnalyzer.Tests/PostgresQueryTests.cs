using SpaceAnalyzer.App.Databases;
using SpaceAnalyzer.App.Infrastructure;
using Xunit;

namespace SpaceAnalyzer.Tests;

public class PostgresQueryTests
{
    [Theory]
    [InlineData("SELECT 1", "SELECT 1")]
    [InlineData("  select * from t;  ", "select * from t")]
    [InlineData("SELECT 1;; -- fine", "SELECT 1")]
    [InlineData("WITH a AS (SELECT 1) SELECT * FROM a", "WITH a AS (SELECT 1) SELECT * FROM a")]
    [InlineData("(SELECT 1) UNION (SELECT 2)", "(SELECT 1) UNION (SELECT 2)")]
    [InlineData("-- commento\nSELECT 'a;b' AS x", "-- commento\nSELECT 'a;b' AS x")]
    [InlineData("SELECT $$a;b$$, $q$;$q$ /* ; */", "SELECT $$a;b$$, $q$;$q$ /* ; */")]
    [InlineData("SELECT E'it\\'s;ok'", "SELECT E'it\\'s;ok'")]
    [InlineData("VALUES (1), (2)", "VALUES (1), (2)")]
    [InlineData("TABLE plant.hot", "TABLE plant.hot")]
    public void AcceptsSingleReadOnlyStatements(string input, string expected) => Assert.Equal(expected, PostgresService.CheckSql(input));

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("DELETE FROM t")]
    [InlineData("UPDATE t SET a = 1")]
    [InlineData("INSERT INTO t VALUES (1)")]
    [InlineData("DROP TABLE t")]
    [InlineData("SELECT 1; SELECT 2")]
    [InlineData("SELECT 1; DELETE FROM t")]
    [InlineData("CREATE TABLE x AS SELECT 1")]
    [InlineData("-- SELECT 1\nDROP TABLE t")]
    [InlineData("EXPLAIN SELECT 1")]
    public void RejectsWritesAndMultipleStatements(string input) => Assert.Throws<UserError>(() => PostgresService.CheckSql(input));

    [Fact]
    public void SkeletonKeepsLengthAndBlanksLiteralsAndComments()
    {
        const string sql = "SELECT 'x;y' /* a;b */ -- c;d\n, \"co;l\"";
        var skeleton = PostgresService.Skeleton(sql);
        Assert.Equal(sql.Length, skeleton.Length);
        Assert.DoesNotContain(';', skeleton);
        Assert.StartsWith("SELECT ", skeleton);
    }

    [Fact]
    public void RejectsQueriesThatAreTooLong() =>
        Assert.Throws<UserError>(() => PostgresService.CheckSql("SELECT '" + new string('a', PostgresService.MaxSqlLength) + "'"));
}
