"""Bounded live computation and original-sample replay, independent of acquisition."""
import hashlib
import itertools
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from formula_engine import Plan, Engine, number, stamp
from recording_reader import RecordingLibrary


class CalculationService:
    def __init__(self, directory, callback, capacity=512):
        self.directory = Path(directory)
        self.callback = callback;self.fault_callback=None
        self.lock = threading.RLock()
        self.queue = queue.Queue(maxsize=capacity)
        self.generation = 0
        self.config = None
        self.closed = False
        self.process = None
        self.responses = None
        self.dropped = self.late = 0
        self.error = ''
        self.elapsed_ms = 0
        self.raw_ids = set()
        self.ids = []
        self.overflow = None
        self.history_lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True, name='FormulaController')
        self.thread.start()

    def configure(self, formulas, protocol):
        plan = Plan(formulas, 'live', protocol=protocol)
        with self.lock:
            self.generation += 1
            self.config = {'formulas': formulas, 'protocol': protocol}
            self.raw_ids = plan.raw_ids
            self.ids = plan.ids
            self.error = ''; self.overflow = None
            while True:
                try:
                    item = self.queue.get_nowait()
                    if item[0] == 'barrier': item[1].set()
                except queue.Empty: break
            return self.generation

    def submit(self, t, rows, sample_ms):
        # No parsing, serialization, I/O or formula evaluation on the PLC thread.
        with self.lock:
            if not self.ids or self.closed: return
            event = ('batch', self.generation, t, tuple(rows), sample_ms)
            try: self.queue.put_nowait(event)
            except queue.Full:
                self.dropped += 1; self.overflow = (self.generation, t, sample_ms)
                self.error = 'Coda calcoli piena: campioni calcolati persi; acquisizione originale continua'
                if self.fault_callback:self.fault_callback(self.error,self.dropped)

    def status(self):
        with self.lock:
            return {'active_ids': list(self.ids), 'queue': self.queue.qsize(),
                'capacity': self.queue.maxsize, 'dropped_batches': self.dropped,
                'late_samples': self.late, 'processing_ms': self.elapsed_ms,
                'error': self.error, 'generation': self.generation}

    def flush(self, timeout=10):
        event = threading.Event()
        try: self.queue.put(('barrier', event), timeout=timeout)
        except queue.Full: raise RuntimeError('Calcoli ancora occupati: riprovare Stop') from None
        if not event.wait(timeout): raise RuntimeError('Attesa calcoli scaduta: riprovare Stop')

    def close(self):
        self.closed = True
        self._terminate()
        self.thread.join(timeout=6)

    @staticmethod
    def command():
        return [sys.executable, '-u', str(Path(__file__).with_name('calculation_worker.py'))]

    def _terminate(self):
        process, self.process = self.process, None
        if process is not None:
            try: process.kill(); process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired): pass
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe:
                    try: pipe.close()
                    except OSError: pass

    def _start(self, config):
        self._terminate()
        process = subprocess.Popen(self.command()+['--live'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        responses = queue.Queue()
        self.process, self.responses = process, responses
        def read():
            try:
                for line in process.stdout: responses.put(line)
            except (OSError, ValueError): pass
            finally: responses.put(None)
        threading.Thread(target=read, daemon=True, name='FormulaResponses').start()
        self._request({'op': 'configure', **config})

    def _request(self, message):
        started = time.perf_counter()
        self.process.stdin.write(json.dumps(message, ensure_ascii=False, allow_nan=False)+'\n')
        self.process.stdin.flush()
        try: line = self.responses.get(timeout=5)
        except queue.Empty: raise RuntimeError('Il processo calcoli non risponde') from None
        if line is None: raise RuntimeError('Il processo calcoli è stato interrotto')
        response = json.loads(line)
        if not response.get('ok'): raise ValueError(response.get('error', 'Errore motore calcoli'))
        with self.lock:
            self.elapsed_ms = (time.perf_counter()-started)*1000
            self.late = response.get('late_samples', 0)
            if self.overflow is None: self.error = ''
        return response.get('rows', [])

    def _deliver(self, rows, generation, sample_ms):
        if rows and generation == self.generation and not self.closed:
            self.callback(rows, generation, sample_ms)

    def _loop(self):
        generation = -1; sample_ms = 0; last_event_t = None
        while not self.closed:
            barrier = None
            try:
                with self.lock: current, config, ids = self.generation, self.config, list(self.ids)
                if current != generation:
                    generation = current
                    last_event_t = None
                    if ids: self._start(config)
                    else: self._terminate()
                try: item = self.queue.get(timeout=.05)
                except queue.Empty:
                    if self.process: self._deliver(self._request({'op': 'flush'}), generation, sample_ms)
                    continue
                # Configuration can change while waiting for the first batch.
                with self.lock: current, config, ids = self.generation, self.config, list(self.ids)
                if current != generation:
                    generation = current
                    last_event_t = None
                    if ids: self._start(config)
                    else: self._terminate()
                if item[0] == 'barrier':
                    barrier = item[1]
                    if self.process: self._deliver(self._request({'op': 'flush'}), generation, sample_ms)
                    barrier.set(); continue
                batches = [item]
                for _ in range(31):
                    try: next_item = self.queue.get_nowait()
                    except queue.Empty: break
                    if next_item[0] == 'barrier': barrier = next_item[1]; break
                    batches.append(next_item)
                events = []
                for _, token, t, rows, period in batches:
                    if token != generation: continue
                    sample_ms = period
                    last_event_t = t if last_event_t is None else max(t,last_event_t)
                    normalized = [{'node_id': row['nodeid'], 'value_numeric': row.get('plot_value'),
                        'quality': row.get('quality') or ('Good' if row.get('good') else 'Bad')} for row in rows if row['nodeid'] in self.raw_ids]
                    events.append([t, normalized])
                if events and self.process: self._deliver(self._request({'op': 'batch', 'events': events}), generation, sample_ms)
                with self.lock: overflow, self.overflow = self.overflow, None
                if overflow and overflow[0] == generation:
                    t = overflow[1]
                    gaps = [dict(node_id=n, timestamp_ms=t, value_numeric=None, quality='Bad', good=False,
                        status='CalculationQueueOverflow', data_type='Double', source_timestamp=stamp(t)) for n in ids]
                    self._deliver(gaps, generation, overflow[2])
                    # Reset held values: discarded source changes must never be used as if current.
                    self._start(config)
                if barrier:
                    if self.process: self._deliver(self._request({'op': 'flush'}), generation, sample_ms)
                    barrier.set()
            except Exception as exc:
                with self.lock: self.error = str(exc) or type(exc).__name__
                logging.exception('Errore processo calcoli')
                if last_event_t is not None:
                    self._deliver([dict(node_id=n,timestamp_ms=last_event_t,value_numeric=None,quality='Bad',
                        good=False,status='CalculationWorkerError: '+str(exc),data_type='Double',source_timestamp=stamp(last_event_t)) for n in ids],generation,sample_ms)
                self._terminate(); generation = -1
                if barrier: barrier.set()
                # Yield without delaying the acquisition thread.
                threading.Event().wait(.1)
        self._terminate()

    @staticmethod
    def available(formulas, physical):
        physical = set(physical)
        plan = Plan(formulas, 'recorded', external_ids=physical)
        result = []
        for nid in plan.ids:
            f = plan.formulas[nid]
            if nid not in physical and all(s['nodeid'] in physical for s in plan.raw_sources(nid)):
                result.append({'nodeid': nid, 'display_name': f['name'], 'unit': f['unit'] or f['unit_hint'],
                    'calculated': True, 'expression': f['expression']})
        return result

    def history(self, library, name, ids, formulas, start=None, end=None, destination=None, fmt='parquet'):
        original = library._path(name)
        descriptor = library.describe(name)
        physical = {s['nodeid'] for s in descriptor.get('signals', []) if not s.get('virtual')}
        if not physical:
            physical = {s['nodeid'] for s in library.load(name, max_points=100)['signals']}
        wanted = list(dict.fromkeys(ids or physical))
        if len(wanted) > 64: raise ValueError('Massimo 64 segnali per elaborazione')
        virtual = set(wanted)-physical
        available = {s['nodeid'] for s in self.available(formulas, physical)}
        if not virtual <= available: raise ValueError('Formula non disponibile: controllare modalità, stato e sorgenti presenti nel file')
        stat = original.stat()
        dat_signature=None
        if original.suffix.lower()=='.pda3seq':
            from pda3.service import sequence_info,reader
            parts,info=sequence_info(library,original)
            dat_signature=[info,[(n,reader(library._path(n)).sha256,m) for n,m in parts]]
        elif original.suffix.lower()=='.dat':
            from pda3.service import is_pda3,details
            dat_signature=details(original,library._metadata(original)) if is_pda3(original) else library._dat_source(original)[0]
        key = hashlib.sha256(json.dumps([str(original), stat.st_size, stat.st_mtime_ns, dat_signature, wanted, formulas, start, end], sort_keys=True).encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        output = Path(destination) if destination else self.directory / (key+'.parquet')
        meta = output.with_suffix('.metadata.json')
        # Serialize cache generation; readers only see complete immutable files.
        with self.history_lock:
            if not destination and output.exists() and meta.exists():
                os.utime(output,None)  # Keep recently used caches out of age-based cleanup.
                return RecordingLibrary(output.parent), output.name, descriptor
            temporary = output.with_name(output.name+'.'+uuid.uuid4().hex+'.part')
            job = {'path': str(original), 'signal_ids': wanted, 'materialized_ids': list(physical), 'formulas': formulas,
                'start_ms': start, 'end_ms': end, 'output': str(temporary), 'format': fmt,
                'sample_ms': descriptor.get('sample_ms') or 0}
            try:
                response = subprocess.run(self.command(), input=json.dumps(job, ensure_ascii=False, allow_nan=False),
                    text=True, encoding='utf-8', capture_output=True, timeout=180,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if response.returncode: raise RuntimeError('Processo storico interrotto: '+response.stderr[-500:])
                result = json.loads(response.stdout)
                if not result.get('ok'): raise ValueError(result.get('error', 'Calcolo storico non riuscito'))
                metadata = result['metadata']
                metadata.update(format=fmt, version=1, started_at=stamp(metadata['start_ms']), ended_at=stamp(metadata['end_ms']))
                output.parent.mkdir(parents=True, exist_ok=True)
                os.replace(temporary, output)
                temporary_meta = meta.with_name(meta.name+'.tmp')
                temporary_meta.write_text(json.dumps(metadata, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
                os.replace(temporary_meta, meta)
                if not destination:
                    caches = sorted(self.directory.glob('*.parquet'), key=lambda p: p.stat().st_mtime, reverse=True)
                    for old in caches[8:]:
                        # A recent cached file may still be read by an HTTP request.
                        if old != output and time.time()-old.stat().st_mtime > 86400:
                            old.unlink(missing_ok=True); old.with_suffix('.metadata.json').unlink(missing_ok=True)
            except subprocess.TimeoutExpired: raise ValueError('Calcolo storico scaduto: ridurre intervallo o numero di segnali') from None
            finally: temporary.unlink(missing_ok=True)
        return RecordingLibrary(output.parent), output.name, descriptor

    @staticmethod
    def preview_live(formulas, nid, points, start=None, end=None):
        plan = Plan(formulas, 'live', [nid]); engine = Engine(plan)
        ordered = sorted((p[0], key, p) for key, data in points.items() if not plan.raw_ids or key in plan.raw_ids for p in data)
        result = []
        for t, group in itertools.groupby(ordered, key=lambda p: p[0]):
            if end is not None and t > end: break
            rows = [{'node_id': key, 'value_numeric': p[1], 'quality': p[2] if len(p)>2 else 'Good'} for _, key, p in group]
            output = engine.process(t, rows)
            if start is None or t >= start: result.extend(output)
        return result[-200:]

    def preview(self, formulas, nid, points, start=None, end=None):
        try:
            response = subprocess.run(self.command()+['--preview'],
                input=json.dumps(dict(formulas=formulas,nid=nid,points=points,start=start,end=end),allow_nan=False),
                text=True,encoding='utf-8',capture_output=True,timeout=60,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired: raise ValueError('Anteprima scaduta: ridurre numero di sorgenti o complessità') from None
        if response.returncode: raise RuntimeError('Processo anteprima interrotto')
        result=json.loads(response.stdout)
        if not result.get('ok'): raise ValueError(result.get('error','Errore anteprima'))
        return result['rows']
