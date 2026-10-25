using System.Globalization;
using System.Numerics;
using System.Text;
using System.Text.RegularExpressions;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Drivers.Mqtt;

/// <summary>Stable MQTT addresses, JSON Pointer access and value rows (mqtt_source.py).</summary>
public static class MqttFormat
{
    public const int MaxCatalog = 10000, MaxLeaves = 4096;
    public const long MaxQueueBytes = 32L * 1024 * 1024;

    public sealed class PointerError(string message) : ArgumentException(message);
    /// <summary>Python KeyError: its text is repr(key).</summary>
    public sealed class KeyError(string key) : Exception(PyJson.StrRepr(key));
    public sealed class IndexError() : Exception("list index out of range");

    public static List<string> PointerTokens(string pointer)
    {
        if (pointer.Length > 2048 || pointer.Contains('\0')) throw new PointerError("Percorso JSON MQTT non valido");
        if (pointer.Length == 0) return new();
        if (!pointer.StartsWith('/') || Regex.IsMatch(pointer, "~(?![01])"))
            throw new PointerError("Usare un JSON Pointer: /value, /machine/pressure, ~1 per / e ~0 per ~");
        return pointer[1..].Split('/').Select(p => p.Replace("~1", "/").Replace("~0", "~")).ToList();
    }

    public static object? PointerValue(object? data, string pointer)
    {
        var value = data;
        foreach (var token in PointerTokens(pointer))
        {
            if (value is List<object?> list && token.Length > 0 && token.All(char.IsAsciiDigit) && (token == "0" || token[0] != '0'))
            {
                if (!int.TryParse(token, NumberStyles.None, CultureInfo.InvariantCulture, out var index) || index >= list.Count) throw new IndexError();
                value = list[index];
            }
            else if (value is PyDict dict)
            {
                if (!dict.TryGet(token, out value)) throw new KeyError(token);
            }
            else throw new KeyError(pointer);
        }
        return value;
    }

    public static (string topic, string pointer) Parse(string nodeId)
    {
        if (!nodeId.StartsWith("mqtt:", StringComparison.Ordinal) || !nodeId[5..].Contains('#'))
            throw new ArgumentException("MQTT: usare mqtt:plant/topic# per payload semplice o mqtt:plant/topic#/value per JSON");
        var rest = nodeId[5..];
        var hash = rest.IndexOf('#');
        return (rest[..hash], rest[(hash + 1)..]);
    }

    public static string Address(string topic, string pointer = "") => "mqtt:" + topic + "#" + pointer;

    public static string TopicFolder(string topic) =>
        "mqtt:topic:" + Convert.ToBase64String(Encoding.UTF8.GetBytes(topic)).Replace('+', '-').Replace('/', '_').TrimEnd('=');

    /// <summary>parse_address(nid, allow_folder=True): (folder, topic or null for the root).</summary>
    public static (bool folder, string? topic, string pointer) ParseFolder(string nodeId)
    {
        if (nodeId == "mqtt:/") return (true, null, "");
        if (nodeId.StartsWith("mqtt:topic:", StringComparison.Ordinal) && !nodeId.Contains('#'))
        {
            try
            {
                var encoded = nodeId["mqtt:topic:".Length..].Replace('-', '+').Replace('_', '/');
                encoded += new string('=', (4 - encoded.Length % 4) % 4);
                var topic = PyJson.DecodeUtf8(Convert.FromBase64String(encoded));
                if (TopicFolder(topic) != nodeId) throw new FormatException();
                return (true, topic, "");
            }
            catch (Exception exc) when (exc is FormatException or ArgumentException) { throw new ArgumentException("Cartella topic MQTT non valida"); }
        }
        var (t, p) = Parse(nodeId);
        PointerTokens(p);
        return (false, t, p);
    }

    public static List<string> TopicFilters(string value) =>
        value.Split('\n').Select(p => p.Trim()).Where(p => p.Length > 0).Distinct().ToList();

