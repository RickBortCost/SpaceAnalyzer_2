"""C# engine recording parity: same samples through the Python Recorder and the engine.

Files must be identical except for wall-clock dependent columns (elapsed_ms,
generated names); the primary capture must be readable by primary_format.py.
"""
import csv
import json
import math
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyarrow.parquet as pq
from engine_bridge import Engine
from primary_format import inspect, iter_events, iter_samples
from recording_reader import RecordingLibrary
from recordings import Recorder, prepare_format

BASE = 1700000000000.125
INFO = {'protocols': ['ads', 'mqtt'], 'time_basis': 'PC UTC arrival', 'source_intervals': {'ads': 10, 'mqtt': 40.5}, 'note': 'àèì "x"'}
NODES = [{"nodeid": "ads:MAIN.Double", "display_name": "Carico"},
         {"nodeid": "mqtt:plant/motor#/load", "display_name": "Carico, \"motore\""},
         {"nodeid": "calc:formula-1", "display_name": "Formula"}]


def cycles():
    """Values covering Python repr, typed integers, text, booleans, lists, qualities."""
    values = [0.1, -0.0, 1e-05, 1e16, 123456789.125, 5e-324, 1.7976931348623157e308, 2.5, float('nan'), 42, True, 'testo, "quotato"\nriga', [1, 2.5, 'x'], {'a': 1, 'b': [True, None]}]
    for i, value in enumerate(values):
        t = BASE + i * 10.5
        rows = []
        for n, node in enumerate(NODES):
            good = not (i == 3 and n == 1)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            rows.append({"nodeid": node["nodeid"], "value": value if not (isinstance(value, float) and math.isnan(value)) else 'nan',
                         "plot_value": float(value) if numeric else None, "data_type": "LREAL" if n == 0 else "Double",
                         "good": good, "quality": "Good" if good else "Bad", "status": "ADS_OK" if good else "Bad",
                         "status_code": 0 if good else 2147483648, "source_ts": None if n == 2 else "2026-10-02T00:00:00.1234567+00:00",
                         "server_ts": None, "local_ts": "2026-10-02T00:00:00.123457+00:00" if n != 1 else None,
                         "source_filetime": "134037504001234567" if n == 0 else None, "source_protocol": ["ads", "mqtt", "calculated"][n],
                         "acquisition_timestamp_ms": t - 1 if n == 1 else None, "acquisition_origin": "notification" if n == 0 else None,
                         "received_ns": 1700000000000125000 + i})
        for row in rows:
            for key in [k for k, v in row.items() if v is None and k in ('acquisition_timestamp_ms', 'acquisition_origin', 'local_ts', 'source_filetime')]:
                del row[key]
        yield t, rows


class EngineRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = []
        cls.engine = Engine(on_event=cls.events.append)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def record_python(self, directory, fmt, options):
        prepare_format(fmt, options)
        rec = Recorder(directory, '3.0.0')
        rec.start(fmt, NODES, 'mixed://test', 10, {**options, 'acquisition_info': INFO})
        for t, rows in cycles():
            self.assertTrue(rec.enqueue(t, rows, 10))
        state = rec.stop(timeout=30)
        self.assertEqual(state['state'], 'stopped', state)
        return state

    def record_engine(self, directory, fmt, options):
        state = self.engine.call('record.start', directory=str(directory), version='3.0.0', format=fmt, nodes=NODES,
                                 endpoint='mixed://test', sample_ms=10, options={**options, 'acquisition_info': INFO})
        session = state['engine_session']
        for t, rows in cycles():
            self.assertTrue(self.engine.call('record.enqueue', session=session, t=t, rows=rows, period=10))
        self.engine.call('record.stop', session=session, reason='user', timeout=30)
        for _ in range(600):
            state = self.engine.call('record.status', session=session)
            if not state['busy']:
                break
            time.sleep(.05)
        self.assertEqual(state['state'], 'stopped', state)
        return state

    def test_csv_matches_python_writer(self):
        with tempfile.TemporaryDirectory() as py_dir, tempfile.TemporaryDirectory() as cs_dir:
            py = self.record_python(py_dir, 'csv', {})
            cs = self.record_engine(cs_dir, 'csv', {})
            def rows(path):
                raw = Path(path).read_bytes()
                self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
                with open(path, encoding='utf-8-sig', newline='') as f:
                    data = list(csv.DictReader(f))
                for row in data:
                    row.pop('elapsed_ms')
                return data
            self.assertEqual(rows(cs['path']), rows(py['path']))
            self.assertEqual(Path(cs['path']).read_bytes().count(b'\r\n'), Path(py['path']).read_bytes().count(b'\r\n'))
            self.assertEqual(cs['rows_written'], py['rows_written'])

    def test_parquet_profiles_match_python_writer(self):
        for profile in ('compact', 'maximum', 'fast', 'compatible'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as py_dir, tempfile.TemporaryDirectory() as cs_dir:
                options = {'parquet_profile': profile}
                py = self.record_python(py_dir, 'parquet', options)
                cs = self.record_engine(cs_dir, 'parquet', options)
                a, b = pq.read_table(py['path']), pq.read_table(cs['path'])
                self.assertEqual(b.schema.remove_metadata(), a.schema.remove_metadata())
                left, right = a.drop(['elapsed_ms']).to_pylist(), b.drop(['elapsed_ms']).to_pylist()
                self.assertEqual(len(left), len(right))
                for x, y in zip(left, right):
                    for key in x:
                        same = x[key] == y[key] or (isinstance(x[key], float) and math.isnan(x[key]) and math.isnan(y[key]))
                        self.assertTrue(same, (profile, key, x[key], y[key]))
                    self.assertEqual(math.copysign(1, x['value_numeric']) if x['value_numeric'] is not None else None,
                                     math.copysign(1, y['value_numeric']) if y['value_numeric'] is not None else None)
                meta = pq.read_metadata(cs['path'])
                expected = {k: v for k, v in pq.read_metadata(py['path']).metadata.items() if k != b'ARROW:schema'}
                self.assertEqual(dict(meta.metadata), expected)
                column = meta.row_group(0).column(1)
                if profile != 'compatible':
                    self.assertEqual(column.compression, 'ZSTD')
                    self.assertIn('BYTE_STREAM_SPLIT', column.encodings)
                library = RecordingLibrary(cs_dir)
                view = library.load(Path(cs['path']).name)
                self.assertEqual(set(view['series']), {n['nodeid'] for n in NODES})

    def converted_pair(self, fmt, options=None, rows=None):
        """Python FDA/RBF session vs engine session on the same samples."""
        options = dict(options or {'session_name': 'Conversione'})
        source = list(rows or cycles())
        def python(directory):
            prepare_format(fmt, options)
            rec = Recorder(directory, '3.0.0')
            rec.start(fmt, NODES, 'mixed://test', 10, options)
            for t, batch in source:
                self.assertTrue(rec.enqueue(t, batch, 10))
            state = rec.stop(timeout=60)
            while state['busy']:
                state = rec.stop(timeout=1)
            return state
        def engine(directory):
            state = self.engine.call('record.start', directory=str(directory), version='3.0.0', format=fmt, nodes=NODES,
                                     endpoint='mixed://test', sample_ms=10, options=options)
            session = state['engine_session']
            for t, batch in source:
                self.assertTrue(self.engine.call('record.enqueue', session=session, t=t, rows=batch, period=10))
            self.engine.call('record.stop', session=session, reason='user', timeout=30)
            for _ in range(1200):
                state = self.engine.call('record.status', session=session)
                if not state['busy']:
                    break
                time.sleep(.05)
            return state
        return python, engine

    def compare_converted(self, fmt, rows=None):
        python, engine = self.converted_pair(fmt, rows=rows)
        with tempfile.TemporaryDirectory() as py_dir, tempfile.TemporaryDirectory() as cs_dir:
            py, cs = python(py_dir), engine(cs_dir)
            self.assertEqual(py['state'], 'stopped', py)
            self.assertEqual(cs['state'], 'stopped', cs)
            self.assertEqual(len(cs['files']), len(py['files']))
            for a, b in zip(py['files'], cs['files']):
                self.assertEqual(Path(cs_dir, b['name']).read_bytes(), Path(py_dir, a['name']).read_bytes(), (fmt, a['name']))
                self.assertEqual({k: v for k, v in b.items() if k != 'name'}, {k: v for k, v in a.items() if k != 'name'})
                meta_a = json.loads(Path(py_dir, a['name']).with_suffix('.metadata.json').read_text())
                meta_b = json.loads(Path(cs_dir, b['name']).with_suffix('.metadata.json').read_text())
                volatile = {'started_at', 'ended_at', 'path', 'filename', 'session_id', 'original_archive', 'duration_seconds', 'writer'} if fmt == 'das' else {'path', 'filename', 'session_id', 'original_archive', 'writer'}
                for key in set(meta_a) | set(meta_b):
                    if key in volatile or key in ('max_file_bytes', 'queue_capacity_cycles', 'cycles_in_file', 'session_rows', 'part'):
                        continue
                    if key in ('started_at', 'ended_at') or key.endswith('_at'):
                        continue
                    self.assertEqual(meta_b.get(key), meta_a.get(key), (fmt, key))
            library = RecordingLibrary(cs_dir)
            view = library.load(cs['files'][0]['name'])
            self.assertTrue(view['series'])

    def fda_cycles(self, scale=1.0):
        for i in range(60):
            t = BASE + i * 3.6  # several samples share a rounded millisecond
            good = i % 11 != 3
            uncertain = i % 13 == 4
            yield t, [
                {"nodeid": NODES[0]["nodeid"], "value": i * 0.37 * scale, "plot_value": i * 0.37 * scale, "data_type": "LREAL", "good": good and not uncertain,
                 "quality": "Uncertain" if uncertain else "Good" if good else "Bad", "status": "OK"},
                {"nodeid": NODES[1]["nodeid"], "value": -i * 123.5 * scale, "plot_value": -i * 123.5 * scale, "data_type": "Double", "good": True, "quality": "Good", "status": "OK"},
                {"nodeid": NODES[2]["nodeid"], "value": 'testo', "plot_value": None, "data_type": "String", "good": True, "quality": "Good", "status": "OK"}]
            if i % 7 == 0:  # repeated channel at the same time: an extra frame
                yield t, [{"nodeid": NODES[0]["nodeid"], "value": 1.5, "plot_value": 1.5, "data_type": "LREAL", "good": True, "quality": "Good", "status": "OK"}]

    def test_fda_files_are_byte_identical_to_python(self):
        for scale in (1.0, 1e-6, 4e5):
            with self.subTest(scale=scale):
                self.compare_converted('das', rows=list(self.fda_cycles(scale)))

    def test_fda_overflow_is_reported_like_python(self):
        python, engine = self.converted_pair('das')
        with tempfile.TemporaryDirectory() as py_dir, tempfile.TemporaryDirectory() as cs_dir:
            py, cs = python(py_dir), engine(cs_dir)
            self.assertEqual((py['state'], cs['state']), ('error', 'error'))
            self.assertIn('Overflow numerico FDA', py['error'])
            self.assertIn('Overflow numerico FDA', cs['error'])
            self.assertEqual(cs['files'], [])

    def test_rbf_files_are_byte_identical_to_python(self):
        typed = []
        for i in range(40):
            t = BASE + i * 7.25
            typed.append((t, [
                {"nodeid": NODES[0]["nodeid"], "value": i * 0.5, "plot_value": i * 0.5, "data_type": "LREAL", "good": True, "quality": "Good", "status": "OK"},
                {"nodeid": NODES[1]["nodeid"], "value": i % 2 == 0, "plot_value": float(i % 2 == 0), "data_type": "BOOL", "good": i != 5, "quality": "Good" if i != 5 else "Bad", "status": "OK"},
                {"nodeid": NODES[2]["nodeid"], "value": 'stato %d àè' % i, "plot_value": None, "data_type": "STRING", "good": True, "quality": "Good", "status": "OK"}]))
            if i % 9 == 0:  # a repeated channel at the same time opens a new frame
                typed.append((t, [{"nodeid": NODES[0]["nodeid"], "value": -1.0, "plot_value": -1.0, "data_type": "LREAL", "good": True, "quality": "Uncertain", "status": "U"}]))
        self.compare_converted('rbf', rows=typed)

    def test_csv_size_rotation_matches_python(self):
        rows = [(BASE + i, [dict(r, value=float(i), plot_value=float(i)) for r in batch]) for i, (_, batch) in enumerate(list(cycles()) * 30)]
        options = {'max_file_mb': 0.004}
        def python(directory):
            rec = Recorder(directory, '3.0.0'); rec.start('csv', NODES, 'mixed://test', 10, options)
            for t, batch in rows: self.assertTrue(rec.enqueue(t, batch, 10))
            return rec.stop(timeout=30)
        with tempfile.TemporaryDirectory() as py_dir, tempfile.TemporaryDirectory() as cs_dir:
            py = python(py_dir)
            state = self.engine.call('record.start', directory=cs_dir, version='3.0.0', format='csv', nodes=NODES, endpoint='mixed://test', sample_ms=10, options=options)
            session = state['engine_session']
            for t, batch in rows: self.assertTrue(self.engine.call('record.enqueue', session=session, t=t, rows=batch, period=10))
            self.engine.call('record.stop', session=session, timeout=30)
            cs = self.wait_idle(session)
            self.assertGreater(len(py['files']), 2)
            self.assertEqual([f['rows'] for f in cs['files']], [f['rows'] for f in py['files']])
            for a, b in zip(py['files'], cs['files']):
                self.assertLessEqual(b['bytes'], 0.004 * 1024 * 1024)
                strip = lambda p: [l.split(',', 3)[:2] + l.split(',', 3)[3:] for l in Path(p).read_text(encoding='utf-8-sig').splitlines()]
                self.assertEqual(strip(Path(cs_dir, b['name'])), strip(Path(py_dir, a['name'])))

    def test_parquet_size_rotation_never_exceeds_limit(self):
        rows = [(BASE + i, [dict(r, value=float(i), plot_value=float(i)) for r in batch]) for i, (_, batch) in enumerate(list(cycles()) * 30)]
        with tempfile.TemporaryDirectory() as cs_dir:
            state = self.engine.call('record.start', directory=cs_dir, version='3.0.0', format='parquet', nodes=NODES, endpoint='mixed://test', sample_ms=10, options={'max_file_mb': 0.006})
            session = state['engine_session']
            for t, batch in rows: self.assertTrue(self.engine.call('record.enqueue', session=session, t=t, rows=batch, period=10))
            self.engine.call('record.stop', session=session, timeout=30)
            cs = self.wait_idle(session)
            self.assertEqual(cs['state'], 'stopped', cs)
            self.assertGreater(len(cs['files']), 1)
            self.assertEqual(sum(f['rows'] for f in cs['files']), len(rows) * len(NODES))
            for f in cs['files']:
                self.assertLessEqual(f['bytes'], 0.006 * 1024 * 1024)
                self.assertEqual(pq.read_table(Path(cs_dir, f['name'])).num_rows, f['rows'])

    def test_failed_format_is_rebuilt_from_primary(self):
        for fmt in ('csv', 'parquet', 'das', 'rbf'):
            with self.subTest(format=fmt), tempfile.TemporaryDirectory() as cs_dir:
                source = list(self.fda_cycles())
                state = self.engine.call('record.start', directory=cs_dir, version='3.0.0', format=fmt, nodes=NODES, endpoint='mixed://test', sample_ms=10,
                                         options={'_test_external_reject_after': 5})
                session = state['engine_session']
                for t, batch in source: self.assertTrue(self.engine.call('record.enqueue', session=session, t=t, rows=batch, period=10))
                self.engine.call('record.stop', session=session, timeout=30)
                cs = self.wait_idle(session)
                self.assertEqual(cs['state'], 'stopped', cs)
                self.assertTrue(cs['external_rebuilt_from_primary'], cs)
                self.assertEqual(cs['external_lost_samples'], 0)
                kinds = [e['kind'] for e in self.events]
                self.assertIn('external_rebuild_finished', kinds)
                library = RecordingLibrary(cs_dir)
                rows = [r for f in (cs.get('original_files') or cs['files']) for r in library._iter_rows(Path(cs_dir, f['name']))]
                self.assertEqual(len(rows), sum(len(b) for _, b in source))

    def wait_idle(self, session):
        for _ in range(1200):
            state = self.engine.call('record.status', session=session)
            if not state['busy']:
                return state
            time.sleep(.05)
        self.fail('engine session still busy')

    def test_primary_capture_is_readable_by_python_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.record_engine(directory, 'parquet', {})
            primary = state['primary']
            self.assertEqual(primary['integrity'], 'complete')
            path = primary['path']
            self.assertTrue(path.endswith('.sac'))
            report = inspect(path)
            self.assertEqual(report['error'], '')
            self.assertTrue(report['finalized'])
            expected = [row for _, rows in cycles() for row in rows]
            captured = list(iter_samples(path))
            self.assertEqual(len(captured), len(expected))
            for want, got in zip(expected, captured):
                self.assertEqual(got['nodeid'], want['nodeid'])
                if isinstance(want['value'], float):
                    self.assertEqual(struct_bits(got['value']), struct_bits(want['value']))
                else:
                    self.assertEqual(got['value'], want['value'])
                self.assertEqual(got['received_ns'], str(want['received_ns']))
                self.assertEqual(got['quality'], want['quality'])
                self.assertEqual(got['source_filetime'], want.get('source_filetime'))

    def test_annotations_and_failure_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.engine.call('record.start', directory=directory, version='3.0.0', format='csv', nodes=NODES,
                                     endpoint='mixed://test', sample_ms=10, options={})
            session = state['engine_session']
            t, rows = next(cycles())
            self.assertTrue(self.engine.call('record.enqueue', session=session, t=t, rows=rows, period=10))
            self.assertTrue(self.engine.call('record.annotate', session=session, kind='source_loss', details={'protocol': 'mqtt', 'lost': 3}))
            self.engine.call('record.fail', session=session, message='MQTT: coda piena (test)')
            for _ in range(600):
                state = self.engine.call('record.status', session=session)
                if not state['busy']:
                    break
                time.sleep(.05)
            self.assertEqual(state['state'], 'error')
            self.assertIn('coda piena', state['error'])
            path = state['primary']['path']
            self.assertTrue(path.endswith('.incomplete.sac'), path)
            self.assertEqual([(e['kind'], e['details']['lost']) for e in iter_events(path)], [('source_loss', 3)])
            self.assertTrue(any(e['kind'] == 'primary_failure' for e in self.events))


def struct_bits(value):
    import struct
    return struct.pack('<d', value)


if __name__ == '__main__':
    unittest.main()
