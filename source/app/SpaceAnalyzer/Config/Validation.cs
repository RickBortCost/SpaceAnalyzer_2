using System.Globalization;
using System.Net;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Drivers.Ads;
using SpaceAnalyzer.Engine.Drivers.Mqtt;
using SpaceAnalyzer.Engine.Drivers.OpcUa;
using SpaceAnalyzer.Engine.Drivers.S7;

namespace SpaceAnalyzer.App.Config;

/// <summary>Configuration validation of the 2.x service (mqtt_source, s7_protocols, ads_client, app.validated_config).</summary>
public static class Validation
{
    static long IntegerOrThrow(JsonNode? value, string message, double low, double high, bool rejectBool = true)
    {
        if (rejectBool && value.IsBool()) throw new UserError(message);
        var n = value.Float();
        if (n is not double d || !double.IsFinite(d) || Math.Floor(d) != d || d < low || d > high) throw new UserError(message);
        return (long)d;
    }

    public static string NodeIdText(JsonNode? value)
    {
        var text = value.Str();
        if (text == null || text.Trim().Length == 0 || text.Length > 4096)
            throw new UserError("NodeId non valido: usare ad esempio ns=2;s=PLC1.Temperatura oppure ns=2;i=1001.");
        var t = text.Trim();
        try
        {
            if (t.StartsWith("mqtt:", StringComparison.Ordinal)) { MqttFormat.ParseFolder(t); return t; }
            if (t.StartsWith("s7:", StringComparison.Ordinal)) return S7Types.ParseNode(text, true).NodeId;
            if (t.StartsWith("ads:", StringComparison.Ordinal)) { AdsProtocol.SymbolName(t); return t; }
        }
        catch (ArgumentException exc) { throw new UserError(exc.Message); }
        try { return OpcUaFormat.NodeIdText(Opc.Ua.NodeId.Parse(t)); }
        catch (Exception) { throw new UserError("NodeId non valido: " + text); }
    }

    // ---------------------------------------------------------------- MQTT

    public static void Mqtt(JsonObject cfg)
    {
        foreach (var (k, v) in Defaults.Mqtt()) if (!cfg.ContainsKey(k)) cfg[k] = v?.DeepClone();
        foreach (var key in new[] { "mqtt_host", "mqtt_client_id", "mqtt_username", "mqtt_password", "mqtt_ca_file", "mqtt_cert_file", "mqtt_key_file" })
            if (cfg[key].Str() is not string s || s.Contains('\0') || s.Length > 4096) throw new UserError("Parametro MQTT non valido: " + key);
        var host = cfg["mqtt_host"]!.Str()!.Trim();
        cfg["mqtt_host"] = host;
        if (host.Length == 0 || host.IndexOfAny(['/', '\\', '@', ' ', '\r', '\n']) >= 0) throw new UserError("MQTT: inserire solo IP/hostname del broker, senza schema o credenziali");
        foreach (var key in new[] { "mqtt_tls", "mqtt_include_retained" })
            if (!cfg[key].IsBool()) throw new UserError(key + ": valore booleano richiesto");
        foreach (var (key, low, high) in new[] { ("mqtt_port", 1, 65535), ("mqtt_qos", 0, 2), ("mqtt_keepalive", 5, 3600), ("mqtt_timeout_ms", 1000, 30000), ("mqtt_queue", 32, 16384), ("mqtt_max_payload_kb", 1, 8192) })
            cfg[key] = IntegerOrThrow(cfg[key], $"{key}: intero richiesto tra {low} e {high}", low, high);
        if (cfg["mqtt_version"].Str() is not ("3.1.1" or "5") || cfg["mqtt_time_basis"].Str() is not ("arrival" or "payload"))
            throw new UserError("Versione o base tempi MQTT non valida");
        if (cfg["mqtt_timestamp_unit"].Str() is not ("iso" or "s" or "ms" or "us" or "ns")) throw new UserError("Unità timestamp MQTT non valida");
        foreach (var key in new[] { "mqtt_timestamp_pointer", "mqtt_quality_pointer" })
        {
            if (cfg[key].Str() is not string pointer) throw new UserError("Percorso JSON MQTT non valido");
            try { MqttFormat.PointerTokens(pointer); } catch (ArgumentException exc) { throw new UserError(exc.Message); }
        }
        if (cfg["mqtt_time_basis"].Str() == "payload" && cfg["mqtt_timestamp_pointer"].Str()!.Length == 0) throw new UserError("Base tempi payload: indicare il JSON Pointer del timestamp");
        if (cfg["mqtt_password"].Str()!.Length > 0 && cfg["mqtt_username"].Str()!.Length == 0) throw new UserError("MQTT: indicare anche lo username");
        if ((cfg["mqtt_cert_file"].Str()!.Length > 0) != (cfg["mqtt_key_file"].Str()!.Length > 0)) throw new UserError("MQTT TLS client: servono certificato e chiave privata insieme");
        TopicFilters(cfg["mqtt_topics"]);
        try { MqttFormat.ParseFolder(cfg["mqtt_root"].Str() ?? throw new ArgumentException("Indirizzo MQTT richiesto: mqtt:plant/topic#/value")); }
        catch (ArgumentException exc) { throw new UserError(exc.Message); }
    }

