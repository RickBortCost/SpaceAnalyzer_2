using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Calculations;

/// <summary>Versioned, atomic formula registry (calculation_store.FormulaStore).</summary>
public sealed class FormulaStore(string path)
{
    public static readonly Regex CalcId = new("^calc:[0-9a-f]{32}$", RegexOptions.Compiled);
    readonly object gate = new();
    List<JsonObject> formulas = new();
    long revision;
    string error = "";

    static string Now() => Values.PythonIso(DateTime.UtcNow);

    public static JsonObject Normalize(JsonNode? data, JsonObject? previous = null)
    {
        if (data is not JsonObject d) throw new UserError("Definizione formula non valida");
        var ident = previous?["id"].Str() is { Length: > 0 } pid ? pid : d["id"].Truthy() ? d["id"].Str() ?? Validation(d["id"]) : Guid.NewGuid().ToString("N");
        var nid = "calc:" + ident;
        if (!CalcId.IsMatch(nid) || nid.EndsWith('\n')) throw new UserError("ID formula non valido");
        var name = d["name"].IsString() ? d["name"]!.Str()!.Trim() : "";
        if (name.Length == 0 || name.Length > 120) throw new UserError("Nome formula: da 1 a 120 caratteri");
        var unit = d.ContainsKey("unit") ? d["unit"] : "";
        if (!unit.IsString() || unit!.Str()!.Length > 40) throw new UserError("Unità: massimo 40 caratteri");
        var active = d.ContainsKey("active") ? d["active"] : true;
        var modes = d.ContainsKey("modes") ? d["modes"] : new JsonObject { ["live"] = true, ["recorded"] = true };
        if (!active.IsBool() || modes is not JsonObject m || new[] { "live", "recorded" }.Any(k => !m[k].IsBool()))
            throw new UserError("Specificare modalità Live/Recorded e stato attivo");
        if (!m.Any(p => p.Value.Truthy())) throw new UserError("Scegliere almeno una modalità: Live o Recorded");
        var alignment = d.ContainsKey("alignment") ? d["alignment"] : "alignment-default";
        var alignmentText = d.ContainsKey("alignment") ? alignment.Str() : "hold";
        if (alignmentText is not ("hold" or "exact")) throw new UserError("Allineamento temporale non valido");
        double age;
        var rawAge = d.ContainsKey("max_age_ms") ? d["max_age_ms"] : 1000;
        if (rawAge.Float() is not double a || !double.IsFinite(a) || a < 0 || a > 3600000) throw new UserError("Tolleranza temporale: da 0 a 3600000 ms");
        age = a;
        var bindings = d.ContainsKey("bindings") ? d["bindings"] : new JsonObject();
        var expression = new Expression(d["expression"], bindings);
        var clean = new JsonObject();
        foreach (var alias in expression.UsedBindings)
        {
            var source = bindings![alias];
            if (source is not JsonObject s || s["nodeid"].Str() is not string sid || sid.Length is < 1 or > 2048) throw new UserError($"Sorgente [{alias}] non valida");
            if (sid.StartsWith("calc:", StringComparison.Ordinal) && !CalcId.IsMatch(sid)) throw new UserError("Riferimento calcolato non valido");
            var kind = s.ContainsKey("kind") ? s["kind"].Str() : sid.StartsWith("calc:", StringComparison.Ordinal) ? "formula" : "signal";
            if (kind is not ("signal" or "formula" or "stored")) throw new UserError("Tipo riferimento non valido");
            if (kind == "stored" && m["live"].Bool() == true) throw new UserError("Una serie salvata senza formula attiva può essere usata solo in Recorded");
            var label = s["name"].Truthy() ? Config.Validation.PyStr(s["name"]) : alias;
            var sunit = s["unit"].Truthy() ? Config.Validation.PyStr(s["unit"]) : "";
            clean[alias] = new JsonObject { ["nodeid"] = sid, ["name"] = label.Length > 120 ? label[..120] : label, ["unit"] = sunit.Length > 40 ? sunit[..40] : sunit, ["kind"] = kind };
        }
        var clock = d["clock_nodeid"].Truthy() ? d["clock_nodeid"]?.DeepClone() : null;
        if (clock != null && !clean.Any(p => p.Value!["nodeid"].Str() == clock.Str())) throw new UserError("Il clock deve essere una sorgente della formula");
        return new JsonObject
        {
            ["id"] = ident, ["nodeid"] = nid, ["name"] = name, ["expression"] = d["expression"]!.Str()!.Trim(), ["bindings"] = clean,
            ["unit"] = unit.Str()!.Trim(), ["modes"] = new JsonObject { ["live"] = m["live"]!.DeepClone(), ["recorded"] = m["recorded"]!.DeepClone() },
            ["active"] = active!.DeepClone(), ["alignment"] = alignmentText, ["max_age_ms"] = age, ["clock_nodeid"] = clock,
            ["created_at"] = previous?["created_at"].Str() is { Length: > 0 } created ? created : Now(), ["updated_at"] = Now(),
            ["revision"] = (long)(previous?["revision"].Float() ?? 0) + 1,
        };
    }

    static string Validation(JsonNode? id) => throw new UserError("ID formula non valido");

