using System.Globalization;
using System.Text.Json.Nodes;
using SpaceAnalyzer.App.Infrastructure;
using SpaceAnalyzer.Engine.Core;

namespace SpaceAnalyzer.App.Calculations;

/// <summary>Dependency order of the formulas needed for some calculated ids (formula_engine.Plan).</summary>
public sealed class Plan
{
    public const int MaxFormulas = 64;
    public Dictionary<string, JsonObject> Formulas { get; } = new();
    public Dictionary<string, Expression> Expressions { get; } = new();
    public List<string> Order { get; }
    public List<string> Ids { get; }
    public HashSet<string> RawIds { get; } = new();
    public HashSet<string> ExternalIds { get; }
    public string Mode { get; }

    static string Source(JsonObject f, string name) => f["bindings"]![name]!["nodeid"]!.Str()!;
    static bool Stored(JsonObject f, string name) => f["bindings"]![name]!["kind"].Str() == "stored";

    public Plan(IEnumerable<JsonObject> formulas, string mode = "live", IEnumerable<string>? ids = null, string? protocol = null, IEnumerable<string>? externalIds = null)
    {
        var list = formulas.ToList();
        if (list.Count > MaxFormulas) throw new UserError("Massimo 64 formule");
        foreach (var f in list) Formulas[f["nodeid"]!.Str()!] = f;
        if (Formulas.Count != list.Count) throw new UserError("ID formula duplicato");
        foreach (var (nid, f) in Formulas) Expressions[nid] = new Expression(f["expression"], f["bindings"]);
        ExternalIds = externalIds?.ToHashSet() ?? new();
        var order = new List<string>();
        var visiting = new HashSet<string>(); var visited = new HashSet<string>();
        void Visit(string nid)
        {
            if (visiting.Contains(nid)) throw new UserError("Riferimento circolare nella formula " + Formulas[nid]["name"].Str());
            if (visited.Contains(nid)) return;
            if (!Formulas.ContainsKey(nid)) throw new UserError("Una variabile calcolata referenziata non esiste più");
            visiting.Add(nid);
            foreach (var name in Expressions[nid].UsedBindings)
            {
                var source = Source(Formulas[nid], name);
                if (source.StartsWith("calc:", StringComparison.Ordinal) && !Stored(Formulas[nid], name) && !ExternalIds.Contains(source)) Visit(source);
            }
            visiting.Remove(nid); visited.Add(nid); order.Add(nid);
        }
        foreach (var nid in Formulas.Keys) Visit(nid);
        Mode = mode;
        Ids = ids?.ToList() ?? Formulas.Where(p => p.Value["active"].Bool() == true && p.Value["modes"]?[mode].Bool() == true
            && (protocol == null || p.Value["protocol"].Str() is var proto && (proto == protocol || proto == "any"))).Select(p => p.Key).ToList();
        if (Ids.Any(nid => !Formulas.ContainsKey(nid))) throw new UserError("Formula non trovata");
        var needed = new HashSet<string>();
        void Collect(string nid)
        {
            if (!needed.Add(nid)) return;
            foreach (var name in Expressions[nid].UsedBindings)
            {
                var source = Source(Formulas[nid], name);
                if (source.StartsWith("calc:", StringComparison.Ordinal) && !Stored(Formulas[nid], name) && !ExternalIds.Contains(source)) Collect(source);
            }
        }
        foreach (var nid in Ids) Collect(nid);
        Order = order.Where(needed.Contains).ToList();
        foreach (var nid in Order)
            foreach (var name in Expressions[nid].UsedBindings)
            {
                var source = Source(Formulas[nid], name);
                if (!source.StartsWith("calc:", StringComparison.Ordinal) || ExternalIds.Contains(source) || Stored(Formulas[nid], name)) RawIds.Add(source);
            }
    }

    public List<JsonObject> RawSources(string nid)
    {
        var found = new List<(string id, JsonObject source)>();
        var visited = new HashSet<string>();
        void Visit(string key)
        {
            if (!visited.Add(key)) return;
            var f = Formulas[key];
            foreach (var name in Expressions[key].UsedBindings)
            {
                var source = (JsonObject)f["bindings"]![name]!;
                var id = source["nodeid"]!.Str()!;
                if (id.StartsWith("calc:", StringComparison.Ordinal) && source["kind"].Str() != "stored" && !ExternalIds.Contains(id)) Visit(id);
                else
                {
                    var index = found.FindIndex(x => x.id == id);
                    if (index >= 0) found[index] = (id, source); else found.Add((id, source));
                }
            }
        }
        Visit(nid);
        return found.Select(x => x.source).ToList();
    }
}

