using System.Net;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Config;

/// <summary>config.json, the saved settings profile and the workspace (load_config / save_config of 2.x).</summary>
public sealed class ConfigStore(AppPaths paths)
{
    public readonly object FileLock = new();

    public JsonObject Load()
    {
        var cfg = Defaults.Config();
        var loaded = false;
        if (File.Exists(paths.ConfigFile) && Py.ReadObject(paths.ConfigFile) is JsonObject saved)
        {
            foreach (var (k, v) in saved) cfg[k] = v?.DeepClone();
            loaded = true;
        }
        // The full saved profile is the fallback when config.json is missing or unreadable.
        if (!loaded && LoadSettings()["config"] is JsonObject profile)
            foreach (var (k, v) in profile) cfg[k] = v?.DeepClone();
        cfg["security"] = Defaults.NormalizePolicy(cfg["security"].Str() ?? "Basic256Sha256");
        if (!cfg["application_uri"].Truthy()) cfg["application_uri"] = $"urn:{Dns.GetHostName()}:DSiNC:OPCUAWebClient";
        return cfg;
    }

    public void Save(JsonObject cfg, bool uiLanguageUpdate = false)
    {
        lock (FileLock)
        {
            var copy = (JsonObject)cfg.DeepClone();
            copy["s7_symbolic_password"] = "";
            copy["mqtt_password"] = "";
            if (!uiLanguageUpdate) copy["ui_language"] = Load()["ui_language"]?.DeepClone();
            copy["security"] = Defaults.NormalizePolicy(copy["security"].Str() ?? "None");
            Py.AtomicJson(paths.ConfigFile, copy);
        }
    }

    public JsonObject Validated(JsonNode? data) => Validation.Validate(data, Load());

    public JsonObject LoadSettings() => File.Exists(paths.SettingsFile) && Py.ReadObject(paths.SettingsFile) is JsonObject o ? o : new JsonObject();

    public JsonObject LoadWorkspace() => Py.ReadObject(paths.WorkspaceFile) ?? new JsonObject();
}
