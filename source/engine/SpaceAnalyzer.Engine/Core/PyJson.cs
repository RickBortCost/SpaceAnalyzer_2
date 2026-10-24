using System.Globalization;
using System.Numerics;
using System.Text;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>A JSON object as Python's dict: insertion order kept, a repeated key replaces the value in place.</summary>
public sealed class PyDict : List<KeyValuePair<object?, object?>>
{
    public bool TryGet(string key, out object? value)
    {
        foreach (var (k, v) in this)
            if (k is string s && s == key) { value = v; return true; }
        value = null;
        return false;
    }

    public void Set(string key, object? value)
    {
        for (var i = 0; i < Count; i++)
            if (this[i].Key is string s && s == key) { this[i] = new(key, value); return; }
        Add(new(key, value));
    }
}

/// <summary>Python json.loads / json.dumps and the repr/str texts that appear in user-visible messages.</summary>
public static class PyJson
{
    public sealed class DecodeError(string message) : FormatException(message);

    /// <summary>float('nan') of CPython: positive quiet NaN (.NET double.NaN has the sign bit set).</summary>
    public static readonly double PythonNaN = BitConverter.Int64BitsToDouble(0x7FF8000000000000);

    /// <summary>json.loads(text): ints of any size, NaN/Infinity literals, strict strings.</summary>
    public static object? Loads(string text)
    {
        if (text.Length > 0 && text[0] == '﻿') throw new DecodeError("Unexpected UTF-8 BOM (decode using utf-8-sig)");
        var pos = 0;
        SkipSpace(text, ref pos);
        var value = ParseValue(text, ref pos, 0);
        SkipSpace(text, ref pos);
        if (pos != text.Length) throw new DecodeError("Extra data");
        return value;
    }

    static void SkipSpace(string s, ref int i)
    {
        while (i < s.Length && s[i] is ' ' or '\t' or '\n' or '\r') i++;
    }

    static object? ParseValue(string s, ref int i, int depth)
    {
        // Python raises RecursionError near its recursion limit; the caller treats it as invalid.
        if (depth > 900) throw new InsufficientExecutionStackException("maximum recursion depth exceeded");
        if (i >= s.Length) throw new DecodeError("Expecting value");
        var c = s[i];
        switch (c)
        {
            case '{': return ParseObject(s, ref i, depth);
            case '[': return ParseArray(s, ref i, depth);
            case '"': return ParseString(s, ref i);
            case 'n' when Starts(s, i, "null"): i += 4; return null;
            case 't' when Starts(s, i, "true"): i += 4; return true;
            case 'f' when Starts(s, i, "false"): i += 5; return false;
            case 'N' when Starts(s, i, "NaN"): i += 3; return PythonNaN;
            case 'I' when Starts(s, i, "Infinity"): i += 8; return double.PositiveInfinity;
            case '-' when Starts(s, i, "-Infinity"): i += 9; return double.NegativeInfinity;
        }
        return ParseNumber(s, ref i);
    }

    static bool Starts(string s, int i, string word) => string.CompareOrdinal(s, i, word, 0, word.Length) == 0;

