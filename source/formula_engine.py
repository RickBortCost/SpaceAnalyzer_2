"""Controlled numeric expressions and causal time alignment, shared by live/replay.

No Python expression is compiled or executed. AST is translated to a bounded
tree containing only explicit operators, references and registered functions.
"""
from __future__ import annotations
import ast
import math
import operator
import re
from datetime import datetime, timezone

MAX_FORMULAS = 64
MAX_INPUTS = 32
CALC_ID = re.compile(r'^calc:[0-9a-f]{32}$')
REFERENCE = re.compile(r'\[([^\[\]]+)\]')
BINARY = {ast.Add: ('+', operator.add), ast.Sub: ('-', operator.sub),
          ast.Mult: ('*', operator.mul), ast.Div: ('/', operator.truediv),
          ast.Pow: ('**', operator.pow)}
COMPARE = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
           ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}
FUNCTIONS = {'abs': (1, 1, abs), 'sqrt': (1, 1, math.sqrt),
             'min': (1, 16, lambda *a: min(a)), 'max': (1, 16, lambda *a: max(a)),
             'mean': (1, 16, lambda *a: math.fsum(a) / len(a)),
             'pow': (2, 2, operator.pow), 'ifelse': (3, 3, None)}

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None

def stamp(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).isoformat(timespec='microseconds')

class Expression:
    def __init__(self, expression, bindings):
        if not isinstance(expression, str) or not expression.strip() or len(expression) > 2000:
            raise ValueError('Inserire una formula di massimo 2000 caratteri')
        if not isinstance(bindings, dict) or len(bindings) > MAX_INPUTS:
            raise ValueError('Massimo 32 riferimenti per formula')
        self.references = {}
        aliases = {}
        def substitute(match):
            name = match.group(1).strip()
            if name not in bindings:
                raise ValueError(f'Variabile [{name}] non associata: sceglierla dall’elenco')
            if name not in aliases:
                alias = '_ref_' + str(len(aliases))
                aliases[name] = alias
                self.references[alias] = name
            return aliases[name]
        converted = REFERENCE.sub(substitute, expression)
        try:
            tree = ast.parse(converted.strip(), mode='eval')
        except (SyntaxError, RecursionError):
            raise ValueError('Formula non valida: controllare operatori, parentesi e riferimenti [variabile]') from None
        self.count = 0
        self.tree = self._parse(tree.body, 0)
        self.used_bindings = list(dict.fromkeys(self.references.values()))

    def _parse(self, node, depth):
        self.count += 1
        if depth > 24 or self.count > 200:
            raise ValueError('Formula troppo complessa: massimo 200 operazioni e 24 livelli')
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            x = number(node.value)
            if x is None or abs(x) > 1e100:
                raise ValueError('Costante numerica non finita o troppo grande')
            return ('constant', x)
        if isinstance(node, ast.Name) and node.id in self.references:
            return ('reference', self.references[node.id])
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            return ('binary', BINARY[type(node.op)][0], self._parse(node.left, depth+1), self._parse(node.right, depth+1))
        if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            return ('unary', '-' if isinstance(node.op, ast.USub) else '+', self._parse(node.operand, depth+1))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE:
            return ('compare', type(node.ops[0]).__name__, self._parse(node.left, depth+1), self._parse(node.comparators[0], depth+1))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and not node.keywords:
            lo, hi, _ = FUNCTIONS[node.func.id]
            if not lo <= len(node.args) <= hi:
                raise ValueError(f'{node.func.id}: richiesti da {lo} a {hi} argomenti')
            return ('function', node.func.id, tuple(self._parse(a, depth+1) for a in node.args))
        raise ValueError('Sono consentiti solo numeri, riferimenti [variabile], + - * / **, confronti e funzioni autorizzate')

    def evaluate(self, values):
        def visit(n):
            kind = n[0]
            if kind == 'constant': return n[1]
            if kind == 'reference': return values[n[1]]
            if kind == 'unary': return -visit(n[2]) if n[1] == '-' else visit(n[2])
            if kind in ('binary', 'compare'):
                a, b = visit(n[2]), visit(n[3])
                if kind == 'compare': return next(fn for key, fn in COMPARE.items() if key.__name__ == n[1])(a, b)
                if n[1] == '**' and abs(b) > 16: raise ValueError('Esponente fuori limite (-16 … 16)')
                fn = next(fn for symbol, fn in BINARY.values() if symbol == n[1])
                v = fn(a, b)
            else:
                if n[1] == 'ifelse': return visit(n[2][1] if visit(n[2][0]) else n[2][2])
                args = [visit(a) for a in n[2]]
                if n[1] == 'pow' and abs(args[1]) > 16: raise ValueError('Esponente fuori limite (-16 … 16)')
                v = FUNCTIONS[n[1]][2](*args)
            if number(v) is None: raise ValueError('Risultato non finito o dominio matematico non valido')
            return float(v)
        return float(visit(self.tree))

    def infer_unit(self, units):
        def visit(n):
            if n[0] == 'constant': return ''
            if n[0] == 'reference': return units.get(n[1], '')
            if n[0] == 'unary': return visit(n[2])
            if n[0] == 'compare': return ''
            if n[0] == 'function':
                u = [visit(a) for a in n[2]]
                if n[1] == 'ifelse': return u[1] if u[1] == u[2] else ''
                if n[1] == 'sqrt': return '√(' + u[0] + ')' if u[0] else ''
                if n[1] == 'pow': return ''
                return u[0] if len(set(u)) == 1 else ''
            a, b = visit(n[2]), visit(n[3])
            if n[1] in ('+', '-'): return a if a == b else ''
            if n[1] == '*': return a or b if not a or not b else f'({a})·({b})'
            if n[1] == '/': return '' if a == b else a if not b else f'({a or "1"})/({b})'
            return ''
        return visit(self.tree)

