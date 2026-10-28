using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Databases;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;

namespace SpaceAnalyzer.App.Influx;

/// <summary>
/// Read-only access to an InfluxDB 2.x server: connection (URL + token, organization found automatically), catalog of buckets / measurements /
/// series and time series for the charts, reduced automatically to what a chart can show.
/// </summary>
public sealed class InfluxService(string dataDir) : ISeriesDatabase, IDisposable
{
    const int MaxSeriesDefault = 5000;
    const int RawHardLimit = 500_000;
    static readonly double[] WindowSteps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
    static readonly HashSet<string> Aggregates = ["mean", "min", "max", "last", "first", "median", "sum"];

    readonly object gate = new();
    readonly Dictionary<bool, HttpClient> clients = new();
    string SettingsPath => Path.Combine(dataDir, "influx.json");

    sealed record Settings(string Url, string Token, string Org, string OrgId, bool TlsInsecure);

    Settings Load()
    {
        lock (gate)
        {
            try
            {
                if (File.Exists(SettingsPath) && Py.Parse(File.ReadAllText(SettingsPath)) is JsonObject o)
                    return new Settings(o["url"].Str() ?? "", o["token"].Str() ?? "", o["org"].Str() ?? "", o["org_id"].Str() ?? "", o["tls_insecure"].Bool() ?? false);
            }
            catch (Exception exc) { Log.Warning("Impostazioni InfluxDB non leggibili: " + exc.Message); }
            return new Settings("", "", "", "", false);
        }
    }

    void Save(Settings s)
    {
        lock (gate)
        {
            Directory.CreateDirectory(dataDir);
            Py.AtomicJson(SettingsPath, new JsonObject { ["url"] = s.Url, ["token"] = s.Token, ["org"] = s.Org, ["org_id"] = s.OrgId, ["tls_insecure"] = s.TlsInsecure });
        }
    }

    /// <summary>Saved settings for the UI: the token itself never leaves the service, only whether one is saved and its last characters.</summary>
    public Dictionary<string, object?> PublicSettings()
    {
        var s = Load();
        return new Dictionary<string, object?>
        {
            ["url"] = s.Url, ["org"] = s.Org, ["tls_insecure"] = s.TlsInsecure, ["token_saved"] = s.Token.Length > 0,
            ["token_hint"] = s.Token.Length >= 4 ? "…" + s.Token[^4..] : "",
        };
    }

    public void Forget()
    {
        var s = Load();
        Save(s with { Token = "", OrgId = "" });
    }

