using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Opc.Ua;
using Opc.Ua.Client;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.OpcUa;

/// <summary>
/// OPC UA acquisition (port of the app.py sampler loop and source_sessions on asyncua):
/// MonitoredItems at the requested sampling interval archive every notification; a periodic
/// snapshot of the latest values feeds the uniform trend, calculations and the selected format.
/// Servers or nodes that reject subscriptions fall back to Read with per-node retry.
/// </summary>
public sealed class OpcUaDriver : SourceDriver, IAcquisitionLoop
{
    public static readonly Dictionary<string, string> Policies = new()
    {
        ["None"] = SecurityPolicies.None, ["Basic256Sha256"] = SecurityPolicies.Basic256Sha256,
        ["Aes128Sha256RsaOaep"] = SecurityPolicies.Aes128_Sha256_RsaOaep, ["Aes256Sha256RsaPss"] = SecurityPolicies.Aes256_Sha256_RsaPss,
    };
    const uint ServerStateNode = 2259;

    ISession? session;
    Subscription? subscription;
    readonly object gate = new();
    readonly Dictionary<string, (Sample row, double received)> latest = new();
    Dictionary<string, Sample> subscriptionRows = new();
    Dictionary<string, string> subscriptionBad = new();
    readonly HashSet<string> individualNodes = new();
    (string[] ids, long? revision, int period)? subscriptionKey;
    volatile string keepAliveError = "";
    string overflow = "";

    public OpcUaDriver(JsonElement cfg) : base(cfg)
    {
        Info["transport"] = "OPC UA";
        Info["read_mode"] = "OPC UA Subscription";
    }

    public override string Protocol => "opcua";
    public ISession? Session => session;

    public static async Task<ApplicationConfiguration> Configuration(JsonElement cfg, string? pinnedServerCertificate)
    {
        string Text(string k) => cfg.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : "";
        var pki = Path.Combine(Path.GetTempPath(), "SpaceAnalyzer-opcua-pki");
        var config = new ApplicationConfiguration
        {
            ApplicationName = "Space Fieldbus Analyzer",
            ApplicationUri = Text("application_uri"),
            ApplicationType = ApplicationType.Client,
            SecurityConfiguration = new SecurityConfiguration
            {
                ApplicationCertificate = new CertificateIdentifier(),
                TrustedPeerCertificates = new CertificateTrustList { StoreType = CertificateStoreType.Directory, StorePath = Path.Combine(pki, "trusted") },
                TrustedIssuerCertificates = new CertificateTrustList { StoreType = CertificateStoreType.Directory, StorePath = Path.Combine(pki, "issuers") },
                RejectedCertificateStore = new CertificateTrustList { StoreType = CertificateStoreType.Directory, StorePath = Path.Combine(pki, "rejected") },
                AutoAcceptUntrustedCertificates = false, RejectSHA1SignedCertificates = false, MinimumCertificateKeySize = 1024,
                AddAppCertToTrustedStore = false,
            },
            TransportConfigurations = new TransportConfigurationCollection(),
            TransportQuotas = new TransportQuotas { OperationTimeout = 8000, MaxMessageSize = 64 * 1024 * 1024, MaxStringLength = 16 * 1024 * 1024, MaxByteStringLength = 64 * 1024 * 1024, MaxArrayLength = 1024 * 1024 },
            ClientConfiguration = new ClientConfiguration { DefaultSessionTimeout = 3600000 },
        };
        var certPath = Text("_client_cert"); var keyPath = Text("_client_key");
        if (certPath.Length > 0 && File.Exists(certPath) && File.Exists(keyPath))
        {
            using var der = X509CertificateLoader.LoadCertificate(File.ReadAllBytes(certPath));
            var pem = new string(PemEncoding.Write("CERTIFICATE", der.RawData));
            var certificate = X509Certificate2.CreateFromPem(pem, File.ReadAllText(keyPath));
            if (OperatingSystem.IsWindows()) certificate = X509CertificateLoader.LoadPkcs12(certificate.Export(X509ContentType.Pkcs12), null);
            config.SecurityConfiguration.ApplicationCertificate.Certificate = certificate;
        }
        await config.Validate(ApplicationType.Client);
        // Trust exactly the server certificate acquired or imported by the user (2.x pinning).
        byte[]? pinned = pinnedServerCertificate != null && File.Exists(pinnedServerCertificate) ? File.ReadAllBytes(pinnedServerCertificate) : null;
        config.CertificateValidator.CertificateValidation += (_, e) =>
        {
            if (pinned != null && e.Certificate.RawData.AsSpan().SequenceEqual(pinned)) e.Accept = true;
        };
        return config;
    }

