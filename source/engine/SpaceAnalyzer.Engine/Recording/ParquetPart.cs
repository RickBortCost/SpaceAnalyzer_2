using System.Text.Json;
using ParquetSharp;
using ParquetSharp.IO;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>Storage profiles of recording_storage.py.</summary>
public sealed record StorageProfile(string Format, string Profile, string Codec, int? Level, int RowGroupRows, double FlushSeconds, string Label, bool Lossless)
{
    static readonly Dictionary<string, StorageProfile> Parquet = new()
    {
        ["compact"] = new("parquet", "compact", "zstd", 9, 32768, 5, "Parquet ZSTD · Compatto", true),
        ["maximum"] = new("parquet", "maximum", "zstd", 19, 65536, 5, "Parquet ZSTD · Massima compressione", true),
        ["fast"] = new("parquet", "fast", "zstd", 3, 32768, 5, "Parquet ZSTD · Rapido", true),
        ["compatible"] = new("parquet", "compatible", "snappy", null, 4096, 1, "Parquet Snappy · Compatibilità", true),
    };

    public static StorageProfile For(string format, JsonElement options)
    {
        switch (format)
        {
            case "csv": return new("csv", "text", "none", null, 4096, 1, "CSV", true);
            case "parquet":
                var key = options.ValueKind == JsonValueKind.Object && options.TryGetProperty("parquet_profile", out var p) && p.ValueKind == JsonValueKind.String ? p.GetString()! : "compact";
                return Parquet.TryGetValue(key, out var profile) ? profile
                    : throw new ArgumentException("Profilo binario non valido: scegliere Compatto, Massima compressione, Rapido o Compatibilità");
            default:
                throw new ArgumentException("Scegliere CSV, Binario Parquet, FDA DAS4 o RBF v3. La registrazione .dat (iba/PDA3) è stata rimossa nella 3.0; i file .dat restano apribili");
        }
    }

    public Dictionary<string, object?> ToDictionary()
    {
        var result = new Dictionary<string, object?> { ["format"] = Format, ["profile"] = Profile };
        if (Format == "parquet") result["lossless"] = Lossless;
        result["codec"] = Codec;
        result["level"] = Level;
        result["row_group_rows"] = RowGroupRows;
        result["flush_seconds"] = FlushSeconds % 1 == 0 ? (object)(int)FlushSeconds : FlushSeconds;
        result["label"] = Label;
        if (Format != "parquet") result["lossless"] = Lossless;
        return result;
    }

    public string CompactJson()
    {
        using var buffer = new MemoryStream();
        using (var w = new Utf8JsonWriter(buffer, SacFormat.Compact)) Core.JsonOut.Write(w, ToDictionary());
        return System.Text.Encoding.UTF8.GetString(buffer.ToArray());
    }
}

/// <summary>One Parquet part file written row group by row group.</summary>
public sealed class ParquetPart : IDisposable
{
    static readonly string[] DictionaryColumns = ["node_id", "signal_name", "data_type", "status", "quality", "sample_ms", "status_code"];
    static readonly string[] DoubleColumns = ["timestamp_ms", "elapsed_ms", "value_numeric", "acquisition_timestamp_ms"];

    readonly ManagedOutputStream output;
    readonly ParquetFileWriter writer;

    public ParquetPart(Stream stream, StorageProfile profile, IReadOnlyDictionary<string, string> metadata)
    {
        output = new ManagedOutputStream(stream, leaveOpen: true);
        writer = new ParquetFileWriter(output, Schema(), Properties(profile), metadata.ToDictionary(k => k.Key, k => k.Value));
    }

    static Column[] Schema() =>
    [
        new Column<DateTime?>("timestamp_utc", LogicalType.Timestamp(isAdjustedToUtc: true, TimeUnit.Micros)),
        new Column<double?>("timestamp_ms"), new Column<double?>("elapsed_ms"), new Column<long?>("cycle"),
        new Column<string>("node_id", LogicalType.String()), new Column<string>("signal_name", LogicalType.String()),
        new Column<double?>("value_numeric"), new Column<string>("value_text", LogicalType.String()),
        new Column<string>("data_type", LogicalType.String()), new Column<string>("status", LogicalType.String()),
        new Column<bool?>("good"), new Column<string>("source_timestamp", LogicalType.String()),
        new Column<string>("server_timestamp", LogicalType.String()), new Column<int?>("sample_ms"),
        new Column<string>("quality", LogicalType.String()), new Column<uint?>("status_code"),
        new Column<string>("local_timestamp", LogicalType.String()), new Column<string>("ads_filetime", LogicalType.String()),
        new Column<string>("source_protocol", LogicalType.String()), new Column<double?>("acquisition_timestamp_ms"),
        new Column<string>("acquisition_origin", LogicalType.String()),
    ];

    static WriterProperties Properties(StorageProfile profile)
    {
        using var builder = new WriterPropertiesBuilder();
        builder.CreatedBy("Space Fieldbus Analyzer engine (ParquetSharp)").Version(ParquetVersion.PARQUET_2_6);
        if (profile.Codec == "snappy") return builder.Compression(Compression.Snappy).Build();
        builder.Compression(Compression.Zstd).CompressionLevel(profile.Level ?? 9)
            .DataPageVersion(ParquetDataPageVersion.V2).EnablePageChecksum().DisableDictionary();
        foreach (var column in DictionaryColumns) builder.EnableDictionary(column);
        // Byte-stream split keeps IEEE-754 bytes intact and compresses better.
        foreach (var column in DoubleColumns) builder.Encoding(column, Encoding.ByteStreamSplit);
        return builder.Build();
    }

    public void Write(IReadOnlyList<TableRecorder.Row> rows)
    {
        using var group = writer.AppendRowGroup();
        void Column<T>(Func<TableRecorder.Row, T> select)
        {
            using var column = group.NextColumn().LogicalWriter<T>();
            column.WriteBatch(rows.Select(select).ToArray());
        }
        Column<DateTime?>(r => r.TimestampUtc);
        Column<double?>(r => r.TimestampMs);
        Column<double?>(r => r.ElapsedMs);
        Column<long?>(r => r.Cycle);
        Column<string>(r => r.NodeId);
        Column<string>(r => r.SignalName);
        Column<double?>(r => r.ValueNumeric);
        Column<string?>(r => r.ValueText);
        Column<string>(r => r.DataType);
        Column<string>(r => r.Status);
        Column<bool?>(r => r.Good);
        Column<string?>(r => r.SourceTimestamp);
        Column<string?>(r => r.ServerTimestamp);
        Column<int?>(r => r.SampleMs);
        Column<string>(r => r.Quality);
        Column<uint?>(r => r.StatusCode);
        Column<string?>(r => r.LocalTimestamp);
        Column<string?>(r => r.AdsFiletime);
        Column<string>(r => r.SourceProtocol);
        Column<double?>(r => r.AcquisitionTimestampMs);
        Column<string>(r => r.AcquisitionOrigin);
    }

    /// <summary>Size of the rows as a complete standalone file (schema and footer included).</summary>
    public static long StandaloneSize(IReadOnlyList<TableRecorder.Row> rows, StorageProfile profile, IReadOnlyDictionary<string, string> metadata)
    {
        using var buffer = new MemoryStream();
        using (var part = new ParquetPart(buffer, profile, metadata)) part.Write(rows);
        return buffer.Length;
    }

    public void Dispose()
    {
        writer.Close();
        writer.Dispose();
        output.Dispose();
    }
}