class Plan:
    def __init__(self, formulas, mode='live', ids=None, protocol=None, external_ids=()):
        if not isinstance(formulas, list) or len(formulas) > MAX_FORMULAS:
            raise ValueError('Massimo 64 formule')
        self.formulas = {f['nodeid']: f for f in formulas}
        if len(self.formulas) != len(formulas): raise ValueError('ID formula duplicato')
        self.expressions = {nid: Expression(f['expression'], f['bindings']) for nid, f in self.formulas.items()}
        self.external_ids = set(external_ids)
        self.order, visiting, visited = [], set(), set()
        def visit(nid):
            if nid in visiting: raise ValueError('Riferimento circolare nella formula ' + self.formulas[nid]['name'])
            if nid in visited: return
            if nid not in self.formulas: raise ValueError('Una variabile calcolata referenziata non esiste più')
            visiting.add(nid)
            for name in self.expressions[nid].used_bindings:
                source = self.formulas[nid]['bindings'][name]['nodeid']
                if source.startswith('calc:') and self.formulas[nid]['bindings'][name].get('kind') != 'stored' and source not in self.external_ids: visit(source)
            visiting.remove(nid); visited.add(nid); self.order.append(nid)
        for nid in self.formulas: visit(nid)
        self.mode = mode
        self.ids = list(ids) if ids is not None else [nid for nid, f in self.formulas.items() if f['active'] and f['modes'].get(mode) and (protocol is None or f.get('protocol') in (protocol, 'any'))]
        if any(nid not in self.formulas for nid in self.ids): raise ValueError('Formula non trovata')
        needed = set()
        def collect(nid):
            if nid in needed: return
            needed.add(nid)
            for name in self.expressions[nid].used_bindings:
                source = self.formulas[nid]['bindings'][name]['nodeid']
                if source.startswith('calc:') and self.formulas[nid]['bindings'][name].get('kind') != 'stored' and source not in self.external_ids: collect(source)
        for nid in self.ids: collect(nid)
        self.order = [nid for nid in self.order if nid in needed]
        self.raw_ids = set()
        for nid in self.order:
            for name in self.expressions[nid].used_bindings:
                source = self.formulas[nid]['bindings'][name]['nodeid']
                if not source.startswith('calc:') or source in self.external_ids or self.formulas[nid]['bindings'][name].get('kind') == 'stored': self.raw_ids.add(source)

    def raw_sources(self, nid):
        found, visited = {}, set()
        def visit(key):
            if key in visited: return
            visited.add(key)
            f = self.formulas[key]
            for name in self.expressions[key].used_bindings:
                source = f['bindings'][name]
                if source['nodeid'].startswith('calc:') and source.get('kind') != 'stored' and source['nodeid'] not in self.external_ids: visit(source['nodeid'])
                else: found[source['nodeid']] = source
        visit(nid)
        return list(found.values())

class Engine:
    """Evaluate only changed dependency paths. Past values expire; never use future values."""
    def __init__(self, plan):
        self.plan = plan; self.values = {}; self.last_time = None; self.late_samples = 0

    def process(self, t, rows):
        if self.last_time is not None and t < self.last_time:
            self.late_samples += len(rows); return []
        self.last_time = t; changed = set()
        for row in rows:
            nid = row['node_id']
            if nid not in self.plan.raw_ids: continue
            self.values[nid] = (t, number(row.get('value_numeric')), row.get('quality') or ('Good' if str(row.get('good', True)).lower() in ('true', '1') else 'Bad'))
            changed.add(nid)
        output = []
        for nid in self.plan.order:
            f = self.plan.formulas[nid]; expression = self.plan.expressions[nid]
            inputs = [(name, f['bindings'][name]['nodeid']) for name in expression.used_bindings]
            if inputs and not any(source in changed for _, source in inputs): continue
            clock = f.get('clock_nodeid')
            if clock and clock not in changed: continue
            value, quality, error = None, 'Good', ''
            if not f['active'] or not f['modes'].get(self.plan.mode):
                quality, error = 'Bad', 'Formula disattivata in questa modalità'
            else:
                args = {}
                for name, source in inputs:
                    item = self.values.get(source)
                    if item is None:
                        quality, error = 'Bad', f'Sorgente [{name}] non disponibile'; break
                    source_t, source_value, source_quality = item
                    age = t - source_t
                    if age < 0 or age > f['max_age_ms'] or (f['alignment'] == 'exact' and age != 0):
                        quality, error = 'Bad', f'Sorgente [{name}] fuori tolleranza temporale ({age:.3f} ms)'; break
                    if source_quality != 'Good' or source_value is None:
                        quality, error = source_quality if source_quality in ('Bad', 'Uncertain') else 'Bad', f'Sorgente [{name}] nulla o qualità {source_quality}'; break
                    args[name] = source_value
                if not error:
                    try: value = expression.evaluate(args)
                    except ZeroDivisionError: quality, error = 'Bad', 'Divisione per zero'
                    except (ValueError, OverflowError, TypeError): quality, error = 'Bad', 'Dominio matematico non valido o risultato non finito'
            self.values[nid] = (t, value, quality); changed.add(nid)
            if nid in self.plan.ids:
                output.append(dict(node_id=nid, signal_name=f['name'], timestamp_ms=t,
                    timestamp_utc=stamp(t), value_numeric=value, value_text=str(value) if value is not None else None,
                    data_type='Double', quality=quality, good=quality == 'Good',
                    status='CalculatedGood' if not error else error, status_code=None,
                    source_timestamp=stamp(t), server_timestamp=None,
                    local_timestamp=datetime.now(timezone.utc).isoformat(), ads_filetime=None))
        return output
