using System.Collections;
using System.Text.Json;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>Writes plain .NET values (dictionaries, lists, scalars, JsonElement) as JSON.</summary>
public static class JsonOut
{
    public static void Write(Utf8JsonWriter w, object? value)
    {
        switch (value)
        {
            case null: w.WriteNullValue(); break;
            case JsonElement e: e.WriteTo(w); break;
            case Action<Utf8JsonWriter> writer: writer(w); break;
            case string s: w.WriteStringValue(s); break;
            case bool b: w.WriteBooleanValue(b); break;
            case double d: if (double.IsFinite(d)) Values.WriteDouble(w, d); else w.WriteNullValue(); break;
            case float f: Write(w, (double)f); break;
            case int i: w.WriteNumberValue(i); break;
            case long l: w.WriteNumberValue(l); break;
            case uint u: w.WriteNumberValue(u); break;
            case ulong ul: w.WriteNumberValue(ul); break;
            case IDictionary dict:
                w.WriteStartObject();
                foreach (DictionaryEntry entry in dict)
                {
                    w.WritePropertyName(Convert.ToString(entry.Key, System.Globalization.CultureInfo.InvariantCulture) ?? "");
                    Write(w, entry.Value);
                }
                w.WriteEndObject();
                break;
            case IEnumerable list:
                w.WriteStartArray();
                foreach (var item in list) Write(w, item);
                w.WriteEndArray();
                break;
            default: Values.WritePlain(w, value); break;
        }
    }
}