    public static string NormalizeUrl(string? url)
    {
        var u = (url ?? "").Trim().TrimEnd('/');
        if (u.Length == 0) throw new UserError("Inserisci l'indirizzo del server InfluxDB (ad esempio http://localhost:8086).");
        if (!u.Contains("://", StringComparison.Ordinal)) u = "http://" + u;
        if (!Uri.TryCreate(u, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https")) throw new UserError("Indirizzo InfluxDB non valido: usare http:// o https://.");
        return u;
    }

    HttpClient Client(bool tlsInsecure)
    {
        lock (gate)
        {
            if (clients.TryGetValue(tlsInsecure, out var existing)) return existing;
            var handler = new SocketsHttpHandler { ConnectTimeout = TimeSpan.FromSeconds(10), AutomaticDecompression = DecompressionMethods.All };
            if (tlsInsecure) handler.SslOptions.RemoteCertificateValidationCallback = (_, _, _, _) => true;
            var client = new HttpClient(handler) { Timeout = TimeSpan.FromMinutes(5) };
            clients[tlsInsecure] = client;
            return client;
        }
    }

    static async Task<string> Fail(HttpResponseMessage response, CancellationToken ct)
    {
        string message = response.ReasonPhrase ?? "";
        try
        {
            var body = await response.Content.ReadAsStringAsync(ct);
            if (Py.Parse(body) is JsonObject o && o["message"].Str() is { Length: > 0 } m) message = m;
        }
        catch (Exception) { }
        return response.StatusCode switch
        {
            HttpStatusCode.Unauthorized => "InfluxDB: token non valido o scaduto" + (message.Length > 0 ? " (" + message + ")" : ""),
            HttpStatusCode.Forbidden => "InfluxDB: il token non ha il permesso di lettura richiesto" + (message.Length > 0 ? " (" + message + ")" : ""),
            _ => $"InfluxDB: {(int)response.StatusCode} {message}".Trim(),
        };
    }

    async Task<JsonNode?> GetJson(Settings s, string path, bool auth, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, s.Url + path);
        if (auth) request.Headers.Authorization = new AuthenticationHeaderValue("Token", s.Token);
        using var response = await Client(s.TlsInsecure).SendAsync(request, ct);
        if (!response.IsSuccessStatusCode) throw new UserError(await Fail(response, ct));
        return Py.Parse(await response.Content.ReadAsStringAsync(ct));
    }

    /// <summary>Tests the connection, finds the organization and the buckets, and saves the settings on success.</summary>
    public async Task<Dictionary<string, object?>> Connect(JsonObject request, CancellationToken ct)
    {
        var saved = Load();
        var url = NormalizeUrl(request["url"].Str() is { Length: > 0 } u ? u : saved.Url);
        var token = request["token"].Str() is { Length: > 0 } t ? t.Trim() : saved.Token;
        var tls = request["tls_insecure"].Bool() ?? saved.TlsInsecure;
        var wantedOrg = (request.ContainsKey("org") ? request["org"].Str() : saved.Org)?.Trim() ?? "";
        if (token.Length == 0) throw new UserError("Inserisci il token di accesso di InfluxDB (Load Data → API Tokens).");
        var draft = new Settings(url, token, wantedOrg, "", tls);
        string version;
        try
        {
            var health = await GetJson(draft, "/health", false, ct) as JsonObject;
            version = health?["version"].Str() ?? "";
            if (health?["status"].Str() is { } st && st != "pass") throw new UserError("InfluxDB non è pronto: " + (health["message"].Str() ?? st));
        }
        catch (HttpRequestException exc) { throw new UserError($"Server InfluxDB non raggiungibile ({url}): {exc.Message}"); }
        catch (TaskCanceledException) when (!ct.IsCancellationRequested) { throw new UserError($"Server InfluxDB non raggiungibile ({url}): tempo scaduto."); }
        var buckets = new List<Dictionary<string, object?>>();
        var orgIds = new List<string>();
        for (var offset = 0; offset < 2000; offset += 100)
        {
            var page = await GetJson(draft, "/api/v2/buckets?limit=100&offset=" + offset, true, ct) as JsonObject;
            var items = page?["buckets"] as JsonArray ?? new JsonArray();
            foreach (var b in items.OfType<JsonObject>())
            {
                var orgId = b["orgID"].Str() ?? "";
                if (orgId.Length > 0 && !orgIds.Contains(orgId)) orgIds.Add(orgId);
                var retention = (b["retentionRules"] as JsonArray)?.OfType<JsonObject>().Select(r => r["everySeconds"].Num()).FirstOrDefault(n => n is > 0);
                buckets.Add(new Dictionary<string, object?>
                {
                    ["id"] = b["id"].Str() ?? "", ["name"] = b["name"].Str() ?? "", ["type"] = b["type"].Str() ?? "user", ["org_id"] = orgId, ["retention_s"] = retention,
                });
            }
            if (items.Count < 100) break;
        }
        var orgs = new List<Dictionary<string, object?>>();
        try
        {
            if (await GetJson(draft, "/api/v2/orgs", true, ct) is JsonObject o && o["orgs"] is JsonArray list)
                foreach (var item in list.OfType<JsonObject>()) orgs.Add(new Dictionary<string, object?> { ["id"] = item["id"].Str() ?? "", ["name"] = item["name"].Str() ?? "" });
        }
        catch (UserError) { /* a token limited to buckets cannot list organizations: the organization of the buckets is enough */ }
        var chosenId = "";
        var chosenName = "";
        if (wantedOrg.Length > 0)
        {
            var match = orgs.FirstOrDefault(x => string.Equals((string?)x["name"], wantedOrg, StringComparison.Ordinal) || (string?)x["id"] == wantedOrg);
            if (match == null && orgs.Count > 0) throw new UserError($"Organizzazione «{wantedOrg}» non trovata: disponibili {string.Join(", ", orgs.Select(x => x["name"]))}.");
            chosenId = (string?)match?["id"] ?? wantedOrg;
            chosenName = (string?)match?["name"] ?? wantedOrg;
        }
        else if (orgIds.Count > 0)
        {
            chosenId = orgIds[0];
            chosenName = (string?)orgs.FirstOrDefault(x => (string?)x["id"] == chosenId)?["name"] ?? "";
        }
        if (chosenId.Length == 0) throw new UserError("Nessun bucket leggibile con questo token: controlla i permessi (lettura sui bucket) o indica l'organizzazione.");
        Save(new Settings(url, token, chosenName, chosenId, tls));
        return new Dictionary<string, object?>
        {
            ["ok"] = true, ["version"] = version, ["url"] = url, ["org"] = chosenName, ["org_id"] = chosenId, ["orgs"] = orgs,
            ["buckets"] = buckets.Where(b => (string?)b["org_id"] == chosenId || (string?)b["org_id"] == "").ToList(),
        };
    }

    Settings Connected()
    {
        var s = Load();
        if (s.Url.Length == 0 || s.Token.Length == 0 || s.OrgId.Length == 0) throw new UserError("InfluxDB non connesso: premi Connetti.");
        return s;
    }

    async Task<List<FluxRow>> Query(Settings s, string flux, int maxRows, CancellationToken ct)
    {
        var body = new JsonObject
        {
            ["query"] = flux, ["type"] = "flux",
            ["dialect"] = new JsonObject
            {
                ["annotations"] = new JsonArray("datatype", "group", "default"), ["header"] = true, ["delimiter"] = ",",
                ["commentPrefix"] = "#", ["dateTimeFormat"] = "RFC3339Nano",
            },
        };
        using var request = new HttpRequestMessage(HttpMethod.Post, s.Url + "/api/v2/query?orgID=" + Uri.EscapeDataString(s.OrgId))
        {
            Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"),
        };
        request.Headers.Authorization = new AuthenticationHeaderValue("Token", s.Token);
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/csv"));
        using var response = await Client(s.TlsInsecure).SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        if (!response.IsSuccessStatusCode) throw new UserError(await Fail(response, ct));
        await using var stream = await response.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream, Encoding.UTF8);
        var rows = new List<FluxRow>();
        foreach (var row in FluxCsv.Read(reader))
        {
            rows.Add(row);
            if (rows.Count >= maxRows) break;
        }
        return rows;
    }

