"""S7 interoperability with the independent native Snap7 server.

Test dependency only: pip install python-snap7==2.0.2
No Siemens PLC, Windows EXE execution or 10 ms throughput claim.
"""
import asyncio
import ctypes
import json
import logging
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import time
import unittest

DATA=tempfile.TemporaryDirectory(prefix='space-s7-tests-')
os.environ['OPCUA_DATA_DIR']=DATA.name+'/config'
os.environ['OPCUA_APP_DIR']=DATA.name+'/app'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from s7_client import DEFAULTS, S7Client, parse_tag, normalize_tags, decode, validate_config
import app
from snap7.server import Server
from snap7.type import SrvArea
logging.disable(logging.CRITICAL)


def start_server():
    server=Server(log=False)
    memory=bytearray(2048)
    struct.pack_into('>f',memory,0,42.5)
    struct.pack_into('>h',memory,4,-1234)
    memory[6]=0x80
    struct.pack_into('>d',memory,8,12.125)
    struct.pack_into('>q',memory,16,-9223372036854775807)
    memory[32:32+256]=bytes([254,3])+b'ABC'+bytes(251)
    server.test_buffers=[]
    for area,index in ((SrvArea.DB,1),(SrvArea.MK,0),(SrvArea.PE,0),(SrvArea.PA,0)):
        buffer=(ctypes.c_uint8*len(memory)).from_buffer_copy(memory)
        server.test_buffers.append(buffer);server.register_area(area,index,buffer)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    server.start_to('127.0.0.1',port)
    return server,port


