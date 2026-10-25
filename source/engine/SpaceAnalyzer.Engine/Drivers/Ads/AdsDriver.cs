using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>ADS polling (SumRead or individual Read) over ADS/TCP or the local TwinCAT router.</summary>
public class AdsDriver : SourceDriver
{
    protected readonly AdsClient Client;

    public AdsDriver(JsonElement cfg, bool catalog = false) : base(cfg)
    {
        Client = new AdsClient(cfg, catalog);
    }

    public override string Protocol => "ads";
    public AdsClient Ads => Client;

    public override async Task ConnectAsync(CancellationToken ct)
    {
        try { await Client.Connect(ct); }
        catch (AdsException) { throw; }
        catch (Exception exc) when (exc is TimeoutException or InvalidDataException) { throw new SourceException(exc.Message); }
        Sync();
    }

    protected void Sync()
    {
        foreach (var (k, v) in Client.Info) Info[k] = v;
    }

    public override Task DisconnectAsync() => Client.Disconnect();

    public override async Task ReadStateAsync(CancellationToken ct)
    {
        await Client.ReadState(ct);
        Sync();
    }

    public override async Task CheckSymbolsAsync(CancellationToken ct)
    {
        await Client.CheckSymbols(ct);
        Sync();
    }

    public override async Task<List<Sample>> ReadValuesAsync(IReadOnlyList<string> ids, CancellationToken ct)
    {
        var rows = await Client.ReadValues(ids, ct);
        Sync();
        return rows;
    }

    public override async Task<object?> InspectAsync(string nodeId, CancellationToken ct) => await Client.Inspect(nodeId, ct);
    public override async Task<object?> BrowseAsync(string nodeId, CancellationToken ct) => (await Client.BrowsePage(nodeId, 0, 250, ct))["nodes"];
    public Task<Dictionary<string, object?>> BrowsePageAsync(string nodeId, long offset, long count, CancellationToken ct) => Client.BrowsePage(nodeId, offset, count, ct);
}
