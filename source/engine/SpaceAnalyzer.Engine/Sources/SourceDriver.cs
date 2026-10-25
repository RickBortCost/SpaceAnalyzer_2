using System.Text.Json;
using SpaceAnalyzer.Engine.Acquisition;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Sources;

/// <summary>The signals of one protocol, its period and the Python selection generation.</summary>
public sealed record Selection(IReadOnlyList<string> Ids, int Period, Generation? Generation);

/// <summary>A streaming driver hands every decoded publication/telegram/notification here.</summary>
public delegate bool StreamSink(double t, List<Sample> rows, int period, Generation? generation);

/// <summary>
/// One protocol client. Polled drivers implement <see cref="ReadValuesAsync"/>; streaming
/// drivers deliver through <see cref="ConfigureStream"/>. All drivers are read-only.
/// </summary>
public abstract class SourceDriver : IAsyncDisposable
{
    protected SourceDriver(JsonElement cfg) => Cfg = cfg;

    public JsonElement Cfg { get; }
    public abstract string Protocol { get; }
    /// <summary>Connection facts shown in the UI and stored as acquisition_info.</summary>
    public System.Collections.Concurrent.ConcurrentDictionary<string, object?> Info { get; } = new();
    public virtual bool Streaming => false;
    /// <summary>A fatal acquisition error (lost data); the session reconnects and the recording fails.</summary>
    public virtual string Error => "";

    public abstract Task ConnectAsync(CancellationToken ct);
    public abstract Task DisconnectAsync();
    public virtual Task ReadStateAsync(CancellationToken ct) => Task.CompletedTask;
    public virtual Task CheckSymbolsAsync(CancellationToken ct) => Task.CompletedTask;
    public virtual Task<List<Sample>> ReadValuesAsync(IReadOnlyList<string> ids, CancellationToken ct) =>
        throw new NotSupportedException(Protocol + ": lettura ciclica non supportata");
    public virtual void ConfigureStream(Func<Selection> selection, StreamSink sink) { }
    /// <summary>Wait until every received item has been handed to the sink.</summary>
    public virtual bool Flush(TimeSpan timeout) => true;
    public virtual Dictionary<string, object?>? Diagnostics() => null;
    /// <summary>Recorder period hint for streaming sources (median source interval).</summary>
    public virtual int SamplePeriodMs(int fallback) => fallback;

    public virtual Task<object?> InspectAsync(string nodeId, CancellationToken ct) => throw new NotSupportedException("Catalogo non disponibile");
    public virtual Task<object?> BrowseAsync(string nodeId, CancellationToken ct) => throw new NotSupportedException("Catalogo non disponibile");
    public virtual Task<object?> ScanItemsAsync(string root, CancellationToken ct) => throw new NotSupportedException("Catalogo non disponibile");

    /// <summary>The Bad row recorded for every selected signal when the connection is lost.</summary>
    public virtual Sample LostRow(string nodeId, string error, double receivedMs) => new()
    {
        NodeId = nodeId, Value = null, Good = false, Quality = "Bad", Numeric = false, PlotValue = null,
        DataType = "Null", Status = "ConnectionLost", Error = error,
    };

    public async ValueTask DisposeAsync()
    {
        try { await DisconnectAsync(); } catch (Exception) { }
        GC.SuppressFinalize(this);
    }

    protected string Text(string key, string fallback = "") =>
        Cfg.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? fallback : fallback;
    protected int Int(string key, int fallback = 0) =>
        Cfg.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Number ? (int)v.GetDouble() : fallback;
    protected bool Flag(string key, bool fallback = false) =>
        Cfg.TryGetProperty(key, out var v) ? v.ValueKind == JsonValueKind.True || (v.ValueKind != JsonValueKind.False && fallback) : fallback;
}

/// <summary>Errors shown to the user as they are (Italian text, no stack).</summary>
public sealed class SourceException(string message) : Exception(message);
