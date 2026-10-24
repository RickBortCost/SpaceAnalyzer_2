"""Build a durable, lossless comparison on a common time axis in a separate process.

Inputs stay untouched. Each file has a distinct signal namespace, so two PLCs or
two overlapping recordings with identical node IDs cannot overwrite one another.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from datetime import datetime, timezone

from recording_reader import RecordingLibrary, _finite_float, _truth

MAX_FILES = 32
MAX_SIGNALS = 2048


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def fingerprint(path):
    """Also detect changed sidecars between the discovery and write passes."""
    result = []
    for candidate in (path, path.with_suffix('.metadata.json'), path.with_suffix('.json')):
        if candidate.is_file():
            stat = candidate.stat()
            result.append([str(candidate), stat.st_size, stat.st_mtime_ns])
    return result


def scan_recording(library, path, progress=None, allow_empty=False, allow_unordered=False):
    metadata = library._metadata(path)
    # MQTT source clocks can arrive late. Preserve their archive order, then
    # let the historical viewer sort points without changing the input file.
    allow_unordered=allow_unordered or metadata.get('source_protocol') in ('mqtt','mixed') or metadata.get('timestamp_order')=='arrival'
    by_id = {str(s['nodeid']): s for s in metadata.get('signals', []) if isinstance(s, dict) and s.get('nodeid')}
    signals = {}
    start, end, count = math.inf, -math.inf, 0
    for row in library._iter_rows(path):
        nid = str(row.get('node_id') or '')
        t = _finite_float(row.get('timestamp_ms'))
        if not nid or t is None:
            raise ValueError('Timestamp o identificativo segnale mancante/non valido')
        source = by_id.get(nid, {})
        info = signals.setdefault(nid, {'nodeid': nid, 'display_name': str(source.get('display_name') or row.get('signal_name') or nid),
            'unit': str(source.get('unit') or ''), 'data_type': str(row.get('data_type') or 'Double'),
            'calculated': bool(source.get('calculated') or nid.startswith('calc:')), 'rows': 0,
            'start_ms': t, 'end_ms': t, 'sample_ms': None, 'last_ms': None, 'time_gaps': 0,'out_of_order_rows':0})
        previous = info['last_ms']
        if previous is not None and t < previous:
            if not allow_unordered:raise ValueError('Timestamp non ordinati per il segnale ' + info['display_name'])
            info['out_of_order_rows']+=1
        sm = _finite_float(row.get('sample_ms'))
        if sm is not None and sm > 0:
            info['sample_ms'] = sm if info['sample_ms'] is None else min(sm, info['sample_ms'])
        if previous is not None and info['sample_ms'] and t - previous > info['sample_ms'] * 1.5:
            info['time_gaps'] += 1
        info.update(last_ms=t, end_ms=max(info['end_ms'], t), start_ms=min(info['start_ms'], t), rows=info['rows'] + 1)
        start, end, count = min(start, t), max(end, t), count + 1
        if len(signals) > MAX_SIGNALS:
            raise ValueError('Troppi segnali nel file (massimo 2048)')
        if progress and count % 65536 == 0:
            progress(count)
    if not count and allow_empty:
        return {'signals': [], 'start_ms': None, 'end_ms': None, 'rows_written': 0, 'duration_seconds': 0}
    if not count:
        raise ValueError('Nessun campione leggibile nella registrazione')
    for signal in signals.values():
        signal.pop('last_ms', None)
    return {'signals': list(signals.values()), 'start_ms': start, 'end_ms': end, 'rows_written': count,
        'duration_seconds': (end - start) / 1000}


def build_comparison(job, progress=None):
    """Two streaming passes; bounded RAM, original sample rates, no interpolation."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from recordings import COLUMNS
    from recording_storage import storage_profile, parquet_options, parquet_metadata

    items = job['files']
    mode, coverage = job.get('mode', 'absolute'), job.get('coverage', 'union')
    if mode not in ('absolute', 'starts') or coverage not in ('union', 'intersection'):
        raise ValueError('Modalità di sincronizzazione non valida')
    if not isinstance(items, list) or not 2 <= len(items) <= MAX_FILES:
        raise ValueError('Selezionare da 2 a 32 file')
    output = Path(job['output'])
    partial = output.with_suffix('.parquet.part')
    scans, snapshots, seen = [], [], set()

    def update(**state):
        if progress:
            progress(state)

    try:
        for index, item in enumerate(items):
            path = Path(item['path']).resolve()
            if str(path) in seen:
                raise ValueError('La stessa registrazione è stata selezionata più volte')
            seen.add(str(path))
            lib = RecordingLibrary(path.parent)
            lib._path(path.name)
            if lib._metadata(path).get('state') in ('recording', 'stopping'):
                raise ValueError('Attendere Stop per confrontare una registrazione ancora in corso')
            snapshots.append(fingerprint(path))
            update(phase='scan', file_index=index + 1, file_count=len(items), file_name=item['display_name'], rows_written=0)
            try:
                scan = scan_recording(lib, path, lambda n: update(phase='scan', file_index=index + 1,
                    file_count=len(items), file_name=item['display_name'], rows_scanned=n))
            except (ValueError, OSError) as exc:
                raise ValueError(item['display_name'] + ': ' + str(exc)) from exc
            offset = _finite_float(item.get('offset_ms', 0))
            if offset is None or abs(offset) > 315576000000:
                raise ValueError('Correzione orario non valida (massimo 10 anni)')
            if mode == 'starts':
                offset += scans[0]['start_ms'] - scan['start_ms'] if scans else 0
            scan.update(offset_ms=offset, path=str(path), display_name=item['display_name'], source=item.get('source', 'repository'),
                original_name=item.get('name', path.name), format=item.get('format', path.suffix[1:]))
            scans.append(scan)
        if sum(len(s['signals']) for s in scans) > MAX_SIGNALS:
            raise ValueError('Confronto: massimo 2048 segnali complessivi; selezionare meno file')
        starts = [s['start_ms'] + s['offset_ms'] for s in scans]
        ends = [s['end_ms'] + s['offset_ms'] for s in scans]
        start = max(starts) if coverage == 'intersection' else min(starts)
        end = min(ends) if coverage == 'intersection' else max(ends)
        if start > end:
            raise ValueError('I file non hanno un intervallo temporale comune. Scegliere “Intera estensione” oppure correggere gli orari.')
        columns = COLUMNS + ['original_timestamp_ms', 'original_node_id', 'source_file']
        floats = {'timestamp_ms', 'elapsed_ms', 'value_numeric', 'original_timestamp_ms','acquisition_timestamp_ms'}
        integers = {'cycle', 'sample_ms', 'status_code'}
        schema = pa.schema([(c, pa.float64() if c in floats else pa.int64() if c in integers else pa.bool_() if c == 'good' else pa.string()) for c in columns])
        storage = storage_profile('parquet')
        schema = schema.with_metadata(parquet_metadata(storage))
        batch, signals, total = [], [], 0
        update(phase='write', file_index=1, file_count=len(items), rows_written=0, total_rows=sum(s['rows_written'] for s in scans))
        with pq.ParquetWriter(str(partial), schema, **parquet_options(schema, storage)) as writer:
            for index, scan in enumerate(scans):
                path = Path(scan['path'])
                if fingerprint(path) != snapshots[index]:
                    raise ValueError(scan['display_name'] + ': il file è cambiato durante la preparazione; ripetere il confronto')
                namespace = hashlib.sha256((str(index) + '\0' + str(path)).encode()).hexdigest()[:12]
                ids, labels = {}, {}
                for original in scan['signals']:
                    nid = 'cmp:' + namespace + ':' + original['nodeid']
                    ids[original['nodeid']] = nid
                    labels[original['nodeid']] = original['display_name'] + ' · ' + scan['display_name']
                    signals.append({**original, 'nodeid': nid,
                        'display_name': original['display_name'] + ' · ' + scan['display_name'],
                        'original_node_id': original['nodeid'], 'source_file': scan['display_name'],
                        'time_offset_ms': scan['offset_ms'], 'start_ms': original['start_ms'] + scan['offset_ms'],
                        'end_ms': original['end_ms'] + scan['offset_ms']})
                for row in RecordingLibrary(path.parent)._iter_rows(path):
                    original_time = _finite_float(row.get('timestamp_ms'))
                    nid = str(row.get('node_id') or '')
                    if original_time is None or nid not in ids:
                        raise ValueError(scan['display_name'] + ': dati cambiati o timestamp non valido')
                    t = original_time + scan['offset_ms']
                    if not start <= t <= end:
                        continue
                    clean = {}
                    for c in COLUMNS:
                        value = row.get(c)
                        if value is None or value == '': clean[c] = None
                        elif c == 'good': clean[c] = _truth(value)
                        elif c in integers: clean[c] = int(value)
                        elif c in floats: clean[c] = _finite_float(value)
                        else: clean[c] = value.isoformat() if isinstance(value, datetime) else str(value)
                    quality = str(row.get('quality') or ('Good' if _truth(row.get('good', True)) else 'Bad'))
                    clean.update(timestamp_ms=t, timestamp_utc=datetime.fromtimestamp(t / 1000, timezone.utc).isoformat(),
                        elapsed_ms=t - start, node_id=ids[nid], signal_name=labels[nid], quality=quality,
                        good=quality == 'Good',
                        original_timestamp_ms=original_time, original_node_id=nid, source_file=scan['display_name'])
                    batch.append(clean)
                    total += 1
                    if len(batch) >= 32768:
                        writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                        batch.clear()
                        update(phase='write', file_index=index + 1, file_count=len(items), file_name=scan['display_name'], rows_written=total,
                            total_rows=sum(s['rows_written'] for s in scans))
                if fingerprint(path) != snapshots[index]:
                    raise ValueError(scan['display_name'] + ': il file è cambiato durante la lettura; confronto annullato')
            if batch:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        if not total:
            raise ValueError('Nessun campione nell’intervallo selezionato')
        metadata = {'display_name': job.get('display_name') or 'Confronto · ' + str(len(scans)) + ' file',
            'timestamp_order':'arrival' if any(s.get('out_of_order_rows') for scan in scans for s in scan['signals']) else 'per-signal',
            'signals': signals, 'rows_written': total, 'started_at': datetime.fromtimestamp(start / 1000, timezone.utc).isoformat(),
            'ended_at': datetime.fromtimestamp(end / 1000, timezone.utc).isoformat(), 'duration_seconds': (end - start) / 1000,
            'storage': {**storage, 'label': 'Confronto · ' + ' + '.join(dict.fromkeys(s['format'].upper() for s in scans))},
            'state': 'stopped', 'comparison': {'schema': 1, 'mode': mode, 'coverage': coverage,
                'files': [{k: v for k, v in s.items() if k != 'signals' and k != 'path'} for s in scans],
                'resampled': False, 'time_basis': 'timestamp_ms originale + correzione esplicita',
                'common_seconds': max(0, (min(ends) - max(starts)) / 1000)},
            'view_source_note': 'Segnali distinti per file; campionamenti originali. ' +
                ('Inizi allineati al primo file: orari traslati per il confronto.' if mode == 'starts' else 'Orari assoluti, con le eventuali correzioni indicate.')}
        atomic_json(output.with_suffix('.metadata.json'), metadata)
        os.replace(partial, output)
        return RecordingLibrary(output.parent).describe(output.name, source='local')
    except BaseException:
        partial.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        output.with_suffix('.metadata.json').unlink(missing_ok=True)
        raise