    public static List<string> TopicFilters(JsonNode? value)
    {
        if (value.Str() is not string text || text.Trim().Length == 0 || text.Length > 16384) throw new UserError("Specificare almeno un filtro topic MQTT, uno per riga");
        var filters = text.Split('\n').Select(p => p.Trim()).Where(p => p.Length > 0).Distinct().ToList();
        if (filters.Count > 32) throw new UserError("MQTT: massimo 32 filtri topic");
        foreach (var topic in filters)
        {
            if (topic.Contains('\0') || System.Text.Encoding.UTF8.GetByteCount(topic) > 2048) throw new UserError("Filtro topic MQTT troppo lungo o non valido");
            var levels = topic.Split('/');
            for (var i = 0; i < levels.Length; i++)
                if (levels[i].Contains('+') && levels[i] != "+" || levels[i].Contains('#') && (levels[i] != "#" || i != levels.Length - 1))
                    throw new UserError("MQTT: + deve occupare un livello e # deve essere l’ultimo livello");
        }
        return filters;
    }

    public static string MqttAddress(string nodeId)
    {
        try { var (folder, _, _) = MqttFormat.ParseFolder(nodeId); if (folder) throw new ArgumentException("MQTT: usare mqtt:plant/topic# per payload semplice o mqtt:plant/topic#/value per JSON"); return nodeId; }
        catch (ArgumentException exc) { throw new UserError(exc.Message); }
    }

    // ---------------------------------------------------------------- S7

    static readonly Regex Tsap = new("^[0-9A-F]{4}$", RegexOptions.Compiled);

