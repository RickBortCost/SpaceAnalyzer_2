using System.Buffers.Binary;
using System.IO.Compression;
using System.IO.Hashing;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.Encodings.Web;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// Space Analyzer Primary Capture v1 (see source/primary_format.py, the reference reader).
/// File: magic SACAP001, version, header length, CRC32 + JSON header.
/// Block: "SAB1", kind, flags, reserved, sequence, first sample, count, stored/raw sizes, CRC32.
/// </summary>
public static class SacFormat
{
    public const int Version = 1;
    public const byte Data = 1, Event = 2, Finish = 3;
    public const int MaxHeader = 8 * 1024 * 1024, MaxPayload = 32 * 1024 * 1024;
    public static readonly byte[] Magic = "SACAP001"u8.ToArray();
    static readonly byte[] BlockMagic = "SAB1"u8.ToArray();

    public static readonly string[] Fields =
    [
        "timestamp_ms", "received_ns", "channel", "connection_id", "sample_sequence", "connection_sequence",
        "source_epoch", "source_sequence", "value", "data_type", "quality", "status", "status_code", "source_ts",
        "server_ts", "source_filetime", "local_ts", "source_value", "source_picoseconds", "server_picoseconds",
        "acquisition_origin", "sample_ms", "clock_discontinuity", "acquisition_timestamp_ms", "source_binary",
    ];

    public static readonly JsonWriterOptions Compact = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, Indented = false };

    public static void WriteHeader(Stream stream, byte[] payload)
    {
        if (payload.Length > MaxHeader) throw new InvalidDataException("Metadata primari troppo grandi");
        Span<byte> prefix = stackalloc byte[20];
        Magic.CopyTo(prefix);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix[8..], Version);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix[12..], (uint)payload.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix[16..], Crc32.HashToUInt32(payload));
        stream.Write(prefix);
        stream.Write(payload);
    }

    public static void WriteBlock(Stream stream, byte kind, ulong sequence, ulong first, uint count, byte[] payload, bool compress)
    {
        if (payload.Length > MaxPayload) throw new InvalidDataException("Blocco primario oltre il limite");
        var flags = compress && payload.Length > 1024 ? (byte)1 : (byte)0;
        var stored = flags == 1 ? Zlib(payload) : payload;
        Span<byte> prefix = stackalloc byte[36];
        BlockMagic.CopyTo(prefix);
        prefix[4] = kind;
        prefix[5] = flags;
        BinaryPrimitives.WriteUInt16LittleEndian(prefix[6..], 0);
        BinaryPrimitives.WriteUInt64LittleEndian(prefix[8..], sequence);
        BinaryPrimitives.WriteUInt64LittleEndian(prefix[16..], first);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix[24..], count);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix[28..], (uint)stored.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(prefix[32..], (uint)payload.Length);
        var crc = new Crc32();
        crc.Append(prefix);
        crc.Append(stored);
        Span<byte> checksum = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32LittleEndian(checksum, crc.GetCurrentHashAsUInt32());
        stream.Write(prefix);
        stream.Write(checksum);
        stream.Write(stored);
    }

    /// <summary>zlib stream, level 1 (zlib.compress(payload, 1)).</summary>
    static byte[] Zlib(byte[] payload)
    {
        using var output = new MemoryStream(payload.Length / 3 + 64);
        using (var zlib = new ZLibStream(output, CompressionLevel.Fastest, leaveOpen: true)) zlib.Write(payload);
        return output.ToArray();
    }

    public static byte[] Json(Action<Utf8JsonWriter> write)
    {
        using var buffer = new MemoryStream();
        using (var w = new Utf8JsonWriter(buffer, Compact)) write(w);
        return buffer.ToArray();
    }

    /// <summary>atomic_json(): write a temporary file, fsync, replace, sync the directory.</summary>
    public static void AtomicJson(string path, Action<Utf8JsonWriter> write)
    {
        var temporary = path + ".tmp";
        using (var stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            using (var w = new Utf8JsonWriter(stream, new JsonWriterOptions { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, Indented = true }))
                write(w);
            stream.Flush(true);
        }
        File.Move(temporary, path, overwrite: true);
        SyncDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
    }

    /// <summary>fsync of a directory entry; Windows has no equivalent (as in the Python writer).</summary>
    public static void SyncDirectory(string directory)
    {
        if (OperatingSystem.IsWindows()) return;
        var fd = Posix.open(directory, 0);
        if (fd < 0) return;
        try { Posix.fsync(fd); }
        finally { Posix.close(fd); }
    }

    static class Posix
    {
        [DllImport("libc", SetLastError = true)] public static extern int open([MarshalAs(UnmanagedType.LPUTF8Str)] string path, int flags);
        [DllImport("libc", SetLastError = true)] public static extern int fsync(int fd);
        [DllImport("libc", SetLastError = true)] public static extern int close(int fd);
    }

    public static string Utf8(byte[] data) => Encoding.UTF8.GetString(data);
}
