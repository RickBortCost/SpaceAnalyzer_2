using System.Collections.Specialized;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>Helpers shared by the FDA and RBF converters (same text rules as fda_das.py / rbf.py).</summary>
public static class Legacy
{
    // Field initializers run before a static constructor body: register the code page provider first.
    public static readonly Encoding Cp1252Replace = Cp1252(new EncoderReplacementFallback("?"), new DecoderReplacementFallback("?"));
    public static readonly Encoding Cp1252Strict = Cp1252(EncoderFallback.ExceptionFallback, DecoderFallback.ExceptionFallback);

    static Encoding Cp1252(EncoderFallback encoder, DecoderFallback decoder)
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        return Encoding.GetEncoding(1252, encoder, decoder);
    }

    /// <summary>fda_das.safe_name: NFKD, ASCII only, at most 100 characters, then _index.</summary>
    public static string SafeName(string name, int index)
    {
        var ascii = new string(name.Normalize(NormalizationForm.FormKD).Where(c => c < 128).ToArray());
        if (ascii.Length > 100) ascii = ascii[..100];
        return (ascii.Length > 0 ? ascii : "Signal") + "_" + (index + 1).ToString(CultureInfo.InvariantCulture);
    }

    public static string NodeName(NodeSpec node) => string.IsNullOrEmpty(node.DisplayName) ? node.NodeId : node.DisplayName!;

    public static string Unit(NodeSpec node) =>
        node.Raw is JsonElement raw && raw.TryGetProperty("unit", out var unit) && unit.ValueKind == JsonValueKind.String ? unit.GetString() ?? "" : "";

    /// <summary>The original's sidecar as an ordered dictionary ({} when missing or invalid).</summary>
    public static OrderedDictionary SourceMetadata(string original)
    {
        var result = new OrderedDictionary();
        var path = Path.ChangeExtension(original, ".metadata.json");
        try
        {
            if (!File.Exists(path)) return result;
            using var document = JsonDocument.Parse(File.ReadAllBytes(path));
            if (document.RootElement.ValueKind != JsonValueKind.Object) return result;
            foreach (var property in document.RootElement.EnumerateObject()) result[property.Name] = property.Value.Clone();
        }
        catch (Exception exc) when (exc is IOException or JsonException) { }
        return result;
    }

    public static double? MetaNumber(OrderedDictionary meta, string key) =>
        meta[key] is JsonElement { ValueKind: JsonValueKind.Number } e ? e.GetDouble() : null;

    public static void WriteJson(string path, object meta)
    {
        using var file = new FileStream(path, FileMode.Create, FileAccess.Write);
        using var w = new Utf8JsonWriter(file, new JsonWriterOptions { Indented = true, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
        JsonOut.Write(w, meta);
    }

    public static Dictionary<string, object?> FileEntry(string path, long rows, long frames, int part) => new()
    {
        ["name"] = Path.GetFileName(path), ["rows"] = rows, ["frames"] = frames, ["bytes"] = new FileInfo(path).Length, ["part"] = part,
    };

    public static int Quality(string? quality, bool? good) => quality switch
    {
        "Good" => 3, "Uncertain" => 2, "Bad" => 1, _ => good == true ? 3 : 1,
    };

    public static bool EnoughSpace(string directory, long? minimumMb = null)
    {
        var minimum = (minimumMb ?? long.Parse(Environment.GetEnvironmentVariable("SPACE_FDA_MIN_FREE_MB") ?? "256", CultureInfo.InvariantCulture)) * 1024 * 1024;
        try { return new DriveInfo(Path.GetFullPath(directory)).AvailableFreeSpace >= minimum; }
        catch (Exception exc) when (exc is IOException or ArgumentException or UnauthorizedAccessException) { return true; }
    }
}
