using System.Globalization;
using System.Numerics;
using System.Text;
using SpaceAnalyzer.App.Infrastructure;

namespace SpaceAnalyzer.App.Calculations;

/// <summary>
/// The Python expression grammar used by ast.parse(mode='eval'), enough to classify every input
/// exactly as the 2.x validator does: valid syntax yields a node (allowed or not), invalid syntax
/// raises the syntax message. Nothing is evaluated here.
/// </summary>
public static class PyParser
{
    public abstract record Ast;
    public sealed record Num(PyNum Value) : Ast;
    public sealed record Name(string Id) : Ast;
    public sealed record BinOp(string Op, Ast Left, Ast Right) : Ast;
    public sealed record UnaryOp(string Op, Ast Operand) : Ast;
    public sealed record CompareOp(Ast Left, List<string> Ops, List<Ast> Comparators) : Ast;
    public sealed record Call(Ast Func, List<Ast> Args, bool HasKeywords) : Ast;
    /// <summary>Valid Python that the formula language rejects (strings, lambda, boolean operators, ...).</summary>
    public sealed record Other(string Kind) : Ast;

    const string Syntax = "Formula non valida: controllare operatori, parentesi e riferimenti [variabile]";

    sealed record Token(string Kind, string Text, PyNum? Number = null, bool Imaginary = false);

    static readonly string[] Operators = ["**=", "//=", ">>=", "<<=", "!=", "==", "<=", ">=", "**", "//", "<<", ">>", "->", ":=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=",
        "+", "-", "*", "/", "%", "@", "<", ">", "(", ")", "[", "]", "{", "}", ",", ":", ".", ";", "=", "~", "&", "|", "^"];
    static readonly HashSet<string> Keywords = ["False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"];

    static UserError Error() => new(Syntax);

    static List<Token> Tokenize(string s)
    {
        var tokens = new List<Token>();
        var i = 0;
        var depth = 0;
        while (i < s.Length)
        {
            var c = s[i];
            if (c is ' ' or '\t' or '\f') { i++; continue; }
            if (c == '\\' && i + 1 < s.Length && s[i + 1] == '\n') { i += 2; continue; }
            if (c == '\\' && i + 2 < s.Length && s[i + 1] == '\r' && s[i + 2] == '\n') { i += 3; continue; }
            if (c is '\n' or '\r') { if (depth == 0) throw Error(); i++; continue; }
            if (c == '#') { while (i < s.Length && s[i] != '\n') i++; continue; }
            if (char.IsAsciiDigit(c) || c == '.' && i + 1 < s.Length && char.IsAsciiDigit(s[i + 1]))
            {
                var (num, end, imaginary) = ReadNumber(s, i);
                tokens.Add(new Token("num", s[i..end], num, imaginary));
                i = end;
                continue;
            }
            if (c == '_' || char.IsLetter(c))
            {
                var start = i;
                while (i < s.Length && (s[i] == '_' || char.IsLetterOrDigit(s[i]))) i++;
                var word = s[start..i];
                // String prefixes (r, b, f, u...) before a quote.
                if (i < s.Length && s[i] is '\'' or '"' && word.Length <= 2 && word.ToLowerInvariant().All(ch => "rbfu".Contains(ch)))
                {
                    i = ReadString(s, i);
                    tokens.Add(new Token("str", s[start..i]));
                    continue;
                }
                tokens.Add(new Token(Keywords.Contains(word) ? "kw" : "name", word));
                continue;
            }
            if (c is '\'' or '"')
            {
                var start = i;
                i = ReadString(s, i);
                tokens.Add(new Token("str", s[start..i]));
                continue;
            }
            var op = Operators.FirstOrDefault(o => string.CompareOrdinal(s, i, o, 0, o.Length) == 0) ?? throw Error();
            if (op is "(" or "[" or "{") { depth++; if (depth > 200) throw Error(); }
            if (op is ")" or "]" or "}") { depth--; if (depth < 0) throw Error(); }
            tokens.Add(new Token("op", op));
            i += op.Length;
        }
        if (depth != 0) throw Error();
        tokens.Add(new Token("end", ""));
        return tokens;
    }