    public static void S7(JsonObject cfg)
    {
        foreach (var (k, v) in Defaults.S7()) if (!cfg.ContainsKey(k)) cfg[k] = v?.DeepClone();
        // s7_client.validate_config
        if (cfg["s7_family"].Str() is not ("1200/1500" or "300" or "400" or "custom")) throw new UserError("Famiglia S7 non valida");
        if (cfg["s7_host"].Str() is not string host || host.Trim().Length == 0 || host.IndexOfAny(['/', '\\', '\0', ' ', '\r', '\n']) >= 0)
            throw new UserError("S7: inserire IP o hostname del PLC");
        cfg["s7_host"] = host.Trim();
        foreach (var (key, lo, hi) in new[] { ("s7_tcp_port", 1, 65535), ("s7_rack", 0, 7), ("s7_slot", 0, 31), ("s7_connection_type", 1, 3), ("s7_timeout_ms", 100, 10000), ("s7_pdu", 240, 960) })
        {
            if (cfg[key].IsBool()) throw new UserError(key + ": numero intero richiesto");
            cfg[key] = IntegerOrThrow(cfg[key], $"{key}: intero tra {lo} e {hi}", lo, hi);
        }
        foreach (var key in new[] { "s7_local_tsap", "s7_remote_tsap" })
        {
            if (cfg[key].Str() is not string raw) throw new UserError(key + ": testo richiesto");
            var v = raw.Trim().ToUpperInvariant();
            if (v.StartsWith("0X", StringComparison.Ordinal)) v = v[2..];
            if ((key == "s7_local_tsap" || v.Length > 0) && !Tsap.IsMatch(v)) throw new UserError(key + ": quattro cifre esadecimali, es. 0100");
            cfg[key] = v;
        }
        cfg["s7_root"] = Wrap(() => S7Types.ParseTag(cfg["s7_root"].Truthy() ? cfg["s7_root"].Str() ?? "" : "s7:/", true).NodeId);
        cfg["s7_tags"] = NormalizeS7Tags(cfg["s7_tags"]);
        // s7_protocols.validate_config
        if (cfg["s7_mode"].Str() is not ("classic" or "symbolic" or "udp")) throw new UserError("Modalità S7 non valida");
        foreach (var key in new[] { "s7_symbolic_tls", "s7_legacy_key_fallback" })
            if (!cfg[key].IsBool()) throw new UserError(key + ": valore booleano richiesto");
        foreach (var key in new[] { "s7_symbolic_password", "s7_tls_ca", "s7_tls_cert", "s7_tls_key" })
            if (cfg[key].Str() is not string s || s.Length > 4096 || s.Contains('\0')) throw new UserError("Parametro S7 simbolico non valido: " + key);
        if ((cfg["s7_tls_cert"].Str()!.Length > 0) != (cfg["s7_tls_key"].Str()!.Length > 0)) throw new UserError("TLS client richiede sia certificato sia chiave privata");
        var symbolicRoot = Wrap(() => S7Types.ParseNode(cfg["s7_symbolic_root"].Truthy() ? cfg["s7_symbolic_root"].Str() ?? "" : "s7:sym:/", true).NodeId);
        if (!symbolicRoot.StartsWith("s7:sym:", StringComparison.Ordinal)) throw new UserError("Radice simbolica S7 non valida");
        cfg["s7_symbolic_root"] = symbolicRoot;
        if (cfg["s7_udp_root"].Str() != "s7:udp:/") throw new UserError("Radice UDP: usare s7:udp:/");
        if (cfg["s7_udp_bind"].Str() is not string bind || bind.Trim().Length == 0 || bind.IndexOfAny(['/', '\\', '\0', ' ', '\r', '\n']) >= 0)
            throw new UserError("Indirizzo IPv4 locale UDP non valido");
        cfg["s7_udp_bind"] = bind.Trim();
        foreach (var (key, low, high) in new[] { ("s7_udp_port", 1, 65535), ("s7_udp_sender_port", 0, 65535), ("s7_udp_module", 200, 3263), ("s7_udp_length", 8, 4096), ("s7_udp_timestamp_offset", -1, 4082), ("s7_udp_watchdog_ms", 20, 60000), ("s7_udp_queue", 32, 16384) })
            cfg[key] = IntegerOrThrow(cfg[key], $"{key}: intero tra {low} e {high}", low, high);
        var module = (long)cfg["s7_udp_module"].Num()!;
        if (!new long[] { 200, 1200, 2200, 3200 }.Any(start => start <= module && module <= start + 63)) throw new UserError("Modulo UDP Generic: 200–263, 1200–1263, 2200–2263 o 3200–3263");
        var length = (long)cfg["s7_udp_length"].Num()!;
        if (length % 2 == 1) throw new UserError("Lunghezza telegramma UDP deve essere pari (header incluso)");
        if (cfg["s7_udp_endian"].Str() is not ("big" or "little")) throw new UserError("Ordine byte UDP non valido");
        var offset = (long)cfg["s7_udp_timestamp_offset"].Num()!;
        if (offset >= 0 && offset + 8 > length - 6) throw new UserError("Timestamp PLC di 8 byte oltre il payload UDP");
        cfg["s7_udp_tags"] = NormalizeUdpTags(cfg["s7_udp_tags"], (int)length - 6);
    }

    static string Wrap(Func<string> parse)
    {
        try { return parse(); } catch (ArgumentException exc) { throw new UserError(exc.Message); }
    }

    public static JsonArray NormalizeS7Tags(JsonNode? tags)
    {
        if (tags is not JsonArray list || list.Count > 5000) throw new UserError("Catalogo S7: massimo 5000 definizioni");
        var result = new JsonArray();
        var seen = new HashSet<string>();
        foreach (var row in list)
        {
            if (row is not JsonObject r) throw new UserError("Definizione variabile S7 non valida");
            var spec = r["nodeid"].Truthy() ? r["nodeid"].Str() ?? PyStr(r["nodeid"]) : PyStr(r["address"] ?? "") + ":" + (r.ContainsKey("data_type") ? PyStr(r["data_type"]) : "REAL");
            var tag = Wrap(() => S7Types.ParseTag(spec).NodeId);
            if (!seen.Add(tag)) throw new UserError("Variabile S7 duplicata: " + tag);
            var parsed = S7Types.ParseTag(tag);
            var name = (r["display_name"].Truthy() ? PyStr(r["display_name"]) : r["name"].Truthy() ? PyStr(r["name"]) : parsed.Address).Trim();
            if (name.Length == 0 || name.Length > 160) throw new UserError("Nome variabile: da 1 a 160 caratteri");
            result.Add(new JsonObject { ["nodeid"] = tag, ["display_name"] = name, ["data_type"] = parsed.DataType });
        }
        return result;
    }

