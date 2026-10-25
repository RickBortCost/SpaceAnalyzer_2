using System.Buffers.Binary;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.S7;

/// <summary>
/// Passive cyclic S7 UDP Direct Generic receiver (port of s7_udp.S7UDPClient): one configured
/// sender/module, an independent socket thread, a bounded handoff queue and unmodified
/// per-datagram timing.
/// </summary>
public sealed class S7UdpDriver : SourceDriver
{
    sealed record Item(byte[]? Payload, double? SourceMs, string? SourceTs, double ReceivedMs, IReadOnlyList<string> Ids, int SampleMs,
        Acquisition.Generation? Generation, string Quality, string Status, long ReceivedNs, long ReceivedMono);

    readonly Dictionary<string, Dictionary<string, object?>> catalog = new();
    readonly List<string> order = new();
    readonly Dictionary<string, S7Tag> tags = new();
    readonly object guard = new();
    readonly object done = new();
    readonly Queue<Item> queue = new();
    readonly SemaphoreSlim available = new(0);
    readonly int capacity;
    readonly CancellationTokenSource stop = new();
    readonly Dictionary<string, Sample> latest = new();
    readonly Queue<double> periods = new();
    readonly Queue<double> samplePeriods = new();
    readonly Dictionary<string, long> counts = new()
    {
        ["received"] = 0, ["accepted"] = 0, ["lost"] = 0, ["duplicates"] = 0, ["out_of_order"] = 0, ["invalid"] = 0,
        ["filtered"] = 0, ["queue_dropped"] = 0, ["sequence_resets"] = 0, ["timeouts"] = 0, ["timestamp_errors"] = 0,
    };
    Socket? socket;
    Thread? receiver, consumer;
    Func<Selection>? selection;
    StreamSink? sink;
    IPAddress? sender;
    long serial, processedSerial;
    double? lastReceived, sequenceTime, lastSourceMs, lastDispatchMs;
    int? lastSeq;
    bool stale;
    double started;
    string error = "";

    public S7UdpDriver(JsonElement cfg) : base(cfg)
    {
        capacity = Int("s7_udp_queue", 2048);
        if (cfg.TryGetProperty("s7_udp_tags", out var list) && list.ValueKind == JsonValueKind.Array)
            foreach (var t in list.EnumerateArray())
            {
                var nid = t.GetProperty("nodeid").GetString()!;
                catalog[nid] = new() { ["nodeid"] = nid, ["display_name"] = t.GetProperty("display_name").GetString(), ["data_type"] = t.GetProperty("data_type").GetString() };
                order.Add(nid);
                tags[nid] = S7Types.ParseNode(nid);
            }
        Info["mode"] = "udp";
        Info["transport"] = "S7 UDP Direct Generic";
        Info["read_mode"] = "UDP ciclico · ogni telegramma";
        Info["read_only"] = true;
        Info["host"] = Text("s7_host");
        Info["port"] = (long)Int("s7_udp_port", 4170);
        Info["bind_ip"] = Text("s7_udp_bind", "0.0.0.0");
        Info["module_index"] = (long)Int("s7_udp_module", 200);
        Info["length"] = (long)Int("s7_udp_length", 16);
        Info["timestamps"] = Int("s7_udp_timestamp_offset", -1) >= 0 ? "Timestamp PLC UTC µs nel payload + timestamp locale di ricezione" : "Timestamp locale di ricezione; header Direct senza timestamp PLC";
        Info["quality_origin"] = "Validità telegramma, contatore sequenza e watchdog; qualità interna CPU non trasmessa";
    }

    public override string Protocol => "s7";
    public override bool Streaming => true;
    public override string Error { get { lock (guard) return error; } }
    double Watchdog => Int("s7_udp_watchdog_ms", 1000) / 1000.0;
    static double Now() => SourceSession.Seconds();

