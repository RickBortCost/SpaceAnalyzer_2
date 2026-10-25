using System.Buffers;
using System.Security.Authentication;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using MQTTnet;
using MQTTnet.Adapter;
using MQTTnet.Formatter;
using MQTTnet.Protocol;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.Mqtt;

/// <summary>
/// Read-only MQTT acquisition (port of mqtt_source.MQTTClient): the network loop only
/// copies publications into a bounded queue; one worker decodes, delivers and then
/// acknowledges QoS 1/2. No polling, holding, interpolation or broker writes.
/// </summary>
public sealed class MqttDriver : SourceDriver
{
    sealed record Item(long Serial, string Topic, byte[] Payload, double ReceivedMs, bool Retained, bool Dup,
        MqttApplicationMessageReceivedEventArgs? Message, int Qos, Selection? Snapshot, long ReceivedNs, long ReceivedMono);

    readonly object guard = new();
    readonly Queue<Item> queue = new();
    readonly SemaphoreSlim available = new(0);
    readonly int capacity;
    readonly CancellationTokenSource stop = new();
    readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    IMqttClient? client;
    Thread? consumer;
    Func<Selection>? selection;
    StreamSink? sink;
    readonly Dictionary<string, Dictionary<string, object?>> catalog = new();
    long catalogRevision;
    readonly string catalogSession = Convert.ToHexString(System.Security.Cryptography.RandomNumberGenerator.GetBytes(8)).ToLowerInvariant();
    long queueBytes, serial, processedSerial;
    double? lastArrival;
    long? lastReceivedMono;
    readonly Queue<double> periods = new();
    readonly Dictionary<string, double> topicTimes = new();
    readonly Queue<double> samplePeriods = new();
    readonly Dictionary<string, long> counts = new()
    {
        ["received"] = 0, ["processed"] = 0, ["invalid_payloads"] = 0, ["retained"] = 0, ["retained_excluded"] = 0, ["dup_flag"] = 0,
        ["queue_dropped"] = 0, ["oversized"] = 0, ["catalog_limited"] = 0, ["decoded_values"] = 0, ["selected_values"] = 0, ["late_samples"] = 0,
    };
    string error = "", connectionError = "";
    volatile bool lost;
    readonly object done = new();

    public MqttDriver(JsonElement cfg) : base(cfg)
    {
        capacity = Int("mqtt_queue", 2048);
        var tls = Flag("mqtt_tls");
        Info["mode"] = "mqtt";
        Info["transport"] = "MQTT " + Text("mqtt_version", "3.1.1") + (tls ? " TLS" : " TCP");
        Info["read_mode"] = "MQTT · ogni pubblicazione";
        Info["read_only"] = true;
        Info["endpoint"] = Endpoint(cfg);
        Info["topics"] = MqttFormat.TopicFilters(Text("mqtt_topics", "#"));
        Info["qos"] = (long)Int("mqtt_qos", 1);
        Info["clean_session"] = true;
        Info["timestamps"] = Text("mqtt_time_basis") == "payload" ? "Timestamp JSON" : "Ricezione locale";
        Info["quality_origin"] = "Payload finito e comunicazione; qualità JSON solo se configurata";
    }

    public override string Protocol => "mqtt";
    public override bool Streaming => true;
    public override string Error { get { lock (guard) return error.Length > 0 ? error : ""; } }

    public static string Endpoint(JsonElement cfg)
    {
        var host = cfg.GetProperty("mqtt_host").GetString() ?? "";
        if (host.Contains(':')) host = "[" + host.Trim('[', ']') + "]";
        var tls = cfg.TryGetProperty("mqtt_tls", out var t) && t.ValueKind == JsonValueKind.True;
        return (tls ? "mqtts" : "mqtt") + "://" + host + ":" + cfg.GetProperty("mqtt_port").GetRawText();
    }

