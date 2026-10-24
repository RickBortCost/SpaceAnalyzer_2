"""ADS wire + application integration; deterministic local peer, no hardware claims."""
import asyncio
import csv
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest

TMP=tempfile.TemporaryDirectory(prefix='ads-integration-')
os.environ['OPCUA_DATA_DIR']=TMP.name+'/data';os.environ['OPCUA_APP_DIR']=TMP.name+'/app'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from ads_client import ADSClient, ADSError, netid, parse_symbol, decode_value, validate_config
from ads_simulator import Peer
import pyarrow.parquet as pq

class Wire(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.peer=await Peer().start();self.cfg={**app.DEFAULT_CONFIG,'source':'ads','ads_tcp_port':self.peer.port,'ads_timeout_ms':1000}
        self.c=ADSClient(self.cfg);await self.c.connect()
    async def asyncTearDown(self):
        await self.c.disconnect();await self.peer.close()
    async def test_fragmentation_and_decoding(self):
        symbols=await self.c.upload_symbols();self.assertEqual(len(symbols),len(self.peer.symbols))
        rows={r['nodeid']:r for r in await self.c.read_values(['ads:'+s for s in self.peer.symbols])}
        self.assertEqual(rows['ads:MAIN.Counter']['value'],str(2**63+123));self.assertTrue(rows['ads:MAIN.Counter']['precision_warning'])
        self.assertEqual(rows['ads:MAIN.Running']['data_type'],'Boolean')
        self.assertEqual(rows['ads:MAIN.Text']['value'],'Caffè');self.assertEqual(rows['ads:MAIN.Wide']['value'],'PLC Ω')
        self.assertEqual(rows['ads:MAIN.Array']['value'],[1.,2.,3.]);self.assertIsNone(rows['ads:MAIN.Array']['plot_value'])
        self.assertIsNone(rows['ads:MAIN.Nonfinite']['plot_value']);self.assertEqual(rows['ads:MAIN.Bad']['quality'],'Bad')
        self.assertTrue(all(r['source_ts'] is None and r['server_ts'] is None for r in rows.values()))
        self.assertEqual(self.c.info['read_mode'],'ADS SumRead')
        await self.c.disconnect();self.assertFalse(self.peer.handles);self.assertTrue(self.peer.released)
    async def test_fallback_and_runtime_state(self):
        self.peer.sum_enabled=False;self.peer.ads_state=6;await self.c.read_state()
        row=(await self.c.read_values(['ads:MAIN.Wave']))[0]
        self.assertEqual(row['quality'],'Uncertain');self.assertIsNone(row['plot_value'])
        self.assertEqual(self.c.info['read_mode'],'ADS Read individuale')
    async def test_online_change_and_selection_releases(self):
        await self.c.check_symbols();await self.c.read_values(['ads:MAIN.Wave','ads:MAIN.Running'])
        await self.c.read_values(['ads:MAIN.Wave']);self.assertEqual(len(self.peer.handles),1)
        self.peer.version+=1;await self.c.check_symbols();self.assertFalse(self.c.handles)
        self.assertEqual((await self.c.read_values(['ads:MAIN.Wave']))[0]['quality'],'Good')
        self.peer.handles.clear();row=(await self.c.read_values(['ads:MAIN.Wave']))[0]
        self.assertEqual(row['status_code'],1809)
        self.assertEqual((await self.c.read_values(['ads:MAIN.Wave']))[0]['quality'],'Good')
    async def test_browse_arrays_and_manual_nested(self):
        self.assertEqual((await self.c.inspect('ads:MAIN'))['node_class'],'Object')
        children=await self.c.browse('ads:MAIN.Array');self.assertEqual(len(children),3)
        self.assertEqual((await self.c.inspect('ads:MAIN.Struct.Value'))['data_type'],'DINT')
    async def test_reject_mismatched_ams_and_oversized_packet(self):
        for fault in ('invoke','length'):
            if not self.c.writer:await self.c.connect()
            self.peer.fault=fault
            with self.assertRaises(ValueError):await self.c.read_state()
            self.assertIsNone(self.c.writer)
    async def test_timeout_and_cancellation_close_transport(self):
        self.c.cfg['ads_timeout_ms']=100;self.peer.delay=.3
        with self.assertRaises(TimeoutError):await self.c.read_state()
        self.assertIsNone(self.c.writer)
        self.peer.delay=0;await self.c.connect();self.peer.delay=.3
        task=asyncio.create_task(self.c.read_state());await asyncio.sleep(.02);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertIsNone(self.c.writer)
    def test_validation_and_malformed_symbols(self):
        for value in ('1.2.3','1.2.3.4.5.256','1.2.3.-1.5.6'):
            with self.assertRaises(ValueError):netid(value)
        with self.assertRaises(ValueError):parse_symbol(b'\0'*30)
        with self.assertRaises(ValueError):validate_config({**self.cfg,'ads_source_port':65535})

class Integrated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize();cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
        cls.peer=app.runtime.run(Peer().start())
        cls.cfg={**app.DEFAULT_CONFIG,'source':'ads','ads_tcp_port':cls.peer.port,'sample_ms':10,'security':'None','auto_reconnect':True}
    @classmethod
    def tearDownClass(cls):
        app.runtime.run(app.disconnect_command());app.runtime.run(cls.peer.close())
    def post(self,path,data=None,expected=200):
        r=self.http.post(path,json=data or {},headers=self.headers);self.assertEqual(r.status_code,expected,r.get_json());return r.get_json()
    def get(self,path):return self.http.get(path).get_json()
    def wait(self,predicate,seconds=7):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            v=predicate()
            if v:return v
            time.sleep(.025)
        self.fail('ADS integration timed out')
    def setUp(self):
        self.peer.ads_state=5;self.peer.sum_enabled=True;self.peer.bad={'MAIN.Bad'}
        self.post('/api/select',{'nodeids':[]});self.post('/api/connect',self.cfg)
    def tearDown(self):
        self.post('/api/disconnect');self.post('/api/select',{'nodeids':[]})
    def test_one_ms_config_settings_workspace_and_protocol_limits(self):
        cfg={**self.cfg,'sample_ms':1}
        self.post('/api/config',cfg)
        self.assertEqual(app.runtime.sample_ms,1)
        self.post('/api/settings/save',{'config':cfg,'selected_nodes':[]})
        self.assertEqual(app.load_config()['sample_ms'],1)
        self.assertEqual(json.loads(app.SETTINGS_FILE.read_text())['config']['sample_ms'],1)
        self.post('/api/workspace',{'schema':1,'config':cfg,'selected_nodes':[],'live':{},'history':{}})
        self.assertEqual(self.get('/api/config')['workspace']['config']['sample_ms'],1)
        for value in (0,-1,.5,1.5,True,60001):
            self.post('/api/config',{**cfg,'sample_ms':value},expected=400)
        for source in ('opcua','s7'):
            with self.assertRaises(ValueError):app.validated_config({**cfg,'source':source})
        for value in (1,2,5,10):
            self.assertEqual(app.validated_config({**cfg,'sample_ms':value})['sample_ms'],value)
    def test_scan_selection_and_source_guard(self):
        self.post('/api/scan',{'ads_root':'ads:MAIN'})
        job=self.wait(lambda:(lambda j:j if j['state']!='running' else None)(self.get('/api/scan')))
        self.assertEqual(job['state'],'completed');self.assertEqual(job['total'],len(self.peer.symbols))
        self.assertEqual(self.get('/api/node?nodeid=ads:MAIN.Wave')['node']['data_type'],'LREAL')
        self.post('/api/select',{'nodeids':['ads:MAIN.Wave','ads:MAIN.Bad']})
        data=self.wait(lambda:(lambda j:j if j['diagnostics']['read_count']>10 else None)(self.get('/api/values')))
        self.assertEqual(data['source'],'ads');self.assertEqual(data['ads']['ads_state_name'],'RUN')
        self.assertTrue(data['samples']['ads:MAIN.Wave']);self.assertEqual(data['values'][1]['quality'],'Bad')
        self.post('/api/config',{'source':'opcua'},400)
        self.post('/api/select',{'nodeids':['ns=2;s=Wrong']},400)
        self.post('/api/ads/test',self.cfg,400)
    def test_record_csv_parquet_and_export(self):
        ids=['ads:MAIN.Wave','ads:MAIN.Bad'];nodes=[{'nodeid':n,'display_name':n} for n in ids]
        self.post('/api/select',{'nodeids':ids})
        for fmt in ('csv','parquet'):
            self.post('/api/record/start',{'format':fmt,'selected_nodes':nodes,'options':{'session_name':'ADS regression','machine':'Peer','max_duration_s':.08,'max_file_mb':1}})
            self.wait(lambda:self.get('/api/record/status')['recording']['cycles_received']>=25)
            rec=self.post('/api/record/stop')['recording'];self.assertGreaterEqual(len(rec['files']),2)
            rows=[]
            for part in rec['files']:
                p=app.RECORDINGS_DIR/part['name'] if isinstance(part,dict) else app.RECORDINGS_DIR/part
                if fmt=='csv':
                    with p.open(encoding='utf-8-sig',newline='') as f:rows+=list(csv.DictReader(f))
                else:rows+=pq.read_table(p).to_pylist()
            self.assertTrue(rows);self.assertTrue(all(not r['source_timestamp'] and not r['server_timestamp'] for r in rows))
            self.assertEqual({r['quality'] for r in rows},{'Good','Bad'})
            self.assertTrue(all(r['local_timestamp'] for r in rows))
            meta=json.loads(Path(rec['path']).with_suffix('.metadata.json').read_text());self.assertTrue(meta['endpoint'].startswith('ads://'))
        with app.runtime.lock:points=list(app.runtime.samples[ids[0]])
        self.assertGreater(len(points),16)
        result=self.post('/api/analysis/export',{'source':'live','signal_ids':ids,'x1':points[0][0],'x2':points[-1][0],'format':'csv'})
        self.assertGreater(result['rows'],0)
        # Polling timestamps include scheduler jitter and are not a uniform FFT
        # fixture. FFT calibration and timing-break rejection/exclusion are
        # covered by test_fft_engine.py and test_fft_api.py using source clocks.
    def test_reconnect_and_uncertain_state(self):
        self.post('/api/select',{'nodeids':['ads:MAIN.Wave']})
        self.wait(lambda:self.get('/api/values')['values'])
        async def cut():
            for w in list(self.peer.writers):w.close()
        app.runtime.run(cut());self.wait(lambda:self.get('/api/values')['state']=='reconnecting')
        self.wait(lambda:self.get('/api/values')['connected'],seconds=8)
        self.peer.ads_state=6
        self.wait(lambda:any(r['quality']=='Uncertain' for r in self.get('/api/values')['values']))
        self.peer.ads_state=5
    def test_workspace_and_test_connection(self):
        self.post('/api/disconnect')
        result=self.post('/api/ads/test',self.cfg);self.assertEqual(result['ads']['source_netid'],'127.0.0.1.1.1')
        workspace={'schema':1,'config':self.cfg,'selected_nodes':[{'nodeid':'ads:MAIN.Wave'}], 'live':{'charts':[{'name':'ADS 1','ids':['ads:MAIN.Wave']}]},'source_profiles':{'opcua':{'selected_nodes':[{'nodeid':'i=2258'}]}}}
        self.post('/api/workspace',workspace)
        cfg=self.get('/api/config');self.assertEqual(cfg['workspace']['config']['source'],'ads');self.assertIn('opcua',cfg['workspace']['source_profiles'])

if __name__=='__main__':unittest.main(verbosity=2)
