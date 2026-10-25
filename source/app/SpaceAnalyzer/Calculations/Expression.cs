using System.Globalization;
using System.Numerics;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Calculations;

/// <summary>A Python number: exact int or float (the formula language keeps the Python numeric tower).</summary>
public readonly record struct PyNum(BigInteger? Int, double Float)
{
    public static PyNum I(BigInteger v) => new(v, 0);
    public static PyNum F(double v) => new(null, v);
    public static PyNum B(bool v) => I(v ? 1 : 0);
    public bool IsInt => Int != null;
    public double AsDouble => Int is BigInteger i ? Engine.Core.Values.BigToDouble(i) : Float;
    public bool Truthy => Int is BigInteger i ? !i.IsZero : Float != 0;
}

/// <summary>
/// Controlled numeric expressions (formula_engine.Expression): no code is compiled or executed;
/// the text is parsed into a bounded tree of explicit operators, references and registered functions.
/// </summary>
public sealed class Expression
{
    public abstract record Node;
    public sealed record Constant(PyNum Value) : Node;
    public sealed record Reference(string Name) : Node;
    public sealed record Binary(string Op, Node Left, Node Right) : Node;
    public sealed record Unary(string Op, Node Operand) : Node;
    public sealed record Compare(string Op, Node Left, Node Right) : Node;
    public sealed record Function(string Name, IReadOnlyList<Node> Args) : Node;

    const string Allowed = "Sono consentiti solo numeri, riferimenti [variabile], + - * / **, confronti e funzioni autorizzate";
    const string Syntax = "Formula non valida: controllare operatori, parentesi e riferimenti [variabile]";
    public static readonly Dictionary<string, (int lo, int hi)> Functions = new()
    {
        ["abs"] = (1, 1), ["sqrt"] = (1, 1), ["min"] = (1, 16), ["max"] = (1, 16), ["mean"] = (1, 16), ["pow"] = (2, 2), ["ifelse"] = (3, 3),
    };
    static readonly Regex ReferencePattern = new(@"\[([^\[\]]+)\]", RegexOptions.Compiled);

    readonly Dictionary<string, string> references = new();
    int count;
    public Node Tree { get; }
    public List<string> UsedBindings { get; }

    public Expression(JsonNode? expression, JsonNode? bindings)
    {
        if (expression.Str() is not string text || text.Trim().Length == 0 || text.Length > 2000) throw new UserError("Inserire una formula di massimo 2000 caratteri");
        if (bindings is not JsonObject map || map.Count > 32) throw new UserError("Massimo 32 riferimenti per formula");
        var aliases = new Dictionary<string, string>();
        var converted = ReferencePattern.Replace(text, m =>
        {
            var name = m.Groups[1].Value.Trim();
            if (!map.ContainsKey(name)) throw new UserError($"Variabile [{name}] non associata: sceglierla dall’elenco");
            if (!aliases.TryGetValue(name, out var alias))
            {
                alias = "_ref_" + aliases.Count.ToString(CultureInfo.InvariantCulture);
                aliases[name] = alias;
                references[alias] = name;
            }
            return alias;
        });
        var ast = PyParser.Parse(converted.Trim());
        Tree = Build(ast, 0);
        UsedBindings = references.Values.Distinct().ToList();
    }

    Node Build(PyParser.Ast node, int depth)
    {
        count++;
        if (depth > 24 || count > 200) throw new UserError("Formula troppo complessa: massimo 200 operazioni e 24 livelli");
        switch (node)
        {
            case PyParser.Num n:
            {
                var x = n.Value.AsDouble;
                if (!double.IsFinite(x) || Math.Abs(x) > 1e100) throw new UserError("Costante numerica non finita o troppo grande");
                return new Constant(n.Value.IsInt ? n.Value : PyNum.F(x));
            }
            case PyParser.Name name when references.TryGetValue(name.Id, out var reference):
                return new Reference(reference);
            case PyParser.BinOp b when b.Op is "+" or "-" or "*" or "/" or "**":
                return new Binary(b.Op, Build(b.Left, depth + 1), Build(b.Right, depth + 1));
            case PyParser.UnaryOp u when u.Op is "+" or "-":
                return new Unary(u.Op, Build(u.Operand, depth + 1));
            case PyParser.CompareOp c when c.Ops.Count == 1:
                return new Compare(c.Ops[0], Build(c.Left, depth + 1), Build(c.Comparators[0], depth + 1));
            case PyParser.Call call when call.Func is PyParser.Name fn && Functions.TryGetValue(fn.Id, out var range) && !call.HasKeywords:
                if (call.Args.Count < range.lo || call.Args.Count > range.hi) throw new UserError($"{fn.Id}: richiesti da {range.lo} a {range.hi} argomenti");
                return new Function(fn.Id, call.Args.Select(a => Build(a, depth + 1)).ToList());
            default:
                throw new UserError(Allowed);
        }
    }

