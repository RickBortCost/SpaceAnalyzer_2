using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using SpaceAnalyzer.Engine.Sources;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>
/// ADS through the installed TwinCAT router (TcAdsDll x64, port of ads_native.py). Only read
/// operations, symbol handle release and notifications are exposed. No Beckhoff binary is
/// bundled: the installed router owns AMS identity and routes.
/// </summary>
public sealed class AdsNativeTransport : IAdsTransport
{
    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct AmsAddr { public unsafe fixed byte NetId[6]; public ushort Port; }

    [StructLayout(LayoutKind.Sequential, Pack = 1)]
    struct NotificationAttrib { public uint Length, Mode, MaxDelay, Cycle; }

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    delegate void NotificationFunction(IntPtr address, IntPtr header, uint user);

    delegate int PortOpen();
    delegate int PortClose(int port);
    delegate int GetLocalAddress(int port, ref AmsAddr address);
    delegate int SetTimeout(int port, int timeout);
    delegate int ReadDeviceInfo(int port, ref AmsAddr address, byte[] name, byte[] version);
    delegate int ReadState(int port, ref AmsAddr address, out ushort state, out ushort device);
    delegate int ReadReq(int port, ref AmsAddr address, uint group, uint offset, uint length, byte[] data, out uint read);
    delegate int ReadWriteReq(int port, ref AmsAddr address, uint group, uint offset, uint readLength, byte[] read, uint writeLength, byte[] write, out uint count);
    delegate int WriteReq(int port, ref AmsAddr address, uint group, uint offset, uint length, byte[] data);
    delegate int AddNotificationReq(int port, ref AmsAddr address, uint group, uint offset, ref NotificationAttrib attrib, NotificationFunction callback, uint user, out uint handle);
    delegate int DelNotificationReq(int port, ref AmsAddr address, uint handle);

    readonly string explicitPath, targetNetId;
    readonly int targetPort, timeoutMs;
    readonly SemaphoreSlim gate = new(1, 1);
    readonly ConcurrentDictionary<uint, uint> notifications = new();
    IntPtr library;
    int port;
    AmsAddr address;
    PortOpen? open; PortClose? close; GetLocalAddress? local; SetTimeout? setTimeout; ReadDeviceInfo? deviceInfo; ReadState? readState;
    ReadReq? read; ReadWriteReq? readWrite; WriteReq? write; AddNotificationReq? addNotification; DelNotificationReq? delNotification;
    readonly NotificationFunction callback;

    public AdsNativeTransport(string dllPath, string targetNetId, int targetPort, int timeoutMs)
    {
        explicitPath = dllPath; this.targetNetId = targetNetId; this.targetPort = targetPort; this.timeoutMs = timeoutMs;
        // Kept alive for the transport lifetime: the router calls it from its own thread.
        callback = OnRouterNotification;
    }

    public bool IsOpen => port > 0;
    public Dictionary<string, object?> Info { get; } = new();
    public AdsNotificationCallback? OnNotification { get; set; }

    static IEnumerable<string> Candidates(string explicitPath)
    {
        if (explicitPath.Length > 0)
        {
            if (!Path.IsPathRooted(explicitPath)) throw new SourceException("Percorso TcAdsDll: inserire un percorso assoluto");
            yield return explicitPath;
            yield break;
        }
        var bases = new List<string> { (Environment.GetEnvironmentVariable("SystemDrive") ?? "C:") + "\\TwinCAT" };
        foreach (var key in new[] { "TWINCAT3DIR", "TWINCAT2DIR" })
            if (Environment.GetEnvironmentVariable(key) is { Length: > 0 } dir) bases.Add(dir);
        if (OperatingSystem.IsWindows())
            foreach (var key in new[] { @"SOFTWARE\Beckhoff\TwinCAT3", @"SOFTWARE\WOW6432Node\Beckhoff\TwinCAT3" })
                foreach (var field in new[] { "InstallDir", "TwinCATDir" })
                    try { if (Microsoft.Win32.Registry.LocalMachine.OpenSubKey(key)?.GetValue(field) is string value) bases.Add(value); } catch (Exception) { }
        foreach (var b in bases)
            foreach (var directory in new[] { b, Path.GetDirectoryName(b.TrimEnd('\\', '/')) ?? b })
                foreach (var rel in new[] { "Common64/TcAdsDll.dll", "3.1/Common64/TcAdsDll.dll", "AdsApi/TcAdsDll/x64/TcAdsDll.dll" })
                    yield return Path.Combine(directory, rel);
        yield return Path.Combine(Environment.GetEnvironmentVariable("SystemRoot") ?? @"C:\Windows", "System32", "TcAdsDll.dll");
    }

