using System.Buffers.Binary;
using System.Globalization;
using System.Net.Sockets;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.S7;

/// <summary>
/// Read-only classic S7comm over ISO-on-TCP (TPKT/COTP), port of s7_client.S7Client.
/// Only Setup Communication (0xF0) and Read Var (0x04) jobs are emitted.
/// </summary>
public sealed class S7ClassicDriver : SourceDriver
{
    static readonly Dictionary<int, string> Errors = new()
    {
        [1] = "Errore hardware", [3] = "Accesso negato: verificare autorizzazione PUT/GET",
        [5] = "Indirizzo fuori area: verificare DB, offset e dimensione", [6] = "Tipo/trasporto non supportato",
        [7] = "Dimensione non coerente", [10] = "DB o area non disponibile: verificare DB standard non ottimizzato",
    };

    readonly SemaphoreSlim jobLock = new(1, 1);
    readonly Dictionary<string, Dictionary<string, object?>> catalog = new();
    readonly List<string> order = new();
    TcpClient? tcp;
    NetworkStream? stream;
    int sequence;
    int pdu = 240;
    public int Sequence => sequence;

    public S7ClassicDriver(JsonElement cfg) : base(cfg)
    {
        Info["transport"] = "S7 ISO-on-TCP";
        Info["read_mode"] = "S7 ReadVar · polling aggregato";
        Info["timestamps"] = "Timestamp locale di ricezione; timestamp PLC non fornito da ReadVar";
        Info["quality_origin"] = "Esito lettura S7; stato RUN/STOP CPU non interrogato";
        Info["read_only"] = true;
        if (cfg.TryGetProperty("s7_tags", out var tags) && tags.ValueKind == JsonValueKind.Array)
            foreach (var t in tags.EnumerateArray())
            {
                var nid = t.GetProperty("nodeid").GetString()!;
                catalog[nid] = new Dictionary<string, object?> { ["nodeid"] = nid, ["display_name"] = t.GetProperty("display_name").GetString(), ["data_type"] = t.GetProperty("data_type").GetString() };
                order.Add(nid);
            }
    }

    public override string Protocol => "s7";
    int TimeoutMs => Int("s7_timeout_ms", 3000);

