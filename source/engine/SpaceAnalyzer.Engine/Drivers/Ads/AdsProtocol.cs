using System.Buffers.Binary;
using System.Globalization;
using System.Numerics;
using System.Text;
using System.Text.RegularExpressions;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.S7;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>An ADS return code with the text of the 2.x client.</summary>
public sealed class AdsException(uint code, string context = "ADS")
    : Exception($"{context}: {AdsProtocol.Errors.GetValueOrDefault(code, "Errore ADS")} (0x{code:X8})")
{
    public uint Code { get; } = code;
}

/// <summary>One symbol table entry (or a member/element derived from a datatype).</summary>
public sealed class AdsSymbol
{
    public string Name { get; set; } = "";
    public string Type { get; set; } = "";
    public string Comment { get; set; } = "";
    public uint? Group { get; set; }
    public uint? Offset { get; set; }
    public uint Size { get; set; }
    public uint Datatype { get; set; }
    public uint Flags { get; set; }
    public bool Derived { get; set; }
    public AdsTypeDecl? Declaration { get; set; }
    public AdsSymbol Clone() => (AdsSymbol)MemberwiseClone();
}

public static class AdsProtocol
{
    public const int MaxPacket = 64 * 1024 * 1024;
    public const int MaxValue = 1024 * 1024;
    public const long FiletimeEpoch = 116444736000000000L;

    public static readonly Dictionary<uint, string> Errors = new()
    {
        [6] = "Porta ADS non trovata: verificare il runtime (es. 851)", [7] = "Target AMS non trovato: verificare Net ID e route",
        [13] = "Porta AMS sorgente non valida", [15] = "Net ID AMS non valido", [18] = "Porta ADS disabilitata: verificare il servizio TwinCAT",
        [1793] = "Servizio non supportato", [1794] = "Index Group non valido", [1795] = "Index Offset non valido",
        [1796] = "Accesso non consentito", [1797] = "Dimensione non valida", [1800] = "Dispositivo occupato",
        [1808] = "Simbolo non trovato", [1809] = "Versione simboli cambiata", [1810] = "Stato ADS non valido",
        [1811] = "Modalità notifica non supportata", [1812] = "Handle notifica non valido", [1813] = "Client notifica sconosciuto",
        [1817] = "Timeout dispositivo", [1826] = "Simbolo non attivo", [1861] = "Timeout ADS",
    };

    public static readonly Dictionary<uint, string> States = new()
    {
        [0] = "INVALID", [1] = "IDLE", [2] = "RESET", [3] = "INIT", [4] = "START", [5] = "RUN", [6] = "STOP", [7] = "SAVECFG", [8] = "LOADCFG",
        [9] = "POWERFAILURE", [10] = "POWERGOOD", [11] = "ERROR", [12] = "SHUTDOWN", [13] = "SUSPEND", [14] = "RESUME", [15] = "CONFIG",
        [16] = "RECONFIG", [17] = "STOPPING",
    };

    /// <summary>Size and signedness come from the ADS data type, not from names chosen by a PLC program.</summary>
    public static readonly Dictionary<uint, (char fmt, int size, string label)> Scalars = new()
    {
        [2] = ('h', 2, "Int16"), [3] = ('i', 4, "Int32"), [4] = ('f', 4, "Float"), [5] = ('d', 8, "Double"), [16] = ('b', 1, "SByte"),
        [17] = ('B', 1, "Byte"), [18] = ('H', 2, "UInt16"), [19] = ('I', 4, "UInt32"), [20] = ('q', 8, "Int64"), [21] = ('Q', 8, "UInt64"),
        [33] = ('?', 1, "Boolean"),
    };

    public static readonly Dictionary<string, uint> BaseTypes = new()
    {
        ["BOOL"] = 33, ["BYTE"] = 17, ["SINT"] = 16, ["USINT"] = 17, ["INT"] = 2, ["UINT"] = 18, ["WORD"] = 18, ["DINT"] = 3, ["UDINT"] = 19,
        ["DWORD"] = 19, ["REAL"] = 4, ["LREAL"] = 5, ["LINT"] = 20, ["ULINT"] = 21, ["LWORD"] = 21, ["TIME"] = 19, ["TOD"] = 19,
        ["TIME_OF_DAY"] = 19, ["DATE"] = 19, ["DT"] = 19, ["DATE_AND_TIME"] = 19, ["LTIME"] = 21,
    };

