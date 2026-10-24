"""New industrial recorder/analysis contracts, using actual CSV and Parquet bytes."""
import csv
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from recordings import Recorder
from recording_reader import RecordingLibrary
from analysis_tools import spectrum, export_rows, selected_rows
import pyarrow.parquet as pq

NODE='ns=2;s=Sine'
NODES=[{'nodeid':NODE,'display_name':'Sine'}]
def signal(i):
    return {'nodeid':NODE,'value':math.sin(i/10),'plot_value':math.sin(i/10),'good':True,'quality':'Good','status':'Good','status_code':0,'data_type':'Double','source_ts':'2026-09-30T00:00:00.123456+00:00','server_ts':'2026-09-30T00:00:00.123457+00:00'}

class Advanced(unittest.TestCase):
    def test_fft_calibrated_amplitude_and_reject_quality_gaps(self):
        rows=[dict(node_id=NODE,timestamp_ms=1000+i*1000/1024,value_numeric=10+3*math.sin(2*math.pi*25*i/1024),good=True) for i in range(1024)]
        result=spectrum(iter(rows),NODE,1000,2000)
        self.assertAlmostEqual(result['peak_hz'],25,places=5)
        self.assertAlmostEqual(result['peak_amplitude'],3,delta=.002)
        self.assertEqual(result['samples_fft'],1024)
        rows[20]['quality']='Uncertain'
        with self.assertRaisesRegex(ValueError,'Bad/Uncertain'):spectrum(iter(rows),NODE,1000,2000)
        rows[20]['quality']='Good'
        with self.assertRaisesRegex(ValueError,'buco'):spectrum(iter(rows[:10]+rows[20:]),NODE,1000,2000)

    def test_csv_size_rotation_metadata_and_no_lost_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            rec=Recorder(directory,'1.2.0')
            try:
                rec.start('csv',NODES,sample_ms=10,options={'max_file_mb':.012,'machine':'M1','operator':'R','session_name':'Test / Sine','description':'Transient','buffer_seconds':10})
                stamp=time.time()*1000
                for i in range(450):self.assertTrue(rec.enqueue(stamp+i*10,[signal(i)],10))
                state=rec.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
                paths=sorted(Path(directory).glob('*.csv'));self.assertGreater(len(paths),1)
                rows=[]
                for path in paths:
                    self.assertLessEqual(path.stat().st_size,int(.012*1024*1024))
                    with path.open(encoding='utf-8-sig',newline='') as f:part=list(csv.DictReader(f));rows.extend(part)
                    meta=json.loads(path.with_suffix('.metadata.json').read_text())
                    self.assertEqual(meta['rows_written'],len(part));self.assertEqual(meta['machine'],'M1');self.assertTrue(meta['session_id'])
                self.assertEqual([int(r['cycle']) for r in rows],list(range(1,451)))
                self.assertEqual(rows[0]['source_timestamp'],signal(0)['source_ts']);self.assertEqual(rows[0]['server_timestamp'],signal(0)['server_ts'])
                self.assertTrue(rows[0]['local_timestamp']);self.assertEqual(rows[0]['status_code'],'0')
            finally:rec.stop(timeout=10)

    def test_duration_rotation_both_formats_and_busy_start_keeps_state(self):
        for fmt in ('csv','parquet'):
            with self.subTest(fmt=fmt),tempfile.TemporaryDirectory() as directory:
                rec=Recorder(directory,'1.2.0')
                try:
                    rec.start(fmt,NODES,sample_ms=10,options={'max_duration_s':.1,'max_file_mb':0})
                    with self.assertRaises(ValueError):rec.start(fmt,NODES,options={'max_duration_s':9})
                    self.assertEqual(rec.max_duration,.1)
                    stamp=time.time()*1000
                    for i in range(8):
                        rec.started_mono-=.06
                        rec.enqueue(stamp+i*60,[signal(i)],10)
                    state=rec.stop(timeout=10);self.assertEqual(state['state'],'stopped',state)
                    parts=sorted(Path(directory).glob('*.'+fmt));self.assertGreater(len(parts),1)
                    library=RecordingLibrary(directory);rows=[row for path in parts for row in library._iter_rows(path)]
                    self.assertEqual(len(rows),8);self.assertEqual([int(r['cycle']) for r in rows],list(range(1,9)))
                    self.assertTrue(all(json.loads(p.with_suffix('.metadata.json').read_text())['duration_seconds']<.1 for p in parts))
                finally:rec.stop(timeout=10)

    def test_parquet_size_rotation_produces_readable_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            rec=Recorder(directory,'1.2.0')
            try:
                rec.start('parquet',NODES,sample_ms=10,options={'max_file_mb':.02,'buffer_seconds':120})
                stamp=time.time()*1000
                for i in range(9000):self.assertTrue(rec.enqueue(stamp+i*10,[signal(i)],10))
                state=rec.stop(timeout=20);self.assertEqual(state['state'],'stopped',state)
                paths=sorted(Path(directory).glob('*.parquet'));self.assertGreater(len(paths),1)
                self.assertTrue(all(p.stat().st_size<=int(.02*1024*1024) for p in paths))
                rows=[r for p in paths for r in pq.read_table(p).to_pylist()]
                self.assertEqual(len(rows),9000);self.assertEqual([r['cycle'] for r in rows],list(range(1,9001)))
            finally:rec.stop(timeout=10)

    def test_export_exact_interval_all_quality_and_timestamp_fields(self):
        rows=[dict(timestamp_ms=i*10,node_id=NODE,signal_name='Sine',value_numeric=i if i==0 else None,value_text=str(i),good=i==0,quality=['Good','Uncertain','Bad'][i],status=['Good','Uncertain','Bad'][i],status_code=i,source_timestamp='2026-09-30T00:00:00.123456+00:00',server_timestamp='2026-09-30T00:00:00.123457+00:00',local_timestamp='2026-09-30T00:00:00.123458+00:00') for i in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            for fmt in ('csv','parquet'):
                path=Path(directory)/('export.'+fmt)
                self.assertEqual(export_rows(selected_rows(iter(rows),[NODE],10,20),path,fmt),2)
                out=list(RecordingLibrary(directory)._iter_rows(path))
                self.assertEqual([r['quality'] for r in out],['Uncertain','Bad'])
                self.assertEqual(out[0]['source_timestamp'],rows[1]['source_timestamp'])
                view=RecordingLibrary(directory).load(path.name,[NODE])
                self.assertEqual([p[2] for p in view['series'][NODE]],['Uncertain','Bad'])
                self.assertTrue(all(p[1] is None for p in view['series'][NODE]))

if __name__=='__main__':unittest.main(verbosity=2)