    public static JsonArray NormalizeUdpTags(JsonNode? rows, int payloadBytes)
    {
        if (rows is not JsonArray list || list.Count > 5000) throw new UserError("Catalogo UDP: massimo 5000 campi");
        var result = new JsonArray();
        var seen = new HashSet<string>();
        foreach (var row in list)
        {
            if (row is not JsonObject r) throw new UserError("Campo UDP non valido");
            var nid = r["nodeid"].Truthy() ? r["nodeid"].Str() : null;
            if (nid == null)
            {
                var offset = IntegerOrThrow(r["offset"], "Offset UDP: intero tra 0 e 4089", 0, 4089);
                var kind = (r.ContainsKey("data_type") ? PyStr(r["data_type"]) : "REAL").ToUpperInvariant();
                var bit = kind == "BOOL" ? "." + IntegerOrThrow(r.ContainsKey("bit") ? r["bit"] : 0, "Bit UDP: intero tra 0 e 7", 0, 7).ToString(CultureInfo.InvariantCulture) : "";
                nid = $"s7:udp:{offset}{bit}:{kind}";
            }
            var tag = S7Tag(nid);
            if (tag.Mode != "udp") throw new UserError("Il catalogo UDP accetta solo campi s7:udp:");
            if (tag.Offset + tag.Size > payloadBytes) throw new UserError("Campo oltre la lunghezza configurata del telegramma: " + nid);
            if (!seen.Add(tag.NodeId)) throw new UserError("Campo UDP duplicato: " + nid);
            var name = (r["display_name"].Truthy() ? PyStr(r["display_name"]) : r["name"].Truthy() ? PyStr(r["name"]) : tag.NodeId).Trim();
            if (name.Length == 0 || name.Length > 160) throw new UserError("Nome UDP: da 1 a 160 caratteri");
            result.Add(new JsonObject { ["nodeid"] = tag.NodeId, ["display_name"] = name, ["data_type"] = tag.DataType });
        }
        return result;
    }

    static S7Tag S7Tag(string nid)
    {
        try { return S7Types.ParseNode(nid); } catch (ArgumentException exc) { throw new UserError(exc.Message); }
    }

    /// <summary>str() of a JSON value as Python would print it.</summary>
    public static string PyStr(JsonNode? value) => value switch
    {
        null => "None",
        JsonValue v when v.IsString() => v.Str()!,
        JsonValue v when v.IsBool() => v.Bool()!.Value ? "True" : "False",
        JsonValue v when v.IsNumber() => v.ToJsonString(),
        _ => value.ToJsonString(),
    };

    // ---------------------------------------------------------------- ADS

