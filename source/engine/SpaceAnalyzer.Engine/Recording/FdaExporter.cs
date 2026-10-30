using System.Buffers.Binary;
using System.Collections.Specialized;
using System.Globalization;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Recording;

/// <summary>
/// FDA DAS4 conversion of a finalized long-layout original (port of fda_das.export_parquet).
/// Samples are sorted on disk; disjoint channel updates at the same rounded millisecond
/// share a frame; a repeated channel opens a new frame; absent inputs are 0 with Q=0.
/// </summary>
public static class FdaExporter
{
    const int MaxAbsInt16 = 32767;
    const long MaxTicks = 0xffffffffL;

    /// <summary>
    /// The FDA viewer refuses to open a file whose data need more than 256 MB of RAM (frames × channels × 8 bytes, time channel included)
    /// and forces a manual "data subset" selection. A file is therefore closed before it would exceed that (255 MB used, 1 MB of margin).
    /// Public and writable only so tests can use a small value.
    /// </summary>
    public static long ViewerRamLimitBytes = 255L << 20;

    public static long MaxFramesPerFile(int channelCount) => Math.Max(1, ViewerRamLimitBytes / (8L * Math.Max(1, channelCount)));

    readonly record struct Staged(double T, long Cycle, int Node, double Value, byte Q);

    public static double ChooseGain(double peak)
    {
        if (peak == 0 || !double.IsFinite(peak)) return 1.0;
        var exponent = (int)Math.Floor(Math.Log10(peak / MaxAbsInt16));
        for (var e = exponent - 1; e < exponent + 3; e++)
            foreach (var m in new[] { "1", "2", "5" })
            {
                var gain = double.Parse(m + "e" + e.ToString(CultureInfo.InvariantCulture), CultureInfo.InvariantCulture);
                if (gain > 0 && peak / gain <= MaxAbsInt16) return gain;
            }
        throw new InvalidDataException("Scala FDA fuori dai limiti numerici");
    }

    static byte[] Text(string? value)
    {
        var raw = Legacy.Cp1252Replace.GetBytes(value ?? "");
        if (raw.Length > 254) raw = raw[..254];
        return [(byte)raw.Length, .. raw];
    }

    sealed record Channel(string Name, string Description, string Unit, int Size, double Gain, double Offset = 0.0);

    static byte[] Header(IReadOnlyList<Channel> channels, long epoch, double sampleMs)
    {
        if (epoch is < 0 or > 0xffffffffL || channels.Count is < 1 or > 4096) throw new InvalidDataException("Limiti intestazione FDA superati");
        var recordSize = 4 + channels.Skip(1).Sum(c => c.Size);
        using var data = new MemoryStream();
        var w = new BinaryWriter(data);
        w.Write(new byte[22]);
        w.Write("DAS4\0"u8);
        w.Write((uint)epoch);
        w.Write(0.0);
        w.Write(1000 / Math.Max(1, sampleMs));
        w.Write((uint)recordSize);
        w.Write(20.0);
        w.Write((ushort)0);
        w.Write((uint)120);
        w.Write((ushort)channels.Count);
        var offset = 0u;
        for (var i = 0; i < channels.Count; i++)
        {
            var c = channels[i];
            if (i == 0) { w.Write((ushort)65535); w.Write((ushort)2); w.Write((ushort)8); w.Write("CDasChan"u8); }
            else w.Write([0x01, 0x80]);
            w.Write(Text(c.Name)); w.Write(Text(c.Description)); w.Write(Text(c.Unit));
            w.Write((ushort)(i == 0 ? 65535 : i - 1));
            w.Write((ushort)1);
            w.Write((byte)(i == 0 ? 255 : 0));
            w.Write((byte)(i == 0 ? 0 : 2));
            w.Write((ushort)c.Size);
            w.Write((ushort)1);
            w.Write(c.Gain);
            w.Write(c.Offset);
            w.Write(offset);
            offset += (uint)c.Size;
        }
        w.Write((ushort)0);
        w.Write((uint)(data.Length + 4));
        w.Flush();
        return data.ToArray();
    }

