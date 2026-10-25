using System.Text.Json;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>A selected recording format behind the primary capture.</summary>
public interface IFormatRecorder
{
    bool Busy { get; }
    string Error { get; }
    void Start(string format, IReadOnlyList<NodeSpec> nodes, string endpoint, int sampleMs, JsonElement options);
    bool Enqueue(double timestamp, IReadOnlyList<Sample> rows, int period);
    void Fail(string message, string reason = "source_acquisition_error");
    void SourceDiagnostics(JsonElement diagnostics);
    void Stop(string reason, TimeSpan timeout);
    void WriteStatus(Utf8JsonWriter w);

    JsonElement StatusJson()
    {
        using var document = JsonDocument.Parse(SacFormat.Json(WriteStatus));
        return document.RootElement.Clone();
    }
}

public static class FormatRecorders
{
    /// <summary>CSV and Parquet are written live; FDA and RBF are converted from a Parquet original after Stop.</summary>
    public static IFormatRecorder Create(string format, string directory, string version) => format switch
    {
        "csv" or "parquet" => new TableRecorder(directory, version),
        "das" or "rbf" => new ConvertedRecorder(directory, version),
        _ => throw new ArgumentException("Scegliere CSV, Binario Parquet, FDA DAS4 o RBF v3. La registrazione .dat (iba/PDA3) è stata rimossa nella 3.0; i file .dat restano apribili"),
    };
}
