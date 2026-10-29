using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using Npgsql;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Influx;
using SpaceAnalyzer.App.Recordings;

namespace SpaceAnalyzer.App.Databases;

/// <summary>
/// Read-only access to time series stored in PostgreSQL (also TimescaleDB): connection (host, database, user, password), catalog of the tables and views
/// with a time column and numeric columns, and time series for the charts, reduced on the server to what a chart can show.
/// Two layouts: one column per signal (series = numeric column), or one row per signal and instant with a label column (series = column × label value).
/// Identifiers are always quoted and values passed as parameters.
/// </summary>
public sealed partial class PostgresService(string dataDir) : ISeriesDatabase, IDisposable
{
    const int MaxSeriesDefault = 5000;
    const int MaxTables = 2000;
    const int RawHardLimit = 500_000;
    static readonly HashSet<string> Aggregates = ["mean", "min", "max", "last", "first", "median", "sum"];
    public static readonly HashSet<string> TimeTypes = ["timestamptz", "timestamp", "date"];
    static readonly Dictionary<string, string> ValueTypes = new()
    {
        ["int2"] = "Int16", ["int4"] = "Int32", ["int8"] = "Int64", ["float4"] = "Float", ["float8"] = "Double", ["numeric"] = "Double", ["bool"] = "Boolean",
    };
    static readonly HashSet<string> LabelTypes = ["text", "varchar", "bpchar", "name", "int2", "int4", "int8"];

    readonly object gate = new();
    NpgsqlDataSource? source;
    string? sourceKey;
    string SettingsPath => Path.Combine(dataDir, "postgres.json");

    sealed record Settings(string Host, int Port, string Database, string User, string Password, string Ssl, string Zone);

    Settings Load()
    {
        lock (gate)
        {
            try
            {
                if (File.Exists(SettingsPath) && Py.Parse(File.ReadAllText(SettingsPath)) is JsonObject o)
                    return new Settings(o["host"].Str() ?? "", (int)(o["port"].Num() ?? 5432), o["database"].Str() ?? "", o["user"].Str() ?? "", o["password"].Str() ?? "",
                        o["ssl"].Str() ?? "prefer", o["timezone"].Str() ?? "utc");
            }
            catch (Exception exc) { Log.Warning("Impostazioni PostgreSQL non leggibili: " + exc.Message); }
            return new Settings("", 5432, "", "", "", "prefer", "utc");
        }
    }

    void Save(Settings s)
    {
        lock (gate)
        {
            Directory.CreateDirectory(dataDir);
            Py.AtomicJson(SettingsPath, new JsonObject
            {
                ["host"] = s.Host, ["port"] = s.Port, ["database"] = s.Database, ["user"] = s.User, ["password"] = s.Password, ["ssl"] = s.Ssl, ["timezone"] = s.Zone,
            });
        }
    }

    /// <summary>Saved settings for the UI: the password never leaves the service, only whether one is saved.</summary>
    public Dictionary<string, object?> PublicSettings()
    {
        var s = Load();
        return new Dictionary<string, object?>
        {
            ["host"] = s.Host, ["port"] = s.Port, ["database"] = s.Database, ["user"] = s.User, ["ssl"] = s.Ssl, ["timezone"] = s.Zone,
            ["password_saved"] = s.Password.Length > 0,
        };
    }

    public void Forget()
    {
        Save(Load() with { Password = "" });
        Reset();
    }

    void Reset()
    {
        lock (gate) { source?.Dispose(); source = null; sourceKey = null; }
    }

    /// <summary>IANA name of the time zone used for timestamps without time zone: UTC, or the time zone of the PC.</summary>
    public static string ZoneName(string zone)
    {
        if (zone != "local") return "UTC";
        var id = TimeZoneInfo.Local.Id;
        if (TimeZoneInfo.TryConvertWindowsIdToIanaId(id, out var iana)) return iana;
        return id.Contains('/') ? id : "UTC";
    }

    static NpgsqlConnectionStringBuilder Builder(Settings s) => new()
    {
        Host = s.Host, Port = s.Port, Database = s.Database, Username = s.User, Password = s.Password.Length > 0 ? s.Password : null,
        SslMode = s.Ssl switch { "disable" => SslMode.Disable, "require" => SslMode.Require, "verify-full" => SslMode.VerifyFull, _ => SslMode.Prefer },
        Timeout = 10, CommandTimeout = 300, ApplicationName = "Space Fieldbus Analyzer", Timezone = ZoneName(s.Zone),
        // Read-only use: a pool of a few connections is enough for the parallel reads of one chart.
        MaxPoolSize = 8,
    };

