using System.Buffers.Binary;
using System.Globalization;
using System.Numerics;
using System.Text;
using System.Text.Json;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>
/// Value encodings shared with the Python side.
/// "Typed" is the lossless encoding of primary_format.typed(); "Python JSON"
/// writes numbers so that json.loads gives back the same Python type
/// (a double always carries a decimal point or an exponent).
/// </summary>
public static class Values
{
    const long SafeInteger = 1L << 53;

    public static bool IsNumeric(object? value) => value switch
    {
        double d => double.IsFinite(d),
        float f => float.IsFinite(f),
        long or int or short or sbyte or byte or ushort or uint or ulong or BigInteger => true,
        _ => false,
    };

    public static double? AsDouble(object? value) => value switch
    {
        double d => d,
        float f => f,
        long l => l,
        int i => i,
        short s => s,
        sbyte sb => sb,
        byte b => b,
        ushort us => us,
        uint ui => ui,
        ulong ul => ul,
        BigInteger bi => (double)bi,
        bool => null,
        _ => null,
    };

    /// <summary>Writes a double the way Python's json.dumps(repr) would round-trip it.</summary>
    public static void WriteDouble(Utf8JsonWriter writer, double value)
    {
        writer.WriteRawValue(DoubleText(value), skipInputValidation: true);
    }

    public static string DoubleText(double value)
    {
        if (!double.IsFinite(value)) throw new ArgumentException("Non-finite double needs a typed encoding");
        return PythonRepr(value);
    }

    /// <summary>
    /// Python float.__repr__: shortest round-trip digits, fixed notation when the
    /// decimal exponent is in [-4, 16), otherwise d.ddde+XX.
    /// </summary>
    public static string PythonRepr(double value)
    {
        if (double.IsNaN(value)) return "nan";
        if (double.IsInfinity(value)) return value > 0 ? "inf" : "-inf";
        if (value == 0) return double.IsNegative(value) ? "-0.0" : "0.0";
        var r = value.ToString("R", CultureInfo.InvariantCulture);
        var negative = r[0] == '-';
        if (negative) r = r[1..];
        var exponentAt = r.IndexOfAny(['E', 'e']);
        var mantissa = exponentAt < 0 ? r : r[..exponentAt];
        var shift = exponentAt < 0 ? 0 : int.Parse(r[(exponentAt + 1)..], NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
        var dot = mantissa.IndexOf('.');
        var integerDigits = dot < 0 ? mantissa.Length : dot;
        var digits = mantissa.Replace(".", "");
        var leading = 0;
        while (leading < digits.Length - 1 && digits[leading] == '0') leading++;
        digits = digits[leading..].TrimEnd('0');
        if (digits.Length == 0) digits = "0";
        // value = 0.<digits> x 10^point
        var point = integerDigits - leading + shift;
        var exponent = point - 1;
        var sb = new StringBuilder(negative ? "-" : "");
        if (exponent >= -4 && exponent < 16)
        {
            if (point <= 0) sb.Append("0.").Append('0', -point).Append(digits);
            else if (point >= digits.Length) sb.Append(digits).Append('0', point - digits.Length).Append(".0");
            else sb.Append(digits, 0, point).Append('.').Append(digits, point, digits.Length - point);
        }
        else
        {
            sb.Append(digits[0]);
            if (digits.Length > 1) sb.Append('.').Append(digits, 1, digits.Length - 1);
            sb.Append('e').Append(exponent < 0 ? '-' : '+').Append(Math.Abs(exponent).ToString("00", CultureInfo.InvariantCulture));
        }
        return sb.ToString();
    }

    /// <summary>Python str() of a float, used for display-safe non-finite values.</summary>
    public static string PythonFloatText(double value) => PythonRepr(value);

    /// <summary>primary_format.typed(): lossless JSON for the SAC payload.</summary>
    public static void WriteTyped(Utf8JsonWriter w, object? value)
    {
        switch (value)
        {
            case null: w.WriteNullValue(); break;
            case bool b: w.WriteBooleanValue(b); break;
            case string s: w.WriteStringValue(s); break;
            case double d: WriteTypedDouble(w, d); break;
            case float f: WriteTypedDouble(w, f); break;
            case long l: WriteTypedInteger(w, l); break;
            case int i: w.WriteNumberValue(i); break;
            case short s16: w.WriteNumberValue(s16); break;
            case sbyte s8: w.WriteNumberValue(s8); break;
            case byte u8: w.WriteNumberValue(u8); break;
            case ushort u16: w.WriteNumberValue(u16); break;
            case uint u32: w.WriteNumberValue(u32); break;
            case ulong u64:
                if (u64 > SafeInteger) WriteTag(w, "$int", u64.ToString(CultureInfo.InvariantCulture));
                else w.WriteNumberValue(u64);
                break;
            case BigInteger big:
                if (BigInteger.Abs(big) > SafeInteger) WriteTag(w, "$int", big.ToString(CultureInfo.InvariantCulture));
                else w.WriteNumberValue((long)big);
                break;
            case byte[] bytes: WriteTag(w, "$bytes", Convert.ToBase64String(bytes)); break;
            case DateTime dt:
                w.WriteStartObject();
                w.WriteString("$datetime", PythonIso(dt));
                w.WriteNumber("fold", 0);
                w.WriteEndObject();
                break;
            case DateOnly date: WriteTag(w, "$date", date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)); break;
            case decimal dec: WriteTag(w, "$decimal", dec.ToString(CultureInfo.InvariantCulture)); break;
            case Guid guid: WriteTag(w, "$uuid", guid.ToString("D")); break;
            case TypedTuple tuple:
                w.WriteStartObject();
                w.WriteStartArray("$tuple");
                foreach (var item in tuple.Items) WriteTyped(w, item);
                w.WriteEndArray();
                w.WriteEndObject();
                break;
            case IReadOnlyList<KeyValuePair<object?, object?>> map:
                w.WriteStartObject();
                w.WriteStartArray("$map");
                foreach (var (key, item) in map)
                {
                    w.WriteStartArray();
                    WriteTyped(w, key);
                    WriteTyped(w, item);
                    w.WriteEndArray();
                }
                w.WriteEndArray();
                w.WriteEndObject();
                break;
            case System.Collections.IEnumerable list:
                w.WriteStartArray();
                foreach (var item in list) WriteTyped(w, item);
                w.WriteEndArray();
                break;
            default:
                throw new NotSupportedException("Tipo primario non supportato: " + value.GetType().Name);
        }
    }