    public override async Task ConnectAsync(CancellationToken ct)
    {
        var addresses = await Dns.GetHostAddressesAsync(Text("s7_host"), AddressFamily.InterNetwork, ct);
        sender = addresses.FirstOrDefault() ?? throw new SourceException("Indirizzo PLC UDP non risolto");
        var s = new Socket(AddressFamily.InterNetwork, SocketType.Dgram, ProtocolType.Udp);
        try
        {
            if (OperatingSystem.IsWindows()) s.ExclusiveAddressUse = true;
            s.ReceiveBufferSize = 4 * 1024 * 1024;
            s.Bind(new IPEndPoint(IPAddress.Parse(Text("s7_udp_bind", "0.0.0.0")), Int("s7_udp_port", 4170)));
            s.ReceiveTimeout = (int)Math.Max(1, Math.Min(100, Int("s7_udp_watchdog_ms", 1000) / 2.0));
        }
        catch (SocketException exc)
        {
            s.Dispose();
            throw new SourceException("UDP: porta locale non disponibile (" + exc.Message + ")");
        }
        socket = s;
        Info["socket_buffer_bytes"] = (long)s.ReceiveBufferSize;
        started = Now();
        receiver = new Thread(Receive) { Name = "S7-UDP-receive", IsBackground = true };
        receiver.Start();
    }

    public override void ConfigureStream(Func<Selection> selection, StreamSink sink)
    {
        this.selection = selection;
        this.sink = sink;
        consumer = new Thread(Consume) { Name = "S7-UDP-acquire", IsBackground = true };
        consumer.Start();
    }

    /// <summary>decode_packet: sequence, payload and the optional PLC UTC microsecond timestamp.</summary>
    public (int sequence, byte[] payload, double? sourceMs, string? sourceTs) DecodePacket(byte[] data, int length)
    {
        if (length < 8 || length > 4096 || length % 2 == 1) throw new ArgumentException("Lunghezza UDP deve essere pari, tra 8 e 4096 byte");
        var span = data.AsSpan(0, length);
        var declared = BinaryPrimitives.ReadUInt16BigEndian(span);
        var module = BinaryPrimitives.ReadUInt16BigEndian(span[2..]);
        var sequence = BinaryPrimitives.ReadUInt16BigEndian(span[4..]);
        if (declared != length || declared != Int("s7_udp_length", 16)) throw new ArgumentException("message_length diverso dal telegramma configurato/ricevuto");
        if (module != Int("s7_udp_module", 200)) throw new KeyNotFoundException("Modulo UDP diverso");
        var payload = span[6..].ToArray();
        var offset = Int("s7_udp_timestamp_offset", -1);
        double? sourceMs = null; string? sourceTs = null;
        if (offset >= 0)
        {
            var micros = Text("s7_udp_endian", "big") == "big" ? BinaryPrimitives.ReadInt64BigEndian(payload.AsSpan(offset)) : BinaryPrimitives.ReadInt64LittleEndian(payload.AsSpan(offset));
            // datetime(2000..2200): 946684800000000 µs .. 7258118400000000 µs.
            if (micros < 946684800000000L || micros >= 7258118400000000L) throw new ArgumentException("Timestamp PLC UTC in microsecondi fuori intervallo 2000–2199");
            sourceMs = micros / 1000.0;
            sourceTs = Values.IsoMicro(DateTime.UnixEpoch.AddTicks(micros * 10));
        }
        return (sequence, payload, sourceMs, sourceTs);
    }

