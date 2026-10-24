"""Short-stop edge cases and upgrade of rejected V1.18.0 recordings."""
import json,tempfile,time,unittest
from pathlib import Path
from test_pda3 import fixture,BASE,NODES
from recordings import Recorder,prepare_format
from recording_reader import RecordingLibrary
from pda3.recording import recover_pending
from pda3.window_export import process_job
from pda3.storage import atomic_json,digest

class Lifecycle(unittest.TestCase):
    def test_empty_stop_is_normal_without_fake_dat(self):
        with tempfile.TemporaryDirectory() as d:
            prepare_format('pda3');r=Recorder(d,'test');r.start('pda3',NODES,sample_ms=10,options={'pda3_accept_template_time':True})
            end=r.stop(timeout=15);self.assertEqual(end['state'],'stopped');self.assertEqual(end['error'],'');self.assertFalse(end['busy'])
            self.assertFalse(list(Path(d).rglob('*.dat')))

    def test_one_sample_replay_accepts_same_start_and_end(self):
        with tempfile.TemporaryDirectory() as d:
            _,job=fixture(d,count=1);rec=process_job(job);self.assertEqual(rec['status'],'ready')
            name=(job.parent/'window.dat').relative_to(d).as_posix()
            result=RecordingLibrary(d).load(name,[NODES[0]['nodeid']],BASE,BASE)
            self.assertEqual(result['rows_scanned'],1);self.assertEqual(len(result['series'][NODES[0]['nodeid']]),1)

    def test_old_short_stop_retried_only_with_finalized_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source,job=fixture(d,count=1000);before=digest(source)
            request=json.loads(job.read_text());request.update(state='skipped',last_error='Finestra incompleta o con gap: richiesti 60000 campioni distinti per ogni segnale')
            request.pop('allow_partial');atomic_json(job,request)
            atomic_json(source.with_suffix('.metadata.json'),{'state':'stopped','reason':'user','error':''})
            recovery=recover_pending(root);deadline=time.monotonic()+25
            while recovery['running'] and time.monotonic()<deadline:time.sleep(.05)
            self.assertFalse(recovery['running']);self.assertEqual(recovery['errors'],[]);self.assertEqual(recovery['recovered'],1)
            result=json.loads(job.read_text());self.assertEqual(result['state'],'ready');self.assertTrue(result['upgraded_short_stop'])
            manifest=json.loads((job.parent/'window.json').read_text());self.assertEqual(manifest['expected_samples_per_channel'],1000)
            self.assertEqual(digest(source),before);self.assertTrue((job.parent/'window.dat').is_file())

if __name__=='__main__':unittest.main(verbosity=2)
