using System.Collections;
using System.Globalization;
using System.Numerics;
using System.Text;
using ParquetSharp;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>
/// One recording row as the Python readers produce it: CSV rows carry strings,
/// Parquet rows typed values (double, long, bool, DateTime, string), binary
/// formats the decoded values.
/// </summary>
public sealed class Row : Dictionary<string, object?>
{
    public Row() : base(StringComparer.Ordinal) { }
    public Row(int capacity) : base(capacity, StringComparer.Ordinal) { }
    public object? Value(string key) => TryGetValue(key, out var v) ? v : null;
}

public static class RowValues
{
    /// <summary>float(value) of Python, null on TypeError/ValueError/OverflowError.</summary>
    public static double? Float(object? value) => value switch
    {
        null => null,
        double d => d,
        float f => f,
        long l => l,
        int i => i,
        uint u => u,
        ulong ul => ul,
        short s => s,
        BigInteger big => Values.BigToDouble(big),
        bool b => b ? 1 : 0,
        string s => Py.TryFloat(s, out var x) ? x : null,
        _ => null,
    };

    /// <summary>_finite_float of recording_reader.</summary>
    public static double? Finite(object? value) => Float(value) is double d && double.IsFinite(d) ? d : null;

    /// <summary>bool(value) of Python for the scalar values found in rows.</summary>
    public static bool Truthy(object? value) => value switch
    {
        null => false,
        bool b => b,
        string s => s.Length > 0,
        double d => d != 0,
        float f => f != 0,
        long l => l != 0,
        int i => i != 0,
        uint u => u != 0,
        _ => true,
    };

    /// <summary>str(value) of Python (datetime keeps its space separator).</summary>
    public static string Str(object? value) => value switch
    {
        null => "None",
        string s => s,
        bool b => b ? "True" : "False",
        double d => PyJson.Str(d),
        float f => PyJson.Str((double)f),
        long l => l.ToString(CultureInfo.InvariantCulture),
        int i => i.ToString(CultureInfo.InvariantCulture),
        uint u => u.ToString(CultureInfo.InvariantCulture),
        ulong ul => ul.ToString(CultureInfo.InvariantCulture),
        DateTime dt => Values.PythonIso(dt).Replace('T', ' '),
        _ => Convert.ToString(value, CultureInfo.InvariantCulture) ?? "",
    };

    /// <summary>str(value or '') of Python.</summary>
    public static string StrOr(object? value, string fallback = "") => Truthy(value) ? Str(value) : fallback;

    /// <summary>_truth of recording_reader.</summary>
    public static bool Truth(object? value) => value is bool b ? b : Str(value).Trim().ToLowerInvariant() is "1" or "true" or "yes" or "good";

    /// <summary>str(value).lower() in ('true','1').</summary>
    public static bool TrueOne(object? value) => Str(value).ToLowerInvariant() is "true" or "1";

    /// <summary>quality of a row: explicit, else Good/Uncertain/Bad from good/status.</summary>
    public static string Quality(Row row, bool truthGood = true)
    {
        var explicitQuality = row.Value("quality");
        if (Truthy(explicitQuality)) return Str(explicitQuality);
        var good = row.TryGetValue("good", out var g) ? g : true;
        var isGood = truthGood ? Truth(good) : TrueOne(good);
        return isGood ? "Good" : Str(row.Value("status") ?? "").StartsWith("Uncertain", StringComparison.Ordinal) ? "Uncertain" : "Bad";
    }

    /// <summary>Deep copy of the plain result trees kept in caches.</summary>
    public static object? DeepCopy(object? value) => value switch
    {
        Dictionary<string, object?> d => d.ToDictionary(k => k.Key, k => DeepCopy(k.Value)),
        List<object?> l => l.Select(DeepCopy).ToList(),
        object?[] a => a.Select(DeepCopy).ToArray(),
        List<Dictionary<string, object?>> l => l.Select(x => (Dictionary<string, object?>)DeepCopy(x)!).ToList(),
        List<double?[]> l => l.Select(x => (double?[])x.Clone()).ToList(),
        List<object?[]> l => l.Select(x => (object?[])x.Clone()).ToList(),
        _ => value,
    };
}