    public static async Task<EndpointDescriptionCollection> Endpoints(ApplicationConfiguration config, string url, CancellationToken ct)
    {
        var endpointConfiguration = EndpointConfiguration.Create(config);
        endpointConfiguration.OperationTimeout = 8000;
        using var discovery = DiscoveryClient.Create(config, new Uri(url), endpointConfiguration);
        return await discovery.GetEndpointsAsync(null, ct);
    }

    public override async Task ConnectAsync(CancellationToken ct)
    {
        var url = Text("endpoint");
        var security = Text("security", "None");
        var mode = security == "None" ? "None" : Text("security_mode", "SignAndEncrypt");
        if (security != "None" && Text("_server_cert") is var server && (server.Length == 0 || !File.Exists(server)))
            throw new SourceException("Certificato server mancante: acquisirlo dal server oppure importarlo.");
        try
        {
            var config = await Configuration(Cfg, security == "None" ? null : Text("_server_cert"));
            var endpoints = await Endpoints(config, url, ct);
            var wanted = Policies.TryGetValue(security, out var policy) ? policy : throw new SourceException("Security Policy non supportata");
            var mode2 = mode switch { "Sign" => MessageSecurityMode.Sign, "SignAndEncrypt" => MessageSecurityMode.SignAndEncrypt, _ => MessageSecurityMode.None };
            var description = endpoints.Where(e => e.SecurityPolicyUri == wanted && e.SecurityMode == mode2).OrderByDescending(e => e.SecurityLevel).FirstOrDefault()
                ?? throw new SourceException("Endpoint richiesto non esposto dal server: " + security + " / " + mode);
            // The configured address is used for the socket (servers may advertise unreachable host names).
            description.EndpointUrl = url;
            var endpoint = new ConfiguredEndpoint(null, description, EndpointConfiguration.Create(config));
            IUserIdentity identity = Text("username").Length > 0 ? new UserIdentity(Text("username"), System.Text.Encoding.UTF8.GetBytes(Text("password"))) : new UserIdentity(new AnonymousIdentityToken());
            session = await DefaultSessionFactory.Instance.CreateAsync(config, endpoint, false, false, "Space Fieldbus Analyzer", 3600000, identity, null, ct);
            session.KeepAlive += (s, e) =>
            {
                if (ServiceResult.IsBad(e.Status)) keepAliveError = "OPC UA: collegamento interrotto (" + e.Status + ")";
            };
            Info["endpoint"] = url; Info["security"] = security; Info["security_mode"] = mode;
        }
        catch (ServiceResultException exc)
        {
            await DisconnectAsync();
            throw new SourceException("OPC UA: " + exc.Message);
        }
    }

    public override async Task DisconnectAsync()
    {
        var s = session;
        session = null;
        var sub = subscription;
        subscription = null;
        if (s == null) return;
        // CloseSession deletes the session's subscriptions on the server: no separate round trip.
        try { await s.CloseAsync(2000, true, CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(3)); } catch (Exception) { }
        try { s.Dispose(); } catch (Exception) { }
    }

    ISession Active => session ?? throw new SourceException("Client non connesso");