class ComparisonService:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.jobs = {}
        self.process = None
        self.closed = False

    def start(self, data, resolver):
        items = data.get('files')
        if not isinstance(items, list) or not 2 <= len(items) <= MAX_FILES:
            raise ValueError('Selezionare da 2 a 32 file per il confronto')
        mode, coverage = data.get('mode', 'absolute'), data.get('coverage', 'union')
        if mode not in ('absolute', 'starts') or coverage not in ('union', 'intersection'):
            raise ValueError('Modalità di sincronizzazione non valida')
        files, seen = [], set()
        for item in items:
            if not isinstance(item, dict): raise ValueError('Selezione file non valida')
            source = item.get('source', 'repository')
            library = resolver(source)
            path = library._path(item.get('name'))
            if path in seen: raise ValueError('File duplicato nella selezione')
            seen.add(path)
            offset = _finite_float(item.get('offset_ms', 0))
            if offset is None or abs(offset) > 315576000000: raise ValueError('Correzione orario non valida')
            meta = library._metadata(path)
            if meta.get('state') in ('recording', 'stopping'): raise ValueError('Attendere Stop prima di confrontare il file')
            files.append({'path': str(path), 'name': item['name'], 'source': source, 'offset_ms': offset,
                'format': path.suffix[1:], 'display_name': str(meta.get('display_name') or path.name)})
        with self.lock:
            if self.closed: raise ValueError('Servizio confronti arrestato')
            if any(j['state'] == 'running' for j in self.jobs.values()):
                raise ValueError('Un confronto è già in preparazione; attendere il completamento')
            self.directory.mkdir(parents=True, exist_ok=True)
            work = self.directory / '.comparisons'
            work.mkdir(exist_ok=True)
            ident = secrets.token_hex(12)
            output = self.directory / ('Confronto_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + ident[:8] + '.parquet')
            job_path = work / (ident + '.job.json')
            progress_path = work / (ident + '.status.json')
            atomic_json(job_path, {'files': files, 'output': str(output), 'mode': mode, 'coverage': coverage,
                'progress_path': str(progress_path)})
            self.jobs = {k: v for k, v in list(self.jobs.items())[-15:]}
            self.jobs[ident] = {'id': ident, 'state': 'running', 'phase': 'scan', 'file_count': len(files), 'rows_written': 0,
                'progress_path': str(progress_path), 'output': str(output)}
            threading.Thread(target=self._run, args=(ident, job_path), daemon=True, name='recording-comparison').start()
            return self.status(ident)

    def _run(self, ident, job_path):
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('comparison_worker.py')), str(job_path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            with self.lock:
                self.process = process
                if self.closed: process.terminate()
            stdout, stderr = process.communicate(timeout=3600)
            if process.returncode: raise ValueError('Preparazione confronto interrotta: ' + stderr[-1000:])
            result = json.loads(stdout)
            if not result.get('ok'): raise ValueError(result.get('error') or 'Confronto non riuscito')
            with self.lock: self.jobs[ident].update(state='ready', recording=result['recording'])
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                process.kill(); process.communicate()
            with self.lock:
                self.jobs[ident].update(state='error', error=str(exc))
                output = Path(self.jobs[ident]['output'])
            output.with_suffix('.parquet.part').unlink(missing_ok=True)
            if not output.exists(): output.with_suffix('.metadata.json').unlink(missing_ok=True)
        finally:
            with self.lock: self.process = None

    def status(self, ident):
        with self.lock:
            if ident not in self.jobs: raise ValueError('Preparazione confronto non trovata')
            job = dict(self.jobs[ident])
        progress_path = job.pop('progress_path')
        job.pop('output', None)
        if job['state'] == 'running':
            try: job.update(json.loads(Path(progress_path).read_text(encoding='utf-8')))
            except (OSError, ValueError): pass
        return job

    def close(self):
        with self.lock:
            self.closed = True
            process = self.process
        if process and process.poll() is None: process.terminate()
