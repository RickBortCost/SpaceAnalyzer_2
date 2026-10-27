using System.Globalization;
using System.Text;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Influx;

/// <summary>One data row of an annotated CSV result: column names and data types come from the block header it belongs to.</summary>
public sealed class FluxRow(string[] columns, string[] dataTypes, string[] values)
{
    public string[] Columns { get; } = columns;
    public string[] DataTypes { get; } = dataTypes;
    public string[] Values { get; } = values;

    public int Index(string column) => Array.IndexOf(Columns, column);
    public string? Get(string column) { var i = Index(column); return i >= 0 && i < Values.Length ? Values[i] : null; }
    public string? TypeOf(string column) { var i = Index(column); return i >= 0 && i < DataTypes.Length ? DataTypes[i] : null; }
}

/// <summary>Parser of InfluxDB 2.x annotated CSV (Flux query results), streaming and tolerant of several result blocks.</summary>
public static class FluxCsv
{
    /// <summary>The columns that are not tags.</summary>
    public static readonly HashSet<string> Reserved = ["", "result", "table", "_start", "_stop", "_time", "_value", "_field", "_measurement"];

    public static IEnumerable<FluxRow> Read(TextReader reader)
    {
        string[]? header = null;
        string[] types = [];
        foreach (var record in Records(reader))
        {
            if (record.Count == 1 && record[0].Length == 0)
            {
                // Blank line: a new block (new annotations and header) follows.
                header = null; types = [];
                continue;
            }
            if (record[0].StartsWith('#'))
            {
                if (record[0] == "#datatype") types = record.Skip(1).ToArray();
                continue;
            }
            if (header == null)
            {
                header = record.Skip(1).ToArray();
                if (header.Length is 1 or 2 && header[0] == "error")
                {
                    // Error block: header ",error,reference", one data row with the message.
                    var data = Records(reader).FirstOrDefault();
                    throw new UserError("InfluxDB: " + (data != null && data.Count > 1 ? data[1] : "errore nella query Flux"));
                }
                continue;
            }
            yield return new FluxRow(header, types, record.Skip(1).ToArray());
        }
    }

    /// <summary>RFC 4180 records (quoted fields with doubled quotes, CRLF or LF).</summary>
    static IEnumerable<List<string>> Records(TextReader reader)
    {
        var fields = new List<string>();
        var field = new StringBuilder();
        var quoted = false;
        var any = false;
        int c;
        while ((c = reader.Read()) >= 0)
        {
            var ch = (char)c;
            if (quoted)
            {
                if (ch == '"')
                {
                    if (reader.Peek() == '"') { reader.Read(); field.Append('"'); }
                    else quoted = false;
                }
                else field.Append(ch);
                continue;
            }
            switch (ch)
            {
                case '"': quoted = true; any = true; break;
                case ',': fields.Add(field.ToString()); field.Clear(); any = true; break;
                case '\r': break;
                case '\n':
                    fields.Add(field.ToString()); field.Clear();
                    yield return fields;
                    fields = new List<string>();
                    any = false;
                    break;
                default: field.Append(ch); any = true; break;
            }
        }
        if (any || field.Length > 0) { fields.Add(field.ToString()); yield return fields; }
    }

    /// <summary>RFC 3339 (up to nanoseconds, Z or offset) to milliseconds since the Unix epoch.</summary>
    public static double ParseTimeMs(string text)
    {
        var s = text.Trim();
        var dot = s.IndexOf('.');
        long extraNs = 0;
        if (dot >= 0)
        {
            var end = dot + 1;
            while (end < s.Length && char.IsDigit(s[end])) end++;
            var digits = s[(dot + 1)..end];
            var ns = digits.Length > 9 ? digits[..9] : digits.PadRight(9, '0');
            extraNs = long.Parse(ns, CultureInfo.InvariantCulture);
            s = s[..dot] + s[end..];
        }
        var whole = DateTimeOffset.Parse(s, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);
        return whole.ToUnixTimeSeconds() * 1000.0 + extraNs / 1_000_000.0;
    }

    /// <summary>A numeric value as plotted: numbers, and booleans as 1/0. Null for anything else.</summary>
    public static double? ParseValue(string? text)
    {
        if (string.IsNullOrEmpty(text)) return null;
        if (text == "true") return 1;
        if (text == "false") return 0;
        return double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) && double.IsFinite(v) ? v : null;
    }
}

/// <summary>Quoting for values placed in Flux string literals (no Flux injection from measurement, field or tag names).</summary>
public static class Flux
{
    public static string Str(string value)
    {
        var sb = new StringBuilder("\"");
        for (var i = 0; i < value.Length; i++)
        {
            var ch = value[i];
            switch (ch)
            {
                case '\\': sb.Append("\\\\"); break;
                case '"': sb.Append("\\\""); break;
                // "${" opens string interpolation in Flux: it must stay literal.
                case '$' when i + 1 < value.Length && value[i + 1] == '{': sb.Append("\\$"); break;
                case '\n': sb.Append("\\n"); break;
                case '\r': sb.Append("\\r"); break;
                case '\t': sb.Append("\\t"); break;
                default: sb.Append(ch); break;
            }
        }
        return sb.Append('"').ToString();
    }

    public static string Time(double ms) => DateTime.UnixEpoch.AddMilliseconds(Math.Floor(ms)).ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture);
}