    public override async Task ReadStateAsync(CancellationToken ct)
    {
        if (keepAliveError.Length > 0) throw new SourceException(keepAliveError);
        var result = await Active.ReadAsync(null, 0, TimestampsToReturn.Source, new ReadValueIdCollection { new() { NodeId = new NodeId(ServerStateNode), AttributeId = Attributes.Value } }, ct);
        if (result.Results.Count != 1 || StatusCode.IsBad(result.Results[0].StatusCode))
            throw new SourceException("OPC UA: stato server non leggibile (" + (result.Results.Count == 1 ? OpcUaFormat.StatusName(result.Results[0].StatusCode) : "nessun risultato") + ")");
    }

    async Task<List<DataValue>> Read(IReadOnlyList<string> ids, CancellationToken ct)
    {
        var nodes = new ReadValueIdCollection(ids.Select(n => new ReadValueId { NodeId = OpcUaFormat.ParseNodeId(n), AttributeId = Attributes.Value }));
        var response = await Active.ReadAsync(null, 0, TimestampsToReturn.Source, nodes, ct);
        if (response.Results.Count != ids.Count) throw new SourceException("Il server ha restituito un numero di valori inatteso");
        return response.Results.ToList();
    }

    Sample Row(string nid, DataValue dv) => OpcUaFormat.ValueRow(nid, dv, Active.MessageContext);

    /// <summary>
    /// read_selected_once: batch Read with per-node fallback. A node that succeeds alone after
    /// failing in the batch stays in individual mode for the rest of the connection.
    /// </summary>
    public async Task<List<Sample>> ReadSelected(IReadOnlyList<string> ids, bool retryIndividual, CancellationToken ct)
    {
        var rows = new Dictionary<string, Sample>();
        var bulk = ids.Where(n => !individualNodes.Contains(n)).ToList();
        if (bulk.Count > 0)
        {
            var values = await Read(bulk, ct);
            for (var i = 0; i < bulk.Count; i++) rows[bulk[i]] = Row(bulk[i], values[i]);
        }
        foreach (var nid in ids.Where(individualNodes.Contains)) rows[nid] = Row(nid, (await Read([nid], ct))[0]);
        if (retryIndividual)
        {
            foreach (var nid in bulk.Where(n => !rows[n].Good || rows[n].DataType == "Null").ToList())
            {
                try
                {
                    var candidate = Row(nid, (await Read([nid], ct))[0]);
                    var current = rows[nid];
                    if (candidate.Good || (current.Value == null && candidate.Value != null)) rows[nid] = candidate;
                    if (candidate.Good && !current.Good) individualNodes.Add(nid);
                }
                catch (Exception exc) when (exc is ServiceResultException or SourceException) { }
            }
        }
        return ids.Select(n => rows.TryGetValue(n, out var r) ? r : OpcUaFormat.Missing(n, "Nessun DataValue restituito")).ToList();
    }

    public override Task<List<Sample>> ReadValuesAsync(IReadOnlyList<string> ids, CancellationToken ct) => ReadSelected(ids, true, ct);

    async Task StopSubscription()
    {
        var sub = subscription;
        subscription = null;
        lock (gate) { subscriptionKey = null; subscriptionRows = new(); subscriptionBad = new(); latest.Clear(); }
        if (sub != null)
        {
            try { await sub.DeleteAsync(true).WaitAsync(TimeSpan.FromSeconds(3)); } catch (Exception) { }
            try { Active.RemoveSubscription(sub); } catch (Exception) { }
        }
    }

