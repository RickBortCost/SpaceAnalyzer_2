"""Regression for the real failure: varying Float32 samples exceed native block size.

Noise is deterministic synthetic input. Tests do not claim hardware timing or
ibaAnalyzer execution. Every output is the real native writer, never a DAT mock.
"""
import json,math,random,struct,tempfile,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import pyarrow as pa
import pyarrow.parquet as pq
from test_pda3 import BASE
from pda3.exporter import PDA3TemplateWriter
from pda3.errors import PDA3CapacityError
from pda3.window_export import process_job
from pda3.recording import run_job,recover_pending
from pda3.storage import atomic_json,digest
from pda3.service import iter_rows,sequence_info
from recordings import Recorder,prepare_format
from recording_reader import RecordingLibrary

def noise(count,channels=9):
    # Odd affine permutation: different Float32 mantissa every cycle; no run-length
    # compression of adjacent values, including smooth/noisy industrial values.
    data={k:b''.join(struct.pack('<I',0x3f000000+((j*7919+k*97531)&0x7fffff)) for j in range(count)) for k in range(channels)}
    nodes=[{'nodeid':f'ads:MAIN.Noise{k}','display_name':f'Variabile {k}','unit':'u'} for k in range(channels)]
    return data,nodes

def noisy_fixture(root,count=3635,channels=9):
    root=Path(root);data,nodes=noise(count,channels);values={k:struct.unpack('<'+str(count)+'f',v) for k,v in data.items()}
    parquet=root/'Parquet';parquet.mkdir(parents=True,exist_ok=True);source=parquet/'noisy.parquet'
    rows=[{'timestamp_ms':BASE+j*10,'node_id':n['nodeid'],'signal_name':n['display_name'],'value_numeric':values[k][j],
        'quality':'Good','sample_ms':10,'cycle':j+1} for j in range(count) for k,n in enumerate(nodes)]
    pq.write_table(pa.Table.from_pylist(rows),source,compression='zstd')
    folder=root/'DAT'/'2026-10-21';folder.mkdir(parents=True,exist_ok=True);job=folder/'noisy.pda3job.json'
    atomic_json(job,{'id':'noisy','name':'noisy.dat','sources':['Parquet/noisy.parquet'],'nodes':nodes,'start_ms':BASE,
        'state':'queued','allow_partial':True,'tolerance_ms':1,'auto_split':True,'session':{'session_name':'Noise regression'}})
    return source,job,data,nodes

def reconstructed(library,path,nodes):
    streams={n['nodeid']:bytearray() for n in nodes};times={n['nodeid']:[] for n in nodes}
    for row in library._iter_rows(path):
        streams[row['node_id']].extend(struct.pack('<f',row['value_numeric']));times[row['node_id']].append(row['timestamp_ms'])
    return streams,times

