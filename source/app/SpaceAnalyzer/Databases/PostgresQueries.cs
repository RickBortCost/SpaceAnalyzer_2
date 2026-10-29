using System.Globalization;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Npgsql;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Databases;

/// <summary>
/// Tabular view of PostgreSQL data (a table or view, or a SQL query written by the operator) and the saved queries.
/// Read-only by construction: a single SELECT / WITH / VALUES / TABLE statement, run in a READ ONLY transaction behind a server-side cursor
/// (so only the requested page is transferred), with a statement timeout.
/// </summary>
public sealed partial class PostgresService
{
    public const int MaxPageSize = 5000;
    public const int MaxSavedQueries = 200;
    public const int MaxSqlLength = 100_000;
    const int MaxCellText = 10_000;
    const string CursorPrefix = "DECLARE sfa_cur NO SCROLL CURSOR FOR ";
    static readonly HashSet<string> ReadOnlyStarts = ["SELECT", "WITH", "VALUES", "TABLE"];

    string QueriesPath => Path.Combine(dataDir, "postgres_queries.json");

    // ---- SQL checks -------------------------------------------------------------------------------------------------------

    /// <summary>The statement with comments, string literals and quoted identifiers blanked out (same length), to look at its structure only.</summary>
    public static string Skeleton(string sql)
    {
        var chars = sql.ToCharArray();
        void Blank(int from, int to) { for (var k = from; k <= Math.Min(to, chars.Length - 1); k++) chars[k] = ' '; }
        for (var i = 0; i < sql.Length; i++)
        {
            var ch = sql[i];
            var start = i;
            if (ch == '-' && i + 1 < sql.Length && sql[i + 1] == '-')
            {
                while (i < sql.Length && sql[i] != '\n') i++;
                Blank(start, i - 1);
            }
            else if (ch == '/' && i + 1 < sql.Length && sql[i + 1] == '*')
            {
                var depth = 1;
                i += 2;
                while (i < sql.Length && depth > 0)
                {
                    if (sql[i] == '/' && i + 1 < sql.Length && sql[i + 1] == '*') { depth++; i += 2; }
                    else if (sql[i] == '*' && i + 1 < sql.Length && sql[i + 1] == '/') { depth--; i += 2; }
                    else i++;
                }
                Blank(start, i - 1);
                i--;
            }
            else if (ch == '\'' || ch == '"')
            {
                // E'...' strings accept backslash escapes; the others only double the quote.
                var escapes = ch == '\'' && i > 0 && sql[i - 1] is 'E' or 'e' && (i < 2 || !(char.IsLetterOrDigit(sql[i - 2]) || sql[i - 2] == '_'));
                i++;
                while (i < sql.Length)
                {
                    if (escapes && sql[i] == '\\') { i += 2; continue; }
                    if (sql[i] == ch)
                    {
                        if (i + 1 < sql.Length && sql[i + 1] == ch) { i += 2; continue; }
                        break;
                    }
                    i++;
                }
                Blank(start, i);
            }
            else if (ch == '$')
            {
                var end = i + 1;
                while (end < sql.Length && (char.IsLetterOrDigit(sql[end]) || sql[end] == '_')) end++;
                if (end < sql.Length && sql[end] == '$' && (end == i + 1 || !char.IsDigit(sql[i + 1])))
                {
                    var tag = sql.Substring(i, end - i + 1);
                    var close = sql.IndexOf(tag, end + 1, StringComparison.Ordinal);
                    i = close < 0 ? sql.Length - 1 : close + tag.Length - 1;
                    Blank(start, i);
                }
            }
        }
        return new string(chars);
    }

