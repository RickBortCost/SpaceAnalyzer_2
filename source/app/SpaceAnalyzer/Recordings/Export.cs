using System.Globalization;
using System.Text;
using System.Text.Json;
using ParquetSharp;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Recording;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>Original-row exports (analysis_tools.export_rows) and the generic Parquet table writer.</summary>
public static class Export
{
    public static readonly string[] Columns = TableRecorder.Columns;
    static readonly string[] DictionaryColumns = ["node_id", "signal_name", "data_type", "status", "quality", "sample_ms", "status_code"];
    public static readonly StorageProfile Parquet = StorageProfile.For("parquet", default);

    public enum Kind { Double, Long, Bool, String }

    public static Dictionary<string, string> Metadata(StorageProfile profile) => new()
    {
        ["storage"] = profile.CompactJson(), ["lossless"] = "true", ["sample_policy"] = "all original acquisition samples",
    };

    /// <summary>Clean value for a typed column, as the Python writers convert row values.</summary>
    public static object? Clean(object? v, Kind kind, bool finiteFloats = false)
    {
        if (v == null || v is string { Length: 0 }) return null;
        switch (kind)
        {
            case Kind.Bool: return finiteFloats ? RowValues.Truth(v) : RowValues.TrueOne(v);
            case Kind.Long:
                switch (v)
                {
                    case long l: return l;
                    case bool b: return b ? 1L : 0L;
                    case double d when double.IsFinite(d): return (long)Math.Truncate(d);
                    case string s when long.TryParse(s.Trim().Replace("_", ""), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var parsed): return parsed;
                    default: throw new UserError($"invalid literal for int() with base 10: {PyRepr(v)}");
                }
            case Kind.Double:
                var f = RowValues.Float(v) ?? throw new UserError($"could not convert string to float: {PyRepr(v)}");
                return finiteFloats && !double.IsFinite(f) ? null : f;
            default:
                return v is DateTime dt ? Engine.Core.Values.PythonIso(dt) : RowValues.Str(v);
        }
    }

    static string PyRepr(object v) => v is string s ? Engine.Core.PyJson.StrRepr(s) : RowValues.Str(v);

    /// <summary>Streams rows to CSV or Parquet; raises when the interval holds no sample.</summary>
    public static long ExportRows(IEnumerable<Row> rows, string path, string fmt)
    {
        long count = 0;
        if (fmt == "csv")
        {
            using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write);
            using var writer = new StreamWriter(stream, new System.Text.UTF8Encoding(true));
            var sb = new StringBuilder(string.Join(",", Columns)).Append("\r\n");
            writer.Write(sb);
            foreach (var row in rows)
            {
                sb.Clear();
                for (var i = 0; i < Columns.Length; i++)
                {
                    if (i > 0) sb.Append(',');
                    var v = row.Value(Columns[i]);
                    CsvRows.Append(sb, v == null ? "" : RowValues.Str(v));
                }
                writer.Write(sb.Append("\r\n"));
                count++;
            }
        }
        else if (fmt == "parquet")
        {
            var kinds = Columns.Select(c => c is "timestamp_ms" or "elapsed_ms" or "value_numeric" ? Kind.Double
                : c is "cycle" or "sample_ms" or "status_code" ? Kind.Long : c == "good" ? Kind.Bool : Kind.String).ToArray();
            using var writer = new TableWriter(path, Columns, kinds, Parquet);
            foreach (var row in rows)
            {
                writer.Add(Columns.Select((c, i) => Clean(row.Value(c), kinds[i])).ToArray());
                count++;
            }
            if (count == 0) { writer.Dispose(); File.Delete(path); }
        }
        else throw new UserError("Formato export non valido");
        if (count == 0)
        {
            try { File.Delete(path); } catch (IOException) { }
            throw new UserError("Nessun campione nell’intervallo X1–X2");
        }
        return count;
    }

    /// <summary>Row-group buffered Parquet writer over a simple typed schema.</summary>
    public sealed class TableWriter : IDisposable
    {
        readonly ParquetFileWriter writer;
        readonly string[] names;
        readonly Kind[] kinds;
        readonly int groupRows;
        readonly List<object?[]> batch = new();
        bool closed;

        public TableWriter(string path, string[] names, Kind[] kinds, StorageProfile profile, Dictionary<string, string>? metadata = null)
        {
            this.names = names;
            this.kinds = kinds;
            groupRows = profile.RowGroupRows;
            var columns = names.Select((n, i) => kinds[i] switch
            {
                Kind.Double => (Column)new Column<double?>(n),
                Kind.Long => new Column<long?>(n),
                Kind.Bool => new Column<bool?>(n),
                _ => new Column<string>(n, LogicalType.String()),
            }).ToArray();
            using var builder = new WriterPropertiesBuilder();
            builder.CreatedBy("Space Analyzer (ParquetSharp)").Version(ParquetVersion.PARQUET_2_6);
            if (profile.Codec == "snappy") builder.Compression(Compression.Snappy);
            else
            {
                builder.Compression(Compression.Zstd).CompressionLevel(profile.Level ?? 9).DataPageVersion(ParquetDataPageVersion.V2).EnablePageChecksum().DisableDictionary();
                foreach (var c in names.Where(DictionaryColumns.Contains)) builder.EnableDictionary(c);
                for (var i = 0; i < names.Length; i++) if (kinds[i] == Kind.Double) builder.Encoding(names[i], ParquetSharp.Encoding.ByteStreamSplit);
            }
            using var properties = builder.Build();
            writer = new ParquetFileWriter(path, columns, properties, metadata ?? Metadata(profile));
        }

        public void Add(object?[] row)
        {
            batch.Add(row);
            if (batch.Count >= groupRows) Flush();
        }

        void Flush()
        {
            if (batch.Count == 0) return;
            using var group = writer.AppendRowGroup();
            for (var i = 0; i < names.Length; i++)
            {
                var index = i;
                switch (kinds[i])
                {
                    case Kind.Double: { using var c = group.NextColumn().LogicalWriter<double?>(); c.WriteBatch(batch.Select(r => (double?)r[index]).ToArray()); break; }
                    case Kind.Long: { using var c = group.NextColumn().LogicalWriter<long?>(); c.WriteBatch(batch.Select(r => (long?)r[index]).ToArray()); break; }
                    case Kind.Bool: { using var c = group.NextColumn().LogicalWriter<bool?>(); c.WriteBatch(batch.Select(r => (bool?)r[index]).ToArray()); break; }
                    default: { using var c = group.NextColumn().LogicalWriter<string?>(); c.WriteBatch(batch.Select(r => (string?)r[index]).ToArray()); break; }
                }
            }
            batch.Clear();
        }

        public void Dispose()
        {
            if (closed) return;
            closed = true;
            try { Flush(); writer.Close(); }
            finally { writer.Dispose(); }
        }
    }
}
