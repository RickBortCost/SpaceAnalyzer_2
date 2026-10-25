using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using Xunit;

namespace SpaceAnalyzer.Tests;

public class AtomicJsonTests
{
    [Fact]
    public async Task ConcurrentWritersAndReadersNeverFailOrLeaveTemporaryFiles()
    {
        using var dir = new TempDir();
        var path = System.IO.Path.Combine(dir.Path, "settings.json");
        var errors = new ConcurrentBag<Exception>();
        var writers = Enumerable.Range(0, 8).Select(w => Task.Run(() =>
        {
            for (var i = 0; i < 60; i++)
            {
                try { Py.AtomicJson(path, new JsonObject { ["writer"] = w, ["n"] = i }); }
                catch (Exception exc) { errors.Add(exc); }
            }
        }));
        var readers = Enumerable.Range(0, 4).Select(_ => Task.Run(() =>
        {
            for (var i = 0; i < 200; i++)
            {
                try { if (Py.ReadObject(path) is { } read && read["writer"] == null) errors.Add(new InvalidDataException("lettura parziale")); }
                catch (Exception exc) { errors.Add(exc); }
            }
        }));
        await Task.WhenAll(writers.Concat(readers));
        Assert.Empty(errors);
        Assert.Equal(["settings.json"], Directory.GetFiles(dir.Path).Select(System.IO.Path.GetFileName));
        Assert.NotNull(Py.ReadObject(path)?["writer"]);
    }
}
