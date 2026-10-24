using System.Buffers.Binary;
using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using SpaceAnalyzer.Engine.Drivers.S7;

namespace SpaceAnalyzer.Engine.Drivers.Ads;

/// <summary>An ADS datatype declaration (ads_types.parse_type).</summary>
public sealed class AdsTypeDecl
{
    public string Name = "", Type = "", Comment = "";
    public uint Size, Offset, Datatype, Flags;
    public List<(long lo, long hi)> Bounds = new();
    public List<AdsTypeDecl> Children = new();
}

/// <summary>
/// Datatype declarations and read-only symbolic member traversal (ads_types.py). Unknown
/// trailing extensions are skipped by entry length: browsing never invokes methods or
/// dereferences PLC pointers.
/// </summary>
public static class AdsTypes
{
    public const int MaxTypesBytes = 64 * 1024 * 1024 - 64;
    public const int MaxDepth = 64;
    public static readonly Dictionary<string, uint> Native = new()
    {
        ["BOOL"] = 33, ["SINT"] = 16, ["USINT"] = 17, ["BYTE"] = 17, ["INT"] = 2, ["UINT"] = 18, ["WORD"] = 18, ["DINT"] = 3, ["UDINT"] = 19,
        ["DWORD"] = 19, ["LINT"] = 20, ["ULINT"] = 21, ["LWORD"] = 21, ["REAL"] = 4, ["LREAL"] = 5, ["TIME"] = 19, ["DATE"] = 19, ["TOD"] = 19,
        ["DT"] = 19, ["LTIME"] = 21,
    };

    public static (AdsTypeDecl decl, int length) ParseType(ReadOnlySpan<byte> data, int offset, string encoding, int depth, int[] budget)
    {
        budget[0]--;
        if (depth > MaxDepth || budget[0] < 0) throw new InvalidDataException("Datatype ADS troppo complesso");
        if (data.Length - offset < 42) throw new InvalidDataException("Header datatype ADS troncato");
        var d = data[offset..];
        var u = new uint[8];
        for (var i = 0; i < 8; i++) u[i] = BinaryPrimitives.ReadUInt32LittleEndian(d[(4 * i)..]);
        var h = new int[5];
        for (var i = 0; i < 5; i++) h[i] = BinaryPrimitives.ReadUInt16LittleEndian(d[(32 + 2 * i)..]);
        uint U(int i) => u[i];
        int H(int i) => h[i];
        var length = (int)U(0);
        var decl = new AdsTypeDecl { Size = U(4), Offset = U(5), Datatype = U(6), Flags = U(7) };
        int n = H(0), t = H(1), c = H(2), dim = H(3), count = H(4);
        var end = offset + length;
        if (length < 45 + n + t + c + 8 * dim || end > data.Length || length < 0) throw new InvalidDataException("Lunghezza datatype ADS non valida");
        var pos = offset + 42;
        var texts = new string[3];
        var k = 0;
        foreach (var size in new[] { n, t, c })
        {
            if (data[pos + size] != 0) throw new InvalidDataException("Nome datatype ADS non terminato");
            var raw = data.Slice(pos, size);
            texts[k++] = encoding == "cp1252" ? Cp1252.Decode(raw) : Encoding.UTF8.GetString(raw);
            pos += size + 1;
        }
        (decl.Name, decl.Type, decl.Comment) = (texts[0], texts[1], texts[2]);
        for (var i = 0; i < dim; i++)
        {
            var lo = BinaryPrimitives.ReadInt32LittleEndian(data[pos..]);
            var number = BinaryPrimitives.ReadUInt32LittleEndian(data[(pos + 4)..]);
            pos += 8;
            if (number == 0) throw new InvalidDataException("Array ADS a dimensione aperta non espandibile");
            decl.Bounds.Add((lo, lo + (long)number - 1));
        }
        for (var i = 0; i < count; i++)
        {
            var (child, used) = ParseType(data[..end], pos, encoding, depth + 1, budget);
            decl.Children.Add(child);
            pos += used;
        }
        return (decl, length);
    }

