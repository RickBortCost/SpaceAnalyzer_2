using System.Buffers.Binary;
using System.Text.Json;

namespace SpaceAnalyzer.Engine.Ipc;

/// <summary>
/// Frames on the inherited stdin/stdout pipes: u32 little-endian payload length,
/// one type byte, then a UTF-8 JSON payload. No sockets, no ports.
/// </summary>
public sealed class Host
{
    public const byte Request = 1, Response = 2, Event = 3, Rows = 4, Log = 5;
    const int MaxFrame = 64 * 1024 * 1024;

    readonly Stream input;
    readonly Stream output;
    readonly object writeGate = new();

    public Host(Stream input, Stream output)
    {
        this.input = input;
        this.output = output;
    }

    public (byte type, byte[] payload)? Read()
    {
        Span<byte> header = stackalloc byte[5];
        if (!ReadExact(header)) return null;
        var length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length < 0 || length > MaxFrame) throw new InvalidDataException("Frame IPC non valido");
        var payload = new byte[length];
        if (!ReadExact(payload)) return null;
        return (header[4], payload);
    }

    bool ReadExact(Span<byte> buffer)
    {
        var read = 0;
        while (read < buffer.Length)
        {
            var n = input.Read(buffer[read..]);
            if (n == 0) return false;
            read += n;
        }
        return true;
    }

    public void Send(byte type, byte[] payload)
    {
        Span<byte> header = stackalloc byte[5];
        BinaryPrimitives.WriteInt32LittleEndian(header, payload.Length);
        header[4] = type;
        lock (writeGate)
        {
            output.Write(header);
            output.Write(payload);
            output.Flush();
        }
    }

    public void Send(byte type, Action<Utf8JsonWriter> write) => Send(type, Recording.SacFormat.Json(write));

    public void Emit(string kind, string? connectionId, Action<Utf8JsonWriter> details) => Send(Event, w =>
    {
        w.WriteStartObject();
        w.WriteString("kind", kind);
        if (connectionId == null) w.WriteNull("connection_id"); else w.WriteString("connection_id", connectionId);
        w.WriteStartObject("details");
        details(w);
        w.WriteEndObject();
        w.WriteEndObject();
    });
}
