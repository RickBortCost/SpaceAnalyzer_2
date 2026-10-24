"""Phase 1: fault injection and numbered-source integrity, no PLC claims."""
import errno
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from acquisition_engine import AcquisitionEngine
from acquisition_health import EventStore
from primary_format import inspect,iter_samples,recover,typed,untyped,FILE,IPC,DATA,json_bytes
from primary_recording import PrimaryRecorder
from primary_recovery import RecoveryService
from durable_recording import DurableRecorder


def until(predicate,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Timeout')


class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.events=EventStore(self.root/'events.sqlite');self.engines=[];self.recorders=[]
    def tearDown(self):
        for e in self.engines:e.close()
        for r in self.recorders:r.stop(5)
        self.events.close();self.tmp.cleanup()
    def recorder(self,nodes=None,**kw):
        r=PrimaryRecorder(self.root/'.primary',flush_ms=20,events=self.events,**kw)
        r.start(nodes or [dict(nodeid='ads:x')]);self.recorders.append(r);return r
    def engine(self,r,display=lambda *a:None,**kw):
        e=AcquisitionEngine(lambda t,rows,ms,g:r.enqueue(t,rows,ms),display,r.fail,self.events,**kw)
        self.engines.append(e);return e
    def test_typed_values_precision_and_original_times(self):
        from decimal import Decimal
        from datetime import datetime,timezone
        import uuid
        values=[2**64-1,-2**63,float('-0.0'),float('inf'),float('nan'),b'\x00\xff',Decimal('1.23000000000000000001'),datetime.now(timezone.utc),uuid.uuid4(),{'$int':'plain string'},(1,True,None)]
        r=self.recorder()
        for i,value in enumerate(values):
            self.assertTrue(r.enqueue(i,[dict(nodeid='ads:x',value='UI value',_source_typed=typed(value),received_ns=1700000000123456789,source_filetime='133400000001234567',source_ts='2026-01-01T00:00:00.1234567+00:00')],10))
        state=r.stop(10);self.assertEqual(state['integrity'],'complete');rows=list(iter_samples(state['path']))
        for value,row in zip(values,rows):
            self.assertEqual(typed(row['source_value']),typed(value));self.assertEqual(row['received_ns'],'1700000000123456789')
            self.assertEqual(row['source_filetime'],'133400000001234567')
        self.assertEqual(inspect(state['path'])['samples'],len(values))
    def test_four_sources_continue_while_live_is_blocked(self):
        protocols=['ads','opcua','mqtt','s7'];nodes=[dict(nodeid=(p+':x') if p!='opcua' else 'ns=2;s=x') for p in protocols]
        r=self.recorder(nodes);gate=threading.Event();entered=threading.Event()
        def display(*a):entered.set();gate.wait(5)
        e=self.engine(r,display)
        try:
            for cycle in range(100):
                for p,n in zip(protocols,nodes):
                    self.assertTrue(e.submit(p,p,cycle,[dict(nodeid=n['nodeid'],value=cycle,source_sequence=cycle+1,source_epoch=1)],10,(1,1)))
            self.assertTrue(e.flush());self.assertTrue(r.flush());self.assertTrue(entered.is_set())
            self.assertEqual(r.status()['durable_samples'],400)
        finally:gate.set()
        path=r.stop(10)['path'];rows=list(iter_samples(path));self.assertEqual(len(rows),400)
        for node in nodes:self.assertEqual([x['value'] for x in rows if x['nodeid']==node['nodeid']],list(range(100)))
        self.assertEqual(len({x['connection_id'] for x in rows}),4)
    def test_display_saturation_marks_loss_without_losing_originals(self):
        r=self.recorder();gate=threading.Event();entered=threading.Event();drops=[]
        def display(*a):entered.set();gate.wait(5)
        e=self.engine(r,display);e.live.capacity=1;e.live.on_drop=lambda item,count:drops.append(count)
        try:
            e.submit('ads','ads',0,[dict(nodeid='ads:x',value=0)],10);until(entered.is_set)
            for i in range(1,50):e.submit('ads','ads',i,[dict(nodeid='ads:x',value=i)],10)
            self.assertTrue(e.flush());self.assertTrue(r.flush());self.assertEqual(r.status()['durable_samples'],50)
            self.assertGreater(e.live.skipped,0);self.assertEqual(sum(drops),e.live.skipped)
        finally:gate.set()
        self.assertEqual(inspect(r.stop(10)['path'])['samples'],50)

    def test_recovery_does_not_touch_active_writer(self):
        r=self.recorder();r.enqueue(1,[dict(nodeid='ads:x',value=1)],10);self.assertTrue(r.flush())
        before=r.path.stat().st_size;result=recover(r.path)
        self.assertEqual(result['state'],'writer_active');self.assertEqual(r.path.stat().st_size,before)
        self.assertTrue(r.accepting)

    def test_offline_csv_export_uses_existing_schema_and_preserves_original(self):
        import csv,subprocess
        r=self.recorder()
        for i in range(20):r.enqueue(1700000000000+i*10,[dict(nodeid='ads:x',value=i,quality='Good',data_type='Int32')],10)
        path=Path(r.stop(10)['path']);original=path.read_bytes();output=self.root/'export'
        result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'primary_tools.py'),'export',str(path),'--format','csv','--output',str(output)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        exported=json.loads(result.stdout)
        with Path(exported['path']).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual([float(row['value_numeric']) for row in rows],list(range(20)))
        self.assertEqual(path.read_bytes(),original)

    def test_consumer_backpressure_absorbs_burst_without_enlarging_buffer(self):
        r=self.recorder();e=self.engine(r,max_samples=2,max_bytes=4096)
        def slow_record(t,rows,ms,g):time.sleep(.005);return r.enqueue(t,rows,ms)
        e.record=slow_record
        for i in range(30):
            self.assertTrue(e.submit('mqtt','mqtt',i,[dict(nodeid='ads:x',value=i)],10,backpressure_timeout=.2))
        self.assertTrue(e.flush());state=e.status()['connections']['mqtt']
        self.assertEqual(state['lost'],0);self.assertGreater(state['backpressure_waits'],0)
        self.assertLessEqual(state['buffer']['high_water'],2)
        self.assertEqual(inspect(r.stop(10)['path'])['samples'],30)

    def test_mqtt_object_remains_typed_before_ui_string_conversion(self):
        from mqtt_source import MQTTClient,DEFAULTS,validate_config
        nid='mqtt:objects#';r=self.recorder([dict(nodeid=nid)])
        client=MQTTClient(validate_config(dict(DEFAULTS)))
        client.sink=lambda t,rows,ms,g:r.enqueue(t,rows,ms)
        value={'array':[1,2**64-1,True],'text':'Ω'}
        client._decode('objects',json.dumps(value).encode(),1700000000000,False,False,([nid],10,(1,1)))
        row=list(iter_samples(r.stop(10)['path']))[0]
        self.assertEqual(row['source_value'],value);self.assertEqual(row['data_type'],'JSONObject')
        self.assertIsInstance(row['value'],str)

    def test_primary_backpressure_drains_before_declaring_overflow(self):
        r=self.recorder(max_samples=2);write=r._write
        def slow_write(*args):time.sleep(.005);return write(*args)
        with patch.object(r,'_write',side_effect=slow_write):
            for i in range(30):self.assertTrue(r.enqueue(i,[dict(nodeid='ads:x',value=i)],10,backpressure_timeout=.2))
            state=r.stop(10)
        self.assertEqual(state['durable_samples'],30);self.assertEqual(state['lost_samples'],0)
        self.assertLessEqual(state['queue_high_water'],2);self.assertGreater(state['backpressure_waits'],0)

    def test_deferred_rebuild_preserves_each_available_format(self):
        from recording_manager import RecordingManager
        for fmt in ('csv','parquet','das','rbf'):
            with self.subTest(format=fmt):
                manager=RecordingManager(self.root/fmt,'test',factory=DurableRecorder)
                from recordings import prepare_format
                options={}
                prepare_format(fmt,options)
                manager.start(fmt,[dict(nodeid='ads:x',display_name='Signal')],sample_ms=10,options=options)
                rec=manager.latest
                try:
                    with patch.object(rec.external,'enqueue',return_value=False):
                        for i in range(20):self.assertTrue(manager.enqueue(1700000000000+i*10,[dict(nodeid='ads:x',value=i,quality='Good',good=True,plot_value=i,data_type='Double')],10))
                        until(lambda:bool(rec.external_error))
                    manager.stop(timeout=0)
                finally:manager.close()
                state=rec.status();self.assertTrue(state['external_rebuilt_from_primary'],state)
                self.assertEqual(state['rows_written'],20);self.assertFalse(state['error'])
                self.assertTrue(all((self.root/fmt/f['name']).is_file() for f in state['files']))

    def test_late_format_write_error_rebuilds_after_primary_is_sealed(self):
        from recording_manager import RecordingManager
        manager=RecordingManager(self.root/'late-error','test',factory=DurableRecorder)
        manager.start('csv',[dict(nodeid='ads:x')],sample_ms=10);rec=manager.latest
        try:
            with patch.object(rec.external,'_flush',side_effect=OSError('injected finalization disk error')):
                for i in range(20):manager.enqueue(1700000000000+i*10,[dict(nodeid='ads:x',value=i,quality='Good',data_type='Double')],10)
                manager.stop(timeout=0);until(lambda:bool(rec.external_error))
        finally:manager.close()
        self.assertTrue(rec.status()['external_rebuilt_from_primary']);self.assertEqual(rec.status()['rows_written'],20)

    def test_failed_rebuild_remains_explicit_and_keeps_primary(self):
        from recording_manager import RecordingManager
        manager=RecordingManager(self.root/'export-error','test',factory=DurableRecorder)
        manager.start('csv',[dict(nodeid='ads:x')],sample_ms=10);rec=manager.latest
        with patch.object(rec.external,'enqueue',return_value=False):
            for i in range(20):manager.enqueue(i,[dict(nodeid='ads:x',value=i)],10)
            until(lambda:bool(rec.external_error))
        with patch('durable_recording.subprocess.Popen',side_effect=OSError('export worker unavailable')):
            manager.stop(timeout=0);manager.close()
        state=rec.status();self.assertEqual(state['state'],'error');self.assertIn('export worker unavailable',state['error'])
        self.assertEqual(inspect(state['primary']['path'])['samples'],20);self.assertEqual(state['primary']['integrity'],'complete')

    def test_primary_overflow_is_terminal_explicit_and_bounded(self):
        r=self.recorder(max_samples=1)
        self.assertFalse(r.enqueue(0,[dict(nodeid='ads:x',value=i) for i in range(2)],10))
        state=r.stop(10);self.assertEqual(state['lost_samples'],2);self.assertEqual(state['integrity'],'incomplete')
        self.assertIn('Overflow',state['error']);self.assertEqual(state['received_samples'],0)
    def test_acquisition_overflow_has_source_event(self):
        r=self.recorder();e=self.engine(r,max_samples=1,max_bytes=4096)
        self.assertFalse(e.submit('ads-plc','ads',0,[dict(nodeid='ads:x',value=1)]*2,10))
        self.assertEqual(e.status()['connections']['ads-plc']['lost'],2)
        self.assertTrue(any(x['kind']=='acquisition_overflow' for x in self.events.recent))
    def test_killed_process_and_automatic_recovery_verified_prefix(self):
        r=self.recorder()
        for i in range(200):self.assertTrue(r.enqueue(i,[dict(nodeid='ads:x',value=i)],10))
        self.assertTrue(r.flush());durable=r.status()['durable_samples'];original=r.path
        r.process.kill();r.process.wait();until(r.done.is_set)
        self.assertTrue(r.error);service=RecoveryService(self.root/'.primary',self.events);service.start();service.thread.join(10)
        result=service.status()['results'][0];self.assertTrue(result['recovered']);self.assertTrue(result['unknown_uncommitted_loss'])
        recovered=Path(result['output']);self.assertEqual(inspect(recovered)['samples'],durable)
        self.assertEqual([x['value'] for x in iter_samples(recovered)],list(range(durable)))
        self.assertTrue(original.exists());self.assertEqual(recover(original)['output'],str(recovered))
    def test_checksum_tail_corruption_and_header_failure(self):
        r=self.recorder();r.enqueue(1,[dict(nodeid='ads:x',value=1)],10);state=r.stop(10)
        data=Path(state['path']).read_bytes();path=self.root/'broken.sac.part';path.write_bytes(data[:-7])
        report=recover(path);self.assertEqual(report['samples'],1);self.assertGreater(report['discarded_bytes'],0)
        self.assertTrue(inspect(report['output'])['finalized']);self.assertTrue(report['unknown_uncommitted_loss'])
        path2=self.root/'noheader.sac.part';path2.write_bytes(b'bad');service=RecoveryService(self.root,self.events);service.start();service.thread.join(10)
        self.assertTrue(any(x.get('integrity')=='unrecoverable_header_or_io_error' for x in service.results))
    def test_reconnect_epoch_and_sequence_gap_are_separate(self):
        r=self.recorder();e=self.engine(r)
        for epoch,sequence in [(1,1),(1,2),(2,1),(2,3)]:e.submit('ads','ads',sequence,[dict(nodeid='ads:x',value=sequence,source_epoch=epoch,source_sequence=sequence)],10)
        self.assertTrue(e.flush());status=e.status()['connections']['ads'];self.assertEqual(status['sequence_gaps'],1)
        self.assertTrue(r.error);self.assertTrue(any(x['kind']=='source_sequence_gap' for x in self.events.recent))
    def test_writer_disk_full_is_explicit_and_prefix_recoverable(self):
        import primary_worker
        from primary_format import FIELDS
        path=self.root/'disk.sac.part';cfg=json_bytes(dict(path=str(path),metadata=dict(channels=[dict(nodeid='ads:x')]),flush_ms=20))
        row=[None]*len(FIELDS);row[2]=0;payload=json_bytes([row])
        feed=io.BytesIO(struct.pack('<I',len(cfg))+cfg+IPC.pack(DATA,1,1,time.monotonic_ns(),len(payload))+payload)
        replies=[]
        with patch.object(primary_worker,'read_exact',side_effect=lambda fd,n:feed.read(n)),patch.object(primary_worker,'send',side_effect=replies.append),patch.object(primary_worker,'write_block',side_effect=OSError(errno.ENOSPC,'simulated disk full')):
            with self.assertRaises(OSError):primary_worker.run()
        self.assertEqual(replies[-1]['op'],'error');self.assertIn('disk full',replies[-1]['error']);self.assertEqual(inspect(path)['samples'],0)
        self.assertTrue(recover(path)['recovered'])
    def test_secondary_failure_does_not_stop_primary_and_next_record_overlaps(self):
        from recording_manager import RecordingManager
        manager=RecordingManager(self.root,'test',factory=DurableRecorder)
        try:
            manager.start('csv',[dict(nodeid='ads:x')],sample_ms=10)
            first=manager.latest
            with patch.object(first.external,'enqueue',return_value=False):
                for i in range(100):self.assertTrue(manager.enqueue(i,[dict(nodeid='ads:x',value=i,good=True,quality='Good',data_type='Int32')],10))
                until(lambda:bool(first.external_error))
            self.assertTrue(first.primary.accepting);self.assertEqual(first.primary.status()['received_samples'],100)
            manager.stop(timeout=0);manager.start('csv',[dict(nodeid='ads:x')],sample_ms=10)
            self.assertTrue(manager.enqueue(101,[dict(nodeid='ads:x',value=101,good=True,quality='Good',data_type='Int32')],10))
            manager.stop(timeout=0)
        finally:manager.close()
        self.assertEqual(inspect(first.primary.status()['path'])['samples'],100)
        self.assertTrue(first.status()['external_rebuilt_from_primary'],first.status())
        self.assertEqual(first.status()['rows_written'],100);self.assertFalse(first.status()['error'])

if __name__=='__main__':unittest.main()
