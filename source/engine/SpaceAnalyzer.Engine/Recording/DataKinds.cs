namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// Classification of the acquired data type, used to pick the narrowest storage that is still exact for the values actually recorded.
/// 64-bit and floating point values are never narrowed: only integer types and booleans are candidates.
/// </summary>
public static class DataKinds
{
    static readonly HashSet<string> Bool = ["BOOL", "BOOLEAN"];
    static readonly HashSet<string> Text = ["STRING", "WSTRING", "CHAR", "WCHAR"];
    static readonly HashSet<string> Small = ["SBYTE", "BYTE", "INT8", "UINT8", "USINT", "SINT", "INT16", "UINT16", "INT", "UINT", "WORD"];
    static readonly HashSet<string> Wide = ["INT32", "UINT32", "DINT", "UDINT", "DWORD", "INT64", "UINT64", "LINT", "ULINT", "LWORD"];
    static readonly HashSet<string> Single = ["REAL", "FLOAT", "SINGLE", "FLOAT32"];

    static string Normalize(string? dataType) => (dataType ?? "").Trim().ToUpperInvariant();

    public static bool IsBoolean(string? dataType) => Bool.Contains(Normalize(dataType));
    public static bool IsText(string? dataType) => Text.Contains(Normalize(dataType));
    /// <summary>Integer of at most 16 bits (signed or unsigned).</summary>
    public static bool IsSmallInteger(string? dataType) => Small.Contains(Normalize(dataType));
    /// <summary>Any integer type, 8 to 64 bits.</summary>
    public static bool IsInteger(string? dataType) { var n = Normalize(dataType); return Small.Contains(n) || Wide.Contains(n); }
    /// <summary>Single precision float: a Float32 channel stores it exactly.</summary>
    public static bool IsSingleFloat(string? dataType) => Single.Contains(Normalize(dataType));
}
