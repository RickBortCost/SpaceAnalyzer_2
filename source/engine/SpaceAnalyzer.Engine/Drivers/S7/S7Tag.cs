using System.Buffers.Binary;
using System.Globalization;
using System.Numerics;
using System.Text;
using System.Text.RegularExpressions;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Drivers.S7;

/// <summary>An absolute S7 address (s7_client.parse_tag) or a UDP payload field (s7_catalog.parse_node).</summary>
public sealed record S7Tag(string NodeId, string Address, string Kind, string DataType, int Size, int Offset, int Bit, int Db, string Group, int Area,
    bool Folder = false, string Mode = "classic", string Name = "");

public static class S7Types
{
    /// <summary>Big-endian struct formats of s7_client.SCALARS.</summary>
    public static readonly Dictionary<string, (char fmt, int size)> Scalars = new()
    {
        ["BOOL"] = ('B', 1), ["BYTE"] = ('B', 1), ["USINT"] = ('B', 1), ["SINT"] = ('b', 1),
        ["WORD"] = ('H', 2), ["UINT"] = ('H', 2), ["INT"] = ('h', 2), ["DWORD"] = ('I', 4),
        ["UDINT"] = ('I', 4), ["DINT"] = ('i', 4), ["REAL"] = ('f', 4), ["LREAL"] = ('d', 8),
        ["LINT"] = ('q', 8), ["ULINT"] = ('Q', 8), ["LWORD"] = ('Q', 8), ["TIME"] = ('i', 4),
    };

    static readonly Regex Address = new(@"^(DB([1-9]\d{0,4})\.DB([XBWD])|(M|I|Q|E|A)([BWD]?))(\d{1,7})(?:\.([0-7]))?:([A-Z]+)(?:\[(\d{1,3})\])?$", RegexOptions.Compiled);
    static readonly Regex FolderPattern = new(@"^(/|/(?:DB[1-9]\d{0,4}|M|I|Q))$", RegexOptions.Compiled);
    static readonly Regex Udp = new(@"^s7:udp:(\d{1,4})(?:\.([0-7]))?:([A-Za-z]+)(?:\[(\d{1,3})\])?$", RegexOptions.Compiled);