    /// <summary>MonitoredItems at the requested sampling interval (UAExpert-like); false when the server rejects all.</summary>
    async Task<bool> RebuildSubscription(SourceSession session, IReadOnlyList<string> ids, Selection selection, CancellationToken ct)
    {
        await StopSubscription();
        if (ids.Count == 0) return false;
        var queue = (uint)Math.Max(2, Math.Min(65536, int.Parse(Environment.GetEnvironmentVariable("SPACE_UA_QUEUE") ?? "256")));
        var sub = new Subscription(Active.DefaultSubscription)
        {
            PublishingInterval = selection.Period, KeepAliveCount = 10, LifetimeCount = 10000, MaxNotificationsPerPublish = 10000,
            PublishingEnabled = true, Priority = 0, TimestampsToReturn = TimestampsToReturn.Both, DisplayName = "Space Fieldbus Analyzer",
        };
        var token = session.Token;
        var generation = selection.Generation;
        foreach (var nid in ids)
        {
            var item = new MonitoredItem(sub.DefaultItem)
            {
                StartNodeId = OpcUaFormat.ParseNodeId(nid), AttributeId = Attributes.Value, SamplingInterval = selection.Period,
                QueueSize = queue, DiscardOldest = true, MonitoringMode = MonitoringMode.Reporting, Handle = nid,
            };
            item.Notification += (monitored, e) =>
            {
                if (e.NotificationValue is not MonitoredItemNotification notification) return;
                var row = Row((string)monitored.Handle, notification.Value);
                row.AcquisitionOrigin = "notification";
                row.ReceivedMonoNs = Clock.MonoNs(); row.ReceivedNs = Clock.UtcNs();
                var received = row.ReceivedNs / 1e6;
                lock (gate) latest[row.NodeId] = (row, received);
                if (((row.StatusCode ?? 0) & 0x80) != 0) { overflow = "OPC UA: overflow MonitoredItem"; Fatal(session, overflow); }
                // A selection change invalidates display callbacks, never queued original rows.
                session.Accept(token, received, [row], selection.Period, generation);
            };
            sub.AddItem(item);
        }
        Active.AddSubscription(sub);
        try
        {
            await sub.CreateAsync(ct);
            var bad = new Dictionary<string, string>();
            foreach (var item in sub.MonitoredItems)
                if (ServiceResult.IsBad(item.Status.Error)) bad[(string)item.Handle] = OpcUaFormat.StatusName(item.Status.Error.StatusCode);
            if (bad.Count == ids.Count)
            {
                try { await sub.DeleteAsync(true); } catch (Exception) { }
                Active.RemoveSubscription(sub);
                lock (gate) subscriptionBad = bad;
                session.Mode = "Read polling (subscription rifiutata)";
                return false;
            }
            subscription = sub;
            lock (gate) { subscriptionBad = bad; subscriptionKey = (ids.ToArray(), selection.Generation?.Revision, selection.Period); }
            session.Mode = bad.Count == 0 ? "OPC UA Subscription" : "Subscription + Read fallback";
            Info["read_mode"] = session.Mode;
            return true;
        }
        catch
        {
            try { await sub.DeleteAsync(true); } catch (Exception) { }
            try { Active.RemoveSubscription(sub); } catch (Exception) { }
            throw;
        }
    }

    static void Fatal(SourceSession session, string message) => session.Fatal(message);