    public override async Task ConnectAsync(CancellationToken ct)
    {
        var remoteText = Text("s7_remote_tsap");
        var remote = remoteText.Length > 0 ? int.Parse(remoteText, NumberStyles.HexNumber, CultureInfo.InvariantCulture)
            : Int("s7_connection_type", 3) * 256 + Int("s7_rack") * 32 + Int("s7_slot", 1);
        try
        {
            using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct))
            {
                timeout.CancelAfter(TimeoutMs);
                try
                {
                    tcp = new TcpClient { NoDelay = true };
                    await tcp.ConnectAsync(Text("s7_host"), Int("s7_tcp_port", 102), timeout.Token);
                    stream = tcp.GetStream();
                    // ISO transport connection request, class 0, TPDU size 1024.
                    var request = new List<byte> { 0x11, 0xE0, 0x00, 0x00, 0x00, 0x01, 0x00, 0xC1, 0x02 };
                    request.AddRange(Convert.FromHexString(Text("s7_local_tsap", "0100")));
                    request.AddRange(new byte[] { 0xC2, 0x02, (byte)(remote >> 8), (byte)remote, 0xC0, 0x01, 0x0A });
                    await Send(request.ToArray(), timeout.Token);
                    var cc = await Packet(timeout.Token);
                    if (cc.Length < 7 || cc[0] + 1 != cc.Length || cc[1] != 0xD0 || cc[2] != 0 || cc[3] != 1 || cc[6] != 0)
                        throw new SourceException("Connessione COTP rifiutata: controllare rack, slot e TSAP");
                }
                catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                {
                    throw new SourceException("Timeout S7: verificare IP, porta TCP, rack/slot, TSAP e firewall");
                }
            }
            var setup = new byte[8] { 0xF0, 0x00, 0x00, 0x01, 0x00, 0x01, 0, 0 };
            BinaryPrimitives.WriteUInt16BigEndian(setup.AsSpan(6), (ushort)Int("s7_pdu", 480));
            var (parameters, _) = await Job(setup, ct);
            if (parameters.Length != 8 || parameters[0] != 0xF0 || parameters[1] != 0) throw new SourceException("Negoziazione PDU S7 non valida");
            pdu = BinaryPrimitives.ReadUInt16BigEndian(parameters.AsSpan(6));
            if (pdu < 128 || pdu > Int("s7_pdu", 480)) throw new SourceException("Dimensione PDU S7 inattesa");
            Info["host"] = Text("s7_host"); Info["port"] = (long)Int("s7_tcp_port", 102); Info["rack"] = (long)Int("s7_rack");
            Info["slot"] = (long)Int("s7_slot", 1); Info["local_tsap"] = Text("s7_local_tsap", "0100"); Info["remote_tsap"] = remote.ToString("X4", CultureInfo.InvariantCulture);
            Info["pdu"] = (long)pdu;
        }
        catch (SocketException exc)
        {
            await DisconnectAsync();
            throw new SourceException("Connessione S7 non riuscita: " + exc.Message);
        }
        catch
        {
            await DisconnectAsync();
            throw;
        }
    }

    public override Task DisconnectAsync()
    {
        var s = stream; var t = tcp;
        stream = null; tcp = null;
        try { s?.Dispose(); } catch (Exception) { }
        try { t?.Dispose(); } catch (Exception) { }
        return Task.CompletedTask;
    }

    async Task Send(byte[] payload, CancellationToken ct)
    {
        if (stream == null) throw new SourceException("Connessione S7 chiusa");
        var frame = new byte[payload.Length + 4];
        frame[0] = 3;
        BinaryPrimitives.WriteUInt16BigEndian(frame.AsSpan(2), (ushort)(payload.Length + 4));
        payload.CopyTo(frame, 4);
        await stream.WriteAsync(frame, ct);
    }

    async Task<byte[]> Packet(CancellationToken ct)
    {
        var header = await ReadExact(4, ct);
        var length = BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(2));
        if (header[0] != 3 || header[1] != 0 || length < 7) throw new InvalidDataException("Header TPKT non valido");
        return await ReadExact(length - 4, ct);
    }

    async Task<byte[]> ReadExact(int count, CancellationToken ct)
    {
        if (stream == null) throw new SourceException("Connessione S7 chiusa");
        var buffer = new byte[count];
        var read = 0;
        while (read < count)
        {
            var n = await stream.ReadAsync(buffer.AsMemory(read), ct);
            if (n == 0) throw new SourceException("Connessione S7 interrotta");
            read += n;
        }
        return buffer;
    }

    async Task<(byte[] parameters, byte[] data)> Job(byte[] parameters, CancellationToken ct)
    {
        await jobLock.WaitAsync(ct);
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeoutMs);
            try
            {
                sequence = sequence % 65535 + 1;
                var header = new byte[10];
                header[0] = 0x32; header[1] = 1;
                BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(4), (ushort)sequence);
                BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(6), (ushort)parameters.Length);
                await Send([0x02, 0xF0, 0x80, .. header, .. parameters], timeout.Token);
                var parts = new List<byte>();
                var total = 0;
                var complete = false;
                for (var i = 0; i < 64; i++)
                {
                    var packet = await Packet(timeout.Token);
                    if (packet.Length < 3 || packet[0] != 0x02 || packet[1] != 0xF0) throw new InvalidDataException("Risposta COTP Data non valida");
                    total += packet.Length - 3;
                    if (total > Math.Max(pdu, 240)) throw new InvalidDataException("Risposta oltre la PDU negoziata");
                    parts.AddRange(packet.AsSpan(3).ToArray());
                    if ((packet[2] & 0x80) != 0) { complete = true; break; }
                }
                if (!complete) throw new InvalidDataException("Troppi segmenti COTP");
                var response = parts.ToArray();
                if (response.Length < 12) throw new InvalidDataException("Risposta S7 troncata");
                var plen = BinaryPrimitives.ReadUInt16BigEndian(response.AsSpan(6));
                var dlen = BinaryPrimitives.ReadUInt16BigEndian(response.AsSpan(8));
                if (response[0] != 0x32 || response[1] != 3 || BinaryPrimitives.ReadUInt16BigEndian(response.AsSpan(2)) != 0
                    || BinaryPrimitives.ReadUInt16BigEndian(response.AsSpan(4)) != sequence || response.Length != 12 + plen + dlen)
                    throw new InvalidDataException("Header o sequenza S7 incoerente");
                if (response[10] != 0 || response[11] != 0)
                    throw new SourceException($"S7 errore 0x{response[10]:X2}{response[11]:X2}: verificare protezione CPU, PUT/GET e DB standard");
                return (response[12..(12 + plen)], response[(12 + plen)..]);
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested)
            {
                await DisconnectAsync();
                throw new SourceException("Timeout risposta S7: verificare rete, carico CPU e timeout configurato");
            }
            catch
            {
                await DisconnectAsync();
                throw;
            }
        }
        finally { jobLock.Release(); }
    }

    public override Task ReadStateAsync(CancellationToken ct)
    {
        if (stream == null || tcp is not { Connected: true }) throw new SourceException("Connessione S7 interrotta");
        // A peer close is visible as a readable socket with no data.
        if (tcp.Client.Poll(0, SelectMode.SelectRead) && tcp.Client.Available == 0) throw new SourceException("Connessione S7 interrotta");
        return Task.CompletedTask;
    }

    public override async Task<List<Sample>> ReadValuesAsync(IReadOnlyList<string> ids, CancellationToken ct)
    {
        var tags = ids.Select(n => S7Types.ParseTag(n)).ToList();
        // A single large STRING may exceed a small negotiated PDU: split its byte reads.
        var pieces = new List<(int index, S7Tag tag, int offset, int size)>();
        var payloads = tags.Select(_ => new List<byte>()).ToList();
        var failures = new Dictionary<int, (int code, string message)>();
        for (var i = 0; i < tags.Count; i++)
            for (var offset = 0; offset < tags[i].Size; offset += pdu - 18)
                pieces.Add((i, tags[i], tags[i].Offset + offset, Math.Min(pdu - 18, tags[i].Size - offset)));
        var cursor = 0;
        while (cursor < pieces.Count)
        {
            var batch = new List<(int index, S7Tag tag, int offset, int size)>();
            var responseSize = 14;
            while (cursor < pieces.Count && batch.Count < 20)
            {
                var size = pieces[cursor].size;
                if (batch.Count > 0 && (12 + 12 * (batch.Count + 1) > pdu || responseSize + 4 + size + size % 2 > pdu)) break;
                batch.Add(pieces[cursor]); responseSize += 4 + size + size % 2; cursor++;
            }
            var request = new List<byte> { 0x04, (byte)batch.Count };
            foreach (var (_, tag, offset, size) in batch)
            {
                request.AddRange(new byte[] { 0x12, 0x0A, 0x10, 0x02, (byte)(size >> 8), (byte)size, (byte)(tag.Db >> 8), (byte)tag.Db, (byte)tag.Area });
                var bits = offset * 8;
                request.AddRange(new[] { (byte)(bits >> 16), (byte)(bits >> 8), (byte)bits });
            }
            var (parameters, data) = await Job(request.ToArray(), ct);
            if (parameters.Length != 2 || parameters[0] != 4 || parameters[1] != batch.Count)
            {
                await DisconnectAsync(); throw new InvalidDataException("Conteggio ReadVar S7 incoerente");
            }
            var pos = 0;
            try
            {
                for (var j = 0; j < batch.Count; j++)
                {
                    var (i, _, _, size) = batch[j];
                    if (pos + 4 > data.Length) throw new InvalidDataException("ReadVar S7 troncato");
                    var code = data[pos]; var transport = data[pos + 1]; var length = BinaryPrimitives.ReadUInt16BigEndian(data.AsSpan(pos + 2));
                    pos += 4;
                    if (code != 255)
                    {
                        // Error items have no payload (null transport).
                        if (transport != 0) throw new InvalidDataException("Errore S7 con trasporto inatteso");
                        failures[i] = (code, Errors.GetValueOrDefault(code, "Errore lettura S7"));
                        continue;
                    }
                    var count = transport is 3 or 4 or 5 ? (length + 7) / 8 : transport is 7 or 9 ? length : -1;
                    if (count != size || pos + count > data.Length) throw new InvalidDataException("Lunghezza/trasporto ReadVar non valido");
                    payloads[i].AddRange(data.AsSpan(pos, count).ToArray()); pos += count;
                    if (count % 2 == 1 && j < batch.Count - 1) pos++;
                }
                if (pos != data.Length) throw new InvalidDataException("Dati ReadVar in eccesso o padding mancante");
            }
            catch
            {
                await DisconnectAsync(); throw;
            }
        }
        var local = Values.IsoMicro(DateTime.UtcNow);
        var rows = new List<Sample>(tags.Count);
        for (var i = 0; i < tags.Count; i++)
        {
            var tag = tags[i];
            object? value = null;
            var (code, message) = failures.TryGetValue(i, out var f) ? f : (0, "S7_OK");
            if (code == 0)
            {
                try { value = S7Types.Decode(tag, payloads[i].ToArray()); }
                catch (ArgumentException exc) { code = 0xFFFF; message = exc.Message; }
            }
            var numeric = S7Types.IsFiniteNumber(value);
            var inexact = S7Types.Inexact(value);
            var safe = !inexact && (value is not double d || double.IsFinite(d)) ? value : PyJson.Str(value);
            rows.Add(new Sample
            {
                NodeId = tag.NodeId, Value = safe, HasSourceValue = true, SourceValue = value,
                PlotValue = numeric && code == 0 ? S7Types.ToDouble(value) : null, DataType = tag.DataType, Numeric = numeric,
                Good = code == 0, Quality = code != 0 ? "Bad" : "Good", Status = message, StatusCode = code,
                Error = code != 0 ? message : "", PrecisionWarning = inexact, SourceTs = null, ServerTs = null, LocalTs = local,
                TimestampOrigin = "local read completion",
            });
        }
        return rows;
    }

    Dictionary<string, object?> Inspect(string nodeId)
    {
        var tag = S7Types.ParseTag(nodeId, true);
        if (tag.Folder)
            return new() { ["nodeid"] = tag.NodeId, ["display_name"] = tag.Group.Length > 0 ? tag.Group : "Variabili S7 configurate", ["node_class"] = "Object", ["has_children"] = true };
        var name = catalog.TryGetValue(tag.NodeId, out var entry) && entry["display_name"] is string n ? n : tag.Address;
        return new()
        {
            ["nodeid"] = tag.NodeId, ["display_name"] = name, ["browse_name"] = name, ["node_class"] = "Variable", ["has_children"] = false,
            ["data_type"] = tag.DataType, ["path"] = tag.Group + "/" + name, ["comment"] = tag.Address + " · " + tag.DataType,
        };
    }

    public override Task<object?> InspectAsync(string nodeId, CancellationToken ct) => Task.FromResult<object?>(Inspect(nodeId));

    public override Task<object?> BrowseAsync(string nodeId, CancellationToken ct)
    {
        var tag = S7Types.ParseTag(nodeId, true);
        if (!tag.Folder) return Task.FromResult<object?>(new List<object?>());
        if (tag.Group.Length == 0)
        {
            var groups = order.Select(n => S7Types.ParseTag(n).Group).Distinct().OrderBy(g => g, StringComparer.Ordinal);
            return Task.FromResult<object?>(groups.Select(g => (object?)Inspect("s7:/" + g)).ToList());
        }
        return Task.FromResult<object?>(order.Where(n => S7Types.ParseTag(n).Group == tag.Group).Select(n => (object?)Inspect(n)).ToList());
    }

    public override Task<object?> ScanItemsAsync(string root, CancellationToken ct)
    {
        var group = S7Types.ParseTag(root, true);
        if (!group.Folder) return Task.FromResult<object?>(new List<object?> { Inspect(group.NodeId) });
        return Task.FromResult<object?>(order.Where(n => group.Group.Length == 0 || S7Types.ParseTag(n).Group == group.Group).Select(n => (object?)Inspect(n)).ToList());
    }
}