    public static List<JsonObject> Check(List<JsonObject> list)
    {
        if (list.Select(f => f["name"]!.Str()!.ToLowerInvariant()).Distinct().Count() != list.Count) throw new UserError("Esiste già una formula con questo nome");
        var plan = new Plan(list);
        foreach (var f in list)
        {
            var nid = f["nodeid"]!.Str()!;
            var sources = plan.RawSources(nid);
            var kinds = sources.Select(s => s["kind"].Str() == "stored" ? "recorded" : Engine.Core.Sample.ProtocolOf(s["nodeid"]!.Str()!)).Distinct().ToList();
            f["protocol"] = kinds.Count == 1 ? kinds[0] : kinds.Count == 0 || f["modes"]!["live"].Bool() == true ? "any" : "recorded";
            f["required_sources"] = new JsonArray(sources.Select(s => (JsonNode?)s.DeepClone()).ToArray());
            f["dependencies"] = new JsonArray(((JsonObject)f["bindings"]!).Select(p => p.Value!).Where(s => s["nodeid"]!.Str()!.StartsWith("calc:", StringComparison.Ordinal) && s["kind"].Str() != "stored").Select(s => (JsonNode?)s["nodeid"]!.DeepClone()).ToArray());
            var units = ((JsonObject)f["bindings"]!).ToDictionary(p => p.Key, p =>
            {
                var s = p.Value!;
                var sid = s["nodeid"]!.Str()!;
                return sid.StartsWith("calc:", StringComparison.Ordinal) && s["kind"].Str() != "stored" ? plan.Formulas[sid]["unit"].Str() ?? "" : s["unit"].Str() ?? "";
            });
            f["unit_hint"] = plan.Expressions[nid].InferUnit(units);
        }
        return list;
    }

    public string Error { get { lock (gate) return error; } }

    public void Load(JsonNode? backup = null)
    {
        lock (gate)
        {
            try
            {
                var payload = File.Exists(path) ? Py.Parse(File.ReadAllText(path)) : backup?.DeepClone();
                if (!payload.Truthy()) { formulas = new(); revision = 0; return; }
                var normalized = new List<JsonObject>();
                foreach (var f in (JsonArray)payload!["formulas"]!)
                {
                    var row = Normalize(f, (JsonObject)f!);
                    row["revision"] = (long)(f!["revision"].Float() ?? 1);
                    row["updated_at"] = f["updated_at"]?.DeepClone() ?? row["updated_at"]!.DeepClone();
                    normalized.Add(row);
                }
                formulas = Check(normalized);
                revision = (long)(payload["revision"].Float() ?? 1);
                error = "";
                if (!File.Exists(path)) Write();
            }
            catch (Exception exc) when (exc is UserError or InvalidCastException or NullReferenceException or IOException or System.Text.Json.JsonException)
            {
                formulas = new(); error = "Formule non caricate: " + exc.Message;
            }
        }
    }

    public JsonObject Snapshot()
    {
        lock (gate)
            return new JsonObject
            {
                ["schema"] = 1, ["revision"] = revision, ["formulas"] = new JsonArray(formulas.Select(f => (JsonNode?)f.DeepClone()).ToArray()), ["error"] = error,
            };
    }

    public List<JsonObject> Formulas() { lock (gate) return formulas.Select(f => (JsonObject)f.DeepClone()).ToList(); }

    void Write() => Py.AtomicJson(path, Snapshot());

    public (JsonObject formula, List<JsonObject> all) Candidate(JsonNode? data)
    {
        lock (gate)
        {
            if (data is not JsonObject d) throw new UserError("Definizione formula non valida");
            var old = formulas.FirstOrDefault(f => d["id"] != null && f["id"].Str() == d["id"].Str() && d["id"].Truthy());
            if (d["id"].Truthy() && old == null) throw new UserError("Formula da modificare non trovata");
            if (old != null && d.ContainsKey("expected_revision") && !d["expected_revision"].IsNull() && d["expected_revision"].Float() != old["revision"].Float())
                throw new UserError("Formula modificata da un’altra finestra: ricaricare prima di salvare");
            var f = Normalize(d, old);
            var list = formulas.Where(x => x["id"].Str() != f["id"].Str()).Select(x => (JsonObject)x.DeepClone()).Append(f).ToList();
            Check(list);
            return (f, list);
        }
    }

    public JsonObject Save(JsonNode? data)
    {
        lock (gate)
        {
            var (f, list) = Candidate(data);
            var old = formulas; var rev = revision;
            formulas = list; revision++; error = "";
            try { Write(); }
            catch { formulas = old; revision = rev; throw; }
            return (JsonObject)f.DeepClone();
        }
    }

    public void Delete(string? ident)
    {
        lock (gate)
        {
            var f = formulas.FirstOrDefault(x => x["id"].Str() == ident) ?? throw new UserError("Formula non trovata");
            var users = formulas.Where(g => g["dependencies"] is JsonArray deps && deps.Any(n => n.Str() == f["nodeid"].Str())).Select(g => g["name"].Str()).ToList();
            if (users.Count > 0) throw new UserError("Prima modificare o eliminare le formule dipendenti: " + string.Join(", ", users));
            var old = formulas; var rev = revision;
            formulas = formulas.Where(g => g["id"].Str() != ident).ToList(); revision++;
            try { Write(); }
            catch { formulas = old; revision = rev; throw; }
        }
    }

    public JsonObject Duplicate(string? ident)
    {
        lock (gate)
        {
            var original = formulas.FirstOrDefault(x => x["id"].Str() == ident) ?? throw new UserError("Formula non trovata");
            var data = (JsonObject)original.DeepClone();
            data.Remove("id");
            data["active"] = false;
            var names = formulas.Select(f => f["name"]!.Str()!.ToLowerInvariant()).ToHashSet();
            var baseName = original["name"]!.Str()!;
            if (baseName.Length > 100) baseName = baseName[..100];
            for (var i = 1; ; i++)
            {
                data["name"] = baseName + (i == 1 ? " (copia)" : $" (copia {i})");
                if (!names.Contains(data["name"]!.Str()!.ToLowerInvariant())) break;
            }
            return Save(data);
        }
    }
}
