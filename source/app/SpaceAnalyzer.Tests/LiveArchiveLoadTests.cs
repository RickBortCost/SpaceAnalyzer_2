using System.Diagnostics;
using SpaceAnalyzer.App.Live;
using Xunit;
using Xunit.Abstractions;

namespace SpaceAnalyzer.Tests;

/// <summary>Carico sintetico dello storico Live: segnali, frequenza e durata arrivano da SA_LOAD_* (misura), i controlli automatici verificano solo la correttezza.</summary>
public class LiveArchiveLoadTests(ITestOutputHelper output)
{
    static int Env(string name, int fallback) => int.TryParse(Environment.GetEnvironmentVariable(name), out var v) && v > 0 ? v : fallback;

    [Fact]
    public void LoadBelowCapacityIsFullyArchived()
    {
        using var dir = new TempDir();
        using var archive = new LiveArchive(dir.Path);
        var generation = archive.NewSession();
        const int signals = 20, cycles = 500;
        var t0 = 1_700_000_000_000d;
        for (var c = 0; c < cycles; c++)
            for (var s = 0; s < signals; s++)
                Assert.True(archive.Append(generation, "n" + s, new LivePoint(t0 + c, c + s, "Good")) > 0);
        Assert.Equal("", archive.Error);
        var reference = archive.Reference(Enumerable.Range(0, signals).Select(s => "n" + s).ToList(), double.NegativeInfinity, double.PositiveInfinity);
        var count = archive.Points(reference, reference.Ids, double.NegativeInfinity, double.PositiveInfinity).Count();
        Assert.Equal(signals * cycles, count);
    }

    [Fact]
    public void OverloadIsReportedAndNeverSilentlyIncomplete()
    {
        using var dir = new TempDir();
        // Coda minuscola: il produttore supera sempre lo scrittore, quindi l'archivio deve dichiararsi incompleto.
        using var archive = new LiveArchive(dir.Path, maxPending: 8, maxPendingBytes: 4096);
        var generation = archive.NewSession();
        long accepted = 0;
        for (var i = 0; i < 200_000 && archive.Error.Length == 0; i++)
            if (archive.Append(generation, "n" + (i % 50), new LivePoint(1_700_000_000_000d + i, i, "Good")) > 0) accepted++;
        Assert.NotEqual("", archive.Error);
        Assert.Contains("incompleto", archive.Error);
        Assert.Equal(0, archive.Append(generation, "n0", new LivePoint(1_800_000_000_000d, 1, "Good")));
        Assert.True(accepted < 200_000);
    }

    [Fact]
    public void ThroughputMeasurement()
    {
        // Misura informativa (nessun limite di tempo): SA_LOAD_SIGNALS, SA_LOAD_HZ, SA_LOAD_SECONDS.
        var signals = Env("SA_LOAD_SIGNALS", 50);
        var hz = Env("SA_LOAD_HZ", 1000);
        var seconds = Env("SA_LOAD_SECONDS", 1);
        using var dir = new TempDir();
        using var archive = new LiveArchive(dir.Path);
        var generation = archive.NewSession();
        var cycles = hz * seconds;
        var clock = Stopwatch.StartNew();
        long refused = 0;
        var t0 = 1_700_000_000_000d;
        for (var c = 0; c < cycles; c++)
        {
            // Il produttore segue il tempo reale: un ciclo ogni 1000/hz ms.
            var due = c * 1000.0 / hz;
            while (clock.Elapsed.TotalMilliseconds < due) Thread.SpinWait(50);
            for (var s = 0; s < signals; s++)
                if (archive.Append(generation, "n" + s, new LivePoint(t0 + c, c, "Good")) == 0) refused++;
        }
        var produced = clock.Elapsed;
        // Before() attende lo svuotamento della coda di scrittura prima di leggere.
        try { archive.Before("n0", long.MaxValue, 1, out _); } catch (SpaceAnalyzer.App.Infrastructure.UserError) { /* archivio dichiaratosi incompleto: è proprio ciò che si misura */ }
        var drained = clock.Elapsed;
        output.WriteLine($"segnali={signals} frequenza={hz} Hz durata={seconds}s righe={(long)signals * cycles}  prodotte in {produced.TotalSeconds:F2}s, svuotate in {drained.TotalSeconds:F2}s  rifiutate={refused}  errore='{archive.Error}'");
        Assert.True(drained >= produced);
    }

    [Fact]
    public void WriterDrainRate()
    {
        // Misura informativa: velocità massima dello scrittore SQLite con coda illimitata (SA_LOAD_ROWS righe).
        var rows = Env("SA_LOAD_ROWS", 100_000);
        using var dir = new TempDir();
        using var archive = new LiveArchive(dir.Path, maxPending: rows + 10, maxPendingBytes: 8L * 1024 * 1024 * 1024);
        var generation = archive.NewSession();
        var t0 = 1_700_000_000_000d;
        for (var i = 0; i < rows; i++) archive.Append(generation, "n" + (i % 100), new LivePoint(t0 + i / 100, i, "Good"));
        var clock = Stopwatch.StartNew();
        archive.Before("n0", long.MaxValue, 1, out _);
        output.WriteLine($"righe={rows} svuotate in {clock.Elapsed.TotalSeconds:F2}s = {rows / clock.Elapsed.TotalSeconds:F0} righe/s  errore='{archive.Error}'");
        Assert.Equal("", archive.Error);
    }
}
