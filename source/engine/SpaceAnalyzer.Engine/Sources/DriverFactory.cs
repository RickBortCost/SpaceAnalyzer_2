using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.Ads;
using SpaceAnalyzer.Engine.Drivers.Mqtt;
using SpaceAnalyzer.Engine.Drivers.OpcUa;
using SpaceAnalyzer.Engine.Drivers.S7;

namespace SpaceAnalyzer.Engine.Sources;

/// <summary>Protocol and mode → driver. Configuration is validated by the UI before it reaches the engine.</summary>
public static class DriverFactory
{
    public static SourceDriver Create(string protocol, JsonElement cfg, bool catalog = false) => protocol switch
    {
        "mqtt" => new MqttDriver(cfg),
        "opcua" => new OpcUaDriver(cfg),
        "ads" => !catalog && Mode(cfg, "ads_mode", "sum") == "notify" ? new AdsNotifyDriver(cfg) : new AdsDriver(cfg, catalog),
        "s7" => Mode(cfg, "s7_mode", "classic") switch
        {
            "udp" => new S7UdpDriver(cfg),
            "symbolic" => new S7SymbolicDriver(cfg),
            _ => new S7ClassicDriver(cfg),
        },
        _ => throw new SourceException("Protocollo non supportato dal motore: " + protocol),
    };

    static string Mode(JsonElement cfg, string key, string fallback) =>
        cfg.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? fallback : fallback;

    public static Sample LostRow(string protocol, string nodeId, string error, double receivedMs) => protocol switch
    {
        "mqtt" => MqttFormat.ValueRow(nodeId, null, "Bad", "MQTT collegamento interrotto", null, receivedMs),
        _ => new Sample
        {
            NodeId = nodeId, Value = null, Good = false, Quality = "Bad", Numeric = false, PlotValue = null,
            DataType = "Null", Status = "ConnectionLost", Error = error,
        },
    };
}
