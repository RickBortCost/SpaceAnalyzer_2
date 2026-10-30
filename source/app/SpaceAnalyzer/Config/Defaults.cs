using System.Text.Json.Nodes;

namespace SpaceAnalyzer.App.Config;

/// <summary>DEFAULT_CONFIG of the 2.x service (key order preserved for saved files).</summary>
public static class Defaults
{
    public const string AppName = "Space Fieldbus Analyzer";
    public const string Version = "3.17.0";
    public const int MaxSelected = 64;
    public const int MaxSamples = 360000;
    public const int MaxTotalSamples = 2000000;

    public static JsonObject Mqtt() => new()
    {
        ["mqtt_host"] = "127.0.0.1", ["mqtt_port"] = 1883, ["mqtt_version"] = "3.1.1", ["mqtt_client_id"] = "", ["mqtt_username"] = "",
        ["mqtt_password"] = "", ["mqtt_tls"] = false, ["mqtt_ca_file"] = "", ["mqtt_cert_file"] = "", ["mqtt_key_file"] = "",
        ["mqtt_keepalive"] = 30, ["mqtt_timeout_ms"] = 5000, ["mqtt_topics"] = "#", ["mqtt_qos"] = 1, ["mqtt_root"] = "mqtt:/",
        ["mqtt_timestamp_pointer"] = "", ["mqtt_timestamp_unit"] = "iso", ["mqtt_time_basis"] = "arrival", ["mqtt_quality_pointer"] = "",
        ["mqtt_include_retained"] = false, ["mqtt_queue"] = 2048, ["mqtt_max_payload_kb"] = 256,
    };

    public static JsonObject S7Classic() => new()
    {
        ["s7_host"] = "192.168.0.1", ["s7_tcp_port"] = 102, ["s7_rack"] = 0, ["s7_slot"] = 1, ["s7_family"] = "1200/1500",
        ["s7_connection_type"] = 3, ["s7_local_tsap"] = "0100", ["s7_remote_tsap"] = "", ["s7_timeout_ms"] = 3000, ["s7_pdu"] = 480,
        ["s7_root"] = "s7:/", ["s7_tags"] = new JsonArray(),
    };

    public static JsonObject S7() 
    {
        var o = S7Classic();
        foreach (var (k, v) in new JsonObject
        {
            ["s7_mode"] = "classic", ["s7_symbolic_password"] = "", ["s7_symbolic_tls"] = false, ["s7_tls_ca"] = "", ["s7_tls_cert"] = "",
            ["s7_tls_key"] = "", ["s7_legacy_key_fallback"] = true, ["s7_symbolic_root"] = "s7:sym:/", ["s7_udp_root"] = "s7:udp:/",
            ["s7_udp_bind"] = "0.0.0.0", ["s7_udp_port"] = 4170, ["s7_udp_sender_port"] = 0, ["s7_udp_module"] = 200, ["s7_udp_length"] = 16,
            ["s7_udp_endian"] = "big", ["s7_udp_timestamp_offset"] = -1, ["s7_udp_watchdog_ms"] = 1000, ["s7_udp_queue"] = 2048,
            ["s7_udp_tags"] = new JsonArray(), ["s7_time_tag"] = "", ["s7_symbolic_time_tag"] = "", ["s7_time_unit"] = "us",
        }) o[k] = v?.DeepClone();
        return o;
    }

    public static JsonObject Config()
    {
        var o = new JsonObject { ["mqtt_parallel"] = false, ["enabled_sources"] = new JsonArray(), ["source_intervals"] = new JsonObject(), ["ui_language"] = "it" };
        foreach (var (k, v) in Mqtt()) o[k] = v?.DeepClone();
        foreach (var (k, v) in S7()) o[k] = v?.DeepClone();
        foreach (var (k, v) in new JsonObject
        {
            ["source"] = "opcua", ["ads_transport"] = "tcp", ["ads_dll_path"] = "", ["ads_host"] = "127.0.0.1", ["ads_target_netid"] = "127.0.0.1.1.1",
            ["ads_port"] = 851, ["ads_tcp_port"] = 48898, ["ads_source_netid"] = "", ["ads_source_port"] = 32905,
            ["ads_bind_ip"] = "", ["ads_timeout_ms"] = 3000, ["ads_notify_batch_ms"] = 0, ["ads_mode"] = "sum", ["ads_encoding"] = "cp1252", ["ads_root"] = "ads:/",
            ["endpoint"] = "opc.tcp://127.0.0.1:4840", ["security"] = "Basic256Sha256", ["security_mode"] = "SignAndEncrypt",
            ["username"] = "", ["password"] = "", ["client_cert"] = "certificates/client.der", ["client_key"] = "certificates/client-key.pem",
            ["server_cert"] = "certificates/server.der", ["application_uri"] = "", ["auto_reconnect"] = true, ["sample_ms"] = 500,
            ["root_nodeid"] = "i=85", ["window_seconds"] = 60, ["max_samples"] = 360000, ["buffer_mode"] = "time", ["scan_limit"] = 50000,
        }) o[k] = v?.DeepClone();
        return o;
    }

    public static readonly string[] Protocols = ["opcua", "ads", "mqtt", "s7"];
    public static readonly string[] Languages = ["en", "it", "de", "fr", "es"];
    public static readonly Dictionary<string, string> PolicyAliases = new()
    {
        ["Aes128_Sha256_RsaOaep"] = "Aes128Sha256RsaOaep", ["Aes256_Sha256_RsaPss"] = "Aes256Sha256RsaPss", ["Basic256Sha256"] = "Basic256Sha256", ["None"] = "None",
    };
    public static readonly string[] Policies = ["Basic256Sha256", "Aes128Sha256RsaOaep", "Aes256Sha256RsaPss"];

    public static string NormalizePolicy(string value) => PolicyAliases.GetValueOrDefault(value, value);
}