    static string DataTypeName(string? flux) => flux switch
    {
        "double" => "Double", "long" => "Int64", "unsignedLong" => "UInt64", "boolean" => "Boolean", "string" => "String", _ => "Double",
    };

    public static string SeriesKey(string bucket, string measurement, string field, IReadOnlyDictionary<string, string> tags) =>
        "influx:" + bucket + "/" + measurement + "/" + field + (tags.Count == 0 ? "" : "{" + string.Join(",", tags.OrderBy(t => t.Key, StringComparer.Ordinal).Select(t => t.Key + "=" + t.Value)) + "}");

    /// <summary>Series (measurement + field + tags) of a bucket that have data in the look-back window, with their value type and the time of their last point.</summary>
    public async Task<Dictionary<string, object?>> Catalog(JsonObject request, CancellationToken ct)
    {
        var s = Connected();
        var bucket = request["bucket"].Str();
        if (string.IsNullOrWhiteSpace(bucket)) throw new UserError("Scegli un bucket.");
        var lookback = (long)Math.Clamp(request["lookback_s"].Num() ?? 30 * 86400, 60, 3650L * 86400);
        var maxSeries = (int)Math.Clamp(request["max_series"].Num() ?? MaxSeriesDefault, 1, 20000);
        var flux = $"from(bucket: {Flux.Str(bucket)}) |> range(start: -{lookback.ToString(CultureInfo.InvariantCulture)}s) |> last() |> drop(columns: [\"_start\", \"_stop\"])";
        var rows = await Query(s, flux, maxSeries + 1, ct);
        var truncated = rows.Count > maxSeries;
        var series = new List<Dictionary<string, object?>>();
        var seen = new HashSet<string>();
        foreach (var row in rows.Take(maxSeries))
        {
            var measurement = row.Get("_measurement") ?? "";
            var field = row.Get("_field") ?? "";
            var tags = new SortedDictionary<string, string>(StringComparer.Ordinal);
            for (var i = 0; i < row.Columns.Length; i++)
                if (!FluxCsv.Reserved.Contains(row.Columns[i]) && i < row.Values.Length && row.Values[i].Length > 0) tags[row.Columns[i]] = row.Values[i];
            var key = SeriesKey(bucket, measurement, field, tags);
            if (!seen.Add(key)) continue;
            series.Add(new Dictionary<string, object?>
            {
                ["key"] = key, ["bucket"] = bucket, ["measurement"] = measurement, ["field"] = field, ["tags"] = tags.ToDictionary(t => t.Key, t => (object?)t.Value),
                ["data_type"] = DataTypeName(row.TypeOf("_value")),
                ["last_ms"] = row.Get("_time") is { Length: > 0 } time ? FluxCsv.ParseTimeMs(time) : null,
            });
        }
        series.Sort((a, b) => string.CompareOrdinal((string)a["key"]!, (string)b["key"]!));
        return new Dictionary<string, object?> { ["bucket"] = bucket, ["lookback_s"] = lookback, ["series"] = series, ["truncated"] = truncated };
    }