    static void WriteTypedDouble(Utf8JsonWriter w, double d)
    {
        if (double.IsFinite(d)) WriteDouble(w, d);
        else
        {
            Span<byte> raw = stackalloc byte[8];
            BinaryPrimitives.WriteDoubleBigEndian(raw, d);
            WriteTag(w, "$float_bits", Convert.ToHexString(raw).ToLowerInvariant());
        }
    }

    static void WriteTypedInteger(Utf8JsonWriter w, long l)
    {
        if (l > SafeInteger || l < -SafeInteger) WriteTag(w, "$int", l.ToString(CultureInfo.InvariantCulture));
        else w.WriteNumberValue(l);
    }

    static void WriteTag(Utf8JsonWriter w, string tag, string text)
    {
        w.WriteStartObject();
        w.WriteString(tag, text);
        w.WriteEndObject();
    }

    /// <summary>datetime.isoformat() of an aware UTC datetime (microseconds only when non-zero).</summary>
    public static string PythonIso(DateTime value)
    {
        var utc = value.Kind == DateTimeKind.Local ? value.ToUniversalTime() : DateTime.SpecifyKind(value, DateTimeKind.Utc);
        var micro = (utc.Ticks % TimeSpan.TicksPerSecond) / 10;
        var text = utc.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture);
        if (micro != 0) text += "." + micro.ToString("000000", CultureInfo.InvariantCulture);
        return text + "+00:00";
    }

    /// <summary>float(int) of Python: correctly rounded (BigInteger's explicit conversion truncates); infinity beyond the range.</summary>
    public static double BigToDouble(BigInteger value)
    {
        if (value >= long.MinValue && value <= long.MaxValue) return (long)value;
        if (value >= 0 && value <= ulong.MaxValue) return (ulong)value;
        return double.Parse(value.ToString(CultureInfo.InvariantCulture), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
    }

    /// <summary>datetime.fromtimestamp(ms/1000, utc).isoformat(): microseconds only when non-zero.</summary>
    public static string IsoFromMs(double ms) => PythonIso(Recording.TableRecorder.PythonFromTimestamp(ms));

    /// <summary>isoformat(timespec='microseconds') of a UTC instant.</summary>
    public static string IsoMicro(DateTime utc) =>
        utc.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + (utc.Ticks % TimeSpan.TicksPerSecond / 10).ToString("000000", CultureInfo.InvariantCulture) + "+00:00";

    /// <summary>Reads a JSON value produced by Python (rows sent over IPC) into an engine value.</summary>
    public static object? FromJson(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Null or JsonValueKind.Undefined => null,
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.String => e.GetString(),
        JsonValueKind.Number => NumberFromJson(e),
        JsonValueKind.Array => e.EnumerateArray().Select(FromJson).ToList(),
        JsonValueKind.Object => FromTypedObject(e),
        _ => null,
    };

    static object NumberFromJson(JsonElement e)
    {
        var raw = e.GetRawText();
        if (raw.IndexOfAny(['.', 'e', 'E']) >= 0) return e.GetDouble();
        if (e.TryGetInt64(out var l)) return l;
        return BigInteger.Parse(raw, CultureInfo.InvariantCulture);
    }

    /// <summary>Inverse of typed() for tagged objects; plain objects stay as ordered maps.</summary>
    static object? FromTypedObject(JsonElement e)
    {
        if (e.TryGetProperty("$int", out var i)) return BigInteger.Parse(i.GetString()!, CultureInfo.InvariantCulture) is var big && big >= long.MinValue && big <= long.MaxValue ? (object)(long)big : big;
        if (e.TryGetProperty("$float_bits", out var bits)) return BinaryPrimitives.ReadDoubleBigEndian(Convert.FromHexString(bits.GetString()!));
        if (e.TryGetProperty("$bytes", out var b64)) return Convert.FromBase64String(b64.GetString()!);
        if (e.TryGetProperty("$datetime", out var dt)) return DateTime.Parse(dt.GetString()!, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal);
        if (e.TryGetProperty("$decimal", out var dec)) return decimal.Parse(dec.GetString()!, NumberStyles.Float, CultureInfo.InvariantCulture);
        if (e.TryGetProperty("$uuid", out var uuid)) return Guid.Parse(uuid.GetString()!);
        if (e.TryGetProperty("$tuple", out var tuple)) return new TypedTuple(tuple.EnumerateArray().Select(FromJson).ToArray());
        if (e.TryGetProperty("$map", out var map))
            return map.EnumerateArray().Select(p => new KeyValuePair<object?, object?>(FromJson(p[0]), FromJson(p[1]))).ToList();
        return e.EnumerateObject().Select(p => new KeyValuePair<object?, object?>(p.Name, FromJson(p.Value))).ToList();
    }

    /// <summary>Plain JSON (non-typed) for display rows: lists stay lists, maps become objects.</summary>
    public static void WritePlain(Utf8JsonWriter w, object? value)
    {
        switch (value)
        {
            case null: w.WriteNullValue(); break;
            case bool b: w.WriteBooleanValue(b); break;
            case string s: w.WriteStringValue(s); break;
            case double d:
                if (double.IsFinite(d)) WriteDouble(w, d); else w.WriteStringValue(PythonFloatText(d));
                break;
            case float f: WritePlain(w, (double)f); break;
            case long l: w.WriteNumberValue(l); break;
            case int i: w.WriteNumberValue(i); break;
            case short s16: w.WriteNumberValue(s16); break;
            case sbyte s8: w.WriteNumberValue(s8); break;
            case byte u8: w.WriteNumberValue(u8); break;
            case ushort u16: w.WriteNumberValue(u16); break;
            case uint u32: w.WriteNumberValue(u32); break;
            case ulong u64: w.WriteNumberValue(u64); break;
            case BigInteger big: w.WriteRawValue(big.ToString(CultureInfo.InvariantCulture), true); break;
            case byte[] bytes: w.WriteStringValue(Convert.ToHexString(bytes).ToLowerInvariant()); break;
            case DateTime dt: w.WriteStringValue(PythonIso(dt)); break;
            case Guid guid: w.WriteStringValue(guid.ToString("D")); break;
            case decimal dec: w.WriteStringValue(dec.ToString(CultureInfo.InvariantCulture)); break;
            case TypedTuple tuple:
                w.WriteStartArray();
                foreach (var item in tuple.Items) WritePlain(w, item);
                w.WriteEndArray();
                break;
            case IReadOnlyList<KeyValuePair<object?, object?>> map:
                w.WriteStartObject();
                foreach (var (key, item) in map)
                {
                    w.WritePropertyName(Convert.ToString(key, CultureInfo.InvariantCulture) ?? "");
                    WritePlain(w, item);
                }
                w.WriteEndObject();
                break;
            case System.Collections.IEnumerable list:
                w.WriteStartArray();
                foreach (var item in list) WritePlain(w, item);
                w.WriteEndArray();
                break;
            default: w.WriteStringValue(Convert.ToString(value, CultureInfo.InvariantCulture)); break;
        }
    }

    /// <summary>Python json.dumps(value, ensure_ascii=False, default=str) for the value_text column.</summary>
    public static string? ValueText(object? value)
    {
        switch (value)
        {
            case null: return null;
            case string s: return s;
            case bool b: return b ? "true" : "false";
            case double d: return PythonFloatText(d);
            case float f: return PythonFloatText(f);
            case byte[] bytes: return PythonBytesRepr(bytes);
            case System.Collections.IEnumerable or IReadOnlyList<KeyValuePair<object?, object?>> or TypedTuple:
                using (var stream = new MemoryStream())
                {
                    using (var w = new Utf8JsonWriter(stream, new JsonWriterOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }))
                        WritePythonDumps(w, value);
                    return PythonSeparators(Encoding.UTF8.GetString(stream.ToArray()));
                }
            case DateTime dt: return PythonIso(dt).Replace('T', ' ');
            default: return Convert.ToString(value, CultureInfo.InvariantCulture);
        }
    }

    static void WritePythonDumps(Utf8JsonWriter w, object? value)
    {
        switch (value)
        {
            case bool b: w.WriteBooleanValue(b); break;
            case double d:
                if (double.IsFinite(d)) WriteDouble(w, d);
                else w.WriteRawValue(double.IsNaN(d) ? "NaN" : d > 0 ? "Infinity" : "-Infinity", true);
                break;
            case float f: WritePythonDumps(w, (double)f); break;
            case TypedTuple tuple:
                w.WriteStartArray();
                foreach (var item in tuple.Items) WritePythonDumps(w, item);
                w.WriteEndArray();
                break;
            case IReadOnlyList<KeyValuePair<object?, object?>> map:
                w.WriteStartObject();
                foreach (var (key, item) in map)
                {
                    w.WritePropertyName(Convert.ToString(key, CultureInfo.InvariantCulture) ?? "");
                    WritePythonDumps(w, item);
                }
                w.WriteEndObject();
                break;
            case string or null: WritePlain(w, value); break;
            case byte[] bytes: w.WriteStringValue(PythonBytesRepr(bytes)); break;
            case System.Collections.IEnumerable list:
                w.WriteStartArray();
                foreach (var item in list) WritePythonDumps(w, item);
                w.WriteEndArray();
                break;
            default: WritePlain(w, value); break;
        }
    }

    /// <summary>json.dumps uses ", " and ": " separators.</summary>
    static string PythonSeparators(string compact)
    {
        var sb = new StringBuilder(compact.Length + 16);
        var inString = false;
        for (var i = 0; i < compact.Length; i++)
        {
            var c = compact[i];
            sb.Append(c);
            if (c == '"' && (i == 0 || compact[i - 1] != '\\' || EscapedQuote(compact, i))) inString = !inString;
            else if (!inString && (c == ',' || c == ':')) sb.Append(' ');
        }
        return sb.ToString();
    }

    static bool EscapedQuote(string text, int index)
    {
        var slashes = 0;
        for (var j = index - 1; j >= 0 && text[j] == '\\'; j--) slashes++;
        return slashes % 2 == 0;
    }

    static string PythonBytesRepr(byte[] bytes)
    {
        var sb = new StringBuilder("b'");
        foreach (var b in bytes)
        {
            if (b == (byte)'\\') sb.Append("\\\\");
            else if (b == (byte)'\'') sb.Append("\\'");
            else if (b == (byte)'\t') sb.Append("\\t");
            else if (b == (byte)'\n') sb.Append("\\n");
            else if (b == (byte)'\r') sb.Append("\\r");
            else if (b >= 0x20 && b < 0x7f) sb.Append((char)b);
            else sb.Append("\\x").Append(b.ToString("x2", CultureInfo.InvariantCulture));
        }
        return sb.Append('\'').ToString();
    }
}

/// <summary>A Python tuple (kept distinct from a list in the typed encoding).</summary>
public sealed record TypedTuple(object?[] Items);