    public sealed class DomainError(string message) : Exception(message);
    public sealed class ZeroDivision() : Exception("division by zero");

    /// <summary>Evaluate with Python semantics; DomainError for non-finite results or invalid domains.</summary>
    public double Evaluate(IReadOnlyDictionary<string, double> values) => Visit(Tree, values).AsDouble is var v && double.IsFinite(v) ? v : throw new DomainError("Risultato non finito");

    static PyNum Checked(PyNum v)
    {
        if (!v.IsInt && !double.IsFinite(v.Float)) throw new DomainError("Risultato non finito o dominio matematico non valido");
        if (v.IsInt && !double.IsFinite(v.AsDouble)) throw new DomainError("Risultato non finito o dominio matematico non valido");
        return v;
    }

    PyNum Visit(Node n, IReadOnlyDictionary<string, double> values)
    {
        switch (n)
        {
            case Constant c: return c.Value;
            case Reference r: return PyNum.F(values[r.Name]);
            case Unary u:
            {
                var v = Visit(u.Operand, values);
                return u.Op == "-" ? v.IsInt ? PyNum.I(-v.Int!.Value) : PyNum.F(-v.Float) : v;
            }
            case Compare c:
            {
                var a = Visit(c.Left, values); var b = Visit(c.Right, values);
                var cmp = CompareNumbers(a, b);
                // Every ordered comparison with NaN is false; only != is true.
                if (cmp == 2) return PyNum.B(c.Op == "!=");
                return PyNum.B(c.Op switch { "<" => cmp < 0, "<=" => cmp <= 0, ">" => cmp > 0, ">=" => cmp >= 0, "==" => cmp == 0, _ => cmp != 0 });
            }
            case Binary b:
            {
                var a = Visit(b.Left, values); var y = Visit(b.Right, values);
                if (b.Op == "**" && Math.Abs(y.AsDouble) > 16) throw new DomainError("Esponente fuori limite (-16 … 16)");
                return Checked(Arith(b.Op, a, y));
            }
            case Function f:
            {
                if (f.Name == "ifelse") return Visit(Visit(f.Args[0], values).Truthy ? f.Args[1] : f.Args[2], values);
                var args = f.Args.Select(a => Visit(a, values)).ToList();
                if (f.Name == "pow" && Math.Abs(args[1].AsDouble) > 16) throw new DomainError("Esponente fuori limite (-16 … 16)");
                return Checked(f.Name switch
                {
                    "abs" => args[0].IsInt ? PyNum.I(BigInteger.Abs(args[0].Int!.Value)) : PyNum.F(Math.Abs(args[0].Float)),
                    "sqrt" => args[0].AsDouble < 0 ? throw new DomainError("math domain error") : PyNum.F(Math.Sqrt(args[0].AsDouble)),
                    "min" => args.Aggregate((m, x) => CompareNumbers(x, m) < 0 ? x : m),
                    "max" => args.Aggregate((m, x) => CompareNumbers(x, m) > 0 ? x : m),
                    "mean" => PyNum.F(FSum(args.Select(a => a.AsDouble)) / args.Count),
                    _ => Arith("**", args[0], args[1]),
                });
            }
        }
        throw new InvalidOperationException();
    }

    static int CompareNumbers(PyNum a, PyNum b)
    {
        if (a.IsInt && b.IsInt) return a.Int!.Value.CompareTo(b.Int!.Value);
        var x = a.AsDouble; var y = b.AsDouble;
        if (double.IsNaN(x) || double.IsNaN(y)) return 2;
        return x.CompareTo(y);
    }