    public static void Ads(JsonObject cfg)
    {
        if ((cfg["ads_transport"].Str() ?? "tcp") is not ("tcp" or "native")) throw new UserError("Trasporto ADS non valido");
        if (cfg.ContainsKey("ads_dll_path") && !cfg["ads_dll_path"].IsString()) throw new UserError("Percorso DLL non valido");
        if (cfg["source"].Str() is not ("opcua" or "ads" or "s7" or "mqtt")) throw new UserError("Fonte dati non supportata");
        foreach (var key in new[] { "ads_host", "ads_target_netid", "ads_source_netid", "ads_bind_ip", "ads_root" })
        {
            if (cfg[key].Str() is not string s) throw new UserError(key + ": testo richiesto");
            cfg[key] = s.Trim();
        }
        string Text(string k) => cfg[k]!.Str()!;
        try
        {
            if (cfg["source"].Str() == "ads")
            {
                if (Text("ads_host").Length == 0 || Text("ads_host").IndexOfAny(['/', '\\', '\0', ' ']) >= 0) throw new UserError("Inserire IP o hostname del dispositivo ADS");
                AdsProtocol.NetId(Text("ads_target_netid"));
            }
            else if (Text("ads_target_netid").Length > 0) AdsProtocol.NetId(Text("ads_target_netid"));
            if (Text("ads_source_netid").Length > 0) AdsProtocol.NetId(Text("ads_source_netid"));
        }
        catch (ArgumentException exc) { throw new UserError(exc.Message); }
        if (Text("ads_bind_ip").Length > 0 && (!IPAddress.TryParse(Text("ads_bind_ip"), out var ip) || ip.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork || Text("ads_bind_ip").Count(c => c == '.') != 3))
            throw new UserError($"'{Text("ads_bind_ip")}' does not appear to be an IPv4 address");
        foreach (var (key, lo, hi) in new[] { ("ads_port", 1, 65535), ("ads_tcp_port", 1, 65535), ("ads_source_port", 1, 65534), ("ads_timeout_ms", 100, 10000), ("ads_notify_batch_ms", 0, 1000) })
            cfg[key] = IntegerOrThrow(cfg[key], $"{key}: intero tra {lo} e {hi}", lo, hi, rejectBool: false);
        if (cfg["ads_mode"].Str() is not ("sum" or "individual" or "notify")) throw new UserError("Modalità ADS non valida");
        if (cfg["source"].Str() == "ads" && cfg["ads_mode"].Str() == "notify" && cfg["ads_transport"].Str() != "native")
            throw new UserError("Le notifiche cicliche richiedono TwinCAT locale. Selezionare questo collegamento oppure SumRead per ADS/TCP.");
        if (cfg["ads_encoding"].Str() is not ("cp1252" or "utf-8")) throw new UserError("Codifica STRING non valida");
        var root = Text("ads_root");
        if (root.Length > 0 && !root.StartsWith("ads:", StringComparison.Ordinal)) root = "ads:" + root;
        cfg["ads_root"] = root.Length > 0 ? root : "ads:/";
        try { AdsProtocol.SymbolName(cfg["ads_root"]!.Str()!); } catch (ArgumentException exc) { throw new UserError(exc.Message); }
    }

    // ---------------------------------------------------------------- whole configuration

    public static HashSet<string> ConfiguredSources(JsonObject cfg)
    {
        if (cfg["enabled_sources"] is JsonArray explicitSources && explicitSources.Count > 0)
            return explicitSources.Select(s => s.Str() ?? "").ToHashSet();
        var source = cfg["source"].Str() ?? "opcua";
        return cfg["mqtt_parallel"].Truthy() ? [source, "mqtt"] : [source];
    }

    public static JsonObject Validate(JsonNode? data, JsonObject loaded)
    {
        if (data is not JsonObject input) throw new UserError("Configurazione non valida");
        var defaults = Defaults.Config();
        var cfg = loaded;
        foreach (var (k, v) in input) if (defaults.ContainsKey(k)) cfg[k] = v?.DeepClone();
        if (cfg["ui_language"].Str() is not string language || !Defaults.Languages.Contains(language)) throw new UserError("Lingua interfaccia non supportata");
        foreach (var key in new[] { "endpoint", "security", "security_mode", "username", "password", "application_uri" })
            if (!cfg[key].IsString()) throw new UserError("Parametro non valido: " + key);
        Ads(cfg); S7(cfg); Mqtt(cfg);
        if (!cfg["mqtt_parallel"].IsBool()) throw new UserError("mqtt_parallel deve essere booleano");
        if (cfg["enabled_sources"] is not JsonArray sources || sources.Count > 4 || sources.Any(p => !Defaults.Protocols.Contains(p.Str() ?? "")) || sources.Select(p => p.Str()).Distinct().Count() != sources.Count)
            throw new UserError("enabled_sources: scegliere protocolli distinti tra OPC UA, ADS, MQTT e S7");
        if (cfg["source_intervals"] is not JsonObject intervals || intervals.Any(p => !Defaults.Protocols.Contains(p.Key)))
            throw new UserError("source_intervals: configurazione non valida");
        foreach (var protocol in ConfiguredSources(cfg))
        {
            var local = (JsonObject)cfg.DeepClone();
            local["source"] = protocol;
            Ads(local); S7(local); Mqtt(local);
        }
        foreach (var (protocol, period) in intervals)
        {
            var low = protocol == "ads" ? 1 : 10;
            if (period is not JsonValue v || !v.IsNumber() || v.ToJsonString().Contains('.') || v.ToJsonString().Contains('e') || v.Num() < low || v.Num() > 60000)
                throw new UserError("Periodo sorgente non valido: " + protocol);
        }
        var endpoint = cfg["endpoint"].Str()!.Trim();
        cfg["endpoint"] = endpoint;
        CheckEndpoint(endpoint);
        var security = Defaults.NormalizePolicy(cfg["security"].Str()!);
        cfg["security"] = security;
        if (security != "None" && !Defaults.Policies.Contains(security)) throw new UserError("Security Policy non supportata");
        if (security == "None") cfg["security_mode"] = "None";
        else if (cfg["security_mode"].Str() is not ("Sign" or "SignAndEncrypt")) throw new UserError("Selezionare Sign oppure SignAndEncrypt");
        var minimum = cfg["source"].Str() == "ads" ? 1 : 10;
        foreach (var (key, low, high) in new[] { ("sample_ms", minimum, 60000), ("window_seconds", 10, 3600), ("max_samples", 1000, 360000), ("scan_limit", 100, 500000) })
            cfg[key] = IntegerOrThrow(cfg[key], $"{key}: inserire un intero tra {low} e {high}", low, high);
        if (cfg["buffer_mode"].Str() is not ("time" or "samples")) throw new UserError("Modalità buffer non valida");
        if (!cfg["auto_reconnect"].IsBool()) throw new UserError("auto_reconnect deve essere un valore booleano");
        cfg["root_nodeid"] = NodeIdText(cfg["root_nodeid"].Truthy() ? cfg["root_nodeid"] : "i=85");
        return cfg;
    }

