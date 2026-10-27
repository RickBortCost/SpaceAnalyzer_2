using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using Xunit;

namespace SpaceAnalyzer.Tests;

public class ConversionTests
{
    const double Base = 1700000000000;

    /// <summary>CSV recording with two signals sampled together every 10 ms, plus its sidecar.</summary>
    static RecordingLibrary Fixture(TempDir dir, int samples = 100)
    {
        var sb = new StringBuilder("timestamp_utc,timestamp_ms,cycle,node_id,signal_name,value_numeric,value_text,data_type,status,good,sample_ms,quality\r\n");
        for (var i = 0; i < samples; i++)
        {
            var t = Base + i * 10;
            var stamp = DateTime.UnixEpoch.AddMilliseconds(t).ToString("yyyy-MM-dd'T'HH:mm:ss.fff'+00:00'", CultureInfo.InvariantCulture);
            foreach (var (id, name, value) in new[] { ("ns=2;s=A", "Carico", i * 1.5), ("ns=2;s=B", "Velocita", 100 - i * 0.25) })
            {
                var text = value.ToString(CultureInfo.InvariantCulture);
                sb.Append($"{stamp},{t.ToString(CultureInfo.InvariantCulture)},{i},{id},{name},{text},{text},Double,Good,True,10,Good\r\n");
            }
        }
        File.WriteAllText(Path.Combine(dir.Path, "prova.csv"), sb.ToString());
        Py.AtomicPlain(Path.Combine(dir.Path, "prova.metadata.json"), new Dictionary<string, object?>
        {
            ["signals"] = new List<object?>
            {
                new Dictionary<string, object?> { ["nodeid"] = "ns=2;s=A", ["display_name"] = "Carico", ["unit"] = "kN" },
                new Dictionary<string, object?> { ["nodeid"] = "ns=2;s=B", ["display_name"] = "Velocita", ["unit"] = "m/s" },
            },
            ["initial_sample_ms"] = 10,
        });
        return new RecordingLibrary(dir.Path);
    }

    static Dictionary<string, object?> Convert(RecordingLibrary library, string name, string format, JsonObject? extra = null)
    {
        var conversions = new Conversions(() => Path.Combine(library.Directory, Conversions.Folder));
        var request = new JsonObject { ["name"] = name, ["format"] = format };
        foreach (var (k, v) in extra ?? new JsonObject()) request[k] = v?.DeepClone();
        var job = conversions.Start(request, _ => library);
        var deadline = DateTime.UtcNow.AddSeconds(60);
        while ((string?)job["state"] == "running" && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(20);
            job = conversions.Status((string)job["id"]!);
        }
        Assert.True((string?)job["state"] == "ready", "conversione non riuscita: " + job.GetValueOrDefault("error"));
        return job;
    }

    static List<Dictionary<string, object?>> Files(Dictionary<string, object?> job) => ((List<object?>)job["files"]!).Cast<Dictionary<string, object?>>().ToList();

    static List<(double t, string id, double? v)> Samples(RecordingLibrary library, string name) =>
        library.IterRows(library.PathOf(name))
            .Where(r => !(r.Value("status") is string s && s.EndsWith("_Missing", StringComparison.Ordinal)))
            .Select(r => (RowValues.Finite(r.Value("timestamp_ms"))!.Value, RowValues.StrOr(r.Value("node_id")), RowValues.Finite(r.Value("value_numeric"))))
            .OrderBy(x => x.Item1).ThenBy(x => x.Item2).ToList();

    [Fact]
    public void FdaFilesStayWithinTheViewerRamLimit()
    {
        // 2 segnali = 4 canali dati + tempo = 5 canali; con 5 × 8 byte per campione, 1.200 byte di RAM ammessi = 30 campioni per file.
        Assert.Equal(711_135, Engine.Recording.FdaExporter.MaxFramesPerFile(47));
        var original = Engine.Recording.FdaExporter.ViewerRamLimitBytes;
        try
        {
            Engine.Recording.FdaExporter.ViewerRamLimitBytes = 5 * 8 * 30;
            using var dir = new TempDir();
            var library = Fixture(dir, samples: 100);
            var files = Files(Convert(library, "prova.csv", "das"));
            Assert.Equal(4, files.Count);
            Assert.Equal([60L, 60L, 60L, 20L], files.Select(f => System.Convert.ToInt64(f["rows"], CultureInfo.InvariantCulture)));  // righe = campioni × 2 segnali
            var all = files.SelectMany(f => Samples(library, (string)f["name"]!)).OrderBy(x => x.Item1).ThenBy(x => x.Item2).ToList();
            Assert.Equal(200, all.Count);
        }
        finally { Engine.Recording.FdaExporter.ViewerRamLimitBytes = original; }
    }

