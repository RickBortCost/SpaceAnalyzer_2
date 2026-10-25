using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace SpaceAnalyzer.App.Infrastructure;

/// <summary>A user error: HTTP 400 with the message shown in the UI (Python ValueError).</summary>
public class UserError(string message) : Exception(message);

/// <summary>An HTTP error with an explicit status code.</summary>
public sealed class HttpError(int status, string message) : Exception(message)
{
    public int Status { get; } = status;
}

/// <summary>Helpers giving JsonNode values the semantics of the Python dictionaries they replace.</summary>
public static class Py
{
    public static readonly JsonSerializerOptions Options = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, WriteIndented = false };
    public static readonly JsonSerializerOptions Indented = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, WriteIndented = true };

    public static JsonObject Obj(params (string key, JsonNode? value)[] items)
    {
        var o = new JsonObject();
        foreach (var (k, v) in items) o[k] = v;
        return o;
    }

    public static JsonNode? Clone(this JsonNode? node) => node?.DeepClone();

    public static string? Str(this JsonNode? node) =>
        node is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    public static bool IsString(this JsonNode? node) => node is JsonValue v && v.GetValueKind() == JsonValueKind.String;
    public static bool IsBool(this JsonNode? node) => node is JsonValue v && v.GetValueKind() is JsonValueKind.True or JsonValueKind.False;
    public static bool IsNumber(this JsonNode? node) => node is JsonValue v && v.GetValueKind() == JsonValueKind.Number;
    public static bool IsNull(this JsonNode? node) => node == null || node is JsonValue v && v.GetValueKind() == JsonValueKind.Null;

    public static bool? Bool(this JsonNode? node) => node is JsonValue v && v.GetValueKind() is JsonValueKind.True or JsonValueKind.False ? v.GetValue<bool>() : null;

    public static double? Num(this JsonNode? node)
    {
        if (node is not JsonValue v || v.GetValueKind() != JsonValueKind.Number) return null;
        return v.TryGetValue<double>(out var d) ? d : double.Parse(v.ToJsonString(), CultureInfo.InvariantCulture);
    }

    /// <summary>Python truthiness of a JSON value.</summary>
    public static bool Truthy(this JsonNode? node) => node switch
    {
        null => false,
        JsonObject o => o.Count > 0,
        JsonArray a => a.Count > 0,
        JsonValue v => v.GetValueKind() switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False or JsonValueKind.Null => false,
            JsonValueKind.String => v.GetValue<string>().Length > 0,
            JsonValueKind.Number => v.Num() != 0,
            _ => true,
        },
        _ => true,
    };

    /// <summary>float(value) of Python on a JSON value: numbers and numeric strings; null otherwise.</summary>
    public static double? Float(this JsonNode? node)
    {
        if (node.IsBool()) return node!.Bool()!.Value ? 1 : 0;
        if (node.IsNumber()) return node.Num();
        if (node.Str() is string s && TryFloat(s, out var d)) return d;
        return null;
    }

    /// <summary>float(str): Python accepts surrounding whitespace, underscores between digits, inf and nan.</summary>
    public static bool TryFloat(string text, out double value)
    {
        var t = text.Trim();
        var lower = t.ToLowerInvariant().TrimStart('+', '-');
        if (lower is "inf" or "infinity" or "nan")
        {
            value = lower == "nan" ? double.NaN : t.StartsWith('-') ? double.NegativeInfinity : double.PositiveInfinity;
            return true;
        }
        if (t.Contains("__") || t.StartsWith('_') || t.EndsWith('_')) { value = 0; return false; }
        t = t.Replace("_", "");
        return double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out value) && t.Length > 0 && !t.Contains(',');
    }

    /// <summary>number() of formula_engine: finite float or null.</summary>
    public static double? Number(this JsonNode? node)
    {
        var d = node.Float();
        return d is double x && double.IsFinite(x) ? x : null;
    }

    public static JsonNode? Get(this JsonNode? node, string key) => node is JsonObject o && o.TryGetPropertyValue(key, out var v) ? v : null;

    public static string Dumps(JsonNode? node, bool indent = false) => node?.ToJsonString(indent ? Indented : Options) ?? "null";

    public static JsonNode? Parse(string text) => JsonNode.Parse(text, documentOptions: new JsonDocumentOptions { AllowTrailingCommas = false });

    /// <summary>Atomic JSON write (temporary file then replace), indented like json.dumps(indent=2).</summary>
    public static void AtomicJson(string path, JsonNode? data)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + ".tmp";
        File.WriteAllText(temporary, Dumps(data, true), new UTF8Encoding(false));
        File.Move(temporary, path, true);
    }

    public static JsonObject? ReadObject(string path)
    {
        try { return File.Exists(path) ? Parse(File.ReadAllText(path, Encoding.UTF8)) as JsonObject : null; }
        catch (Exception exc) when (exc is IOException or JsonException or UnauthorizedAccessException) { return null; }
    }

    public static JsonNode? ToNode(object? value) => value switch
    {
        null => null,
        JsonNode n => n.DeepClone(),
        _ => Parse(Engine.Recording.SacFormat.Json(w => Engine.Core.JsonOut.Write(w, value)) is var bytes ? Encoding.UTF8.GetString(bytes) : "null"),
    };

    public static string Iso(DateTime utc) => Engine.Core.Values.PythonIso(utc);
    public static string NowIso() => Engine.Core.Values.PythonIso(DateTime.UtcNow);

    /// <summary>json.loads value of a node as plain .NET objects (int stays long, float double).</summary>
    public static object? Plain(JsonNode? node)
    {
        switch (node)
        {
            case null: return null;
            case JsonObject o: return o.ToDictionary(k => k.Key, k => Plain(k.Value));
            case JsonArray a: return a.Select(Plain).ToList();
            case JsonValue v:
                switch (v.GetValueKind())
                {
                    case JsonValueKind.String: return v.GetValue<string>();
                    case JsonValueKind.True: return true;
                    case JsonValueKind.False: return false;
                    case JsonValueKind.Null: return null;
                    case JsonValueKind.Number:
                        var text = v.ToJsonString();
                        if (text.IndexOfAny(['.', 'e', 'E']) < 0 && long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l)) return l;
                        return v.GetValue<double>();
                }
                return null;
        }
        return null;
    }

    /// <summary>JSON text of plain .NET values (dictionaries, lists, scalars, JsonNode).</summary>
    public static string DumpsPlain(object? value, bool indent = false)
    {
        using var buffer = new MemoryStream();
        using (var w = new Utf8JsonWriter(buffer, new JsonWriterOptions { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, Indented = indent, SkipValidation = true }))
            WritePlain(w, value);
        return Encoding.UTF8.GetString(buffer.ToArray());
    }

    public static void WritePlain(Utf8JsonWriter w, object? value)
    {
        if (value is JsonNode node) node.WriteTo(w);
        else Engine.Core.JsonOut.Write(w, value);
    }

    /// <summary>Atomic write of plain values as JSON.</summary>
    public static void AtomicPlain(string path, object? value, bool indent = true)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllText(temporary, DumpsPlain(value, indent), new UTF8Encoding(false));
            File.Move(temporary, path, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }

    /// <summary>Numeric value of a node whatever its .NET backing type (int, long, double or JSON text).</summary>
    public static double AsDouble(this JsonNode node) => node.Num() ?? throw new UserError("Valore numerico atteso");

    public static long AsLong(this JsonNode node) => (long)node.AsDouble();
}
