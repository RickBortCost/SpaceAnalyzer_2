using System.Globalization;
using System.Reflection;
using System.Text.Json;
using S7CommPlusDriver;
using S7CommPlusDriver.ClientApi;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.S7;

/// <summary>
/// Read-only symbolic S7-1200/1500 access over S7CommPlus (DotNetProjects.S7CommPlusDriver,
/// LGPL-3.0, separate DLL). TLS or legacy challenge authentication; writes and CPU control
/// stay disabled (WriteEnabled = false).
/// </summary>
public sealed class S7SymbolicDriver : SourceDriver
{
    const int MaxSymbols = 50000;
    readonly SemaphoreSlim gate = new(1, 1);
    S7CommPlusClient? client;
    SymbolTree? tree;
    readonly Dictionary<string, PlcTag> resolved = new();

    public S7SymbolicDriver(JsonElement cfg) : base(cfg)
    {
        Info["transport"] = "S7CommPlus · simbolico";
        Info["read_mode"] = "S7 simbolico · lettura aggregata";
        Info["mode"] = "symbolic";
        Info["provider"] = "DotNetProjects.S7CommPlusDriver 5.0.0";
        Info["read_only"] = true;
        Info["timestamps"] = "Timestamp locale di ricezione; GetMultiVariables non fornisce il timestamp PLC";
        Info["quality_origin"] = "Esito lettura simbolica; stato RUN/STOP non interrogato";
    }

    public override string Protocol => "s7";

