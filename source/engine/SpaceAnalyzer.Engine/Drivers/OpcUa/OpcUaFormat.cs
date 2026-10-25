using System.Globalization;
using System.Numerics;
using System.Text;
using Opc.Ua;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.S7;

namespace SpaceAnalyzer.Engine.Drivers.OpcUa;

/// <summary>DataValue → acquisition row with the semantics of app.value_row on asyncua values.</summary>
public static class OpcUaFormat
{
    static readonly BigInteger Safe = 9007199254740991;

    /// <summary>asyncua NodeId.to_string(): i=85, ns=2;s=Name, ns=3;g=guid, ns=4;b=base64.</summary>
    public static string NodeIdText(NodeId id)
    {
        var prefix = id.NamespaceIndex == 0 ? "" : "ns=" + id.NamespaceIndex.ToString(CultureInfo.InvariantCulture) + ";";
        return prefix + id.IdType switch
        {
            IdType.Numeric => "i=" + ((uint)id.Identifier).ToString(CultureInfo.InvariantCulture),
            IdType.String => "s=" + (string)id.Identifier,
            IdType.Guid => "g=" + ((Guid)(Uuid)id.Identifier).ToString("D"),
            _ => "b=" + Convert.ToBase64String((byte[])id.Identifier),
        };
    }

    public static NodeId ParseNodeId(string text) => NodeId.Parse(text);

    /// <summary>asyncua StatusCode.name: the symbolic name of the code without info bits.</summary>
    public static string StatusName(StatusCode code)
    {
        var masked = code.Code & 0xFFFF0000;
        return masked == 0 ? "Good" : StatusCodes.LookupSymbolicId(masked) ?? "Unknown StatusCode value: " + code.Code.ToString(CultureInfo.InvariantCulture);
    }

    /// <summary>asyncua datetime: aware UTC, truncated to the microsecond (FILETIME // 10).</summary>
    public static DateTime Micro(DateTime value) =>
        DateTime.SpecifyKind(new DateTime(value.Ticks - value.Ticks % 10), DateTimeKind.Utc);

    /// <summary>The Python value asyncua returns for a Variant body; null for types typed() rejects.</summary>
    public static object? Python(object? value, out bool typed)
    {
        typed = true;
        switch (value)
        {
            case null: return null;
            case bool b: return b;
            case sbyte or byte or short or ushort or int or uint or long: return Convert.ToInt64(value, CultureInfo.InvariantCulture);
            case ulong u: return u <= long.MaxValue ? (long)u : new BigInteger(u);
            case float f: return (double)f;
            case double d: return d;
            case string s: return s;
            case DateTime dt: return dt == DateTime.MinValue ? null : Micro(dt);
            case Uuid g: return (Guid)g;
            case Guid g2: return g2;
            case byte[] bytes: return bytes;
            case Array array when value is not byte[]:
            {
                var list = new List<object?>();
                foreach (var item in array)
                {
                    list.Add(Python(item, out var t));
                    typed &= t;
                }
                return list;
            }
            default:
                typed = false;
                return value;
        }
    }

    /// <summary>app.json_value: JSON-safe display value (str() for everything that is not plain JSON).</summary>
    public static object? Json(object? value) => value switch
    {
        null => null,
        double d when !double.IsFinite(d) => PyJson.Str(d),
        bool or string or long or BigInteger or double => value,
        List<object?> list => list.Select(Json).ToList(),
        DateTime dt => PythonStr(dt),
        Guid g => g.ToString("D"),
        byte[] bytes => BytesRepr(bytes),
        LocalizedText text => $"LocalizedText(Locale={Repr(text.Locale)}, Text={Repr(text.Text)})",
        QualifiedName name => $"QualifiedName(NamespaceIndex={name.NamespaceIndex}, Name={Repr(name.Name)})",
        StatusCode code => $"StatusCode(value={code.Code})",
        NodeId id => NodeIdText(id),
        _ => value.ToString(),
    };

    static string Repr(string? s) => s == null ? "None" : PyJson.StrRepr(s);

    /// <summary>str(datetime) of an aware UTC datetime: space separator, microseconds only when non-zero.</summary>
    public static string PythonStr(DateTime dt) => Values.PythonIso(dt).Replace('T', ' ');

    /// <summary>repr(bytes).</summary>
    public static string BytesRepr(byte[] data)
    {
        var quote = Array.IndexOf(data, (byte)'\'') >= 0 && Array.IndexOf(data, (byte)'"') < 0 ? '"' : '\'';
        var sb = new StringBuilder("b").Append(quote);
        foreach (var b in data)
        {
            if (b == quote || b == '\\') sb.Append('\\').Append((char)b);
            else if (b == '\t') sb.Append("\\t");
            else if (b == '\n') sb.Append("\\n");
            else if (b == '\r') sb.Append("\\r");
            else if (b < 32 || b >= 127) sb.Append("\\x").Append(b.ToString("x2", CultureInfo.InvariantCulture));
            else sb.Append((char)b);
        }
        return sb.Append(quote).ToString();
    }

    static string VariantType(Variant variant)
    {
        if (variant.Value == null) return "Null";
        var builtIn = variant.TypeInfo?.BuiltInType ?? BuiltInType.Null;
        return builtIn == BuiltInType.Enumeration ? "Int32" : builtIn.ToString();
    }

    /// <summary>app.value_row for one DataValue (Read or MonitoredItem notification).</summary>
    public static Sample ValueRow(string nodeId, DataValue dv, IServiceMessageContext context)
    {
        var value = Python(dv.WrappedValue.Value, out var typed);
        var good = StatusCode.IsGood(dv.StatusCode);
        var quality = good ? "Good" : StatusCode.IsUncertain(dv.StatusCode) ? "Uncertain" : "Bad";
        var numeric = S7Types.IsFiniteNumber(value);
        var safeNumber = value is not (long or BigInteger) || BigInteger.Abs(value is long l ? l : (BigInteger)value) <= Safe;
        var row = new Sample
        {
            NodeId = nodeId, Value = safeNumber ? Json(value) : PyJson.Str(value),
            SourcePicoseconds = dv.SourcePicoseconds == 0 ? null : dv.SourcePicoseconds,
            ServerPicoseconds = dv.ServerPicoseconds == 0 ? null : dv.ServerPicoseconds,
            Status = StatusName(dv.StatusCode), Good = good, Quality = quality, StatusCode = dv.StatusCode.Code, Numeric = numeric,
            PrecisionWarning = numeric && !safeNumber, PlotValue = good && numeric ? S7Types.ToDouble(value) : null,
            DataType = VariantType(dv.WrappedValue),
            SourceTs = dv.SourceTimestamp == DateTime.MinValue ? null : Values.PythonIso(Micro(dv.SourceTimestamp)),
            ServerTs = dv.ServerTimestamp == DateTime.MinValue ? null : Values.PythonIso(Micro(dv.ServerTimestamp)),
        };
        if (typed) { row.HasSourceValue = true; row.SourceValue = value; }
        else
        {
            using var encoder = new BinaryEncoder(context);
            encoder.WriteVariant(null, dv.WrappedValue);
            row.SourceBinary = encoder.CloseAndReturnBuffer();
        }
        if (!good) row.Error = row.Status;
        return row;
    }

    public static Sample Missing(string nodeId, string message = "In attesa della prima notifica OPC UA") => new()
    {
        NodeId = nodeId, Value = null, Status = "Waiting", Good = false, Numeric = false, PlotValue = null, DataType = "Null", Error = message,
    };
}