    static readonly Regex BlockedPattern = new(@"^(POINTER\s+TO|REFERENCE\s+TO|PVOID\b|INTERFACE\b)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    public static bool Blocked(string name) => BlockedPattern.IsMatch(name.Trim());

    static readonly Regex ArrayPattern = new(@"^ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)\z", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    static readonly Regex Bound = new(@"^\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*\z", RegexOptions.Compiled);

    /// <summary>ads_types.array_info: raises on unusable bounds (array_shape returns null instead).</summary>
    public static (List<(long lo, long hi)> bounds, string baseType)? ArrayInfo(string name)
    {
        var m = ArrayPattern.Match(name.Trim());
        if (!m.Success) return null;
        var bounds = new List<(long, long)>();
        foreach (var part in m.Groups[1].Value.Split(','))
        {
            var r = Bound.Match(part);
            if (!r.Success) throw new InvalidDataException("Limiti array ADS non riconosciuti");
            var a = long.Parse(r.Groups[1].Value, CultureInfo.InvariantCulture);
            var b = long.Parse(r.Groups[2].Value, CultureInfo.InvariantCulture);
            if (b < a) throw new InvalidDataException("Limiti array ADS non validi");
            bounds.Add((a, b));
        }
        return (bounds, m.Groups[2].Value.Trim());
    }
}

/// <summary>Resolves members and array elements from PLC datatype declarations.</summary>
public sealed class DatatypeBrowser(AdsClient client)
{
    readonly Dictionary<string, AdsTypeDecl> types = new();
    bool uploaded, byName = true;
    string encoding = "utf-8";

    static string Key(string name) => name.ToLowerInvariant();

    public async Task<AdsTypeDecl> Declaration(string name, CancellationToken ct)
    {
        var key = Key(name);
        if (types.TryGetValue(key, out var known)) return known;
        try
        {
            if (!byName) throw new KeyNotFoundException();
            var data = await client.ReadWrite(0xF011, 0, 4 * 1024 * 1024, Encoding(name), ct);
            var (result, used) = AdsTypes.ParseType(data, 0, encoding, 0, [100000]);
            if (used != data.Length) throw new InvalidDataException("Datatype ADS con lunghezza incoerente");
            types[key] = result;
            return result;
        }
        catch (Exception exc) when (exc is KeyNotFoundException || exc is AdsException { Code: 1793 or 1794 or 1797 or 1808 })
        {
            if (exc is AdsException { Code: 1793 or 1794 }) byName = false;
            await Upload(ct);
            return types.TryGetValue(key, out var found) ? found : throw new InvalidDataException("Datatype non pubblicato dal PLC: " + name);
        }
    }

    byte[] Encoding(string name) => [.. (encoding == "cp1252" ? Recording.Legacy.Cp1252Strict : System.Text.Encoding.UTF8).GetBytes(name), 0];

    async Task Upload(CancellationToken ct)
    {
        if (uploaded) return;
        byte[] info;
        try { info = await client.Read(0xF00F, 0, 64, ct); }
        catch (Exception exc) when (exc is not OperationCanceledException)
        {
            if (exc is not AdsException { Code: 1797 }) throw new InvalidDataException("Il PLC non rende disponibile la tabella datatype ADS: " + exc.Message);
            info = await client.Read(0xF00F, 0, 24, ct);
        }
        if (info.Length < 24) throw new InvalidDataException("UploadInfo2 ADS non disponibile: impossibile espandere strutture/FB");
        var count = BinaryPrimitives.ReadUInt32LittleEndian(info.AsSpan(8));
        var size = BinaryPrimitives.ReadUInt32LittleEndian(info.AsSpan(12));
        if (info.Length >= 32) encoding = BinaryPrimitives.ReadUInt32LittleEndian(info.AsSpan(28)) == 65001 ? "utf-8" : "cp1252";
        if (size > AdsTypes.MaxTypesBytes || count > 1000000) throw new InvalidDataException("Catalogo datatype ADS oltre il limite di 64 MiB");
        var data = await client.Read(0xF00E, 0, size, ct);
        var pos = 0;
        var parsed = new Dictionary<string, AdsTypeDecl>();
        var budget = new[] { 1000000 };
        for (var i = 0; i < count; i++)
        {
            var (item, length) = AdsTypes.ParseType(data, pos, encoding, 0, budget);
            pos += length;
            parsed[Key(item.Name)] = item;
        }
        if (pos != data.Length) throw new InvalidDataException("Conteggio datatype ADS incoerente");
        foreach (var (k, v) in parsed) types[k] = v;
        uploaded = true;
    }

    /// <summary>The browsable description of a symbol (members or array bounds), or null for scalars.</summary>
    public async Task<AdsTypeDecl?> Description(AdsSymbol symbol, CancellationToken ct, HashSet<string>? trail = null)
    {
        var name = symbol.Type;
        var key = Key(name);
        if (AdsTypes.Blocked(name)) return null;
        if (symbol.Declaration is { } own && (own.Children.Count > 0 || own.Bounds.Count > 0)) return own;
        if (AdsTypes.ArrayInfo(name) is var (bounds, baseType))
            return new AdsTypeDecl { Name = name, Type = baseType, Bounds = bounds, Size = symbol.Size, Datatype = 65 };
        if (AdsTypes.Native.ContainsKey(name.ToUpperInvariant()) || Regex.IsMatch(name, @"^(W?STRING)(\s*\(|$)", RegexOptions.IgnoreCase)) return null;
        // Native ADS scalars, including enums, have no browsable members.
        if (symbol.Datatype != 65) return null;
        trail ??= new();
        if (trail.Contains(key) || trail.Count >= AdsTypes.MaxDepth) throw new InvalidDataException("Ciclo alias datatype ADS: " + name);
        var desc = await Declaration(name, ct);
        if (desc.Children.Count > 0 || desc.Bounds.Count > 0) return desc;
        if (desc.Type.Length > 0 && Key(desc.Type) != key)
        {
            var alias = symbol.Clone();
            alias.Type = desc.Type; alias.Datatype = desc.Datatype;
            return await Description(alias, ct, [.. trail, key]);
        }
        return null;
    }

    public async Task<(List<AdsSymbol> items, long total)> ChildSymbols(AdsSymbol symbol, long offset, long limit, CancellationToken ct)
    {
        var desc = await Description(symbol, ct);
        if (desc == null) return (new(), 0);
        var result = new List<AdsSymbol>();
        if (desc.Bounds.Count > 0)
        {
            var count = desc.Bounds.Aggregate(1L, (a, b) => a * (b.hi - b.lo + 1));
            if (count == 0 || symbol.Size % count != 0) throw new InvalidDataException("Dimensione array ADS incoerente");
            var size = symbol.Size / (uint)count;
            for (var flat = offset; flat < Math.Min(count, offset + limit); flat++)
            {
                var indexes = new List<long>();
                var remaining = flat;
                for (var i = desc.Bounds.Count - 1; i >= 0; i--)
                {
                    var (a, b) = desc.Bounds[i];
                    indexes.Add(a + remaining % (b - a + 1));
                    remaining /= b - a + 1;
                }
                indexes.Reverse();
                result.Add(new AdsSymbol
                {
                    Name = symbol.Name + "[" + string.Join(',', indexes) + "]", Type = desc.Type, Size = size,
                    Datatype = AdsTypes.Native.GetValueOrDefault(desc.Type.ToUpperInvariant(), 65u), Flags = 0, Group = symbol.Group,
                    Offset = symbol.Offset + (uint)(flat * size), Comment = symbol.Comment, Derived = true,
                });
            }
            return (result, count);
        }
        foreach (var member in desc.Children.Skip((int)offset).Take((int)Math.Min(limit, int.MaxValue)))
        {
            // Metadata includes memory members only; RPC extensions are deliberately ignored.
            result.Add(new AdsSymbol
            {
                Name = symbol.Name + "." + member.Name, Type = member.Type.Length > 0 ? member.Type : member.Name, Size = member.Size,
                Datatype = member.Datatype, Flags = 0, Group = symbol.Group, Offset = symbol.Offset + member.Offset, Comment = member.Comment,
                Derived = true, Declaration = member,
            });
        }
        return (result, desc.Children.Count);
    }

    /// <summary>A member or element path below an uploaded symbol (MAIN.Struct.Value, MAIN.Array[2]).</summary>
    public async Task<AdsSymbol> Resolve(string name, CancellationToken ct)
    {
        if (!client.CatalogLoaded) await client.UploadSymbols(ct);
        var roots = client.Symbols.Keys.Where(n => name == n || name.StartsWith(n + ".", StringComparison.Ordinal) || name.StartsWith(n + "[", StringComparison.Ordinal)).ToList();
        if (roots.Count == 0) throw new InvalidDataException("Simbolo ADS non trovato: " + name);
        var current = client.Symbols[roots.OrderByDescending(r => r.Length).First()];
        var tail = name[current.Name.Length..];
        var depth = 0;
        while (tail.Length > 0)
        {
            if (++depth > AdsTypes.MaxDepth) throw new InvalidDataException("Profondita ADS oltre 64 livelli");
            Match match;
            if (tail.StartsWith('.'))
            {
                match = Regex.Match(tail, @"^\.([^.[\]]+)");
                if (!match.Success) throw new InvalidDataException("Percorso membro ADS non valido");
                var part = match.Groups[1].Value;
                var desc = await Description(current, ct);
                if (desc == null || desc.Bounds.Count > 0) throw new InvalidDataException("Il tipo non contiene membri: " + current.Type);
                var index = desc.Children.FindIndex(m => Key(m.Name) == Key(part));
                if (index < 0) throw new InvalidDataException("Membro ADS non trovato: " + part);
                current = (await ChildSymbols(current, index, 1, ct)).items[0];
            }
            else if (tail.StartsWith('['))
            {
                match = Regex.Match(tail, @"^\[([^]]+)\]");
                if (!match.Success) throw new InvalidDataException("Indice array non valido");
                var bounds = (await Description(current, ct))?.Bounds ?? new();
                List<long> indexes;
                try { indexes = match.Groups[1].Value.Split(',').Select(p => long.Parse(p.Trim(), NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture)).ToList(); }
                catch (FormatException) { throw new InvalidDataException("Indici array ADS non validi"); }
                if (indexes.Count != bounds.Count) throw new InvalidDataException("Numero dimensioni array ADS errato");
                long flat = 0;
                for (var i = 0; i < indexes.Count; i++)
                {
                    var (a, b) = bounds[i];
                    if (indexes[i] < a || indexes[i] > b) throw new InvalidDataException("Indice fuori dai limiti array ADS");
                    flat = flat * (b - a + 1) + indexes[i] - a;
                }
                current = (await ChildSymbols(current, flat, 1, ct)).items[0];
            }
            else throw new InvalidDataException("Percorso ADS non valido");
            tail = tail[match.Length..];
            client.Symbols[current.Name] = current;
        }
        return current;
    }
}
