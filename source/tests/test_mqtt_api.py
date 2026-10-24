"""MQTT through real Flask APIs, calculation process and recording workers."""
import json
import shutil
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from test_integration import client as app
from mqtt_peer import Peer, until
import pyarrow.parquet as pq

A='mqtt:plant/motor#/pressure'
B='mqtt:plant/motor#/load'


class Integrated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize();cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    def setUp(self):
        app.runtime.run(app.disconnect_command());app.runtime.active_config=None
        with app.formula_store.lock:app.formula_store.formulas=[];app.formula_store._write()
        self.peer=Peer();self.cfg={**app.DEFAULT_CONFIG,'source':'mqtt','mqtt_port':self.peer.port,'mqtt_topics':'plant/#','sample_ms':500,'auto_reconnect':True}
        app.save_config(self.cfg);self.post('/api/select',{'nodeids':[]})
    def tearDown(self):
        self.post('/api/disconnect');self.post('/api/select',{'nodeids':[]});self.peer.close()
        with app.formula_store.lock:app.formula_store.formulas=[];app.formula_store._write()
    def post(self,url,data=None,code=200):
        r=self.http.post(url,json=data or {},headers=self.headers)
        self.assertEqual(r.status_code,code,r.json)
        result=r.json
        # These tests read finalized files; Stop itself is asynchronous now.
        if url=='/api/record/stop' and code==200:
            until(lambda:not app.recording.busy);result['recording']=app.recording.status()
        return result
    def get(self,url):return self.http.get(url).json
    def connect(self,ids=None,**changes):
        self.cfg.update(changes);self.post('/api/config',self.cfg)
        self.post('/api/select',{'nodeids':ids if ids is not None else [A,B]})
        self.post('/api/connect',self.cfg)
        until(lambda:app.runtime.client and app.runtime.client.consumer is not None)
    def send(self,count=1,**extra):
        c=app.runtime.client;before=c.counts['processed']
        for i in range(count):self.peer.publish('plant/motor',dict(pressure=i+.25,load=i+2,**extra),qos=1)
        until(lambda:c.counts['processed']>=before+count)
    def start(self,fmt='parquet',ids=None,**options):
        return self.post('/api/record/start',{'format':fmt,'selected_nodes':[{'nodeid':n,'display_name':n} for n in (ids or [A,B])],'options':{'buffer_seconds':60,**options}})['recording']
    def rows(self,rec):
        rows=[]
        for part in rec.get('original_files') or rec['files']:
            path=app.RECORDINGS_DIR/(part['name'] if isinstance(part,dict) else part)
            rows.extend(app.recording_library._iter_rows(path))
        return rows

    def test_catalog_manual_signals_scan_and_source_guard(self):
        self.connect();self.send(4)
        data=self.get('/api/values');self.assertEqual(data['source'],'mqtt');self.assertTrue(data['mqtt']['read_only'])
        self.assertEqual(data['diagnostics']['mqtt']['received'],4)
        until(lambda:len(app.runtime.samples[A])==4)
        self.assertEqual(len(app.runtime.samples[A]),4)
        time.sleep(.12);self.assertEqual(len(app.runtime.samples[A]),4)
        self.post('/api/scan');until(lambda:self.get('/api/scan')['state']!='running')
        scan=self.get('/api/scan');self.assertEqual(scan['state'],'completed');self.assertEqual(scan['total'],2)
        self.assertEqual(self.get('/api/node?nodeid='+quote(A,safe=''))['node']['node_class'],'Variable')
        self.post('/api/select',{'nodeids':['mqtt:/']},400)
        self.post('/api/select',{'nodeids':['ads:MAIN.Wrong']},400)
        self.post('/api/config',{**self.cfg,'mqtt_topics':'other/#'},400)
        self.post('/api/mqtt/test',self.cfg,400)

    def test_csv_parquet_and_rbf_all_publications_no_holding(self):
        self.connect()
        for fmt in ('csv','parquet','rbf'):
            self.start(fmt);self.send(80)
            state=self.post('/api/record/stop')['recording'];self.assertEqual(state['state'],'stopped',state)
            rows=self.rows(state);self.assertEqual(len(rows),160)
            self.assertTrue(all(r['local_timestamp'] and not r.get('server_timestamp') for r in rows))
            self.assertEqual(sum(r['node_id']==A for r in rows),80)
            path=app.RECORDINGS_DIR/(state.get('original_files') or state['files'])[0]['name']
            meta=json.loads(path.with_suffix('.metadata.json').read_text())
            self.assertEqual(meta['source_protocol'],'mqtt');self.assertTrue(meta['acquisition_info']['read_only'])
            self.assertEqual(meta['acquisition_final']['queue_dropped'],0)
            if fmt=='rbf':
                self.assertEqual(state['rbf_phase'],'ready',state)
                view=app.recording_library.load(state['files'][0]['name']);self.assertEqual(view['numeric_rows'],160)
            self.send(2);self.assertEqual(len(self.rows(state)),160,'Stop must not append future publications')

    def test_source_time_late_samples_preserved_and_metadata_bounds(self):
        self.connect(mqtt_time_basis='payload',mqtt_timestamp_pointer='/ts',mqtt_timestamp_unit='ns')
        self.start();c=app.runtime.client
        times=[1792584000100000000,1792584000300000000,1792584000200000000,1792584000050000000]
        for t in times:self.peer.publish('plant/motor',dict(pressure=t%1000,load=2,ts=t))
        until(lambda:c.counts['processed']==4)
        state=self.post('/api/record/stop')['recording'];rows=self.rows(state)
        self.assertEqual(len(rows),8)
        self.assertEqual([r['source_timestamp'] for r in rows if r['node_id']==A],
                         ['2026-10-21T12:00:00.100000000+00:00','2026-10-21T12:00:00.300000000+00:00','2026-10-21T12:00:00.200000000+00:00','2026-10-21T12:00:00.050000000+00:00'])
        self.assertEqual([p[0] for p in app.runtime.samples[A]],[1792584000100,1792584000300])
        self.assertEqual(app.runtime.mqtt_late_samples,4)
        meta=json.loads(Path(state['path']).with_suffix('.metadata.json').read_text())
        self.assertEqual(meta['started_at'],'2026-10-21T12:00:00.050000+00:00')
        self.assertEqual(meta['ended_at'],'2026-10-21T12:00:00.300000+00:00')
        view=app.recording_library.load(Path(state['path']).name)
        self.assertEqual([p[0] for p in view['series'][A]],sorted(t//1_000_000 for t in times))
        # Even without sidecar, discovery/replay must not reject arrival order.
        copied=app.RECORDINGS_DIR/'MQTT_late_without_metadata.parquet';shutil.copy2(state['path'],copied)
        self.assertEqual(app.recording_library.describe(copied.name)['rows_written'],8)
        from recording_compare import build_comparison
        output=app.RECORDINGS_DIR/'MQTT_late_comparison.parquet'
        # A second genuine MQTT recording carries the same source-clock contract.
        self.start();self.send(1,ts=1792584000400000000)
        other=self.post('/api/record/stop')['recording']
        desc=build_comparison({'files':[{'path':state['path'],'display_name':'MQTT A'},{'path':other['path'],'display_name':'MQTT B'}],'output':str(output)})
        self.assertEqual(desc['rows_written'],10)
        self.assertEqual(app.recording_library.load(output.name)['numeric_rows'],10)

    def test_mqtt_calculated_signals_and_record_stop_drain(self):
        self.post('/api/config',self.cfg)
        f=self.post('/api/calculations/save',dict(name='MQTT delta',expression='[A]-[B]',alignment='exact',bindings={'A':{'nodeid':A},'B':{'nodeid':B}}))['formula']
        self.assertEqual(f['protocol'],'mqtt')
        self.connect();self.send(5);until(lambda:app.runtime.calculated_latest.get(f['nodeid']))
        self.assertEqual(app.runtime.calculated_latest[f['nodeid']]['value'],-1.75)
        self.start(ids=[A,B,f['nodeid']]);self.send(70)
        state=self.post('/api/record/stop')['recording'];rows=self.rows(state)
        raw={r['timestamp_ms'] for r in rows if r['node_id']==A}
        calc={r['timestamp_ms'] for r in rows if r['node_id']==f['nodeid']}
        self.assertEqual(len(raw),70);self.assertEqual(raw,calc)
        self.assertTrue(all(r['value_numeric']==-1.75 for r in rows if r['node_id']==f['nodeid']))

    def test_reconnect_marks_gap_and_resubscribes(self):
        self.connect();self.send(3);self.start();self.peer.drop_all()
        until(lambda:app.runtime.state=='reconnecting')
        until(lambda:app.runtime.connected and app.runtime.mqtt_reconnections>=1,8)
        until(lambda:app.runtime.client.consumer is not None)
        self.send(3);state=self.post('/api/record/stop')['recording']
        rows=self.rows(state);self.assertTrue(any(r['quality']=='Bad' for r in rows))
        self.assertTrue(any(r['status']=='MQTT collegamento interrotto' for r in rows))
        self.assertGreaterEqual(self.peer.connects,2);self.assertFalse(self.peer.errors,self.peer.errors)

    def test_upstream_overflow_keeps_partial_instead_of_complete_file(self):
        self.connect();self.start();self.send(3)
        app.runtime.client.error='MQTT: coda di acquisizione piena; pubblicazioni non acquisite'
        state=self.post('/api/record/stop')['recording']
        self.assertEqual(state['state'],'error',state);self.assertTrue(state['path'].endswith('.part'))
        self.assertTrue(Path(state['path']).is_file());self.assertIn('MQTT',state['error'])

    def test_stop_drains_pending_messages_and_keeps_exact_integer_text(self):
        self.connect();self.start();c=app.runtime.client;decode=c._decode
        def slow(*args):time.sleep(.004);return decode(*args)
        c._decode=slow
        for i in range(60):self.peer.publish('plant/motor',dict(pressure=2**63+i,load=2),qos=1)
        until(lambda:c.counts['received']==60)
        self.assertLess(c.counts['processed'],60,'Fixture must actually exercise queued messages at Stop')
        state=self.post('/api/record/stop')['recording'];rows=self.rows(state)
        self.assertEqual(state['state'],'stopped');self.assertEqual(len(rows),120)
        self.assertEqual([r['value_text'] for r in rows if r['node_id']==A],[str(2**63+i) for i in range(60)])

    def test_bad_source_clock_fails_record_without_fabricated_timestamps(self):
        self.connect(mqtt_time_basis='payload',mqtt_timestamp_pointer='/ts',mqtt_timestamp_unit='ns',auto_reconnect=False)
        self.start();self.send(2,ts=1792584000000000000)
        self.peer.publish('plant/motor',{'pressure':1,'load':2})
        until(lambda:app.recording.status()['state']=='error')
        state=app.recording.status();self.assertIn('timestamp',state['error'])
        self.assertTrue(state['path'].endswith('.part'));self.assertTrue(Path(state['path']).is_file())

    def test_host_utc_clock_jump_does_not_discard_recorded_samples(self):
        from primary_format import iter_samples
        self.connect();self.start();self.send(5)
        shifted=time.time_ns()-3600*1000000000
        with patch('mqtt_source.time.time_ns',return_value=shifted):self.send(5)
        state=self.post('/api/record/stop')['recording']
        self.assertEqual(state['primary']['durable_samples'],20)
        rows=list(iter_samples(state['primary']['path']))
        self.assertEqual(sum(int(r['received_ns'])==shifted for r in rows),10)
        self.assertTrue(any(r['clock_discontinuity'] for r in rows))
        self.assertTrue(any(e['kind']=='receive_clock_backwards' for e in app.acquisition_events.recent))

    def test_ui_mutex_does_not_block_original_recording(self):
        self.connect();self.start()
        with app.runtime.lock:
            self.send(100)
            until(lambda:app.recording.status().get('primary',{}).get('received_samples')==200)
            self.assertEqual(app.recording.status()['primary']['lost_samples'],0)
        state=self.post('/api/record/stop')['recording']
        self.assertEqual(len(self.rows(state)),200)
        self.assertEqual(state['primary']['durable_samples'],200)

    def test_clearing_live_does_not_discard_queued_original_record_samples(self):
        self.connect();self.start();c=app.runtime.client;decode=c._decode
        def slow(*args):time.sleep(.005);return decode(*args)
        c._decode=slow
        for i in range(50):self.peer.publish('plant/motor',dict(pressure=i,load=2))
        until(lambda:c.counts['received']==50)
        self.assertLess(c.counts['processed'],50)
        self.post('/api/samples/clear')
        state=self.post('/api/record/stop')['recording']
        self.assertEqual(state['state'],'stopped',state);self.assertEqual(len(self.rows(state)),100)

    def test_test_connection_and_password_never_saved(self):
        self.cfg.update(mqtt_username='operator',mqtt_password='SESSION-SECRET')
        info=self.post('/api/mqtt/test',self.cfg)['mqtt'];self.assertNotIn('SESSION-SECRET',json.dumps(info))
        self.post('/api/config',self.cfg)
        self.post('/api/settings/save',{'config':self.cfg,'selected_nodes':[]})
        self.post('/api/workspace',dict(schema=1,config=self.cfg,selected_nodes=[],live={},history={}))
        for path in (app.CONFIG_FILE,app.SETTINGS_FILE,app.WORKSPACE_FILE):
            self.assertNotIn('SESSION-SECRET',path.read_text())
        self.assertEqual(self.get('/api/config')['mqtt_password'],'')


if __name__=='__main__':unittest.main(verbosity=2)