    static PyNum Arith(string op, PyNum a, PyNum b)
    {
        if (a.IsInt && b.IsInt && op != "/")
        {
            var x = a.Int!.Value; var y = b.Int!.Value;
            switch (op)
            {
                case "+": return PyNum.I(x + y);
                case "-": return PyNum.I(x - y);
                case "*": return PyNum.I(x * y);
                case "**":
                    if (y >= 0) return PyNum.I(BigInteger.Pow(x, (int)y));
                    if (x.IsZero) throw new ZeroDivision();
                    return PyNum.F(Math.Pow(Engine.Core.Values.BigToDouble(x), (double)y));
            }
        }
        if (op == "/")
        {
            if (b.AsDouble == 0 && (b.IsInt || b.Float == 0)) throw new ZeroDivision();
            return PyNum.F(a.AsDouble / b.AsDouble);
        }
        var p = a.AsDouble; var q = b.AsDouble;
        switch (op)
        {
            case "+": return PyNum.F(p + q);
            case "-": return PyNum.F(p - q);
            case "*": return PyNum.F(p * q);
            default:
                if (p == 0 && q < 0) throw new ZeroDivision();
                // A negative base with a fractional exponent is complex in Python: not a finite real result.
                if (p < 0 && Math.Floor(q) != q) throw new DomainError("complex");
                var r = Math.Pow(p, q);
                if (double.IsInfinity(r) && double.IsFinite(p) && double.IsFinite(q)) throw new OverflowException("Numerical result out of range");
                return PyNum.F(r);
        }
    }

    /// <summary>math.fsum: exactly rounded sum of floats (Shewchuk partials).</summary>
    public static double FSum(IEnumerable<double> values)
    {
        var partials = new List<double>();
        foreach (var value in values)
        {
            var x = value;
            var i = 0;
            foreach (var p in partials.ToList())
            {
                var y = p;
                if (Math.Abs(x) < Math.Abs(y)) (x, y) = (y, x);
                var hi = x + y;
                var lo = y - (hi - x);
                if (lo != 0) partials[i++] = lo;
                x = hi;
            }
            partials.RemoveRange(i, partials.Count - i);
            partials.Add(x);
        }
        if (partials.Count == 0) return 0;
        var n = partials.Count - 1;
        var total = partials[n];
        double low = 0;
        while (n > 0)
        {
            var x = total; var y = partials[--n];
            total = x + y;
            var yr = total - x;
            low = y - yr;
            if (low != 0) break;
        }
        if (n > 0 && (low < 0 && partials[n - 1] < 0 || low > 0 && partials[n - 1] > 0))
        {
            var y = low * 2;
            var x = total + y;
            if (y == x - total) total = x;
        }
        return total;
    }

    public string InferUnit(IReadOnlyDictionary<string, string> units)
    {
        string Visit(Node n)
        {
            switch (n)
            {
                case Constant: return "";
                case Reference r: return units.GetValueOrDefault(r.Name, "");
                case Unary u: return Visit(u.Operand);
                case Compare: return "";
                case Function f:
                {
                    var u = f.Args.Select(Visit).ToList();
                    if (f.Name == "ifelse") return u[1] == u[2] ? u[1] : "";
                    if (f.Name == "sqrt") return u[0].Length > 0 ? "√(" + u[0] + ")" : "";
                    if (f.Name == "pow") return "";
                    return u.Distinct().Count() == 1 ? u[0] : "";
                }
                case Binary b:
                {
                    var a = Visit(b.Left); var c = Visit(b.Right);
                    return b.Op switch
                    {
                        "+" or "-" => a == c ? a : "",
                        "*" => a.Length == 0 || c.Length == 0 ? (a.Length > 0 ? a : c) : $"({a})·({c})",
                        "/" => a == c ? "" : c.Length == 0 ? a : $"({(a.Length > 0 ? a : "1")})/({c})",
                        _ => "",
                    };
                }
            }
            return "";
        }
        return Visit(Tree);
    }
}
