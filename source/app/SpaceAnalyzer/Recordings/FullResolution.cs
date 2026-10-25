using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Recordings;

/// <summary>Every sample of one signal, in time order: timestamp, value (NaN when not Good or not numeric) and quality code.</summary>
public sealed class SampleColumns(string nodeId)
{
    public const byte Good = 0, Uncertain = 1, Bad = 2;

    public string NodeId { get; } = nodeId;
    public string Name { get; set; } = nodeId;
    public string DataType { get; set; } = "Double";
    double[] t = new double[1024];
    double[] v = new double[1024];
    byte[] q = new byte[1024];
    bool ordered = true;
    public int Count { get; private set; }

    public ReadOnlySpan<double> T => t.AsSpan(0, Count);
    public ReadOnlySpan<double> V => v.AsSpan(0, Count);
    public ReadOnlySpan<byte> Q => q.AsSpan(0, Count);
    internal ReadOnlyMemory<double> TMemory => t.AsMemory(0, Count);
    internal ReadOnlyMemory<double> VMemory => v.AsMemory(0, Count);
    internal ReadOnlyMemory<byte> QMemory => q.AsMemory(0, Count);

    public static byte Code(string quality) => quality == "Good" ? Good : quality.StartsWith("Uncertain", StringComparison.Ordinal) ? Uncertain : Bad;

    public void Add(double time, double? value, byte quality)
    {
        if (Count == t.Length)
        {
            var size = checked(t.Length * 2);
            Array.Resize(ref t, size); Array.Resize(ref v, size); Array.Resize(ref q, size);
        }
        if (Count > 0 && time < t[Count - 1]) ordered = false;
        t[Count] = time;
        v[Count] = quality == Good && value is double d && double.IsFinite(d) ? d : double.NaN;
        q[Count] = quality;
        Count++;
    }

    /// <summary>Stable sort by timestamp (only when the source was not already ordered).</summary>
    public void Sort()
    {
        if (ordered) return;
        var index = new int[Count];
        for (var i = 0; i < Count; i++) index[i] = i;
        var keys = t;
        Array.Sort(index, (a, b) => keys[a] != keys[b] ? keys[a].CompareTo(keys[b]) : a.CompareTo(b));
        var nt = new double[Math.Max(1, Count)]; var nv = new double[nt.Length]; var nq = new byte[nt.Length];
        for (var i = 0; i < Count; i++) { nt[i] = t[index[i]]; nv[i] = v[index[i]]; nq[i] = q[index[i]]; }
        t = nt; v = nv; q = nq; ordered = true;
    }

    /// <summary>Comparison files: a Bad, valueless sample in the middle of each acquisition gap, so the line is not joined across it.</summary>
    public void MarkGaps(double period)
    {
        var gaps = 0;
        for (var i = 0; i + 1 < Count; i++) if (t[i + 1] - t[i] > period * 1.5) gaps++;
        if (gaps == 0) return;
        var size = Count + gaps;
        var nt = new double[size]; var nv = new double[size]; var nq = new byte[size];
        var j = 0;
        for (var i = 0; i < Count; i++)
        {
            if (i > 0 && t[i] - t[i - 1] > period * 1.5) { nt[j] = (t[i - 1] + t[i]) / 2; nv[j] = double.NaN; nq[j] = Bad; j++; }
            nt[j] = t[i]; nv[j] = v[i]; nq[j] = q[i]; j++;
        }
        t = nt; v = nv; q = nq; Count = size;
    }
}

public sealed record FullView(Dictionary<string, object?> Info, List<SampleColumns> Series);

public sealed partial class RecordingLibrary
{
    /// <summary>Upper bound of samples kept in memory for one full-resolution view (about 17 bytes each on both sides).</summary>
    public const long MaxFullSamples = 80_000_000;

