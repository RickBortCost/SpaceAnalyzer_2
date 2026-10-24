using S7CommPlusDriver;
using S7CommPlusDriver.ClientApi;
using SpaceAnalyzer.Engine.Drivers.S7;
using Xunit;

public class S7SymbolicTests
{
    static readonly ItemAddress Address = new(1, 1);

    [Fact]
    public void Scalars_follow_python_decode_symbol()
    {
        Assert.Equal(42.5, S7SymbolicDriver.Convert("REAL", new PlcTagReal("a", Address, Softdatatype.S7COMMP_SOFTDATATYPE_REAL) { Value = 42.5f }));
        Assert.Equal(true, S7SymbolicDriver.Convert("BOOL", new PlcTagBool("b", Address, Softdatatype.S7COMMP_SOFTDATATYPE_BOOL) { Value = true }));
        Assert.Equal(-12L, S7SymbolicDriver.Convert("INT", new PlcTagInt("c", Address, Softdatatype.S7COMMP_SOFTDATATYPE_INT) { Value = -12 }));
        Assert.Equal(new System.Numerics.BigInteger(ulong.MaxValue), S7SymbolicDriver.Convert("ULINT", new PlcTagULInt("d", Address, Softdatatype.S7COMMP_SOFTDATATYPE_ULINT) { Value = ulong.MaxValue }));
        Assert.Equal("àX", S7SymbolicDriver.Convert("WSTRING", new PlcTagWString("e", Address, Softdatatype.S7COMMP_SOFTDATATYPE_WSTRING) { Value = "àX" }));
        Assert.Throws<ArgumentException>(() => S7SymbolicDriver.Convert("REAL", new PlcTagReal("f", Address, Softdatatype.S7COMMP_SOFTDATATYPE_REAL) { Value = float.NaN }));
    }

    [Fact]
    public void Dates_keep_the_raw_encoding_of_the_2x_reader()
    {
        // DATE: days since 1990-01-01 (alias UINT); DTL: 12 raw bytes as upper-case hex (123456700 ns, Wednesday = 4).
        Assert.Equal(12784L, S7SymbolicDriver.Convert("DATE", new PlcTagDate("g", Address, Softdatatype.S7COMMP_SOFTDATATYPE_DATE) { Value = new DateTime(2025, 1, 1) }));
        var dtl = new DateTime(2026, 10, 21, 12, 30, 45, DateTimeKind.Utc).AddTicks(1234567);
        Assert.Equal("07EA0A1504" + "0C1E2D" + "075BCCBC", S7SymbolicDriver.Convert("DTL", new PlcTagDTL("h", Address, Softdatatype.S7COMMP_SOFTDATATYPE_DTL) { Value = dtl }));
        Assert.Equal("REAL", S7SymbolicDriver.TypeName(Softdatatype.S7COMMP_SOFTDATATYPE_REAL));
    }

    [Fact]
    public void Symbol_tree_matches_python_browse_and_scan()
    {
        var items = new[] { "DB_A.x", "DB_A.s.y", "DB_B.z" }.Select(n => new Dictionary<string, object?>
        { ["nodeid"] = "s7:sym:" + n, ["display_name"] = n[(n.LastIndexOf('.') + 1)..], ["path"] = n, ["node_class"] = "Variable", ["has_children"] = false });
        var tree = new SymbolTree(items);
        Assert.Equal(new[] { "s7:sym:/DB_A", "s7:sym:/DB_B" }, tree.Browse("").Select(i => (string)i["nodeid"]!));
        Assert.Equal(new[] { "s7:sym:DB_A.x", "s7:sym:/DB_A.s" }, tree.Browse("DB_A").Select(i => (string)i["nodeid"]!));
        Assert.Equal(2, tree.Scan(S7Types.ParseNode("s7:sym:/DB_A", true)).Count());
    }
}
