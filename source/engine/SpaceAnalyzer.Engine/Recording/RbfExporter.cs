using System.Buffers.Binary;
using System.Collections.Specialized;
using System.Globalization;
using System.Text;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// RBF v3 conversion of a finalized long-layout original (port of rbf.export_parquet):
/// wide frames sorted by nanosecond timestamp, explicit _Q channel per signal,
/// no interpolation and no carried values.
/// </summary>
public static class RbfExporter
{
    public const int MaxFrames = 57600;
    public const string QualityMarker = "SpaceAnalyzer RBF3 quality 0=missing,1=Bad,2=Uncertain,3=Good";
    static readonly Dictionary<byte, (int size, string name)> Types = new() { [0] = (1, "Boolean"), [2] = (2, "Int16"), [7] = (4, "Float32"), [9] = (81, "String") };

    readonly record struct Staged(long Ticks, long Cycle, int Node, double Value, string Text, byte Q);

    static byte[] Header(IReadOnlyList<(string name, byte type)> channels, uint dataBytes, uint sampleUs)
    {
        if (channels.Count is < 1 or > 4096 || sampleUs < 1) throw new InvalidDataException("Limiti intestazione RBF superati");
        using var data = new MemoryStream();
        var w = new BinaryWriter(data);
        w.Write(3u); w.Write(dataBytes); w.Write((uint)channels.Count); w.Write(sampleUs); w.Write(57600u);
        foreach (var (name, type) in channels)
        {
            byte[] raw;
            try { raw = Legacy.Cp1252Strict.GetBytes(name); }
            catch (EncoderFallbackException) { throw new InvalidDataException("Nome canale RBF non valido"); }
            if (raw.Length is < 1 or > 254 || name.Contains('\0')) throw new InvalidDataException("Nome canale RBF non valido");
            w.Write((byte)raw.Length); w.Write(raw);
            w.Write([type, (byte)Types[type].size, 1, 10]);
        }
        w.Write(new byte[12 * channels.Count]);
        w.Flush();
        return data.ToArray();
    }

    static byte[] SavedState(IReadOnlyList<(string name, byte type)> channels, IReadOnlyList<int> valueChannels)
    {
        var lines = new List<string> { "", "Plotter = 1", "# Plot X Axes", "X-Axis00 = 16777215 1 3 10000 0 0.000000 0.000000",
            "# Plot Y Axes", "Y-Axis00 = 16777215 0.000000 0.000000 19 0 -1", "# Plot Channels" };
        for (var i = 0; i < Math.Min(16, valueChannels.Count); i++)
        {
            var c = valueChannels[i];
            lines.Add($"Channel{i:00} = {channels[c].name} {c} 65535 {i} 0 -1 -1");
        }
        lines.AddRange(["# Properties", "Property0 = Space Fieldbus Analyzer", "Property5 = " + QualityMarker, "END SAVESTATE", ""]);
        return Legacy.Cp1252Strict.GetBytes(string.Join("\r\n", lines));
    }

