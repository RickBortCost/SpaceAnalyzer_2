"""Isolated exporter/recording tests. FakeNative never produces a real iba file."""
import json,math,struct,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_binary_recordings import Recorder,RecordingLibrary,sample,NODES,BASE,pq
from iba_dat import export_parquet,IBABridge,bridge

class FakeNative:
    def __init__(self):self.jobs=[]
    def write(self,job,path):
        with Path(job).open('rb') as stream:
            size=struct.unpack('<I',stream.read(4))[0];header=json.loads(stream.read(size));values=[]
            for c in header['channels']:values.append(struct.unpack('<'+str(c['count'])+'f',stream.read(c['count']*4)))
            assert not stream.read(1)
        self.jobs.append((header,values));Path(path).write_bytes(b'TEST MOCK NOT IBA DAT\n'+b'x'*(sum(c['count'] for c in header['channels'])*4))
        return {'ok':True,'native':False,'simulated':True,'licensed':True,'roundtrip_verified':True}

class Format(unittest.TestCase):
    def original(self,d,times=None):
        r=Recorder(d,'test');r.start('parquet',NODES,sample_ms=1)
        for i,t in enumerate(times or [BASE+i for i in range(100)]):
            r.enqueue(t,[sample(math.sin(i*.1)),sample(2**64-1-i,NODES[1])],1)
        state=r.stop(timeout=15);self.assertEqual(state['state'],'stopped');return Path(state['path'])

    def test_float32_quality_timestamps_and_lossless_originals(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('parquet',NODES,sample_ms=1)
            r.enqueue(BASE,[sample(.123456789123),sample(2**64-1,NODES[1])],1)
            r.enqueue(BASE+1,[sample(3.,quality='Bad')],1)
            r.enqueue(BASE+2,[sample(4.,quality='Uncertain')],1)
            end=r.stop(timeout=15);original=Path(end['path']);before=original.read_bytes()
            native=FakeNative();files=export_parquet(original,NODES,native_bridge=native)
            self.assertEqual(original.read_bytes(),before)
            header,values=native.jobs[0];self.assertEqual(len(header['channels']),6)
            self.assertEqual(values[1],(3.,1.,2.));self.assertEqual(values[2][0],0.)
            self.assertAlmostEqual(values[2][1],.001,places=8)
            self.assertNotEqual(values[0][0],.123456789123)
            original_rows=pq.read_table(original).to_pylist()
            self.assertEqual(original_rows[1]['value_text'],str(2**64-1))
            self.assertEqual(original_rows[0]['ads_filetime'],sample(1)['source_filetime'])
            self.assertEqual(original_rows[0]['source_timestamp'],sample(1)['source_ts'])
            replay=list(RecordingLibrary(d)._iter_rows(Path(files[0]['path'])))
            self.assertEqual(replay,original_rows)

    def test_rotation_preserves_late_duplicate_samples_and_whole_cycles(self):
        with tempfile.TemporaryDirectory() as d:
            original=self.original(d,[BASE+5,BASE,BASE+1,BASE+1,BASE+3,BASE+2]*10)
            native=FakeNative();files=export_parquet(original,NODES,400,native_bridge=native)
            self.assertGreater(len(files),1);self.assertTrue(all(f['bytes']<=400 for f in files))
            replay=[r for f in files for r in RecordingLibrary(d)._iter_rows(Path(f['path']))]
            raw=pq.read_table(original).to_pylist()
            self.assertEqual(sorted((r['cycle'],r['node_id']) for r in replay),sorted((r['cycle'],r['node_id']) for r in raw))
            for f in files:
                rows=list(RecordingLibrary(d)._iter_rows(Path(f['path'])))
                self.assertTrue(all(sum(r['cycle']==i for r in rows)==2 for i in {r['cycle'] for r in rows}))

    def test_float32_overflow_keeps_archive(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('parquet',NODES);r.enqueue(BASE,[sample(1e200)],1)
            p=Path(r.stop(timeout=15)['path'])
            with self.assertRaisesRegex(ValueError,'Float32'):export_parquet(p,NODES,native_bridge=FakeNative())
            self.assertEqual(pq.read_table(p).num_rows,1);self.assertFalse(list(Path(d).glob('*.dat')))

    def test_companion_missing_modified_and_traversal_rejected(self):
        for mode in ('missing','modified','traversal'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                p=self.original(d);f=export_parquet(p,NODES,native_bridge=FakeNative())[0];dat=Path(f['path'])
                if mode=='missing':p.rename(p.with_suffix('.moved'))
                elif mode=='modified':p.write_bytes(p.read_bytes()+b'changed')
                else:
                    sidecar=dat.with_suffix('.metadata.json');meta=json.loads(sidecar.read_text());meta['original_archive']='../elsewhere.parquet';sidecar.write_text(json.dumps(meta))
                with self.assertRaises(ValueError):list(RecordingLibrary(d)._iter_rows(dat))

    def test_existing_dat_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.original(d);files=export_parquet(p,NODES,native_bridge=FakeNative());before=Path(files[0]['path']).read_bytes()
            with self.assertRaisesRegex(ValueError,'esistente'):export_parquet(p,NODES,native_bridge=FakeNative())
            self.assertEqual(Path(files[0]['path']).read_bytes(),before)

    def test_check_requires_native_license_and_complete_roundtrip(self):
        good={'ok':True,'licensed':True,'native_windows_test':True,'samples_written':512,'samples_read':512,'float32_exact':True,'timebase_matches':True}
        for key,bad in [('licensed',False),('float32_exact',False),('timebase_matches',False),('native_windows_test',False),('samples_read',511)]:
            b=IBABridge()
            with patch.object(b,'_invoke',return_value={**good,key:bad}) as invoke:
                self.assertFalse(b.check()['ok']);self.assertFalse(b.check()['ok']);self.assertEqual(invoke.call_count,2)
        b=IBABridge()
        with patch.object(b,'_invoke',return_value=good) as invoke:
            self.assertTrue(b.require()['ok']);b.require_prepared();self.assertTrue(b.require()['ok']);self.assertEqual(invoke.call_count,1)

    def test_negative_license_blocks_before_original_file_creation(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bridge,'last',{}):
            with self.assertRaises(ValueError):Recorder(d,'test').start('dat',NODES)
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_channel_limit_rejected_before_original_file_creation(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bridge,'require_prepared'):
            with self.assertRaisesRegex(ValueError,'64'):Recorder(d,'test').start('dat',[{'nodeid':str(i)} for i in range(65)])
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_isolated_worker_reports_unavailable_native_runtime(self):
        import os
        if os.name=='nt':self.skipTest('This check expects the non-Windows test host')
        from iba_recording import export_in_worker
        with tempfile.TemporaryDirectory() as d:
            p=self.original(d)
            with self.assertRaisesRegex(ValueError,'Windows'):export_in_worker(p,NODES,0)
            self.assertTrue(p.is_file());self.assertFalse(list(Path(d).glob('*.dat')))

    def test_record_stop_and_native_failure_keeps_original(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bridge,'require_prepared'),patch('iba_recording.export_in_worker',side_effect=ValueError('Licensed = false')):
            r=Recorder(d,'test');r.start('dat',NODES);r.enqueue(BASE,[sample(1.)],1);end=r.stop(timeout=15)
            self.assertEqual(end['state'],'error');self.assertIn('originale conservato',end['error'])
            self.assertEqual(pq.read_table(end['original_path']).num_rows,1)

    def test_record_stop_and_replay(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bridge,'require_prepared'),patch('iba_recording.export_in_worker',side_effect=lambda p,n,l:export_parquet(p,n,l,native_bridge=FakeNative())):
            r=Recorder(d,'test');r.start('dat',NODES,sample_ms=1)
            for i in range(256):
                rows=[sample(math.sin(2*math.pi*125*i/1000)),sample(i,NODES[1])]
                for row in rows:row['source_filetime']=str(int(sample(0)['source_filetime'])+i*10000)
                r.enqueue(BASE+i,rows,1)
            end=r.stop(timeout=15);self.assertEqual(end['state'],'stopped',end);self.assertEqual(end['iba_phase'],'ready')
            library=RecordingLibrary(d);data=library.load(end['filename']);self.assertEqual(data['numeric_rows'],512)
            from analysis_tools import spectrum,export_rows
            dat=Path(end['path']);rows=list(library._iter_rows(dat))
            fft=spectrum(rows,NODES[0]['nodeid'],BASE,BASE+255,options={'block_size':128})
            self.assertEqual(fft['samples_original'],256)
            self.assertEqual(fft['sample_hz'],1000);self.assertAlmostEqual(fft['peak_hz'],125)
            self.assertEqual(export_rows(rows,Path(d)/'range.csv','csv'),512)

    def test_http_simulation_flags_cannot_grant_license(self):
        from test_ads import app
        http=app.app.test_client();headers={'X-OPCUA-Token':app.SESSION_TOKEN}
        with patch.object(bridge,'_invoke',side_effect=ValueError('licenza assente')):
            result=http.post('/api/record/iba/check',json={'simulate':True,'licensed':True,'native':True},headers=headers).get_json()
            self.assertFalse(result['check']['ok']);self.assertFalse(result['check']['licensed'])

    def test_mixed_frequencies_are_not_resampled(self):
        with tempfile.TemporaryDirectory() as d:
            r=Recorder(d,'test');r.start('parquet',NODES,sample_ms=1)
            for i in range(100):r.enqueue(BASE+i,[sample(i)]+([sample(i,NODES[1])] if i%10==0 else []),1)
            p=Path(r.stop(timeout=15)['path']);fake=FakeNative();export_parquet(p,NODES,native_bridge=fake)
            channels=fake.jobs[0][0]['channels'];self.assertEqual(channels[0]['count'],100);self.assertEqual(channels[3]['count'],10)
            self.assertAlmostEqual(channels[0]['period'],.001,places=8);self.assertAlmostEqual(channels[3]['period'],.01,places=8)

if __name__=='__main__':unittest.main(verbosity=2)
