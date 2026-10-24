"""Four real client stacks against local peers; no PLC or Windows claims."""
import asyncio
import itertools
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_integration import client as app, make_server
from test_s7 import start_server
from ads_simulator import Peer as ADSPeer
from mqtt_peer import Peer as MQTTPeer, until
from recording_reader import RecordingLibrary
from source_sessions import PROTOCOLS


class Sources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize()
        cls.ua_server, cls.endpoint, cls.root, cls.ua_nodes = app.runtime.run(make_server())
        cls.s7_server, cls.s7_port = start_server()
        cls.http = app.app.test_client()
        cls.headers = {'X-OPCUA-Token': app.SESSION_TOKEN}
        async def vary():
            i=0
            while True:
                i+=1
                await cls.ua_nodes[0].write_value(float(i))
                await asyncio.sleep(.02)
        cls.vary=asyncio.run_coroutine_threadsafe(vary(),app.runtime.loop)

    @classmethod
    def tearDownClass(cls):
        cls.vary.cancel()
        app.runtime.run(app.disconnect_command())
        app.runtime.run(cls.ua_server.stop())
        cls.s7_server.stop();cls.s7_server.destroy()

    def setUp(self):
        app.runtime.run(app.disconnect_command())
        app.runtime.active_config=None
        self.ads=app.runtime.run(ADSPeer().start())
        self.mqtt=MQTTPeer()
        self.ids={'opcua':self.ua_nodes[0].nodeid.to_string(),'ads':'ads:MAIN.Wave',
                  'mqtt':'mqtt:plant/motor#/load','s7':'s7:DB1.DBD0:REAL'}
        self.cfg={**app.DEFAULT_CONFIG,'source':'opcua','enabled_sources':list(PROTOCOLS),
            'endpoint':self.endpoint,'security':'None','security_mode':'None','sample_ms':20,
            'source_intervals':{'opcua':20,'ads':30,'mqtt':40,'s7':50},
            'ads_tcp_port':self.ads.port,'mqtt_port':self.mqtt.port,'mqtt_topics':'plant/#',
            's7_host':'127.0.0.1','s7_tcp_port':self.s7_port,'s7_tags':[{'address':'DB1.DBD0','data_type':'REAL'}]}
        self.post('/api/select',{'nodeids':[]})

    def tearDown(self):
        self.post('/api/disconnect')
        self.post('/api/select',{'nodeids':[]})
        app.runtime.run(self.ads.close());self.mqtt.close()

    def post(self,route,body=None,code=200):
        r=self.http.post(route,json=body or {},headers=self.headers)
        self.assertEqual(r.status_code,code,r.json)
        return r.json

    def connect(self,protocols=PROTOCOLS):
        chosen=list(protocols)
        self.post('/api/select',{'nodeids':[self.ids[p] for p in chosen]})
        cfg={**self.cfg,'source':chosen[0],'enabled_sources':chosen}
        self.post('/api/connect',cfg)
        if len(chosen)>1:
            until(lambda:all(app.source_sessions.sessions[p].state=='connected' for p in chosen),timeout=15)
        until(lambda:all(app.runtime.samples.get(self.ids[p]) and app.runtime.samples[self.ids[p]][-1][2]=='Good' for p in chosen if p!='mqtt'),timeout=15)
        self.publish()
        until(lambda:all(app.runtime.samples.get(self.ids[p]) for p in chosen),timeout=10)

    def publish(self,count=1):
        if 'mqtt' not in app.allowed_sources(app.runtime.active_config):return
        if app.source_sessions.enabled:
            until(lambda:app.source_sessions.sessions['mqtt'].state=='connected',timeout=10)
        c=app.mqtt_catalog_client();before=c.counts['processed']
        for i in range(count):self.mqtt.publish('plant/motor',{'load':i+7.25},qos=1)
        until(lambda:c.counts['processed']>=before+count)

    def start(self,fmt):
        return self.post('/api/record/start',{'format':fmt,
            'selected_nodes':[{'nodeid':n,'display_name':p} for p,n in self.ids.items()],
            'options':{'buffer_seconds':30,'pda3_accept_template_time':True}})

    def stop(self):
        self.post('/api/record/stop')
        until(lambda:not app.recording.busy,timeout=60)
        return app.recording.status()

    def test_all_nonempty_protocol_subsets_and_catalogs(self):
        for size in range(1,5):
            for protocols in itertools.combinations(PROTOCOLS,size):
                with self.subTest(protocols=protocols):
                    self.connect(protocols)
                    for p in protocols:
                        r=self.http.get('/api/node',query_string={'nodeid':self.ids[p]})
                        self.assertEqual(r.status_code,200,r.json)
                        self.assertEqual(r.json['node']['node_class'],'Variable')
                    self.assertEqual(set(self.http.get('/api/values').json['samples']),{self.ids[p] for p in protocols})
                    self.post('/api/disconnect')
                    self.post('/api/select',{'nodeids':[]})
        self.assertFalse(app.source_sessions.enabled)

    def test_every_format_records_four_sources_and_replays(self):
        import iba_dat, iba_recording
        from test_iba_dat import FakeNative
        self.connect()
        # Native DAT is explicitly simulated. FDA/RBF/PDA3 codecs are real.
        with patch.object(iba_dat.bridge,'require'),patch.object(iba_dat.bridge,'require_prepared'), \
             patch.object(iba_recording,'export_in_worker',lambda p,n,l:iba_dat.export_parquet(p,n,l,native_bridge=FakeNative())):
            for fmt in ('csv','parquet','das','rbf','pda3','dat'):
                with self.subTest(format=fmt):
                    self.start(fmt)
                    self.publish(12);time.sleep(.15)
                    rec=self.stop()
                    self.assertEqual(rec['state'],'stopped',rec)
                    original=app.RECORDINGS_DIR/('Parquet' if fmt=='pda3' else '')
                    rows=[]
                    for file in rec.get('original_files') or rec['files']:
                        rows.extend(RecordingLibrary(original)._iter_rows(original/file['name']))
                    self.assertEqual({r['node_id'] for r in rows},set(self.ids.values()))
                    self.assertEqual({r['source_protocol'] for r in rows},set(PROTOCOLS))
                    self.assertTrue(all(r['local_timestamp'] and r['acquisition_timestamp_ms'] is not None for r in rows))
                    self.assertTrue(any(r['source_timestamp'] for r in rows if r['source_protocol']=='opcua'))
                    for file in rec['files']:
                        if fmt=='dat':continue  # FakeNative bytes are deliberately not an iba file.
                        view=app.recording_library.load(file['name'])
                        self.assertEqual(set(view['series']),set(self.ids.values()))
                    if fmt=='pda3':
                        manifests=list(app.RECORDINGS_DIR.glob('DAT/*/*.json'))
                        self.assertTrue(any(json.loads(p.read_text()).get('resampling') for p in manifests if p.name.endswith('.json') and not p.name.endswith('.progress.json')))

    def test_each_transport_reconnect_isolated_during_recording(self):
        self.connect();self.start('parquet')
        epoch=app.runtime.buffer_epoch
        for protocol in PROTOCOLS:
            session=app.source_sessions.sessions[protocol]
            previous=session.reconnections
            clients={p:s.client for p,s in app.source_sessions.sessions.items() if p!=protocol}
            app.runtime.run(session.client.disconnect())
            until(lambda:session.reconnections>previous and session.state=='connected',timeout=15)
            self.publish(3)
            self.assertEqual(app.runtime.buffer_epoch,epoch)
            self.assertTrue(app.recording.busy)
            for p,c in clients.items():self.assertIs(app.source_sessions.sessions[p].client,c)
        rec=self.stop()
        self.assertEqual(rec['state'],'stopped',rec)
        rows=list(app.recording_library._iter_rows(Path(rec['path'])))
        self.assertEqual({r['source_protocol'] for r in rows if r['quality']=='Bad'},set(PROTOCOLS))

    def test_source_controls_preserve_other_buffers_and_start_failure_is_local(self):
        self.cfg.update(endpoint='opc.tcp://127.0.0.1:1')
        self.post('/api/select',{'nodeids':list(self.ids.values())})
        self.post('/api/connect',self.cfg)
        until(lambda:app.runtime.samples.get(self.ids['ads']) and app.runtime.samples.get(self.ids['s7']))
        self.publish()
        self.assertTrue(self.http.get('/api/values').json['connected'])
        self.assertEqual(app.source_sessions.sessions['opcua'].state,'reconnecting')
        epoch=app.runtime.buffer_epoch
        self.start('parquet')
        for action in ('disconnect','connect'):
            before=len(app.runtime.samples[self.ids['s7']])
            self.post('/api/sources/ads/'+action)
            until(lambda:len(app.runtime.samples[self.ids['s7']])>before)
            self.assertEqual(epoch,app.runtime.buffer_epoch)
        self.publish(3)
        self.assertEqual(self.stop()['state'],'stopped')

    def test_queue_overflow_and_late_originals_are_explicit(self):
        self.connect();self.start('parquet')
        generation=(app.runtime.selection_revision,app.runtime.buffer_epoch)
        nid=self.ids['mqtt'];s=app.source_sessions.sessions['mqtt']
        time.sleep(.01)
        t=time.time()*1000
        row=dict(nodeid=nid,value=22.,plot_value=22.,good=True,quality='Good',data_type='Double',received_ms=t)
        s.accept(s.token,t,[row],40,generation)
        s.accept(s.token,t-1,[{**row,'received_ms':t-1}],40,generation)
        self.assertGreater(s.late_samples,0)
        rec=self.stop()
        rows=list(app.recording_library._iter_rows(Path(rec['path'])))
        self.assertTrue(any(r['timestamp_ms']==t-1 for r in rows))
        self.start('parquet');self.publish(2)
        s.client.error='MQTT: coda piena (test)'
        rec=self.stop()
        self.assertEqual(rec['state'],'error',rec)
        self.assertTrue(Path(rec['path']).is_file())
        self.assertTrue(rec['path'].endswith('.part'))

    def test_clear_and_disconnect_drain_pending_original_publications(self):
        self.connect();self.start('parquet')
        c=app.source_sessions.client('mqtt');decode=c._decode
        def slow(*args):
            time.sleep(.005)
            return decode(*args)
        c._decode=slow
        before=c.counts['received']
        for i in range(60):self.mqtt.publish('plant/motor',{'load':i})
        until(lambda:c.counts['received']>=before+60)
        self.post('/api/samples/clear')
        self.post('/api/disconnect')
        until(lambda:not app.recording.busy)
        rec=app.recording.status()
        self.assertEqual(rec['state'],'stopped',rec)
        rows=list(app.recording_library._iter_rows(Path(rec['path'])))
        self.assertEqual([r['value_numeric'] for r in rows if r['node_id']==self.ids['mqtt']],list(range(60)))
        self.assertFalse(app.source_sessions.enabled)

    def test_duplicate_start_stop_and_restart_keep_record_boundaries(self):
        self.connect()
        for _ in range(2):
            self.start('csv')
            begin=app.runtime.multi_recording_begin
            self.post('/api/record/start',{'format':'csv','selected_nodes':[{'nodeid':n} for n in self.ids.values()]},400)
            self.assertEqual(begin,app.runtime.multi_recording_begin)
            self.publish(10)
            rec=self.stop()
            self.assertEqual(rec['state'],'stopped',rec)
            rows=list(app.recording_library._iter_rows(Path(rec['path'])))
            self.assertEqual(sum(r['node_id']==self.ids['mqtt'] for r in rows),10)
            self.publish(3)
            self.assertEqual(len(rows),len(list(app.recording_library._iter_rows(Path(rec['path'])))))

    def test_opcua_formula_keeps_exact_snapshot_alignment_in_mixed_recording(self):
        self.connect()
        second=self.ua_nodes[1].nodeid.to_string()
        chosen=[*self.ids.values(),second]
        self.post('/api/select',{'nodeids':chosen})
        formula=self.post('/api/calculations/save',dict(name='Mixed UA delta',expression='[A]-[B]',alignment='exact',
            bindings={'A':{'nodeid':self.ids['opcua']},'B':{'nodeid':second}}))['formula']
        try:
            until(lambda:app.runtime.calculated_latest.get(formula['nodeid'],{}).get('good'))
            self.post('/api/record/start',dict(format='parquet',selected_nodes=[{'nodeid':n} for n in chosen+[formula['nodeid']]]))
            self.publish(3);time.sleep(.15)
            rec=self.stop();self.assertEqual(rec['state'],'stopped',rec)
            rows=list(app.recording_library._iter_rows(Path(rec['path'])))
            derived=[r for r in rows if r['node_id']==formula['nodeid'] and r['quality']=='Good']
            snapshots={r['timestamp_ms'] for r in rows if r['node_id']==second and r['acquisition_origin']=='subscription_snapshot'}
            self.assertTrue(derived)
            self.assertTrue(all(r['timestamp_ms'] in snapshots for r in derived))
        finally:
            self.stop()
            self.post('/api/calculations/delete',{'id':formula['id']})

    def test_native_ads_callbacks_and_s7_udp_remain_independent(self):
        import socket
        import threading
        from test_ads_notifications import NotifyDLL, configure_library, FILETIME_EPOCH
        from test_s7_extended import udp_config, packet
        udp=udp_config()
        self.cfg.update(udp,ads_transport='native',ads_mode='notify')
        self.ids['s7']=udp['s7_udp_tags'][0]['nodeid']
        dll=NotifyDLL();configure_library(dll)
        stop=threading.Event()
        def emit():
            i=0
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                while not stop.wait(.01):
                    i+=1
                    sock.sendto(packet(i,1700000000000001+i*10000),('127.0.0.1',udp['s7_udp_port']))
                    dll.emit(FILETIME_EPOCH+17000000000000000+i*100000)
        with patch('ads_native.load_library',return_value=(dll,'simulated.dll')):
            thread=threading.Thread(target=emit,daemon=True);thread.start()
            try:
                self.connect();self.start('parquet');self.publish(5);time.sleep(.08)
                rec=self.stop();self.assertEqual(rec['state'],'stopped',rec)
                rows=list(app.recording_library._iter_rows(Path(rec['path'])))
                self.assertEqual({r['source_protocol'] for r in rows},set(PROTOCOLS))
                ads=[r for r in rows if r['source_protocol']=='ads']
                s7=[r for r in rows if r['source_protocol']=='s7']
                self.assertTrue(all(r['ads_filetime'] and r['source_timestamp'] for r in ads))
                self.assertTrue(all(r['source_timestamp'] for r in s7))
                self.assertTrue(all(abs(r['timestamp_ms']-time.time()*1000)<10000 for r in ads+s7))
                self.post('/api/disconnect')
                self.assertFalse(dll.subs)
            finally:
                stop.set();thread.join(2)
                app.runtime.run(app.disconnect_command())


if __name__=='__main__':unittest.main(verbosity=2)