    /// <summary>Validates a query typed by the operator and returns it without the final semicolon.</summary>
    public static string CheckSql(string? text)
    {
        var sql = (text ?? "").Trim();
        if (sql.Length == 0) throw new UserError("Scrivi la query SQL da eseguire.");
        if (sql.Length > MaxSqlLength) throw new UserError($"Query troppo lunga (massimo {MaxSqlLength:N0} caratteri).");
        var skeleton = Skeleton(sql);
        // Final semicolons (possibly followed by a comment) are not part of the statement.
        for (var trimmed = skeleton.TrimEnd(); trimmed.EndsWith(';'); trimmed = skeleton.TrimEnd())
        {
            sql = sql[..(trimmed.Length - 1)].TrimEnd();
            skeleton = Skeleton(sql);
        }
        if (skeleton.Contains(';')) throw new UserError("È ammessa una sola istruzione per volta: togli i punti e virgola intermedi.");
        var head = skeleton.TrimStart();
        var first = new string(head.TakeWhile(char.IsLetter).ToArray()).ToUpperInvariant();
        if (first.Length == 0 && head.StartsWith('(')) first = "SELECT";
        if (!ReadOnlyStarts.Contains(first))
            throw new UserError("Sono ammesse solo query di lettura che iniziano con SELECT, WITH, VALUES o TABLE.");
        return sql;
    }

    static string Literal(double value) =>
        double.IsFinite(value) ? value.ToString("R", CultureInfo.InvariantCulture) : throw new UserError("Intervallo temporale non valido");

    /// <summary>"Table" mode: every column of a table or view, newest rows first, optionally within the period of the chart.</summary>
    static string TableQuery(JsonObject request)
    {
        var schema = request["schema"].Str() is { Length: > 0 } s ? s : throw new UserError("Scegli una tabella.");
        var table = request["table"].Str() is { Length: > 0 } t ? t : throw new UserError("Scegli una tabella.");
        var sb = new StringBuilder($"SELECT * FROM {TableName(schema, table)}");
        var time = request["time_column"].Str() is { Length: > 0 } c ? c : null;
        if (time != null)
        {
            var type = request["time_type"].Str() ?? "";
            if (request["start_ms"].Num() is double start && request["end_ms"].Num() is double end)
            {
                if (!TimeTypes.Contains(type)) throw new UserError("Tipo della colonna del tempo non supportato: " + type);
                if (!(end > start)) throw new UserError("L'intervallo di tempo non è valido: la fine deve seguire l'inizio.");
                sb.Append($" WHERE {Ident(time)} >= {Bound(type, "(" + Literal(start) + ")")} AND {Ident(time)} < {Bound(type, "(" + Literal(end) + ")")}");
            }
            sb.Append($" ORDER BY {Ident(time)} {(request["order"].Str() == "asc" ? "ASC" : "DESC")}");
        }
        return sb.ToString();
    }

    // ---- values -----------------------------------------------------------------------------------------------------------

    static object? Cell(object? value)
    {
        switch (value)
        {
            case null or DBNull: return null;
            case bool or long or string: return value is string text && text.Length > MaxCellText ? text[..MaxCellText] + "…" : value;
            case short or int or byte or sbyte or ushort or uint: return Convert.ToInt64(value, CultureInfo.InvariantCulture);
            case ulong u: return u <= long.MaxValue ? (long)u : u.ToString(CultureInfo.InvariantCulture);
            case float f: return double.IsFinite(f) ? Convert.ToDouble(f.ToString("R", CultureInfo.InvariantCulture), CultureInfo.InvariantCulture) : f.ToString(CultureInfo.InvariantCulture);
            case double d: return double.IsFinite(d) ? d : d.ToString(CultureInfo.InvariantCulture);
            case decimal m: return m.ToString(CultureInfo.InvariantCulture);
            case BigInteger b: return b.ToString(CultureInfo.InvariantCulture);
            case DateTime dt: return dt.ToString("yyyy-MM-dd HH:mm:ss.FFFFFF", CultureInfo.InvariantCulture).TrimEnd('.') + (dt.Kind == DateTimeKind.Utc ? "Z" : "");
            case DateTimeOffset dto: return dto.UtcDateTime.ToString("yyyy-MM-dd HH:mm:ss.FFFFFF", CultureInfo.InvariantCulture).TrimEnd('.') + "Z";
            case DateOnly day: return day.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            case TimeOnly time: return time.ToString("HH:mm:ss.FFFFFF", CultureInfo.InvariantCulture).TrimEnd('.');
            case TimeSpan span: return span.ToString("c", CultureInfo.InvariantCulture);
            case Guid g: return g.ToString();
            case byte[] bytes: return "\\x" + Convert.ToHexString(bytes.AsSpan(0, Math.Min(bytes.Length, 32))).ToLowerInvariant() + (bytes.Length > 32 ? "… (" + bytes.Length + " byte)" : "");
            case Array array:
                var parts = new List<string>();
                foreach (var item in array) parts.Add(Cell(item)?.ToString() ?? "NULL");
                return "{" + string.Join(",", parts) + "}";
            default:
                var s = Convert.ToString(value, CultureInfo.InvariantCulture) ?? "";
                return s.Length > MaxCellText ? s[..MaxCellText] + "…" : s;
        }
    }

