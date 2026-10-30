using System.Text.Json;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;
using SpaceAnalyzer.Engine.Core;
using SpaceAnalyzer.Engine.Recording;
using Xunit;

namespace SpaceAnalyzer.Tests;

/// <summary>Esportazione degli archivi primari (.sac): ogni campione originale torna in un file leggibile.</summary>
public class PrimaryExportTests
{
    const double T0 = 1_700_000_000_000d;
    const int Cycles = 400, Period = 100;
    static readonly string[] Ids = ["opc:Pressione", "opc:Forza", "opc:Marcia"];

    static NodeSpec Node(string id, string unit) => NodeSpec.FromJson(JsonDocument.Parse($"{{\"nodeid\":\"{id}\",\"display_name\":\"{id[4..]}\",\"unit\":\"{unit}\"}}").RootElement.Clone());

    /// <summary>A real primary capture written by the application's PrimaryRecorder.</summary>
    static string Capture(string root, int cycles = Cycles, string? fail = null)
    {
        var recorder = new PrimaryRecorder(Path.Combine(root, ".primary"));
        var nodes = new[] { Node(Ids[0], "bar"), Node(Ids[1], "kN"), Node(Ids[2], "") };
        recorder.Start(nodes, w =>
        {
            w.WriteString("application_version", "test");
            w.WriteString("selected_format", "parquet");
            w.WriteString("endpoint", "opc.tcp://demo:4840");
            w.WriteStartObject("session");
            w.WriteString("session_name", "Prova esportazione");
            w.WriteString("machine", "Linea 2");
            w.WriteString("operator", "Rossi");
            w.WriteString("description", "");
            w.WriteEndObject();
        }, new Dictionary<string, string>());
        for (var c = 0; c < cycles; c++)
        {
            var t = T0 + c * Period;
            var rows = Ids.Select((id, i) => new Sample
            {
                NodeId = id, Value = i == 2 ? c % 2 == 0 : (object)(c * 0.5 + i), SourceValue = i == 2 ? c % 2 == 0 : (object)(c * 0.5 + i), HasSourceValue = true,
                PlotValue = i == 2 ? c % 2 : c * 0.5 + i, Numeric = true, DataType = i == 2 ? "Boolean" : "Double", Good = true, Quality = "Good", Status = "Good", StatusCode = 0,
                ReceivedNs = (long)(t * 1_000_000), ReceivedMonoNs = (long)(t * 1_000_000), LocalTs = "2026-10-21T09:30:00.000000+00:00", AcquisitionTimestampMs = t,
            }).ToList();
            while (!recorder.Enqueue(t, rows, Period)) Thread.Sleep(1);
        }
        if (fail != null) recorder.Fail(fail);
        recorder.RequestStop();
        Assert.True(recorder.WaitDone(TimeSpan.FromSeconds(30)));
        if (fail == null) Assert.Equal("", recorder.Error);
        var final = recorder.FinalPath;
        recorder.Dispose();
        return final;
    }

    [Theory]
    [InlineData("csv")]
    [InlineData("parquet")]
    public void Every_sample_of_the_capture_is_written(string format)
    {
        using var dir = new TempDir();
        var capture = Capture(dir.Path);
        var output = Path.Combine(dir.Path, "Conversioni");
        var result = PrimaryExport.Export(capture, format, output, "test");
        Assert.Equal(Cycles * Ids.Length, result.Samples);
        Assert.True(result.Finalized);
        Assert.Null(result.Warning);
        var file = Assert.Single(result.Files);
        Assert.True(File.Exists((string)file["path"]!));
        Assert.Equal((long)(Cycles * Ids.Length), file["rows"]);
        Assert.Equal(T0, result.First);
        Assert.Equal(T0 + (Cycles - 1) * Period, result.Last);
        // Sidecar refers to the capture; no work folder is left behind.
        var sidecar = File.ReadAllText(RecordingLibrary.MetadataPath((string)file["path"]!));
        Assert.Contains("converted_from", sidecar);
        Assert.Contains(Path.GetFileName(capture), sidecar);
        Assert.Contains("Linea 2", sidecar);
        Assert.Empty(Directory.GetDirectories(output));
        if (format == "csv")
            Assert.Equal(Cycles * Ids.Length + 1, File.ReadAllLines((string)file["path"]!).Length);
    }

    [Theory]
    [InlineData("das")]
    [InlineData("rbf")]
    public void Fixed_rate_formats_are_produced_without_the_intermediate_parquet(string format)
    {
        using var dir = new TempDir();
        var capture = Capture(dir.Path);
        var output = Path.Combine(dir.Path, "Conversioni");
        var result = PrimaryExport.Export(capture, format, output, "test");
        Assert.Equal(Cycles * Ids.Length, result.Samples);
        Assert.NotEmpty(result.Files);
        Assert.All(result.Files, f => Assert.EndsWith(format == "das" ? ".das" : ".rbf", (string)f["file"]!));
        Assert.DoesNotContain(Directory.GetFiles(output), f => f.EndsWith(".parquet"));
        Assert.Empty(Directory.GetDirectories(output));
    }

