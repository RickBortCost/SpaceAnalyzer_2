using System.Buffers.Binary;
using System.Globalization;
using System.Text;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>
/// Read-only ADS client logic (port of ads_client.ADSClient): symbol information, handles,
/// SumRead with individual fallback, online-change detection and symbolic browsing.
/// ADS Write is used only to release previously acquired symbol handles.
/// </summary>
public sealed class AdsClient
{
    readonly JsonElement cfg;
    public IAdsTransport Transport { get; }
    public Dictionary<string, AdsSymbol> Symbols { get; } = new();
    public Dictionary<string, uint> Handles { get; } = new();
    public bool CatalogLoaded { get; private set; }
    public System.Collections.Concurrent.ConcurrentDictionary<string, object?> Info { get; } = new();
    public DatatypeBrowser Datatypes { get; private set; }
    byte[]? symbolVersion;
    bool sumSupported = true;
    readonly string encoding;
    public string Mode { get; }

    public AdsClient(JsonElement cfg, bool catalog = false)
    {
        this.cfg = cfg;
        string Text(string k, string f = "") => cfg.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() ?? f : f;
        int Int(string k, int f) => cfg.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Number ? (int)v.GetDouble() : f;
        encoding = Text("ads_encoding", "cp1252");
        Mode = Text("ads_mode", "sum");
        var timeout = Int("ads_timeout_ms", 1000);
        Transport = Text("ads_transport", "tcp") == "native"
            ? new AdsNativeTransport(Text("ads_dll_path"), Text("ads_target_netid"), Int("ads_port", 851), timeout)
            : new AdsTcpTransport(Text("ads_host"), Int("ads_tcp_port", 48898), Text("ads_target_netid"), Int("ads_port", 851),
                Text("ads_source_netid"), Int("ads_source_port", 32905) + (catalog ? 1 : 0), Text("ads_bind_ip"), timeout);
        Datatypes = new DatatypeBrowser(this);
    }

    public static void CheckResult(byte[] reply)
    {
        if (reply.Length < 4) throw new InvalidDataException("Risposta ADS troncata");
        var result = BinaryPrimitives.ReadUInt32LittleEndian(reply);
        if (result != 0) throw new AdsException(result);
    }

    public async Task Connect(CancellationToken ct)
    {
        try
        {
            await Transport.Open(ct);
            foreach (var (k, v) in Transport.Info) Info[k] = v;
            Info["target_netid"] = cfg.GetProperty("ads_target_netid").GetString();
            Info["target_port"] = (long)cfg.GetProperty("ads_port").GetDouble();
            var device = await Transport.Command(1, [], ct);
            CheckResult(device);
            if (device.Length != 24) throw new InvalidDataException("ReadDeviceInfo ADS non valido");
            var name = device[8..24];
            var end = Array.IndexOf(name, (byte)0);
            Info["device"] = Encoding.UTF8.GetString(name, 0, end < 0 ? name.Length : end);
            Info["version"] = $"{device[4]}.{device[5]}.{BinaryPrimitives.ReadUInt16LittleEndian(device.AsSpan(6))}";
            await ReadState(ct);
        }
        catch
        {
            await Transport.Abort();
            throw;
        }
    }