/// <summary>csv.DictReader with the excel dialect over a UTF-8 (BOM tolerant) file.</summary>
public static class CsvRows
{
    public static IEnumerable<Row> Read(string path, bool requireColumns = true)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16);
        using var reader = new StreamReader(stream, new UTF8Encoding(false, true), detectEncodingFromByteOrderMarks: true);
        string[]? header = null;
        foreach (var fields in Records(reader))
        {
            if (header == null)
            {
                header = fields;
                if (requireColumns)
                {
                    var missing = new[] { "node_id", "timestamp_ms", "value_numeric" }.Where(c => !header.Contains(c)).OrderBy(c => c, StringComparer.Ordinal).ToList();
                    if (missing.Count > 0)
                        throw new UserError("CSV non compatibile: colonne mancanti " + string.Join(", ", missing) + ". Usare il CSV esportato da Space Fieldbus Analyzer.");
                }
                continue;
            }
            if (fields.Length == 0) continue;
            var row = new Row(header.Length);
            for (var i = 0; i < header.Length; i++) row[header[i]] = i < fields.Length ? fields[i] : null;
            yield return row;
        }
        if (header == null && requireColumns)
            throw new UserError("CSV non compatibile: colonne mancanti node_id, timestamp_ms, value_numeric. Usare il CSV esportato da Space Fieldbus Analyzer.");
    }

    /// <summary>Records of the excel dialect (quoted fields may span lines); blank lines yield empty records.</summary>
    public static IEnumerable<string[]> Records(TextReader reader)
    {
        var fields = new List<string>();
        var field = new StringBuilder();
        var quoted = false;
        var started = false;
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
            if (ch == '"' && field.Length == 0 && !started) { quoted = true; started = true; continue; }
            if (ch == ',') { fields.Add(field.ToString()); field.Clear(); started = false; continue; }
            if (ch == '\r' || ch == '\n')
            {
                if (ch == '\r' && reader.Peek() == '\n') reader.Read();
                if (fields.Count == 0 && field.Length == 0 && !started) { yield return []; continue; }
                fields.Add(field.ToString());
                yield return fields.ToArray();
                fields.Clear(); field.Clear(); started = false;
                continue;
            }
            started = true;
            field.Append(ch);
        }
        if (quoted) throw new UserError("CSV non valido: campo tra virgolette non terminato");
        if (fields.Count > 0 || field.Length > 0 || started)
        {
            fields.Add(field.ToString());
            yield return fields.ToArray();
        }
    }

    /// <summary>csv.QUOTE_MINIMAL field.</summary>
    public static void Append(StringBuilder sb, string field)
    {
        if (field.IndexOfAny([',', '"', '\r', '\n']) < 0) { sb.Append(field); return; }
        sb.Append('"').Append(field.Replace("\"", "\"\"")).Append('"');
    }
}

/// <summary>Generic Parquet row reader (all physical/logical types used by the writers).</summary>
public static class ParquetRows
{
    public static readonly string[] Required = ["timestamp_ms", "node_id", "value_numeric"];

    public static List<string> Names(string path)
    {
        using var reader = new ParquetFileReader(path);
        var schema = reader.FileMetaData.Schema;
        return Enumerable.Range(0, schema.NumColumns).Select(i => schema.Column(i).Name).ToList();
    }

    public static IReadOnlyDictionary<string, string> KeyValues(string path)
    {
        using var reader = new ParquetFileReader(path);
        return reader.FileMetaData.KeyValueMetadata.ToDictionary(k => k.Key, k => k.Value);
    }

    public static IEnumerable<Row> Read(string path, IEnumerable<string>? columns = null, ISet<string>? signalIds = null, double? startMs = null, double? endMs = null)
    {
        using var reader = new ParquetFileReader(path);
        var schema = reader.FileMetaData.Schema;
        var names = Enumerable.Range(0, schema.NumColumns).Select(i => schema.Column(i).Name).ToList();
        var missing = Required.Where(c => !names.Contains(c)).OrderBy(c => c, StringComparer.Ordinal).ToList();
        if (missing.Count > 0) throw new UserError("File Parquet non compatibile: colonne mancanti " + string.Join(", ", missing));
        var wanted = (columns ?? names).Where(names.Contains).Select(c => names.IndexOf(c)).ToList();
        var timeIndex = names.IndexOf("timestamp_ms");
        var nodeIndex = names.IndexOf("node_id");
        for (var g = 0; g < reader.FileMetaData.NumRowGroups; g++)
        {
            using var group = reader.RowGroup(g);
            if (startMs != null || endMs != null)
            {
                try
                {
                    if (group.MetaData.GetColumnChunkMetaData(timeIndex).Statistics is Statistics<double> stats && stats.HasMinMax
                        && (startMs != null && stats.Max < startMs || endMs != null && stats.Min > endMs)) continue;
                }
                catch (Exception) { }
            }
            var count = checked((int)group.MetaData.NumRows);
            var data = new Dictionary<int, object?[]>();
            foreach (var index in wanted.Append(timeIndex).Append(nodeIndex).Distinct()) data[index] = Column(group, schema.Column(index), index, count);
            var times = data[timeIndex];
            var nodes = data[nodeIndex];
            for (var i = 0; i < count; i++)
            {
                if (signalIds != null && !(nodes[i] is string n && signalIds.Contains(n))) continue;
                if (startMs != null || endMs != null)
                {
                    var t = RowValues.Float(times[i]);
                    if (t == null) continue;
                    if (startMs != null && !(t >= startMs) || endMs != null && !(t <= endMs)) continue;
                }
                var row = new Row(wanted.Count);
                foreach (var index in wanted) row[names[index]] = data[index][i];
                yield return row;
            }
        }
    }

