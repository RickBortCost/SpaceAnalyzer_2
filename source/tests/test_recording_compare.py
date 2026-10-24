"""Real mixed-format files, timestamps, quality and analysis: no mock data reader."""
import csv
import json
import math
from pathlib import Path
import sys
import tempfile
import struct
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyarrow as pa
import pyarrow.parquet as pq
from recording_compare import build_comparison, scan_recording
from recording_reader import RecordingLibrary
from recordings import COLUMNS
from analysis_tools import export_rows, selected_rows
from fft_engine import analyze

START = 1792580400000


def fixture(directory, name, start=START, period=10, count=101, value_offset=0, bad=(), missing=(), nid='ads:MAIN.Pressure'):
    path = Path(directory) / name
    rows = []
    for i in range(count):
        if i in missing: continue
        row = {c: None for c in COLUMNS}
        row.update(timestamp_ms=start + i * period, elapsed_ms=i * period, cycle=i + 1,
            node_id=nid, signal_name='Pressione', value_numeric=value_offset + math.sin(2 * math.pi * i / 32),
            value_text=str(i), data_type='Double', sample_ms=period,
            quality='Uncertain' if i in bad else 'Good', good=i not in bad,
            status='Uncertain' if i in bad else 'Good', ads_filetime=str(116444736000000000 + (start + i * period) * 10000))
        rows.append(row)
    if path.suffix == '.csv':
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader(); writer.writerows(rows)
    else: pq.write_table(pa.Table.from_pylist(rows), path)
    path.with_suffix('.metadata.json').write_text(json.dumps({'signals': [{'nodeid': nid, 'display_name': 'Pressione', 'unit': 'bar'}]}))
    return path, rows


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lib = RecordingLibrary(self.root)
        self.out = self.root / 'comparison.parquet'

    def tearDown(self): self.temp.cleanup()

    def job(self, paths, **options):
        return {'files': [{'path': str(p), 'name': p.name, 'display_name': p.name, 'format': p.suffix[1:]} for p in paths],
            'output': str(self.out), **options}

    def test_mixed_formats_keep_rates_values_names_and_absolute_time(self):
        a, ra = fixture(self.root, 'fast.csv', period=1, count=1500)
        b, rb = fixture(self.root, 'slow.parquet', start=START + 500, period=10, count=80, value_offset=30)
        descriptor = build_comparison(self.job([a, b]))
        self.assertEqual(len(descriptor['signals']), 2)
        self.assertEqual(descriptor['duration_seconds'], 1.499)
        self.assertEqual(descriptor['rows_written'], len(ra) + len(rb))
        self.assertNotEqual(descriptor['signals'][0]['nodeid'], descriptor['signals'][1]['nodeid'])
        self.assertIn('fast.csv', descriptor['signals'][0]['display_name'])
        rows = list(self.lib._iter_rows(self.out))
        for original, actual in zip(ra + rb, rows):
            self.assertEqual(actual['timestamp_ms'], original['timestamp_ms'])
            self.assertEqual(actual['original_timestamp_ms'], original['timestamp_ms'])
            self.assertEqual(actual['value_numeric'], original['value_numeric'])
            self.assertEqual(actual['ads_filetime'], original['ads_filetime'])
        self.assertEqual({r['sample_ms'] for r in rows}, {1, 10})
        self.assertEqual(self.lib.load(self.out.name)['signals'][0]['points_original'], 1500)
        self.assertEqual(self.lib._metadata(self.out)['signals'][0]['unit'], 'bar')

    def test_union_preserves_nonoverlapping_durations_without_joining_traces(self):
        a, ra = fixture(self.root, 'one.csv', count=31)
        b, rb = fixture(self.root, 'two.parquet', start=START + 5000, count=11)
        desc = build_comparison(self.job([a, b]))
        data = self.lib.load(self.out.name)
        self.assertEqual(len(data['series']), 2)
        self.assertEqual(sum(len(s) for s in data['series'].values()), 42)
        self.assertEqual(desc['duration_seconds'], 5.1)
        self.assertEqual(desc['comparison']['common_seconds'], 0)
        ids = [s['nodeid'] for s in desc['signals']]
        markers = self.lib.markers(self.out.name, ids, START + 2000, START + 5000)
        self.assertIsNone(markers['signals'][0]['x1'])
        self.assertIsNone(markers['signals'][0]['x2'])
        self.assertIsNone(markers['signals'][1]['x1'])
        self.assertEqual(markers['signals'][1]['x2']['timestamp_ms'], START + 5000)

    def test_intersection_clips_each_original_series_and_rejects_no_overlap(self):
        a, _ = fixture(self.root, 'a.csv', count=101)
        b, _ = fixture(self.root, 'b.parquet', start=START + 500, period=20, count=101)
        desc = build_comparison(self.job([a, b], coverage='intersection'))
        rows = list(self.lib._iter_rows(self.out))
        self.assertEqual(desc['rows_written'], 51 + 26)
        self.assertTrue(all(START + 500 <= r['timestamp_ms'] <= START + 1000 for r in rows))
        b, _ = fixture(self.root, 'b.parquet', start=START + 5000)
        with self.assertRaisesRegex(ValueError, 'intervallo temporale comune'):
            build_comparison(self.job([a, b], coverage='intersection'))
        self.assertFalse(self.out.exists())

    def test_manual_offset_preserves_original_timestamps_and_ads_time(self):
        a, _ = fixture(self.root, 'a.csv')
        b, rb = fixture(self.root, 'b.parquet', start=START + 3600000)
        job = self.job([a, b]); job['files'][1]['offset_ms'] = -3600000
        desc = build_comparison(job)
        rows = [r for r in self.lib._iter_rows(self.out) if r['source_file'] == b.name]
        self.assertEqual(rows[0]['timestamp_ms'], START)
        self.assertEqual(rows[0]['original_timestamp_ms'], START + 3600000)
        self.assertEqual(rows[0]['ads_filetime'], rb[0]['ads_filetime'])
        self.assertEqual(desc['comparison']['files'][1]['offset_ms'], -3600000)

    def test_relative_start_alignment_is_explicit_and_preserves_duration(self):
        a, _ = fixture(self.root, 'a.csv', count=101)
        b, _ = fixture(self.root, 'b.parquet', start=START + 86400000, count=51)
        desc = build_comparison(self.job([a, b], mode='starts'))
        self.assertEqual(desc['duration_seconds'], 1)
        self.assertEqual(desc['comparison']['files'][1]['offset_ms'], -86400000)
        data = self.lib.load(self.out.name)
        self.assertEqual({p[0] for p in [s[0] for s in data['series'].values()]}, {START})

    def test_quality_and_missing_time_are_never_interpolated(self):
        a, ra = fixture(self.root, 'a.csv', count=100, bad=(3, 4), missing=tuple(range(20, 40)))
        b, rb = fixture(self.root, 'b.parquet', count=40)
        desc = build_comparison(self.job([a, b]))
        ids = [s['nodeid'] for s in desc['signals']]
        rows = list(self.lib._iter_rows(self.out))
        self.assertEqual(len(rows), len(ra) + len(rb))
        self.assertEqual(sum(r['quality'] == 'Uncertain' for r in rows), 2)
        data = self.lib.load(self.out.name)
        self.assertTrue(any(p[1] is None and START + 200 < p[0] < START + 400 for p in data['series'][ids[0]]))
        marker = self.lib.markers(self.out.name, ids, START + 300, START + 30)
        self.assertIsNone(marker['signals'][0]['x1'])
        self.assertIsNone(marker['signals'][0]['x2'])
        self.assertEqual(self.lib._metadata(self.out)['signals'][0]['time_gaps'], 1)

    def test_exact_markers_and_fft_use_original_rows_on_shifted_timeline(self):
        a, _ = fixture(self.root, 'a.csv', count=1024)
        b, _ = fixture(self.root, 'b.parquet', start=START + 3600000, count=1024)
        job = self.job([a, b]); job['files'][1]['offset_ms'] = -3600000
        desc = build_comparison(job)
        nid = desc['signals'][1]['nodeid']
        spec = analyze(self.lib._iter_rows(self.out), nid, START, START + 10230, options={'block_size': 1024}, window='rectangular')
        self.assertEqual(spec['sample_hz'], 100)
        self.assertAlmostEqual(spec['peak_hz'], 3.125)
        self.assertAlmostEqual(spec['peak_amplitude'], 1)
        markers = self.lib.markers(self.out.name, [nid], START + 34, START + 87)
        self.assertEqual(markers['signals'][0]['x1']['timestamp_ms'], START + 30)
        self.assertEqual(markers['signals'][0]['x2']['timestamp_ms'], START + 90)
        target = self.root / 'export.csv'
        count = export_rows(selected_rows(self.lib._iter_rows(self.out), [nid], START + 100, START + 200), target, 'csv')
        self.assertEqual(count, 11)

    def test_standalone_summary_discovers_file_duration_and_units(self):
        path, _ = fixture(self.root, 'raw.csv', period=20, count=31)
        meta = self.lib.describe(path.name)
        self.assertEqual(meta['duration_seconds'], .6)
        self.assertEqual(meta['rows_written'], 31)
        self.assertEqual(meta['signals'][0]['unit'], 'bar')
        self.assertTrue(meta['started_at'].endswith('+00:00'))

    def test_fda_and_csv_on_one_real_timeline(self):
        from fda_das import read_header, iter_rows
        import io
        header = (Path(__file__).parent / 'fixtures/fda_reference_header.bin').read_bytes()
        h = read_header(io.BytesIO(header))
        das = self.root / 'external.das'
        das.write_bytes(header + b''.join(struct.pack('<I11h', 752 + i * 10, *([i] * 11)) for i in range(32)))
        start = h['epoch'] * 1000 + 752
        csv_path, rows = fixture(self.root, 'external.csv', start=start, count=16)
        meta = self.lib.describe(das.name)
        self.assertEqual(meta['duration_seconds'], .31)
        desc = build_comparison(self.job([das, csv_path]))
        self.assertEqual(len(desc['signals']), 12)
        data = self.lib.load(self.out.name)
        self.assertEqual(data['start_ms'], start)
        self.assertEqual(data['numeric_rows'], 32 * 11 + 16)
        expected = list(iter_rows(das))
        actual = [r for r in self.lib._iter_rows(self.out) if r['source_file'] == das.name]
        self.assertEqual([r['value_numeric'] for r in actual], [r['value_numeric'] for r in expected])

    def test_known_pda3_and_parquet_use_manifest_real_clock_and_real_channels(self):
        from test_pda3 import fixture as pda_fixture, BASE, NODES
        from pda3.window_export import process_job
        _, job = pda_fixture(self.root, count=32)
        result = process_job(job)
        self.assertEqual(result['status'], 'ready', result)
        dat = job.parent / 'window.dat'
        parquet, _ = fixture(self.root, 'other.parquet', start=BASE, count=16)
        desc = build_comparison(self.job([dat, parquet]))
        self.assertEqual(len(desc['signals']), 2)
        data = self.lib.load(self.out.name)
        self.assertEqual(data['start_ms'], BASE)
        self.assertEqual(data['numeric_rows'], 48)
        self.assertEqual({s['format'] for s in desc['comparison']['files']}, {'dat', 'parquet'})

    def test_invalid_input_order_or_clock_never_publishes_a_partial_result(self):
        a, _ = fixture(self.root, 'a.csv')
        b, _ = fixture(self.root, 'b.parquet')
        for change in ('offset', 'mode', 'duplicate', 'invalid_timestamp'):
            job = self.job([a, b])
            if change == 'offset': job['files'][0]['offset_ms'] = math.nan
            if change == 'mode': job['mode'] = 'guess'
            if change == 'duplicate': job['files'][1] = job['files'][0]
            if change == 'invalid_timestamp':
                rows = pq.read_table(b).to_pylist(); rows[5]['timestamp_ms'] = None; pq.write_table(pa.Table.from_pylist(rows), b)
            with self.subTest(change=change), self.assertRaises(ValueError): build_comparison(job)
            self.assertFalse(self.out.exists())
            self.assertFalse(self.out.with_suffix('.parquet.part').exists())
            self.assertTrue(a.exists()); self.assertTrue(b.exists())

    def test_gapped_decimation_stays_bounded_without_modifying_raw_values(self):
        a, ra = fixture(self.root, 'a.csv', count=15000, missing=range(0, 15000, 3))
        b, _ = fixture(self.root, 'b.parquet', count=10)
        desc = build_comparison(self.job([a, b]))
        nid = desc['signals'][0]['nodeid']
        data = self.lib.load(self.out.name, [nid], max_points=1000)
        self.assertLessEqual(len(data['series'][nid]), 1000)
        self.assertEqual(data['signals'][0]['points_original'], len(ra))
        self.assertTrue(any(p[1] is None for p in data['series'][nid]))

    def test_changed_input_is_rejected_between_streaming_passes(self):
        a, _ = fixture(self.root, 'a.csv')
        b, _ = fixture(self.root, 'b.parquet')
        changed = False
        def progress(state):
            nonlocal changed
            if state.get('phase') == 'write' and not changed:
                changed = True
                with a.open('a') as stream: stream.write('\n')
        with self.assertRaisesRegex(ValueError, 'cambiato'):
            build_comparison(self.job([a, b]), progress)
        self.assertFalse(self.out.exists())


if __name__ == '__main__': unittest.main(verbosity=2)