    static void CheckEndpoint(string endpoint)
    {
        var m = Regex.Match(endpoint, @"^([A-Za-z][A-Za-z0-9+.\-]*):\/\/([^/?#]*)");
        var scheme = m.Success ? m.Groups[1].Value.ToLowerInvariant() : "";
        var netloc = m.Success ? m.Groups[2].Value : "";
        var userinfo = netloc.Contains('@');
        var hostPort = userinfo ? netloc[(netloc.LastIndexOf('@') + 1)..] : netloc;
        string host, port = "";
        if (hostPort.StartsWith('['))
        {
            var close = hostPort.IndexOf(']');
            host = close > 0 ? hostPort[1..close] : "";
            port = close > 0 && close + 1 < hostPort.Length && hostPort[close + 1] == ':' ? hostPort[(close + 2)..] : "";
        }
        else
        {
            var colon = hostPort.LastIndexOf(':');
            host = colon >= 0 ? hostPort[..colon] : hostPort;
            port = colon >= 0 ? hostPort[(colon + 1)..] : "";
        }
        if (scheme != "opc.tcp" || host.Length == 0 || userinfo)
            throw new UserError("Specificare un endpoint opc.tcp://host:porta e le credenziali nei campi dedicati.");
        if (port.Length > 0)
        {
            if (!port.All(char.IsAsciiDigit)) throw new UserError($"Port could not be cast to integer value as '{port}'");
            if (!int.TryParse(port, out var p) || p > 65535) throw new UserError("Port out of range 0-65535");
        }
    }

    public static JsonArray Nodes(JsonNode? nodes)
    {
        if (nodes is not JsonArray list || list.Count > Defaults.MaxSelected) throw new UserError($"Selezionare al massimo {Defaults.MaxSelected} variabili");
        var result = new List<(string id, JsonObject node)>();
        foreach (var item in list)
        {
            var row = item.IsString() ? new JsonObject { ["nodeid"] = item.Str() } : item as JsonObject ?? throw new UserError("Selezione nodi non valida");
            var nid = NodeIdText(row["nodeid"]);
            if (nid.StartsWith("s7:", StringComparison.Ordinal)) S7Tag(nid);
            if (nid.StartsWith("mqtt:", StringComparison.Ordinal)) MqttAddress(nid);
            var node = new JsonObject
            {
                ["nodeid"] = nid, ["display_name"] = row["display_name"].Truthy() ? PyStr(row["display_name"]) : nid,
                ["browse_name"] = row["browse_name"].Truthy() ? PyStr(row["browse_name"]) : "", ["node_class"] = "Variable",
            };
            var existing = result.FindIndex(r => r.id == nid);
            if (existing >= 0) result[existing] = (nid, node); else result.Add((nid, node));
        }
        return new JsonArray(result.Select(r => (JsonNode?)r.node).ToArray());
    }
}