    static int ReadString(string s, int i)
    {
        var quote = s[i];
        var triple = i + 2 < s.Length && s[i + 1] == quote && s[i + 2] == quote;
        i += triple ? 3 : 1;
        while (i < s.Length)
        {
            if (s[i] == '\\') { i += 2; continue; }
            if (triple ? i + 2 < s.Length + 0 && s.AsSpan(i).StartsWith(new string(quote, 3)) : s[i] == quote) return i + (triple ? 3 : 1);
            if (!triple && s[i] is '\n' or '\r') throw Error();
            i++;
        }
        throw Error();
    }

    /// <summary>Python numeric literals: decimal/hex/octal/binary ints with underscores, floats, imaginary (j).</summary>
    static (PyNum value, int end, bool imaginary) ReadNumber(string s, int i)
    {
        var start = i;
        PyNum value;
        if (s[i] == '0' && i + 1 < s.Length && "xXoObB".Contains(s[i + 1]))
        {
            var radix = char.ToLowerInvariant(s[i + 1]) switch { 'x' => 16, 'o' => 8, _ => 2 };
            i += 2;
            var digits = new StringBuilder();
            while (i < s.Length && (s[i] == '_' || Uri.IsHexDigit(s[i])))
            {
                if (s[i] == '_') { if (i + 1 >= s.Length || s[i + 1] == '_' || !Uri.IsHexDigit(s[i + 1])) throw Error(); }
                else digits.Append(s[i]);
                i++;
            }
            if (digits.Length == 0) throw Error();
            BigInteger v = 0;
            foreach (var ch in digits.ToString())
            {
                var d = Convert.ToInt32(ch.ToString(), 16);
                if (d >= radix) throw Error();
                v = v * radix + d;
            }
            value = PyNum.I(v);
        }
        else
        {
            string Digits()
            {
                var sb = new StringBuilder();
                while (i < s.Length && (char.IsAsciiDigit(s[i]) || s[i] == '_'))
                {
                    if (s[i] == '_') { if (sb.Length == 0 || i + 1 >= s.Length || !char.IsAsciiDigit(s[i + 1])) throw Error(); }
                    else sb.Append(s[i]);
                    i++;
                }
                return sb.ToString();
            }
            var integer = Digits();
            var isFloat = false;
            var fraction = "";
            if (i < s.Length && s[i] == '.')
            {
                isFloat = true; i++;
                if (i < s.Length && s[i] == '_') throw Error();
                fraction = Digits();
            }
            var exponent = "";
            if (i < s.Length && s[i] is 'e' or 'E')
            {
                var j = i + 1;
                var sign = "";
                if (j < s.Length && s[j] is '+' or '-') { sign = s[j].ToString(); j++; }
                if (j < s.Length && char.IsAsciiDigit(s[j]))
                {
                    i = j; isFloat = true;
                    exponent = "e" + sign + Digits();
                }
                else throw Error();
            }
            if (i < s.Length && s[i] is 'j' or 'J')
            {
                i++;
                if (i < s.Length && (char.IsLetterOrDigit(s[i]) || s[i] == '_')) throw Error();
                return (PyNum.F(0), i, true);
            }
            if (!isFloat)
            {
                // Leading zeros are not permitted in decimal integer literals (except 0 itself).
                if (integer.Length > 1 && integer[0] == '0' && integer.Any(ch => ch != '0')) throw Error();
                value = PyNum.I(BigInteger.Parse(integer, CultureInfo.InvariantCulture));
            }
            else value = PyNum.F(double.Parse((integer.Length > 0 ? integer : "0") + "." + (fraction.Length > 0 ? fraction : "0") + exponent, NumberStyles.Float, CultureInfo.InvariantCulture));
        }
        // A literal directly followed by a name character is invalid (e.g. 2_ref_0, 1if).
        if (i < s.Length && (char.IsLetter(s[i]) || s[i] == '_')) throw Error();
        return (value, i, false);
    }

    sealed class State(List<Token> tokens)
    {
        public int Pos;
        public Token Peek => tokens[Pos];
        public Token Next() => tokens[Pos++];
        public bool IsOp(string op) => Peek.Kind == "op" && Peek.Text == op;
        public bool IsKw(string kw) => Peek.Kind == "kw" && Peek.Text == kw;
        public void Expect(string op) { if (!IsOp(op)) throw Error(); Pos++; }
    }

    public static Ast Parse(string text)
    {
        var st = new State(Tokenize(text));
        if (st.Peek.Kind == "end") throw Error();
        var node = Expr(st, allowTuple: true);
        if (st.Peek.Kind != "end") throw Error();
        return node;
    }

