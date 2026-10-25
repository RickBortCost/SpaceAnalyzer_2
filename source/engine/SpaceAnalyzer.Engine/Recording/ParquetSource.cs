using ParquetSharp;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>Row-group streaming reader of a long-layout original (the columns the converters need).</summary>
public static class ParquetSource
{
    public sealed record Row(double? TimestampMs, long? Cycle, string? NodeId, double? ValueNumeric, string? ValueText, string? DataType, string? Quality, bool? Good);

    public static IEnumerable<Row> Read(string path)
    {
        using var reader = new ParquetFileReader(path);
        var schema = reader.FileMetaData.Schema;
        int Index(string name) => Enumerable.Range(0, schema.NumColumns).FirstOrDefault(i => schema.Column(i).Name == name, -1);
        var columns = new[] { "timestamp_ms", "cycle", "node_id", "value_numeric", "value_text", "data_type", "quality", "good" }.Select(Index).ToArray();
        for (var g = 0; g < reader.FileMetaData.NumRowGroups; g++)
        {
            using var group = reader.RowGroup(g);
            var count = checked((int)group.MetaData.NumRows);
            T[] Column<T>(int slot)
            {
                if (columns[slot] < 0) return new T[count];
                using var column = group.Column(columns[slot]).LogicalReader<T>();
                return column.ReadAll(count);
            }
            var t = Column<double?>(0);
            var cycle = Column<long?>(1);
            var node = Column<string?>(2);
            var value = Column<double?>(3);
            var text = Column<string?>(4);
            var type = Column<string?>(5);
            var quality = Column<string?>(6);
            var good = Column<bool?>(7);
            for (var i = 0; i < count; i++) yield return new Row(t[i], cycle[i], node[i], value[i], text[i], type[i], quality[i], good[i]);
        }
    }
}
