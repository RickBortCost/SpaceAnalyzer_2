using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>A DeviceNotification sample: the client's user value, the PLC FILETIME and a copy of the bytes.</summary>
public delegate void AdsNotificationCallback(uint user, long filetime, byte[] data, long receivedNs, long receivedMono);

/// <summary>
/// ADS command transport. Replies are returned as the ADS payload (result code first),
/// exactly as on the wire, so the client logic is shared by ADS/TCP and TcAdsDll.
/// </summary>
public interface IAdsTransport
{
    bool IsOpen { get; }
    Dictionary<string, object?> Info { get; }
    Task Open(CancellationToken ct);
    Task<byte[]> Command(ushort command, byte[] data, CancellationToken ct);
    Task Abort();
    AdsNotificationCallback? OnNotification { get; set; }
    /// <summary>AddDeviceNotification (SERVERCYCLE = 3), times in 100 ns. Returns the notification handle.</summary>
    Task<uint> AddNotification(uint group, uint offset, uint length, uint maxDelay, uint cycle, uint user, CancellationToken ct);
    Task DeleteNotification(uint handle, CancellationToken ct);
}

/// <summary>
/// AMS/TCP to the router of the target (port 48898). One request is outstanding at a time;
/// a background reader also receives DeviceNotification frames.
/// </summary>
public sealed class AdsTcpTransport : IAdsTransport
{
    readonly string host, bindIp, sourceNetId;
    readonly int port, timeoutMs, sourcePort;
    readonly byte[] target;
    byte[]? source;
    TcpClient? tcp;
    NetworkStream? stream;
    readonly SemaphoreSlim gate = new(1, 1);
    uint invoke;
    TaskCompletionSource<byte[]>? pending;
    (ushort command, uint invoke)? expected;
    Task? reader;
    readonly ConcurrentDictionary<uint, uint> notifications = new();

    public AdsTcpTransport(string host, int port, string targetNetId, int targetPort, string sourceNetId, int sourcePort, string bindIp, int timeoutMs)
    {
        this.host = host; this.port = port; this.sourceNetId = sourceNetId; this.sourcePort = sourcePort; this.bindIp = bindIp; this.timeoutMs = timeoutMs;
        target = [.. AdsProtocol.NetId(targetNetId), (byte)targetPort, (byte)(targetPort >> 8)];
    }

    public bool IsOpen => stream != null;
    public Dictionary<string, object?> Info { get; } = new();
    public AdsNotificationCallback? OnNotification { get; set; }

