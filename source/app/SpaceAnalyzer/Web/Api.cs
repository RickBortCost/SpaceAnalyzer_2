using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SpaceAnalyzer.App.Analysis;
using SpaceAnalyzer.App.Calculations;
using SpaceAnalyzer.App.Config;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Live;
using SpaceAnalyzer.App.Recordings;
using SpaceAnalyzer.App.Runtime;
using SpaceAnalyzer.App.Ssh;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Drivers.OpcUa;
using SpaceAnalyzer.Engine.Drivers.S7;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.App.Web;

/// <summary>A file sent as attachment (send_file).</summary>
public sealed record FileDownload(string Path, string Name, string ContentType = "application/octet-stream");

/// <summary>The HTTP API of app.py, route by route.</summary>
public sealed class Api(AppRuntime rt, SshService ssh, string token, Action shutdown)
{
    readonly Queue<DateTime> frontendLog = new();
    AppPaths Paths => rt.Paths;

    delegate Task<object?> Handler(HttpContext ctx);

    void Get(WebApplication app, string pattern, Handler handler) => app.MapGet(pattern, ctx => Run(ctx, handler));
    void Post(WebApplication app, string pattern, Handler handler) => app.MapPost(pattern, ctx => Run(ctx, handler));

    static async Task Run(HttpContext ctx, Handler handler)
    {
        var result = await handler(ctx);
        switch (result)
        {
            case FileDownload file:
                ctx.Response.ContentType = file.ContentType;
                ctx.Response.Headers.ContentDisposition = "attachment; filename=" + file.Name;
                await ctx.Response.SendFileAsync(file.Path);
                break;
            case SamplePayload samples:
                ctx.Response.ContentType = SamplePayload.ContentType;
                await samples.WriteAsync(ctx.Response.Body, ctx.RequestAborted);
                break;
            case Action<Utf8JsonWriter> writer:
                await Http.WriteWith(ctx, writer);
                break;
            default:
                await Http.Write(ctx, result);
                break;
        }
    }

    static Dictionary<string, object?> Ok(params (string, object?)[] items) => Http.Ok(items);

    static object? Plain(JsonNode? node) => Py.Plain(node);

    static JsonElement Element(JsonObject cfg)
    {
        using var doc = JsonDocument.Parse(cfg.ToJsonString());
        return doc.RootElement.Clone();
    }

    public void Map(WebApplication app)
    {
        app.MapGet("/", ctx =>
        {
            var code = rt.Config.Load()["ui_language"].Str() ?? "it";
            var html = Templates.Render(Paths.Templates, "index.html", new Dictionary<string, string>
            {
                ["version"] = Defaults.Version, ["token"] = token, ["ui_language"] = Defaults.Languages.Contains(code) ? code : "it",
            });
            ctx.Response.ContentType = "text/html; charset=utf-8";
            return ctx.Response.WriteAsync(html);
        });
        MapConfig(app);
        MapSources(app);
        MapRecording(app);
        MapRecordings(app);
        MapCalculations(app);
        MapAnalysis(app);
        MapSsh(app);
        Get(app, "/api/logs", _ =>
        {
            var path = Paths.LogFile;
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            stream.Seek(Math.Max(0, stream.Length - 65536), SeekOrigin.Begin);
            var bytes = new byte[stream.Length - stream.Position];
            stream.ReadExactly(bytes);
            return Task.FromResult<object?>(Ok(("path", path), ("tail", new UTF8Encoding(false, false).GetString(bytes))));
        });
        Post(app, "/api/log/frontend", async ctx =>
        {
            var now = DateTime.UtcNow;
            lock (frontendLog)
            {
                if (frontendLog.Count == 100 && (now - frontendLog.Peek()).TotalSeconds < 60) return Ok(("throttled", true));
                frontendLog.Enqueue(now);
                while (frontendLog.Count > 100) frontendLog.Dequeue();
            }
            var data = await Http.Json(ctx, silent: true) as JsonObject ?? new JsonObject();
            var text = data.ContainsKey("message") ? RowValues.Str(Plain(data["message"])) : "Frontend error";
            text = (text.Length > 1200 ? text[..1200] : text).Replace('\n', ' ').Replace('\r', ' ');
            text = Regex.Replace(text, @"(?i)(password|token|authorization|secret)([\s:=]+)\S+", "$1=[redacted]");
            Log.Error("Frontend: " + text);
            return Ok();
        });
        if (Environment.GetEnvironmentVariable("SPACE_TEST_HOOKS") == "1")
        {
            rt.Recording.TestFinalizerGate = new ManualResetEventSlim(false);
            Post(app, "/test/finalizers/release", _ => { rt.Recording.TestFinalizerGate.Set(); return Task.FromResult<object?>(Ok()); });
            Post(app, "/test/fatal", _ => { new Thread(() => throw new InvalidOperationException("Errore fatale di prova")) { Name = "test-fatal" }.Start(); return Task.FromResult<object?>(Ok()); });
        }
        Post(app, "/api/shutdown", _ =>
        {
            ThreadPool.QueueUserWorkItem(_ => shutdown());
            return Task.FromResult<object?>(Ok());
        });
    }

    // ---- Configuration, settings, certificates --------------------------------------------

    Dictionary<string, object?> SaveFullSettings(JsonObject cfg, JsonArray nodes, JsonNode? serverData)
    {
        Directory.CreateDirectory(Paths.SettingsDir);
        Directory.CreateDirectory(Paths.SettingsCertDir);
        cfg = (JsonObject)cfg.DeepClone();
        cfg["ui_language"] = rt.Config.Load()["ui_language"]?.DeepClone();
        cfg["security"] = Defaults.NormalizePolicy(cfg["security"].Str() ?? "None");
        cfg["s7_symbolic_password"] = "";
        cfg["mqtt_password"] = "";
        cfg["sample_ms"] = Math.Max(cfg["source"].Str() == "ads" ? 1 : 10, (long)(cfg["sample_ms"].Truthy() ? cfg["sample_ms"].Num() ?? 500 : 500));
        rt.Config.Save(cfg);
        rt.Certs.EnsureClient(cfg["application_uri"].Str());
        var copied = new Dictionary<string, object?>();
        foreach (var (label, src, name) in new[] { ("client_cert", Paths.CertFile, "client.der"), ("client_key", Paths.KeyFile, "client-key.pem"), ("server_cert", Paths.ServerCertFile, "server.der") })
        {
            if (!File.Exists(src)) continue;
            var dst = Path.Combine(Paths.SettingsCertDir, name);
            File.Copy(src, dst, true);
            copied[label] = dst;
        }
        var payload = new Dictionary<string, object?>
        {
            ["version"] = Defaults.Version, ["saved_at"] = Py.NowIso(), ["config"] = cfg, ["selected_nodes"] = Validation.Nodes(nodes),
            ["server_data"] = serverData is JsonObject ? serverData.DeepClone() : new JsonObject(), ["calculations"] = rt.Formulas.Snapshot(),
            ["certificates"] = new Dictionary<string, object?> { ["copies"] = copied, ["client"] = Certificates.Info(Paths.CertFile), ["server"] = Certificates.Info(Paths.ServerCertFile) },
        };
        Py.AtomicPlain(Paths.SettingsFile, payload);
        return payload;
    }