    static string Stamp(long ticks)
    {
        var seconds = ticks / 1_000_000_000;
        var ns = ticks % 1_000_000_000;
        return DateTime.UnixEpoch.AddSeconds(seconds).ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture) + "." + ns.ToString("000000000", CultureInfo.InvariantCulture) + "+00:00";
    }

    public static List<Dictionary<string, object?>> Export(string original, IReadOnlyList<NodeSpec> nodes, long maxBytes)
    {
        var ids = new Dictionary<string, int>();
        foreach (var node in nodes) ids.TryAdd(node.NodeId, ids.Count);
        if (ids.Count is < 1 or > 64 || ids.Count != nodes.Count) throw new InvalidDataException("RBF: selezionare da 1 a 64 segnali con identificativi distinti");
        var directory = Path.GetDirectoryName(Path.GetFullPath(original))!;
        var sourceMeta = Legacy.SourceMetadata(original);
        var kinds = new byte?[nodes.Count];
        var files = new List<Dictionary<string, object?>>();
        using var sort = new ExternalSort<Staged>(directory,
            (a, b) => a.Ticks != b.Ticks ? a.Ticks.CompareTo(b.Ticks) : a.Cycle.CompareTo(b.Cycle),
            (w, s) => { w.Write(s.Ticks); w.Write(s.Cycle); w.Write(s.Node); w.Write(s.Value); w.Write(s.Text); w.Write(s.Q); },
            r => new Staged(r.ReadInt64(), r.ReadInt64(), r.ReadInt32(), r.ReadDouble(), r.ReadString(), r.ReadByte()));
        foreach (var row in ParquetSource.Read(original))
        {
            if (row.NodeId == null || !ids.TryGetValue(row.NodeId, out var i)) continue;
            var t = row.TimestampMs ?? double.NaN;
            if (!double.IsFinite(t)) throw new InvalidDataException("Timestamp RBF non finito");
            // Decimal(str(t)) * 1e6, half-to-even: nanoseconds from Python's shortest repr.
            var exact = decimal.Parse(Values.PythonRepr(t), NumberStyles.Float, CultureInfo.InvariantCulture) * 1_000_000m;
            var ticks = (long)Math.Round(exact, MidpointRounding.ToEven);
            if (ticks < 0 || ticks >= 0x100000000L * 1_000_000_000L / 1) throw new InvalidDataException("Timestamp RBF oltre il limite UInt32 secondi");
            var dtype = (row.DataType ?? "").ToUpperInvariant();
            byte kind = dtype is "BOOL" or "BOOLEAN" ? (byte)0 : dtype is "STRING" or "WSTRING" or "CHAR" or "WCHAR" ? (byte)9 : (byte)7;
            if (dtype is not ("" or "NULL" or "NONE"))
            {
                if (kinds[i] is byte known && known != kind) throw new InvalidDataException("RBF: tipo del segnale cambiato durante la registrazione");
                kinds[i] = kind;
            }
            var q = Legacy.Quality(row.Quality, row.Good);
            var value = row.ValueNumeric;
            var text = row.ValueText;
            if (q == 3 && kind != 9 && !(value is double v && double.IsFinite(v))) q = 1;
            double number;
            if (kind == 9)
            {
                text ??= "";
                if (q == 3)
                {
                    byte[] encoded;
                    try { encoded = Legacy.Cp1252Strict.GetBytes(text); }
                    catch (EncoderFallbackException) { throw new InvalidDataException("RBF: stringa non rappresentabile in Windows-1252; originali conservati"); }
                    if (encoded.Length > 80 || encoded.Contains((byte)0)) throw new InvalidDataException("RBF: stringa oltre 80 byte o contenente NUL; originali conservati");
                }
                number = 0;
            }
            else if (q != 3) number = 0;
            else
            {
                number = value!.Value;
                if (kind == 0 && number is not (0 or 1)) throw new InvalidDataException("RBF: valore Boolean fuori da 0/1");
            }
            sort.Add(new Staged(ticks, row.Cycle ?? 0, i, number, text ?? "", (byte)q));
        }
        var channels = new List<(string name, byte type)>();
        var mapping = new List<object?>();
        var valueChannels = new List<int>();
        for (var i = 0; i < nodes.Count; i++)
        {
            var name = Legacy.SafeName(Legacy.NodeName(nodes[i]), i);
            var kind = kinds[i] ?? 7;
            channels.Add((name, kind));
            channels.Add((name + "_Q", 2));
            valueChannels.Add(2 * i);
            mapping.Add(new Dictionary<string, object?>
            {
                ["nodeid"] = nodes[i].NodeId, ["display_name"] = Legacy.NodeName(nodes[i]), ["unit"] = Legacy.Unit(nodes[i]),
                ["value_channel"] = 2 * i, ["quality_channel"] = 2 * i + 1, ["data_type"] = Types[kind].name,
            });
        }
        var frameSize = 8 + channels.Sum(c => Types[c.type].size);
        var initial = Legacy.MetaNumber(sourceMeta, "initial_sample_ms") is double p && p != 0 ? p : 10;
        var sampleUs = (uint)Math.Max(1, Math.Round(initial * 1000, MidpointRounding.ToEven));
        var header = Header(channels, 0, sampleUs);
        var footer = SavedState(channels, valueChannels);
        if (maxBytes > 0 && header.Length + frameSize + footer.Length > maxBytes) throw new InvalidDataException("Limite RBF inferiore a intestazione e un campione completo");
        FileStream? stream = null;
        string? partial = null;
        int part = 0;
        long frames = 0;
        long? first = null, last = null;
        var stem = Path.Combine(directory, Path.GetFileNameWithoutExtension(original));

        void Finish()
        {
            if (stream == null) return;
            stream.Write(footer);
            stream.Seek(4, SeekOrigin.Begin);
            Span<byte> size = stackalloc byte[4];
            BinaryPrimitives.WriteUInt32LittleEndian(size, (uint)(frames * frameSize));
            stream.Write(size);
            stream.Flush(true);
            stream.Dispose();
            stream = null;
            var destination = partial![..^5];
            var info = new OrderedDictionary();
            foreach (System.Collections.DictionaryEntry entry in sourceMeta) info[entry.Key] = entry.Value;
            info["format"] = "rbf"; info["layout"] = "RBF v3 wide frames";
            info["signals"] = nodes.Select(n => (object?)(Action<System.Text.Json.Utf8JsonWriter>)n.Write).ToList();
            info["rbf_channels"] = mapping; info["frames"] = frames; info["rows_written"] = frames * nodes.Count;
            info["original_archive"] = Path.GetFileName(original);
            info["duration_seconds"] = first is long a && last is long b ? (b - a) / 1e9 : 0;
            info["storage"] = new Dictionary<string, object?> { ["format"] = "rbf", ["label"] = "RBF v3 · Float32 + qualità", ["lossless"] = false };
            info["rbf_compatibility"] = "reference-derived v3; external application validation pending";
            info["timestamp_semantics"] = "UInt32 seconds + UInt32 nanoseconds; original timestamps also in Parquet";
            info["quality_semantics"] = QualityMarker;
            info["reference_header_word"] = 57600;
            if (first is long f && last is long l) { info["started_at"] = Stamp(f); info["ended_at"] = Stamp(l); }
            var meta = Path.ChangeExtension(destination, ".metadata.json");
            var temporary = Path.ChangeExtension(meta, ".tmp");
            Legacy.WriteJson(temporary, info);
            File.Move(partial, destination, overwrite: true);
            File.Move(temporary, meta, overwrite: true);
            partial = null;
            files.Add(Legacy.FileEntry(destination, frames * nodes.Count, frames, part));
        }

        var frame = new byte[frameSize];
        var current = new (double value, string text, byte q)[nodes.Count];
        var present = new bool[nodes.Count];
        void Emit(long ticks)
        {
            if (stream != null && (frames >= MaxFrames || (maxBytes > 0 && stream.Position + frameSize + footer.Length > maxBytes) || (frames + 1) * frameSize > 0xffffffffL))
                Finish();
            if (stream == null)
            {
                part++; frames = 0; first = last = null;
                var destination = stem + "_RBF" + part.ToString("0000", CultureInfo.InvariantCulture) + ".rbf";
                partial = destination + ".part";
                stream = new FileStream(partial, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1 << 16);
                stream.Write(header);
            }
            Array.Clear(frame);
            BinaryPrimitives.WriteUInt32LittleEndian(frame, (uint)(ticks / 1_000_000_000));
            BinaryPrimitives.WriteUInt32LittleEndian(frame.AsSpan(4), (uint)(ticks % 1_000_000_000));
            var offset = 8;
            for (var i = 0; i < nodes.Count; i++)
            {
                var kind = channels[2 * i].type;
                var (value, text, q) = present[i] ? current[i] : (0, "", (byte)0);
                switch (kind)
                {
                    case 0: frame[offset] = (byte)value; break;
                    case 7:
                        var single = (float)value;
                        if (float.IsInfinity(single) && double.IsFinite(value)) throw new InvalidDataException("RBF: valore oltre i limiti Float32; archivio originale conservato");
                        BinaryPrimitives.WriteSingleLittleEndian(frame.AsSpan(offset), single);
                        break;
                    case 9:
                        if (q == 3) Legacy.Cp1252Strict.GetBytes(text).CopyTo(frame.AsSpan(offset));
                        break;
                }
                offset += Types[kind].size;
                BinaryPrimitives.WriteInt16LittleEndian(frame.AsSpan(offset), q);
                offset += 2;
            }
            stream!.Write(frame);
            frames++;
            first ??= ticks;
            last = ticks;
            Array.Clear(present);
        }

        try
        {
            long? key = null;
            foreach (var s in sort.Sorted())
            {
                if (key != s.Ticks)
                {
                    if (key is long previous) Emit(previous);
                    key = s.Ticks;
                }
                else if (present[s.Node]) Emit(s.Ticks);
                current[s.Node] = (s.Value, s.Text, s.Q);
                present[s.Node] = true;
            }
            if (key is long lastKey) Emit(lastKey);
            if (part == 0)
            {
                part = 1;
                partial = stem + "_RBF0001.rbf.part";
                stream = new FileStream(partial, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None, 1 << 16);
                stream.Write(header);
            }
            Finish();
            return files;
        }
        finally
        {
            stream?.Dispose();
            if (partial != null) File.Delete(partial);
        }
    }
}
