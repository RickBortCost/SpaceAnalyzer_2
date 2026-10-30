using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.AspNetCore.StaticFiles;
using Microsoft.Extensions.FileProviders;
using SpaceAnalyzer.App.Config;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Runtime;
using SpaceAnalyzer.App.Ssh;
using SpaceAnalyzer.App.Web;

namespace SpaceAnalyzer.App;

public static class Program
{
    const string Marker = "@@SPACE_ANALYZER_STARTUP";

    /// <summary>Startup progress markers for the launcher splash screen (only when SPACE_SPLASH=1).</summary>
    static void Progress(int percent, string label)
    {
        if (Environment.GetEnvironmentVariable("SPACE_SPLASH") != "1") return;
        try { Console.WriteLine($"{Marker} {Math.Clamp(percent, 0, 100)} {label}"); Console.Out.Flush(); }
        catch (IOException) { }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    [DllImport("winmm.dll")]
    static extern uint timeBeginPeriod(uint period);

    [DllImport("winmm.dll")]
    static extern uint timeEndPeriod(uint period);

    public static int Main(string[] args)
    {
        // Redirected output (launcher log, tools) is UTF-8 whatever the console code page.
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        if (args.Length > 0 && args[0] == "--tool") return Tool(args[1..]);
        try { return Run(args); }
        catch (Exception exc)
        {
            if (OperatingSystem.IsWindows()) MessageBoxW(IntPtr.Zero, exc.Message, Defaults.AppName + " - Errore di avvio", 16);
            Console.Error.WriteLine(exc);
            return 1;
        }
    }

    /// <summary>Offline tools (primary_tools.py): recover a primary capture, export a Parquet original to FDA/RBF.</summary>
    static int Tool(string[] args)
    {
        try
        {
            object? result = args.FirstOrDefault() switch
            {
                "recover" when args.Length >= 2 => Recordings.PrimaryRecovery.Recover(args[1], args.Length > 2 ? args[2] : null),
                "inspect" when args.Length >= 2 => Recordings.PrimaryRecovery.InspectReport(args[1]),
                "export-fda" or "export-rbf" when args.Length >= 3 => Export(args[0], args[1], args[2]),
                "export-sac" when args.Length >= 3 => ExportSac(args[1], args[2], args.Length > 3 ? args[3] : null),
                _ => throw new UserError("Uso: --tool inspect <file.sac> | recover <file.sac.part> [output] | export-sac <file.sac> csv|parquet|das|rbf [cartella] | export-fda|export-rbf <originale.parquet> <nodi.json> [max_bytes]"),
            };
            Console.WriteLine(Py.DumpsPlain(result, indent: true));
            return 0;
        }
        catch (Exception exc)
        {
            Console.Error.WriteLine(exc.Message);
            return 1;
        }

        static object? ExportSac(string capture, string format, string? output)
        {
            var result = Recordings.PrimaryExport.Export(capture, format.ToLowerInvariant(), output ?? Recordings.PrimaryExport.DefaultOutput(capture), Defaults.Version);
            return new Dictionary<string, object?>
            {
                ["format"] = result.Format, ["samples"] = result.Samples, ["channels"] = (long)result.Channels, ["finalized"] = result.Finalized, ["warning"] = result.Warning,
                ["files"] = result.Files.Select(f => (object?)new Dictionary<string, object?> { ["path"] = f["path"], ["bytes"] = f["bytes"], ["rows"] = f["rows"] }).ToList(),
            };
        }

        static object? Export(string kind, string original, string nodesFile)
        {
            using var doc = System.Text.Json.JsonDocument.Parse(File.ReadAllText(nodesFile));
            var nodes = doc.RootElement.EnumerateArray().Select(Engine.Core.NodeSpec.FromJson).ToList();
            return kind == "export-fda" ? Engine.Recording.FdaExporter.Export(original, nodes, 0) : Engine.Recording.RbfExporter.Export(original, nodes, 0);
        }
    }

    /// <summary>One service per data folder; subsequent launches reopen its browser page.</summary>
    static FileStream? ClaimInstance(AppPaths paths)
    {
        Directory.CreateDirectory(paths.DataDir);
        var lockPath = Path.Combine(paths.DataDir, "instance.lock");
        try { return new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException) { }
        using var http = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(1) };
        for (var attempt = 0; attempt < 20; attempt++)
        {
            try
            {
                var info = JsonNode.Parse(File.ReadAllText(Path.Combine(paths.DataDir, "instance.json")));
                var port = (int)(info.Get("port").Num() ?? 0);
                if (port is < 1024 or > 65535) throw new InvalidDataException("Porta non valida");
                var url = $"http://127.0.0.1:{port}";
                var status = JsonNode.Parse(http.GetStringAsync(url + "/api/status").GetAwaiter().GetResult());
                if (status.Get("app").Str() == Defaults.AppName)
                {
                    var running = status.Get("version").Str() ?? "sconosciuta";
                    if (running != Defaults.Version)
                        throw new InvalidOperationException($"È già attiva Space Fieldbus Analyzer V{running}. Chiudi la versione precedente con 'Chiudi applicazione' e poi avvia V{Defaults.Version}.");
                    AppWindow.Open(url, Path.Combine(paths.DataDir, "webapp"), () => { });
                    Console.WriteLine($"Applicazione già avviata: {url}");
                    return null;
                }
            }
            catch (InvalidOperationException) { throw; }
            catch (Exception) { Thread.Sleep(250); }
        }
        throw new InvalidOperationException("L'applicazione è già in avvio. Attendere alcuni secondi e riprovare.");
    }

