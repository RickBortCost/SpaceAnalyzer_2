"""Versioned, atomic formula registry. Mutable state is protected by one RLock."""
import copy
import json
import logging
import math
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from formula_engine import Expression, Plan, CALC_ID, MAX_FORMULAS
from s7_client import protocol_of

def now(): return datetime.now(timezone.utc).isoformat()

def normalize(data, previous=None):
    if not isinstance(data, dict): raise ValueError('Definizione formula non valida')
    previous = previous or {}
    ident = previous.get('id') or data.get('id') or uuid.uuid4().hex
    nid = 'calc:' + ident
    if not CALC_ID.fullmatch(nid): raise ValueError('ID formula non valido')
    name = data.get('name', '').strip() if isinstance(data.get('name'), str) else ''
    if not name or len(name) > 120: raise ValueError('Nome formula: da 1 a 120 caratteri')
    unit = data.get('unit', '')
    if not isinstance(unit, str) or len(unit) > 40: raise ValueError('Unità: massimo 40 caratteri')
    active = data.get('active', True); modes = data.get('modes', {'live': True, 'recorded': True})
    if type(active) is not bool or not isinstance(modes, dict) or any(type(modes.get(k)) is not bool for k in ('live', 'recorded')):
        raise ValueError('Specificare modalità Live/Recorded e stato attivo')
    if not any(modes.values()): raise ValueError('Scegliere almeno una modalità: Live o Recorded')
    alignment = data.get('alignment', 'hold')
    if alignment not in ('hold', 'exact'): raise ValueError('Allineamento temporale non valido')
    try:
        age = float(data.get('max_age_ms', 1000))
        if not math.isfinite(age) or not 0 <= age <= 3600000: raise ValueError()
    except (ValueError, TypeError): raise ValueError('Tolleranza temporale: da 0 a 3600000 ms') from None
    bindings = data.get('bindings', {})
    expression = Expression(data.get('expression'), bindings)
    clean = {}
    for alias in expression.used_bindings:
        source = bindings[alias]
        if not isinstance(source, dict) or not isinstance(source.get('nodeid'), str) or not 1 <= len(source['nodeid']) <= 2048:
            raise ValueError(f'Sorgente [{alias}] non valida')
        if source['nodeid'].startswith('calc:') and not CALC_ID.fullmatch(source['nodeid']): raise ValueError('Riferimento calcolato non valido')
        kind = source.get('kind', 'formula' if source['nodeid'].startswith('calc:') else 'signal')
        if kind not in ('signal', 'formula', 'stored'): raise ValueError('Tipo riferimento non valido')
        if kind == 'stored' and modes['live']: raise ValueError('Una serie salvata senza formula attiva può essere usata solo in Recorded')
        clean[alias] = {'nodeid': source['nodeid'], 'name': str(source.get('name') or alias)[:120], 'unit': str(source.get('unit') or '')[:40], 'kind': kind}
    clock = data.get('clock_nodeid') or None
    if clock is not None and clock not in {s['nodeid'] for s in clean.values()}:
        raise ValueError('Il clock deve essere una sorgente della formula')
    return {'id': ident, 'nodeid': nid, 'name': name, 'expression': data['expression'].strip(),
        'bindings': clean, 'unit': unit.strip(), 'modes': {k: modes[k] for k in ('live', 'recorded')},
        'active': active, 'alignment': alignment, 'max_age_ms': age, 'clock_nodeid': clock,
        'created_at': previous.get('created_at') or now(), 'updated_at': now(),
        'revision': int(previous.get('revision', 0)) + 1}

class FormulaStore:
    def __init__(self, path):
        self.path = Path(path); self.lock = threading.RLock(); self.formulas = []; self.revision = 0; self.error = ''

    @staticmethod
    def check(formulas):
        if len({f['name'].casefold() for f in formulas}) != len(formulas): raise ValueError('Esiste già una formula con questo nome')
        plan = Plan(formulas)
        for f in formulas:
            sources = plan.raw_sources(f['nodeid'])
            kinds = {'recorded' if s.get('kind') == 'stored' else protocol_of(s['nodeid']) for s in sources}
            f['protocol'] = next(iter(kinds)) if len(kinds) == 1 else 'any' if not kinds or f['modes']['live'] else 'recorded'
            f['required_sources'] = sources
            f['dependencies'] = [s['nodeid'] for s in f['bindings'].values() if s['nodeid'].startswith('calc:') and s.get('kind') != 'stored']
            units = {alias: (plan.formulas[s['nodeid']]['unit'] if s['nodeid'].startswith('calc:') and s.get('kind') != 'stored' else s['unit']) for alias, s in f['bindings'].items()}
            f['unit_hint'] = plan.expressions[f['nodeid']].infer_unit(units)
        return formulas

    def load(self, backup=None):
        with self.lock:
            try:
                payload = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else backup
                if not payload: self.formulas = []; self.revision = 0; return
                normalized = []
                for f in payload['formulas']:
                    row = normalize(f, f); row['revision'] = int(f.get('revision', 1)); row['updated_at'] = f.get('updated_at', row['updated_at']); normalized.append(row)
                self.formulas = self.check(normalized); self.revision = int(payload.get('revision', 1)); self.error = ''
                if not self.path.exists(): self._write()
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.formulas = []; self.error = 'Formule non caricate: ' + str(exc)
                logging.error(self.error)

    def snapshot(self):
        with self.lock: return {'schema': 1, 'revision': self.revision, 'formulas': copy.deepcopy(self.formulas), 'error': self.error}

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(self.snapshot(), indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            os.replace(temporary, self.path)
        finally: temporary.unlink(missing_ok=True)

    def candidate(self, data):
        with self.lock:
            if not isinstance(data, dict): raise ValueError('Definizione formula non valida')
            old = next((f for f in self.formulas if f['id'] == data.get('id')), None)
            if data.get('id') and old is None: raise ValueError('Formula da modificare non trovata')
            if old is not None and data.get('expected_revision') is not None and data['expected_revision'] != old['revision']:
                raise ValueError('Formula modificata da un’altra finestra: ricaricare prima di salvare')
            f = normalize(data, old)
            formulas = [copy.deepcopy(x) for x in self.formulas if x['id'] != f['id']] + [f]
            self.check(formulas)
            return f, formulas

    def save(self, data):
        with self.lock:
            f, new = self.candidate(data); old, rev = self.formulas, self.revision
            self.formulas = new; self.revision += 1; self.error = ''
            try: self._write()
            except Exception: self.formulas, self.revision = old, rev; raise
            return copy.deepcopy(f)

    def delete(self, ident):
        with self.lock:
            f = next((f for f in self.formulas if f['id'] == ident), None)
            if f is None: raise ValueError('Formula non trovata')
            users = [g['name'] for g in self.formulas if f['nodeid'] in g['dependencies']]
            if users: raise ValueError('Prima modificare o eliminare le formule dipendenti: ' + ', '.join(users))
            old, rev = self.formulas, self.revision
            self.formulas = [g for g in self.formulas if g['id'] != ident]; self.revision += 1
            try: self._write()
            except Exception: self.formulas, self.revision = old, rev; raise

    def duplicate(self, ident):
        with self.lock:
            original = next((f for f in self.formulas if f['id'] == ident), None)
            if original is None: raise ValueError('Formula non trovata')
            data = copy.deepcopy(original); data.pop('id'); data['active'] = False
            i = 1
            names = {f['name'].casefold() for f in self.formulas}
            while True:
                data['name'] = original['name'][:100] + (' (copia)' if i == 1 else f' (copia {i})')
                if data['name'].casefold() not in names: break
                i += 1
            return self.save(data)