    static string Filter(string measurement, string field, JsonObject? tags)
    {
        var sb = new StringBuilder($"r._measurement == {Flux.Str(measurement)} and r._field == {Flux.Str(field)}");
        if (tags != null)
            foreach (var (k, v) in tags.OrderBy(t => t.Key, StringComparer.Ordinal))
                sb.Append($" and r[{Flux.Str(k)}] == {Flux.Str(v.Str() ?? "")}");
        return sb.ToString();
    }

    /// <summary>Window for aggregation: the smallest round duration that brings the range under the requested number of points.</summary>
    public static double WindowSeconds(double rangeSeconds, int maxPoints)
    {
        var target = Math.Max(1, rangeSeconds / Math.Max(10, maxPoints));
        foreach (var step in WindowSteps) if (step >= target) return step;
        return Math.Ceiling(target / 86400) * 86400;
    }

    sealed record Loaded(SampleColumns Columns, long Original, long Returned, double Window, string Aggregate);

    /// <summary>Time series for the charts. Few points: all of them. Many points: aggregated by a round window (mean unless chosen otherwise).</summary>
    public async Task<SamplePayload> Samples(JsonObject request, CancellationToken ct)
    {
        var s = Connected();
        var end = request["end_ms"].Num() ?? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var start = request["start_ms"].Num() ?? end - 6 * 3600_000.0;
        if (!(end > start)) throw new UserError("L'intervallo di tempo non è valido: la fine deve seguire l'inizio.");
        var maxPoints = (int)Math.Clamp(request["max_points"].Num() ?? 4000, 100, 100_000);
        var aggregate = (request["aggregate"].Str() ?? "auto").ToLowerInvariant();
        if (aggregate != "auto" && aggregate != "raw" && !Aggregates.Contains(aggregate)) throw new UserError("Aggregazione non valida: " + aggregate);
        var requested = request["series"] as JsonArray ?? throw new UserError("Seleziona almeno una serie.");
        var items = requested.OfType<JsonObject>().Take(64).ToList();
        if (items.Count == 0) throw new UserError("Seleziona almeno una serie.");
        using var limit = new SemaphoreSlim(4);
        var tasks = items.Select(async item =>
        {
            await limit.WaitAsync(ct);
            try { return await LoadSeries(s, item, start, end, maxPoints, aggregate, ct); }
            finally { limit.Release(); }
        }).ToList();
        var loaded = await Task.WhenAll(tasks);
        var info = new Dictionary<string, object?>
        {
            ["signals"] = loaded.Select(l => (object?)new Dictionary<string, object?>
            {
                ["nodeid"] = l.Columns.NodeId, ["display_name"] = l.Columns.Name, ["data_type"] = l.Columns.DataType,
                ["points_original"] = l.Original, ["points_returned"] = l.Returned, ["window_s"] = l.Window, ["aggregate"] = l.Aggregate,
            }).ToList(),
            ["start_ms"] = start, ["end_ms"] = end, ["max_points"] = maxPoints,
            ["full_resolution"] = loaded.All(l => l.Window == 0), ["decimated"] = loaded.Any(l => l.Window > 0),
            ["numeric_rows"] = loaded.Sum(l => l.Returned),
        };
        return new SamplePayload(info, loaded.Select(l => l.Columns).ToList());
    }