    NpgsqlDataSource Source(Settings s)
    {
        var text = Builder(s).ConnectionString;
        lock (gate)
        {
            if (source != null && sourceKey == text) return source;
            source?.Dispose();
            source = NpgsqlDataSource.Create(text);
            sourceKey = text;
            return source;
        }
    }

    /// <summary>Clear messages for the common connection and permission errors.</summary>
    static UserError Error(Exception exc, Settings s) => exc switch
    {
        PostgresException { SqlState: "28P01" or "28000" } => new UserError("PostgreSQL: utente o password non validi."),
        PostgresException { SqlState: "3D000" } => new UserError($"PostgreSQL: il database «{s.Database}» non esiste."),
        PostgresException { SqlState: "42501" } p => new UserError("PostgreSQL: permesso negato (" + p.MessageText + ")."),
        PostgresException { SqlState: "42P01" or "42703" } p => new UserError("PostgreSQL: tabella o colonna non trovata (" + p.MessageText + "): aggiorna l'elenco."),
        PostgresException p => new UserError("PostgreSQL: " + p.MessageText),
        NpgsqlException { InnerException: System.Net.Sockets.SocketException or TimeoutException or System.IO.IOException } n =>
            new UserError($"Server PostgreSQL non raggiungibile ({s.Host}:{s.Port}): {n.InnerException!.Message}"),
        NpgsqlException n => new UserError("PostgreSQL: " + n.Message),
        TimeoutException => new UserError($"Server PostgreSQL non raggiungibile ({s.Host}:{s.Port}): tempo scaduto."),
        _ => exc as UserError ?? new UserError("PostgreSQL: " + exc.Message),
    };

    async Task<T> Run<T>(Settings s, Func<NpgsqlConnection, Task<T>> work, CancellationToken ct)
    {
        try
        {
            await using var connection = await Source(s).OpenConnectionAsync(ct);
            return await work(connection);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception exc) when (exc is not UserError) { throw Error(exc, s); }
    }

    public string ServerUrl { get { var s = Load(); return $"postgresql://{s.User}@{s.Host}:{s.Port}/{s.Database}"; } }

    /// <summary>Tests the connection, lists the tables with time series and saves the settings on success.</summary>
    public async Task<Dictionary<string, object?>> Connect(JsonObject request, CancellationToken ct)
    {
        var saved = Load();
        var host = (request["host"].Str() is { Length: > 0 } h ? h : saved.Host).Trim();
        if (host.Length == 0) throw new UserError("Inserisci l'indirizzo del server PostgreSQL (ad esempio localhost).");
        var port = (int)(request["port"].Num() ?? saved.Port);
        if (port is < 1 or > 65535) throw new UserError("Porta PostgreSQL non valida (1–65535).");
        var database = (request["database"].Str() is { Length: > 0 } d ? d : saved.Database).Trim();
        if (database.Length == 0) throw new UserError("Inserisci il nome del database.");
        var user = (request["user"].Str() is { Length: > 0 } u ? u : saved.User).Trim();
        if (user.Length == 0) throw new UserError("Inserisci l'utente PostgreSQL.");
        // The saved password is reused only for the same server and user.
        var password = request["password"].Str() is { Length: > 0 } p ? p : saved.Host == host && saved.User == user && saved.Port == port ? saved.Password : "";
        var ssl = request["ssl"].Str() ?? saved.Ssl;
        if (ssl is not ("disable" or "prefer" or "require" or "verify-full")) throw new UserError("Modalità SSL non valida.");
        var zone = request["timezone"].Str() ?? saved.Zone;
        if (zone is not ("utc" or "local")) throw new UserError("Fuso orario non valido.");
        var draft = new Settings(host, port, database, user, password, ssl, zone);
        var (version, tables) = await Run(draft, async c =>
        {
            await using var command = new NpgsqlCommand("SELECT current_setting('server_version')", c);
            var v = (string?)await command.ExecuteScalarAsync(ct) ?? "";
            return (v, await Tables(c, null, null, ct));
        }, ct);
        Save(draft);
        return new Dictionary<string, object?>
        {
            ["ok"] = true, ["version"] = version, ["host"] = host, ["port"] = port, ["database"] = database, ["user"] = user, ["timezone"] = zone,
            ["timezone_name"] = ZoneName(zone), ["tables"] = tables.Select(t => (object?)t.ToDictionary()).ToList(), ["truncated"] = tables.Count >= MaxTables,
        };
    }