    public async Task RunAsync(SourceSession session, CancellationToken ct)
    {
        double? lastStart = null;
        var deadline = SourceSession.Seconds();
        double lastBadProbe = 0, lastHeartbeat = 0, lastSubAttempt = 0;
        (string[], long?, int)? lastSignature = null;
        using var timer = HighResolutionTimer.Begin();
        while (!ct.IsCancellationRequested)
        {
            if (keepAliveError.Length > 0) throw new SourceException(keepAliveError);
            var start = SourceSession.Seconds();
            var selection = session.Selection();
            var ids = selection.Ids;
            var interval = ids.Count > 0 ? selection.Period / 1000.0 : .5;
            if (ids.Count > 0)
            {
                // Recreate MonitoredItems after selection or period changes; failures retry every 2 s.
                var signature = (ids.ToArray(), selection.Generation?.Revision, selection.Period);
                (string[] ids, long? revision, int period)? key;
                lock (gate) key = subscriptionKey;
                var need = subscription == null || key is not { } k || !k.ids.SequenceEqual(signature.Item1) || k.revision != signature.Item2 || k.period != signature.Item3;
                var same = lastSignature is { } ls && ls.Item1.SequenceEqual(signature.Item1) && ls.Item2 == signature.Item2 && ls.Item3 == signature.Item3;
                if (need && (!same || start - lastSubAttempt >= 2))
                {
                    lastSignature = signature; lastSubAttempt = start;
                    try { await RebuildSubscription(session, ids, selection, ct); }
                    catch (Exception exc) when (exc is ServiceResultException or SourceException)
                    {
                        session.Mode = "Read polling (fallback)";
                        session.Error = "Subscription non disponibile: " + exc.Message;
                    }
                }
                List<Sample> rows;
                if (subscription != null)
                {
                    Dictionary<string, (Sample row, double received)> cache;
                    Dictionary<string, string> bad;
                    lock (gate) { cache = ids.Where(latest.ContainsKey).ToDictionary(n => n, n => latest[n]); bad = new(subscriptionBad); }
                    var missing = ids.Where(n => !cache.ContainsKey(n)).ToList();
                    var badCached = ids.Where(n => cache.ContainsKey(n) && !cache[n].row.Good).ToList();
                    // Seed missing items immediately; afterwards retry bad-quality nodes at 4 Hz.
                    var probe = missing.Concat(start - lastBadProbe >= .25 ? badCached.Concat(bad.Keys) : []).Distinct().ToList();
                    var direct = new Dictionary<string, Sample>();
                    if (probe.Count > 0)
                    {
                        foreach (var row in await ReadSelected(probe, true, ct)) direct[row.NodeId] = row;
                        lastBadProbe = start;
                    }
                    var now = Clock.UtcNs() / 1e6;
                    rows = ids.Select(n =>
                    {
                        if (direct.TryGetValue(n, out var d) && (!cache.TryGetValue(n, out var c) || d.Good || !c.row.Good)) return Snapshot(d, now);
                        if (cache.TryGetValue(n, out var entry)) return Snapshot(entry.row, entry.received);
                        return OpcUaFormat.Missing(n);
                    }).ToList();
                }
                else
                {
                    var retry = start - lastBadProbe >= .25;
                    rows = await ReadSelected(ids, retry, ct);
                    if (retry) lastBadProbe = start;
                    session.Mode = individualNodes.Count > 0 ? "Read polling + individual fallback" : "Read polling (fallback)";
                }
                // An explicit heartbeat catches a lost transport even with constant values.
                if (start - lastHeartbeat >= 1) { await ReadStateAsync(ct); lastHeartbeat = start; }
                session.Accept(session.Token, Clock.UtcNs() / 1e6, rows, selection.Period, selection.Generation);
                session.ObserveCycle(start, lastStart);
                lastStart = start;
            }
            else
            {
                if (subscription != null) await StopSubscription();
                await ReadStateAsync(ct);
                lastStart = null;
            }
            Info["read_mode"] = session.Mode;
            deadline += interval;
            if (deadline < SourceSession.Seconds() - interval) deadline = SourceSession.Seconds() + interval;
            await HighResolutionTimer.DelayUntil(deadline, ct);
        }
    }

    /// <summary>The periodic snapshot of a notification or a direct Read for the trend and the format.</summary>
    static Sample Snapshot(Sample row, double acquiredMs)
    {
        var copy = row.Copy();
        copy.AcquisitionOrigin = "subscription_snapshot";
        copy.AcquisitionTimestampMs = acquiredMs;
        copy.ReceivedNs = 0; copy.ReceivedMonoNs = 0;
        copy.ConnectionId = null; copy.SampleSequence = null; copy.SourceEpoch = null; copy.LocalTs = null;
        return copy;
    }