    [Fact]
    public void Signal_and_interval_selection()
    {
        using var dir = new TempDir();
        var capture = Capture(dir.Path);
        var result = PrimaryExport.Export(capture, "csv", Path.Combine(dir.Path, "o"), "test", [Ids[0]], T0 + 100 * Period, T0 + 199 * Period);
        Assert.Equal(100, result.Samples);
        Assert.Equal(1, result.Channels);
        Assert.Throws<UserError>(() => PrimaryExport.Export(capture, "csv", Path.Combine(dir.Path, "o2"), "test", ["opc:Assente"]));
        Assert.Throws<UserError>(() => PrimaryExport.Export(capture, "csv", Path.Combine(dir.Path, "o3"), "test", null, T0 + 1_000_000, T0 + 2_000_000));
    }

    [Fact]
    public void A_truncated_capture_exports_the_valid_part_with_a_warning()
    {
        using var dir = new TempDir();
        var capture = Capture(dir.Path, cycles: 4000);
        var broken = Path.Combine(dir.Path, ".primary", "interrotto.sac.part");
        var bytes = File.ReadAllBytes(capture);
        File.WriteAllBytes(broken, bytes[..(int)(bytes.Length * 0.6)]);
        var result = PrimaryExport.Export(broken, "csv", Path.Combine(dir.Path, "o"), "test");
        Assert.InRange(result.Samples, 1, 4000 * Ids.Length - 1);
        Assert.False(result.Finalized);
        Assert.NotNull(result.Warning);
        Assert.Equal(result.Samples, ((long)result.Files[0]["rows"]!));
    }

    [Fact]
    public void A_capture_closed_with_an_error_exports_what_it_holds_and_says_so()
    {
        using var dir = new TempDir();
        var capture = Capture(dir.Path, cycles: 50, fail: "Disco pieno");
        Assert.EndsWith(".incomplete.sac", capture);
        var result = PrimaryExport.Export(capture, "csv", Path.Combine(dir.Path, "o"), "test");
        Assert.True(result.Samples > 0);
        Assert.True(result.Finalized);
        Assert.Contains("chiuso con errore", result.Warning);
        Assert.Contains("Disco pieno", result.Warning);
    }

    [Fact]
    public void An_unreadable_capture_is_refused_and_listed_with_the_reason()
    {
        using var dir = new TempDir();
        var junk = Path.Combine(dir.Path, ".primary", "rotto.sac");
        Directory.CreateDirectory(Path.GetDirectoryName(junk)!);
        File.WriteAllBytes(junk, new byte[200]);
        Assert.ThrowsAny<Exception>(() => PrimaryExport.Export(junk, "csv", Path.Combine(dir.Path, "o"), "test"));
        Assert.True(Directory.Exists(Path.Combine(dir.Path, "o")) ? Directory.GetFileSystemEntries(Path.Combine(dir.Path, "o")).Length == 0 : true);
        var entry = PrimaryExport.Describe(junk);
        Assert.Contains("non leggibile", (string)entry["read_error"]!);
    }

    [Fact]
    public void Describe_lists_signals_and_state_without_scanning_the_capture()
    {
        using var dir = new TempDir();
        var capture = Capture(dir.Path, cycles: 10);
        var entry = PrimaryExport.Describe(capture);
        Assert.Equal("sac", entry["format"]);
        Assert.Equal("complete", entry["state"]);
        Assert.Equal(Ids.Length, ((List<object?>)entry["signals"]!).Count);
        Assert.Contains("Prova esportazione", (string)entry["display_name"]!);
        Assert.NotNull(entry["started_at"]);
        Assert.Equal("part", PrimaryExport.Describe(CopyAs(capture, "x.sac.part"))["state"]);
        Assert.Equal("incomplete", PrimaryExport.Describe(CopyAs(capture, "x.incomplete.sac"))["state"]);
        Assert.Equal("recovered", PrimaryExport.Describe(CopyAs(capture, "x.sac.recovered.sac"))["state"]);
    }

    static string CopyAs(string source, string name)
    {
        var target = Path.Combine(Path.GetDirectoryName(source)!, name);
        File.Copy(source, target, overwrite: true);
        return target;
    }

    [Theory]
    [InlineData("a.sac", true)]
    [InlineData("a.sac.part", true)]
    [InlineData("../a.sac", false)]
    [InlineData("sub/a.sac", false)]
    [InlineData("a.parquet", false)]
    [InlineData("", false)]
    public void Only_capture_file_names_are_accepted(string name, bool expected) => Assert.Equal(expected, PrimaryExport.IsPrimaryName(name));
}