    T Function<T>(string name) where T : Delegate =>
        Marshal.GetDelegateForFunctionPointer<T>(NativeLibrary.GetExport(library, name));

    void Load()
    {
        if (!OperatingSystem.IsWindows()) throw new SourceException("TwinCAT locale richiede Windows e TcAdsDll a 64 bit installata sul PC");
        var errors = new List<string>();
        foreach (var path in Candidates(explicitPath).Distinct())
        {
            if (!File.Exists(path)) continue;
            try
            {
                // Load by absolute path: never search the working directory for a DLL.
                library = NativeLibrary.Load(Path.GetFullPath(path));
                open = Function<PortOpen>("AdsPortOpenEx"); close = Function<PortClose>("AdsPortCloseEx");
                local = Function<GetLocalAddress>("AdsGetLocalAddressEx"); setTimeout = Function<SetTimeout>("AdsSyncSetTimeoutEx");
                deviceInfo = Function<ReadDeviceInfo>("AdsSyncReadDeviceInfoReqEx"); readState = Function<ReadState>("AdsSyncReadStateReqEx");
                read = Function<ReadReq>("AdsSyncReadReqEx2"); readWrite = Function<ReadWriteReq>("AdsSyncReadWriteReqEx2");
                write = Function<WriteReq>("AdsSyncWriteReqEx");
                try
                {
                    addNotification = Function<AddNotificationReq>("AdsSyncAddDeviceNotificationReqEx");
                    delNotification = Function<DelNotificationReq>("AdsSyncDelDeviceNotificationReqEx");
                }
                catch (EntryPointNotFoundException) { }
                Info["dll_path"] = path;
                return;
            }
            catch (Exception exc) when (exc is DllNotFoundException or BadImageFormatException or EntryPointNotFoundException) { errors.Add(path + ": " + exc.Message); }
        }
        throw new SourceException("TcAdsDll x64 non disponibile. Installare i componenti ADS/TwinCAT sul PC o indicare il percorso della DLL a 64 bit. "
            + (errors.Count > 0 ? string.Join("; ", errors) : "Ricerca eseguita nelle cartelle TwinCAT Common64 e System32."));
    }

    static void Check(int code)
    {
        if (code != 0) throw new AdsException(unchecked((uint)code), "TwinCAT locale");
    }

    public Task Open(CancellationToken ct) => Task.Run(OpenCore, ct);

    unsafe void OpenCore()
    {
        Load();
        port = open!();
        if (port <= 0) throw new SourceException("Router TwinCAT locale non disponibile: verificare che il servizio TwinCAT System sia avviato");
        try
        {
            Check(setTimeout!(port, timeoutMs));
            var me = new AmsAddr();
            Check(local!(port, ref me));
            var netId = AdsProtocol.NetId(targetNetId);
            fixed (byte* p = address.NetId) for (var i = 0; i < 6; i++) p[i] = netId[i];
            address.Port = (ushort)targetPort;
            var parts = new string[6];
            for (var i = 0; i < 6; i++) parts[i] = me.NetId[i].ToString(System.Globalization.CultureInfo.InvariantCulture);
            var sourceId = string.Join('.', parts);
            Info["transport"] = "TwinCAT locale / TcAdsDll"; Info["source_netid"] = sourceId; Info["source_port"] = (long)me.Port;
            Info["local_ip"] = "gestito dal router TwinCAT"; Info["timestamps"] = "Local only (ADS Read)";
        }
        catch
        {
            close!(port); port = 0;
            throw;
        }
    }

    /// <summary>The router replies are re-encoded as the ADS/TCP payloads they replace.</summary>
    public async Task<byte[]> Command(ushort command, byte[] data, CancellationToken ct)
    {
        await gate.WaitAsync(ct);
        try
        {
            // A cancelled caller must not close the port while a native request still runs.
            return await Task.Run(() => NativeCommand(command, data), CancellationToken.None);
        }
        finally { gate.Release(); }
    }