    // expression: lambda | disjunction ['if' disjunction 'else' expression]
    static Ast Expr(State st, bool allowTuple = false)
    {
        var first = Single(st);
        if (allowTuple && st.IsOp(","))
        {
            while (st.IsOp(",")) { st.Next(); if (st.Peek.Kind == "end" || st.IsOp(")")) break; Single(st); }
            return new Other("Tuple");
        }
        return first;
    }

    static Ast Single(State st)
    {
        if (st.IsKw("lambda"))
        {
            st.Next();
            while (!st.IsOp(":")) { if (st.Peek.Kind == "end") throw Error(); st.Next(); }
            st.Next();
            Single(st);
            return new Other("Lambda");
        }
        var body = Disjunction(st);
        if (st.IsKw("if"))
        {
            st.Next();
            Disjunction(st);
            if (!st.IsKw("else")) throw Error();
            st.Next();
            Single(st);
            return new Other("IfExp");
        }
        if (st.IsOp(":="))
        {
            if (body is not Name) throw Error();
            st.Next(); Single(st);
            return new Other("NamedExpr");
        }
        return body;
    }

    static Ast Disjunction(State st)
    {
        var left = Conjunction(st);
        if (!st.IsKw("or")) return left;
        while (st.IsKw("or")) { st.Next(); Conjunction(st); }
        return new Other("BoolOp");
    }

    static Ast Conjunction(State st)
    {
        var left = Inversion(st);
        if (!st.IsKw("and")) return left;
        while (st.IsKw("and")) { st.Next(); Inversion(st); }
        return new Other("BoolOp");
    }

    static Ast Inversion(State st)
    {
        if (st.IsKw("not")) { st.Next(); Inversion(st); return new Other("UnaryOp(Not)"); }
        return Comparison(st);
    }

    static Ast Comparison(State st)
    {
        var left = BitOr(st);
        var ops = new List<string>();
        var rights = new List<Ast>();
        while (true)
        {
            string? op = null;
            if (st.Peek.Kind == "op" && st.Peek.Text is "<" or "<=" or ">" or ">=" or "==" or "!=") op = st.Next().Text;
            else if (st.IsKw("in")) { st.Next(); op = "in"; }
            else if (st.IsKw("not")) { var save = st.Pos; st.Next(); if (st.IsKw("in")) { st.Next(); op = "not in"; } else { st.Pos = save; break; } }
            else if (st.IsKw("is")) { st.Next(); op = "is"; if (st.IsKw("not")) { st.Next(); op = "is not"; } }
            if (op == null) break;
            ops.Add(op);
            rights.Add(BitOr(st));
        }
        if (ops.Count == 0) return left;
        if (ops.Any(o => o is "in" or "not in" or "is" or "is not")) return new Other("Compare");
        return new CompareOp(left, ops, rights);
    }

    static Ast BitOr(State st) => LeftAssoc(st, ["|"], s => LeftAssoc(s, ["^"], s2 => LeftAssoc(s2, ["&"], s3 => LeftAssoc(s3, ["<<", ">>"], Sum))));

    static Ast Sum(State st) => LeftAssoc(st, ["+", "-"], Term);

    static Ast Term(State st) => LeftAssoc(st, ["*", "/", "//", "%", "@"], Factor);

    static Ast LeftAssoc(State st, string[] ops, Func<State, Ast> next)
    {
        var left = next(st);
        while (st.Peek.Kind == "op" && ops.Contains(st.Peek.Text))
        {
            var op = st.Next().Text;
            left = new BinOp(op, left, next(st));
        }
        return left;
    }

    static Ast Factor(State st)
    {
        if (st.Peek.Kind == "op" && st.Peek.Text is "+" or "-" or "~")
        {
            var op = st.Next().Text;
            return new UnaryOp(op, Factor(st));
        }
        return Power(st);
    }

    static Ast Power(State st)
    {
        if (st.IsKw("await")) { st.Next(); Primary(st); return new Other("Await"); }
        var left = Primary(st);
        if (st.IsOp("**")) { st.Next(); return new BinOp("**", left, Factor(st)); }
        return left;
    }