    Settings Connected()
    {
        var s = Load();
        if (s.Host.Length == 0 || s.Database.Length == 0 || s.User.Length == 0) throw new UserError("PostgreSQL non connesso: premi Connetti.");
        return s;
    }

    public sealed record Column(string Name, string Type);

    public sealed record TableInfo(string Schema, string Name, string Kind, List<Column> Times, List<Column> Values, List<Column> Labels)
    {
        public Dictionary<string, object?> ToDictionary() => new()
        {
            ["schema"] = Schema, ["name"] = Name, ["kind"] = Kind,
            ["time_columns"] = Times.Select(c => (object?)new Dictionary<string, object?> { ["name"] = c.Name, ["type"] = c.Type }).ToList(),
            ["value_columns"] = Values.Select(c => (object?)new Dictionary<string, object?> { ["name"] = c.Name, ["type"] = c.Type, ["data_type"] = ValueTypes[c.Type] }).ToList(),
            ["label_columns"] = Labels.Select(c => (object?)c.Name).ToList(),
        };
    }

    /// <summary>Tables, views and materialized views readable by the user that have at least one time column and one numeric or boolean column.</summary>
    static async Task<List<TableInfo>> Tables(NpgsqlConnection c, string? schema, string? table, CancellationToken ct)
    {
        const string sql = """
            SELECT n.nspname, c.relname, c.relkind::text, a.attname, t.typname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            JOIN pg_type t ON t.oid = a.atttypid
            WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')
              AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_toast%' AND n.nspname NOT LIKE '\_timescaledb%'
              AND (@schema::text IS NULL OR n.nspname = @schema) AND (@table::text IS NULL OR c.relname = @table)
              AND has_table_privilege(c.oid, 'SELECT')
            ORDER BY n.nspname, c.relname, a.attnum
            """;
        await using var command = new NpgsqlCommand(sql, c);
        command.Parameters.AddWithValue("schema", (object?)schema ?? DBNull.Value);
        command.Parameters.AddWithValue("table", (object?)table ?? DBNull.Value);
        var result = new List<TableInfo>();
        TableInfo? current = null;
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            var (ns, rel, kind, column, type) = (reader.GetString(0), reader.GetString(1), reader.GetString(2), reader.GetString(3), reader.GetString(4));
            if (current == null || current.Schema != ns || current.Name != rel)
            {
                if (current is { Times.Count: > 0, Values.Count: > 0 }) result.Add(current);
                if (result.Count >= MaxTables) break;
                current = new TableInfo(ns, rel, kind switch { "v" => "view", "m" => "materialized view", "f" => "foreign table", _ => "table" }, [], [], []);
            }
            if (TimeTypes.Contains(type)) current.Times.Add(new Column(column, type));
            else if (ValueTypes.ContainsKey(type)) current.Values.Add(new Column(column, type));
            if (LabelTypes.Contains(type)) current.Labels.Add(new Column(column, type));
        }
        if (current is { Times.Count: > 0, Values.Count: > 0 } && result.Count < MaxTables) result.Add(current);
        // Most likely time column first: time, timestamp, ts, datetime, then the declared order.
        foreach (var t in result)
            t.Times.Sort((a, b) => Rank(a.Name).CompareTo(Rank(b.Name)));
        return result;