    public static byte[] NetId(string value)
    {
        var parts = value.Split('.');
        if (parts.Length != 6 || parts.Any(p => p.Length == 0 || !p.All(char.IsAsciiDigit) || !int.TryParse(p, out var n) || n > 255))
            throw new ArgumentException("AMS Net ID: servono 6 numeri tra 0 e 255, es. 192.168.1.10.1.1");
        return parts.Select(p => byte.Parse(p, CultureInfo.InvariantCulture)).ToArray();
    }

    public static string SymbolName(string nodeId)
    {
        if (!nodeId.StartsWith("ads:", StringComparison.Ordinal)) throw new ArgumentException("Simbolo ADS richiesto: ads:MAIN.Temperatura");
        var name = nodeId[4..];
        if (name.Length == 0 || name.Contains('\0') || name.Length > 2048) throw new ArgumentException("Nome simbolo ADS non valido");
        return name;
    }

    public static (AdsSymbol symbol, int length) ParseSymbol(ReadOnlySpan<byte> data, int offset = 0)
    {
        if (data.Length - offset < 30) throw new InvalidDataException("Tabella simboli ADS troncata");
        var d = data[offset..];
        var length = BinaryPrimitives.ReadUInt32LittleEndian(d);
        var symbol = new AdsSymbol
        {
            Group = BinaryPrimitives.ReadUInt32LittleEndian(d[4..]), Offset = BinaryPrimitives.ReadUInt32LittleEndian(d[8..]),
            Size = BinaryPrimitives.ReadUInt32LittleEndian(d[12..]), Datatype = BinaryPrimitives.ReadUInt32LittleEndian(d[16..]),
            Flags = BinaryPrimitives.ReadUInt32LittleEndian(d[20..]),
        };
        int n = BinaryPrimitives.ReadUInt16LittleEndian(d[24..]), t = BinaryPrimitives.ReadUInt16LittleEndian(d[26..]), c = BinaryPrimitives.ReadUInt16LittleEndian(d[28..]);
        if (length < 33 + n + t + c || offset + length > data.Length) throw new InvalidDataException("Lunghezza simbolo ADS non valida");
        var pos = offset + 30;
        var texts = new string[3];
        var i = 0;
        foreach (var count in new[] { n, t, c })
        {
            if (data[pos + count] != 0) throw new InvalidDataException("Stringa simbolo ADS non terminata");
            texts[i++] = Encoding.UTF8.GetString(data.Slice(pos, count));
            pos += count + 1;
        }
        symbol.Name = texts[0]; symbol.Type = texts[1]; symbol.Comment = texts[2];
        return (symbol, (int)length);
    }

