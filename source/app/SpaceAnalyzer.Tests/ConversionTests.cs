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

    /// <summary>CSV recording where each signal has its own data type and value function (sampled together every 10 ms).</summary>
    static RecordingLibrary TypedFixture(TempDir dir, int samples, params (string id, string dtype, Func<int, double> value)[] signals)
    {
        var sb = new StringBuilder("timestamp_utc,timestamp_ms,cycle,node_id,signal_name,value_numeric,value_text,data_type,status,good,sample_ms,quality\r\n");
        for (var i = 0; i < samples; i++)
        {
            var t = Base + i * 10;
            var stamp = DateTime.UnixEpoch.AddMilliseconds(t).ToString("yyyy-MM-dd'T'HH:mm:ss.fff'+00:00'", CultureInfo.InvariantCulture);
            foreach (var (id, dtype, value) in signals)
            {
                var text = value(i).ToString("R", CultureInfo.InvariantCulture);
                sb.Append($"{stamp},{t.ToString(CultureInfo.InvariantCulture)},{i},ns=2;s={id},{id},{text},{text},{dtype},Good,True,10,Good\r\n");
            }
        }
        File.WriteAllText(Path.Combine(dir.Path, "tipi.csv"), sb.ToString());
        Py.AtomicPlain(Path.Combine(dir.Path, "tipi.metadata.json"), new Dictionary<string, object?>
        {
            ["signals"] = signals.Select(s => (object?)new Dictionary<string, object?> { ["nodeid"] = "ns=2;s=" + s.id, ["display_name"] = s.id, ["unit"] = "" }).ToList(),
            ["initial_sample_ms"] = 10,
        });
        return new RecordingLibrary(dir.Path);
    }

    static (string id, string dtype, Func<int, double> value)[] MixedSignals() =>
    [
        ("Piccolo", "UInt16", i => i * 3),                       // 16 bit, valori piccoli: Int16 esatto
        ("Alto", "UInt16", i => 40000 + i * 150),                // 16 bit oltre 32767: Float32 in RBF, offset in FDA
        ("Frazione", "Double", i => i * 0.37 + 0.001),           // 64 bit non intero: mai ristretto
        ("Flag", "Boolean", i => i % 2),
        ("Enorme", "Int64", i => 3_000_000_000d + i),            // 64 bit intero: mai ristretto a 16 bit
    ];

    static List<(string id, string type)> RbfTypes(RecordingLibrary library, string name) =>
        library.Metadata(library.PathOf(name))["rbf_channels"]!.AsArray().Select(m => ((string)m!["display_name"]!, (string)m["data_type"]!)).ToList();

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
    public void RbfUsesTheNarrowestExactTypePerSignal()
    {
        using var dir = new TempDir();
        var library = TypedFixture(dir, 100, MixedSignals());
        var file = Assert.Single(Files(Convert(library, "tipi.csv", "rbf")));
        var described = library.Describe((string)file["name"]!);
        Assert.Equal(
            [("Piccolo", "Int16"), ("Alto", "Float32"), ("Frazione", "Float32"), ("Flag", "Boolean"), ("Enorme", "Float32")],
            RbfTypes(library, (string)file["name"]!));
        // Dimensione del frame: 8 + (2+2) + (4+2) + (4+2) + (1+2) + (4+2) byte.
        var bytes = new FileInfo(library.PathOf((string)file["name"]!)).Length;
        Assert.True(bytes < 100 * 37 + 5000, "file RBF piu grande del previsto: " + bytes);
        var original = Samples(library, "tipi.csv");
        var back = Samples(library, (string)file["name"]!);
        // Valori interi di 16 bit, booleani e interi sotto 2^24 tornano identici; il 64 bit non intero e' Float32 (RBF non ha altro): archivio originale conservato.
        foreach (var id in new[] { "ns=2;s=Piccolo", "ns=2;s=Alto", "ns=2;s=Flag" })
            Assert.Equal(original.Where(x => x.id == id).Select(x => x.v), back.Where(x => x.id == id).Select(x => x.v));
        Assert.Equal(false, ((Dictionary<string, object?>)described["storage"]!)["lossless"]);
    }

    [Fact]
    public void RbfWithOnlyExactTypesIsDeclaredLossless()
    {
        using var dir = new TempDir();
        var library = TypedFixture(dir, 50, ("A", "UInt16", i => i), ("B", "Boolean", i => i % 2), ("C", "Real", i => i * 0.5));
        var file = Assert.Single(Files(Convert(library, "tipi.csv", "rbf")));
        var described = library.Describe((string)file["name"]!);
        Assert.Equal(true, ((Dictionary<string, object?>)described["storage"]!)["lossless"]);
        Assert.Equal([("A", "Int16"), ("B", "Boolean"), ("C", "Float32")], RbfTypes(library, (string)file["name"]!));
    }

    [Fact]
    public void FdaStoresSixteenBitIntegersExactlyEvenAbove32767()
    {
        using var dir = new TempDir();
        var library = TypedFixture(dir, 100, ("Alto", "UInt16", i => 40000 + i * 150), ("Piccolo", "UInt16", i => i * 3), ("Frazione", "Double", i => i * 0.37 + 0.001));
        var file = Assert.Single(Files(Convert(library, "tipi.csv", "das")));
        var back = Samples(library, (string)file["name"]!);
        var original = Samples(library, "tipi.csv");
        foreach (var id in new[] { "ns=2;s=Alto", "ns=2;s=Piccolo" })
            Assert.Equal(original.Where(x => x.id == id).Select(x => x.v), back.Where(x => x.id == id).Select(x => x.v));
        var meta = library.Metadata(library.PathOf((string)file["name"]!))["fda_channels"]!.AsArray().ToDictionary(m => (string)m!["display_name"]!, m => m!);
        Assert.Equal(1.0, (double)meta["Alto"]["gain"]!);
        Assert.NotEqual(0.0, (double)meta["Alto"]["offset"]!);
        Assert.Equal(0.0, (double)meta["Piccolo"]["offset"]!);
    }

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

    [Fact]
    public void ProgressPercentCoversReadingStagingAndWriting()
    {
        // CSV and Parquet only read: the share of rows read is the whole progress, capped below 100 until the job ends.
        Assert.Equal(0.0, Conversions.Percent("csv", 1000, "read", 0));
        Assert.Equal(50.0, Conversions.Percent("parquet", 1000, "read", 500));
        Assert.Equal(99.0, Conversions.Percent("csv", 1000, "read", 1000));
        // FDA and RBF: read 0-40, stage 40-70, write 70-100.
        Assert.Equal(20.0, Conversions.Percent("rbf", 1000, "read", 500));
        Assert.Equal(40.0, Conversions.Percent("das", 1000, "stage", 0));
        Assert.Equal(55.0, Conversions.Percent("das", 1000, "stage", 500));
        Assert.Equal(85.0, Conversions.Percent("rbf", 1000, "write", 500));
        Assert.Null(Conversions.Percent("csv", null, "read", 10));
    }

    [Fact]
    public void ExpectedRowsAreScaledByTheSelectedSignalsAndInterval()
    {
        var d = new Dictionary<string, object?> { ["rows_written"] = 2000L, ["start_ms"] = 1000.0, ["end_ms"] = 2000.0 };
        Assert.Equal(2000, Conversions.EstimateTotal(d, 2, 2, null, null));
        Assert.Equal(1000, Conversions.EstimateTotal(d, 1, 2, null, null));
        Assert.Equal(500, Conversions.EstimateTotal(d, 1, 2, 1000.0, 1500.0));
        Assert.Equal(1000, Conversions.EstimateTotal(d, 2, 2, 1500.0, 9000.0));
        Assert.Null(Conversions.EstimateTotal(new Dictionary<string, object?>(), 2, 2, null, null));
    }

    [Fact]
    public void JobReportsTotalPercentAndFinishesAtOneHundred()
    {
        using var dir = new TempDir();
        var library = Fixture(dir, 200);
        foreach (var format in new[] { "csv", "rbf" })
        {
            var job = Convert(library, "prova.csv", format);
            Assert.Equal(100.0, System.Convert.ToDouble(job["percent"]));
            Assert.Equal(400L, System.Convert.ToInt64(job["rows_total"]));
            Assert.Equal(false, job["rows_total_estimated"]);
        }
        var subset = Convert(library, "prova.csv", "csv", new JsonObject { ["signal_ids"] = new JsonArray("ns=2;s=A") });
        Assert.Equal(true, subset["rows_total_estimated"]);
        Assert.Equal(200L, System.Convert.ToInt64(subset["rows_total"]));
    }
}
