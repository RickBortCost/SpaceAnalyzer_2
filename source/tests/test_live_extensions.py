import math
import time
import unittest
from collections import deque
from test_integration import client as app, make_server
from mqtt_peer import Peer, until
from quick_analysis import summarize
import pyarrow.parquet as pq


class LiveExtensions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize()
        cls.server,cls.endpoint,_,cls.nodes=app.runtime.run(make_server())
        cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    @classmethod
    def tearDownClass(cls):app.runtime.run(cls.server.stop())
    def setUp(self):
        app.runtime.run(app.disconnect_command());app.runtime.active_config=None
        self.peer=Peer();self.ua=self.nodes[0].nodeid.to_string();self.mqtt='mqtt:plant/motor#/load'
        self.cfg={**app.DEFAULT_CONFIG,'source':'opcua','endpoint':self.endpoint,'security':'None','security_mode':'None','sample_ms':20,
                  'mqtt_parallel':True,'mqtt_port':self.peer.port,'mqtt_topics':'plant/#','auto_reconnect':True}
        self.post('/api/select',{'nodeids':[]})
    def tearDown(self):
        self.post('/api/disconnect');self.post('/api/select',{'nodeids':[]});self.peer.close()
    def post(self,path,data=None,code=200):
        r=self.http.post(path,json=data or {},headers=self.headers);self.assertEqual(r.status_code,code,r.json);return r.json
    def connect(self):
        self.post('/api/select',{'nodeids':[self.ua,self.mqtt]});self.post('/api/connect',self.cfg)
        until(lambda:app.runtime.samples.get(self.ua) and app.source_sessions.sessions['mqtt'].state=='connected')
    def send(self,value=7):
        c=app.source_sessions.sessions.get("mqtt").client;before=c.counts['processed'];self.peer.publish('plant/motor',{'load':value},qos=1)
        until(lambda:c.counts['processed']>before)
    def test_independent_acquisition_catalog_and_mixed_recording(self):
        self.connect();self.send()
        with app.runtime.lock:epoch=app.runtime.buffer_epoch;rev=app.runtime.selection_revision
        first=self.http.get('/api/mqtt/catalog').json
        self.assertTrue(any(n['nodeid']==self.mqtt for n in first['nodes']))
        for _ in range(5):self.assertTrue(self.http.get('/api/mqtt/catalog?revision='+first['revision']).json['unchanged'])
        self.assertEqual(app.runtime.buffer_epoch,epoch);self.assertEqual(app.runtime.selection_revision,rev)
        self.post('/api/record/start',{'format':'parquet','selected_nodes':[{'nodeid':n} for n in [self.ua,self.mqtt]]})
        for i in range(20):self.send(i);time.sleep(.01)
        stopped=self.post('/api/record/stop')['recording']
        rows=pq.read_table(stopped['path']).to_pylist()
        self.assertEqual({r['node_id'] for r in rows},{self.ua,self.mqtt})
        self.assertEqual([r['value_numeric'] for r in rows if r['node_id']==self.mqtt],list(range(20)))
        vals=self.http.get('/api/values').json
        self.assertEqual({r['nodeid'] for r in vals['values']},{self.ua,self.mqtt})
        self.assertTrue(vals['parallel_mqtt']['enabled'])
        self.post('/api/record/start',{'format':'rbf','selected_nodes':[{'nodeid':n} for n in [self.ua,self.mqtt]]})
        self.send(3);self.post('/api/record/stop');until(lambda:not app.recording.busy)
    def test_mqtt_reconnect_does_not_clear_or_stop_opc(self):
        self.connect();self.send();client=app.source_sessions.client("opcua")
        before=len(app.runtime.samples[self.ua]);epoch=app.runtime.buffer_epoch
        self.peer.drop_all()
        until(lambda:app.source_sessions.sessions["mqtt"].reconnections>0,timeout=8)
        self.send(19)
        self.assertIs(app.source_sessions.client("opcua"),client);self.assertTrue(app.runtime.connected)
        self.assertGreater(len(app.runtime.samples[self.ua]),before);self.assertEqual(app.runtime.buffer_epoch,epoch)
        self.assertEqual(app.runtime.samples[self.mqtt][-1][1],19)
    def test_mode_guards_and_disconnect_clean_both_sessions(self):
        self.assertEqual(app.allowed_sources({**self.cfg,'source':'ads'}),{'ads','mqtt'})
        self.connect();self.post('/api/config',{'mqtt_parallel':False},400)
        self.post('/api/select',{'nodeids':[self.ua,'ads:MAIN.X']},400)
        self.post('/api/disconnect');self.assertFalse(app.source_sessions.enabled);self.assertIsNone(app.parallel_mqtt.task);self.assertIsNone(app.runtime.client)
    def test_opc_reconnect_keeps_mqtt_session_and_samples(self):
        self.connect();self.send();mqtt_client=app.source_sessions.sessions.get("mqtt").client;epoch=app.runtime.buffer_epoch
        app.runtime.run(app.source_sessions.client("opcua").disconnect())
        until(lambda:app.source_sessions.sessions['opcua'].state=='reconnecting',timeout=8)
        before=len(app.runtime.samples[self.mqtt])
        for i in range(5):self.send(100+i)
        self.assertEqual(len(app.runtime.samples[self.mqtt]),before+5)
        self.assertIs(app.source_sessions.sessions.get("mqtt").client,mqtt_client);self.assertEqual(app.runtime.buffer_epoch,epoch)
        until(lambda:app.source_sessions.sessions['opcua'].state=='connected',timeout=8)
    def test_partial_connect_failure_preserves_primary_session(self):
        rejected=Peer(reject_subscribe=True)
        try:
            self.post('/api/connect',{**self.cfg,'mqtt_port':rejected.port})
            until(lambda:app.source_sessions.sessions['opcua'].state=='connected')
            until(lambda:app.source_sessions.sessions['mqtt'].state=='reconnecting')
            self.assertIsNotNone(app.source_sessions.client('opcua'))
            self.assertTrue(self.http.get('/api/values').json['connected'])
        finally:rejected.close()
    def test_quick_uses_original_samples_and_fft_requires_snapshot(self):
        n=self.ua;start=1700000000000
        with app.runtime.lock:app.runtime.samples={n:deque([[start+i*10,math.sin(i*.1),'Good',0,None,None,None,'Double','Good',10] for i in range(512)])}
        body={'source':'live','signal_ids':[n],'x1':start,'x2':start+5110}
        result=self.post('/api/analysis/quick',body)['results'][0]
        self.assertEqual(result['count'],512);self.assertAlmostEqual(result['statistics']['mean'],sum(math.sin(i*.1) for i in range(512))/512)
        self.post('/api/analysis/fft',body,400);self.post('/api/analysis/fft',{**body,'live_seconds':5},400)
        snap=self.post('/api/analysis/live-snapshot',body)['snapshot_id']
        with app.runtime.lock:app.runtime.samples[n].clear()
        frozen=self.post('/api/analysis/quick',{**body,'snapshot_id':snap})['results'][0]
        self.assertEqual(frozen,result)
        self.post('/api/analysis/fft',{**body,'snapshot_id':snap,'options':{'block_size':128}})
        self.post('/api/analysis/live-snapshot/release',{'snapshot_id':snap})
    def test_statistics_exclude_bad_samples_and_do_not_integrate_gaps(self):
        rows=[dict(node_id='n',timestamp_ms=i*100,value_numeric=v,quality=q,sample_ms=100) for i,v,q in [(0,1,'Good'),(1,3,'Good'),(2,100,'Bad'),(3,5,'Good')]]
        r=summarize(rows,['n'],0,300,0,300)[0]
        self.assertEqual(r['count'],3);self.assertEqual(r['excluded'],1)
        self.assertEqual(r['statistics']['mean'],3);self.assertEqual(r['statistics']['delta'],4)
        self.assertAlmostEqual(r['statistics']['integral'],.2)


if __name__=='__main__':unittest.main(verbosity=2)