    public override async Task ConnectAsync(CancellationToken ct)
    {
        var tls = Flag("s7_symbolic_tls");
        foreach (var key in new[] { "s7_tls_ca", "s7_tls_cert", "s7_tls_key" })
            if (Text(key).Length > 0 && !File.Exists(Text(key))) throw new SourceException("File TLS non trovato: " + Text(key));
        var timeout = TimeSpan.FromMilliseconds(Int("s7_timeout_ms", 3000));
        client = new S7CommPlusClient(new S7CommPlusClientOptions
        {
            Address = Text("s7_host"),
            Port = Int("s7_tcp_port", 102),
            Password = Text("s7_symbolic_password") is { Length: > 0 } password ? password : null!,
            SecurityMode = tls ? S7CommPlusSecurityMode.Tls : S7CommPlusSecurityMode.LegacyChallenge,
            LegacyPublicKeyFallbackEnabled = Flag("s7_legacy_key_fallback", true),
            ConnectTimeout = timeout,
            RequestTimeout = timeout,
            BrowseTimeout = TimeSpan.FromSeconds(Math.Max(30, timeout.TotalSeconds)),
            AutoReconnect = false,
            WriteEnabled = false,
        });
        try
        {
            await client.ConnectAsync(ct);
        }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            await DisconnectAsync();
            throw new SourceException("S7 simbolico: connessione non riuscita (" + exc.Message + ")");
        }
        Info["host"] = Text("s7_host");
        Info["port"] = (long)Int("s7_tcp_port", 102);
        Info["tls"] = client.Options.NegotiatedSecurityMode == S7CommPlusSecurityMode.Tls;
        Info["security"] = client.Options.NegotiatedSecurityMode?.ToString();
        // The provider has no CA option: a TLS session is encrypted but the PLC certificate is not verified.
        Info["certificate_verified"] = false;
        Info["symbols"] = 0L;
    }

    public override async Task DisconnectAsync()
    {
        var c = client;
        client = null;
        if (c == null) return;
        try { await c.DisconnectAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
        try { await c.DisposeAsync(); } catch (Exception) { }
    }

    public override Task ReadStateAsync(CancellationToken ct)
    {
        if (client is not { IsConnected: true }) throw new SourceException("Sessione S7CommPlus interrotta");
        return Task.CompletedTask;
    }

    /// <summary>S7COMMP_SOFTDATATYPE_* constant → the type name used by the 2.x catalog (REAL, BOOL, ...).</summary>
    static readonly Dictionary<uint, string> TypeNames = typeof(Softdatatype).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(f => f.FieldType == typeof(uint) && f.Name.StartsWith("S7COMMP_SOFTDATATYPE_", StringComparison.Ordinal))
        .GroupBy(f => (uint)f.GetValue(null)!).ToDictionary(g => g.Key, g => g.First().Name["S7COMMP_SOFTDATATYPE_".Length..]);

    public static string TypeName(uint softdatatype) => TypeNames.TryGetValue(softdatatype, out var name) ? name : "UNKNOWN";

    /// <summary>Reload the CPU symbol table (ensure_catalog(refresh=True)); returns the symbol count.</summary>
    public async Task<int> RefreshCatalogAsync(CancellationToken ct)
    {
        await Locked(() => EnsureCatalog(ct, true), ct);
        return tree!.Catalog.Count;
    }

    async Task EnsureCatalog(CancellationToken ct, bool refresh = false)
    {
        if (tree != null && !refresh) return;
        var c = client ?? throw new SourceException("Sessione S7CommPlus interrotta");
        var variables = await c.BrowseAsync(new S7CommPlusBrowseOptions { ExpandPrimitiveArrayElements = true }, ct);
        if (variables.Count > MaxSymbols) throw new SourceException("Il PLC espone oltre 50000 simboli: restringere il programma da acquisire");
        var items = new List<Dictionary<string, object?>>();
        foreach (var v in variables)
        {
            var nid = "s7:sym:" + v.Name;
            S7Types.ParseNode(nid);
            var kind = TypeName(v.Softdatatype);
            items.Add(new()
            {
                ["nodeid"] = nid, ["display_name"] = v.Name[(v.Name.LastIndexOf('.') + 1)..], ["browse_name"] = v.Name, ["path"] = v.Name,
                ["data_type"] = kind, ["node_class"] = "Variable", ["has_children"] = false, ["comment"] = v.Name + " · " + kind,
            });
        }
        tree = new SymbolTree(items);
        resolved.Clear();
        Info["symbols"] = (long)tree.Catalog.Count;
    }

    public override async Task<object?> InspectAsync(string nodeId, CancellationToken ct)
    {
        var node = S7Types.ParseNode(nodeId, true);
        if (node.Mode != "symbolic") throw new SourceException("In modalità simbolica scegliere un simbolo S7");
        await Locked(() => EnsureCatalog(ct), ct);
        if (node.Folder)
        {
            if (node.Name.Length > 0 && !tree!.Children.ContainsKey(node.Name)) throw new SourceException("Ramo simbolico non trovato nel PLC");
            return new Dictionary<string, object?>
            {
                ["nodeid"] = nodeId, ["display_name"] = node.Name[(node.Name.LastIndexOf('.') + 1)..] is { Length: > 0 } n ? n : "Simboli CPU S7",
                ["path"] = node.Name, ["node_class"] = "Object", ["has_children"] = true,
            };
        }
        if (!tree!.Catalog.TryGetValue(nodeId, out var item)) throw new SourceException("Simbolo non trovato nel PLC: " + node.Name);
        return new Dictionary<string, object?>(item);
    }

    public override async Task<object?> BrowseAsync(string nodeId, CancellationToken ct)
    {
        var node = S7Types.ParseNode(nodeId, true);
        await InspectAsync(nodeId, ct);
        return node.Folder ? tree!.Browse(node.Name).Select(i => (object?)new Dictionary<string, object?>(i)).ToList() : new List<object?>();
    }

    public override async Task<object?> ScanItemsAsync(string root, CancellationToken ct)
    {
        await InspectAsync(root, ct);
        return tree!.Scan(S7Types.ParseNode(root, true)).Select(i => (object?)new Dictionary<string, object?>(i)).ToList();
    }

    async Task Locked(Func<Task> work, CancellationToken ct)
    {
        await gate.WaitAsync(ct);
        try { await work(); } finally { gate.Release(); }
    }

    public override async Task<List<Sample>> ReadValuesAsync(IReadOnlyList<string> ids, CancellationToken ct)
    {
        var timeTag = Text("s7_symbolic_time_tag");
        if (timeTag.Length == 0) return await ReadGroupAsync(ids, ct);
        // One GetMultiVariables request reads the time tag together with the values.
        var read = await ReadGroupAsync(S7PlcTime.Plan(ids, timeTag, false), ct);
        var rows = S7PlcTime.Collect(ids, timeTag, read);
        S7PlcTime.Apply(rows, read[0], null, S7PlcTime.Unit(Text("s7_time_unit", "us")));
        return rows;
    }

    async Task<List<Sample>> ReadGroupAsync(IReadOnlyList<string> ids, CancellationToken ct)
    {
        foreach (var nid in ids)
            if (S7Types.ParseNode(nid).Mode != "symbolic") throw new SourceException("Segnale incompatibile con S7 simbolico");
        var rows = new Dictionary<string, Sample>();
        await Locked(async () =>
        {
            await EnsureCatalog(ct);
            var c = client ?? throw new SourceException("Sessione S7CommPlus interrotta");
            var known = ids.Where(n => tree!.Catalog.ContainsKey(n)).Distinct().ToList();
            var missing = known.Where(n => !resolved.ContainsKey(n)).Select(n => n[7..]).ToList();
            if (missing.Count > 0)
            {
                var found = await c.GetTagsBySymbolsAsync(missing, ct);
                foreach (var (symbol, tag) in found) resolved["s7:sym:" + symbol] = tag;
            }
            var tags = known.Where(resolved.ContainsKey).ToList();
            if (tags.Count > 0)
            {
                var result = await c.ReadAsync(tags.Select(n => resolved[n]), ct);
                for (var i = 0; i < tags.Count; i++)
                {
                    var item = result.Items[i];
                    var kind = TypeName(resolved[tags[i]].Datatype);
                    if (!item.IsSuccess) { rows[tags[i]] = S7Rows.ValueRow(tags[i], kind, error: "Errore lettura simbolica 0x" + item.ItemError.ToString("X", CultureInfo.InvariantCulture), quality: "Bad"); continue; }
                    try { rows[tags[i]] = S7Rows.ValueRow(tags[i], kind, Convert(kind, item.Tag)); }
                    catch (ArgumentException exc) { rows[tags[i]] = S7Rows.ValueRow(tags[i], kind, error: exc.Message, quality: "Bad"); }
                }
            }
        }, ct);
        var local = Values.PythonIso(DateTime.UtcNow);
        var output = new List<Sample>(ids.Count);
        foreach (var nid in ids)
        {
            var row = rows.TryGetValue(nid, out var r) ? r : S7Rows.ValueRow(nid, "Null", error: "Simbolo non presente nel catalogo CPU", quality: "Bad");
            row.LocalTs = local;
            output.Add(row);
        }
        return output;
    }

    /// <summary>The value of s7_symbolic.decode_symbol for the provider's decoded tag.</summary>
    public static object? Convert(string kind, PlcTag tag)
    {
        var value = tag.GetValue();
        switch (value)
        {
            case bool b: return b;
            case float f: return Finite(f);
            case double d: return Finite(d);
            case sbyte or byte or short or ushort or int or uint or long: return System.Convert.ToInt64(value, CultureInfo.InvariantCulture);
            case ulong u: return u <= long.MaxValue ? (long)u : new System.Numerics.BigInteger(u);
            case string s: return s;
            case char ch: return ch.ToString();
            case DateTime dt when kind == "DATE": return (long)(dt.Date - new DateTime(1990, 1, 1)).TotalDays;
            case DateTime dt when kind == "DTL": return Dtl(dt);
            case DateTime dt: return DateAndTime(dt);
            case byte[] raw: return System.Convert.ToHexString(raw);
            case TimeSpan span when kind == "S5TIME": return (long)span.TotalMilliseconds;
            default: return value?.ToString();
        }
    }

    static double Finite(double value) => double.IsFinite(value) ? value : throw new ArgumentException("Valore PLC non finito");

    /// <summary>DATE_AND_TIME: 8 BCD bytes (year 1990–2089 … weekday), as the raw hex of the 2.x reader.</summary>
    static string DateAndTime(DateTime dt)
    {
        static byte Bcd(int v) => (byte)(v / 10 << 4 | v % 10);
        var ms = dt.Millisecond;
        var bytes = new[] { Bcd(dt.Year % 100), Bcd(dt.Month), Bcd(dt.Day), Bcd(dt.Hour), Bcd(dt.Minute), Bcd(dt.Second),
            Bcd(ms / 10), (byte)((ms % 10) << 4 | ((int)dt.DayOfWeek + 1)) };
        return System.Convert.ToHexString(bytes);
    }

    /// <summary>DTL: 12 bytes (year, month, day, weekday, hour, minute, second, nanoseconds).</summary>
    static string Dtl(DateTime dt)
    {
        var ns = (uint)(dt.Ticks % TimeSpan.TicksPerSecond * 100);
        var bytes = new byte[] { (byte)(dt.Year >> 8), (byte)dt.Year, (byte)dt.Month, (byte)dt.Day, (byte)((int)dt.DayOfWeek + 1),
            (byte)dt.Hour, (byte)dt.Minute, (byte)dt.Second, (byte)(ns >> 24), (byte)(ns >> 16), (byte)(ns >> 8), (byte)ns };
        return System.Convert.ToHexString(bytes);
    }
}