    public static S7Tag ParseTag(string value, bool allowFolder = false)
    {
        value = value.Trim();
        if (value.StartsWith("s7:", StringComparison.Ordinal)) value = value[3..];
        value = value.ToUpperInvariant();
        if (allowFolder && FolderPattern.IsMatch(value))
        {
            if (value.StartsWith("/DB", StringComparison.Ordinal) && int.Parse(value[3..], CultureInfo.InvariantCulture) > 65535) throw new ArgumentException("Numero DB massimo 65535");
            return new S7Tag("s7:" + value, "", "", "", 0, 0, 0, 0, value.TrimStart('/'), 0, Folder: true);
        }
        var m = Address.Match(value);
        if (!m.Success || value.EndsWith('\n')) throw new ArgumentException("S7: usare DB1.DBD0:REAL, DB1.DBX4.0:BOOL, MW10:INT oppure DB1.DBB20:STRING[40]");
        var db = m.Groups[2].Success ? int.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture) : 0;
        var offset = int.Parse(m.Groups[6].Value, CultureInfo.InvariantCulture);
        var area = m.Groups[4].Success ? m.Groups[4].Value switch { "E" => "I", "A" => "Q", var a => a } : "";
        var width = m.Groups[3].Success ? m.Groups[3].Value : m.Groups[5].Success && m.Groups[5].Value.Length > 0 ? m.Groups[5].Value : "X";
        var bit = m.Groups[7].Success ? m.Groups[7].Value : null;
        var kind = m.Groups[8].Value;
        var length = m.Groups[9].Success ? m.Groups[9].Value : null;
        if (db > 65535) throw new ArgumentException("Numero DB massimo 65535");
        int size;
        if (kind == "STRING")
        {
            if (length == null || int.Parse(length, CultureInfo.InvariantCulture) is < 1 or > 254) throw new ArgumentException("STRING: lunghezza dichiarata da 1 a 254");
            size = int.Parse(length, CultureInfo.InvariantCulture) + 2;
        }
        else if (Scalars.TryGetValue(kind, out var scalar) && length == null) size = scalar.size;
        else throw new ArgumentException("Tipo S7 non supportato: " + kind);
        if (kind == "BOOL")
        {
            if (width != "X" || bit == null) throw new ArgumentException("BOOL richiede un bit: DB1.DBX0.0 oppure M0.0");
        }
        else if (bit != null || width == "X") throw new ArgumentException("Il tipo numerico richiede un indirizzo byte/word/dword senza bit");
        else if (width != (size == 1 || kind == "STRING" ? "B" : size == 2 ? "W" : "D"))
            throw new ArgumentException("Larghezza indirizzo non coerente con il tipo: B=byte, W=16 bit, D=32/64 bit");
        if (offset + size > 2097152) throw new ArgumentException("Offset S7 oltre il campo indirizzi a 24 bit");
        var group = db != 0 ? "DB" + db : area;
        var address = db != 0 ? $"DB{db}.DB{width}{offset}" : area + (width == "X" ? "" : width) + offset;
        if (bit != null) address += "." + bit;
        var dataType = kind + (length != null ? "[" + int.Parse(length, CultureInfo.InvariantCulture) + "]" : "");
        var areaCode = db != 0 ? 0x84 : area switch { "I" => 0x81, "Q" => 0x82, _ => 0x83 };
        return new S7Tag("s7:" + address + ":" + dataType, address, kind, dataType, size, offset, bit != null ? bit[0] - '0' : 0, db, group, areaCode);
    }

    /// <summary>s7_catalog.parse_node: symbolic names, UDP payload fields or classic addresses.</summary>
    public static S7Tag ParseNode(string value, bool allowFolder = false)
    {
        value = value.Trim();
        if (value.StartsWith("s7:sym:", StringComparison.Ordinal))
        {
            var name = value[7..];
            var folder = name.StartsWith('/');
            if (name.Length == 0 || name.Length > 2048 || name.Any(c => c < 32)) throw new ArgumentException("Nome simbolico S7 non valido");
            if (folder && !allowFolder) throw new ArgumentException("Selezionare una variabile, non un ramo S7");
            return new S7Tag(value, "", "", "", 0, 0, 0, 0, "", 0, Folder: folder, Mode: "symbolic", Name: folder ? name.TrimStart('/') : name);
        }
        if (value == "s7:udp:/")
        {
            if (!allowFolder) throw new ArgumentException("Selezionare un campo del telegramma UDP");
            return new S7Tag(value, "", "", "", 0, 0, 0, 0, "", 0, Folder: true, Mode: "udp");
        }
        if (value.StartsWith("s7:udp:", StringComparison.Ordinal))
        {
            var m = Udp.Match(value);
            if (!m.Success || value.EndsWith('\n')) throw new ArgumentException("UDP: usare s7:udp:0:REAL o s7:udp:4.0:BOOL (offset dal payload)");
            var offset = int.Parse(m.Groups[1].Value, CultureInfo.InvariantCulture);
            var bit = m.Groups[2].Success ? m.Groups[2].Value : null;
            var kind = m.Groups[3].Value.ToUpperInvariant();
            var length = m.Groups[4].Success ? m.Groups[4].Value : null;
            var scalarSize = Scalars.TryGetValue(kind, out var s) ? s.size : 0;
            var width = kind == "BOOL" ? "X" : kind == "STRING" || scalarSize == 1 ? "B" : scalarSize == 2 ? "W" : "D";
            var address = $"DB1.DB{width}{offset}" + (bit != null ? "." + bit : "");
            var tag = ParseTag(address + ":" + kind + (length != null ? "[" + length + "]" : ""));
            if (tag.Offset + tag.Size > 4090) throw new ArgumentException("Campo UDP oltre il payload massimo di 4090 byte");
            return tag with { NodeId = "s7:udp:" + offset + (bit != null ? "." + bit : "") + ":" + tag.DataType, Mode = "udp" };
        }
        return ParseTag(value, allowFolder);
    }

    /// <summary>s7_client.decode: exact big-endian value; STRING as cp1252 with replacement.</summary>
    public static object? Decode(S7Tag tag, ReadOnlySpan<byte> raw, bool littleEndian = false)
    {
        if (raw.Length != tag.Size) throw new ArgumentException("Lunghezza valore S7 inattesa");
        if (tag.Kind == "STRING")
        {
            int maximum = raw[0], length = raw[1];
            if (maximum != tag.Size - 2 || length > maximum) throw new ArgumentException("STRING: lunghezza dichiarata diversa dal DB");
            return Cp1252.Decode(raw.Slice(2, length));
        }
        var value = Scalar(Scalars[tag.Kind].fmt, raw, littleEndian && tag.Kind != "BOOL");
        return tag.Kind == "BOOL" ? ((long)value! & (1L << tag.Bit)) != 0 : value;
    }

    /// <summary>struct.unpack of one scalar: Python int (long/ulong) or float (double).</summary>
    public static object? Scalar(char fmt, ReadOnlySpan<byte> raw, bool little = false) => fmt switch
    {
        'B' => (long)raw[0],
        'b' => (long)(sbyte)raw[0],
        'H' => (long)(little ? BinaryPrimitives.ReadUInt16LittleEndian(raw) : BinaryPrimitives.ReadUInt16BigEndian(raw)),
        'h' => (long)(little ? BinaryPrimitives.ReadInt16LittleEndian(raw) : BinaryPrimitives.ReadInt16BigEndian(raw)),
        'I' => (long)(little ? BinaryPrimitives.ReadUInt32LittleEndian(raw) : BinaryPrimitives.ReadUInt32BigEndian(raw)),
        'i' => (long)(little ? BinaryPrimitives.ReadInt32LittleEndian(raw) : BinaryPrimitives.ReadInt32BigEndian(raw)),
        'q' => little ? BinaryPrimitives.ReadInt64LittleEndian(raw) : BinaryPrimitives.ReadInt64BigEndian(raw),
        'Q' => ULong(little ? BinaryPrimitives.ReadUInt64LittleEndian(raw) : BinaryPrimitives.ReadUInt64BigEndian(raw)),
        'f' => (double)(little ? BinaryPrimitives.ReadSingleLittleEndian(raw) : BinaryPrimitives.ReadSingleBigEndian(raw)),
        'd' => little ? BinaryPrimitives.ReadDoubleLittleEndian(raw) : BinaryPrimitives.ReadDoubleBigEndian(raw),
        _ => throw new ArgumentException("Formato non supportato"),
    };

    static object ULong(ulong v) => v <= long.MaxValue ? (long)v : new BigInteger(v);

    public static bool IsFiniteNumber(object? value) => value switch
    {
        bool or long or BigInteger => true,
        double d => double.IsFinite(d),
        _ => false,
    };

    public static double ToDouble(object? value) => value switch
    {
        bool b => b ? 1 : 0,
        long l => l,
        BigInteger big => (double)big,
        double d => d,
        _ => double.NaN,
    };

    static readonly BigInteger Safe = 9007199254740991;

    /// <summary>abs(int) beyond 2^53-1 (bool excluded).</summary>
    public static bool Inexact(object? value) => value switch
    {
        long l => BigInteger.Abs(l) > Safe,
        BigInteger big => BigInteger.Abs(big) > Safe,
        _ => false,
    };
}

/// <summary>Python's cp1252 codec: five undefined bytes decode to U+FFFD with errors='replace'.</summary>
public static class Cp1252
{
    static readonly string High = "€�‚ƒ„…†‡ˆ‰Š‹Œ�Ž�" +
                                  "�‘’“”•–—˜™š›œ�žŸ";

    public static string Decode(ReadOnlySpan<byte> data)
    {
        var sb = new StringBuilder(data.Length);
        foreach (var b in data) sb.Append(b is >= 0x80 and <= 0x9F ? High[b - 0x80] : (char)b);
        return sb.ToString();
    }
}