    static void Fatal(AppPaths paths, Exception? exc, string? thread)
    {
        var text = $"Space Fieldbus Analyzer V{Defaults.Version} - errore fatale {DateTime.Now:yyyy-MM-dd HH:mm:ss.fff}, thread {thread ?? "senza nome"}\n{exc}";
        try { Log.Error("Errore fatale del servizio (thread " + (thread ?? "senza nome") + ")", exc); } catch (Exception) { }
        try { File.WriteAllText(Path.Combine(paths.DataDir, $"crash-{DateTime.Now:yyyyMMdd-HHmmss}.txt"), text); } catch (Exception) { }
        try { Console.Error.WriteLine(text); } catch (Exception) { }
    }

    static int Run(string[] args)
    {
        Progress(70, "Moduli caricati");
        var paths = new AppPaths();
        using var instance = ClaimInstance(paths);
        if (instance == null)
        {
            Progress(100, "Istanza già avviata");
            return 0;
        }
        Directory.CreateDirectory(paths.DataDir);
        Log.Configure(paths.LogFile);
        Log.Info("Application start V" + Defaults.Version);
        // A fatal error ends the process: record it where the operator and support look first.
        AppDomain.CurrentDomain.UnhandledException += (_, e) => Fatal(paths, e.ExceptionObject as Exception, Thread.CurrentThread.Name);
        TaskScheduler.UnobservedTaskException += (_, e) => { Log.Error("Eccezione di un'attività non osservata", e.Exception); e.SetObserved(); };
        if (paths.RecordingsLocationWarning is { } warning) Log.Warning(warning);
        // Uploads ("Apri file dal PC") are buffered beside the other data, not in the Windows temp folder on C:.
        var uploads = Path.Combine(paths.DataDir, "tmp");
        // The launcher also points TMP/TEMP here: leftovers of a previous run are removed, files in use are skipped.
        try
        {
            Directory.CreateDirectory(uploads);
            foreach (var entry in new DirectoryInfo(uploads).EnumerateFileSystemInfos())
                try { if (entry is DirectoryInfo d) d.Delete(true); else entry.Delete(); } catch (Exception exc) when (exc is IOException or UnauthorizedAccessException) { }
            Environment.SetEnvironmentVariable("ASPNETCORE_TEMP", uploads);
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException) { Log.Warning("Cartella temporanea non disponibile: " + exc.Message); }
        Engine.Core.EngineLog.Sink = Log.Info;
        Directory.CreateDirectory(paths.ImportedRecordingsDir);
        var runtime = new AppRuntime(paths);
        runtime.Certs.RestoreSaved();
        runtime.Certs.BootstrapBundle();
        runtime.Initialize();
        var ssh = new SshService(Path.Combine(paths.SettingsDir, "ssh"));
        var token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        Progress(85, "Servizi inizializzati");

        var requested = int.TryParse(Environment.GetEnvironmentVariable("OPCUA_WEB_PORT"), out var p) ? p : 5000;
        WebApplication? app = null;
        var port = 0;
        for (var candidate = requested; candidate < requested + 20; candidate++)
        {
            var built = Build(paths, runtime, ssh, token, candidate, () => app?.StopAsync().Wait(TimeSpan.FromSeconds(30)));
            try
            {
                built.StartAsync().GetAwaiter().GetResult();
                app = built;
                port = candidate;
                break;
            }
            catch (IOException) { built.DisposeAsync().AsTask().Wait(); }
            catch (Microsoft.AspNetCore.Connections.AddressInUseException) { built.DisposeAsync().AsTask().Wait(); }
        }
        if (app == null) throw new InvalidOperationException($"Nessuna porta locale disponibile tra {requested} e {requested + 19}");
        var url = $"http://127.0.0.1:{port}";
        Py.AtomicPlain(Path.Combine(paths.DataDir, "instance.json"), new Dictionary<string, object?> { ["port"] = (long)port, ["pid"] = (long)Environment.ProcessId, ["version"] = Defaults.Version });
        Console.WriteLine($"{Defaults.AppName} V{Defaults.Version}\nPagina: {url}\nImpostazioni: {paths.DataDir}\nChiudi la finestra dell'applicazione o usa 'Chiudi applicazione' per terminare i servizi.");
        Progress(95, "Servizio HTTP pronto");
        AppWindow.Open(url, Path.Combine(paths.DataDir, "webapp"), () => app.StopAsync().Wait(TimeSpan.FromSeconds(30)));
        Progress(100, "Pronto");
        var timer = OperatingSystem.IsWindows() && timeBeginPeriod(1) == 0;
        try { app.WaitForShutdownAsync().GetAwaiter().GetResult(); }
        finally
        {
            ssh.Close();
            runtime.Dispose();
            app.DisposeAsync().AsTask().Wait(TimeSpan.FromSeconds(10));
            if (timer) timeEndPeriod(1);
            Log.Info("Application stop");
        }
        return 0;
    }