    static object?[] Column(RowGroupReader group, ColumnDescriptor descriptor, int index, int count)
    {
        var optional = descriptor.MaxDefinitionLevel > 0;
        using var logical = descriptor.LogicalType;
        object?[] Read<T>() where T : struct
        {
            if (optional)
            {
                using var r = group.Column(index).LogicalReader<T?>();
                return r.ReadAll(count).Select(v => v.HasValue ? Box(v.Value) : null).ToArray();
            }
            using var req = group.Column(index).LogicalReader<T>();
            return req.ReadAll(count).Select(v => Box(v)).ToArray();
        }
        switch (descriptor.PhysicalType)
        {
            case PhysicalType.Boolean: return Read<bool>();
            case PhysicalType.Int32:
                if (logical is IntLogicalType { IsSigned: false } u32) return u32.BitWidth == 32 ? Read<uint>() : u32.BitWidth == 16 ? Read<ushort>() : Read<byte>();
                if (logical is IntLogicalType { BitWidth: 16 }) return Read<short>();
                if (logical is IntLogicalType { BitWidth: 8 }) return Read<sbyte>();
                if (logical is DateLogicalType) return Read<Date>();
                return Read<int>();
            case PhysicalType.Int64:
                if (logical is TimestampLogicalType) return Read<DateTime>();
                if (logical is IntLogicalType { IsSigned: false }) return Read<ulong>();
                return Read<long>();
            case PhysicalType.Float: return Read<float>();
            case PhysicalType.Double: return Read<double>();
            case PhysicalType.ByteArray:
                if (logical is StringLogicalType or JsonLogicalType or EnumLogicalType)
                {
                    using var r = group.Column(index).LogicalReader<string?>();
                    return r.ReadAll(count).Cast<object?>().ToArray();
                }
                using (var r = group.Column(index).LogicalReader<byte[]?>()) return r.ReadAll(count).Cast<object?>().ToArray();
            default:
                return new object?[count];
        }
    }

    static object? Box<T>(T value) => value switch
    {
        int i => (long)i,
        uint u => (long)u,
        short s => (long)s,
        ushort us => (long)us,
        sbyte sb => (long)sb,
        byte b => (long)b,
        ulong ul => ul <= long.MaxValue ? (long)ul : (object)(double)ul,
        float f => (double)f,
        DateTime dt => DateTime.SpecifyKind(dt, DateTimeKind.Utc),
        Date d => d.DateTime.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
        _ => value,
    };
}

/// <summary>Bounded LRU of plain result trees (recording_preview.ReplayCache).</summary>
public sealed class ReplayCache(int maxPoints = 250000, int maxItems = 32)
{
    readonly object gate = new();
    readonly LinkedList<(string key, Dictionary<string, object?> value, int weight)> items = new();
    int points;

    public Dictionary<string, object?>? Get(string key)
    {
        lock (gate)
        {
            var node = items.FirstOrDefault(i => i.key == key);
            if (node.key == null) return null;
            items.Remove(node);
            items.AddLast(node);
            return (Dictionary<string, object?>)RowValues.DeepCopy(node.value)!;
        }
    }

    public void Put(string key, Dictionary<string, object?> result)
    {
        var weight = result.TryGetValue("series", out var s) && s is IDictionary series ? series.Values.Cast<object>().Sum(v => v is ICollection c ? c.Count : 0) : 0;
        if (weight == 0) weight = 1;
        if (weight > maxPoints) return;
        lock (gate)
        {
            var previous = items.FirstOrDefault(i => i.key == key);
            if (previous.key != null) { items.Remove(previous); points -= previous.weight; }
            while (items.Count > 0 && (points + weight > maxPoints || items.Count >= maxItems))
            {
                points -= items.First!.Value.weight;
                items.RemoveFirst();
            }
            items.AddLast((key, (Dictionary<string, object?>)RowValues.DeepCopy(result)!, weight));
            points += weight;
        }
    }
}