/// <summary>One original input sample of a formula engine.</summary>
public sealed record FormulaInput(string NodeId, double? Value, string Quality);

/// <summary>
/// Evaluates only changed dependency paths (formula_engine.Engine): past values expire,
/// future values are never used.
/// </summary>
public sealed class FormulaEngine(Plan plan)
{
    readonly Dictionary<string, (double t, double? value, string quality)> values = new();
    double? lastTime;
    public long LateSamples { get; private set; }
    public Plan Plan => plan;

    public static string Stamp(double t) => Values.IsoMicro(Engine.Recording.TableRecorder.PythonFromTimestamp(t));

    public List<Dictionary<string, object?>> Process(double t, IReadOnlyList<FormulaInput> rows)
    {
        if (lastTime is double last && t < last) { LateSamples += rows.Count; return new(); }
        lastTime = t;
        var changed = new HashSet<string>();
        foreach (var row in rows)
        {
            if (!plan.RawIds.Contains(row.NodeId)) continue;
            values[row.NodeId] = (t, row.Value is double v && double.IsFinite(v) ? v : null, row.Quality);
            changed.Add(row.NodeId);
        }
        var output = new List<Dictionary<string, object?>>();
        foreach (var nid in plan.Order)
        {
            var f = plan.Formulas[nid];
            var expression = plan.Expressions[nid];
            var inputs = expression.UsedBindings.Select(name => (name, source: f["bindings"]![name]!["nodeid"]!.Str()!)).ToList();
            if (inputs.Count > 0 && !inputs.Any(i => changed.Contains(i.source))) continue;
            var clock = f["clock_nodeid"].Str();
            if (clock is { Length: > 0 } && !changed.Contains(clock)) continue;
            double? value = null;
            var quality = "Good";
            var error = "";
            if (f["active"].Bool() != true || f["modes"]?[plan.Mode].Bool() != true) { quality = "Bad"; error = "Formula disattivata in questa modalità"; }
            else
            {
                var args = new Dictionary<string, double>();
                var maxAge = f["max_age_ms"].Num() ?? 1000;
                foreach (var (name, source) in inputs)
                {
                    if (!values.TryGetValue(source, out var item)) { quality = "Bad"; error = $"Sorgente [{name}] non disponibile"; break; }
                    var age = t - item.t;
                    if (age < 0 || age > maxAge || (f["alignment"].Str() == "exact" && age != 0))
                    {
                        quality = "Bad"; error = $"Sorgente [{name}] fuori tolleranza temporale ({age.ToString("F3", CultureInfo.InvariantCulture)} ms)"; break;
                    }
                    if (item.quality != "Good" || item.value == null)
                    {
                        quality = item.quality is "Bad" or "Uncertain" ? item.quality : "Bad";
                        error = $"Sorgente [{name}] nulla o qualità {item.quality}"; break;
                    }
                    args[name] = item.value.Value;
                }
                if (error.Length == 0)
                {
                    try { value = expression.Evaluate(args); }
                    catch (Expression.ZeroDivision) { quality = "Bad"; error = "Divisione per zero"; }
                    catch (Exception exc) when (exc is Expression.DomainError or OverflowException or UserError) { quality = "Bad"; error = "Dominio matematico non valido o risultato non finito"; }
                }
            }
            values[nid] = (t, value, quality);
            changed.Add(nid);
            if (plan.Ids.Contains(nid))
            {
                var stamp = Stamp(t);
                output.Add(new Dictionary<string, object?>
                {
                    ["node_id"] = nid, ["signal_name"] = f["name"].Str(), ["timestamp_ms"] = t, ["timestamp_utc"] = stamp, ["value_numeric"] = value,
                    ["value_text"] = value is double v2 ? PyRepr(v2) : null, ["data_type"] = "Double", ["quality"] = quality, ["good"] = quality == "Good",
                    ["status"] = error.Length == 0 ? "CalculatedGood" : error, ["status_code"] = null, ["source_timestamp"] = stamp,
                    ["server_timestamp"] = null, ["local_timestamp"] = Values.PythonIso(DateTime.UtcNow), ["ads_filetime"] = null,
                });
            }
        }
        return output;
    }

    static string PyRepr(double v) => Values.PythonRepr(v);
}
