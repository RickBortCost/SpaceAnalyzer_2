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
    public string RecordingsDir => Path.Combine(ApplicationDir, "Recordings");
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
