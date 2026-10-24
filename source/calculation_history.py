"""Original-row calculation, before display decimation; materialized files are standard recordings."""
import itertools
import math
from pathlib import Path
from formula_engine import Plan, Engine, number
from recording_reader import RecordingLibrary
from analysis_tools import export_rows

MAX_INPUT_ROWS = 2000000
MAX_OUTPUT_ROWS = 2000000

def calculate_recording(job):
    path = Path(job['path'])
    library = RecordingLibrary(path.parent)
    original_meta = library._metadata(path)
    wanted = list(dict.fromkeys(job['signal_ids']))
    materialized = set(job.get('materialized_ids', ()))
    calculated = [n for n in wanted if n.startswith('calc:') and n not in materialized]
    plan = Plan(job['formulas'], 'recorded', calculated, external_ids=materialized)
    raw_wanted = set(wanted) - set(calculated)
    raw_ids = raw_wanted | plan.raw_ids
    if plan.order and not plan.raw_ids and materialized:
        # Constant expressions use one physical signal as the recording timeline.
        raw_ids.add(sorted(materialized)[0])
    canonical = {n: n for n in raw_ids}
    start, end = job.get('start_ms'), job.get('end_ms')
    # Warm up an entire dependency chain before a selected historical interval.
    ages = {}
    for n in plan.order:
        ages[n] = plan.formulas[n]['max_age_ms'] + max((ages.get(s['nodeid'], 0) for s in plan.formulas[n]['bindings'].values()), default=0)
    age = max(ages.values(), default=0)
    inputs = []
    names = {n: n for n in raw_wanted}
    for row in library._iter_rows(path):
        nid = row.get('node_id')
        if nid not in raw_ids: continue
        t = number(row.get('timestamp_ms'))
        if t is None or (end is not None and t > end) or (start is not None and t < start-age): continue
        quality = row.get('quality') or ('Good' if str(row.get('good', True)).lower() in ('true', '1') else 'Uncertain' if str(row.get('status', '')).startswith('Uncertain') else 'Bad')
        quality = 'Good' if quality == 'Good' else 'Uncertain' if quality == 'Uncertain' else 'Bad'
        inputs.append((t, canonical[nid], number(row.get('value_numeric')), quality))
        if nid in raw_wanted: names[nid] = str(row.get('signal_name') or nid)
        if len(inputs) > MAX_INPUT_ROWS: raise ValueError('Calcolo storico: massimo 2 milioni di campioni sorgente; ridurre l’intervallo o i segnali')
    inputs.sort(key=lambda p: p[0])
    engine = Engine(plan)
    counts = {'Good': 0, 'Uncertain': 0, 'Bad': 0}
    bounds = [math.inf, -math.inf]
    output_count = [0]
    def rows():
        for row in library._iter_rows(path):
            t = number(row.get('timestamp_ms'))
            if row.get('node_id') not in raw_wanted or t is None: continue
            if (start is not None and t < start) or (end is not None and t > end): continue
            output_count[0] += 1
            if output_count[0] > MAX_OUTPUT_ROWS: raise ValueError('Massimo 2 milioni di righe risultato; ridurre l’intervallo')
            bounds[0] = min(bounds[0], t); bounds[1] = max(bounds[1], t)
            quality = row.get('quality') or ('Good' if str(row.get('good', True)).lower() in ('true', '1') else 'Uncertain' if str(row.get('status', '')).startswith('Uncertain') else 'Bad')
            counts[quality if quality in counts else 'Bad'] += 1
            yield row
        for t, group in itertools.groupby(inputs, key=lambda p: p[0]):
            current = [dict(node_id=p[1], value_numeric=p[2], quality=p[3]) for p in group]
            for row in engine.process(t, current):
                if start is not None and t < start: continue
                row['sample_ms'] = job.get('sample_ms') or 0
                counts[row['quality']] += 1; output_count[0] += 1
                if output_count[0] > MAX_OUTPUT_ROWS: raise ValueError('Massimo 2 milioni di righe risultato; ridurre l’intervallo o le formule')
                bounds[0] = min(bounds[0], t); bounds[1] = max(bounds[1], t)
                yield row
    count = export_rows(rows(), job['output'], job.get('format', 'parquet'))
    original_signals = {s['nodeid']: s for s in original_meta.get('signals', [])}
    signals = [{'nodeid': n, 'display_name': names.get(n, n), 'unit': original_signals.get(n, {}).get('unit', ''),
        'calculated': n.startswith('calc:')} for n in wanted if n in raw_wanted]
    signals += [{'nodeid': n, 'display_name': plan.formulas[n]['name'], 'unit': plan.formulas[n]['unit'] or plan.formulas[n]['unit_hint'], 'calculated': True} for n in calculated]
    versions = {f['nodeid']: f for f in original_meta.get('calculation_formulas', []) if f['nodeid'] in raw_ids}
    for n in plan.order:
        if n not in materialized: versions[n] = plan.formulas[n]
    return {'signals': signals, 'rows_written': count, 'start_ms': bounds[0], 'end_ms': bounds[1],
        'duration_seconds': (bounds[1]-bounds[0])/1000, 'quality_counts': counts, 'calculated_ids': calculated,
        'calculation_formulas': list(versions.values()), 'original': path.name, 'calculation_mode': 'materialized',
        'description': 'Serie calcolate dai campioni originali; allineamento causale senza interpolazione',
        'session_name': 'Dati calcolati', 'initial_sample_ms': job.get('sample_ms')}