    public static List<Dictionary<string, object?>> Export(string original, IReadOnlyList<NodeSpec> nodes, long maxBytes, Action<string, long>? progress = null)
    {
        long stagedRows = 0, writtenRows = 0;
        var ids = new Dictionary<string, int>();
        foreach (var node in nodes) ids.TryAdd(node.NodeId, ids.Count);
        if (ids.Count is < 1 or > 64) throw new InvalidDataException("FDA: selezionare da 1 a 64 segnali");
        var directory = Path.GetDirectoryName(Path.GetFullPath(original))!;
        var sourceMeta = Legacy.SourceMetadata(original);
        if (!Legacy.EnoughSpace(directory)) throw new IOException("Spazio disco insufficiente per la conversione FDA: archivio originale conservato");
        var limits = new double[nodes.Count];
        // Integer signals: exact 16-bit storage is possible whenever the recorded span fits 65,536 steps (gain 1 plus an offset), e.g. a UINT above 32767.
        var integerType = new bool[nodes.Count];
        var integral = Enumerable.Repeat(true, nodes.Count).ToArray();
        var minValue = Enumerable.Repeat(double.PositiveInfinity, nodes.Count).ToArray();
        var maxValue = Enumerable.Repeat(double.NegativeInfinity, nodes.Count).ToArray();
        var lastSeen = new Dictionary<int, double>();
        var discontinuities = 0L;
        var examples = new List<object?>();
        var files = new List<Dictionary<string, object?>>();
        using var sort = new ExternalSort<Staged>(directory,
            (a, b) => a.T != b.T ? a.T.CompareTo(b.T) : a.Cycle.CompareTo(b.Cycle),
            (w, s) => { w.Write(s.T); w.Write(s.Cycle); w.Write(s.Node); w.Write(s.Value); w.Write(s.Q); },
            r => new Staged(r.ReadDouble(), r.ReadInt64(), r.ReadInt32(), r.ReadDouble(), r.ReadByte()));
        long staged = 0;
        double? first = null;
        foreach (var row in ParquetSource.Read(original))
        {
            if (row.NodeId == null || !ids.TryGetValue(row.NodeId, out var n)) continue;
            var t = row.TimestampMs ?? double.NaN;
            if (!double.IsFinite(t)) throw new InvalidDataException("Timestamp FDA non finito");
            // A backwards source clock (PLC/NTP step) is reported, never silently hidden.
            if (lastSeen.TryGetValue(n, out var previous) && t < previous - 1.0)
            {
                discontinuities++;
                if (examples.Count < 16) examples.Add(new Dictionary<string, object?> { ["signal"] = row.NodeId, ["previous_ms"] = previous, ["next_ms"] = t });
            }
            lastSeen[n] = t;
            var q = Legacy.Quality(row.Quality, row.Good);
            double v;
            // Uncertain keeps its numeric value (Q=2); only Bad/missing/non-numeric become 0.
            if (!string.IsNullOrEmpty(row.DataType)) integerType[n] = DataKinds.IsInteger(row.DataType);
            if (q is 2 or 3 && row.ValueNumeric is double number && double.IsFinite(number))
            {
                v = number; limits[n] = Math.Max(limits[n], Math.Abs(v));
                minValue[n] = Math.Min(minValue[n], v); maxValue[n] = Math.Max(maxValue[n], v);
                if (v != Math.Truncate(v)) integral[n] = false;
            }
            else { v = 0; q = q == 2 ? 2 : 1; }
            staged++;
            if (staged % 500000 == 0 && !Legacy.EnoughSpace(directory)) throw new IOException("Spazio disco insufficiente durante la conversione FDA: archivio originale conservato");
            first = first is double f ? Math.Min(f, t) : t;
            sort.Add(new Staged(t, row.Cycle ?? 0, n, v, (byte)q));
            if (progress != null && ++stagedRows % 65536 == 0) progress("stage", stagedRows);
        }
        long epoch = first is double minT ? (long)Math.Floor(minT / 1000)
            : (long)DateTimeOffset.Parse(((System.Text.Json.JsonElement)sourceMeta["started_at"]!).GetString()!, CultureInfo.InvariantCulture).ToUnixTimeSeconds();
        var channels = new List<Channel> { new("INTERNAL TIME", "Time from start...", "Milliseconds", 4, 1.0) };
        var mapping = new List<object?>();
        var gains = new double[nodes.Count];
        var offsets = new double[nodes.Count];
        for (var i = 0; i < nodes.Count; i++)
        {
            var gain = ChooseGain(limits[i]);
            if (gain <= 0 || !double.IsFinite(gain)) throw new InvalidDataException("Scala FDA fuori dai limiti numerici");
            double offset = 0;
            if (gain > 1 && integerType[i] && integral[i] && maxValue[i] - minValue[i] <= 65535) { gain = 1; offset = minValue[i] + 32768; }
            gains[i] = gain; offsets[i] = offset;
            var name = Legacy.SafeName(Legacy.NodeName(nodes[i]), i);
            var description = nodes[i].NodeId.Length > 250 ? nodes[i].NodeId[..250] : nodes[i].NodeId;
            channels.Add(new(name, description, Legacy.Unit(nodes[i]), 2, gain, offset));
            var valueChannel = channels.Count - 1;
            channels.Add(new(name + "_Q", "Quality: 0=missing, 1=Bad, 2=Uncertain, 3=Good", "", 2, 1.0));
            mapping.Add(new Dictionary<string, object?>
            {
                ["nodeid"] = nodes[i].NodeId, ["display_name"] = Legacy.NodeName(nodes[i]), ["unit"] = Legacy.Unit(nodes[i]),
                ["value_channel"] = valueChannel, ["quality_channel"] = channels.Count - 1, ["gain"] = gain, ["offset"] = offset, ["maximum_rounding_error"] = offset != 0 ? 0.0 : gain / 2,
            });
        }
        var period = Legacy.MetaNumber(sourceMeta, "initial_sample_ms") is double p && p != 0 ? p : 10;
        var header = Header(channels, epoch, period);
        var frameSize = 4 + 2 * (channels.Count - 1);
        var maxFrames = MaxFramesPerFile(channels.Count);
        if (maxBytes > 0 && header.Length + frameSize > maxBytes) throw new InvalidDataException("Limite FDA inferiore a intestazione e un campione completo");
        FileStream? stream = null;
        string? partial = null;
        int part = 0;
        long frames = 0;
        long? firstT = null, lastT = null;
        var stem = Path.Combine(directory, Path.GetFileNameWithoutExtension(original));

        void Finish()
        {
            if (stream == null) return;
            stream.Flush(true);
            stream.Dispose();
            stream = null;
            var destination = partial![..^5];
            var info = new OrderedDictionary();
            foreach (System.Collections.DictionaryEntry entry in sourceMeta) info[entry.Key] = entry.Value;
            info["format"] = "das"; info["fda_chronological"] = true; info["layout"] = "FDA DAS4 wide frames";
            info["signals"] = nodes.Select(n => (object?)(Action<System.Text.Json.Utf8JsonWriter>)n.Write).ToList();
            info["fda_channels"] = mapping; info["original_archive"] = Path.GetFileName(original);
            info["rows_written"] = frames * nodes.Count; info["frames"] = frames;
            info["started_at"] = Values.PythonIso(DateTime.UnixEpoch.AddMilliseconds(firstT ?? epoch * 1000));
            info["ended_at"] = Values.PythonIso(DateTime.UnixEpoch.AddMilliseconds(lastT ?? epoch * 1000));
            info["duration_seconds"] = firstT is long a && lastT is long b ? (b - a) / 1000.0 : 0;
            info["storage"] = new Dictionary<string, object?> { ["format"] = "das", ["label"] = "FDA DAS4 · Int16 scalato", ["lossless"] = false };
            info["fda_compatibility"] = "reference-derived; external FDA validation pending";
            info["timestamp_semantics"] = "UInt32 relative milliseconds, nearest ms; original precision in companion Parquet";
            info["quality_semantics"] = "Per-signal _Q: 0 missing, 1 Bad, 2 Uncertain, 3 Good; absent values are zero with Q=0; Uncertain keeps its value";
            info["fda_gain_policy"] = "1-2-5 scale steps, peak<=32767";
            info["time_discontinuities"] = new Dictionary<string, object?> { ["count"] = discontinuities, ["tolerance_ms"] = 1.0, ["examples"] = examples };
            var meta = Path.ChangeExtension(destination, ".metadata.json");
            var temporary = Path.ChangeExtension(meta, ".tmp");
            Legacy.WriteJson(temporary, info);
            // Metadata first: a visible .das always has its sidecar, even after a crash.
            File.Move(temporary, meta, overwrite: true);
            File.Move(partial, destination, overwrite: true);
            partial = null;
            files.Add(Legacy.FileEntry(destination, frames * nodes.Count, frames, part));
        }

        void Open()
        {
            part++; frames = 0; firstT = lastT = null;
            var destination = stem + "_FDA" + part.ToString("0000", CultureInfo.InvariantCulture) + ".das";
            partial = destination + ".part";
            stream = new FileStream(partial, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16);
            stream.Write(header);
        }

        var frame = new byte[frameSize];
        var values = new short[channels.Count - 1];
        void Emit(long t)
        {
            var ticks = t - epoch * 1000;
            if (ticks is < 0 or > MaxTicks)
            {
                // UInt32 ms lasts 49.7 days: continue in a new part with its own epoch.
                Finish();
                epoch = (long)Math.Floor(t / 1000.0);
                header = Header(channels, epoch, period);
                ticks = t - epoch * 1000;
                if (ticks is < 0 or > MaxTicks) throw new InvalidDataException("Durata FDA oltre il limite UInt32 millisecondi");
            }
            if (stream != null && ((maxBytes > 0 && stream.Position + frameSize > maxBytes) || frames >= maxFrames)) Finish();
            if (stream == null) Open();
            BinaryPrimitives.WriteUInt32LittleEndian(frame, (uint)ticks);
            for (var i = 0; i < values.Length; i++) BinaryPrimitives.WriteInt16LittleEndian(frame.AsSpan(4 + 2 * i), values[i]);
            stream!.Write(frame);
            frames++;
            firstT ??= t;
            lastT = t;
        }

        try
        {
            long? current = null;
            var seen = new HashSet<int>();
            foreach (var s in sort.Sorted())
            {
                if (progress != null && ++writtenRows % 65536 == 0) progress("write", writtenRows);
                var key = (long)Math.Round(s.T, MidpointRounding.ToEven);
                if (current != key)
                {
                    if (current is long previousKey) Emit(previousKey);
                    current = key; Array.Clear(values); seen.Clear();
                }
                else if (seen.Contains(s.Node))
                {
                    Emit(key); Array.Clear(values); seen.Clear();
                }
                // Missing/Bad samples carry no value: raw 0 regardless of the offset.
                var encoded = s.Q is 2 or 3 ? Math.Round((s.Value - offsets[s.Node]) / gains[s.Node], MidpointRounding.ToEven) : 0;
                if (encoded is < -32768 or > 32767 || !double.IsFinite(encoded * gains[s.Node])) throw new InvalidDataException("Overflow numerico FDA: archivio originale conservato");
                values[2 * s.Node] = (short)encoded;
                values[2 * s.Node + 1] = s.Q;
                seen.Add(s.Node);
            }
            if (current is long last) Emit(last);
            if (part == 0) Open();
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