    // ---- run --------------------------------------------------------------------------------------------------------------

    /// <summary>One page of a table or of a query. Stateless: the cursor lives only inside the request; the page is reached with MOVE.</summary>
    public async Task<Dictionary<string, object?>> RunQuery(JsonObject request, CancellationToken ct)
    {
        var s = Connected();
        var mode = request["mode"].Str() ?? "query";
        var sql = mode switch { "table" => TableQuery(request), "query" => CheckSql(request["sql"].Str()), _ => throw new UserError("Modalità non valida.") };
        var size = (int)Math.Clamp(request["page_size"].Num() ?? 500, 10, MaxPageSize);
        var page = (int)Math.Clamp(request["page"].Num() ?? 1, 1, 1_000_000);
        var timeout = (int)Math.Clamp(request["timeout_s"].Num() ?? 60, 5, 600);
        var offset = (long)(page - 1) * size;
        var started = System.Diagnostics.Stopwatch.StartNew();
        return await Run(s, async c =>
        {
            await using var transaction = await c.BeginTransactionAsync(ct);
            try
            {
                async Task Exec(string text)
                {
                    await using var command = new NpgsqlCommand(text, c, transaction);
                    await command.ExecuteNonQueryAsync(ct);
                }
                await Exec("SET TRANSACTION READ ONLY");
                await Exec($"SET LOCAL statement_timeout = {timeout * 1000}");
                await Exec(CursorPrefix + sql);
                if (offset > 0) await Exec($"MOVE FORWARD {offset} IN sfa_cur");
                await using var fetch = new NpgsqlCommand($"FETCH FORWARD {size + 1} FROM sfa_cur", c, transaction);
                await using var reader = await fetch.ExecuteReaderAsync(ct);
                var columns = new List<object?>();
                for (var i = 0; i < reader.FieldCount; i++)
                    columns.Add(new Dictionary<string, object?> { ["name"] = reader.GetName(i), ["type"] = reader.GetDataTypeName(i) });
                var rows = new List<object?>();
                var hasMore = false;
                while (await reader.ReadAsync(ct))
                {
                    if (rows.Count >= size) { hasMore = true; break; }
                    var row = new object?[reader.FieldCount];
                    for (var i = 0; i < row.Length; i++) row[i] = reader.IsDBNull(i) ? null : Cell(reader.GetValue(i));
                    rows.Add(row);
                }
                return new Dictionary<string, object?>
                {
                    ["mode"] = mode, ["sql"] = sql, ["columns"] = columns, ["rows"] = rows, ["page"] = page, ["page_size"] = size,
                    ["first_row"] = offset + 1, ["row_count"] = rows.Count, ["has_more"] = hasMore, ["elapsed_ms"] = started.ElapsedMilliseconds,
                };
            }
            catch (PostgresException exc) when (exc.Position > 0 && exc.SqlState.StartsWith("42", StringComparison.Ordinal))
            {
                var at = exc.Position - CursorPrefix.Length;
                throw new UserError("PostgreSQL: " + exc.MessageText + (at > 0 ? $" (carattere {at} della query)" : ""));
            }
            catch (PostgresException exc) when (exc.SqlState == "57014")
            {
                throw new UserError($"PostgreSQL: la query supera il tempo massimo di {timeout} s: restringi la query o il periodo.");
            }
            catch (PostgresException exc) when (exc.SqlState == "25006" || exc.MessageText.Contains("data-modifying", StringComparison.OrdinalIgnoreCase))
            {
                throw new UserError("PostgreSQL: la query tenta di modificare i dati; l'applicazione è in sola lettura (" + exc.MessageText + ").");
            }
        }, ct);
    }