    void Receive()
    {
        var buffer = new byte[65535];
        try
        {
            while (!stop.IsCancellationRequested)
            {
                CheckTimeout();
                EndPoint peer = new IPEndPoint(IPAddress.Any, 0);
                int length;
                try { length = socket!.ReceiveFrom(buffer, ref peer); }
                catch (SocketException exc) when (exc.SocketErrorCode == SocketError.TimedOut) { CheckTimeout(); continue; }
                catch (Exception) when (stop.IsCancellationRequested) { break; }
                var receivedNs = Clock.UtcNs(); var receivedMs = receivedNs / 1e6; var receivedMono = Clock.MonoNs(); var mono = receivedMono / 1e9;
                string quality, status;
                double? sourceMs; string? sourceTs; byte[] payload;
                lock (guard)
                {
                    counts["received"]++;
                    var from = (IPEndPoint)peer;
                    var senderPort = Int("s7_udp_sender_port");
                    if (!from.Address.Equals(sender) || (senderPort != 0 && from.Port != senderPort)) { counts["filtered"]++; continue; }
                    int seq;
                    try { (seq, payload, sourceMs, sourceTs) = DecodePacket(buffer, length); }
                    catch (KeyNotFoundException) { counts["filtered"]++; continue; }
                    catch (ArgumentException) { counts["invalid"]++; continue; }
                    var gap = 0; var reset = false;
                    if (lastSeq is int previous)
                    {
                        var distance = (seq - previous) & 0xFFFF;
                        var timedOut = mono - sequenceTime!.Value >= Watchdog;
                        if (distance == 0 && !timedOut) { counts["duplicates"]++; continue; }
                        if (timedOut) reset = true;
                        else if (distance >= 32768) { counts["out_of_order"]++; continue; }
                        else gap = distance - 1;
                    }
                    if (sourceMs is double s && lastSourceMs is double ls && s <= ls) { counts["timestamp_errors"]++; continue; }
                    if (lastReceived is double lr && !stale && !reset)
                    {
                        var elapsed = sourceMs is double s2 && lastSourceMs is double ls2 ? s2 - ls2 : (mono - lr) * 1000;
                        var period = elapsed / (gap + 1);
                        if (period > 0 && period <= 60000) { samplePeriods.Enqueue(period); while (samplePeriods.Count > 128) samplePeriods.Dequeue(); }
                    }
                    lastSeq = seq; sequenceTime = mono;
                    counts["lost"] += gap;
                    if (reset) counts["sequence_resets"]++;
                    if (lastReceived is double lr2 && !stale) { periods.Enqueue((mono - lr2) * 1000); while (periods.Count > 1000) periods.Dequeue(); }
                    lastReceived = mono; lastSourceMs = sourceMs; stale = false;
                    counts["accepted"]++;
                    quality = gap > 0 || reset ? "Uncertain" : "Good";
                    status = gap > 0 ? "UDP sequence gap: " + gap.ToString(CultureInfo.InvariantCulture) : reset ? "UDP sequence restarted after timeout" : "Good";
                }
                var current = selection?.Invoke() ?? new Selection(order, Int("sample_ms", 10), null);
                Enqueue(new Item(payload, sourceMs, sourceTs, receivedMs, current.Ids, SamplePeriodMs(current.Period), current.Generation, quality, status, receivedNs, receivedMono));
                CheckTimeout();
            }
        }
        catch (Exception exc)
        {
            lock (guard) error = exc.Message;
        }
    }

    void Enqueue(Item item)
    {
        lock (guard)
        {
            if (queue.Count >= capacity) { counts["queue_dropped"]++; return; }
            queue.Enqueue(item);
            serial++;
        }
        available.Release();
    }

    void CheckTimeout()
    {
        var now = Now();
        lock (guard)
        {
            if (stale || now - (lastReceived ?? started) < Watchdog) return;
            stale = true; counts["timeouts"]++;
        }
        if (selection != null)
        {
            var current = selection();
            Enqueue(new Item(null, null, null, Clock.UtcNs() / 1e6, current.Ids, SamplePeriodMs(current.Period), current.Generation, "Bad",
                "UDP timeout: nessun telegramma valido", Clock.UtcNs(), Clock.MonoNs()));
        }
    }