class AddressTests(unittest.TestCase):
    def test_addresses_types_and_exact_int64(self):
        self.assertEqual(parse_tag('db1.dbd0:real')['nodeid'],'s7:DB1.DBD0:REAL')
        self.assertEqual(parse_tag('E2.3:BOOL')['nodeid'],'s7:I2.3:BOOL')
        self.assertEqual(parse_tag('AW4:WORD')['nodeid'],'s7:QW4:WORD')
        self.assertEqual(decode(parse_tag('DB1.DBD0:LINT'),struct.pack('>q',-9223372036854775807)),-9223372036854775807)
        self.assertTrue(decode(parse_tag('M0.7:BOOL'),b'\x80'))
        self.assertEqual(decode(parse_tag('MD0:TIME'),struct.pack('>i',-123)),-123)
    def test_reject_invalid_ranges_widths_and_catalog(self):
        for value in ['DB0.DBW0:INT','DB65536.DBW0:INT','DB1.DBX0.8:BOOL','DB1.DBD0:INT','DB1.DBD0:STRING[10]',
                      'MW0:REAL','DB1.DBB0:STRING[255]','MD2097151:LREAL','M0.0:REAL','s7:__import__:REAL']:
            with self.subTest(value=value), self.assertRaises(ValueError):parse_tag(value)
        with self.assertRaises(ValueError):normalize_tags([{'address':'MD0','data_type':'REAL'}]*2)
        with self.assertRaises(ValueError):parse_tag('s7:/DB65536',True)
        with self.assertRaises(ValueError):app.validated_nodes(['s7:/DB1'])
        for key,value in [('s7_rack',8),('s7_slot',32),('s7_tcp_port',0),('s7_remote_tsap','xyz'),('s7_pdu',100),('s7_host','http://host')]:
            with self.subTest(key=key), self.assertRaises(ValueError):validate_config({**DEFAULTS,key:value})


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server,self.port=start_server()
        self.client=S7Client({**DEFAULTS,'s7_host':'127.0.0.1','s7_tcp_port':self.port,'s7_pdu':240})
        await self.client.connect()
    async def asyncTearDown(self):
        await self.client.disconnect();await asyncio.to_thread(self.server.stop)
    async def test_multiread_types_partial_error_and_large_string(self):
        rows=await self.client.read_values(['s7:DB1.DBD0:REAL','s7:DB1.DBW4:INT','s7:DB1.DBX6.7:BOOL',
                                         's7:DB1.DBD8:LREAL','s7:DB1.DBD16:LINT','s7:DB99.DBD0:REAL',
                                         's7:DB1.DBB32:STRING[254]','s7:MW4:INT','s7:I6.7:BOOL','s7:QD0:REAL'])
        self.assertEqual([r['value'] for r in rows[:5]],[42.5,-1234,True,12.125,'-9223372036854775807'])
        self.assertEqual(rows[5]['quality'],'Bad');self.assertEqual(rows[6]['value'],'ABC')
        self.assertEqual([r['value'] for r in rows[7:]],[-1234,True,42.5])
        self.assertTrue(rows[4]['precision_warning'])
        self.assertTrue(all(r['source_ts'] is None and r['server_ts'] is None and r['local_ts'] for r in rows))
    async def test_64_items_pdu_packetization(self):
        rows=await self.client.read_values([f's7:DB1.DBD{i*4}:REAL' for i in range(64)])
        self.assertEqual(len(rows),64);self.assertTrue(all(r['good'] for r in rows))
        self.assertGreater(self.client.sequence,4)
    async def test_transport_close_and_reconnect(self):
        await self.client.disconnect()
        with self.assertRaises(ConnectionError):await self.client.read_values(['s7:MD0:REAL'])
        await self.client.connect();self.assertEqual((await self.client.read_values(['s7:MD0:REAL']))[0]['value'],42.5)
    async def test_reject_wrong_response_sequence(self):
        original=self.client._packet
        async def bad_packet():
            data=bytearray(await original())
            if data[:3]==b'\x02\xf0\x80':data[8]^=1
            return bytes(data)
        self.client._packet=bad_packet
        with self.assertRaises(ValueError):await self.client.read_values(['s7:MD0:REAL'])
        self.assertIsNone(self.client.writer)


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize();cls.server,cls.port=start_server()
        cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    @classmethod
    def tearDownClass(cls):
        app.runtime.run(app.disconnect_command());cls.server.stop();app.calculations.close()
    def setUp(self):
        app.runtime.run(app.disconnect_command());app.runtime.active_config=None
        with app.formula_store.lock:app.formula_store.formulas=[];app.formula_store._write()
        app.runtime.run(app.apply_selection([]))
        self.cfg={**app.DEFAULT_CONFIG,'source':'s7','s7_host':'127.0.0.1','s7_tcp_port':self.port,'sample_ms':10,
                  's7_tags':[{'address':'DB1.DBD0','data_type':'REAL','name':'Carico A'}, {'address':'DB1.DBD8','data_type':'LREAL','name':'Carico B'}]}
    def tearDown(self):app.runtime.run(app.disconnect_command())
    def post(self,url,body,code=200):
        r=self.http.post(url,json=body,headers=self.headers);self.assertEqual(r.status_code,code,r.json);return r.json
    def wait(self,condition):
        end=time.monotonic()+8
        while time.monotonic()<end:
            if condition():return
            time.sleep(.02)
        self.fail('Timed out: '+app.runtime.last_error)
    def test_source_validation_catalog_workspace_and_test_connection(self):
        tags=self.post('/api/s7/tags/validate',{'tags':self.cfg['s7_tags']})['tags']
        self.assertEqual(tags[0]['display_name'],'Carico A')
        self.post('/api/config',self.cfg)
        result=self.post('/api/s7/test',self.cfg);self.assertEqual(result['probe'][0]['value'],42.5)
        self.post('/api/connect',self.cfg)
        roots=self.http.get('/api/browse?nodeid=s7:/').json['nodes'];self.assertEqual(roots[0]['nodeid'],'s7:/DB1')
        children=self.http.get('/api/browse?nodeid=s7:/DB1').json['nodes'];self.assertEqual(len(children),2)
        self.post('/api/scan',self.cfg);self.wait(lambda:app.runtime.scan['state']=='completed')
        self.assertEqual(len(app.runtime.scan['nodes']),2)
        self.post('/api/select',{'nodeids':['ads:MAIN.Value']},400)
        self.post('/api/s7/test',self.cfg,400)
        self.post('/api/disconnect',{})
        self.post('/api/workspace',{'schema':1,'config':self.cfg,'selected_nodes':tags,'source_profiles':{'s7':{'root':'s7:/DB1','selected_nodes':tags}},'live':{'charts':[]},'history':{'charts':[]}})
        saved=app.load_workspace();self.assertEqual(saved['source_profiles']['s7']['root'],'s7:/DB1')
        self.assertEqual(app.load_config()['s7_tags'],tags)
    def test_live_formula_parquet_csv_and_historical_data(self):
        a,b='s7:DB1.DBD0:REAL','s7:DB1.DBD8:LREAL'
        f=self.post('/api/calculations/save',dict(name='Delta S7',expression='[A]-[B]',bindings={'A':{'nodeid':a},'B':{'nodeid':b}}))['formula']
        self.assertEqual(f['protocol'],'s7')
        self.post('/api/select',{'nodeids':[a,b]});self.post('/api/connect',self.cfg)
        self.wait(lambda:app.runtime.calculated_latest.get(f['nodeid'],{}).get('value')==30.375)
        result=self.http.get('/api/values').json;self.assertEqual(result['source'],'s7');self.assertEqual(result['s7']['pdu'],480)
        for fmt in ('csv','parquet'):
            self.post('/api/record/start',dict(format=fmt,selected_nodes=[dict(nodeid=n,display_name=n) for n in [a,b,f['nodeid']]]))
            self.wait(lambda:app.recording.status()['cycles_received']>=20)
            self.post('/api/record/stop',{});self.wait(lambda:not app.recording.busy)
            recorded=app.recording.status();self.assertFalse(recorded['error'])
            path=Path(recorded['path']);metadata=json.loads(path.with_suffix('.metadata.json').read_text())
            self.assertEqual(metadata['source_protocol'],'s7');self.assertTrue(path.name.startswith('S7_'))
            self.assertIn('CPU RUN/STOP state not queried',metadata['quality_semantics'])
            self.assertIn('unavailable in S7',metadata['timestamp_semantics'])
            rows=list(app.recording_library._iter_rows(path));raw=[r for r in rows if r['node_id']==a]
            self.assertTrue(raw);self.assertTrue(all(not r.get('source_timestamp') and r.get('local_timestamp') for r in raw))
            self.assertTrue(all(float(r['value_numeric'])==42.5 for r in raw))
            view=self.post('/api/recordings/data',{'source':'repository','name':path.name,'signal_ids':[a,b,f['nodeid']],'max_points':1000})
            self.assertEqual(len(view['recording']['signals']),3)


if __name__=='__main__':unittest.main(verbosity=2)
