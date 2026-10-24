using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.App.Recordings;

namespace SpaceAnalyzer.App.Web;

/// <summary>Local recording imports and optional verified PDA3 sequences (pda3/import_service.py).</summary>
public static class Import
{
    static string Token() => Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant();

    public static async Task<object?> Files(HttpContext ctx, string directory, RecordingLibrary library)
    {
        if (!ctx.Request.HasFormContentType) throw new UserError("Seleziona CSV, Parquet, FDA DAS4, RBF v3 o DAT PDA3");
        var form = await ctx.Request.ReadFormAsync();
        var uploads = form.Files.GetFiles("files").ToList();
        if (uploads.Count == 0) uploads = form.Files.GetFiles("file").ToList();
        if (uploads.Count == 0 || !uploads.Any(f => f.FileName.Length > 0)) throw new UserError("Seleziona CSV, Parquet, FDA DAS4, RBF v3 o DAT PDA3");
        if (uploads.Count > 128) throw new UserError("Importazione: massimo 64 registrazioni e relativi manifest");
        Directory.CreateDirectory(directory);
        var manifests = new Dictionary<string, JsonObject>();
        var items = new List<Dictionary<string, object?>>();
        var errors = new List<string>();
        var warnings = new List<string>();
        static string BaseName(string name) => Path.GetFileName(name.Replace('\\', '/'));
        foreach (var f in uploads)
        {
            var original = BaseName(f.FileName ?? "");
            if (!original.ToLowerInvariant().EndsWith(".json", StringComparison.Ordinal)) continue;
            using var stream = f.OpenReadStream();
            var raw = new byte[1024 * 1024 + 1];
            var n = await stream.ReadAtLeastAsync(raw, raw.Length, false);
            if (n > 1024 * 1024) throw new UserError("Manifest JSON oltre 1 MiB");
            JsonNode? meta;
            try
            {
                var text = new UTF8Encoding(false, true).GetString(raw, 0, n);
                if (text.StartsWith('﻿')) text = text[1..];
                meta = JsonNode.Parse(text);
            }
            catch (Exception exc) when (exc is System.Text.Json.JsonException or DecoderFallbackException) { throw new UserError("Manifest JSON non valido: " + original); }
            if (meta is not JsonObject o) throw new UserError("Manifest JSON non valido");
            var key = original[..^5];
            if (key.EndsWith(".metadata", StringComparison.Ordinal)) key = key[..^9];
            manifests[key] = o;
        }
        foreach (var f in uploads)
        {
            var original = BaseName(f.FileName ?? "").Trim();
            if (original.Length == 0) original = "registrazione";
            var suffix = Path.GetExtension(original).ToLowerInvariant();
            if (suffix == ".json") continue;
            if (suffix is not (".csv" or ".parquet" or ".das" or ".rbf" or ".dat")) { errors.Add(original + ": formato non supportato"); continue; }
            var target = Path.Combine(directory, Token() + suffix);
            try
            {
                await using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write))
                await using (var input = f.OpenReadStream())
                    await input.CopyToAsync(output, 1024 * 1024);
                if (new FileInfo(target).Length == 0) throw new UserError("file vuoto");
                var meta = manifests.TryGetValue(Path.GetFileNameWithoutExtension(original), out var m) ? (JsonObject)m.DeepClone() : new JsonObject();
                meta["display_name"] = original;
                if (suffix == ".dat")
                {
                    if (!Pda3.IsPda3(target)) throw new UserError("Import DAT: solo PDA3 del modello 64 canali validato; varianti legacy/ignote non supportate");
                    if (!meta["pda3_profile"].Truthy())
                    {
                        var offset = form["pda3_timezone_offset_minutes"].ToString();
                        if (!form.ContainsKey("pda3_timezone_offset_minutes") || !Py.TryFloat(offset, out var minutes))
                            throw new UserError("DAT senza manifest: scegliere il fuso orario del calendario interno");
                        meta["pda3_timezone_offset_minutes"] = minutes;
                    }
                    Pda3.Details(target, meta);
                }
                Py.AtomicJson(Path.ChangeExtension(target, ".metadata.json"), meta);
                items.Add(library.Describe(Path.GetFileName(target), original, "local"));
            }
            catch (Exception exc)
            {
                foreach (var p in new[] { target, Path.ChangeExtension(target, ".metadata.json") })
                    try { File.Delete(p); } catch (IOException) { }
                errors.Add(original + ": " + exc.Message);
            }
        }
        if (items.Count == 0) throw new UserError(errors.Count > 0 ? string.Join("; ", errors) : "Nessuna registrazione selezionata");
        Dictionary<string, object?>? sequence = null;
        if (form["join_pda3"].ToString() == "true")
        {
            var dats = items.Where(i => (string?)i["format"] == "dat").ToList();
            if (dats.Count >= 2)
            {
                var seq = Path.Combine(directory, Token() + ".pda3seq");
                var label = $"Sequenza PDA3 · {dats.Count} file";
                try
                {
                    Py.AtomicPlain(seq, new Dictionary<string, object?> { ["parts"] = dats.Select(d => d["name"]).ToList() });
                    Pda3.SequenceInfo(library, seq);
                    Py.AtomicPlain(Path.ChangeExtension(seq, ".metadata.json"), new Dictionary<string, object?> { ["display_name"] = label });
                    sequence = library.Describe(Path.GetFileName(seq), label, "local");
                }
                catch (Exception exc) when (exc is UserError or IOException)
                {
                    foreach (var p in new[] { seq, Path.ChangeExtension(seq, ".metadata.json") })
                        try { File.Delete(p); } catch (IOException) { }
                    warnings.Add("File importati separatamente: " + exc.Message);
                }
            }
        }
        return new Dictionary<string, object?>
        {
            ["ok"] = true, ["recording"] = sequence ?? items[0], ["recordings"] = items, ["sequence"] = sequence, ["warnings"] = warnings, ["errors"] = errors,
        };
    }
}