    // ---- saved queries ----------------------------------------------------------------------------------------------------

    List<JsonObject> LoadQueries()
    {
        try
        {
            if (File.Exists(QueriesPath) && Py.Parse(File.ReadAllText(QueriesPath)) is JsonObject o && o["queries"] is JsonArray list)
                return list.OfType<JsonObject>().Where(q => q["id"].Str() is { Length: > 0 } && q["name"].Str() is { Length: > 0 }).ToList();
        }
        catch (Exception exc) { Log.Warning("Query PostgreSQL salvate non leggibili: " + exc.Message); }
        return [];
    }

    void SaveQueries(List<JsonObject> queries)
    {
        Directory.CreateDirectory(dataDir);
        var array = new JsonArray();
        foreach (var q in queries) array.Add(JsonNode.Parse(q.ToJsonString()));
        Py.AtomicJson(QueriesPath, new JsonObject { ["queries"] = array });
    }

    static Dictionary<string, object?> Entry(JsonObject q) => new()
    {
        ["id"] = q["id"].Str(), ["name"] = q["name"].Str(), ["sql"] = q["sql"].Str() ?? "", ["page_size"] = q["page_size"].Num(), ["updated_at"] = q["updated_at"].Str(),
    };

    public Dictionary<string, object?> Queries()
    {
        lock (gate)
            return new Dictionary<string, object?>
            {
                ["queries"] = LoadQueries().OrderBy(q => q["name"].Str(), StringComparer.CurrentCultureIgnoreCase).Select(q => (object?)Entry(q)).ToList(),
            };
    }

    /// <summary>Creates or updates a saved query. A new query with the name of an existing one needs <c>overwrite</c>.</summary>
    public Dictionary<string, object?> SaveQuery(JsonObject request)
    {
        var name = (request["name"].Str() ?? "").Trim();
        if (name.Length == 0) throw new UserError("Dai un nome alla query.");
        if (name.Length > 80) throw new UserError("Il nome della query può avere al massimo 80 caratteri.");
        var sql = CheckSql(request["sql"].Str());
        var id = request["id"].Str();
        var overwrite = request["overwrite"].Bool() ?? false;
        var size = request["page_size"].Num() is double n ? (int)Math.Clamp(n, 10, MaxPageSize) : (int?)null;
        lock (gate)
        {
            var queries = LoadQueries();
            var target = id == null ? null : queries.FirstOrDefault(q => q["id"].Str() == id);
            var sameName = queries.FirstOrDefault(q => string.Equals(q["name"].Str(), name, StringComparison.CurrentCultureIgnoreCase));
            if (sameName != null && sameName != target)
            {
                if (!overwrite) throw new UserError($"Esiste già una query chiamata «{sameName["name"].Str()}»: conferma per sostituirla.");
                if (target != null) queries.Remove(target);
                target = sameName;
            }
            if (target == null)
            {
                if (queries.Count >= MaxSavedQueries) throw new UserError($"Si possono salvare al massimo {MaxSavedQueries} query: eliminane qualcuna.");
                target = new JsonObject { ["id"] = Convert.ToHexString(RandomNumberGenerator.GetBytes(6)).ToLowerInvariant() };
                queries.Add(target);
            }
            target["name"] = name;
            target["sql"] = sql;
            target["page_size"] = size;
            target["updated_at"] = Engine.Core.Values.PythonIso(DateTime.UtcNow);
            SaveQueries(queries);
            var result = Queries();
            result["saved"] = Entry(target);
            return result;
        }
    }

    public Dictionary<string, object?> DeleteQuery(JsonObject request)
    {
        var id = request["id"].Str() ?? throw new UserError("Query non indicata.");
        lock (gate)
        {
            var queries = LoadQueries();
            if (queries.RemoveAll(q => q["id"].Str() == id) == 0) throw new UserError("Query non trovata.");
            SaveQueries(queries);
            return Queries();
        }
    }
}