    public override async Task ConnectAsync(CancellationToken ct)
    {
        var timeout = TimeSpan.FromMilliseconds(Int("mqtt_timeout_ms", 5000));
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct, stop.Token);
        deadline.CancelAfter(timeout);
        try { await Connect(deadline.Token); }
        catch (Exception exc)
        {
            await DisconnectAsync();
            if (ct.IsCancellationRequested) throw;
            var message = exc is OperationCanceledException ? "MQTT: timeout CONNACK/SUBACK del broker"
                : exc is SourceException ? exc.Message : TlsFailure(exc) is { } tls ? tls : connectionError.Length > 0 ? connectionError : exc.Message;
            throw new SourceException(message.Length > 0 ? message : "Connessione MQTT non riuscita");
        }
    }

    async Task Connect(CancellationToken ct)
    {
        var factory = new MqttClientFactory();
        var c = factory.CreateMqttClient();
        client = c;
        var builder = new MqttClientOptionsBuilder()
            .WithTcpServer(Text("mqtt_host"), Int("mqtt_port", 1883))
            .WithProtocolVersion(Text("mqtt_version") == "5" ? MqttProtocolVersion.V500 : MqttProtocolVersion.V311)
            .WithClientId(Text("mqtt_client_id") is { Length: > 0 } id ? id : "SpaceAnalyzer-" + Convert.ToHexString(RandomNumberGenerator.GetBytes(8)).ToLowerInvariant())
            .WithCleanSession(true)
            .WithKeepAlivePeriod(TimeSpan.FromSeconds(Int("mqtt_keepalive", 30)))
            .WithTimeout(TimeSpan.FromMilliseconds(Int("mqtt_timeout_ms", 5000)));
        if (Text("mqtt_username").Length > 0) builder.WithCredentials(Text("mqtt_username"), Text("mqtt_password"));
        if (Flag("mqtt_tls")) builder.WithTlsOptions(Tls);
        c.ApplicationMessageReceivedAsync += OnMessage;
        c.DisconnectedAsync += OnDisconnected;
        MqttClientConnectResult result;
        try { result = await c.ConnectAsync(builder.Build(), ct); }
        catch (MqttConnectingFailedException exc)
        {
            var code = System.Text.RegularExpressions.Regex.Match(exc.Message, @"\((\w+)\)");
            throw new SourceException("MQTT: connessione rifiutata dal broker (" + Reason(code.Success ? code.Groups[1].Value : exc.Message) + ")");
        }
        if (result.ResultCode != MqttClientConnectResultCode.Success)
            throw new SourceException("MQTT: connessione rifiutata dal broker (" + Reason(result.ResultCode.ToString()) + ")");
        if (stop.IsCancellationRequested) throw new SourceException("Connessione MQTT annullata");
        var subscribe = new MqttClientSubscribeOptionsBuilder();
        var qos = (MqttQualityOfServiceLevel)Int("mqtt_qos", 1);
        foreach (var topic in MqttFormat.TopicFilters(Text("mqtt_topics", "#"))) subscribe.WithTopicFilter(topic, qos);
        MqttClientSubscribeResult granted;
        try { granted = await c.SubscribeAsync(subscribe.Build(), ct); }
        catch (Exception exc) when (exc is not OperationCanceledException) { throw new SourceException("MQTT: impossibile inviare SUBSCRIBE"); }
        if (granted.Items.Count == 0 || granted.Items.Any(i => (int)i.ResultCode >= 0x80))
            throw new SourceException("MQTT: sottoscrizione rifiutata, verificare ACL/topic/QoS");
        Info["granted_qos"] = granted.Items.Select(i => (object?)(long)(int)i.ResultCode).ToList();
        if (connectionError.Length > 0) throw new SourceException(connectionError);
    }

    /// <summary>A failed TLS handshake (CERTIFICATE_VERIFY_FAILED in the Python client).</summary>
    static string? TlsFailure(Exception exc)
    {
        for (var e = exc; e != null; e = e.InnerException)
            if (e is AuthenticationException)
                return "MQTT TLS: certificato del broker non verificato (CA, nome host o validità): " + e.Message;
        return null;
    }

    static string Reason(string code) =>
        string.Concat(code.Select((ch, i) => i > 0 && char.IsUpper(ch) ? " " + ch : ch.ToString()));

    void Tls(MqttClientTlsOptionsBuilder o)
    {
        o.UseTls().WithSslProtocols(SslProtocols.Tls12 | SslProtocols.Tls13).WithTargetHost(Text("mqtt_host"));
        var caFile = Text("mqtt_ca_file");
        if (caFile.Length > 0)
        {
            var roots = new X509Certificate2Collection();
            roots.ImportFromPemFile(caFile);
            o.WithCertificateValidationHandler(e =>
            {
                // Same policy as ssl.create_default_context(cafile): chain to the given CA and matching host name.
                if ((e.SslPolicyErrors & System.Net.Security.SslPolicyErrors.RemoteCertificateNameMismatch) != 0) return false;
                if (e.Certificate == null) return false;
                using var chain = new X509Chain();
                chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                chain.ChainPolicy.CustomTrustStore.AddRange(roots);
                chain.ChainPolicy.RevocationMode = X509RevocationMode.NoCheck;
                if (e.Chain != null) foreach (var element in e.Chain.ChainElements) chain.ChainPolicy.ExtraStore.Add(element.Certificate);
                return chain.Build(new X509Certificate2(e.Certificate));
            });
        }
        var certFile = Text("mqtt_cert_file");
        if (certFile.Length > 0)
        {
            X509Certificate2 certificate;
            try { certificate = X509Certificate2.CreateFromPemFile(certFile, Text("mqtt_key_file")); }
            catch (CryptographicException) { throw new SourceException("MQTT TLS: usare una chiave client PEM che non richieda password interattiva"); }
            if (OperatingSystem.IsWindows()) certificate = new X509Certificate2(certificate.Export(X509ContentType.Pkcs12));
            o.WithClientCertificates(new[] { certificate });
        }
    }

    Task OnDisconnected(MqttClientDisconnectedEventArgs e)
    {
        if (!stop.IsCancellationRequested && e.ClientWasConnected)
        {
            lock (guard) if (connectionError.Length == 0) connectionError = "MQTT: collegamento al broker interrotto (" + e.Reason + ")";
            lost = true;
        }
        return Task.CompletedTask;
    }

    Task OnMessage(MqttApplicationMessageReceivedEventArgs e)
    {
        var receivedMono = Clock.MonoNs();
        var receivedNs = Clock.UtcNs();
        var receivedMs = receivedNs / 1e6;
        var snapshot = selection?.Invoke();
        var message = e.ApplicationMessage;
        var qos = (int)message.QualityOfServiceLevel;
        if (qos > 0) e.AutoAcknowledge = false;
        lock (guard)
        {
            counts["received"]++;
            if (message.Retain) counts["retained"]++;
            if (message.Dup) counts["dup_flag"]++;
            if (lastArrival is double previous) { periods.Enqueue(receivedMs - previous); while (periods.Count > 512) periods.Dequeue(); }
            lastArrival = receivedMs;
            lastReceivedMono = receivedMono;
            var size = message.Payload.Length;
            if (size > Int("mqtt_max_payload_kb", 256) * 1024L)
            {
                counts["oversized"]++; error = "MQTT: payload oltre il limite configurato"; lost = true; return Task.CompletedTask;
            }
            if (queueBytes + size > MqttFormat.MaxQueueBytes || queue.Count >= capacity)
            {
                counts["queue_dropped"]++; error = "MQTT: coda di acquisizione piena; pubblicazioni non acquisite"; lost = true; return Task.CompletedTask;
            }
            serial++;
            queueBytes += size;
            queue.Enqueue(new Item(serial, message.Topic, message.Payload.ToArray(), receivedMs, message.Retain, message.Dup, qos > 0 ? e : null, qos, snapshot, receivedNs, receivedMono));
        }
        available.Release();
        return Task.CompletedTask;
    }

    public override void ConfigureStream(Func<Selection> selection, StreamSink sink)
    {
        this.selection = selection;
        this.sink = sink;
        if (consumer == null)
        {
            consumer = new Thread(Consume) { Name = "MQTT-Acquire", IsBackground = true };
            consumer.Start();
        }
    }

    void Consume()
    {
        try
        {
            while (!stop.IsCancellationRequested)
            {
                if (!available.Wait(100)) continue;
                Item item;
                lock (guard) item = queue.Dequeue();
                try
                {
                    Decode(item);
                    if (item.Message != null) item.Message.AcknowledgeAsync(CancellationToken.None).GetAwaiter().GetResult();
                }
                finally
                {
                    lock (done)
                    {
                        lock (guard) { queueBytes -= item.Payload.Length; processedSerial = item.Serial; counts["processed"]++; }
                        Monitor.PulseAll(done);
                    }
                }
            }
        }
        catch (Exception exc)
        {
            lock (guard) error = "MQTT: elaborazione interrotta (" + exc.GetType().Name + ")";
            lost = true;
            lock (done) Monitor.PulseAll(done);
        }
    }

    void Decode(Item item)
    {
        var topic = item.Topic;
        var snapshot = item.Snapshot ?? selection?.Invoke() ?? new Selection(Array.Empty<string>(), Int("sample_ms", 10), null);
        var selected = new List<(string nodeId, string pointer)>();
        foreach (var nid in snapshot.Ids)
        {
            var (t, p) = MqttFormat.Parse(nid);
            if (t == topic) selected.Add((nid, p));
        }
        var quality = "Good";
        var status = item.Dup ? "MQTT DUP" : "Good";
        string? sourceTs = null;
        var ts = item.ReceivedMs;
        var failure = "";
        List<(string path, object? value)> leaves;
        object? data;
        try
        {
            var text = PyJson.DecodeUtf8(item.Payload);
            try { data = PyJson.Loads(text); }
            catch (PyJson.DecodeError)
            {
                if (text.TrimStart().StartsWith('{') || text.TrimStart().StartsWith('[')) throw new ArgumentException("JSON MQTT non valido");
                data = text;
            }
            leaves = MqttFormat.Flatten(data);
            var timestampPointer = Text("mqtt_timestamp_pointer");
            if (timestampPointer.Length > 0)
            {
                var (sourceMs, text2) = MqttFormat.Timestamp(MqttFormat.PointerValue(data, timestampPointer), Text("mqtt_timestamp_unit", "iso"));
                sourceTs = text2;
                if (Text("mqtt_time_basis") == "payload") ts = sourceMs;
            }
            var qualityPointer = Text("mqtt_quality_pointer");
            if (qualityPointer.Length > 0)
            {
                var raw = MqttFormat.PointerValue(data, qualityPointer);
                if (raw is not string q || q.ToLowerInvariant() is not ("good" or "uncertain" or "bad")) throw new ArgumentException("Qualità JSON: atteso Good, Uncertain o Bad");
                quality = char.ToUpperInvariant(q[0]) + q[1..].ToLowerInvariant();
                status = "MQTT JSON " + quality;
            }
        }
        catch (Exception exc) when (exc is ArgumentException or MqttFormat.KeyError or MqttFormat.IndexError or InsufficientExecutionStackException or OverflowException)
        {
            leaves = new(); data = null; quality = "Bad";
            var reason = exc is InsufficientExecutionStackException ? "maximum recursion depth exceeded while decoding a JSON document" : exc.Message;
            status = "MQTT payload non valido: " + (reason.Length > 160 ? reason[..160] : reason);
            failure = status;
            lock (guard) counts["invalid_payloads"]++;
            if (Text("mqtt_time_basis") == "payload")
            {
                // Do not fabricate a source timestamp or poison its live timeline.
                lock (guard) error = "MQTT: payload/timestamp non valido sulla base tempi sorgente";
                lost = true;
                return;
            }
        }
        if (item.Retained)
        {
            if (quality == "Good") quality = "Uncertain";
            status = "MQTT retained · " + status;
        }
        lock (guard)
        {
            foreach (var (path, value) in leaves)
            {
                var nid = MqttFormat.Address(topic, path);
                if (!catalog.ContainsKey(nid) && catalog.Count >= MqttFormat.MaxCatalog) { counts["catalog_limited"]++; continue; }
                var name = path.Length > 0 ? MqttFormat.PointerTokens(path)[^1] : topic.TrimEnd('/').Split('/')[^1] is { Length: > 0 } last ? last : topic;
                var metadata = new Dictionary<string, object?>
                {
                    ["nodeid"] = nid, ["display_name"] = name, ["browse_name"] = name, ["node_class"] = "Variable", ["has_children"] = false,
                    ["data_type"] = MqttFormat.ValueType(value), ["path"] = topic + path,
                    ["comment"] = topic + " · JSON Pointer " + (path.Length > 0 ? path : "(payload intero)"),
                };
                if (!catalog.TryGetValue(nid, out var known) || !Same(known, metadata)) { catalog[nid] = metadata; catalogRevision++; }
            }
            counts["decoded_values"] += leaves.Count;
            var hasBefore = topicTimes.TryGetValue(topic, out var before);
            if (hasBefore && ts - before > 0 && ts - before <= 60000) { samplePeriods.Enqueue(ts - before); while (samplePeriods.Count > 256) samplePeriods.Dequeue(); }
            if (hasBefore || topicTimes.Count < MqttFormat.MaxCatalog) topicTimes[topic] = hasBefore ? Math.Max(before, ts) : ts;
        }
        var rows = new List<Sample>();
        foreach (var (nid, pointer) in selected)
        {
            object? value = null;
            if (failure.Length == 0)
            {
                try { value = MqttFormat.PointerValue(data, pointer); }
                catch (Exception exc) when (exc is MqttFormat.KeyError or MqttFormat.IndexError) { value = null; }
            }
            var original = value;
            if (value is PyDict or List<object?>) value = PyJson.DumpsCompact(value);
            var row = MqttFormat.ValueRow(nid, value, quality, status, sourceTs, item.ReceivedMs);
            if (original is PyDict or List<object?>) { row.SourceValue = original; row.SourceDataType = original is PyDict ? "JSONObject" : "JSONArray"; }
            row.ReceivedNs = item.ReceivedNs;
            row.ReceivedMonoNs = item.ReceivedMono;
            rows.Add(row);
        }
        if (item.Retained && !Flag("mqtt_include_retained"))
        {
            lock (guard) counts["retained_excluded"]++;
            return;
        }
        if (rows.Count > 0 && sink != null)
        {
            lock (guard) counts["selected_values"] += rows.Count;
            sink(ts, rows, SamplePeriodMs(snapshot.Period), snapshot.Generation);
        }
    }

    static bool Same(Dictionary<string, object?> a, Dictionary<string, object?> b) => a.Count == b.Count && a.All(p => b.TryGetValue(p.Key, out var v) && Equals(v, p.Value));

    public override bool Flush(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        lock (done)
        {
            long target;
            lock (guard) target = serial;
            while (true)
            {
                lock (guard) if (processedSerial >= target) return true;
                if (consumer == null || !consumer.IsAlive) return false;
                var remaining = deadline - DateTime.UtcNow;
                if (remaining <= TimeSpan.Zero) return false;
                Monitor.Wait(done, remaining < TimeSpan.FromMilliseconds(100) ? remaining : TimeSpan.FromMilliseconds(100));
            }
        }
    }

    public override async Task DisconnectAsync()
    {
        if (consumer != null && !await Task.Run(() => Flush(TimeSpan.FromSeconds(2))))
        {
            lock (guard) if (error.Length == 0) error = "MQTT: coda non drenata prima della disconnessione; campioni non acquisiti";
            lost = true;
        }
        stop.Cancel();
        var c = client;
        if (c != null)
        {
            try { if (c.IsConnected) await c.DisconnectAsync(new MqttClientDisconnectOptionsBuilder().Build(), CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(2)); }
            catch (Exception) { }
            c.Dispose();
        }
        consumer?.Join(2000);
    }

    public override Task ReadStateAsync(CancellationToken ct)
    {
        string current, connection;
        lock (guard) { current = error; connection = connectionError; }
        if (current.Length > 0 || lost || client is not { IsConnected: true })
            throw new SourceException(current.Length > 0 ? current : connection.Length > 0 ? connection : "MQTT non connesso");
        if (consumer != null && !consumer.IsAlive) throw new SourceException("MQTT: elaborazione terminata");
        return Task.CompletedTask;
    }

    public override Sample LostRow(string nodeId, string error, double receivedMs) =>
        MqttFormat.ValueRow(nodeId, null, "Bad", "MQTT collegamento interrotto", null, receivedMs);

    public override int SamplePeriodMs(int fallback)
    {
        lock (guard)
        {
            var values = samplePeriods.OrderBy(v => v).ToList();
            var value = values.Count > 0 ? values[values.Count / 2] : fallback;
            return (int)Math.Max(1, Math.Min(60000, PyJson.Round(value)));
        }
    }

    public override Dictionary<string, object?> Diagnostics()
    {
        lock (guard)
        {
            var result = counts.ToDictionary(p => p.Key, p => (object?)p.Value);
            result["queue_samples"] = (long)queue.Count;
            result["queue_capacity"] = (long)capacity;
            result["queue_bytes"] = queueBytes;
            result["queue_bytes_limit"] = MqttFormat.MaxQueueBytes;
            result["catalog_signals"] = (long)catalog.Count;
            result["age_ms"] = lastReceivedMono is long mono ? (Clock.MonoNs() - mono) / 1e6 : null;
            result["arrival_period_ms"] = periods.Count > 0 ? periods.Sum() / periods.Count : null;
            result["sample_period_ms"] = (long)SamplePeriodMsUnlocked(10);
            result["error"] = error.Length > 0 ? error : connectionError;
            result["timestamp_origin"] = Info["timestamps"];
            return result;
        }
    }

    int SamplePeriodMsUnlocked(int fallback)
    {
        var values = samplePeriods.OrderBy(v => v).ToList();
        var value = values.Count > 0 ? values[values.Count / 2] : fallback;
        return (int)Math.Max(1, Math.Min(60000, PyJson.Round(value)));
    }

    /// <summary>Observed catalog: revision token and a copy of the nodes (never changes selection or samples).</summary>
    public (string revision, List<Dictionary<string, object?>>? nodes) CatalogSnapshot(string? known)
    {
        lock (guard)
        {
            var revision = catalogSession + ":" + catalogRevision;
            if (known == revision) return (revision, null);
            return (revision, catalog.Values.Select(v => new Dictionary<string, object?>(v)).ToList());
        }
    }

    public override Task<object?> InspectAsync(string nodeId, CancellationToken ct)
    {
        var (folder, topic, pointer) = MqttFormat.ParseFolder(nodeId);
        if (folder)
            return Task.FromResult<object?>(new Dictionary<string, object?> { ["nodeid"] = nodeId, ["display_name"] = topic ?? "Topic MQTT osservati", ["node_class"] = "Object", ["has_children"] = true });
        lock (guard)
            if (catalog.TryGetValue(nodeId, out var known)) return Task.FromResult<object?>(new Dictionary<string, object?>(known));
        var name = pointer.Length > 0 ? MqttFormat.PointerTokens(pointer)[^1] : topic!.Split('/')[^1];
        return Task.FromResult<object?>(new Dictionary<string, object?>
        {
            ["nodeid"] = nodeId, ["display_name"] = name, ["node_class"] = "Variable", ["has_children"] = false,
            ["path"] = topic + pointer, ["comment"] = "In attesa della prima pubblicazione",
        });
    }

    public override async Task<object?> BrowseAsync(string nodeId, CancellationToken ct)
    {
        var (folder, topic, _) = MqttFormat.ParseFolder(nodeId);
        List<string> topics;
        lock (guard)
        {
            if (!folder) return new List<object?>();
            if (topic != null) return catalog.Where(p => MqttFormat.Parse(p.Key).topic == topic).Select(p => (object?)new Dictionary<string, object?>(p.Value)).ToList();
            topics = catalog.Keys.Select(k => MqttFormat.Parse(k).topic).Distinct().OrderBy(t => t, StringComparer.Ordinal).ToList();
        }
        var result = new List<object?>();
        foreach (var t in topics) result.Add(await InspectAsync(MqttFormat.TopicFolder(t), ct));
        return result;
    }

    public override async Task<object?> ScanItemsAsync(string root, CancellationToken ct)
    {
        var (folder, topic, _) = MqttFormat.ParseFolder(root);
        if (!folder) return new List<object?> { await InspectAsync(root, ct) };
        lock (guard)
            return catalog.Where(p => topic == null || MqttFormat.Parse(p.Key).topic == topic).Select(p => (object?)new Dictionary<string, object?>(p.Value)).ToList();
    }
}