    /// <summary>Hierarchical references of a node, following continuation points (and releasing them).</summary>
    public async Task<List<ReferenceDescription>> References(string nodeId, CancellationToken ct)
    {
        var description = new BrowseDescription
        {
            NodeId = OpcUaFormat.ParseNodeId(nodeId), BrowseDirection = BrowseDirection.Forward, ReferenceTypeId = ReferenceTypeIds.HierarchicalReferences,
            IncludeSubtypes = true, NodeClassMask = 0, ResultMask = (uint)BrowseResultMask.All,
        };
        var result = new List<ReferenceDescription>();
        var response = await Active.BrowseAsync(null, null, 250, new BrowseDescriptionCollection { description }, ct);
        var item = response.Results[0];
        byte[]? continuation = null;
        try
        {
            while (true)
            {
                if (StatusCode.IsBad(item.StatusCode)) throw new ServiceResultException(item.StatusCode);
                result.AddRange(item.References);
                continuation = item.ContinuationPoint;
                if (continuation == null || continuation.Length == 0) { continuation = null; break; }
                var next = await Active.BrowseNextAsync(null, false, new ByteStringCollection { continuation }, ct);
                item = next.Results[0];
            }
        }
        finally
        {
            if (continuation != null)
                try { await Active.BrowseNextAsync(null, true, new ByteStringCollection { continuation }, CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(2)); } catch (Exception) { }
        }
        return result;
    }

    /// <summary>app.reference_item.</summary>
    public static Dictionary<string, object?> ReferenceItem(ReferenceDescription reference, string parentPath = "")
    {
        var id = new NodeId(reference.NodeId.Identifier, reference.NodeId.NamespaceIndex);
        var nid = OpcUaFormat.NodeIdText(id);
        var name = reference.DisplayName?.Text is { Length: > 0 } text ? text : reference.BrowseName?.Name is { Length: > 0 } browse ? browse : nid;
        var remote = reference.NodeId.ServerIndex != 0 || !string.IsNullOrEmpty(reference.NodeId.NamespaceUri);
        return new()
        {
            ["nodeid"] = nid, ["display_name"] = name, ["browse_name"] = $"{reference.BrowseName?.NamespaceIndex ?? 0}:{reference.BrowseName?.Name}",
            ["node_class"] = reference.NodeClass.ToString(), ["has_children"] = !remote, ["remote"] = remote,
            ["path"] = parentPath.Length > 0 ? parentPath + "/" + name : name,
        };
    }

    public override async Task<object?> InspectAsync(string nodeId, CancellationToken ct)
    {
        var id = OpcUaFormat.ParseNodeId(nodeId);
        var nodes = new ReadValueIdCollection(new[] { Attributes.NodeClass, Attributes.DisplayName, Attributes.BrowseName }.Select(a => new ReadValueId { NodeId = id, AttributeId = a }));
        var response = await Active.ReadAsync(null, 0, TimestampsToReturn.Neither, nodes, ct);
        foreach (var r in response.Results) if (StatusCode.IsBad(r.StatusCode)) throw new SourceException(OpcUaFormat.StatusName(r.StatusCode));
        var nodeClass = (NodeClass)(int)response.Results[0].Value;
        var display = response.Results[1].Value as LocalizedText;
        var browse = response.Results[2].Value as QualifiedName;
        return new Dictionary<string, object?>
        {
            ["nodeid"] = nodeId, ["node_class"] = nodeClass.ToString(),
            ["display_name"] = display?.Text is { Length: > 0 } t ? t : browse?.Name is { Length: > 0 } b ? b : nodeId,
            ["browse_name"] = $"{browse?.NamespaceIndex ?? 0}:{browse?.Name}", ["has_children"] = true,
        };
    }

    public override async Task<object?> BrowseAsync(string nodeId, CancellationToken ct)
    {
        var seen = new HashSet<string>();
        var result = new List<object?>();
        foreach (var reference in await References(nodeId, ct))
        {
            var row = ReferenceItem(reference);
            if (seen.Add((string)row["nodeid"]!)) result.Add(row);
        }
        return result;
    }
}
