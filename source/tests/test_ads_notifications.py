"""Native callback ABI and lifecycle simulation; no Windows/hardware claim."""
import ctypes as C
import csv
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ads import app
from test_ads_native import DLL,Function
from ads_native import configure_library,AmsAddr
from ads_notifications import NotificationAcquisition,NotificationHeader,NotificationAttrib,FILETIME_EPOCH,timestamp
from recordings import Recorder
import pyarrow.parquet as pq

class NotifyDLL(DLL):
    def __init__(self):
        super().__init__();self.subs={};self.deleted=[];self.attrs=[];self.failed_add=0
        self.AdsSyncAddDeviceNotificationReqEx=Function(self.add)
        self.AdsSyncDelDeviceNotificationReqEx=Function(self.delete)
    def AdsSyncReadStateReqEx(self,p,a,state,device):
        C.memmove(state,struct.pack("<H",self.peer.ads_state),2);C.memmove(device,b"\0\0",2);return 0
    def add(self,port,addr,group,handle,attr,callback,user,out):
        a=C.cast(attr,C.POINTER(NotificationAttrib)).contents
        self.attrs.append((a.length,a.mode,a.max_delay,a.cycle));assert group==0xF005
        if self.failed_add and len(self.attrs)>=self.failed_add:return 1796
        notification=user+1000;self.subs[notification]=(callback,user,handle,a.length)
        C.memmove(out,struct.pack('<I',notification),4);return 0
    def delete(self,port,addr,handle):
        self.deleted.append(handle);self.subs.pop(handle,None);return 0
    def emit(self,ticks,size_override=None):
        for notification,(cb,user,handle,size) in list(self.subs.items()):
            name=self.peer.handles[handle];data=self.peer.value(name)
            raw=struct.pack('<IQI',notification,ticks,size if size_override is None else size_override)+data
            buffer=C.create_string_buffer(raw)
            cb(C.pointer(AmsAddr()),C.cast(buffer,C.POINTER(NotificationHeader)),user)
    def AdsPortCloseEx(self,p):
        self.subs.clear();return super().AdsPortCloseEx(p)

