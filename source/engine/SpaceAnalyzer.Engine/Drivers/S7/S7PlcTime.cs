using System.Globalization;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.Engine.Drivers.S7;

/// <summary>
/// PLC time tag for the polled S7 modes (classic ReadVar and symbolic S7CommPlus). The PLC program writes a 64-bit integer
/// (microseconds or nanoseconds since 1970-01-01 UTC, e.g. from RD_SYS_T) once per cycle. The driver reads it with every group:
/// it becomes the sample's source timestamp, and when the group spans several requests it is read before and after the other values,
/// so a change between the two reads shows that the values do not belong to the same PLC cycle.
/// </summary>
public static class S7PlcTime
{
    public const string Origin = "PLC time tag";
    const long MinMicros = 946684800000000L, MaxMicros = 7258118400000000L;   // 2000-01-01 .. 2200-01-01, as the UDP timestamp field

    public static string Unit(string value) => value == "ns" ? "ns" : "us";

    /// <summary>Time of a row read from the time tag, or the reason why it is not usable.</summary>
    public static bool TryDecode(Sample? row, string unit, out DateTime utc, out string reason)
    {
        utc = default;
        if (row == null) { reason = "variabile non letta"; return false; }
        if (!row.Good) { reason = row.Error.Length > 0 ? row.Error : row.Status ?? "lettura non valida"; return false; }
        decimal raw;
        switch (row.SourceValue ?? row.Value)
        {
            case long l: raw = l; break;
            case ulong u: raw = u; break;
            case int i: raw = i; break;
            default: reason = "il tipo deve essere un intero a 64 bit (LINT, ULINT, LWORD)"; return false;
        }
        var micros = unit == "ns" ? Math.Floor(raw / 1000m) : raw;
        if (micros < MinMicros || micros >= MaxMicros) { reason = "valore fuori dall'intervallo 2000–2199 (unità " + (unit == "ns" ? "ns" : "µs") + ")"; return false; }
        utc = DateTime.UnixEpoch.AddTicks((long)micros * 10);
        reason = "";
        return true;
    }

    /// <summary>Stamps the rows with the PLC time. <paramref name="last"/> is the second read of the same tag (null when the group was one request).</summary>
    public static void Apply(IEnumerable<Sample> rows, Sample? first, Sample? last, string unit)
    {
        var ok = TryDecode(first, unit, out var t1, out var reason);
        string? warning = null;
        if (!ok) warning = "Timestamp PLC non utilizzabile: " + reason;
        else if (last != null)
        {
            if (!TryDecode(last, unit, out var t2, out var reason2)) { warning = "Timestamp PLC non verificabile a fine lettura: " + reason2; }
            else if (t1 != t2)
                warning = "Valori non dello stesso ciclo PLC: il timestamp è passato da " + Values.IsoMicro(t1) + " a " + Values.IsoMicro(t2) + " durante la lettura";
        }
        foreach (var row in rows)
        {
            if (ok) { row.SourceTs = Values.IsoMicro(t1); row.TimestampOrigin = Origin; }
            if (warning != null && row.Quality == "Good")
            {
                row.Quality = "Uncertain"; row.Good = false; row.Status = warning;
            }
        }
    }

    /// <summary>The ids to read (time tag first, and again last when asked) and where each requested id ends up.</summary>
    public static List<string> Plan(IReadOnlyList<string> ids, string timeTag, bool bracket) =>
        new List<string> { timeTag }.Concat(ids.Where(n => n != timeTag)).Concat(bracket ? new[] { timeTag } : []).ToList();

    /// <summary>Rows in the order of <paramref name="ids"/> from the rows read with <see cref="Plan"/>.</summary>
    public static List<Sample> Collect(IReadOnlyList<string> ids, string timeTag, IReadOnlyList<Sample> read)
    {
        var output = new List<Sample>(ids.Count);
        var next = 1;
        foreach (var id in ids) output.Add(id == timeTag ? read[0] : read[next++]);
        return output;
    }
}