    public async Task Open(CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(timeoutMs);
        var client = bindIp.Length > 0 ? new TcpClient(new IPEndPoint(IPAddress.Parse(bindIp), 0)) : new TcpClient(AddressFamily.InterNetwork);
        client.NoDelay = true;
        try { await client.ConnectAsync(host, port, timeout.Token); }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            client.Dispose();
            throw new TimeoutException("Timeout ADS: verificare IP, firewall TCP, route AMS di ritorno e runtime PLC");
        }
        catch (SocketException exc)
        {
            client.Dispose();
            throw new SourceException("Connessione ADS/TCP non riuscita: " + exc.Message);
        }
        tcp = client;
        stream = client.GetStream();
        var localIp = ((IPEndPoint)client.Client.LocalEndPoint!).Address.ToString();
        var sourceId = sourceNetId.Length > 0 ? sourceNetId : localIp + ".1.1";
        source = [.. AdsProtocol.NetId(sourceId), (byte)sourcePort, (byte)(sourcePort >> 8)];
        Info["source_netid"] = sourceId; Info["source_port"] = (long)sourcePort; Info["local_ip"] = localIp;
        Info["transport"] = "ADS/TCP"; Info["timestamps"] = "Local only (ADS Read)";
        var s = stream;
        reader = Task.Run(() => ReadLoop(s));
    }

    async Task ReadLoop(NetworkStream s)
    {
        try
        {
            var header = new byte[6];
            while (true)
            {
                await ReadExact(s, header);
                var reserved = BinaryPrimitives.ReadUInt16LittleEndian(header);
                var length = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(2));
                if (reserved != 0 || length < 32 || length > AdsProtocol.MaxPacket) throw new InvalidDataException("Frame ADS/TCP non valido");
                var raw = new byte[length];
                await ReadExact(s, raw);
                var receivedNs = Core.Clock.UtcNs(); var receivedMono = Core.Clock.MonoNs();
                var command = BinaryPrimitives.ReadUInt16LittleEndian(raw.AsSpan(16));
                var flags = BinaryPrimitives.ReadUInt16LittleEndian(raw.AsSpan(18));
                if (command == 8 && (flags & 1) == 0)
                {
                    Notification(raw.AsSpan(32), receivedNs, receivedMono);
                    continue;
                }
                var n = BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(20));
                var error = BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(24));
                var id = BinaryPrimitives.ReadUInt32LittleEndian(raw.AsSpan(28));
                var waiting = pending;
                if (waiting == null || expected is not var (cmd, inv)) throw new InvalidDataException("Risposta AMS non corrispondente alla richiesta");
                if (!raw.AsSpan(0, 8).SequenceEqual(source) || !raw.AsSpan(8, 8).SequenceEqual(target) || command != cmd || (flags & 5) != 5 || id != inv || n != length - 32)
                    throw new InvalidDataException("Risposta AMS non corrispondente alla richiesta");
                if (error != 0) waiting.TrySetException(new AdsException(error, "Router AMS"));
                else waiting.TrySetResult(raw[32..]);
            }
        }
        catch (Exception exc)
        {
            var fault = exc is EndOfStreamException or IOException or ObjectDisposedException
                ? new SourceException("Il router ha chiuso ADS/TCP prima della risposta. Se TwinCAT e installato sul PC e stai usando il suo AMS Net ID, seleziona TwinCAT locale. Verificare altrimenti route e trasporto consentito sul PAC.")
                : exc;
            pending?.TrySetException(fault);
            Closed = fault;
            await Abort();
        }
    }

    /// <summary>Why the reader stopped (null while open).</summary>
    public Exception? Closed { get; private set; }

    void Notification(ReadOnlySpan<byte> data, long receivedNs, long receivedMono)
    {
        // AdsNotificationStream: length, stamps, then per stamp FILETIME, samples, (handle, size, data)*.
        var stamps = BinaryPrimitives.ReadUInt32LittleEndian(data[4..]);
        var pos = 8;
        for (var i = 0; i < stamps; i++)
        {
            var ticks = BinaryPrimitives.ReadInt64LittleEndian(data[pos..]);
            var samples = BinaryPrimitives.ReadUInt32LittleEndian(data[(pos + 8)..]);
            pos += 12;
            for (var j = 0; j < samples; j++)
            {
                var handle = BinaryPrimitives.ReadUInt32LittleEndian(data[pos..]);
                var size = (int)BinaryPrimitives.ReadUInt32LittleEndian(data[(pos + 4)..]);
                var payload = data.Slice(pos + 8, size).ToArray();
                pos += 8 + size;
                if (notifications.TryGetValue(handle, out var user)) OnNotification?.Invoke(user, ticks, payload, receivedNs, receivedMono);
            }
        }
    }

    static async Task ReadExact(NetworkStream s, byte[] buffer)
    {
        var read = 0;
        while (read < buffer.Length)
        {
            var n = await s.ReadAsync(buffer.AsMemory(read));
            if (n == 0) throw new EndOfStreamException();
            read += n;
        }
    }

    public async Task<byte[]> Command(ushort command, byte[] data, CancellationToken ct)
    {
        await gate.WaitAsync(ct);
        try
        {
            if (stream == null || source == null) throw Closed is SourceException closed ? closed : new SourceException("Trasporto ADS chiuso");
            invoke++;
            var frame = new byte[6 + 32 + data.Length];
            BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(2), (uint)(32 + data.Length));
            target.CopyTo(frame, 6); source.CopyTo(frame, 14);
            BinaryPrimitives.WriteUInt16LittleEndian(frame.AsSpan(22), command);
            BinaryPrimitives.WriteUInt16LittleEndian(frame.AsSpan(24), 4);
            BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(26), (uint)data.Length);
            BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(34), invoke);
            data.CopyTo(frame, 38);
            var waiting = new TaskCompletionSource<byte[]>(TaskCreationOptions.RunContinuationsAsynchronously);
            pending = waiting; expected = (command, invoke);
            try
            {
                await stream.WriteAsync(frame, ct);
                return await waiting.Task.WaitAsync(TimeSpan.FromMilliseconds(timeoutMs), ct);
            }
            catch (TimeoutException)
            {
                await Abort();
                throw new TimeoutException("Timeout ADS: verificare IP, firewall TCP, route AMS di ritorno e runtime PLC");
            }
            catch (AdsException) { throw; }
            catch
            {
                await Abort();
                throw;
            }
            finally { pending = null; expected = null; }
        }
        finally { gate.Release(); }
    }

    public Task Abort()
    {
        var s = stream; var t = tcp;
        stream = null; tcp = null;
        try { s?.Dispose(); } catch (Exception) { }
        try { t?.Dispose(); } catch (Exception) { }
        return Task.CompletedTask;
    }

    public async Task<uint> AddNotification(uint group, uint offset, uint length, uint maxDelay, uint cycle, uint user, CancellationToken ct)
    {
        var request = new byte[40];
        BinaryPrimitives.WriteUInt32LittleEndian(request, group);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(4), offset);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(8), length);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(12), 3);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(16), maxDelay);
        BinaryPrimitives.WriteUInt32LittleEndian(request.AsSpan(20), cycle);
        var reply = await Command(6, request, ct);
        AdsClient.CheckResult(reply);
        if (reply.Length != 8) throw new InvalidDataException("Risposta AddDeviceNotification non valida");
        var handle = BinaryPrimitives.ReadUInt32LittleEndian(reply.AsSpan(4));
        notifications[handle] = user;
        return handle;
    }

    public async Task DeleteNotification(uint handle, CancellationToken ct)
    {
        notifications.TryRemove(handle, out _);
        AdsClient.CheckResult(await Command(7, BitConverter.GetBytes(handle), ct));
    }
}