/// <summary>Flat resolved leaves → lazy UI branches; paths are never shortened as IDs (s7_catalog.SymbolTree).</summary>
public sealed class SymbolTree
{
    public Dictionary<string, Dictionary<string, object?>> Catalog { get; } = new();
    public Dictionary<string, Dictionary<string, Dictionary<string, object?>>> Children { get; } = new() { [""] = new() };

    public SymbolTree(IEnumerable<Dictionary<string, object?>> items)
    {
        foreach (var item in items)
        {
            Catalog[(string)item["nodeid"]!] = item;
            var parts = ((string)item["path"]!).Split('.');
            var parent = "";
            for (var i = 0; i < parts.Length; i++)
            {
                var name = string.Join('.', parts[..(i + 1)]);
                var leaf = i == parts.Length - 1;
                var entry = leaf ? new Dictionary<string, object?>(item) : new Dictionary<string, object?>
                {
                    ["nodeid"] = "s7:sym:/" + name, ["display_name"] = parts[i], ["node_class"] = "Object", ["has_children"] = true, ["path"] = name,
                };
                if (!Children.TryGetValue(parent, out var siblings)) Children[parent] = siblings = new();
                siblings[(string)entry["nodeid"]!] = entry;
                if (!leaf && !Children.ContainsKey(name)) Children[name] = new();
                parent = name;
            }
        }
    }

    public IEnumerable<Dictionary<string, object?>> Browse(string folder) =>
        Children.TryGetValue(folder, out var c) ? c.Values : Enumerable.Empty<Dictionary<string, object?>>();

    public IEnumerable<Dictionary<string, object?>> Scan(S7Tag node)
    {
        if (!node.Folder) return [Catalog[node.NodeId]];
        return Catalog.Values.Where(r => node.Name.Length == 0 || ((string)r["path"]!).StartsWith(node.Name + ".", StringComparison.Ordinal));
    }
}