    /// <summary>All samples of the selected signals and interval, without any reduction.</summary>
    public FullView LoadFull(string name, JsonNode? signalIds = null, JsonNode? startMs = null, JsonNode? endMs = null)
    {
        var path = PathOf(name);
        List<string>? ids = null;
        if (signalIds != null && !signalIds.IsNull())
        {
            if (signalIds is not JsonArray list || list.Count > MaxSignals) throw new UserError("Selezionare al massimo 64 segnali");
            ids = list.Select(n => RowValues.Str(Py.Plain(n))).Where(s => s.Length > 0).Distinct().ToList();
        }
        var start = startMs.IsNull() ? null : FiniteParam(startMs);
        var end = endMs.IsNull() ? null : FiniteParam(endMs);
        if (start != null && end != null && end < start) throw new UserError("L'intervallo temporale deve avere una fine non precedente all'inizio");
        var meta = Metadata(path);
        var comparison = meta["comparison"].Truthy();
        var series = new Dictionary<string, SampleColumns>();
        long rows = 0, numeric = 0;
        double low = double.PositiveInfinity, high = double.NegativeInfinity;
        var periods = new List<double>();
        SampleColumns Column(string nid)
        {
            if (!series.TryGetValue(nid, out var s)) series[nid] = s = new SampleColumns(nid);
            return s;
        }
        void Count(double t, double? value, string quality)
        {
            if (++rows > MaxFullSamples)
                throw new UserError("La selezione supera 80 milioni di campioni: riduci l'intervallo temporale o il numero di segnali per aprirla a piena risoluzione");
            numeric += value != null && quality == "Good" ? 1 : 0;
            low = Math.Min(low, t); high = Math.Max(high, t);
        }
        if (comparison)
        {
            var selected = ids == null ? null : new HashSet<string>(ids);
            foreach (var row in IterRows(path, selected, start, end))
            {
                var nid = RowValues.StrOr(row.Value("node_id"));
                if (nid.Length == 0 || selected != null && !selected.Contains(nid)) continue;
                var t = RowValues.Finite(row.Value("timestamp_ms"));
                if (t == null || start != null && t < start || end != null && t > end) continue;
                var value = RowValues.Finite(row.Value("value_numeric"));
                var quality = RowValues.Quality(row);
                Count(t.Value, value, quality);
                var s = Column(nid);
                s.Add(t.Value, value, SampleColumns.Code(quality));
                s.Name = RowValues.StrOr(row.Value("signal_name"), nid);
                s.DataType = RowValues.StrOr(row.Value("data_type"), "Double");
                if (RowValues.Finite(row.Value("sample_ms")) is double sm && periods.Count < 1000) periods.Add(sm);
            }
        }
        else
        {
            foreach (var r in PlotRows(path, ids, start, end))
            {
                Count(r.T, r.Value, r.Quality);
                var s = Column(r.NodeId);
                s.Add(r.T, r.Value, SampleColumns.Code(r.Quality));
                s.Name = r.Name; s.DataType = r.DataType;
                if (periods.Count < 1000 && RowValues.Float(r.Period) is double ms && double.IsFinite(ms)) periods.Add(ms);
            }
        }
        var info = new Dictionary<string, JsonObject>();
        foreach (var s in meta["signals"] as JsonArray ?? new JsonArray())
            if (s is JsonObject o && o["nodeid"].Truthy()) info[RowValues.Str(Py.Plain(o["nodeid"]))] = o;
        if (ids != null)
            foreach (var nid in ids)
                if (!series.ContainsKey(nid))
                {
                    var empty = Column(nid);
                    if (info.TryGetValue(nid, out var o) && o["display_name"].Truthy()) empty.Name = RowValues.Str(Py.Plain(o["display_name"]));
                }
        foreach (var s in series.Values)
        {
            s.Sort();
            if (comparison && info.TryGetValue(s.NodeId, out var o) && FiniteParam(o["sample_ms"]) is > 0 and var period) s.MarkGaps(period);
        }
        var ordered = ids == null ? series.Values.ToList() : ids.Select(n => series[n]).ToList();
        var result = new Dictionary<string, object?>
        {
            ["name"] = Relative(path),
            ["format"] = Supported[Ext(path)],
            ["signals"] = ordered.Select(s => (object?)new Dictionary<string, object?>
            {
                ["nodeid"] = s.NodeId, ["display_name"] = s.Name, ["data_type"] = s.DataType,
                ["points_original"] = (long)s.Count, ["points_returned"] = (long)s.Count,
            }).ToList(),
            ["rows_scanned"] = rows, ["numeric_rows"] = numeric,
            ["start_ms"] = double.IsInfinity(low) ? null : low, ["end_ms"] = double.IsInfinity(high) ? null : high,
            ["sample_ms"] = periods.Count > 0 ? periods.Sum() / periods.Count : null,
            ["full_resolution"] = true,
            ["decimated"] = false,
        };
        return new FullView(result, ordered);
    }
}

/// <summary>
/// Binary full-resolution response: "SAS1", uint32 header length, UTF-8 JSON header, zero padding to 8 bytes,
/// then for each header signal (in order) Float64 timestamps, Float64 values, uint8 qualities, each block padded to 8 bytes.
/// All numbers little-endian.
/// </summary>
public sealed record SamplePayload(Dictionary<string, object?> Header, List<SampleColumns> Series)
{
    public const string ContentType = "application/x-space-analyzer-samples";

    public async Task WriteAsync(Stream output, CancellationToken cancel)
    {
        using var json = new MemoryStream();
        using (var w = new System.Text.Json.Utf8JsonWriter(json)) Py.WritePlain(w, Header);
        var head = new byte[8];
        Encoding.ASCII.GetBytes("SAS1").CopyTo(head, 0);
        BinaryPrimitives.WriteUInt32LittleEndian(head.AsSpan(4), (uint)json.Length);
        await output.WriteAsync(head, cancel);
        await output.WriteAsync(json.GetBuffer().AsMemory(0, (int)json.Length), cancel);
        await Pad(output, json.Length, cancel);
        if (!BitConverter.IsLittleEndian) throw new PlatformNotSupportedException("Formato binario disponibile solo su piattaforme little-endian");
        var buffer = new byte[1 << 20];
        foreach (var s in Series)
        {
            await WriteBlock(output, s.TMemory, buffer, cancel);
            await WriteBlock(output, s.VMemory, buffer, cancel);
            await WriteBlock(output, s.QMemory, buffer, cancel);
        }
    }

    static async Task WriteBlock<T>(Stream output, ReadOnlyMemory<T> data, byte[] buffer, CancellationToken cancel) where T : struct
    {
        var size = Marshal.SizeOf<T>();
        var perChunk = buffer.Length / size;
        for (var offset = 0; offset < data.Length; offset += perChunk)
        {
            var count = Math.Min(perChunk, data.Length - offset);
            var length = Copy(data.Slice(offset, count), buffer);
            await output.WriteAsync(buffer.AsMemory(0, length), cancel);
        }
        await Pad(output, (long)data.Length * size, cancel);
    }

    static int Copy<T>(ReadOnlyMemory<T> data, byte[] buffer) where T : struct
    {
        var bytes = MemoryMarshal.AsBytes(data.Span);
        bytes.CopyTo(buffer);
        return bytes.Length;
    }

    static Task Pad(Stream output, long length, CancellationToken cancel)
    {
        var pad = (int)((8 - length % 8) % 8);
        return pad == 0 ? Task.CompletedTask : output.WriteAsync(new byte[pad], cancel).AsTask();
    }
}
