using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.Mqtt;

namespace SpaceAnalyzer.Engine.Sources;

/// <summary>Protocol and mode → driver. Configuration is validated by the UI before it reaches the engine.</summary>
public static class DriverFactory
{
    public static SourceDriver Create(string protocol, JsonElement cfg, bool catalog = false) => protocol switch
    {
        "mqtt" => new MqttDriver(cfg),
        _ => throw new SourceException("Protocollo non supportato dal motore: " + protocol),
    };

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