    byte[] NativeCommand(ushort command, byte[] data)
    {
        if (port <= 0) throw new SourceException("Porta ADS locale chiusa");
        switch (command)
        {
            case 1:
            {
                var name = new byte[16]; var version = new byte[4];
                Check(deviceInfo!(port, ref address, name, version));
                return [0, 0, 0, 0, .. version, .. name];
            }
            case 4:
            {
                Check(readState!(port, ref address, out var state, out var device));
                var reply = new byte[8];
                BinaryPrimitives.WriteUInt16LittleEndian(reply.AsSpan(4), state);
                BinaryPrimitives.WriteUInt16LittleEndian(reply.AsSpan(6), device);
                return reply;
            }
            case 3:
            {
                var group = BinaryPrimitives.ReadUInt32LittleEndian(data); var offset = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4));
                var length = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(8));
                if (group != 0xF006 || offset != 0 || length != 4 || data.Length != 16) throw new SourceException("Scrittura PLC non consentita");
                Check(write!(port, ref address, group, offset, length, data[12..]));
                return new byte[4];
            }
            case 2:
            case 9:
            {
                var group = BinaryPrimitives.ReadUInt32LittleEndian(data); var offset = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(4));
                var size = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(8));
                if (size > AdsProtocol.MaxPacket) throw new SourceException("Lettura ADS troppo grande");
                var buffer = new byte[size];
                uint count;
                if (command == 2) Check(read!(port, ref address, group, offset, size, buffer, out count));
                else
                {
                    if (group is not (0xF003 or 0xF009 or 0xF011 or 0xF080)) throw new SourceException("ADS ReadWrite non consentita");
                    var length = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(12));
                    if (data.Length != 16 + length) throw new SourceException("Payload ADS non valido");
                    Check(readWrite!(port, ref address, group, offset, size, buffer, length, data[16..], out count));
                }
                if (count > size) throw new InvalidDataException("Lunghezza ADS DLL non valida");
                var reply = new byte[8 + count];
                BinaryPrimitives.WriteUInt32LittleEndian(reply.AsSpan(4), count);
                buffer.AsSpan(0, (int)count).CopyTo(reply.AsSpan(8));
                return reply;
            }
            default: throw new SourceException("Comando ADS non consentito");
        }
    }

    public async Task Abort()
    {
        await gate.WaitAsync();
        try
        {
            if (port > 0)
            {
                var p = port; port = 0;
                await Task.Run(() => close!(p));
            }
        }
        finally { gate.Release(); }
    }

    // Runs on the router thread: copy bytes and clocks only.
    // Called by the TcAdsDll router on its own thread: an exception escaping here would end the whole
    // process, so any failure becomes an invalid notification that the acquisition reports and stops on.
    void OnRouterNotification(IntPtr addr, IntPtr header, uint user)
    {
        var receivedNs = Core.Clock.UtcNs(); var receivedMono = Core.Clock.MonoNs();
        long ticks = 0;
        byte[] data;
        try
        {
            ticks = Marshal.ReadInt64(header, 4);
            var size = Marshal.ReadInt32(header, 12);
            if (size < 0 || size > MaxNotificationBytes) throw new InvalidDataException("Dimensione notifica ADS non valida: " + size);
            data = new byte[size];
            Marshal.Copy(header + 16, data, 0, size);
        }
        catch (Exception exc)
        {
            Core.EngineLog.Info("Notifica TcAdsDll non leggibile: " + exc.Message);
            data = Array.Empty<byte>();
        }
        try { OnNotification?.Invoke(user, ticks, data, receivedNs, receivedMono); }
        catch (Exception exc) { Core.EngineLog.Info("Errore nella notifica TcAdsDll: " + exc); }
    }

    const int MaxNotificationBytes = 16 * 1024 * 1024;

    public async Task<uint> AddNotification(uint group, uint offset, uint length, uint maxDelay, uint cycle, uint user, CancellationToken ct)
    {
        if (addNotification == null) throw new SourceException("TcAdsDll non espone le notifiche ADS Ex richieste");
        var attrib = new NotificationAttrib { Length = length, Mode = 3, MaxDelay = maxDelay, Cycle = cycle };
        uint handle = 0;
        await Task.Run(() => Check(addNotification(port, ref address, group, offset, ref attrib, callback, user, out handle)), CancellationToken.None);
        notifications[handle] = user;
        return handle;
    }

    public async Task DeleteNotification(uint handle, CancellationToken ct)
    {
        notifications.TryRemove(handle, out _);
        await Task.Run(() => Check(delNotification!(port, ref address, handle)), CancellationToken.None);
    }
}
