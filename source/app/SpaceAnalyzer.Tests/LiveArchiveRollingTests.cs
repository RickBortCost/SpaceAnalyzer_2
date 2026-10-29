using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Live;
using Xunit;

namespace SpaceAnalyzer.Tests;

/// <summary>Storico Live a finestra mobile: oltre il limite di spazio si scartano i campioni più vecchi invece di fermare l'archivio.</summary>
public class LiveArchiveRollingTests
{
    const double T0 = 1_700_000_000_000d;
    const int Signals = 20, Cycles = 6000;

    static (LiveArchive archive, long generation) Filled(TempDir dir)
    {
        var archive = new LiveArchive(dir.Path, maxPending: 1_000_000, maxBytes: 2L * 1024 * 1024, maxPendingBytes: 1L * 1024 * 1024 * 1024);
        var generation = archive.NewSession();
        for (var c = 0; c < Cycles; c++)
            for (var s = 0; s < Signals; s++)
                Assert.True(archive.Append(generation, "n" + s, new LivePoint(T0 + c, c * 1.5 + s, "Good")) > 0);
        return (archive, generation);
    }

    static ArchiveReference Everything(LiveArchive archive) =>
        archive.Reference(Enumerable.Range(0, Signals).Select(s => "n" + s).ToList(), double.NegativeInfinity, double.PositiveInfinity);

    [Fact]
    public void Above_the_limit_the_oldest_originals_are_dropped_and_the_archive_keeps_working()
    {
        using var dir = new TempDir();
        var (archive, generation) = Filled(dir);
        using var disposable = archive;
        archive.Before("n0", long.MaxValue, 1, out _);   // waits for the writer
        Assert.Equal("", archive.Error);
        Assert.True(double.IsFinite(archive.TrimmedUntil));
        Assert.True(archive.TrimmedUntil >= T0);
        var points = archive.Points(Everything(archive), Enumerable.Range(0, Signals).Select(s => "n" + s), double.NegativeInfinity, double.PositiveInfinity).ToList();
        Assert.InRange(points.Count, 1, Signals * Cycles - 1);
        Assert.All(points, p => Assert.True(p.p.T > archive.TrimmedUntil));
        // The newest cycles are complete for every signal.
        for (var s = 0; s < Signals; s++)
            Assert.Contains(points, p => p.nid == "n" + s && p.p.T == T0 + Cycles - 1);
        // Writing continues after trimming.
        Assert.True(archive.Append(generation, "n0", new LivePoint(T0 + Cycles, 1, "Good")) > 0);
        archive.Before("n0", long.MaxValue, 1, out _);
        Assert.Equal("", archive.Error);
    }

    [Fact]
    public void Disk_usage_stays_bounded()
    {
        using var dir = new TempDir();
        var (archive, _g) = Filled(dir);
        using var disposable = archive;
        archive.Before("n0", long.MaxValue, 1, out _);
        // The database file stays near the limit (the WAL is a fixed-size log reused by SQLite and is not counted).
        var bytes = new DirectoryInfo(dir.Path).EnumerateFiles("originals.sqlite", SearchOption.AllDirectories).Sum(f => f.Length);
        Assert.True(bytes < 6L * 1024 * 1024, $"file dell'archivio: {bytes} byte");
    }

    [Fact]
    public void A_request_that_reaches_into_dropped_data_fails_explicitly()
    {
        using var dir = new TempDir();
        var (archive, _g) = Filled(dir);
        using var disposable = archive;
        archive.Before("n0", long.MaxValue, 1, out _);
        var ids = Enumerable.Range(0, Signals).Select(s => "n" + s).ToList();
        var old = archive.Reference(ids, T0, T0 + 10);
        var error = Assert.Throws<UserError>(() => archive.Points(old, ids, T0, T0 + 10).ToList());
        Assert.Contains("finestra mobile", error.Message);
        // A recent interval is answered completely.
        var from = T0 + Cycles - 100;
        var recent = archive.Reference(ids, from, T0 + Cycles);
        Assert.Equal(Signals * 100, archive.Points(recent, ids, from, T0 + Cycles).Count(p => p.p.T > from - 1));
    }

    [Fact]
    public void History_for_a_late_view_is_flagged_as_truncated_and_a_new_session_starts_clean()
    {
        using var dir = new TempDir();
        var (archive, _g) = Filled(dir);
        using var disposable = archive;
        archive.Before("n0", long.MaxValue, 1_000_000, out var truncated);
        Assert.True(truncated);
        archive.NewSession();
        Assert.False(double.IsFinite(archive.TrimmedUntil));
    }
}