    sealed record Described(string Bucket, string Measurement, string Field, JsonObject? Tags, string Type, string Key, string Name);

    static Described Describe(JsonObject item)
    {
        var bucket = item["bucket"].Str() ?? throw new UserError("Serie senza bucket.");
        var measurement = item["measurement"].Str() ?? "";
        var field = item["field"].Str() ?? "";
        var tags = item["tags"] as JsonObject;
        var type = item["data_type"].Str() ?? "Double";
        var dictionary = new Dictionary<string, string>();
        if (tags != null) foreach (var (k, v) in tags) dictionary[k] = v.Str() ?? "";
        var name = measurement + "." + field + (dictionary.Count == 0 ? "" : " {" + string.Join(", ", dictionary.OrderBy(t => t.Key, StringComparer.Ordinal).Select(t => t.Key + "=" + t.Value)) + "}");
        return new Described(bucket, measurement, field, tags, type, SeriesKey(bucket, measurement, field, dictionary), name);
    }

    async Task<Loaded> LoadSeries(Settings s, JsonObject item, double start, double end, int maxPoints, string aggregate, CancellationToken ct)
    {
        var d = Describe(item);
        var (bucket, measurement, field, tags, type) = (d.Bucket, d.Measurement, d.Field, d.Tags, d.Type);
        var columns = new SampleColumns(d.Key) { DataType = type, Name = d.Name };
        if (type == "String") throw new UserError($"La serie «{columns.Name}» contiene testo e non può essere disegnata.");
        var range = $"range(start: {Flux.Time(start)}, stop: {Flux.Time(end)})";
        var source = $"from(bucket: {Flux.Str(bucket)}) |> {range} |> filter(fn: (r) => {Filter(measurement, field, tags)})";
        var counted = await Query(s, source + " |> group() |> count()", 4, ct);
        var original = counted.Select(r => (long?)long.Parse(r.Get("_value") ?? "0", CultureInfo.InvariantCulture)).FirstOrDefault() ?? 0;
        var window = 0.0;
        var function = "";
        var useRaw = aggregate == "raw" ? original <= RawHardLimit : original <= maxPoints;
        string flow;
        if (useRaw) flow = source + " |> keep(columns: [\"_time\", \"_value\"])";
        else
        {
            window = WindowSeconds((end - start) / 1000.0, maxPoints);
            function = aggregate is "auto" or "raw" ? "mean" : aggregate;
            var every = window.ToString("0", CultureInfo.InvariantCulture) + "s";
            var numeric = type == "Boolean" ? " |> map(fn: (r) => ({r with _value: if r._value then 1.0 else 0.0}))" : "";
            flow = source + numeric + $" |> group() |> aggregateWindow(every: {every}, fn: {function}, createEmpty: false) |> keep(columns: [\"_time\", \"_value\"])";
        }
        var rows = await Query(s, flow, useRaw ? RawHardLimit + 1 : maxPoints * 4, ct);
        var shift = window * 500.0;  // window stop -> window centre
        foreach (var row in rows)
        {
            if (row.Get("_time") is not { Length: > 0 } text || FluxCsv.ParseValue(row.Get("_value")) is not double value) continue;
            columns.Add(FluxCsv.ParseTimeMs(text) - shift, value, SampleColumns.Good);
        }
        columns.Sort();
        return new Loaded(columns, original, columns.Count, window, function);
    }