class Capacity(unittest.TestCase):
    def test_recorder_normal_stop_with_9_noisy_signals_publishes_full_timeline(self):
        with tempfile.TemporaryDirectory() as d:
            prepare_format('pda3');data,nodes=noise(2100);r=Recorder(d,'test')
            r.start('pda3',nodes,sample_ms=10,options={'pda3_accept_template_time':True,'buffer_seconds':120})
            values={k:struct.unpack('<2100f',v) for k,v in data.items()}
            for j in range(2100):
                rows=[{'nodeid':n['nodeid'],'value':values[k][j],'plot_value':values[k][j],'good':True,'status':'Good','data_type':'Float'} for k,n in enumerate(nodes)]
                self.assertTrue(r.enqueue(BASE+j*10,rows,10))
            end=r.stop(timeout=60);self.assertEqual(end['state'],'stopped',end);self.assertEqual(end['error'],'')
            self.assertEqual(len(end['files']),3);self.assertEqual(end['rows_written'],18900);self.assertTrue(end['path'].endswith('.pda3seq'))
            self.assertEqual(sum(f['rows'] for f in end['files']),18900)
            streams,_=reconstructed(RecordingLibrary(d),Path(end['path']),nodes)
            for k,n in enumerate(nodes):self.assertEqual(bytes(streams[n['nodeid']]),data[k])

    def test_old_failed_capacity_job_is_recovered_from_original_archive(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job,_,_=noisy_fixture(root,count=2100);before=digest(source)
            request=json.loads(job.read_text());request.pop('auto_split');request.update(state='skipped',last_error='PDA3: dati non comprimibili nel blocco (5147 byte minimi, capacita 4997); Parquet conservato');atomic_json(job,request)
            recovery=recover_pending(root);deadline=time.monotonic()+50
            while recovery['running'] and time.monotonic()<deadline:time.sleep(.05)
            self.assertFalse(recovery['running']);self.assertEqual(recovery['errors'],[]);self.assertEqual(recovery['recovered'],1)
            result=json.loads(job.read_text());self.assertEqual(result['state'],'ready');self.assertTrue(result['upgraded_capacity_split'])
            self.assertEqual(digest(source),before);self.assertTrue(list(root.rglob('*.pda3seq')))

    def test_all_64_arbitrary_signals_fit_safe_segment_bit_exact(self):
        with tempfile.TemporaryDirectory() as d:
            data,nodes=noise(1000,64);p=Path(d)/'safe.dat';result=PDA3TemplateWriter().write(p,data,1000)
            self.assertTrue(result['float32_roundtrip_exact']);self.assertEqual(result['valid_samples_per_channel'],1000)
            # Writer checks all 3.84M stored bit patterns including excluded tail.

    def test_32715_values_9_channels_36_35_seconds_split_and_rejoin_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job,data,nodes=noisy_fixture(root);before=digest(source)
            with self.assertRaises(PDA3CapacityError) as failure:PDA3TemplateWriter().write(root/'too_big.dat',data,3635)
            self.assertGreater(failure.exception.details['required_bytes'],failure.exception.details['capacity'])
            start=time.perf_counter();result=run_job(job);seconds=time.perf_counter()-start
            self.assertEqual(result['status'],'ready',result);self.assertTrue(result['segmented']);self.assertEqual(result['segment_count'],4)
            self.assertEqual([f['samples_per_channel'] for f in result['dat_files']],[1000,1000,1000,635])
            self.assertEqual(sum(f['rows'] for f in result['dat_files']),32715);self.assertEqual(digest(source),before)
            lib=RecordingLibrary(root);seq=lib._path(result['sequence_name']);streams,times=reconstructed(lib,seq,nodes)
            for k,n in enumerate(nodes):
                self.assertEqual(bytes(streams[n['nodeid']]),data[k]);self.assertEqual(times[n['nodeid']],[BASE+j*10 for j in range(3635)])
            self.assertEqual(lib.describe(result['sequence_name'])['duration_seconds'],36.35)
            self.assertEqual(lib.load(result['sequence_name'],[nodes[0]['nodeid']])['numeric_rows'],3635)
            again=run_job(job);self.assertEqual(again['status'],'ready',again);self.assertEqual(again['dat_files'],result['dat_files'])
            print('Noise regression: 32715 exact values / 9 channels / 4 native DATs, seconds=',round(seconds,3),flush=True)

    def test_strict_option_and_genuine_data_errors_still_preserve_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job,_,_=noisy_fixture(root);request=json.loads(job.read_text());request['auto_split']=False;atomic_json(job,request)
            result=process_job(job);self.assertEqual(result['status'],'skipped');self.assertIn('comprimibili',result['error'])
            self.assertFalse(list((root/'DAT').rglob('*.dat')));self.assertTrue(source.is_file())
            table=pq.read_table(source);rows=table.to_pylist();rows[100]['quality']='Bad';pq.write_table(pa.Table.from_pylist(rows),source)
            request['auto_split']=True;atomic_json(job,request);result=process_job(job)
            self.assertEqual(result['status'],'skipped');self.assertGreater(result['issues']['bad_or_non_numeric'],0)
            self.assertFalse(list((root/'DAT').rglob('*.dat')))

    def test_disk_or_generic_writer_errors_never_trigger_capacity_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            _,job,_,_=noisy_fixture(d,count=100)
            with patch('pda3.window_export.PDA3TemplateWriter.write',side_effect=OSError('disco pieno')),patch('pda3.segmented_export.write_segmented') as fallback:
                result=process_job(job);self.assertEqual(result['status'],'skipped');self.assertIn('disco pieno',result['error']);fallback.assert_not_called()

    def test_insufficient_split_space_publishes_no_native_file(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job,_,_=noisy_fixture(root)
            with patch('pda3.segmented_export.shutil.disk_usage',return_value=__import__('collections').namedtuple('usage','total used free')(1,1,0)):
                result=process_job(job)
            self.assertEqual(result['status'],'skipped');self.assertIn('spazio insufficiente',result['error'])
            self.assertTrue(source.is_file());self.assertFalse(list((root/'DAT').rglob('*.dat')))

    def test_interval_export_packages_all_parts_and_manifests(self):
        from pda3.historical_export import export_interval
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,_,data,nodes=noisy_fixture(root,count=2100)
            atomic_json(source.with_suffix('.metadata.json'),{'signals':nodes})
            result=export_interval(RecordingLibrary(root),'Parquet/noisy.parquet',[n['nodeid'] for n in nodes],BASE,BASE+20990,root,root/'Exports')
            self.assertEqual(result['rows'],18900);self.assertEqual(result['dat_files'],3)
            with zipfile.ZipFile(result['path']) as z:
                self.assertIsNone(z.testzip());self.assertEqual(len([n for n in z.namelist() if n.endswith('.dat')]),3)
                self.assertEqual(len([n for n in z.namelist() if n.endswith('.json')]),4)

if __name__=='__main__':unittest.main(verbosity=2)
