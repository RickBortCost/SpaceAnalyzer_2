"""S7 symbolic wire fixtures, real UDP sockets and common acquisition/replay API."""
import asyncio
import json
import math
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

DATA=tempfile.TemporaryDirectory(prefix='space-s7-extended-')
os.environ['OPCUA_DATA_DIR']=DATA.name+'/config';os.environ['OPCUA_APP_DIR']=DATA.name+'/app'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from s7_catalog import parse_node,normalize_udp_tags
from s7_protocols import DEFAULTS,create_s7_client,validate_config
from s7_symbolic import decode_symbol
from s7_udp import decode_packet
from s7_plus_peer import Peer
import app


def free_udp_port():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def udp_config(**extra):
    return validate_config({**DEFAULTS,'s7_mode':'udp','s7_host':'127.0.0.1','s7_udp_bind':'127.0.0.1',
        's7_udp_port':free_udp_port(),'s7_udp_length':24,'s7_udp_timestamp_offset':0,
        's7_udp_tags':[dict(offset=8,data_type='REAL',display_name='CaricoA'),dict(offset=12,data_type='REAL',display_name='CaricoB'),
                       dict(offset=16,bit=0,data_type='BOOL',display_name='Enabled')],**extra})


def packet(seq,source_us=1700000000000001,a=42.5,b=12.25,module=200):
    return struct.pack('>HHHqffBB',24,module,seq,source_us,a,b,1,0)


class Configuration(unittest.TestCase):
    def test_symbol_names_preserve_case_unicode_and_folders(self):
        nid='s7:sym:DB_Produzione.àrray[1].Valore'
        self.assertEqual(parse_node(nid)['nodeid'],nid)
        self.assertTrue(parse_node('s7:sym:/DB_Produzione',True)['folder'])
        for nid in ('s7:sym:','s7:sym:/DB1','s7:sym:bad\0'):
            with self.assertRaises(ValueError):parse_node(nid)
    def test_udp_bounds_boolean_and_duplicates(self):
        self.assertEqual(parse_node('s7:udp:0008:real')['nodeid'],'s7:udp:8:REAL')
        for nid in ('s7:udp:1:BOOL','s7:udp:1.8:BOOL','s7:udp:4090:REAL','s7:udp:0:UNKNOWN'):
            with self.assertRaises(ValueError):parse_node(nid)
        cfg=udp_config();self.assertEqual(len(cfg['s7_udp_tags']),3)
        for extra in ({'s7_udp_length':23},{'s7_udp_module':100},{'s7_udp_timestamp_offset':17},{'s7_udp_queue':10}):
            with self.assertRaises(ValueError):udp_config(**extra)
        with self.assertRaises(ValueError):normalize_udp_tags(cfg['s7_udp_tags']*2,18)
    def test_types_precision_and_null_not_finite(self):
        self.assertEqual(decode_symbol('ULINT',struct.pack('>Q',2**64-1)),2**64-1)
        self.assertEqual(decode_symbol('STRING',b'\x06\x03ABC'), 'ABC')
        self.assertEqual(decode_symbol('WSTRING',struct.pack('>HH',4,2)+'àX'.encode('utf-16-be')),'àX')
        with self.assertRaises(ValueError):decode_symbol('REAL',struct.pack('>f',math.nan))
        with self.assertRaises(ValueError):decode_symbol('INT',b'1')
    def test_direct_header_exact_length_module_and_microseconds(self):
        cfg=udp_config();seq,_,ms,ts=decode_packet(packet(65535),cfg)
        self.assertEqual(seq,65535);self.assertTrue(ts.endswith('.000001+00:00'))
        self.assertAlmostEqual(ms,1700000000000.001,places=3)
        for data in (b'bad',packet(1)[:-1],b'\x00\x16'+packet(1)[2:],packet(1,source_us=-1)):
            with self.assertRaises((ValueError,OverflowError)):decode_packet(data,cfg)
        with self.assertRaises(LookupError):decode_packet(packet(1,module=201),cfg)