    static WebApplication Build(AppPaths paths, AppRuntime runtime, SshService ssh, string token, int port, Action shutdown)
    {
        var builder = WebApplication.CreateSlimBuilder(new WebApplicationOptions { ContentRootPath = paths.BaseDir });
        builder.Logging.ClearProviders();
        builder.WebHost.ConfigureKestrel(options =>
        {
            options.Listen(IPAddress.Loopback, port);
            options.Limits.MaxRequestBodySize = null;
            options.AddServerHeader = false;
        });
        builder.Services.Configure<FormOptions>(o =>
        {
            o.MultipartBodyLengthLimit = long.MaxValue;
            o.ValueLengthLimit = int.MaxValue;
            o.MultipartHeadersLengthLimit = 64 * 1024;
        });
        builder.Services.Configure<KestrelServerOptions>(o => o.AllowSynchronousIO = false);
        builder.Services.AddRoutingCore();
        var app = builder.Build();

        app.Use(async (ctx, next) =>
        {
            ctx.Response.OnStarting(() =>
            {
                Http.SecurityHeaders(ctx);
                if (ctx.Request.Path.Value is { } path && (path.EndsWith(".js", StringComparison.Ordinal) || path.EndsWith(".mjs", StringComparison.Ordinal)))
                    ctx.Response.ContentType = "application/javascript; charset=utf-8";
                return Task.CompletedTask;
            });
            var host = ctx.Request.Host.Host;
            if (host is not ("127.0.0.1" or "localhost"))
            {
                await Http.Write(ctx, new Dictionary<string, object?> { ["error"] = "Accesso consentito solo da localhost" }, 403);
                return;
            }
            var post = HttpMethods.IsPost(ctx.Request.Method);
            var supplied = ctx.Request.Headers["X-OPCUA-Token"].ToString();
            if (post && supplied != token)
            {
                await Http.Write(ctx, new Dictionary<string, object?> { ["error"] = "Pagina scaduta: ricaricare l'interfaccia" }, 403);
                return;
            }
            if (ctx.Request.Path.StartsWithSegments("/api/ssh"))
            {
                if (supplied != token)
                {
                    await Http.Write(ctx, new Dictionary<string, object?> { ["error"] = "Pagina scaduta: ricaricare la pagina SSH" }, 403);
                    return;
                }
                if (ctx.Request.ContentLength > 512 * 1024)
                {
                    await Http.Write(ctx, new Dictionary<string, object?> { ["error"] = "Richiesta SSH: massimo 512 KiB" }, 413);
                    return;
                }
            }
            try
            {
                await next();
                if (!ctx.Response.HasStarted && ctx.Response.StatusCode == 405)
                    await Http.Write(ctx, new Dictionary<string, object?> { ["ok"] = false, ["error"] = "405 Method Not Allowed: The method is not allowed for the requested URL." }, 405);
            }
            catch (Exception exc) when (!ctx.Response.HasStarted)
            {
                var (status, message) = Http.Map(exc);
                if (status == 500) Log.Error("Operazione fallita", exc);
                await Http.Write(ctx, new Dictionary<string, object?> { ["ok"] = false, ["error"] = message }, status);
            }
        });

        var types = new FileExtensionContentTypeProvider();
        types.Mappings[".js"] = "application/javascript; charset=utf-8";
        types.Mappings[".mjs"] = "application/javascript; charset=utf-8";
        types.Mappings[".ico"] = "image/vnd.microsoft.icon";
        app.UseStaticFiles(new StaticFileOptions
        {
            FileProvider = new PhysicalFileProvider(paths.Static),
            RequestPath = "/static",
            ContentTypeProvider = types,
        });
        app.UseRouting();
        new Api(runtime, ssh, token, shutdown).Map(app);
        app.MapFallback(ctx => Http.Write(ctx, new Dictionary<string, object?>
        {
            ["ok"] = false,
            ["error"] = "404 Not Found: The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.",
        }, 404));
        return app;
    }
}