    [Fact]
    public void CsvToParquetKeepsEverySampleAndSignalNames()
    {
        using var dir = new TempDir();
        var library = Fixture(dir);
        var job = Convert(library, "prova.csv", "parquet");
        var file = Assert.Single(Files(job));
        Assert.StartsWith("Conversioni/prova_", (string)file["name"]!);
        Assert.EndsWith(".parquet", (string)file["name"]!);
        Assert.Equal(200L, job["rows_read"]);
        Assert.Equal(Samples(library, "prova.csv"), Samples(library, (string)file["name"]!));
        var described = library.Describe((string)file["name"]!);
        Assert.Equal("parquet", described["format"]);
        var signals = ((List<object?>)described["signals"]!).Cast<Dictionary<string, object?>>().ToList();
        Assert.Equal(["Carico", "Velocita"], signals.Select(s => (string)s["display_name"]!));
        Assert.Equal("kN", signals[0]["unit"]);
        Assert.Contains(library.List(), f => (string?)f["name"] == (string)file["name"]!);
    }

    [Fact]
    public void ParquetToFdaAndRbfAreReadableWithoutIntermediateFiles()
    {
        using var dir = new TempDir();
        var library = Fixture(dir);
        var parquet = (string)Files(Convert(library, "prova.csv", "parquet"))[0]["name"]!;
        foreach (var (format, ext) in new[] { ("das", ".das"), ("rbf", ".rbf") })
        {
            var file = Assert.Single(Files(Convert(library, parquet, format)));
            Assert.EndsWith(ext, (string)file["name"]!);
            var samples = Samples(library, (string)file["name"]!);
            Assert.Equal(200, samples.Count);
            var original = Samples(library, "prova.csv");
            for (var i = 0; i < samples.Count; i++)
            {
                Assert.Equal(original[i].t, samples[i].t, 3);
                Assert.Equal(original[i].id, samples[i].id);
                Assert.Equal(original[i].v!.Value, samples[i].v!.Value, 1);
            }
            var meta = library.Metadata(library.PathOf((string)file["name"]!));
            Assert.Null(meta["original_archive"]);
            Assert.Equal(parquet, meta["converted_from"]!.GetValue<string>());
        }
        var folder = Path.Combine(dir.Path, Conversions.Folder);
        Assert.Empty(Directory.GetDirectories(folder));
        Assert.Empty(Directory.GetFiles(folder, "*.part"));
    }

    [Fact]
    public void FdaToCsvWithSignalAndIntervalSelection()
    {
        using var dir = new TempDir();
        var library = Fixture(dir);
        var das = (string)Files(Convert(library, "prova.csv", "das"))[0]["name"]!;
        var job = Convert(library, das, "csv", new JsonObject
        {
            ["signal_ids"] = new JsonArray("ns=2;s=B"), ["start_ms"] = Base + 100, ["end_ms"] = Base + 290,
        });
        var samples = Samples(library, (string)Files(job)[0]["name"]!);
        Assert.Equal(20, samples.Count);
        Assert.All(samples, s => Assert.Equal("ns=2;s=B", s.id));
        Assert.Equal(Base + 100, samples[0].t, 3);
        Assert.Equal(Base + 290, samples[^1].t, 3);
    }

    [Fact]
    public void RejectsInvalidRequests()
    {
        using var dir = new TempDir();
        var library = Fixture(dir);
        var conversions = new Conversions(() => Path.Combine(dir.Path, Conversions.Folder));
        Assert.Throws<UserError>(() => conversions.Start(new JsonObject { ["name"] = "prova.csv", ["format"] = "xlsx" }, _ => library));
        Assert.Throws<UserError>(() => conversions.Start(new JsonObject { ["name"] = "prova.csv", ["format"] = "csv", ["signal_ids"] = new JsonArray("x") }, _ => library));
        Assert.Throws<UserError>(() => conversions.Start(new JsonObject { ["name"] = "../prova.csv", ["format"] = "csv" }, _ => library));
        var empty = conversions.Start(new JsonObject { ["name"] = "prova.csv", ["format"] = "parquet", ["start_ms"] = Base + 1e9 }, _ => library);
        var deadline = DateTime.UtcNow.AddSeconds(30);
        while ((string?)empty["state"] == "running" && DateTime.UtcNow < deadline) { Thread.Sleep(20); empty = conversions.Status((string)empty["id"]!); }
        Assert.Equal("error", empty["state"]);
        Assert.Contains("Nessun campione", (string)empty["error"]!);
        Assert.Empty(Directory.GetFiles(Path.Combine(dir.Path, Conversions.Folder)));
    }
}
