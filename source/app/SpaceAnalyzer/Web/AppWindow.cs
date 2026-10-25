using System.Diagnostics;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Web;

/// <summary>
/// Opens the interface as a desktop application window: Microsoft Edge (or Chrome) in app mode, with its own profile,
/// no tabs or address bar. Closing the last window stops the service. Falls back to the default browser.
/// The window starts full screen (F11 leaves it); SPACE_FULLSCREEN=0 starts it maximized instead.
/// SPACE_WINDOW=browser keeps the previous behaviour (a normal browser page).
/// </summary>
public static class AppWindow
{
    /// <summary>A browser process that exits this soon handed the window to an already running instance of the profile.</summary>
    static readonly TimeSpan Handoff = TimeSpan.FromSeconds(5);

    static int watching;

    public static void Open(string url, string profileDir, Action onClosed)
    {
        if (Environment.GetEnvironmentVariable("OPCUA_NO_BROWSER") is { Length: > 0 }) return;
        var browser = Environment.GetEnvironmentVariable("SPACE_WINDOW") == "browser" ? null : FindBrowser();
        if (browser == null)
        {
            OpenDefault(url);
            return;
        }
        try
        {
            Directory.CreateDirectory(profileDir);
            var info = new ProcessStartInfo(browser) { UseShellExecute = false };
            var size = Environment.GetEnvironmentVariable("SPACE_FULLSCREEN") == "0" ? "--start-maximized" : "--start-fullscreen";
            foreach (var arg in new[] { "--app=" + url, "--user-data-dir=" + profileDir, "--no-first-run", "--no-default-browser-check", size })
                info.ArgumentList.Add(arg);
            var process = Process.Start(info) ?? throw new InvalidOperationException("processo non avviato");
            Log.Info($"Finestra applicazione aperta con {Path.GetFileName(browser)}");
            if (Interlocked.Exchange(ref watching, 1) == 0) Watch(process, onClosed);
        }
        catch (Exception exc)
        {
            Log.Warning("Finestra applicazione non avviata: " + exc.Message);
            OpenDefault(url);
        }
    }

    static void Watch(Process process, Action onClosed)
    {
        var started = DateTime.UtcNow;
        var thread = new Thread(() =>
        {
            try
            {
                process.WaitForExit();
                if (DateTime.UtcNow - started < Handoff)
                {
                    // The profile was already open: the window lives in that process, which is not ours to follow.
                    Interlocked.Exchange(ref watching, 0);
                    return;
                }
                Log.Info("Finestra applicazione chiusa: arresto dei servizi");
                onClosed();
            }
            catch (Exception exc) { Log.Warning("Controllo finestra applicazione: " + exc.Message); }
        }) { IsBackground = true, Name = "app-window" };
        thread.Start();
    }

    static void OpenDefault(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch (Exception exc) { Log.Warning("Browser non avviato: " + exc.Message); }
    }

    /// <summary>Edge first (always present on Windows 10/11), then Chrome; registry App Paths and the usual folders.</summary>
    public static string? FindBrowser()
    {
        if (!OperatingSystem.IsWindows()) return null;
        foreach (var exe in new[] { "msedge.exe", "chrome.exe" })
        {
            foreach (var hive in new[] { Microsoft.Win32.Registry.CurrentUser, Microsoft.Win32.Registry.LocalMachine })
            {
                try
                {
                    using var key = hive.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + exe);
                    if (key?.GetValue(null) is string path && File.Exists(path.Trim('"'))) return path.Trim('"');
                }
                catch (Exception) { }
            }
            var folder = exe == "msedge.exe" ? @"Microsoft\Edge\Application" : @"Google\Chrome\Application";
            foreach (var root in new[] { Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.LocalApplicationData })
            {
                var candidate = Path.Combine(Environment.GetFolderPath(root), folder, exe);
                if (File.Exists(candidate)) return candidate;
            }
        }
        return null;
    }
}