    void MapConfig(WebApplication app)
    {
        Post(app, "/api/ui/language", async ctx =>
        {
            var data = await Http.Json(ctx);
            var code = data.Get("ui_language");
            if (data is not JsonObject || !code.IsString() || !Defaults.Languages.Contains(code.Str())) throw new UserError("Lingua interfaccia non supportata");
            lock (rt.Config.FileLock)
            {
                var cfg = rt.Config.Load();
                cfg["ui_language"] = code.Str();
                rt.Config.Save(cfg, uiLanguageUpdate: true);
                foreach (var path in new[] { Paths.WorkspaceFile, Paths.SettingsFile })
                {
                    if (!File.Exists(path) || Py.ReadObject(path) is not JsonObject profile || profile["config"] is not JsonObject c) continue;
                    c["ui_language"] = code.Str();
                    Py.AtomicJson(path, profile);
                }
            }
            return Ok(("ui_language", code.Str()));
        });
        Get(app, "/api/config", _ =>
        {
            var cfg = rt.Config.Load();
            var info = rt.Certs.ClientInfo();
            var server = Certificates.Info(Paths.ServerCertFile);
            var saved = rt.Config.LoadSettings();
            var result = (Dictionary<string, object?>)Plain(cfg)!;
            result["client_cert_abs"] = info["path"].Str();
            result["client_key_abs"] = info["key_path"].Str();
            result["server_cert_abs"] = server["path"].Str() ?? Paths.ServerCertFile;
            result["server_cert_present"] = server["present"].Bool() ?? false;
            result["server_cert_sha256"] = server["thumbprint_sha256"].Str() ?? "";
            result["version"] = Defaults.Version;
            result["max_selected"] = (long)Defaults.MaxSelected;
            result["saved_settings_present"] = saved.Count > 0;
            result["saved_at"] = saved.ContainsKey("saved_at") ? Plain(saved["saved_at"]) : "";
            result["selected_nodes"] = saved.ContainsKey("selected_nodes") ? Plain(saved["selected_nodes"]) : new List<object?>();
            result["saved_server_data"] = saved.ContainsKey("server_data") ? Plain(saved["server_data"]) : new Dictionary<string, object?>();
            result["data_dir"] = Paths.DataDir;
            result["recordings_dir"] = Paths.RecordingsDir;
            result["workspace"] = Plain(rt.Config.LoadWorkspace());
            result["calculations"] = rt.Formulas.Snapshot();
            var rec = rt.Recording.Status();
            var locked = rec.TryGetValue("selection_locked", out var l) ? l is true : rec["busy"] is true;
            result["recording_busy"] = locked;
            if (locked)
                result["selected_nodes"] = ((List<object?>?)rec.GetValueOrDefault("nodes") ?? new()).Where(n => n is Dictionary<string, object?> d && !((d.GetValueOrDefault("nodeid") as string) ?? "").StartsWith("calc:", StringComparison.Ordinal)).ToList();
            return Task.FromResult<object?>(result);
        });
        Post(app, "/api/config", async ctx =>
        {
            var cfg = rt.Config.Validated(await Http.Json(ctx));
            rt.ConnectionGuard(cfg);
            if (rt.WantConnected && cfg["application_uri"].Str() != rt.ActiveConfig?["application_uri"].Str()) throw new UserError("Disconnettere prima di cambiare Application URI");
            rt.Config.Save(cfg);
            rt.ApplyLiveConfig(cfg);
            return Ok();
        });
        Post(app, "/api/settings/save", async ctx =>
        {
            var data = await Http.Json(ctx);
            var cfg = rt.Config.Validated(data.Get("config") ?? data);
            rt.ConnectionGuard(cfg);
            var nodes = Validation.Nodes(data.Get("selected_nodes") ?? new JsonArray());
            await rt.CommandLock.WaitAsync();
            Dictionary<string, object?> profile;
            try
            {
                if (rt.Recording.Acquiring && !nodes.Select(n => n.Get("nodeid").Str()!).ToHashSet().SetEquals(rt.Selected)) throw new UserError("Premere Stop prima di cambiare i segnali della registrazione");
                lock (rt.Config.FileLock)
                {
                    if (rt.WantConnected && cfg["application_uri"].Str() != rt.ActiveConfig?["application_uri"].Str()) throw new UserError("Disconnettere prima di cambiare Application URI e certificato client");
                    profile = SaveFullSettings(cfg, nodes, data.Get("server_data"));
                }
                rt.ApplyLiveConfig(cfg);
                rt.ApplySelectionCore(nodes);
            }
            finally { rt.CommandLock.Release(); }
            return Ok(("saved_at", profile["saved_at"]), ("settings_file", Paths.SettingsFile));
        });
        Get(app, "/api/certificate", _ => Task.FromResult<object?>(rt.Certs.ClientInfo()));
        Post(app, "/api/certificate/regenerate", async ctx =>
        {
            if (rt.WantConnected) throw new UserError("Disconnettere il client prima di rigenerare il certificato");
            var data = await Http.Json(ctx) ?? new JsonObject();
            (string uri, bool created) result;
            lock (rt.Config.FileLock) result = rt.Certs.EnsureClient(data.Get("application_uri").Str(), force: true);
            return Ok(("created", result.created), ("application_uri", result.uri), ("info", rt.Certs.ClientInfo()));
        });
        Get(app, "/api/certificate/download", _ => Task.FromResult<object?>(new FileDownload(Paths.CertFile, "client.der", "application/x-x509-ca-cert")));
        Get(app, "/api/server-certificate", _ => Task.FromResult<object?>(Certificates.Info(Paths.ServerCertFile)));
        Post(app, "/api/server-certificate", async ctx =>
        {
            if (rt.WantConnected) throw new UserError("Disconnettere prima di sostituire il certificato server");
            var form = ctx.Request.HasFormContentType ? await ctx.Request.ReadFormAsync() : null;
            var file = form?.Files.GetFile("file") ?? throw new UserError("Selezionare un certificato server");
            using var buffer = new MemoryStream();
            await file.CopyToAsync(buffer);
            X509Certificate2 cert;
            try { cert = Certificates.LoadAny(buffer.ToArray()); }
            catch (UserError) { throw; }
            catch (Exception exc) { throw new UserError(exc.Message); }
            using (cert)
            {
                Directory.CreateDirectory(Paths.CertDir);
                lock (rt.Config.FileLock) File.WriteAllBytes(Paths.ServerCertFile, cert.RawData);
            }
            return Ok(("info", Certificates.Info(Paths.ServerCertFile)));
        });
        Post(app, "/api/endpoints", async ctx =>
        {
            var cfg = rt.Config.Validated(await Http.Json(ctx));
            var (endpoints, _) = await Discover(cfg["endpoint"].Str()!);
            return Ok(("endpoints", endpoints));
        });
        Post(app, "/api/server-certificate/fetch", async ctx =>
        {
            if (rt.WantConnected) throw new UserError("Disconnettere prima di sostituire il certificato server");
            var cfg = rt.Config.Validated(await Http.Json(ctx));
            var security = Defaults.NormalizePolicy(cfg["security"].Str()!);
            if (security == "None") throw new UserError("Selezionare una policy sicura per acquisire il certificato server");
            var mode = cfg["security_mode"].Str();
            var (_, descriptions) = await Discover(cfg["endpoint"].Str()!);
            var matches = descriptions.Where(e => PolicyFromUri(e.SecurityPolicyUri) == security && e.SecurityMode.ToString() == mode).ToList();
            if (matches.Count == 0)
            {
                var available = descriptions.Select(e => PolicyFromUri(e.SecurityPolicyUri) + " / " + e.SecurityMode).Distinct().OrderBy(x => x, StringComparer.Ordinal);
                throw new SourceException("Endpoint richiesto non esposto dal server. Disponibili: " + string.Join(", ", available));
            }
            var ep = matches.OrderByDescending(e => e.SecurityLevel).First();
            if (ep.ServerCertificate is not { Length: > 0 }) throw new SourceException("L'endpoint selezionato non espone un certificato server.");
            Directory.CreateDirectory(Paths.CertDir);
            using (var cert = X509CertificateLoader.LoadCertificate(ep.ServerCertificate)) File.WriteAllBytes(Paths.ServerCertFile, cert.RawData);
            return Ok(("info", Certificates.Info(Paths.ServerCertFile)));
        });
        Post(app, "/api/workspace", async ctx =>
        {
            if (ctx.Request.ContentLength > 32L * 1024 * 1024) throw new UserError("Workspace troppo grande (massimo 32 MB)");
            var data = await Http.Json(ctx) as JsonObject;
            if (data == null || data["schema"].Num() != 1 || data["schema"].IsBool()) throw new UserError("Workspace non valido");
            var cfg = rt.Config.Validated(data["config"] ?? new JsonObject());
            rt.ConnectionGuard(cfg);
            cfg["s7_symbolic_password"] = "";
            cfg["mqtt_password"] = "";
            data["config"] = cfg;
            data["selected_nodes"] = Validation.Nodes(data["selected_nodes"] ?? new JsonArray());
            foreach (var scope in new[] { "live", "history" })
            {
                var charts = data[scope].Get("charts") ?? new JsonArray();
                if (charts is not JsonArray list || list.Count > 8) throw new UserError("Massimo 8 grafici per pagina");
            }
            data["saved_at"] = Py.NowIso();
            data["calculations"] = rt.Formulas.Snapshot();
            lock (rt.Config.FileLock)
            {
                cfg["ui_language"] = rt.Config.Load()["ui_language"]?.DeepClone();
                Py.AtomicJson(Paths.WorkspaceFile, data);
            }
            return Ok(("saved_at", data["saved_at"].Str()));
        });
    }

