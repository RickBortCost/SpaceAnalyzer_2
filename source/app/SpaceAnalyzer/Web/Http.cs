using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Web;

/// <summary>Flask-compatible request/response helpers: JSON bodies, error mapping, security headers.</summary>
public static class Http
{
    public const string Csp = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'";
    static readonly JsonWriterOptions WriterOptions = new() { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping, SkipValidation = true };

    /// <summary>request.get_json(): JSON content type required unless silent.</summary>
    public static async Task<JsonNode?> Json(HttpContext ctx, bool silent = false)
    {
        var type = ctx.Request.ContentType ?? "";
        var json = type.StartsWith("application/json", StringComparison.OrdinalIgnoreCase) || Regex.IsMatch(type, @"^application/[^;]*\+json", RegexOptions.IgnoreCase);
        if (!json)
        {
            if (silent) return null;
            throw new HttpError(415, "415 Unsupported Media Type: Did not attempt to load JSON data because the request Content-Type was not 'application/json'.");
        }
        using var reader = new StreamReader(ctx.Request.Body, Encoding.UTF8);
        var text = await reader.ReadToEndAsync();
        try { return JsonNode.Parse(text, documentOptions: new JsonDocumentOptions { AllowTrailingCommas = false }); }
        catch (JsonException)
        {
            if (silent) return null;
            throw new HttpError(400, "400 Bad Request: The browser (or proxy) sent a request that this server could not understand.");
        }
    }

    /// <summary>request.get_json() or {} as an object (non-objects become empty).</summary>
    public static async Task<JsonObject> Object(HttpContext ctx, bool silent = false)
    {
        var node = await Json(ctx, silent);
        return node as JsonObject ?? new JsonObject();
    }

    public static async Task Write(HttpContext ctx, object? value, int status = 200)
    {
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json";
        using var buffer = new MemoryStream();
        using (var w = new Utf8JsonWriter(buffer, WriterOptions)) Py.WritePlain(w, value);
        buffer.WriteByte((byte)'\n');
        await ctx.Response.Body.WriteAsync(buffer.ToArray());
    }

    public static async Task WriteWith(HttpContext ctx, Action<Utf8JsonWriter> write, int status = 200)
    {
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json";
        using var buffer = new MemoryStream();
        using (var w = new Utf8JsonWriter(buffer, WriterOptions)) write(w);
        buffer.WriteByte((byte)'\n');
        await ctx.Response.Body.WriteAsync(buffer.ToArray());
    }

    public static Dictionary<string, object?> Ok(params (string key, object? value)[] items)
    {
        var result = new Dictionary<string, object?> { ["ok"] = true };
        foreach (var (k, v) in items) result[k] = v;
        return result;
    }

    /// <summary>Flask error handler: ValueError 400, HTTP errors their code, everything else 500 (logged).</summary>
    public static (int status, string message) Map(Exception exc)
    {
        var inner = exc is AggregateException { InnerException: { } i } ? i : exc;
        inner = inner is System.Reflection.TargetInvocationException { InnerException: { } t } ? t : inner;
        var message = inner.Message.Length > 0 ? inner.Message : inner.GetType().Name;
        // ERROR_DISK_FULL / ERROR_HANDLE_DISK_FULL: say which disk, instead of a generic I/O error.
        if (inner is IOException { HResult: var hr } && (hr & 0xFFFF) is 0x70 or 0x27)
            return (507, "Disco pieno: liberare spazio sul disco indicato e riprovare (" + message + ")");
        return inner switch
        {
            HttpError h => (h.Status, message),
            UserError or ArgumentException or FormatException or InvalidDataException or JsonException => (400, message),
            _ => (500, message),
        };
    }

    public static void SecurityHeaders(HttpContext ctx)
    {
        var headers = ctx.Response.Headers;
        headers.CacheControl = "no-store";
        headers["X-Content-Type-Options"] = "nosniff";
        headers["Referrer-Policy"] = "no-referrer";
        headers["Content-Security-Policy"] = Csp;
    }

    public static string Query(HttpContext ctx, string key, string fallback = "") => ctx.Request.Query.TryGetValue(key, out var v) && v.Count > 0 ? v[0] ?? fallback : fallback;

    /// <summary>int(request.args.get(key, default)): invalid text is a ValueError (400).</summary>
    public static int QueryInt(HttpContext ctx, string key, int fallback)
    {
        if (!ctx.Request.Query.TryGetValue(key, out var v) || v.Count == 0) return fallback;
        var text = (v[0] ?? "").Trim();
        if (int.TryParse(text, System.Globalization.NumberStyles.AllowLeadingSign, System.Globalization.CultureInfo.InvariantCulture, out var n)) return n;
        throw new UserError($"invalid literal for int() with base 10: '{v[0]}'");
    }
}

/// <summary>Jinja subset of the 2.x templates: {{ name }} (HTML-escaped) and {% include 'file' %}.</summary>
public static class Templates
{
    static readonly Regex Variable = new(@"\{\{\s*(\w+)\s*\}\}", RegexOptions.Compiled);
    static readonly Regex Include = new(@"\{%\s*include\s+'([^']+)'\s*%\}", RegexOptions.Compiled);

    public static string Render(string directory, string name, IReadOnlyDictionary<string, string> values, int depth = 0)
    {
        if (depth > 8) throw new InvalidOperationException("Inclusione template troppo profonda");
        var text = File.ReadAllText(Path.Combine(directory, name), Encoding.UTF8);
        text = Include.Replace(text, m => Render(directory, m.Groups[1].Value, values, depth + 1));
        return Variable.Replace(text, m => values.TryGetValue(m.Groups[1].Value, out var v) ? WebUtility.HtmlEncode(v).Replace("&#39;", "&#39;") : "");
    }
}
