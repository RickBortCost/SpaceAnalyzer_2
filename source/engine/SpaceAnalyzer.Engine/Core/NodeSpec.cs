using System.Text.Json;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>A selected channel as sent by Python ({"nodeid", "display_name", ...}); the raw object is kept verbatim.</summary>
public sealed class NodeSpec
{
    public required string NodeId { get; init; }
    public string? DisplayName { get; init; }
    public JsonElement? Raw { get; init; }

    public string Name => string.IsNullOrEmpty(DisplayName) ? NodeId : DisplayName!;

    public static NodeSpec FromJson(JsonElement e) => new()
    {
        NodeId = e.GetProperty("nodeid").GetString() ?? throw new InvalidDataException("nodeid mancante"),
        DisplayName = e.TryGetProperty("display_name", out var name) && name.ValueKind == JsonValueKind.String ? name.GetString() : null,
        Raw = e.Clone(),
    };

    public void Write(Utf8JsonWriter w)
    {
        if (Raw is JsonElement raw) { raw.WriteTo(w); return; }
        w.WriteStartObject();
        w.WriteString("nodeid", NodeId);
        if (DisplayName != null) w.WriteString("display_name", DisplayName);
        w.WriteEndObject();
    }
}