class Symbolic(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer=Peer();self.c=create_s7_client({**DEFAULTS,'s7_mode':'symbolic','s7_host':'127.0.0.1','s7_tcp_port':self.peer.port})
        await self.c.connect()
    async def asyncTearDown(self):await self.c.disconnect();self.peer.stop()
    async def test_real_explore_nested_array_and_lid_read(self):
        roots=await self.c.browse('s7:sym:/');self.assertEqual(roots[0]['display_name'],'DB1')
        branch=await self.c.browse('s7:sym:/DB1.Data');self.assertEqual(branch[0]['display_name'],'Status')
        self.assertEqual(len(await self.c.scan_items('s7:sym:/DB1')),8)
        nids=list(self.c.catalog);rows=await self.c.read_values(nids)
        vals={r['nodeid']:r for r in rows}
        self.assertEqual(vals['s7:sym:DB1.Data.Status']['value'],-12)
        self.assertEqual(vals['s7:sym:DB1.Array[3]']['value'],3.25)
        self.assertEqual(vals['s7:sym:DB1.Count64']['value'],str(2**64-1))
        self.assertTrue(all(r['source_ts'] is None and r['local_ts'] for r in rows))
        self.assertIn(((50,10),123),self.peer.reads);self.assertEqual(self.peer.variable_writes,0)
    async def test_partial_failure_unknown_symbol_and_mode_error(self):
        await self.c.ensure_catalog();self.peer.bad_lid=(20,)
        rows=await self.c.read_values(['s7:sym:DB1.CaricoA','s7:sym:DB1.CaricoB','s7:sym:DB1.Missing'])
        self.assertTrue(rows[0]['good']);self.assertEqual(rows[1]['quality'],'Bad');self.assertEqual(rows[2]['quality'],'Bad')
        with self.assertRaises(ValueError):await self.c.read_values(['s7:DB1.DBD0:REAL'])
    async def test_network_work_never_blocks_event_loop_and_refresh(self):
        original=self.c.client.refresh_tag_catalog;calls=[]
        def slow():calls.append(1);time.sleep(.1);return original()
        self.c.client.refresh_tag_catalog=slow
        tasks=[asyncio.create_task(self.c.ensure_catalog()) for _ in range(3)]
        start=time.monotonic();await asyncio.sleep(.01)
        self.assertLess(time.monotonic()-start,.08);await asyncio.gather(*tasks)
        self.assertEqual(len(calls),1)
        await self.c.ensure_catalog(refresh=True);self.assertEqual(self.c.info['symbols'],8)
        self.assertEqual(len(calls),2)


class UDP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg=udp_config(s7_udp_watchdog_ms=80);self.c=create_s7_client(self.cfg);self.rows=[];self.guard=threading.Lock()
        await self.c.connect();self.nids=list(self.c.catalog)
        def sink(ts,rows,ms,generation):
            with self.guard:self.rows.append((ts,rows,ms,generation))
        self.c.configure_acquisition(lambda:(self.nids,500,(1,1)),sink)
        self.sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    async def asyncTearDown(self):self.sender.close();await self.c.disconnect()
    def send(self,data):self.sender.sendto(data,('127.0.0.1',self.cfg['s7_udp_port']))
    async def wait(self,fn,timeout=2):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if fn():return
            await asyncio.sleep(.005)
        self.fail('UDP timeout '+str(self.c.diagnostics()))
    async def test_each_datagram_preserves_plc_time_even_if_poll_interval_500ms(self):
        for i in range(40):self.send(packet(i,1700000000000001+i*10000));await asyncio.sleep(.002)
        await self.wait(lambda:len(self.rows)>=40)
        self.assertEqual(len(self.rows),40);self.assertEqual(self.rows[-1][3],(1,1))
        self.assertAlmostEqual(self.rows[-1][0]-self.rows[0][0],390,places=3)
        self.assertTrue(all(r[1][0]['source_ts'].endswith('001+00:00') for r in self.rows))
        self.assertTrue(all(r[1][2]['value'] is True for r in self.rows))
        self.assertEqual(self.c.diagnostics()['lost'],0)
        self.assertEqual(self.c.sample_period_ms(),10)
        self.assertEqual(self.rows[-1][2],10)
    async def test_sequence_wrap_gap_duplicate_reorder_and_invalid(self):
        self.send(packet(65535));self.send(packet(0,1700000000010001));self.send(packet(0,1700000000010001))
        self.send(packet(65535));self.send(packet(3,1700000000040001));self.send(b'bad');self.send(packet(4,module=201))
        await self.wait(lambda:self.c.diagnostics()['received']>=7)
        await self.wait(lambda:len(self.rows)>=3)
        d=self.c.diagnostics();self.assertEqual(d['lost'],2);self.assertEqual(d['duplicates'],1);self.assertEqual(d['out_of_order'],1)
        self.assertEqual(d['invalid'],1);self.assertEqual(d['filtered'],1);self.assertEqual(self.rows[2][1][0]['quality'],'Uncertain')
    async def test_watchdog_bad_then_resume_and_bad_timestamp(self):
        self.send(packet(20));await self.wait(lambda:len(self.rows)>=1)
        await self.wait(lambda:any(r[1][0]['quality']=='Bad' for r in self.rows))
        self.send(packet(0,1700000000300001));await self.wait(lambda:self.c.diagnostics()['accepted']==2)
        await self.wait(lambda:len(self.rows)>=3)
        self.assertEqual(self.rows[-1][1][0]['quality'],'Uncertain')
        self.send(packet(1,1700000000200001));await self.wait(lambda:self.c.diagnostics()['timestamp_errors']==1)
    async def test_little_endian_payload_and_local_time_mode(self):
        await self.c.disconnect();cfg=udp_config(s7_udp_length=16,s7_udp_timestamp_offset=-1,s7_udp_endian='little',
            s7_udp_tags=[dict(offset=0,data_type='LREAL',display_name='X')])
        self.c=create_s7_client(cfg);await self.c.connect();self.nids=list(self.c.catalog)
        self.c.configure_acquisition(lambda:(self.nids,10,(1,1)),lambda ts,rows,ms,g:self.rows.append((ts,rows,ms,g)))
        self.sender.sendto(struct.pack('>HHH',16,200,1)+struct.pack('<d',12.125)+b'\0\0',('127.0.0.1',cfg['s7_udp_port']))
        await self.wait(lambda:len(self.rows)==1);self.assertEqual(self.rows[0][1][0]['value'],12.125)
        self.assertIsNone(self.rows[0][1][0]['source_ts']);self.assertTrue(self.rows[0][1][0]['local_ts'])
    async def test_sender_filter_exclusive_bind_and_stale_generation(self):
        with self.assertRaises(OSError):
            other=create_s7_client(self.cfg);await other.connect()
        self.c.sender_ip='192.0.2.1';self.send(packet(1));await self.wait(lambda:self.c.diagnostics()['filtered']==1)
        self.assertEqual(self.c.diagnostics()['accepted'],0)
    async def test_queue_overflow_is_bounded_and_quality_reports_loss(self):
        await self.c.disconnect();self.cfg=udp_config(s7_udp_queue=32,s7_udp_watchdog_ms=1000)
        self.c=create_s7_client(self.cfg);await self.c.connect();self.nids=list(self.c.catalog)
        release=threading.Event();entered=threading.Event()
        def slow(ts,rows,ms,g):
            if not entered.is_set():entered.set();release.wait(2)
            self.rows.append((ts,rows,ms,g))
        self.c.configure_acquisition(lambda:(self.nids,500,(1,1)),slow)
        try:
            self.send(packet(0));await self.wait(entered.is_set)
            for i in range(1,150):self.send(packet(i,1700000000000001+i*10000))
            await self.wait(lambda:self.c.diagnostics()['accepted']==150)
            d=self.c.diagnostics();self.assertEqual(d['queue_samples'],32);self.assertGreater(d['queue_dropped'],0)
            release.set();await self.wait(lambda:len(self.rows)>=33)
            self.assertTrue(any(r[1][0]['quality']=='Uncertain' and 'overflow' in r[1][0]['status'] for r in self.rows))
        finally:release.set()


class Application(unittest.TestCase):
    @classmethod
    def setUpClass(cls):app.initialize();cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    def setUp(self):
        app.runtime.run(app.disconnect_command());app.runtime.active_config=None
        app.save_config(app.validated_config(app.DEFAULT_CONFIG.copy()))
        app.runtime.run(app.apply_selection([]));self.peer=None;self.sender=None;self.stop_event=None
        with app.formula_store.lock:app.formula_store.formulas=[];app.formula_store._write()
    def tearDown(self):
        if self.stop_event:self.stop_event.set();self.thread.join()
        app.runtime.run(app.disconnect_command());app.runtime.active_config=None
        if self.peer:self.peer.stop()
        if self.sender:self.sender.close()
    def post(self,url,data,code=200):
        r=self.http.post(url,json=data,headers=self.headers);self.assertEqual(r.status_code,code,r.json)
        result=r.json
        # Round trips wait for their own files, independently of Record controls.
        if url=='/api/record/stop' and code==200:
            self.wait(lambda:not app.recording.busy);result['recording']=app.recording.status()
        return result
    def wait(self,fn,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if fn():return
            time.sleep(.02)
        self.fail('Acquisition timeout '+str(app.runtime.last_error))
    def test_symbolic_scan_formula_record_replay_and_workspace_credentials(self):
        self.peer=Peer();cfg=dict(source='s7',s7_mode='symbolic',s7_host='127.0.0.1',s7_tcp_port=self.peer.port,security='None',sample_ms=10)
        self.assertEqual(self.post('/api/s7/test',cfg)['s7']['symbols'],8)
        self.post('/api/connect',cfg);self.post('/api/scan',{**cfg,'s7_symbolic_root':'s7:sym:/DB1'})
        self.wait(lambda:app.runtime.scan['state']=='completed');self.assertEqual(len(app.runtime.scan['nodes']),8)
        page=self.http.get('/api/browse',query_string={'nodeid':'s7:sym:/DB1','count':2}).json
        self.assertEqual(len(page['nodes']),2);self.assertEqual(page['next_offset'],2)
        other=self.http.get('/api/browse',query_string={'nodeid':'s7:sym:/DB1','count':2,'offset':2}).json
        self.assertNotEqual(page['nodes'][0]['nodeid'],other['nodes'][0]['nodeid'])
        a,b='s7:sym:DB1.CaricoA','s7:sym:DB1.CaricoB'
        self.post('/api/select',{'nodeids':[a,b]})
        f=self.post('/api/calculations/save',dict(name='Delta S7',expression='[A]-[B]',bindings={'A':{'nodeid':a},'B':{'nodeid':b}}))['formula']
        self.wait(lambda:any(v.get('value')==30.25 for v in app.runtime.calculated_latest.values()))
        self.post('/api/s7/symbols/refresh',{})
        for fmt in ('csv','parquet'):
            state=self.post('/api/record/start',dict(format=fmt,selected_nodes=[dict(nodeid=n,display_name=n) for n in (a,b,f['nodeid'])]))['recording']
            self.post('/api/s7/symbols/refresh',{},400)
            self.wait(lambda:app.recording.status()['cycles_received']>=30)
            final=self.post('/api/record/stop',{})['recording'];self.assertEqual(final['error'],'')
            path=Path(final['path']);meta=json.loads(path.with_suffix('.metadata.json').read_text())
            self.assertEqual(meta['acquisition_info']['mode'],'symbolic')
            view=self.post('/api/recordings/data',dict(source='repository',name=path.name,signal_ids=[a,b,f['nodeid']],max_points=1000))
            self.assertEqual(len(view['recording']['signals']),3)
        self.post('/api/select',{'nodeids':['s7:DB1.DBD0:REAL']},400)
        self.post('/api/config',{**cfg,'s7_mode':'udp'},400)
        self.post('/api/disconnect',{})
        self.post('/api/workspace',dict(schema=1,config={**cfg,'s7_symbolic_password':'DO_NOT_SAVE'},selected_nodes=[{'nodeid':a}],live={'charts':[{'name':'S7','ids':[a]}]},history={'charts':[]}))
        self.assertNotIn('DO_NOT_SAVE',app.WORKSPACE_FILE.read_text())
        self.post('/api/config',{**cfg,'s7_symbolic_password':'DO_NOT_SAVE'})
        self.assertNotIn('DO_NOT_SAVE',app.CONFIG_FILE.read_text())
    def test_udp_original_samples_recorded_fft_export_and_metadata(self):
        cfg=udp_config();cfg.update(source='s7',sample_ms=500,security='None')
        nids=[r['nodeid'] for r in cfg['s7_udp_tags'][:2]]
        self.post('/api/config',cfg);self.post('/api/select',{'nodeids':nids});self.post('/api/connect',cfg)
        self.sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.stop_event=threading.Event()
        def send():
            seq=0
            while not self.stop_event.is_set():
                self.sender.sendto(packet(seq,1700000000000001+seq*10000,a=math.sin(2*math.pi*5*seq/100)),('127.0.0.1',cfg['s7_udp_port']))
                seq+=1;self.stop_event.wait(.002)
        self.thread=threading.Thread(target=send);self.thread.start()
        self.wait(lambda:len(app.runtime.samples.get(nids[0],[]))>50)
        f=self.post('/api/calculations/save',dict(name='Delta UDP',expression='[A]-[B]',bindings={'A':{'nodeid':nids[0]},'B':{'nodeid':nids[1]}}))['formula']
        self.wait(lambda:bool(app.runtime.calculated_latest))
        state=self.post('/api/record/start',dict(format='parquet',selected_nodes=[dict(nodeid=n,display_name=n) for n in (*nids,f['nodeid'])]))['recording']
        self.assertEqual(app.recording.metadata['initial_sample_ms'],10)
        self.assertEqual(state['queue_capacity_cycles'],1000)
        # Original rows and derived batches each count as recorder enqueues.
        self.wait(lambda:app.recording.status()['cycles_received']>600)
        final=self.post('/api/record/stop',{})['recording'];self.assertEqual(final['error'],'');self.assertEqual(final['dropped_cycles'],0)
        path=Path(final['path']);rows=[r for r in app.recording_library._iter_rows(path) if r['node_id']==nids[0]]
        self.assertGreater(len(rows),200);self.assertTrue(rows[0]['source_timestamp'].endswith('001+00:00'))
        self.assertTrue(rows[0]['local_timestamp']);self.assertEqual(float(rows[1]['timestamp_ms'])-float(rows[0]['timestamp_ms']),10)
        meta=json.loads(path.with_suffix('.metadata.json').read_text());self.assertEqual(meta['acquisition_info']['mode'],'udp')
        start,end=float(rows[0]['timestamp_ms']),float(rows[-1]['timestamp_ms'])
        for source,name in [('live',''),('repository',path.name)]:
            body=dict(source=source,name=name,signal_ids=[nids[0]],x1=start,x2=end,options={'block_size':128})
            if source=='live': body['snapshot_id']=self.post('/api/analysis/live-snapshot',body)['snapshot_id']
            result=self.post('/api/analysis/fft',body)
            self.assertAlmostEqual(result['spectrum']['sample_hz'],100,places=3)
            exported=self.post('/api/analysis/export',dict(source=source,name=name,signal_ids=[nids[0]],x1=start,x2=end,format='parquet'))
            self.assertGreater(exported['rows'],200)
        diag=self.http.get('/api/values').json['diagnostics']['s7_udp'];self.assertEqual(diag['lost'],0)
    def test_udp_test_reports_waiting_without_false_tcp_success(self):
        cfg=udp_config(s7_timeout_ms=100);cfg.update(source='s7')
        data=self.post('/api/s7/test',cfg,400);self.assertIn('nessun telegramma valido',data['error'])
        self.post('/api/s7/udp/tags/validate',dict(config=cfg,tags=[{'offset':30,'data_type':'REAL'}]),400)


if __name__=='__main__':unittest.main(verbosity=2)