    void Consume()
    {
        try
        {
            long dropped = 0;
            while (!stop.IsCancellationRequested)
            {
                if (!available.Wait(200)) continue;
                Item item;
                lock (guard) item = queue.Dequeue();
                try
                {
                    var quality = item.Quality; var status = item.Status;
                    lock (guard)
                        if (counts["queue_dropped"] > dropped && quality != "Bad") { dropped = counts["queue_dropped"]; quality = "Uncertain"; status = "UDP acquisition queue overflow"; }
                    var rows = Rows(item.Payload, item.Ids, quality, status, item.SourceTs, item.ReceivedMs);
                    foreach (var row in rows) { row.ReceivedNs = item.ReceivedNs; row.ReceivedMonoNs = item.ReceivedMono; }
                    var ts = item.SourceMs ?? item.ReceivedMs;
                    // A local timeout uses the next source-timeline instant, keeping time monotonic.
                    if (item.Payload == null && lastDispatchMs is double last) ts = last + item.SampleMs;
                    lock (guard) foreach (var row in rows) latest[row.NodeId] = row;
                    if (rows.Count > 0) sink?.Invoke(ts, rows, item.SampleMs, item.Generation);
                    lastDispatchMs = ts;
                }
                finally
                {
                    lock (done) { lock (guard) processedSerial++; Monitor.PulseAll(done); }
                }
            }
        }
        catch (Exception exc)
        {
            lock (guard) error = exc.Message;
        }
    }

    List<Sample> Rows(byte[]? payload, IReadOnlyList<string> ids, string quality, string status, string? sourceTs, double receivedMs)
    {
        var rows = new List<Sample>();
        var local = Values.IsoFromMs(receivedMs);
        var little = Text("s7_udp_endian", "big") == "little";
        foreach (var nid in ids)
        {
            tags.TryGetValue(nid, out var tag);
            var kind = tag?.DataType ?? "Null";
            var failure = payload != null ? "" : status;
            object? value = null;
            var q = quality;
            if (tag == null) { failure = "Campo non presente nel catalogo UDP"; q = "Bad"; }
            else if (payload != null)
            {
                try
                {
                    var raw = payload.AsSpan(Math.Min(tag.Offset, payload.Length), Math.Max(0, Math.Min(tag.Size, payload.Length - tag.Offset)));
                    value = little && tag.Kind is not ("BOOL" or "STRING") ? S7Types.Scalar(S7Types.Scalars[tag.Kind].fmt, raw.Length == tag.Size ? raw : throw new ArgumentException($"unpack requires a buffer of {tag.Size} bytes"), true) : S7Types.Decode(tag, raw);
                    if (value is double d && !double.IsFinite(d)) throw new ArgumentException("Valore UDP non finito");
                }
                catch (ArgumentException exc) { value = null; failure = exc.Message; q = "Bad"; }
            }
            rows.Add(S7Rows.ValueRow(nid, kind, value, failure, q, failure.Length == 0 ? status : failure, local, sourceTs, receivedMs));
        }
        return rows;
    }

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

    public override Task DisconnectAsync()
    {
        stop.Cancel();
        try { socket?.Dispose(); } catch (Exception) { }
        receiver?.Join(2000);
        consumer?.Join(2000);
        socket = null;
        return Task.CompletedTask;
    }

    public override Task ReadStateAsync(CancellationToken ct)
    {
        lock (guard) if (error.Length > 0) throw new SourceException(error);
        if (receiver is not { IsAlive: true }) throw new SourceException("Ricevitore UDP chiuso");
        return Task.CompletedTask;
    }

    public override Task<List<Sample>> ReadValuesAsync(IReadOnlyList<string> ids, CancellationToken ct)
    {
        lock (guard)
            return Task.FromResult(ids.Select(n => latest.TryGetValue(n, out var row) ? row : S7Rows.ValueRow(n, "Null", null, "In attesa di un telegramma UDP", "Bad")).ToList());
    }

    public override int SamplePeriodMs(int fallback)
    {
        lock (guard) return SamplePeriodUnlocked(fallback);
    }

    int SamplePeriodUnlocked(int fallback)
    {
        var value = samplePeriods.Count > 0 ? Median(samplePeriods) : fallback;
        return (int)Math.Max(1, Math.Min(60000, PyJson.Round(value)));
    }