def until(predicate,timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Timeout waiting for simulated acquisition')

class Cyclic(unittest.TestCase):
    def setUp(self):
        self.dll=NotifyDLL();configure_library(self.dll)
        self.mock=patch('ads_native.load_library',return_value=(self.dll,'fake64.dll'));self.mock.start()
        self.cfg={**app.DEFAULT_CONFIG,'source':'ads','ads_transport':'native','ads_mode':'notify'}
        self.selection=(['ads:MAIN.Wave','ads:MAIN.Running'],10,(1,1));self.samples=[];self.diagnostics={};self.workers=[]
    def tearDown(self):
        for w in self.workers:
            w.stop();w.thread.join(5);self.assertFalse(w.thread.is_alive())
        self.mock.stop()
    def worker(self,sink=None,**kw):
        w=NotificationAcquisition(self.cfg,lambda:self.selection,sink or (lambda *a:self.samples.append(a)),lambda d:self.diagnostics.update(d),**kw)
        self.workers.append(w);w.start();return w
    def test_burst_timestamps_records_and_shutdown(self):
        self.assertEqual(C.sizeof(NotificationHeader),16);self.assertEqual(NotificationHeader.timestamp.offset,4)
        self.assertEqual(C.sizeof(NotificationAttrib),16)
        for fmt in ('csv','parquet'):
            with tempfile.TemporaryDirectory() as tmp:
                rec=Recorder(tmp,'test');nodes=[{'nodeid':n,'display_name':n} for n in self.selection[0]]
                rec.start(fmt,nodes,sample_ms=10)
                w=self.worker(lambda ts,rows,ms,gen:(self.samples.append((ts,rows,ms,gen)),rec.enqueue(ts,rows,ms)))
                until(lambda:len(self.dll.subs)==2)
                base=FILETIME_EPOCH+1700000000*10000000+1234567
                for i in range(12):self.dll.emit(base+i*100000)
                until(lambda:sum(len(a[1]) for a in self.samples)>=24)
                until(lambda:self.diagnostics.get('source_period_ms') is not None)
                self.assertAlmostEqual(self.diagnostics['source_period_ms'],10)
                self.assertLess(self.diagnostics['arrival_period_ms'],10)
                self.assertTrue(all(a[1][0]['source_ts'].endswith('4567+00:00') for a in self.samples))
                self.assertTrue(all(a[1][0]['server_ts'] is None for a in self.samples))
                w.stop();w.thread.join(5);status=rec.stop()
                self.assertIsNone(w.error);self.assertFalse(self.dll.subs);self.assertFalse(self.dll.peer.handles)
                path=Path(status['path'])
                if fmt=='csv':
                    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
                else:rows=pq.read_table(path).to_pylist()
                self.assertEqual(len(rows),24)
                self.assertEqual(rows[0]['ads_filetime'],str(base))
                self.assertNotEqual(rows[0]['local_timestamp'],rows[0]['source_timestamp'])
                self.samples.clear()
        self.assertTrue(all(a[1:]==(3,100000,100000) for a in self.dll.attrs))
    def test_selection_change_online_change_and_nonrun_quality(self):
        w=self.worker();until(lambda:len(self.dll.subs)==2)
        self.selection=(['ads:MAIN.Running'],20,(2,1));until(lambda:len(self.dll.subs)==1 and len(self.dll.deleted)>=2)
        self.dll.peer.version+=1
        until(lambda:len(self.dll.deleted)>=3,3)
        until(lambda:len(self.dll.subs)==1)
        self.dll.peer.ads_state=6;until(lambda:self.diagnostics.get("ads_state")==6)
        self.dll.emit(FILETIME_EPOCH+1700000000*10000000)
        until(lambda:bool(self.samples))
        self.assertEqual(self.samples[-1][1][0]['quality'],'Uncertain')
    def test_one_ms_burst_keeps_every_sample_and_original_timestamp(self):
        self.selection=(self.selection[0],1,(1,1))
        for fmt in ('csv','parquet'):
            with tempfile.TemporaryDirectory() as tmp:
                rec=Recorder(tmp,'one-ms');nodes=[{'nodeid':n,'display_name':n} for n in self.selection[0]]
                rec.start(fmt,nodes,sample_ms=1,options={'buffer_seconds':10})
                self.assertEqual(rec.status()['queue_capacity_cycles'],10000)
                w=self.worker(lambda ts,rows,ms,gen:rec.enqueue(ts,rows,ms))
                until(lambda:len(self.dll.subs)==2)
                base=FILETIME_EPOCH+1700000000*10000000+1234567
                for i in range(1000):self.dll.emit(base+i*10000)
                until(lambda:self.diagnostics.get('source_period_ms')==1)
                w.stop();w.thread.join(5);status=rec.stop()
                self.assertIsNone(w.error);self.assertEqual(w.dropped,0);self.assertEqual(status['dropped_cycles'],0)
                if fmt=='csv':
                    with Path(status['path']).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
                else:rows=pq.read_table(status['path']).to_pylist()
                self.assertEqual(len(rows),2000)
                for nid in self.selection[0]:
                    signal=[r for r in rows if r['node_id']==nid]
                    self.assertEqual([int(r['ads_filetime']) for r in signal],[base+i*10000 for i in range(1000)])
                    self.assertTrue(all(int(r['sample_ms'])==1 for r in signal))
        self.assertTrue(all(a[1:]==(3,10000,10000) for a in self.dll.attrs))
    def test_slow_control_does_not_block_samples_and_timeout_recovers(self):
        self.selection=(self.selection[0],1,(1,1))
        original=self.dll.AdsSyncReadStateReqEx.fn
        calls=[0];entered=threading.Event()
        def delayed(*args):
            calls[0]+=1
            if calls[0]==2:
                entered.set();time.sleep(.5);return 0x745
            return original(*args)
        self.dll.AdsSyncReadStateReqEx.fn=delayed
        w=self.worker(capacity=128)
        until(entered.is_set)
        base=FILETIME_EPOCH+1700000000*10000000
        start=time.monotonic()
        for i in range(800):
            self.dll.emit(base+i*10000)
            time.sleep(max(0,start+(i+1)*.001-time.monotonic()))
        until(lambda:w.processed==1600)
        self.assertIsNone(w.error);self.assertEqual(w.dropped,0)
        self.assertEqual(w.control_timeouts,1);self.assertIsNotNone(w.control_error)
        # Keep the stream alive while the control backoff expires.
        while not w.control_last_ok:
            self.dll.emit(base+800*10000)
            self.assertIsNone(w.error)
            if time.monotonic()-start>8:self.fail('Control did not recover')
            time.sleep(.02)
        self.assertIsNone(w.control_error)
        self.assertEqual(w.restarts,0)

    def test_stop_waits_for_inflight_monitor_before_closing_native_port(self):
        original=self.dll.AdsSyncReadStateReqEx.fn
        calls=[0];entered=threading.Event()
        def delayed(*args):
            calls[0]+=1
            if calls[0]>1:
                self.dll.busy=True;entered.set();time.sleep(.2)
                self.dll.busy=False
            return original(*args)
        self.dll.AdsSyncReadStateReqEx.fn=delayed
        w=self.worker();until(entered.is_set)
        w.stop();w.thread.join(3)
        self.assertFalse(w.thread.is_alive());self.assertIsNone(w.error)
        self.assertFalse(self.dll.close_while_busy);self.assertFalse(self.dll.open)

    def test_partial_registration_cleanup(self):
        self.dll.failed_add=2;w=self.worker();until(lambda:w.error is not None)
        self.assertIn('Accesso non consentito',w.error);self.assertFalse(self.dll.subs);self.assertFalse(self.dll.open)
    def test_flask_reconnect_restores_selection(self):
        app.initialize();http=app.app.test_client();headers={'X-OPCUA-Token':app.SESSION_TOKEN}
        def post(url,data):
            r=http.post(url,json=data,headers=headers);self.assertEqual(r.status_code,200,r.get_json())
        try:
            post('/api/select',{'nodeids':['ads:MAIN.Running']});post('/api/connect',{**self.cfg,'sample_ms':1})
            until(lambda:len(self.dll.subs)==1)
            self.assertEqual(self.dll.attrs[-1][1:],(3,10000,10000))
            old_ports=set(self.dll.open)
            self.dll.emit(FILETIME_EPOCH+1700000000*10000000,size_override=999)
            until(lambda:app.runtime.state=='reconnecting')
            until(lambda:app.runtime.connected and len(self.dll.subs)==1 and set(self.dll.open)!=old_ports,6)
            self.dll.emit(FILETIME_EPOCH+1700000001*10000000)
            until(lambda:app.runtime.latest_values and app.runtime.latest_values[0].get('good'))
            self.assertEqual(list(app.runtime.selected),['ads:MAIN.Running'])
            self.assertTrue(any(p[2]=='Bad' for p in app.runtime.samples['ads:MAIN.Running']))
        finally:post('/api/disconnect',{})

    def test_invalid_payload(self):
        w=self.worker();until(lambda:len(self.dll.subs)==2)
        self.dll.emit(FILETIME_EPOCH+10000000,size_override=999)
        until(lambda:w.error is not None);self.assertIn('Dimensione',w.error)
    def test_queue_overflow_is_visible(self):
        w=NotificationAcquisition(self.cfg,lambda:self.selection,lambda *a:None,lambda d:None,capacity=1)
        w.members={1:('ads:MAIN.Running',{'size':1})};w.accepting=True
        raw=C.create_string_buffer(struct.pack('<IQI',1001,FILETIME_EPOCH+10000000,1)+b'\1')
        for _ in range(2):w._callback(None,C.cast(raw,C.POINTER(NotificationHeader)),1)
        self.assertEqual(w.dropped,1);self.assertIn('Overflow',w.callback_fault)
    def test_flask_acquisition_export_and_disconnect(self):
        app.initialize();http=app.app.test_client();headers={'X-OPCUA-Token':app.SESSION_TOKEN}
        def post(url,data):
            response=http.post(url,json=data,headers=headers)
            self.assertEqual(response.status_code,200,response.get_json());return response.get_json()
        try:
            post('/api/select',{'nodeids':['ads:MAIN.Wave','ads:MAIN.Running']})
            post('/api/connect',self.cfg)
            until(lambda:len(self.dll.subs)==2)
            base=FILETIME_EPOCH+time.time_ns()//100
            for i in range(32):self.dll.emit(base+i*100000)
            until(lambda:app.runtime.notification_count>=64)
            data=http.get('/api/values').get_json()
            self.assertEqual(len(data['samples']['ads:MAIN.Wave']),32)
            self.assertTrue(data['values'][0]['source_filetime'])
            until(lambda:getattr(app.runtime,'ads_notification_diag',{}).get('source_period_ms') is not None)
            self.assertAlmostEqual(app.runtime.ads_notification_diag['source_period_ms'],10)
            points=app.runtime.samples['ads:MAIN.Wave']
            rows=list(app.analysis_rows({'source':'live','signal_ids':['ads:MAIN.Wave'],'x1':points[0][0],'x2':points[-1][0]}))
            self.assertEqual(rows[0]['ads_filetime'],str(base))
            self.assertNotEqual(rows[0]['local_timestamp'],rows[0]['source_timestamp'])
            # Pause affects the browser only: no acquisition-side play/pause gate.
            self.dll.emit(base+32*100000)
            until(lambda:len(app.runtime.samples['ads:MAIN.Wave'])==33)
        finally:post('/api/disconnect',{})
        self.assertFalse(self.dll.subs);self.assertFalse(self.dll.open)

    def test_ui_mutex_does_not_block_native_consumer_or_diagnostic_report(self):
        app.initialize();http=app.app.test_client();headers={'X-OPCUA-Token':app.SESSION_TOKEN}
        def post(url,data):
            response=http.post(url,json=data,headers=headers);self.assertEqual(response.status_code,200,response.json);return response.json
        try:
            post('/api/select',{'nodeids':list(self.selection[0])});post('/api/connect',self.cfg)
            until(lambda:len(self.dll.subs)==2)
            post('/api/record/start',dict(format='csv',selected_nodes=[dict(nodeid=n) for n in self.selection[0]]))
            base=FILETIME_EPOCH+time.time_ns()//100
            with app.runtime.lock:
                time.sleep(.3) # Includes at least one periodic ADS report.
                for i in range(100):self.dll.emit(base+i*100000)
                until(lambda:app.recording.status().get('primary',{}).get('received_samples')==200)
                self.assertEqual(app.runtime.ads_notification_worker.dropped,0)
            post('/api/record/stop',{});until(lambda:not app.recording.busy)
            self.assertEqual(app.recording.status()['primary']['durable_samples'],200)
        finally:post('/api/disconnect',{})

    def test_clock_regression_preserves_dequeued_samples_and_exact_bytes(self):
        w=self.worker();until(lambda:len(self.dll.subs)==2)
        base=FILETIME_EPOCH+1700000000*10000000
        for ticks in (base,base+100000,base-100000):self.dll.emit(ticks)
        until(lambda:w.processed==6)
        self.assertIsNone(w.error);self.assertEqual(w.clock_backwards,2)
        self.assertEqual(sum(len(a[1]) for a in self.samples),6)
        for nid in self.selection[0]:
            rows=[r for _,batch,_,_ in self.samples for r in batch if r['nodeid']==nid]
            self.assertEqual([r['source_sequence'] for r in rows],[1,2,3])
            self.assertTrue(rows[-1]['clock_discontinuity']);self.assertIn('_source_binary_typed',rows[-1])

    def test_validation_and_timestamp(self):
        from ads_client import validate_config
        with self.assertRaises(ValueError):validate_config({**self.cfg,'ads_transport':'tcp'})
        self.assertEqual(timestamp(FILETIME_EPOCH+1),'1970-01-01T00:00:00.0000001+00:00')
    def test_no_notifications_watchdog(self):
        w=self.worker();until(lambda:w.started)
        until(lambda:w.error is not None,7);self.assertIn('Watchdog',w.error)

if __name__=='__main__':unittest.main(verbosity=2)
