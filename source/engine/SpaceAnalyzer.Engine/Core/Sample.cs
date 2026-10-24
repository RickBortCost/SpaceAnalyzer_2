using System.Text.Json;

namespace SpaceAnalyzer.Engine.Core;

/// <summary>
/// One acquired value with the semantics of the Python acquisition row.
/// Field names in comments are the Python row keys used by display, recording
/// and the primary capture.
/// </summary>
public sealed class Sample
{
    public required string NodeId { get; set; }                 // nodeid
    public object? Value { get; set; }                           // value (display-safe)
    public double? PlotValue { get; set; }                       // plot_value
    public bool Good { get; set; }                               // good
    public string? Quality { get; set; }                         // quality
    public bool Numeric { get; set; }                            // numeric
    public string? Status { get; set; }                          // status
    public long? StatusCode { get; set; }                        // status_code
    public string? DataType { get; set; }                        // data_type
    public string? SourceDataType { get; set; }                  // _source_data_type
    public string? SourceTs { get; set; }                        // source_ts
    public string? ServerTs { get; set; }                        // server_ts
    public string? SourceFiletime { get; set; }                  // source_filetime (decimal FILETIME text)
    public string? LocalTs { get; set; }                         // local_ts
    public long ReceivedNs { get; set; }                         // received_ns (UTC)
    public long ReceivedMonoNs { get; set; }                     // engine monotonic arrival
    public double? ReceivedMs { get; set; }                      // received_ms
    public long? SourceSequence { get; set; }                    // source_sequence
    public string? SourceEpoch { get; set; }                     // source_epoch
    public string? AcquisitionOrigin { get; set; }               // acquisition_origin
    public double? AcquisitionTimestampMs { get; set; }          // acquisition_timestamp_ms
    public string? SourceProtocol { get; set; }                  // source_protocol
    public bool ClockDiscontinuity { get; set; }                 // clock_discontinuity
    public string? Error { get; set; }                           // error
    public bool PrecisionWarning { get; set; }                   // precision_warning
    public string? TimestampOrigin { get; set; }                 // timestamp_origin
    public long? SourcePicoseconds { get; set; }                 // source_picoseconds
    public long? ServerPicoseconds { get; set; }                 // server_picoseconds
    public bool HasSourceValue { get; set; }                     // _source_value / _source_typed present
    public object? SourceValue { get; set; }                     // original typed value
    public byte[]? SourceBinary { get; set; }                    // original bytes (ADS)
    public string? ConnectionId { get; set; }                    // connection_id
    public long? SampleSequence { get; set; }                    // sample_sequence
    public long? ConnectionSequence { get; set; }                // connection_sequence
    public bool Calculated { get; set; }                         // calculated

    public Sample Copy() => (Sample)MemberwiseClone();

    public string ProtocolOrDefault => SourceProtocol ?? ProtocolOf(NodeId);

    public static string ProtocolOf(string nodeId) =>
        nodeId.StartsWith("calc:", StringComparison.Ordinal) ? "calculated"
        : nodeId.StartsWith("ads:", StringComparison.Ordinal) ? "ads"
        : nodeId.StartsWith("s7:", StringComparison.Ordinal) ? "s7"
        : nodeId.StartsWith("mqtt:", StringComparison.Ordinal) ? "mqtt" : "opcua";

    public string QualityOrDerived => Quality ?? (Good ? "Good" : (Status ?? "").StartsWith("Uncertain", StringComparison.Ordinal) ? "Uncertain" : "Bad");