    static string PolicyFromUri(string? uri)
    {
        if (string.IsNullOrEmpty(uri)) return "None";
        return uri.Contains('#') ? uri[(uri.LastIndexOf('#') + 1)..] : uri[(uri.LastIndexOf('/') + 1)..];
    }

    static async Task<(List<object?> rows, List<Opc.Ua.EndpointDescription> raw)> Discover(string url)
    {
        using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(18));
        try
        {
            var config = await OpcUaDriver.Configuration(JsonDocument.Parse("{}").RootElement, null);
            var endpoints = await OpcUaDriver.Endpoints(config, url, cancel.Token);
            var rows = new List<object?>();
            foreach (var ep in endpoints)
            {
                var thumb = "";
                if (ep.ServerCertificate is { Length: > 0 })
                {
                    try
                    {
                        using var cert = X509CertificateLoader.LoadCertificate(ep.ServerCertificate);
                        thumb = string.Join(':', SHA256.HashData(cert.RawData).Select(b => b.ToString("X2")));
                    }
                    catch (Exception) { }
                }
                rows.Add(new Dictionary<string, object?>
                {
                    ["endpoint_url"] = ep.EndpointUrl, ["security"] = PolicyFromUri(ep.SecurityPolicyUri), ["security_mode"] = ep.SecurityMode.ToString(),
                    ["security_level"] = (long)ep.SecurityLevel, ["server_application_uri"] = ep.Server?.ApplicationUri ?? "",
                    ["server_name"] = ep.Server?.ApplicationName?.Text ?? "", ["has_server_certificate"] = ep.ServerCertificate is { Length: > 0 },
                    ["server_certificate_sha256"] = thumb,
                });
            }
            return (rows, endpoints.ToList());
        }
        catch (OperationCanceledException) { throw new SourceException("Operazione scaduta: controllare endpoint, rete e server."); }
        catch (Opc.Ua.ServiceResultException exc) { throw new SourceException(exc.Message); }
    }

    // ---- Sources, catalog, selection, values ---------------------------------------------------

    static Dictionary<string, object?> Info(SourceDriver driver) => new(driver.Info);

    static object? SamplePlain(Sample s) => Py.Plain(JsonNode.Parse(Engine.Recording.SacFormat.Json(s.WriteDisplay)));

    async Task<(Dictionary<string, object?> info, object? probe)> TestDriver(string protocol, JsonObject cfg, Func<SourceDriver, CancellationToken, Task<object?>>? probe, double seconds, bool catalog = false)
    {
        await rt.CommandLock.WaitAsync();
        try
        {
            if (rt.WantConnected) throw new UserError(protocol switch
            {
                "mqtt" => "Disconnettere e fermare Record prima del test MQTT",
                "ads" => "Disconnettere prima di eseguire il test ADS",
                _ => "Disconnettere prima del test S7",
            });
            if (protocol == "mqtt" && rt.Recording.Acquiring) throw new UserError("Disconnettere e fermare Record prima del test MQTT");
            using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(seconds));
            var element = Element(cfg);
            await using var driver = DriverFactory.Create(protocol, element, catalog);
            try
            {
                await driver.ConnectAsync(cancel.Token);
                await driver.ReadStateAsync(cancel.Token);
                var result = probe == null ? null : await probe(driver, cancel.Token);
                return (Info(driver), result);
            }
            catch (OperationCanceledException) { throw new SourceException("Operazione scaduta: controllare endpoint, rete e server."); }
        }
        finally { rt.CommandLock.Release(); }
    }

    void MapSources(WebApplication app)
    {
        Post(app, "/api/mqtt/test", async ctx =>
        {
            var data = await Http.Json(ctx) as JsonObject ?? new JsonObject();
            data["source"] = "mqtt";
            var cfg = rt.Config.Validated(data);
            var (info, _) = await TestDriver("mqtt", cfg, null, 35);
            return Ok(("mqtt", info));
        });
        Post(app, "/api/ads/test", async ctx =>
        {
            var data = await Http.Json(ctx) as JsonObject ?? new JsonObject();
            data["source"] = "ads";
            var cfg = rt.Config.Validated(data);
            var (info, _) = await TestDriver("ads", cfg, null, 35, catalog: true);
            return Ok(("ads", info));
        });
        Post(app, "/api/s7/test", async ctx =>
        {
            var data = await Http.Json(ctx) as JsonObject ?? new JsonObject();
            data["source"] = "s7";
            var cfg = rt.Config.Validated(data);
            var mode = cfg["s7_mode"].Str();
            async Task<object?> Probe(SourceDriver driver, CancellationToken ct)
            {
                if (mode == "symbolic")
                {
                    var items = (await driver.ScanItemsAsync(cfg["s7_symbolic_root"].Str() ?? "s7:sym:/", ct) as List<object?>) ?? new();
                    var first = items.Cast<Dictionary<string, object?>>().Take(1).Select(i => (string)i["nodeid"]!).ToList();
                    return (await driver.ReadValuesAsync(first, ct)).Select(SamplePlain).ToList();
                }
                if (mode == "udp")
                {
                    var items = (await driver.ScanItemsAsync("s7:udp:/", ct) as List<object?>) ?? new();
                    var first = items.Cast<Dictionary<string, object?>>().Take(1).Select(i => (string)i["nodeid"]!).ToList();
                    var received = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                    driver.ConfigureStream(() => new Selection(first, (int)(cfg["sample_ms"].Num() ?? 500), null), (_, _, _, _) => { received.TrySetResult(true); return true; });
                    var wait = TimeSpan.FromSeconds(Math.Min(10, (cfg["s7_timeout_ms"].Num() ?? 3000) / 1000));
                    if (await Task.WhenAny(received.Task, Task.Delay(wait, ct)) != received.Task)
                        throw new UserError("Socket UDP aperto, ma nessun telegramma valido dal PLC. Verificare TUSEND, IP PC, firewall, modulo e lunghezza.");
                    driver.Flush(TimeSpan.FromSeconds(2));
                    driver.Info["diagnostics"] = driver.Diagnostics();
                    return (await driver.ReadValuesAsync(first, ct)).Select(SamplePlain).ToList();
                }
                var tags = cfg["s7_tags"] as JsonArray ?? new JsonArray();
                return tags.Count > 0 ? (await driver.ReadValuesAsync([tags[0].Get("nodeid").Str()!], ct)).Select(SamplePlain).ToList() : new List<object?>();
            }
            var (info, probe) = await TestDriver("s7", cfg, Probe, mode == "symbolic" ? 180 : 35);
            return Ok(("s7", info), ("probe", probe));
        });
        Post(app, "/api/s7/tags/validate", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            return Ok(("tags", Validation.NormalizeS7Tags(data.Get("tags"))));
        });
        Post(app, "/api/s7/udp/tags/validate", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var config = data.Get("config") as JsonObject ?? new JsonObject();
            config = (JsonObject)config.DeepClone();
            config["s7_udp_tags"] = data.Get("tags")?.DeepClone();
            var cfg = rt.Config.Validated(config);
            return Ok(("tags", Validation.NormalizeUdpTags(data.Get("tags"), (int)cfg["s7_udp_length"].Num()!.Value - 6)));
        });
        Post(app, "/api/s7/symbols/refresh", async _ =>
        {
            await rt.CommandLock.WaitAsync();
            try
            {
                if (rt.RequireConnection("s7") is not Engine.Drivers.S7.S7SymbolicDriver driver) throw new UserError("Connettere la modalità S7 simbolica");
                using var cancel = new CancellationTokenSource(TimeSpan.FromSeconds(180));
                return Ok(("symbols", (long)await driver.RefreshCatalogAsync(cancel.Token)));
            }
            finally { rt.CommandLock.Release(); }
        });
        Post(app, "/api/connect", async ctx =>
        {
            var cfg = rt.Config.Validated(await Http.Json(ctx));
            await rt.Connect(cfg);
            rt.Config.Save(cfg);
            return Ok(("connected", rt.Connected), ("sources", rt.Sources.Status()));
        });
        Post(app, "/api/disconnect", async _ =>
        {
            await rt.Disconnect();
            return Ok(("connected", false));
        });
        Post(app, "/api/sources/{protocol}/{action}", async ctx =>
        {
            await rt.SourceControl((string)ctx.Request.RouteValues["protocol"]!, (string)ctx.Request.RouteValues["action"]!);
            return Ok(("sources", rt.Sources.Status()));
        });
        Get(app, "/api/status", _ => Task.FromResult<object?>(new Dictionary<string, object?>
        {
            ["app"] = Defaults.AppName, ["version"] = Defaults.Version, ["state"] = rt.State, ["connected"] = rt.Connected, ["error"] = rt.LastError,
            ["sources"] = rt.SourceStatus(),
        }));
        Get(app, "/api/browse", async ctx =>
        {
            var nid = Validation.NodeIdText(JsonValue.Create(Http.Query(ctx, "nodeid", "i=85") is { Length: > 0 } q ? q : "i=85"));
            var nodes = await rt.Browse(nid);
            var offset = Math.Max(0, Http.QueryInt(ctx, "offset", 0));
            var count = Math.Max(1, Math.Min(1000, Http.QueryInt(ctx, "count", 250)));
            var end = Math.Min(nodes.Count, offset + count);
            return Ok(("nodes", nodes.Skip(offset).Take(Math.Max(0, end - offset)).ToList()), ("total", (long)nodes.Count), ("next_offset", end < nodes.Count ? (long)end : null));
        });
        Get(app, "/api/node", async ctx =>
        {
            var nid = Validation.NodeIdText(ctx.Request.Query.TryGetValue("nodeid", out var v) ? JsonValue.Create(v[0]) : null);
            return Ok(("node", await rt.Inspect(nid)));
        });
        Get(app, "/api/mqtt/catalog", async ctx =>
        {
            if (rt.RequireConnection("mqtt") is not Engine.Drivers.Mqtt.MqttDriver driver) throw new UserError("MQTT non connesso");
            var (revision, nodes) = driver.CatalogSnapshot(Http.Query(ctx, "revision", ""));
            if (nodes == null) return Ok(("revision", revision), ("unchanged", true));
            return Ok(("revision", revision), ("nodes", nodes), ("roots", await driver.BrowseAsync("mqtt:/", ctx.RequestAborted)));
        });
        Post(app, "/api/scan", async ctx =>
        {
            var cfg = rt.Config.Validated(await Http.Json(ctx) ?? new JsonObject());
            return Ok(("id", await rt.StartScan(cfg)));
        });
        Get(app, "/api/scan", ctx =>
        {
            var offset = Math.Max(0, Http.QueryInt(ctx, "offset", 0));
            var count = Math.Min(1000, Math.Max(1, Http.QueryInt(ctx, "count", 500)));
            var status = rt.ScanStatus(Http.Query(ctx, "id", ""), offset, count);
            var result = Ok();
            foreach (var (k, v) in status) result[k] = v;
            return Task.FromResult<object?>(result);
        });
        Post(app, "/api/scan/cancel", async _ => { await rt.StopScan(); return Ok(); });
        Post(app, "/api/select", async ctx =>
        {
            var data = await Http.Json(ctx);
            var nodes = Validation.Nodes(data.Get("nodeids") ?? new JsonArray());
            await rt.ApplySelection(nodes);
            return Ok(("selected", rt.Selected.ToList()));
        });
        Post(app, "/api/samples/clear", _ => { rt.ClearSamples(); return Task.FromResult<object?>(Ok()); });
        Get(app, "/api/values", ctx => Task.FromResult<object?>(rt.Values(Http.Query(ctx, "view", "all"), Http.Query(ctx, "samples", "1") != "0")));
        // Full-resolution Live: new samples since the client's cursors, and session originals from the Live archive.
        Post(app, "/api/live/samples", async ctx => rt.LiveSamples((await Http.Object(ctx, silent: true))["cursors"] as JsonObject ?? new JsonObject()));
        Post(app, "/api/live/history", async ctx => rt.LiveHistory((await Http.Object(ctx, silent: true))["before"] as JsonObject ?? new JsonObject()));
        Get(app, "/api/acquisition/health", _ => Task.FromResult<object?>(Ok(
            ("acquisition", rt.Acquisition.Status()), ("recording", rt.Recording.Status()), ("calculations", rt.Calculations.Status()),
            ("recovery", rt.Recovery.Status()), ("protocols", rt.Sources.Status()),
            ("events", new Dictionary<string, object?> { ["persistence_error"] = rt.Events.Error, ["overflow"] = rt.Events.Overflow }))));
        Get(app, "/api/acquisition/events", ctx => Task.FromResult<object?>(Ok(("events", rt.Events.History(ctx.Request.Query.TryGetValue("limit", out var l) ? l[0] : "100")))));
        Get(app, "/api/acquisition/captures", _ => Task.FromResult<object?>(Ok(("captures", rt.Recovery.Catalog()))));
        Post(app, "/api/acquisition/recover", _ => Task.FromResult<object?>(Ok(("started", rt.Recovery.Start()), ("recovery", rt.Recovery.Status()))));
    }

    // ---- Recording --------------------------------------------------------------------------

    void MapRecording(WebApplication app)
    {
        Get(app, "/api/record/status", _ => Task.FromResult<object?>(Ok(("recording", rt.Recording.Status()))));
        Post(app, "/api/record/start", async ctx =>
        {
            var data = await Http.Json(ctx);
            var format = data.Get("format");
            if (!format.IsString()) throw new UserError("Scegliere CSV, Binario Parquet, FDA DAS4 o RBF v3. La registrazione .dat (iba/PDA3) è stata rimossa nella 3.0; i file .dat restano apribili");
            if (data.Get("options") is { } options && options is not JsonObject && !options.IsNull()) throw new UserError("Opzioni registrazione non valide");
            return Ok(("recording", await rt.StartRecording(data)));
        });
        Post(app, "/api/record/stop", async ctx =>
        {
            var data = await Http.Json(ctx, silent: true);
            return Ok(("recording", await rt.StopRecording(data.Get("recording_id").Str())));
        });
    }

    RecordingLibrary Source(JsonNode? source)
    {
        var value = source.Truthy() ? RowValues.Str(Plain(source)).ToLowerInvariant() : "repository";
        return value switch
        {
            "repository" => rt.Library,
            "local" => rt.Imported,
            _ => throw new UserError("Origine registrazione non valida"),
        };
    }

    (double? start, double? end) CalculationInterval(JsonNode? start, JsonNode? end)
    {
        foreach (var value in new[] { start, end })
            if (!value.IsNull() && value.Number() == null) throw new UserError("Intervallo di calcolo non valido");
        var a = start.Number();
        var b = end.Number();
        if (a != null && b != null && b <= a) throw new UserError("Fine intervallo successiva all’inizio");
        return (a, b);
    }

    (RecordingLibrary library, string name, Dictionary<string, object?> descriptor) HistoricalReader(JsonNode? data, bool full = false)
    {
        var library = Source(data.Get("source"));
        var name = data.Get("name").Str() ?? "";
        var descriptor = library.Describe(name);
        var ids = data.Get("signal_ids") is JsonArray a ? a.Select(n => RowValues.Str(Plain(n))).ToList() : new List<string>();
        var physical = ((List<object?>)descriptor["signals"]!).Cast<Dictionary<string, object?>>().Select(s => (string)s["nodeid"]!).ToHashSet();
        if (ids.Any(n => n.StartsWith("calc:", StringComparison.Ordinal) && !physical.Contains(n)))
        {
            JsonNode? start = null, end = null;
            if (!full)
            {
                start = data is JsonObject o && o.ContainsKey("start_ms") ? o["start_ms"] : data.Get("x1");
                end = data is JsonObject p && p.ContainsKey("end_ms") ? p["end_ms"] : data.Get("x2");
            }
            var (s, e) = !start.IsNull() || !end.IsNull() ? CalculationInterval(start, end) : (null, null);
            return rt.Calculations.History(library, name, ids, rt.Formulas.Formulas(), s, e);
        }
        return (library, name, descriptor);
    }

    List<Dictionary<string, object?>> AvailableRecorded(JsonNode? data, Dictionary<string, object?> descriptor)
    {
        var physical = ((List<object?>)descriptor["signals"]!).Cast<Dictionary<string, object?>>().Select(s => (string)s["nodeid"]!).ToList();
        if (physical.Count == 0)
            physical = ((List<object?>)Source(data.Get("source")).Load(data.Get("name").Str()!, maxPoints: JsonValue.Create(1000))["signals"]!).Cast<Dictionary<string, object?>>().Select(s => (string)s["nodeid"]!).ToList();
        return CalculationService.Available(rt.Formulas.Formulas(), physical);
    }

    /// <summary>Common fields of a recording view: name, format, calculations and per-signal unit.</summary>
    void DescribeRecorded(JsonNode? data, RecordingLibrary library, string name, Dictionary<string, object?> descriptor, Dictionary<string, object?> result)
    {
        result["name"] = Plain(data.Get("name"));
        result["format"] = descriptor["format"];
        result["available_calculations"] = AvailableRecorded(data, descriptor);
        var definitions = rt.Formulas.Formulas().ToDictionary(f => f["nodeid"].Str()!, f => f);
        var metadata = library.Metadata(library.PathOf(name));
        var signalMeta = new Dictionary<string, Dictionary<string, object?>>();
        var source = metadata["signals"] is JsonArray { Count: > 0 } metaSignals ? metaSignals.Select(Plain).ToList() : (List<object?>)descriptor["signals"]!;
        foreach (var s in source.OfType<Dictionary<string, object?>>())
            if (s.GetValueOrDefault("nodeid") is string id) signalMeta[id] = s;
        foreach (Dictionary<string, object?> signal in (List<object?>)result["signals"]!)
        {
            var nid = (string)signal["nodeid"]!;
            var info = signalMeta.GetValueOrDefault(nid) ?? new();
            signal["unit"] = info.GetValueOrDefault("unit") ?? "";
            signal["calculated"] = nid.StartsWith("calc:", StringComparison.Ordinal);
            if (signal["calculated"] is true && !RowValues.Truthy(signal["unit"])) signal["unit"] = definitions.TryGetValue(nid, out var f) ? f["unit"].Str() ?? "" : "";
        }
    }

    void MapRecordings(WebApplication app)
    {
        Get(app, "/api/recordings", _ => Task.FromResult<object?>(Ok(("directory", Paths.RecordingsDir), ("default_directory", Paths.DefaultRecordingsDir),
            ("location_warning", Paths.RecordingsLocationWarning), ("files", rt.Library.List().Concat(rt.Imported.List("local")).ToList()))));
        // Folder of new recordings, exports and the Recordings page; empty directory = default (Recordings beside the application).
        Post(app, "/api/recordings/location", async ctx =>
        {
            var data = await Http.Object(ctx);
            var directory = await rt.SetRecordingsDirectory(data["directory"].Str());
            return Ok(("directory", directory), ("default_directory", Paths.DefaultRecordingsDir));
        });
        Post(app, "/api/recordings/location/browse", async ctx =>
        {
            var title = (await Http.Object(ctx, silent: true))["title"].Str();
            title = string.IsNullOrWhiteSpace(title) ? "Cartella delle registrazioni" : title.Trim()[..Math.Min(title.Trim().Length, 120)];
            return Ok(("directory", await FolderPicker.Pick(title, Paths.RecordingsDir)));
        });
        Post(app, "/api/recordings/import", async ctx => await Import.Files(ctx, Paths.ImportedRecordingsDir, rt.Imported));
        Post(app, "/api/recordings/data", async ctx =>
        {
            var data = await Http.Json(ctx, silent: true) ?? new JsonObject();
            var (library, name, descriptor) = HistoricalReader(data);
            var result = library.Load(name, data.Get("signal_ids"), data.Get("start_ms"), data.Get("end_ms"), data is JsonObject o && o.ContainsKey("max_points") ? o["max_points"] : JsonValue.Create(25000));
            DescribeRecorded(data, library, name, descriptor, result);
            return Ok(("recording", result));
        });
        // Full resolution: every sample of the file, binary columns (no reduction for display).
        Post(app, "/api/recordings/samples", async ctx =>
        {
            var data = await Http.Json(ctx, silent: true) ?? new JsonObject();
            var (library, name, descriptor) = HistoricalReader(data);
            var view = library.LoadFull(name, data.Get("signal_ids"), data.Get("start_ms"), data.Get("end_ms"));
            DescribeRecorded(data, library, name, descriptor, view.Info);
            return new SamplePayload(view.Info, view.Series);
        });
        Post(app, "/api/recordings/compare", async ctx => Ok(("job", rt.Comparisons.Start(await Http.Json(ctx, silent: true) ?? new JsonObject(), s => Source(JsonValue.Create(s))))));
        Get(app, "/api/recordings/compare/{ident}", ctx => Task.FromResult<object?>(Ok(("job", rt.Comparisons.Status((string)ctx.Request.RouteValues["ident"]!)))));
        Post(app, "/api/recordings/markers", async ctx =>
        {
            var data = await Http.Json(ctx, silent: true) ?? new JsonObject();
            var (library, name, _) = HistoricalReader(data, full: true);
            var ids = data.Get("signal_ids").Truthy() ? data.Get("signal_ids") : new JsonArray();
            return Ok(("markers", library.Markers(name, ids, data.Get("x1"), data.Get("x2"))));
        });
    }

    // ---- Calculations -------------------------------------------------------------------------

    void EditGuard()
    {
        if (rt.Recording.Acquiring) throw new UserError("Premere Stop prima di modificare le formule: la registrazione conserva la definizione iniziale");
    }

    Dictionary<string, object?> WithSnapshot(Dictionary<string, object?> result)
    {
        foreach (var (k, v) in rt.Formulas.Snapshot()) result[k] = v?.DeepClone();
        return result;
    }

    void MapCalculations(WebApplication app)
    {
        Get(app, "/api/calculations", _ =>
        {
            var result = WithSnapshot(Ok());
            result["diagnostics"] = rt.Calculations.Status();
            return Task.FromResult<object?>(result);
        });
        Post(app, "/api/calculations/validate", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var (f, _) = rt.Formulas.Candidate(data is JsonObject o && o.ContainsKey("formula") ? o["formula"] : data);
            return Ok(("formula", f), ("unit_hint", Plain(f["unit_hint"])), ("required_sources", Plain(f["required_sources"])));
        });
        Post(app, "/api/calculations/save", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            await rt.CommandLock.WaitAsync();
            JsonObject f;
            try
            {
                EditGuard();
                f = rt.Formulas.Save(data);
                rt.ResetCalculationsLocked();
                Log.Info($"Formula salvata id={f["id"].Str()} revision={f["revision"]?.ToJsonString()}");
            }
            finally { rt.CommandLock.Release(); }
            return WithSnapshot(Ok(("formula", f)));
        });
        Post(app, "/api/calculations/delete", async ctx =>
        {
            var ident = (await Http.Json(ctx) ?? new JsonObject()).Get("id").Str();
            await rt.CommandLock.WaitAsync();
            try { EditGuard(); rt.Formulas.Delete(ident); rt.ResetCalculationsLocked(); }
            finally { rt.CommandLock.Release(); }
            Log.Info("Formula eliminata id=" + ident);
            return WithSnapshot(Ok());
        });
        Post(app, "/api/calculations/duplicate", async ctx =>
        {
            var ident = (await Http.Json(ctx) ?? new JsonObject()).Get("id").Str();
            await rt.CommandLock.WaitAsync();
            JsonObject f;
            try { EditGuard(); f = rt.Formulas.Duplicate(ident); rt.ResetCalculationsLocked(); }
            finally { rt.CommandLock.Release(); }
            return WithSnapshot(Ok(("formula", f)));
        });
        Post(app, "/api/calculations/preview", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var (f, formulas) = rt.Formulas.Candidate(data.Get("formula") ?? new JsonObject());
            var mode = data.Get("mode") == null ? "live" : data.Get("mode").Str();
            if (mode is not ("live" or "recorded")) throw new UserError("Modalità anteprima non valida");
            if (f["modes"]?[mode].Bool() != true) throw new UserError("Abilitare la modalità " + mode + " nella formula");
            var previewFormulas = formulas.Select(g =>
            {
                if (g["nodeid"].Str() != f["nodeid"].Str()) return g;
                var copy = (JsonObject)g.DeepClone();
                copy["active"] = true;
                return copy;
            }).ToList();
            var (start, end) = CalculationInterval(data.Get("start_ms"), data.Get("end_ms"));
            var nid = f["nodeid"].Str()!;
            List<object?> points;
            string status, quality;
            if (mode == "live")
            {
                var plan = new Plan(previewFormulas, "live", [nid]);
                var ids = plan.RawIds.Count > 0 ? plan.RawIds.ToList() : rt.Selected.Take(1).ToList();
                var captured = new Dictionary<string, List<(double, double?, string)>>();
                foreach (var (key, list) in rt.Captured(ids))
                    captured[key] = list.TakeLast(5000).Select(p => (p.T, p.Value, p.Quality ?? "")).ToList();
                var rows = rt.Calculations.Preview(previewFormulas, nid, captured, start, end);
                points = rows.Select(r => (object?)new List<object?> { r["timestamp_ms"], r["value_numeric"], r["quality"] }).ToList();
                var last = rows.Count > 0 ? rows[^1] : new();
                status = last.TryGetValue("status", out var s) ? (string)s! : "Nessun campione sorgente: selezionare le variabili e avviare l’acquisizione";
                quality = last.TryGetValue("quality", out var q) ? (string)q! : "Bad";
            }
            else
            {
                var (library, name, _) = rt.Calculations.History(Source(data.Get("source")), data.Get("name").Str(), [nid], previewFormulas, start, end);
                var result = library.Load(name, new JsonArray(nid), maxPoints: JsonValue.Create(1000));
                var series = (Dictionary<string, object?>)result["series"]!;
                points = series.TryGetValue(nid, out var list) ? ((List<object?[]>)list!).TakeLast(200).Select(p => (object?)p.ToList()).ToList() : new();
                Row? last = null;
                foreach (var row in library.IterRows(library.PathOf(name))) last = row;
                status = last?.TryGetValue("status", out var s) == true ? RowValues.Str(s) : "Nessun campione";
                quality = last?.TryGetValue("quality", out var q) == true ? RowValues.Str(q) : "Bad";
            }
            return Ok(("formula", f), ("series", points), ("value", points.Count > 0 ? ((List<object?>)points[^1]!)[1] : null),
                ("quality", quality), ("status", status), ("unit", f["unit"].Truthy() ? f["unit"].Str() : f["unit_hint"].Str()), ("sample_count", (long)points.Count));
        });
        Post(app, "/api/calculations/materialize", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var fmt = data.Get("format") == null ? "parquet" : data.Get("format").Str();
            if (fmt is not ("csv" or "parquet")) throw new UserError("Dataset calcolati: scegliere CSV o Parquet; FDA e disponibile da Record");
            var (start, end) = CalculationInterval(data.Get("start_ms"), data.Get("end_ms"));
            var ids = data.Get("signal_ids") as JsonArray;
            if (ids == null || ids.Count == 0 || !ids.Any(n => RowValues.Str(Plain(n)).StartsWith("calc:", StringComparison.Ordinal)))
                throw new UserError("Selezionare almeno una variabile calcolata");
            Directory.CreateDirectory(Paths.RecordingsDir);
            var destination = Path.Combine(Paths.RecordingsDir, "Calculated_" + DateTime.Now.ToString("yyyyMMdd_HHmmss") + "_" + Convert.ToHexString(RandomNumberGenerator.GetBytes(4)).ToLowerInvariant() + "." + fmt);
            var (library, name, _) = rt.Calculations.History(Source(data.Get("source")), data.Get("name").Str(), ids.Select(n => RowValues.Str(Plain(n))).ToList(),
                rt.Formulas.Formulas(), start, end, destination, fmt!);
            Log.Info("Dataset calcolato materializzato file=" + Path.GetFileName(destination));
            return Ok(("recording", library.Describe(name)), ("path", destination));
        });
    }

    // ---- Analysis -----------------------------------------------------------------------------

    static List<string> SignalIds(JsonNode? data)
    {
        if (data.Get("signal_ids") is not JsonArray ids || ids.Count is < 1 or > 64 || ids.Any(n => !n.IsString())) throw new UserError("Selezionare da 1 a 64 segnali");
        return ids.Select(n => n.Str()!).ToList();
    }

    IEnumerable<Row> AnalysisRows(JsonNode? data, bool ordered = false)
    {
        var ids = data.Get("signal_ids").Truthy() ? data.Get("signal_ids") : new JsonArray();
        if (ids is not JsonArray list || list.Count is < 1 or > 64 || list.Any(n => !n.IsString())) throw new UserError("Selezionare da 1 a 64 segnali");
        var signalIds = list.Select(n => n.Str()!).ToList();
        if (data.Get("source").Str() == "live")
        {
            ArchiveReference? reference;
            Dictionary<string, List<LivePoint>>? captured;
            if (data.Get("snapshot_id").Truthy())
            {
                var frozen = rt.Snapshots.Get(data.Get("snapshot_id").Str());
                reference = frozen as ArchiveReference;
                captured = reference == null ? signalIds.Distinct().ToDictionary(n => n, n => ((Dictionary<string, List<LivePoint>>)frozen).GetValueOrDefault(n) ?? new()) : null;
            }
            else
            {
                reference = rt.LiveReference(signalIds);
                captured = reference == null ? rt.Captured(signalIds) : null;
            }
            var names = rt.Formulas.Formulas().ToDictionary(f => f["nodeid"].Str()!, f => f["name"].Str() ?? "");
            if (reference != null)
            {
                var (start, end) = Interval.Of(data);
                return PointRows.Rows(reference.Archive.Points(reference, signalIds, start, end), rt.SampleMs, names);
            }
            if (!data.Get("x1").IsNull() && !data.Get("x2").IsNull() && !data.Get("live_seconds").Truthy())
            {
                var requested = Math.Min(data.Get("x1").Float() ?? double.NaN, data.Get("x2").Float() ?? double.NaN);
                foreach (var (_, points) in captured!)
                {
                    if (points.Count == 0) continue;
                    var tolerance = Math.Max(1, points[0].Period is double p && p != 0 ? p : rt.SampleMs) * 1.5;
                    if (requested < points[0].T - tolerance) throw new UserError("Campioni originali di questo intervallo non disponibili: avviare una nuova acquisizione o aprire la registrazione");
                }
            }
            var pairs = captured!.SelectMany(k => k.Value.Select(p => (k.Key, p)));
            if (ordered) pairs = pairs.OrderBy(x => x.p.T);
            return PointRows.Rows(pairs, rt.SampleMs, names);
        }
        var (library, name, _) = HistoricalReader(data);
        var (a, b) = Interval.Of(data);
        return library.IterRows(library.PathOf(name), signalIds.ToHashSet(), a, b);
    }

    void MapAnalysis(WebApplication app)
    {
        Post(app, "/api/analysis/live-snapshot", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var ids = data.Get("signal_ids") as JsonArray;
            if (ids == null || ids.Count is < 1 or > 64 || ids.Any(n => !n.IsString())) throw new UserError("Segnali snapshot non validi");
            var list = ids.Select(n => n.Str()!).ToList();
            var (start, end) = Interval.Of(data);
            var reference = rt.LiveReference(list, start, end);
            if (reference != null) reference = rt.Archive.Reference(rt.AllLiveIds().ToList(), start, end);
            (string key, long count) result;
            if (reference is { Count: > 0 }) result = rt.Snapshots.CreateReference(reference);
            else result = rt.Snapshots.Create(reference == null ? rt.Captured(list, start, end) : new());
            return Ok(("snapshot_id", result.key), ("samples", result.count));
        });
        Post(app, "/api/analysis/live-snapshot/release", async ctx =>
        {
            rt.Snapshots.Delete((await Http.Json(ctx) ?? new JsonObject()).Get("snapshot_id").Str());
            return Ok();
        });
        Post(app, "/api/analysis/markers", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var ids = SignalIds(data);
            Interval.Of(data);
            var x1 = data.Get("x1").Float()!.Value;
            var x2 = data.Get("x2").Float()!.Value;
            ArchiveReference? reference;
            Dictionary<string, List<LivePoint>>? captured;
            if (data.Get("snapshot_id").Truthy())
            {
                var frozen = rt.Snapshots.Get(data.Get("snapshot_id").Str());
                reference = frozen as ArchiveReference;
                captured = reference == null ? (Dictionary<string, List<LivePoint>>)frozen : null;
            }
            else
            {
                reference = rt.LiveReference(ids);
                captured = reference == null ? rt.Captured(ids) : null;
            }
            object? results;
            if (reference != null) results = reference.Archive.Markers(reference, ids, x1, x2);
            else
            {
                var list = new List<object?>();
                foreach (var nid in ids)
                {
                    var row = new Dictionary<string, object?> { ["nodeid"] = nid, ["x1"] = null, ["x2"] = null };
                    var points = captured!.GetValueOrDefault(nid) ?? new();
                    foreach (var (key, t) in new[] { ("x1", x1), ("x2", x2) })
                    {
                        if (points.Count == 0) continue;
                        var p = points.MinBy(q => Math.Abs(q.T - t))!;
                        var period = p.Period is double pp && pp != 0 ? pp : rt.SampleMs;
                        if (Math.Abs(p.T - t) <= 3 * Math.Max(1, period) && p.Quality == "Good" && p.Value != null)
                            row[key] = new Dictionary<string, object?> { ["timestamp_ms"] = p.T, ["value"] = p.Value };
                    }
                    list.Add(row);
                }
                results = list;
            }
            return Ok(("x1", x1), ("x2", x2), ("results", results), ("data_origin", "originals"));
        });
        Post(app, "/api/analysis/quick", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var (start, end) = Interval.Of(data);
            if (data.Get("formula").Truthy())
            {
                var definition = (JsonObject)data.Get("formula")!.DeepClone();
                if (!definition["name"].Truthy()) definition["name"] = "Calcolo intervallo";
                definition["active"] = true;
                definition["modes"] = new JsonObject { ["live"] = true, ["recorded"] = true };
                var f = FormulaStore.Normalize(definition);
                var available = rt.Formulas.Formulas().ToDictionary(g => g["nodeid"].Str()!, g => g);
                available[f["nodeid"].Str()!] = f;
                var stored = rt.StoredCalculated().ToHashSet();
                var needed = new Dictionary<string, JsonObject>();
                void Include(string nid)
                {
                    if (needed.ContainsKey(nid)) return;
                    if (!available.TryGetValue(nid, out var g)) throw new UserError("Una variabile calcolata referenziata non esiste più");
                    needed[nid] = g;
                    var expression = new Expression(g["expression"], g["bindings"]);
                    foreach (var alias in expression.UsedBindings)
                    {
                        var binding = g["bindings"]![alias]!;
                        var source = binding["nodeid"].Str()!;
                        if (source.StartsWith("calc:", StringComparison.Ordinal) && !stored.Contains(source) && binding["kind"].Str() != "stored") Include(source);
                    }
                }
                Include(f["nodeid"].Str()!);
                var plan = new Plan(needed.Values.ToList(), "live", [f["nodeid"].Str()!], externalIds: stored);
                var ids = plan.RawIds.ToList();
                if (ids.Count == 0) throw new UserError("Associare almeno una variabile alla formula");
                var lookback = plan.Order.Sum(n => plan.Formulas[n]["max_age_ms"].Num() ?? 0);
                var body = (JsonObject)data.DeepClone();
                body["signal_ids"] = new JsonArray(ids.Select(n => (JsonNode?)n).ToArray());
                body["x1"] = start - lookback;
                body["x2"] = end;
                if (data.Get("source").Str() != "live") throw new UserError("Calcolo intervallo disponibile sui segnali Live");
                var rows = IntervalStatistics.FormulaRows(AnalysisRows(body, ordered: true), plan, f["nodeid"].Str()!, start, end, rt.SampleMs);
                var result = IntervalStatistics.Summarize(rows, [f["nodeid"].Str()!], start, end, data.Get("x1").Float()!.Value, data.Get("x2").Float()!.Value);
                return Ok(("x1", Plain(data.Get("x1"))), ("x2", Plain(data.Get("x2"))), ("formula", f), ("results", result), ("data_origin", "originals"));
            }
            var summary = IntervalStatistics.Summarize(AnalysisRows(data), ((JsonArray)data.Get("signal_ids")!).Select(n => n.Str()!).ToList(), start, end,
                data.Get("x1").Float()!.Value, data.Get("x2").Float()!.Value);
            return Ok(("x1", Plain(data.Get("x1"))), ("x2", Plain(data.Get("x2"))), ("results", summary));
        });
        Post(app, "/api/analysis/fft", async ctx =>
        {
            var data = await Http.Json(ctx) ?? new JsonObject();
            var ids = data.Get("signal_ids").Truthy() ? data.Get("signal_ids") : new JsonArray();
            if (ids is not JsonArray list || list.Count != 1) throw new UserError("Selezionare un solo segnale per FFT");
            if (!data.Get("live_seconds").IsNull()) throw new UserError("FFT disponibile solo in Pausa con marker X1 e X2");
            if (data.Get("source").Str() == "live" && !data.Get("snapshot_id").Truthy()) throw new UserError("FFT live: mettere il grafico in Pausa e posizionare X1 e X2");
            var (start, end) = Interval.Of(data);
            var options = data.Get("options").Truthy() ? data.Get("options") : new JsonObject();
            if (options is not JsonObject o) throw new UserError("Opzioni FFT non valide");
            var window = data is JsonObject d && d.ContainsKey("window") ? RowValues.Str(Plain(d["window"])) : "hann";
            var detrend = data is JsonObject e && e.ContainsKey("detrend") ? e["detrend"].Truthy() : true;
            var result = Fft.Calculate(AnalysisRows(data), RowValues.Str(Plain(list[0])), start, end, window, detrend, o);
            result["data_origin"] = data.Get("snapshot_id").Truthy() ? "vista live congelata: campioni originali" : data.Get("source").Str() == "live" ? "buffer live corrente" : "file registrazione originale";
            Log.Info($"FFT: original={result["samples_original"]} blocks={result["blocks_used"]} excluded={result["blocks_excluded"]}");
            return Ok(("spectrum", result));
        });
        Post(app, "/api/analysis/export", async ctx =>
        {
            var data = await Http.Json(ctx);
            var (start, end) = Interval.Of(data);
            var fmt = data.Get("format").Str();
            if (!data.Get("format").IsString() || fmt is not ("csv" or "parquet")) throw new UserError("Scegliere CSV o Parquet");
            Directory.CreateDirectory(Paths.ExportDir);
            var name = "Export_" + DateTime.UtcNow.ToString("yyyyMMdd_HHmmss") + "_" + Convert.ToHexString(RandomNumberGenerator.GetBytes(5)).ToLowerInvariant() + "." + fmt;
            var final = Path.Combine(Paths.ExportDir, name);
            var partial = final + ".part";
            long count;
            try
            {
                var ids = data.Get("signal_ids") is JsonArray a ? a.Select(n => RowValues.Str(Plain(n))).ToList() : new List<string>();
                count = Export.ExportRows(Interval.SelectedRows(AnalysisRows(data), ids, start, end), partial, fmt);
                File.Move(partial, final);
                Py.AtomicPlain(Path.ChangeExtension(final, ".metadata.json"), new Dictionary<string, object?>
                {
                    ["source"] = Plain(data.Get("source")), ["original"] = Plain(data.Get("name")), ["x1"] = start, ["x2"] = end, ["rows_written"] = count,
                    ["signal_ids"] = Plain(data.Get("signal_ids")),
                });
            }
            catch
            {
                try { File.Delete(partial); } catch (IOException) { }
                throw;
            }
            Log.Info($"Interval exported: {name} rows={count}");
            return Ok(("name", name), ("path", final), ("rows", count), ("bytes", new FileInfo(final).Length), ("url", "/api/exports/" + name));
        });
        Get(app, "/api/exports/{name}", ctx =>
        {
            var name = (string)ctx.Request.RouteValues["name"]!;
            var path = Path.GetFullPath(Path.Combine(Paths.ExportDir, name));
            if (Path.GetDirectoryName(path) != Path.GetFullPath(Paths.ExportDir) || Path.GetExtension(path) is not (".csv" or ".parquet" or ".zip") || !File.Exists(path))
                throw new UserError("Export non trovato");
            var type = Path.GetExtension(path) switch { ".csv" => "text/csv; charset=utf-8", ".zip" => "application/zip", _ => "application/octet-stream" };
            return Task.FromResult<object?>(new FileDownload(path, Path.GetFileName(path), type));
        });
    }

    // ---- SSH ------------------------------------------------------------------------------------

    static async Task<Dictionary<string, object?>> SshBody(HttpContext ctx)
    {
        var value = Py.Plain(await Http.Json(ctx) ?? new JsonObject());
        return value as Dictionary<string, object?> ?? throw new UserError("Richiesta SSH non valida");
    }

    void MapSsh(WebApplication app)
    {
        Get(app, "/api/ssh/config", _ =>
        {
            var cfg = ssh.Config();
            return Task.FromResult<object?>(Ok(("config", cfg), ("known_key", ssh.KnownKey(cfg)), ("status", ssh.Status())));
        });
        Post(app, "/api/ssh/config", async ctx => Ok(("config", ssh.SaveConfig(await SshBody(ctx)))));
        Post(app, "/api/ssh/connect", async ctx => Ok(("status", ssh.Connect(await SshBody(ctx)))));
        Post(app, "/api/ssh/disconnect", _ => { ssh.Disconnect(); return Task.FromResult<object?>(Ok(("status", ssh.Status()))); });
        Post(app, "/api/ssh/trust", async ctx => { ssh.Trust(await SshBody(ctx)); return Ok(); });
        Post(app, "/api/ssh/forget-key", async ctx => { ssh.ForgetKey(await SshBody(ctx)); return Ok(); });
        Get(app, "/api/ssh/status", ctx =>
        {
            var result = Ok();
            foreach (var (k, v) in ssh.Status(Http.Query(ctx, "after", "0"))) result[k] = v;
            return Task.FromResult<object?>(result);
        });
        Post(app, "/api/ssh/execute", async ctx => Ok(("job", ssh.Execute(await SshBody(ctx)))));
        Post(app, "/api/ssh/stop", async ctx => { ssh.Stop((await SshBody(ctx)).GetValueOrDefault("id")); return Ok(); });
        Post(app, "/api/ssh/input", async ctx => { ssh.SendInput(await SshBody(ctx)); return Ok(); });
        Post(app, "/api/ssh/clear", _ => { ssh.Clear(); return Task.FromResult<object?>(Ok()); });
    }
}
