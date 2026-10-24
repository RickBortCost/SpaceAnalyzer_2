"""A blocked old disk writer cannot block or corrupt the next acquisition."""
import csv
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from test_recordings import Recorder,NODES,ROWS
from recording_manager import RecordingManager
from recording_reader import RecordingLibrary

class Sessions(unittest.TestCase):
    def test_new_session_while_previous_writer_is_blocked(self):
        gate=threading.Event();entered=threading.Event();run=Recorder._run
        def blocked(rec):
            if rec.metadata.get('session_name')=='previous':entered.set();gate.wait(10)
            return run(rec)
        with tempfile.TemporaryDirectory() as folder,patch.object(Recorder,'_run',blocked):
            manager=RecordingManager(folder,'test')
            try:
                first=manager.start('csv',NODES[:1],sample_ms=10,options={'session_name':'previous'})
                self.assertTrue(entered.wait(2))
                for i in range(20):manager.enqueue(1700000000000+i*10,ROWS[:1],10)
                stop=manager.stop(timeout=0)
                self.assertTrue(stop['busy']);self.assertFalse(stop['selection_locked']);self.assertTrue(stop['can_start'])
                self.assertEqual(stop['finalizing_count'],1)
                second=manager.start('parquet',NODES[1:],sample_ms=10)
                self.assertNotEqual(second['recording_id'],first['recording_id'])
                for i in range(30):manager.enqueue(1700000001000+i*10,ROWS[1:],10)
                self.assertTrue(manager.acquiring)
                with self.assertRaises(ValueError):manager.start('csv',NODES)
                manager.stop(timeout=0);gate.set();manager.stop(timeout=10)
                status=manager.status();self.assertEqual(status['recording_id'],second['recording_id'])
                self.assertFalse(status['busy']);self.assertFalse(status['selection_locked'])
                jobs=status['finalizations'];self.assertEqual(len(jobs),2)
                for job,node,count in [(jobs[0],NODES[0],20),(jobs[1],NODES[1],30)]:
                    result=RecordingLibrary(folder).load(Path(job['path']).name)
                    self.assertEqual(set(result['series']),{node['nodeid']})
                    self.assertEqual(result['signals'][0]['points_original'],count)
                # Completed status remains available after the underlying queues
                # and worker objects have been released from the history.
                self.assertEqual(manager.status()['state'],'stopped')
            finally:gate.set();manager.close()

    def test_error_of_older_session_does_not_fail_newer_session(self):
        gate=threading.Event();real=Recorder._flush
        def flush(rec,rows):
            if rec.metadata.get('session_name')=='fail':gate.wait(10);raise OSError('test old disk error')
            return real(rec,rows)
        with tempfile.TemporaryDirectory() as folder,patch.object(Recorder,'_flush',flush):
            manager=RecordingManager(folder,'test')
            try:
                manager.start('csv',NODES,sample_ms=10,options={'session_name':'fail'})
                manager.enqueue(1700000000000,ROWS,10);manager.stop(timeout=0)
                manager.start('csv',NODES,sample_ms=10);manager.enqueue(1700000001000,ROWS,10)
                gate.set();manager.stop(timeout=10)
                result=manager.status();self.assertEqual(result['state'],'stopped');self.assertEqual(result['error'],'')
                self.assertIn('test old disk error',result['finalizations'][0]['error'])
                self.assertTrue(Path(result['finalizations'][0]['path']).name.endswith('.part'))
            finally:gate.set();manager.close()

    def test_close_drains_all_detached_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=RecordingManager(folder,'test')
            for fmt in ('csv','parquet','das','rbf'):
                manager.start(fmt,NODES,sample_ms=10)
                for i in range(10):manager.enqueue(1700000000000+i*10,ROWS,10)
                manager.stop(timeout=0)
            manager.close();self.assertFalse(manager.busy)
            self.assertEqual(len(manager.status()['finalizations']),4)
            self.assertTrue(all(j['state']=='stopped' for j in manager.status()['finalizations']))
            with self.assertRaises(ValueError):manager.start('csv',NODES)

    def test_rotated_fda_parts_are_converted_before_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            rec=Recorder(folder,'test')
            try:
                rec.start('das',NODES,sample_ms=10,options={'max_duration_s':.02,'max_file_mb':0})
                rec.fda_session.original.storage['flush_seconds']=.02
                for i in range(20):
                    rec.enqueue(1700000000000+i*10,ROWS,10);time.sleep(.012)
                deadline=time.monotonic()+5
                while not rec.status()['files'] and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(rec.status()['files']);self.assertTrue(rec.status()['active'])
                result=rec.stop(timeout=10);self.assertEqual(result['state'],'stopped')
                total=sum(RecordingLibrary(folder).load(f['name'])['numeric_rows'] for f in result['files'])
                self.assertEqual(total,40)
            finally:rec.stop(timeout=10)

if __name__=='__main__':unittest.main(verbosity=2)