    public const int MaxExportSeries = 64;

    public string Label => "InfluxDB";
    public string Protocol => "influxdb";
    public List<ExportSeries> Plan(JsonNode? series) => ExportPlan(series);

    /// <summary>Validates the series of an export request (at most 64, no text series).</summary>
    public static List<ExportSeries> ExportPlan(JsonNode? series)
    {
        var items = (series as JsonArray)?.OfType<JsonObject>().ToList() ?? [];
        if (items.Count == 0) throw new UserError("Seleziona almeno una serie.");
        if (items.Count > MaxExportSeries) throw new UserError($"Si possono esportare al massimo {MaxExportSeries} serie (selezionate {items.Count}).");
        var plan = new List<ExportSeries>();
        foreach (var item in items)
        {
            var d = Describe(item);
            if (d.Type == "String") throw new UserError($"La serie «{d.Name}» contiene testo e non può essere esportata.");
            plan.Add(new ExportSeries(d.Key, d.Name, d.Type, item));
        }
        return plan;
    }

    static string ExportSource(Described d, double start, double end) =>
        $"from(bucket: {Flux.Str(d.Bucket)}) |> range(start: {Flux.Time(start)}, stop: {Flux.Time(end)}) |> filter(fn: (r) => {Filter(d.Measurement, d.Field, d.Tags)})";

    /// <summary>Number of original points of a series in [start, end).</summary>
    public async Task<long> CountPoints(ExportSeries series, double start, double end, CancellationToken ct)
    {
        var s = Connected();
        var rows = await Query(s, ExportSource(Describe(series.Item), start, end) + " |> group() |> count()", 4, ct);
        return rows.Select(r => (long?)long.Parse(r.Get("_value") ?? "0", CultureInfo.InvariantCulture)).FirstOrDefault() ?? 0;
    }

    /// <summary>Every original point of a series in [start, end): time (ms) and numeric value (booleans as 0/1), in time order.</summary>
    public async Task<List<(double Time, double Value)>> RawPoints(ExportSeries series, double start, double end, CancellationToken ct)
    {
        var s = Connected();
        var d = Describe(series.Item);
        var numeric = d.Type == "Boolean" ? " |> map(fn: (r) => ({r with _value: if r._value then 1.0 else 0.0}))" : "";
        var rows = await Query(s, ExportSource(d, start, end) + numeric + " |> group() |> sort(columns: [\"_time\"]) |> keep(columns: [\"_time\", \"_value\"])", ExportChunkLimit, ct);
        var points = new List<(double, double)>(rows.Count);
        foreach (var row in rows)
            if (row.Get("_time") is { Length: > 0 } text && FluxCsv.ParseValue(row.Get("_value")) is double value) points.Add((FluxCsv.ParseTimeMs(text), value));
        return points;
    }

    public const int ExportChunkLimit = 2_000_000;

    /// <summary>Address of the server (without credentials) for the metadata of an export.</summary>
    public string ServerUrl => Load().Url;

    public void Dispose()
    {
        foreach (var c in clients.Values) c.Dispose();
    }
}