    /// <summary>statistics.median: the mean of the two middle values for an even count.</summary>
    static double Median(IEnumerable<double> values)
    {
        var ordered = values.OrderBy(v => v).ToList();
        var n = ordered.Count;
        return n % 2 == 1 ? ordered[n / 2] : (ordered[n / 2 - 1] + ordered[n / 2]) / 2;
    }

    public override Dictionary<string, object?> Diagnostics()
    {
        lock (guard)
        {
            var period = periods.ToList();
            double? age = lastReceived is double lr ? (Now() - lr) * 1000 : null;
            var result = counts.ToDictionary(p => p.Key, p => (object?)p.Value);
            result["mode"] = "udp";
            result["queue_samples"] = (long)queue.Count;
            result["queue_capacity"] = (long)capacity;
            result["age_ms"] = age is double a ? Math.Round(a, 3, MidpointRounding.ToEven) : null;
            result["receiving"] = lastReceived != null && !stale;
            result["arrival_period_ms"] = period.Count > 0 ? period.Sum() / period.Count : null;
            result["min_ms"] = period.Count > 0 ? period.Min() : null;
            result["max_ms"] = period.Count > 0 ? period.Max() : null;
            result["p95_ms"] = period.Count > 0 ? period.OrderBy(v => v).ElementAt((int)((period.Count - 1) * .95)) : null;
            result["sample_period_ms"] = samplePeriods.Count > 0 ? Median(samplePeriods) : null;
            result["timestamp_origin"] = Int("s7_udp_timestamp_offset", -1) >= 0 ? "PLC UTC µs" : "Ricezione locale";
            result["error"] = error;
            return result;
        }
    }

    Dictionary<string, object?> Inspect(string nodeId)
    {
        var node = S7Types.ParseNode(nodeId, true);
        if (node.Mode != "udp") throw new SourceException("In modalità UDP scegliere un campo s7:udp:");
        if (node.Folder) return new() { ["nodeid"] = nodeId, ["display_name"] = "Campi telegramma UDP", ["node_class"] = "Object", ["has_children"] = true };
        if (!catalog.TryGetValue(nodeId, out var tag)) throw new SourceException("Campo non presente nel catalogo UDP");
        return new Dictionary<string, object?>(tag)
        {
            ["node_class"] = "Variable", ["has_children"] = false, ["path"] = tag["display_name"], ["comment"] = nodeId + " · offset dal payload",
        };
    }

    public override Task<object?> InspectAsync(string nodeId, CancellationToken ct) => Task.FromResult<object?>(Inspect(nodeId));

    public override Task<object?> BrowseAsync(string nodeId, CancellationToken ct)
    {
        var node = Inspect(nodeId);
        return Task.FromResult<object?>((string?)node["node_class"] == "Object" ? order.Select(n => (object?)Inspect(n)).ToList() : new List<object?>());
    }

    public override async Task<object?> ScanItemsAsync(string root, CancellationToken ct) =>
        root == "s7:udp:/" ? await BrowseAsync(root, ct) : new List<object?> { Inspect(root) };
}

/// <summary>s7_catalog.value_row: the common S7 symbolic/UDP row.</summary>
public static class S7Rows
{
    public static Sample ValueRow(string nodeId, string kind, object? value = null, string error = "", string quality = "Good", string? status = null,
        string? localTs = null, string? sourceTs = null, double? receivedMs = null)
    {
        var numeric = S7Types.IsFiniteNumber(value);
        var inexact = S7Types.Inexact(value);
        return new Sample
        {
            NodeId = nodeId, HasSourceValue = true, SourceValue = value, Value = inexact ? PyJson.Str(value) : value,
            PlotValue = numeric && quality != "Bad" ? S7Types.ToDouble(value) : null, Numeric = numeric,
            Good = quality == "Good", Quality = quality, Status = status ?? (error.Length == 0 ? "Good" : error), StatusCode = null,
            Error = error, DataType = kind, SourceTs = sourceTs, ServerTs = null, LocalTs = localTs, ReceivedMs = receivedMs,
            PrecisionWarning = inexact,
        };
    }
}