    static Ast Primary(State st)
    {
        var node = Atom(st);
        while (true)
        {
            if (st.IsOp("("))
            {
                st.Next();
                var args = new List<Ast>();
                var keywords = false;
                while (!st.IsOp(")"))
                {
                    if (st.IsOp("*") || st.IsOp("**")) { st.Next(); Single(st); keywords = true; }
                    else if (st.Peek.Kind == "name" && Lookahead(st, "="))
                    {
                        st.Next(); st.Next(); Single(st); keywords = true;
                    }
                    else
                    {
                        var arg = Single(st);
                        if (st.IsKw("for")) { Comprehension(st); arg = new Other("GeneratorExp"); }
                        args.Add(arg);
                    }
                    if (st.IsOp(",")) { st.Next(); continue; }
                    if (!st.IsOp(")")) throw Error();
                }
                st.Next();
                node = new Call(node, args, keywords);
            }
            else if (st.IsOp("["))
            {
                st.Next();
                if (st.IsOp("]")) throw Error();
                SliceList(st);
                st.Expect("]");
                node = new Other("Subscript");
            }
            else if (st.IsOp("."))
            {
                st.Next();
                if (st.Peek.Kind != "name" && st.Peek.Kind != "kw") throw Error();
                if (st.Peek.Kind == "kw") throw Error();
                st.Next();
                node = new Other("Attribute");
            }
            else return node;
        }
    }

    static bool Lookahead(State st, string op)
    {
        var save = st.Pos;
        st.Next();
        var result = st.IsOp(op);
        st.Pos = save;
        return result;
    }

    static void SliceList(State st)
    {
        while (true)
        {
            if (!st.IsOp(":")) Single(st);
            if (st.IsOp(":"))
            {
                st.Next();
                if (!st.IsOp("]") && !st.IsOp(",") && !st.IsOp(":")) Single(st);
                if (st.IsOp(":")) { st.Next(); if (!st.IsOp("]") && !st.IsOp(",")) Single(st); }
            }
            if (st.IsOp(",")) { st.Next(); if (st.IsOp("]")) return; continue; }
            return;
        }
    }

    static void Comprehension(State st)
    {
        while (st.IsKw("for") || st.IsKw("async"))
        {
            if (st.IsKw("async")) st.Next();
            st.Next();
            BitOr(st);
            while (st.IsOp(",")) { st.Next(); BitOr(st); }
            if (!st.IsKw("in")) throw Error();
            st.Next();
            Disjunction(st);
            while (st.IsKw("if")) { st.Next(); Disjunction(st); }
        }
    }

    static Ast Atom(State st)
    {
        var t = st.Peek;
        switch (t.Kind)
        {
            case "num":
                st.Next();
                return t.Imaginary ? new Other("Constant(complex)") : new Num(t.Number!.Value);
            case "name":
                st.Next();
                return new Name(t.Text);
            case "str":
                st.Next();
                while (st.Peek.Kind == "str") st.Next();
                return new Other("Constant(str)");
            case "kw" when t.Text is "True" or "False" or "None":
                st.Next();
                return new Other("Constant(" + t.Text + ")");
            case "op" when t.Text == "(":
            {
                st.Next();
                if (st.IsOp(")")) { st.Next(); return new Other("Tuple"); }
                var inner = st.IsKw("yield") ? throw Error() : Single(st);
                if (st.IsKw("for")) { Comprehension(st); st.Expect(")"); return new Other("GeneratorExp"); }
                if (st.IsOp(","))
                {
                    while (st.IsOp(",")) { st.Next(); if (st.IsOp(")")) break; Single(st); }
                    st.Expect(")");
                    return new Other("Tuple");
                }
                st.Expect(")");
                return inner;
            }
            case "op" when t.Text == "[":
            {
                st.Next();
                if (!st.IsOp("]"))
                {
                    Single(st);
                    if (st.IsKw("for")) Comprehension(st);
                    else while (st.IsOp(",")) { st.Next(); if (st.IsOp("]")) break; Single(st); }
                }
                st.Expect("]");
                return new Other("List");
            }
            case "op" when t.Text == "{":
            {
                st.Next();
                if (!st.IsOp("}"))
                {
                    Single(st);
                    if (st.IsOp(":")) { st.Next(); Single(st); }
                    if (st.IsKw("for")) Comprehension(st);
                    else while (st.IsOp(",")) { st.Next(); if (st.IsOp("}")) break; Single(st); if (st.IsOp(":")) { st.Next(); Single(st); } }
                }
                st.Expect("}");
                return new Other("Dict");
            }
        }
        throw Error();
    }
}