        static int Rank(string name) => name.ToLowerInvariant() switch { "time" => 0, "timestamp" => 1, "ts" => 2, "datetime" => 3, "_time" => 4, _ => 10 };
    }

    // ---- SQL building (identifiers quoted, values as parameters) ---------------------------------------------------------

    public static string Ident(string name) => "\"" + name.Replace("\"", "\"\"") + "\"";

    public static string TableName(string schema, string table) => Ident(schema) + "." + Ident(table);

    /// <summary>Time bound with the column's own type, so that an index on the time column can be used.</summary>
    public static string Bound(string timeType, string parameter) => timeType switch
    {
        "timestamptz" => $"to_timestamp({parameter}::float8 / 1000)",
        "timestamp" => $"(to_timestamp({parameter}::float8 / 1000))::timestamp",
        "date" => $"(to_timestamp({parameter}::float8 / 1000))::date",
        _ => throw new UserError("Tipo della colonna del tempo non supportato: " + timeType),
    };

    /// <summary>Milliseconds since 1970 (UTC); timestamps without time zone are read in the session time zone (UTC or the PC's).</summary>
    public static string EpochMs(string timeColumn) => $"(extract(epoch from {timeColumn}::timestamptz) * 1000)::float8";

    public static string ValueExpression(string column, string type) =>
        type == "bool" ? $"(CASE WHEN {column} THEN 1.0 ELSE 0.0 END)" : $"{column}::float8";

    public static string AggregateExpression(string function, string value, string time) => function switch
    {
        "mean" => $"avg({value})",
        "min" => $"min({value})",
        "max" => $"max({value})",
        "sum" => $"sum({value})",
        "median" => $"percentile_cont(0.5) WITHIN GROUP (ORDER BY {value})",
        "first" => $"(array_agg({value} ORDER BY {time}))[1]",
        "last" => $"(array_agg({value} ORDER BY {time} DESC))[1]",
        _ => throw new UserError("Aggregazione non valida: " + function),
    };

    /// <summary>A series as the UI sends it back: table, time column, value column and optional label.</summary>
    public sealed record SeriesRef(string Schema, string Table, string Time, string TimeType, string Column, string ColumnType, string? LabelColumn, string? Label,
        string Key, string Name, string DataType)
    {
        public string From => TableName(Schema, Table);
        public string Where(string start, string end)
        {
            var sb = new StringBuilder($"{Ident(Time)} >= {Bound(TimeType, start)} AND {Ident(Time)} < {Bound(TimeType, end)} AND {Ident(Column)} IS NOT NULL");
            if (LabelColumn != null) sb.Append($" AND {Ident(LabelColumn)}::text = @label");
            return sb.ToString();
        }
    }

    public static string SeriesKey(string schema, string table, string time, string column, string? labelColumn, string? label) =>
        $"pg:{schema}.{table}[{time}]/{column}" + (labelColumn == null ? "" : "{" + labelColumn + "=" + label + "}");

    public static SeriesRef Describe(JsonObject item)
    {
        string Need(string key) => item[key].Str() is { Length: > 0 } v ? v : throw new UserError("Serie PostgreSQL incompleta: manca " + key + ".");
        var (schema, table, time, column) = (Need("schema"), Need("table"), Need("time_column"), Need("column"));
        var timeType = Need("time_type");
        if (!TimeTypes.Contains(timeType)) throw new UserError("Tipo della colonna del tempo non supportato: " + timeType);
        var columnType = Need("column_type");
        if (!ValueTypes.TryGetValue(columnType, out var dataType)) throw new UserError($"La colonna «{column}» non è numerica e non può essere disegnata.");
        var labelColumn = item["label_column"].Str() is { Length: > 0 } l ? l : null;
        var label = labelColumn == null ? null : item["label"].Str() ?? "";
        var name = table + "." + column + (labelColumn == null ? "" : " {" + labelColumn + "=" + label + "}");
        return new SeriesRef(schema, table, time, timeType, column, columnType, labelColumn, label, SeriesKey(schema, table, time, column, labelColumn, label), name, dataType);
    }

    static void Parameters(NpgsqlCommand command, SeriesRef s, double start, double end)
    {
        command.Parameters.AddWithValue("s", start);
        command.Parameters.AddWithValue("e", end);
        if (s.LabelColumn != null) command.Parameters.AddWithValue("label", s.Label ?? "");
    }

    // ---- catalog ----------------------------------------------------------------------------------------------------------

    /// <summary>Series of a table: one per numeric column, or one per numeric column and label value (labels with data in the look-back window).</summary>
    public async Task<Dictionary<string, object?>> Catalog(JsonObject request, CancellationToken ct)
    {
        var s = Connected();
        var schema = request["schema"].Str() ?? throw new UserError("Scegli una tabella.");
        var table = request["table"].Str() ?? throw new UserError("Scegli una tabella.");
        var lookback = (long)Math.Clamp(request["lookback_s"].Num() ?? 30 * 86400, 60, 3650L * 86400);
        var maxSeries = (int)Math.Clamp(request["max_series"].Num() ?? MaxSeriesDefault, 1, 20000);
        return await Run(s, async c =>
        {
            var info = (await Tables(c, schema, table, ct)).FirstOrDefault()
                ?? throw new UserError($"La tabella {schema}.{table} non esiste, non è leggibile o non ha colonne di tempo e numeriche.");
            var timeName = request["time_column"].Str() is { Length: > 0 } tn ? tn : info.Times[0].Name;
            var time = info.Times.FirstOrDefault(t => t.Name == timeName) ?? throw new UserError("Colonna del tempo non valida: " + timeName);
            var labelName = request["label_column"].Str() is { Length: > 0 } ln ? ln : null;
            var label = labelName == null ? null : info.Labels.FirstOrDefault(l => l.Name == labelName) ?? throw new UserError("Colonna delle etichette non valida: " + labelName);
            var values = info.Values.Where(v => v.Name != label?.Name && v.Name != time.Name).ToList();
            var t = Ident(time.Name);
            var since = $"{t} >= {Bound(time.Type, "@since")}";
            var sinceMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - lookback * 1000.0;
            var series = new List<Dictionary<string, object?>>();
            var truncated = false;
            Dictionary<string, object?> Entry(Column v, string? labelValue, double? last) => new()
            {
                ["key"] = SeriesKey(schema, table, time.Name, v.Name, label?.Name, labelValue), ["schema"] = schema, ["table"] = table,
                ["time_column"] = time.Name, ["time_type"] = time.Type, ["column"] = v.Name, ["column_type"] = v.Type, ["data_type"] = ValueTypes[v.Type],
                ["label_column"] = label?.Name, ["label"] = labelValue, ["last_ms"] = last,
            };
            if (label == null)
            {
                await using var command = new NpgsqlCommand($"SELECT {EpochMs("max(" + t + ")")} FROM {TableName(schema, table)}", c);
                var last = await command.ExecuteScalarAsync(ct) is double d ? d : (double?)null;
                foreach (var v in values.Take(maxSeries)) series.Add(Entry(v, null, last));
                truncated = values.Count > maxSeries;
            }
            else
            {
                var perLabel = Math.Max(1, values.Count);
                var limit = maxSeries / perLabel + 1;
                await using var command = new NpgsqlCommand(
                    $"SELECT {Ident(label.Name)}::text AS l, {EpochMs("max(" + t + ")")} FROM {TableName(schema, table)} WHERE {since} AND {Ident(label.Name)} IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT {limit}", c);
                command.Parameters.AddWithValue("since", sinceMs);
                await using var reader = await command.ExecuteReaderAsync(ct);
                var labels = new List<(string, double?)>();
                while (await reader.ReadAsync(ct)) labels.Add((reader.GetString(0), reader.IsDBNull(1) ? null : reader.GetDouble(1)));
                truncated = labels.Count >= limit;
                foreach (var (value, last) in labels.Take(limit - 1))
                    foreach (var v in values) series.Add(Entry(v, value, last));
            }
            return new Dictionary<string, object?>
            {
                ["schema"] = schema, ["table"] = table, ["time_column"] = time.Name, ["label_column"] = label?.Name, ["lookback_s"] = lookback,
                ["series"] = series, ["truncated"] = truncated,
            };
        }, ct);
    }

    // ---- time series ------------------------------------------------------------------------------------------------------

    sealed record Loaded(SampleColumns Columns, long Original, long Returned, double Window, string Aggregate);

    /// <summary>Time series for the charts. Few points: all of them. Many points: aggregated on the server by a round window (mean unless chosen otherwise).</summary>
    public async Task<SamplePayload> Samples(JsonObject request, CancellationToken ct)
    {
        var s = Connected();
        var end = request["end_ms"].Num() ?? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var start = request["start_ms"].Num() ?? end - 6 * 3600_000.0;
        if (!(end > start)) throw new UserError("L'intervallo di tempo non è valido: la fine deve seguire l'inizio.");
        var maxPoints = (int)Math.Clamp(request["max_points"].Num() ?? 4000, 100, 100_000);
        var aggregate = (request["aggregate"].Str() ?? "auto").ToLowerInvariant();
        if (aggregate != "auto" && aggregate != "raw" && !Aggregates.Contains(aggregate)) throw new UserError("Aggregazione non valida: " + aggregate);
        var items = (request["series"] as JsonArray ?? throw new UserError("Seleziona almeno una serie.")).OfType<JsonObject>().Take(64).Select(Describe).ToList();
        if (items.Count == 0) throw new UserError("Seleziona almeno una serie.");
        using var limit = new SemaphoreSlim(4);
        var loaded = await Task.WhenAll(items.Select(async item =>
        {
            await limit.WaitAsync(ct);
            try { return await Run(s, c => LoadSeries(c, item, start, end, maxPoints, aggregate, ct), ct); }
            finally { limit.Release(); }
        }));
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

    static async Task<long> Count(NpgsqlConnection c, SeriesRef s, double start, double end, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand($"SELECT count(*) FROM {s.From} WHERE {s.Where("@s", "@e")}", c);
        Parameters(command, s, start, end);
        return Convert.ToInt64(await command.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture);
    }

    async Task<Loaded> LoadSeries(NpgsqlConnection c, SeriesRef s, double start, double end, int maxPoints, string aggregate, CancellationToken ct)
    {
        var columns = new SampleColumns(s.Key) { DataType = s.DataType, Name = s.Name };
        var original = await Count(c, s, start, end, ct);
        var useRaw = aggregate == "raw" ? original <= RawHardLimit : original <= maxPoints;
        var window = 0.0;
        var function = "";
        var t = Ident(s.Time);
        var value = ValueExpression(Ident(s.Column), s.ColumnType);
        string sql;
        if (useRaw) sql = $"SELECT {EpochMs(t)}, {value} FROM {s.From} WHERE {s.Where("@s", "@e")} ORDER BY {t} LIMIT {RawHardLimit + 1}";
        else
        {
            window = InfluxService.WindowSeconds((end - start) / 1000.0, maxPoints);
            function = aggregate is "auto" or "raw" ? "mean" : aggregate;
            var w = window.ToString("0", CultureInfo.InvariantCulture);
            // Bucket of each point; the point of the chart is the centre of the window.
            sql = $"SELECT (floor({EpochMs(t)} / 1000 / {w}) * {w} + {w} / 2.0) * 1000 AS b, {AggregateExpression(function, value, t)} " +
                  $"FROM {s.From} WHERE {s.Where("@s", "@e")} GROUP BY 1 ORDER BY 1";
        }
        await using var command = new NpgsqlCommand(sql, c);
        Parameters(command, s, start, end);
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            if (reader.IsDBNull(0) || reader.IsDBNull(1)) continue;
            var v = Convert.ToDouble(reader.GetValue(1), CultureInfo.InvariantCulture);
            if (double.IsFinite(v)) columns.Add(Convert.ToDouble(reader.GetValue(0), CultureInfo.InvariantCulture), v, SampleColumns.Good);
        }
        columns.Sort();
        return new Loaded(columns, original, columns.Count, window, function);
    }

    // ---- export -----------------------------------------------------------------------------------------------------------

    public string Label => "PostgreSQL";
    public string Protocol => "postgresql";
    public const int MaxExportSeries = 64;

    public List<ExportSeries> Plan(JsonNode? series)
    {
        var items = (series as JsonArray)?.OfType<JsonObject>().ToList() ?? [];
        if (items.Count == 0) throw new UserError("Seleziona almeno una serie.");
        if (items.Count > MaxExportSeries) throw new UserError($"Si possono esportare al massimo {MaxExportSeries} serie (selezionate {items.Count}).");
        return items.Select(item => { var d = Describe(item); return new ExportSeries(d.Key, d.Name, d.DataType, item); }).ToList();
    }

    public Task<long> CountPoints(ExportSeries series, double start, double end, CancellationToken ct) =>
        Run(Connected(), c => Count(c, Describe(series.Item), start, end, ct), ct);

    public Task<List<(double Time, double Value)>> RawPoints(ExportSeries series, double start, double end, CancellationToken ct) =>
        Run(Connected(), async c =>
        {
            var s = Describe(series.Item);
            var t = Ident(s.Time);
            await using var command = new NpgsqlCommand($"SELECT {EpochMs(t)}, {ValueExpression(Ident(s.Column), s.ColumnType)} FROM {s.From} WHERE {s.Where("@s", "@e")} ORDER BY {t}", c);
            Parameters(command, s, start, end);
            var points = new List<(double, double)>();
            await using var reader = await command.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                if (reader.IsDBNull(0) || reader.IsDBNull(1)) continue;
                var v = Convert.ToDouble(reader.GetValue(1), CultureInfo.InvariantCulture);
                if (double.IsFinite(v)) points.Add((Convert.ToDouble(reader.GetValue(0), CultureInfo.InvariantCulture), v));
            }
            return points;
        }, ct);

    public void Dispose() => Reset();
}