    /// <summary>Every scalar leaf of a decoded payload with its JSON Pointer.</summary>
    public static List<(string path, object? value)> Flatten(object? data)
    {
        var output = new List<(string, object?)>();
        var visited = 0;
        void Visit(string path, object? value, int depth)
        {
            visited++;
            if (depth > 32 || visited > 16384) throw new ArgumentException("JSON MQTT troppo profondo o con troppi campi");
            if (value is PyDict dict)
                foreach (var (k, v) in dict) Visit(path + "/" + ((string)k!).Replace("~", "~0").Replace("/", "~1"), v, depth + 1);
            else if (value is List<object?> list)
                for (var i = 0; i < list.Count; i++) Visit(path + "/" + i.ToString(CultureInfo.InvariantCulture), list[i], depth + 1);
            else
            {
                if (output.Count >= MaxLeaves) throw new ArgumentException("JSON MQTT con troppi campi");
                PointerTokens(path);
                output.Add((path, value));
            }
        }
        Visit("", data, 0);
        return output;
    }

    static readonly Regex Iso = new(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$", RegexOptions.Compiled);

    /// <summary>Exact payload timestamp: (milliseconds for plotting, nanosecond ISO text).</summary>
    public static (double ms, string text) Timestamp(object? value, string unit)
    {
        BigInteger ns;
        if (unit == "iso")
        {
            if (value is not string s || !Iso.IsMatch(s) || s.EndsWith('\n')) throw new ArgumentException("Timestamp MQTT ISO 8601 con fuso orario richiesto");
            var fraction = Regex.Match(s, @"\.(\d+)");
            var digits = fraction.Success ? fraction.Groups[1].Value : "";
            var whole = fraction.Success ? s.Remove(fraction.Index, fraction.Length) : s;
            whole = whole.Replace("Z", "+00:00");
            DateTimeOffset parsed;
            try { parsed = DateTimeOffset.ParseExact(whole, "yyyy-MM-dd'T'HH:mm:sszzz", CultureInfo.InvariantCulture); }
            catch (FormatException) { throw new ArgumentException("Timestamp MQTT ISO 8601 con fuso orario richiesto"); }
            var micro = digits.Length > 0 ? long.Parse(digits.Length > 6 ? digits[..6] : digits.PadRight(6, '0'), CultureInfo.InvariantCulture) : 0;
            var extraText = digits.Length > 6 ? digits[6..] : "";
            var extra = extraText.Length > 0 ? long.Parse(extraText.PadRight(3, '0'), CultureInfo.InvariantCulture) : 0;
            ns = new BigInteger(parsed.ToUnixTimeSeconds()) * 1_000_000_000 + micro * 1000 + extra;
        }
        else
        {
            if (value is bool) throw new ArgumentException("Timestamp MQTT booleano non valido");
            var factor = unit switch { "s" => BigInteger.Pow(10, 9), "ms" => BigInteger.Pow(10, 6), "us" => new BigInteger(1000), _ => BigInteger.One };
            var text = value switch { double d => PyJson.Str(d), long or BigInteger or string => PyJson.Str(value), _ => null };
            if (text == null || !DecimalText.TryParse(text, out var mantissa, out var exponent)) throw new ArgumentException("Timestamp MQTT numerico non valido per l’unità scelta");
            // value * factor must be integral (Decimal exactness of the Python implementation).
            var scaled = mantissa * factor;
            if (exponent >= 0) ns = scaled * BigInteger.Pow(10, exponent);
            else
            {
                var divisor = BigInteger.Pow(10, -exponent);
                if (!(scaled % divisor).IsZero) throw new ArgumentException("Timestamp MQTT numerico non valido per l’unità scelta");
                ns = scaled / divisor;
            }
        }
        if (ns < 0 || ns >= new BigInteger(7_258_118_400) * 1_000_000_000) throw new ArgumentException("Timestamp MQTT fuori intervallo 1970–2199");
        var sec = (long)(ns / 1_000_000_000);
        var nano = (long)(ns % 1_000_000_000);
        var stamp = DateTime.UnixEpoch.AddSeconds(sec).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + nano.ToString("000000000", CultureInfo.InvariantCulture) + "+00:00";
        return ((double)(sec * 1000) + nano / 1e6, stamp);
    }

    public static string ValueType(object? value) => value switch
    {
        bool => "Boolean",
        long => "Int64",
        BigInteger b => b >= long.MinValue && b <= long.MaxValue ? "Int64" : b >= 0 && b <= ulong.MaxValue ? "UInt64" : "Integer",
        double => "Double",
        string => "String",
        _ => "Null",
    };

    static readonly BigInteger Safe = BigInteger.Pow(2, 53);

    /// <summary>value_row(): display-safe value, typed original and plot value.</summary>
    public static Sample ValueRow(string nodeId, object? value, string quality, string status, string? sourceTs, double receivedMs)
    {
        var kind = ValueType(value);
        var numeric = value is bool or long or BigInteger || value is double d && double.IsFinite(d);
        if (value is double nf && !double.IsFinite(nf)) { quality = "Bad"; status = "MQTT valore non finito"; }
        if (value == null)
        {
            if (quality != "Bad") status = "MQTT valore null o campo assente";
            quality = "Bad";
        }
        double? plot = null;
        if (numeric && quality == "Good")
        {
            var f = value switch { bool b => b ? 1.0 : 0.0, long l => l, BigInteger big => Values.BigToDouble(big), double x => x, _ => 0 };
            if (!double.IsFinite(f)) { plot = null; quality = "Bad"; status = "MQTT numero oltre il limite grafico"; }
            else plot = f;
        }
        var precision = value switch { long l => BigInteger.Abs(l) > Safe, BigInteger big => BigInteger.Abs(big) > Safe, _ => false };
        var safe = precision || value is double y && !double.IsFinite(y) ? PyJson.Str(value) : value;
        return new Sample
        {
            NodeId = nodeId, Value = safe, HasSourceValue = true, SourceValue = value, PlotValue = plot, Numeric = numeric,
            Good = quality == "Good", Quality = quality, Status = status, DataType = kind, StatusCode = null, SourceTs = sourceTs,
            ServerTs = null, LocalTs = Values.IsoMicro(Recording.TableRecorder.PythonFromTimestamp(receivedMs)), ReceivedMs = receivedMs,
            PrecisionWarning = precision,
        };
    }
}

/// <summary>Decimal(str(x)) parsing: sign, digits, fraction, exponent; NaN/Infinity rejected.</summary>
static class DecimalText
{
    static readonly Regex Pattern = new(@"^\s*([+-]?)(?:(\d[\d_]*)(?:\.([\d_]*))?|\.(\d[\d_]*))(?:[eE]([+-]?\d+))?\s*$", RegexOptions.Compiled);

    public static bool TryParse(string text, out BigInteger mantissa, out int exponent)
    {
        mantissa = 0; exponent = 0;
        var m = Pattern.Match(text);
        if (!m.Success) return false;
        var integer = (m.Groups[2].Success ? m.Groups[2].Value : "").Replace("_", "");
        var fraction = (m.Groups[3].Success ? m.Groups[3].Value : m.Groups[4].Success ? m.Groups[4].Value : "").Replace("_", "");
        var digits = (integer + fraction).TrimStart('0');
        mantissa = digits.Length == 0 ? BigInteger.Zero : BigInteger.Parse(digits, CultureInfo.InvariantCulture);
        if (m.Groups[1].Value == "-") mantissa = -mantissa;
        if (!int.TryParse(m.Groups[5].Success ? m.Groups[5].Value : "0", NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var e)) return false;
        exponent = e - fraction.Length;
        return true;
    }
}