    static readonly Regex ArrayPattern = new(@"^ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)\z", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    static readonly Regex BoundPattern = new(@"^\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\z", RegexOptions.Compiled);

    /// <summary>ads_client.array_shape: null when not an array or bounds are unusable.</summary>
    public static (List<(long lo, long hi)> dims, string baseType)? ArrayShape(string typeName)
    {
        var m = ArrayPattern.Match(typeName.Trim());
        if (!m.Success) return null;
        var dims = new List<(long, long)>();
        foreach (var part in m.Groups[1].Value.Split(','))
        {
            var r = BoundPattern.Match(part);
            if (!r.Success) return null;
            var lo = long.Parse(r.Groups[1].Value, CultureInfo.InvariantCulture);
            var hi = long.Parse(r.Groups[2].Value, CultureInfo.InvariantCulture);
            if (hi < lo) return null;
            dims.Add((lo, hi));
        }
        return (dims, m.Groups[2].Value.Trim());
    }

    /// <summary>ads_client.decode_value: exact scalar, string, scalar array or raw hex.</summary>
    public static (object? value, string kind) DecodeValue(byte[] data, AdsSymbol symbol, string encoding)
    {
        if (Scalars.TryGetValue(symbol.Datatype, out var scalar) && data.Length == scalar.size)
            return (Scalar(scalar.fmt, data), scalar.label);
        var kind = symbol.Type.ToUpperInvariant();
        if (symbol.Datatype == 30)
        {
            var end = Array.IndexOf(data, (byte)0);
            var raw = data.AsSpan(0, end < 0 ? data.Length : end);
            return (encoding == "cp1252" ? Cp1252.Decode(raw) : Encoding.UTF8.GetString(raw), "String");
        }
        if (symbol.Datatype == 31)
        {
            var end = data.Length;
            for (var i = 0; i < data.Length - 1; i += 2) if (data[i] == 0 && data[i + 1] == 0) { end = i; break; }
            return (Encoding.Unicode.GetString(data, 0, end), "String");
        }
        if (ArrayShape(kind) is var (dims, baseType) && BaseTypes.TryGetValue(baseType, out var dt) && Scalars.TryGetValue(dt, out var element))
        {
            var count = dims.Aggregate(1L, (a, d) => a * (d.hi - d.lo + 1));
            if (count <= 65536 && data.Length == count * element.size)
            {
                var list = new List<object?>((int)count);
                for (var i = 0; i < count; i++) list.Add(Scalar(element.fmt, data.AsSpan(i * element.size, element.size)));
                return (list, "Array");
            }
        }
        // Never interpret unknown structures, packed bits or extended precision as floats.
        return (Convert.ToHexString(data).ToLowerInvariant(), "Bytes (" + symbol.Type + ")");
    }

    static object Scalar(char fmt, ReadOnlySpan<byte> raw) => fmt == '?' ? raw[0] != 0 : S7Types.Scalar(fmt, raw, little: true)!;

    public static Dictionary<string, object?> SymbolItem(AdsSymbol s) => new()
    {
        ["nodeid"] = "ads:" + s.Name, ["display_name"] = s.Name, ["browse_name"] = s.Name, ["node_class"] = "Variable",
        ["has_children"] = !AdsTypes.Blocked(s.Type) && (s.Datatype == 65 || ArrayShape(s.Type) != null), ["path"] = s.Name,
        ["data_type"] = s.Type, ["size"] = (long)s.Size, ["comment"] = s.Comment, ["index_group"] = s.Group, ["index_offset"] = s.Offset,
    };

    /// <summary>FILETIME text keeping the seventh fractional digit (ads_notifications.timestamp).</summary>
    public static string FiletimeIso(long ticks)
    {
        var seconds = Math.DivRem(ticks - FiletimeEpoch, 10_000_000, out var fraction);
        if (fraction < 0) { seconds -= 1; fraction += 10_000_000; }
        return DateTime.UnixEpoch.AddSeconds(seconds).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + fraction.ToString("0000000", CultureInfo.InvariantCulture) + "+00:00";
    }

    /// <summary>UTCText.micro: UTC nanoseconds rounded half up to the microsecond.</summary>
    public static string MicroIso(long ns)
    {
        var us = (ns + 500) / 1000;
        var second = Math.DivRem(us, 1_000_000, out var fraction);
        return DateTime.UnixEpoch.AddSeconds(second).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + fraction.ToString("000000", CultureInfo.InvariantCulture) + "+00:00";
    }

    static readonly BigInteger Safe = 9007199254740991;

    /// <summary>ADSClient.row: display-safe value, typed original, raw bytes, quality from the PLC state.</summary>
    public static Sample Row(string nodeId, AdsSymbol? s, byte[]? data, uint error, uint? adsState, string encoding)
    {
        object? value = null;
        string kind;
        if (error != 0 || data == null) kind = s?.Type is { Length: > 0 } t ? t : s == null ? "Null" : s.Type;
        else (value, kind) = DecodeValue(data, s!, encoding);
        var run = adsState == 5;
        var quality = error != 0 ? "Bad" : run ? "Good" : "Uncertain";
        var numeric = S7Types.IsFiniteNumber(value);
        var exact = value is not (long or BigInteger) || BigInteger.Abs(value is long l ? l : (BigInteger)value) <= Safe;
        object? safe = exact ? value : PyJson.Str(value);
        if (value is double d && !double.IsFinite(d)) safe = PyJson.Str(d);
        if (value is List<object?> list)
            safe = list.Select(x => x is long or BigInteger && BigInteger.Abs(x is long xl ? xl : (BigInteger)x) > Safe || x is double xd && !double.IsFinite(xd) ? PyJson.Str(x) : x).ToList();
        var stateName = adsState is uint st ? States.GetValueOrDefault(st, st.ToString(CultureInfo.InvariantCulture)) : "UNKNOWN";
        var row = new Sample
        {
            NodeId = nodeId, Value = safe, HasSourceValue = true, SourceValue = value, SourceBinary = data,
            PlotValue = numeric && quality == "Good" ? S7Types.ToDouble(value) : null, DataType = kind, Good = quality == "Good",
            Quality = quality, Numeric = numeric, PrecisionWarning = !exact,
            Status = error != 0 ? $"ADS_0x{error:X8}" : run ? "ADS_OK" : "ADS_STATE_" + stateName,
            StatusCode = error, SourceTs = null, ServerTs = null,
        };
        if (error != 0) row.Error = new AdsException(error).Message;
        return row;
    }
}
