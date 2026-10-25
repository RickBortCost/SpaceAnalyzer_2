namespace SpaceAnalyzer.App.Infrastructure;

/// <summary>Folders of the 2.x layout: settings under the data folder, recordings beside the application.</summary>
public sealed class AppPaths
{
    public string BaseDir { get; }
    public string DataDir { get; }
    public string ApplicationDir { get; }
    public string CertDir => Path.Combine(DataDir, "certificates");
    public string ConfigFile => Path.Combine(DataDir, "config.json");
    public string CertFile => Path.Combine(CertDir, "client.der");
    public string KeyFile => Path.Combine(CertDir, "client-key.pem");
    public string ServerCertFile => Path.Combine(CertDir, "server.der");
    public string SettingsDir => Path.Combine(DataDir, "settings");
    public string SettingsFile => Path.Combine(SettingsDir, "saved_settings.json");
    public string SettingsCertDir => Path.Combine(SettingsDir, "certificates");
    /// <summary>Folder of new recordings, exports and the Recordings page: the operator's choice, or Recordings beside the application.</summary>
    public string RecordingsDir { get; private set; } = "";
    public string DefaultRecordingsDir => Path.Combine(ApplicationDir, "Recordings");
    public string RecordingsLocationFile => Path.Combine(DataDir, "recordings_location.json");
    /// <summary>Why the saved folder could not be used at startup (the default is used instead).</summary>
    public string? RecordingsLocationWarning { get; private set; }
    public string ImportedRecordingsDir => Path.Combine(DataDir, "recording_imports");
    public string WorkspaceFile => Path.Combine(DataDir, "workspace.json");
    public string ExportDir => Path.Combine(RecordingsDir, "Exports");
    public string LogFile => Path.Combine(DataDir, "opcua-client.log");
    public string Templates => Path.Combine(BaseDir, "templates");
    public string Static => Path.Combine(BaseDir, "static");

    public AppPaths()
    {
        BaseDir = FindBase();
        var data = Environment.GetEnvironmentVariable("OPCUA_DATA_DIR");
        var packaged = File.Exists(Path.Combine(BaseDir, "bundled-runtime.flag")) || Environment.GetEnvironmentVariable("SPACE_PACKAGED") == "1";
        DataDir = Path.GetFullPath(!string.IsNullOrEmpty(data) ? ExpandUser(data)
            : packaged ? Path.Combine(Environment.GetEnvironmentVariable("LOCALAPPDATA") ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "OPCUA_Web_Client")
            : BaseDir);
        var app = Environment.GetEnvironmentVariable("OPCUA_APP_DIR");
        ApplicationDir = Path.GetFullPath(!string.IsNullOrEmpty(app) ? app : packaged ? AppContext.BaseDirectory : BaseDir);
        RecordingsDir = DefaultRecordingsDir;
        try
        {
            if (File.Exists(RecordingsLocationFile) && System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(RecordingsLocationFile))?["directory"]?.GetValue<string>() is { Length: > 0 } saved)
                RecordingsDir = Usable(saved);
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException or UserError or System.Text.Json.JsonException or InvalidOperationException or ArgumentException or NotSupportedException)
        {
            RecordingsLocationWarning = "Cartella registrazioni salvata non disponibile (" + exc.Message + "): uso " + DefaultRecordingsDir;
        }
    }

    /// <summary>Chooses the recordings folder (null or empty: the default) after checking that it can be written; the choice is kept across restarts.</summary>
    public void SetRecordingsDir(string? path)
    {
        var target = string.IsNullOrWhiteSpace(path) ? DefaultRecordingsDir : Usable(path.Trim());
        Directory.CreateDirectory(DataDir);
        Py.AtomicPlain(RecordingsLocationFile, new Dictionary<string, object?> { ["directory"] = target == DefaultRecordingsDir ? "" : target });
        RecordingsDir = target;
        RecordingsLocationWarning = null;
    }

    static string Usable(string path)
    {
        path = ExpandUser(path);
        if (!Path.IsPathFullyQualified(path)) throw new UserError("Indicare un percorso completo della cartella (ad esempio D:\\Registrazioni)");
        var full = Path.GetFullPath(path);
        try
        {
            Directory.CreateDirectory(full);
            var probe = Path.Combine(full, ".space-analyzer-write-test-" + Guid.NewGuid().ToString("N"));
            File.WriteAllBytes(probe, [1]);
            File.Delete(probe);
        }
        catch (Exception exc) when (exc is IOException or UnauthorizedAccessException)
        {
            throw new UserError("Cartella non scrivibile: " + full + " (" + exc.Message + ")");
        }
        return full;
    }

    static string ExpandUser(string path) =>
        path.StartsWith('~') ? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile) + path[1..] : path;

    /// <summary>templates/ and static/ next to the executable, or the source folder in development.</summary>
    static string FindBase()
    {
        if (Environment.GetEnvironmentVariable("SPACE_BASE_DIR") is { Length: > 0 } configured) return Path.GetFullPath(configured);
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (var d = dir; d != null; d = d.Parent)
            if (Directory.Exists(Path.Combine(d.FullName, "templates")) && Directory.Exists(Path.Combine(d.FullName, "static"))) return d.FullName;
        return dir.FullName;
    }
}