    /// <summary>A row sent by Python (calculated signals, tests): same keys as the Python dict.</summary>
    public static Sample FromPython(JsonElement row)
    {
        string? Str(string key) => row.TryGetProperty(key, out var v) && v.ValueKind != JsonValueKind.Null ? (v.ValueKind == JsonValueKind.String ? v.GetString() : v.GetRawText()) : null;
        long? Long(string key) => row.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var l) ? l : null;
        double? Dbl(string key) => row.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;
        bool Flag(string key) => row.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.True;
        var sample = new Sample
        {
            NodeId = Str("nodeid") ?? throw new InvalidDataException("Riga senza nodeid"),
            Value = row.TryGetProperty("value", out var value) ? Values.FromJson(value) : null,
            PlotValue = Dbl("plot_value"),
            Good = Flag("good"),
            Quality = Str("quality"),
            Numeric = Flag("numeric"),
            Status = Str("status"),
            StatusCode = Long("status_code"),
            DataType = Str("data_type"),
            SourceDataType = Str("_source_data_type"),
            SourceTs = Str("source_ts"),
            ServerTs = Str("server_ts"),
            SourceFiletime = Str("source_filetime"),
            LocalTs = Str("local_ts"),
            ReceivedNs = Long("received_ns") ?? 0,
            ReceivedMs = Dbl("received_ms"),
            SourceSequence = Long("source_sequence"),
            SourceEpoch = Str("source_epoch"),
            AcquisitionOrigin = Str("acquisition_origin"),
            AcquisitionTimestampMs = Dbl("acquisition_timestamp_ms"),
            SourceProtocol = Str("source_protocol"),
            ClockDiscontinuity = Flag("clock_discontinuity"),
            Error = Str("error"),
            PrecisionWarning = Flag("precision_warning"),
            TimestampOrigin = Str("timestamp_origin"),
            SourcePicoseconds = Long("source_picoseconds"),
            ServerPicoseconds = Long("server_picoseconds"),
            ConnectionId = Str("connection_id"),
            SampleSequence = Long("sample_sequence"),
            ConnectionSequence = Long("connection_sequence"),
            Calculated = Flag("calculated"),
        };
        if (row.TryGetProperty("_source_typed", out var typed)) { sample.HasSourceValue = true; sample.SourceValue = Values.FromJson(typed); }
        else if (row.TryGetProperty("_source_value", out var original)) { sample.HasSourceValue = true; sample.SourceValue = Values.FromJson(original); }
        else if (row.TryGetProperty("source_value", out var source)) { sample.HasSourceValue = true; sample.SourceValue = Values.FromJson(source); }
        if (row.TryGetProperty("_source_binary_typed", out var binary) || row.TryGetProperty("source_binary", out binary))
            sample.SourceBinary = Values.FromJson(binary) as byte[];
        return sample;
    }

    /// <summary>Display/calculation row for Python (same keys the Python drivers produce).</summary>
    public void WriteDisplay(Utf8JsonWriter w)
    {
        w.WriteStartObject();
        w.WriteString("nodeid", NodeId);
        w.WritePropertyName("value"); Values.WritePlain(w, Value);
        w.WritePropertyName("plot_value"); if (PlotValue is double p && double.IsFinite(p)) Values.WriteDouble(w, p); else w.WriteNullValue();
        w.WriteBoolean("good", Good);
        w.WriteString("quality", QualityOrDerived);
        w.WriteBoolean("numeric", Numeric);
        WriteOptional(w, "status", Status);
        if (StatusCode is long code) w.WriteNumber("status_code", code); else w.WriteNull("status_code");
        WriteOptional(w, "data_type", DataType);
        WriteOptional(w, "source_ts", SourceTs);
        WriteOptional(w, "server_ts", ServerTs);
        if (SourceFiletime != null) w.WriteString("source_filetime", SourceFiletime);
        WriteOptional(w, "local_ts", LocalTs);
        w.WriteNumber("received_ns", ReceivedNs);
        w.WritePropertyName("received_ms"); Values.WriteDouble(w, ReceivedMs ?? ReceivedNs / 1e6);
        if (SourceSequence is long sequence) w.WriteNumber("source_sequence", sequence);
        if (SourceEpoch != null) w.WriteString("source_epoch", SourceEpoch);
        if (AcquisitionOrigin != null) w.WriteString("acquisition_origin", AcquisitionOrigin);
        if (AcquisitionTimestampMs is double acquired) { w.WritePropertyName("acquisition_timestamp_ms"); Values.WriteDouble(w, acquired); }
        w.WriteString("source_protocol", ProtocolOrDefault);
        if (ClockDiscontinuity) w.WriteBoolean("clock_discontinuity", true);
        if (Error != null) w.WriteString("error", Error);
        if (PrecisionWarning) w.WriteBoolean("precision_warning", true);
        if (TimestampOrigin != null) w.WriteString("timestamp_origin", TimestampOrigin);
        if (SourcePicoseconds is long sp) w.WriteNumber("source_picoseconds", sp);
        if (ServerPicoseconds is long vp) w.WriteNumber("server_picoseconds", vp);
        if (ConnectionId != null) w.WriteString("connection_id", ConnectionId);
        if (SampleSequence is long ss) w.WriteNumber("sample_sequence", ss);
        if (Calculated) w.WriteBoolean("calculated", true);
        w.WriteEndObject();
    }

    static void WriteOptional(Utf8JsonWriter w, string key, string? value)
    {
        if (value == null) w.WriteNull(key); else w.WriteString(key, value);
    }
}