    public async Task<byte[]> Read(uint group, uint offset, uint size, CancellationToken ct)
    {
        var request = new byte[12];
        BinaryPrimitives.WriteUInt32LittleEndian(request, group);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(4), offset);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(8), size);
        return ReadReply(await Transport.Command(2, request, ct), size);
    }

    public async Task<byte[]> ReadWrite(uint group, uint offset, uint size, byte[] data, CancellationToken ct)
    {
        var request = new byte[16 + data.Length];
        BinaryPrimitives.WriteUInt32LittleEndian(request, group);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(4), offset);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(8), size);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(12), (uint)data.Length);
        data.CopyTo(request, 16);
        return ReadReply(await Transport.Command(9, request, ct), size);
    }

    static byte[] ReadReply(byte[] reply, uint maximum)
    {
        CheckResult(reply);
        if (reply.Length < 8) throw new InvalidDataException("Risposta ADS Read troncata");
        var size = BinaryPrimitives.ReadUInt32LittleEndian(reply.AsSpan(4));
        if (size > maximum || size != reply.Length - 8) throw new InvalidDataException("Lunghezza ADS Read non valida");
        return reply[8..];
    }

    public uint? State => Info.GetValueOrDefault("ads_state") is long s ? (uint)s : null;

    public async Task<uint> ReadState(CancellationToken ct)
    {
        var data = await Transport.Command(4, [], ct);
        CheckResult(data);
        if (data.Length != 8) throw new InvalidDataException("ReadState ADS non valido");
        var state = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(4));
        Info["ads_state"] = (long)state;
        Info["ads_state_name"] = AdsProtocol.States.GetValueOrDefault(state, state.ToString(CultureInfo.InvariantCulture));
        Info["device_state"] = (long)BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(6));
        return state;
    }

    /// <summary>Online change: a new symbol version invalidates handles, symbols and datatypes.</summary>
    public async Task<bool> CheckSymbols(CancellationToken ct)
    {
        try
        {
            var version = await Read(0xF008, 0, 4, ct);
            var changed = symbolVersion != null && !version.AsSpan().SequenceEqual(symbolVersion);
            if (changed)
            {
                await ReleaseHandles(ct);
                Symbols.Clear(); CatalogLoaded = false; Datatypes = new DatatypeBrowser(this);
                Info["symbol_changes"] = (Info.GetValueOrDefault("symbol_changes") as long? ?? 0) + 1;
            }
            symbolVersion = version;
            return changed;
        }
        catch (AdsException exc) when (exc.Code is 1793 or 1794) { return false; }
    }

    public async Task<AdsSymbol> GetSymbol(string name, CancellationToken ct)
    {
        if (!Symbols.TryGetValue(name, out var symbol))
        {
            try
            {
                symbol = AdsProtocol.ParseSymbol(await ReadWrite(0xF009, 0, 65536, [.. Encoding.UTF8.GetBytes(name), 0], ct)).symbol;
            }
            catch (AdsException exc) when (exc.Code == 1808)
            {
                try { symbol = await Datatypes.Resolve(name, ct); }
                catch (InvalidDataException) { throw exc; }
            }
            // Read by requested name (case-sensitive runtimes may return qualified aliases).
            symbol.Name = name;
            Symbols[name] = symbol;
        }
        return Symbols[name];
    }

    public async Task<List<AdsSymbol>> UploadSymbols(CancellationToken ct)
    {
        var data = await Read(0xF00C, 0, 8, ct);
        if (data.Length != 8) throw new InvalidDataException("UploadInfo ADS non valido");
        var count = BinaryPrimitives.ReadUInt32LittleEndian(data);
        var size = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4));
        if (size > AdsProtocol.MaxPacket - 40 || count > 1000000) throw new InvalidDataException("Tabella ADS troppo grande (massimo 64 MiB / 1 milione di simboli)");
        var table = await Read(0xF00B, 0, size, ct);
        var pos = 0;
        var symbols = new Dictionary<string, AdsSymbol>();
        var order = new List<AdsSymbol>();
        for (var i = 0; i < count; i++)
        {
            var (s, n) = AdsProtocol.ParseSymbol(table, pos);
            pos += n;
            if (!symbols.ContainsKey(s.Name)) order.Add(s);
            else order[order.FindIndex(x => x.Name == s.Name)] = s;
            symbols[s.Name] = s;
        }
        if (pos != table.Length) throw new InvalidDataException("Conteggio tabella simboli ADS non valido");
        foreach (var (k, v) in symbols) Symbols[k] = v;
        CatalogLoaded = true;
        Info["symbol_count"] = (long)count;
        return order;
    }

    public async Task<Dictionary<string, object?>> Inspect(string nodeId, CancellationToken ct)
    {
        var name = AdsProtocol.SymbolName(nodeId);
        if (name == "/") return new() { ["nodeid"] = "ads:/", ["display_name"] = "Simboli ADS", ["browse_name"] = "ADS", ["node_class"] = "Object", ["has_children"] = true };
        try { return AdsProtocol.SymbolItem(await GetSymbol(name, ct)); }
        catch (AdsException exc) when (exc.Code == 1808)
        {
            if (!CatalogLoaded) await UploadSymbols(ct);
            if (Symbols.Keys.Any(n => n.StartsWith(name + ".", StringComparison.Ordinal)))
                return new() { ["nodeid"] = nodeId, ["display_name"] = name, ["browse_name"] = name, ["node_class"] = "Object", ["has_children"] = true };
            throw;
        }
    }

    public async Task<Dictionary<string, object?>> BrowsePage(string nodeId, long offset, long count, CancellationToken ct)
    {
        offset = Math.Max(0, offset); count = Math.Max(1, Math.Min(1000, count));
        var name = AdsProtocol.SymbolName(nodeId);
        if (!CatalogLoaded) await UploadSymbols(ct);
        var prefix = name == "/" ? "" : name + ".";
        var nodes = new Dictionary<string, Dictionary<string, object?>>();
        string? warning = null;
        foreach (var (n, s) in Symbols.ToList())
        {
            if (!n.StartsWith(prefix, StringComparison.Ordinal)) continue;
            var part = n[prefix.Length..].Split('.')[0];
            var full = prefix + part;
            if (full == n) nodes[full] = AdsProtocol.SymbolItem(s);
            else if (!nodes.ContainsKey(full)) nodes[full] = new() { ["nodeid"] = "ads:" + full, ["display_name"] = full, ["browse_name"] = full, ["node_class"] = "Object", ["has_children"] = true };
        }
        if (name != "/")
        {
            AdsSymbol? symbol = null;
            try { symbol = await GetSymbol(name, ct); }
            catch (AdsException exc) when (exc.Code == 1808 && nodes.Count > 0) { }
            if (symbol != null)
            {
                try
                {
                    var desc = await Datatypes.Description(symbol, ct);
                    if (desc is { Bounds.Count: > 0 })
                    {
                        var (items, total) = await Datatypes.ChildSymbols(symbol, offset, count, ct);
                        foreach (var item in items) Symbols[item.Name] = item;
                        return new()
                        {
                            ["nodes"] = items.Select(i => (object?)AdsProtocol.SymbolItem(i)).ToList(), ["total"] = total,
                            ["next_offset"] = offset + count < total ? offset + count : null,
                        };
                    }
                    var (children, _) = await Datatypes.ChildSymbols(symbol, 0, 100000, ct);
                    foreach (var item in children)
                    {
                        if (!Symbols.ContainsKey(item.Name)) Symbols[item.Name] = item;
                        nodes[item.Name] = AdsProtocol.SymbolItem(Symbols[item.Name]);
                    }
                }
                catch (InvalidDataException exc) when (nodes.Count > 0) { warning = exc.Message; }
            }
        }
        var values = nodes.Values.OrderBy(n => ((string)n["nodeid"]!).ToLowerInvariant(), StringComparer.Ordinal).ToList();
        return new()
        {
            ["nodes"] = values.Skip((int)offset).Take((int)count).Select(v => (object?)v).ToList(), ["total"] = (long)values.Count,
            ["next_offset"] = offset + count < values.Count ? offset + count : null, ["warning"] = warning,
        };
    }

    async Task Release(uint handle, CancellationToken ct)
    {
        var request = new byte[16];
        BinaryPrimitives.WriteUInt32LittleEndian(request, 0xF006);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(8), 4);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(12), handle);
        try { CheckResult(await Transport.Command(3, request, ct)); }
        catch (AdsException) { }
    }

    public async Task ReleaseHandles(CancellationToken ct, IReadOnlySet<string>? keep = null)
    {
        foreach (var (name, handle) in Handles.ToList())
        {
            if (keep != null && keep.Contains(name)) continue;
            Handles.Remove(name);
            if (Transport.IsOpen) await Release(handle, ct);
        }
    }

    public async Task<uint> Handle(string name, CancellationToken ct)
    {
        if (!Handles.TryGetValue(name, out var handle))
        {
            var raw = await ReadWrite(0xF003, 0, 4, [.. Encoding.UTF8.GetBytes(name), 0], ct);
            if (raw.Length != 4) throw new InvalidDataException("Handle ADS non valido");
            Handles[name] = handle = BinaryPrimitives.ReadUInt32LittleEndian(raw);
        }
        return handle;
    }

    public Sample Row(string nodeId, AdsSymbol? s, byte[]? data = null, uint error = 0) => AdsProtocol.Row(nodeId, s, data, error, State, encoding);

    public async Task<List<Sample>> ReadValues(IReadOnlyList<string> ids, CancellationToken ct)
    {
        await ReleaseHandles(ct, ids.Select(AdsProtocol.SymbolName).ToHashSet());
        var ready = new List<(string nid, AdsSymbol s, uint handle)>();
        var rows = new Dictionary<string, Sample>();
        foreach (var nid in ids)
        {
            var name = AdsProtocol.SymbolName(nid);
            try
            {
                var s = await GetSymbol(name, ct);
                if (s.Size > AdsProtocol.MaxValue)
                {
                    var row = Row(nid, s, error: 1797);
                    row.Status = "ClientValueTooLarge"; row.StatusCode = null; row.Error = "Simbolo > 1 MiB: selezionare singoli elementi";
                    rows[nid] = row;
                    continue;
                }
                ready.Add((nid, s, await Handle(name, ct)));
            }
            catch (AdsException exc) { rows[nid] = Row(nid, null, error: exc.Code); }
        }
        if (ready.Count > 0 && Mode == "sum" && sumSupported)
        {
            try
            {
                // Small batches limit one ADS request and PLC execution time.
                var batchSize = Math.Max(1, Math.Min(500, int.Parse(Environment.GetEnvironmentVariable("SPACE_ADS_SUM_BATCH") ?? "32", CultureInfo.InvariantCulture)));
                for (var first = 0; first < ready.Count; first += batchSize)
                {
                    var batch = ready.Skip(first).Take(batchSize).ToList();
                    var size = (uint)batch.Sum(b => b.s.Size);
                    var request = new byte[12 * batch.Count];
                    for (var i = 0; i < batch.Count; i++)
                    {
                        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(12 * i), 0xF005);
                        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(12 * i + 4), batch[i].handle);
                        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(12 * i + 8), batch[i].s.Size);
                    }
                    var data = await ReadWrite(0xF080, (uint)batch.Count, (uint)(4 * batch.Count) + size, request, ct);
                    if (data.Length != 4 * batch.Count + size) throw new InvalidDataException("Risposta ADS SumRead incompleta");
                    var pos = 4 * batch.Count;
                    for (var i = 0; i < batch.Count; i++)
                    {
                        var error = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4 * i));
                        rows[batch[i].nid] = Row(batch[i].nid, batch[i].s, data[pos..(pos + (int)batch[i].s.Size)], error);
                        pos += (int)batch[i].s.Size;
                    }
                }
                Info["read_mode"] = "ADS SumRead";
            }
            catch (AdsException exc) when (exc.Code is 1793 or 1794)
            {
                sumSupported = false;
                Info["sum_fallback"] = exc.Message;
            }
        }
        if (ready.Count > 0 && (Mode == "individual" || !sumSupported))
        {
            Info["read_mode"] = "ADS Read individuale";
            foreach (var (nid, s, h) in ready)
            {
                try { rows[nid] = Row(nid, s, await Read(0xF005, h, s.Size, ct)); }
                catch (AdsException exc) { rows[nid] = Row(nid, s, error: exc.Code); }
            }
        }
        // Invalidated handles are refreshed on the next cycle, retaining a Bad sample now.
        foreach (var (nid, row) in rows)
        {
            if (row.StatusCode is 1808 or 1809 or 1826)
            {
                var name = AdsProtocol.SymbolName(nid);
                if (Handles.Remove(name, out var handle)) await Release(handle, ct);
                Symbols.Remove(name);
            }
        }
        return ids.Select(n => rows[n]).ToList();
    }

    public async Task Disconnect()
    {
        try
        {
            using var timeout = new CancellationTokenSource(1500);
            if (Transport.IsOpen) await ReleaseHandles(timeout.Token);
        }
        catch (Exception) { }
        finally { await Transport.Abort(); }
    }
}