    static object ParseNumber(string s, ref int i)
    {
        // NUMBER_RE = (-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?
        var start = i;
        if (i < s.Length && s[i] == '-') i++;
        if (i < s.Length && s[i] == '0') i++;
        else if (i < s.Length && s[i] is >= '1' and <= '9') { while (i < s.Length && char.IsAsciiDigit(s[i])) i++; }
        else { i = start; throw new DecodeError("Expecting value"); }
        var integer = true;
        if (i + 1 < s.Length && s[i] == '.' && char.IsAsciiDigit(s[i + 1]))
        {
            integer = false; i++;
            while (i < s.Length && char.IsAsciiDigit(s[i])) i++;
        }
        if (i < s.Length && s[i] is 'e' or 'E')
        {
            var j = i + 1;
            if (j < s.Length && s[j] is '+' or '-') j++;
            if (j < s.Length && char.IsAsciiDigit(s[j]))
            {
                integer = false; i = j;
                while (i < s.Length && char.IsAsciiDigit(s[i])) i++;
            }
        }
        var token = s[start..i];
        if (!integer) return double.Parse(token, NumberStyles.Float, CultureInfo.InvariantCulture);
        if (long.TryParse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var l)) return l;
        return BigInteger.Parse(token, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture);
    }

    static string ParseString(string s, ref int i)
    {
        i++;
        var sb = new StringBuilder();
        while (true)
        {
            if (i >= s.Length) throw new DecodeError("Unterminated string starting at");
            var c = s[i++];
            if (c == '"') return sb.ToString();
            if (c == '\\')
            {
                if (i >= s.Length) throw new DecodeError("Unterminated string starting at");
                var e = s[i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length || !int.TryParse(s.AsSpan(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var code) || s.AsSpan(i, 4).Contains('+') || s.AsSpan(i, 4).Contains('-'))
                            throw new DecodeError("Invalid \\uXXXX escape");
                        i += 4;
                        sb.Append((char)code);
                        break;
                    default: throw new DecodeError("Invalid \\escape");
                }
                continue;
            }
            if (c < ' ') throw new DecodeError("Invalid control character at");
            sb.Append(c);
        }
    }

    static PyDict ParseObject(string s, ref int i, int depth)
    {
        i++;
        var result = new PyDict();
        SkipSpace(s, ref i);
        if (i < s.Length && s[i] == '}') { i++; return result; }
        while (true)
        {
            if (i >= s.Length || s[i] != '"') throw new DecodeError("Expecting property name enclosed in double quotes");
            var key = ParseString(s, ref i);
            SkipSpace(s, ref i);
            if (i >= s.Length || s[i] != ':') throw new DecodeError("Expecting ':' delimiter");
            i++;
            SkipSpace(s, ref i);
            result.Set(key, ParseValue(s, ref i, depth + 1));
            SkipSpace(s, ref i);
            if (i < s.Length && s[i] == ',') { i++; SkipSpace(s, ref i); continue; }
            if (i < s.Length && s[i] == '}') { i++; return result; }
            throw new DecodeError("Expecting ',' delimiter");
        }
    }

    static List<object?> ParseArray(string s, ref int i, int depth)
    {
        i++;
        var result = new List<object?>();
        SkipSpace(s, ref i);
        if (i < s.Length && s[i] == ']') { i++; return result; }
        while (true)
        {
            result.Add(ParseValue(s, ref i, depth + 1));
            SkipSpace(s, ref i);
            if (i < s.Length && s[i] == ',') { i++; SkipSpace(s, ref i); continue; }
            if (i < s.Length && s[i] == ']') { i++; return result; }
            throw new DecodeError("Expecting ',' delimiter");
        }
    }

    /// <summary>bytes.decode('utf-8') with CPython's UnicodeDecodeError text.</summary>
    public static string DecodeUtf8(ReadOnlySpan<byte> data)
    {
        var i = 0;
        while (i < data.Length)
        {
            var b = data[i];
            if (b < 0x80) { i++; continue; }
            int need; byte low = 0x80, high = 0xBF;
            if (b is >= 0xC2 and <= 0xDF) need = 1;
            else if (b is >= 0xE0 and <= 0xEF) { need = 2; if (b == 0xE0) low = 0xA0; if (b == 0xED) high = 0x9F; }
            else if (b is >= 0xF0 and <= 0xF4) { need = 3; if (b == 0xF0) low = 0x90; if (b == 0xF4) high = 0x8F; }
            else throw Fail(data, i, i + 1, "invalid start byte");
            var k = 0;
            for (; k < need; k++)
            {
                if (i + 1 + k >= data.Length) throw Fail(data, i, data.Length, "unexpected end of data");
                var c = data[i + 1 + k];
                var lo = k == 0 ? low : (byte)0x80; var hi = k == 0 ? high : (byte)0xBF;
                if (c < lo || c > hi) throw Fail(data, i, i + 1 + k, "invalid continuation byte");
            }
            i += need + 1;
        }
        return Encoding.UTF8.GetString(data);
    }

    static ArgumentException Fail(ReadOnlySpan<byte> data, int start, int end, string reason) =>
        new(end - start == 1
            ? $"'utf-8' codec can't decode byte 0x{data[start]:x2} in position {start}: {reason}"
            : $"'utf-8' codec can't decode bytes in position {start}-{end - 1}: {reason}");

    /// <summary>json.dumps(value, ensure_ascii=False, separators=(',',':')) for parsed JSON values.</summary>
    public static string DumpsCompact(object? value)
    {
        var sb = new StringBuilder();
        Dump(sb, value);
        return sb.ToString();
    }

    static void Dump(StringBuilder sb, object? value)
    {
        switch (value)
        {
            case null: sb.Append("null"); break;
            case bool b: sb.Append(b ? "true" : "false"); break;
            case string s: DumpString(sb, s); break;
            case long l: sb.Append(l.ToString(CultureInfo.InvariantCulture)); break;
            case BigInteger big: sb.Append(big.ToString(CultureInfo.InvariantCulture)); break;
            case double d:
                sb.Append(double.IsNaN(d) ? "NaN" : double.IsPositiveInfinity(d) ? "Infinity" : double.IsNegativeInfinity(d) ? "-Infinity" : Values.PythonRepr(d));
                break;
            case PyDict dict:
                sb.Append('{');
                var first = true;
                foreach (var (k, v) in dict)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    DumpString(sb, (string)k!); sb.Append(':'); Dump(sb, v);
                }
                sb.Append('}');
                break;
            case List<object?> list:
                sb.Append('[');
                for (var j = 0; j < list.Count; j++) { if (j > 0) sb.Append(','); Dump(sb, list[j]); }
                sb.Append(']');
                break;
            default: throw new NotSupportedException("Tipo JSON non supportato");
        }
    }

    static void DumpString(StringBuilder sb, string s)
    {
        sb.Append('"');
        foreach (var c in s)
        {
            switch (c)
            {
                case '"': sb.Append("\\\""); break;
                case '\\': sb.Append("\\\\"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                case '\b': sb.Append("\\b"); break;
                case '\f': sb.Append("\\f"); break;
                default:
                    if (c < ' ') sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else sb.Append(c);
                    break;
            }
        }
        sb.Append('"');
    }

    /// <summary>repr() of a str, as in str(KeyError(key)).</summary>
    public static string StrRepr(string s)
    {
        var quote = s.Contains('\'') && !s.Contains('"') ? '"' : '\'';
        var sb = new StringBuilder().Append(quote);
        foreach (var rune in s.EnumerateRunes())
        {
            var v = rune.Value;
            if (v == quote || v == '\\') sb.Append('\\').Append((char)v);
            else if (v == '\n') sb.Append("\\n");
            else if (v == '\r') sb.Append("\\r");
            else if (v == '\t') sb.Append("\\t");
            else if (v < 0x20 || v == 0x7f) sb.Append("\\x").Append(v.ToString("x2", CultureInfo.InvariantCulture));
            else if (!IsPrintable(rune)) sb.Append(v <= 0xff ? "\\x" + v.ToString("x2", CultureInfo.InvariantCulture) : v <= 0xffff ? "\\u" + v.ToString("x4", CultureInfo.InvariantCulture) : "\\U" + v.ToString("x8", CultureInfo.InvariantCulture));
            else sb.Append(rune.ToString());
        }
        return sb.Append(quote).ToString();
    }

    static bool IsPrintable(Rune r)
    {
        if (r.Value == ' ') return true;
        var cat = Rune.GetUnicodeCategory(r);
        return cat is not (UnicodeCategory.Control or UnicodeCategory.Format or UnicodeCategory.Surrogate or UnicodeCategory.PrivateUse
            or UnicodeCategory.OtherNotAssigned or UnicodeCategory.LineSeparator or UnicodeCategory.ParagraphSeparator or UnicodeCategory.SpaceSeparator);
    }

    /// <summary>str() of a scalar JSON value (Python formatting).</summary>
    public static string Str(object? value) => value switch
    {
        null => "None",
        bool b => b ? "True" : "False",
        string s => s,
        long l => l.ToString(CultureInfo.InvariantCulture),
        BigInteger big => big.ToString(CultureInfo.InvariantCulture),
        double d => double.IsNaN(d) ? "nan" : double.IsPositiveInfinity(d) ? "inf" : double.IsNegativeInfinity(d) ? "-inf" : Values.PythonRepr(d),
        _ => value.ToString() ?? "",
    };

    /// <summary>round() of Python: half to even.</summary>
    public static long Round(double value) => (long)Math.Round(value, MidpointRounding.ToEven);
}
