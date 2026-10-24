import struct
import unittest
from unittest.mock import patch
from test_ads import app, ADSClient
from ads_simulator import Peer
from ads_types import parse_type
from test_ads_native import DLL
from ads_native import NativeADSClient, configure_library

def entry(name,typ='',size=4,dt=65,children=(),offset=0):
    strings=[name.encode(),typ.encode(),b''];body=b'\0'.join(strings)+b'\0'+b''.join(children)
    return struct.pack('<8I5H',42+len(body),1,0,0,size,offset,dt,0,len(strings[0]),len(strings[1]),0,0,len(children))+body
class TypedPeer(Peer):
    def __init__(self):
        super().__init__();self.fallback=False
        self.symbols={'PlcSlow.fbEntryPoint':(65,'FB_EntryPoint',80016)}
        self.types={'FB_EntryPoint':entry('FB_EntryPoint',size=80016,children=[entry('Value','DINT',dt=3),entry('Nested','ST_Data',offset=4),entry('Samples','ARRAY [-2..19998] OF REAL',80004,offset=8)]),'ST_Data':entry('ST_Data',children=[entry('Ready','BOOL',1,33)])}
    def read(self,g,o,n):
        if g==0xF00F:return 0,struct.pack('<6I',1,100,len(self.types),sum(map(len,self.types.values())),0,0)
        if g==0xF00E:return 0,b''.join(self.types.values())
        return super().read(g,o,n)
    def process(self,cmd,payload):
        if cmd==9:
            g,o,n,w=struct.unpack_from('<IIII',payload);name=payload[16:].rstrip(b'\0').decode(errors='replace') if g!=0xF080 else ''
            if g==0xF011:
                data=self.types.get(name,b'');err=1794 if self.fallback else 0 if data else 1808
                return struct.pack('<II',err,0 if err else len(data))+(b'' if err else data)
            if g==0xF003 and name.startswith('PlcSlow.fbEntryPoint.'):
                self.next_handle+=1;self.handles[self.next_handle]=name
                return struct.pack('<III',0,4,self.next_handle)
        return super().process(cmd,payload)
    def value(self,name):
        if name.endswith('.Value'):return struct.pack('<i',42)
        if name.endswith('.Ready'):return b'\1'
        return struct.pack('<f',12.5)
class Types(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,c):
        self.assertEqual(len(await c.browse('ads:PlcSlow')),1)
        self.assertEqual(len(await c.browse('ads:PlcSlow.fbEntryPoint')),3)
        self.assertEqual((await c.browse('ads:PlcSlow.fbEntryPoint.Nested'))[0]['data_type'],'BOOL')
        page=await c.browse_page('ads:PlcSlow.fbEntryPoint.Samples',20000,250)
        self.assertEqual(page['total'],20001);self.assertEqual(len(page['nodes']),1);self.assertIsNone(page['next_offset'])
        self.assertTrue(page['nodes'][0]['nodeid'].endswith('[19998]'))
        c.symbols.clear();c.catalog_loaded=False
        rows=await c.read_values(['ads:PlcSlow.fbEntryPoint.Value','ads:PlcSlow.fbEntryPoint.Nested.Ready'])
        self.assertEqual([r['value'] for r in rows],[42,True])
    async def test_tcp_and_fallback(self):
        for fallback in (False,True):
            peer=await TypedPeer().start();peer.fallback=fallback;c=ADSClient({**app.DEFAULT_CONFIG,'ads_tcp_port':peer.port})
            try:await c.connect();await self.exercise(c)
            finally:await c.disconnect();await peer.close()
    async def test_native(self):
        dll=DLL();dll.peer=TypedPeer();configure_library(dll)
        with patch('ads_native.load_library',return_value=(dll,'fake.dll')):
            c=NativeADSClient({**app.DEFAULT_CONFIG,'ads_transport':'native'})
            try:await c.connect();await self.exercise(c)
            finally:await c.disconnect()
    async def test_scan_limit(self):
        peer=await TypedPeer().start();app.runtime.active_config={**app.DEFAULT_CONFIG,'ads_tcp_port':peer.port,'source':'ads'}
        job={'nodes':[],'visited':0,'state':'running'}
        try:await app.ads_scan_tree(job,'ads:PlcSlow',20)
        finally:await peer.close()
        self.assertEqual(job['state'],'limited');self.assertEqual(len(job['nodes']),20)
    def test_parser_bounds(self):
        data=entry('FB',children=[entry('X','DINT',dt=3)])
        self.assertEqual(parse_type(data)[0]['children'][0]['name'],'X')
        for malformed in (data[:30],data[:-1],struct.pack('<I',1)+data[4:]):
            with self.assertRaises(ValueError):parse_type(malformed)
if __name__=='__main__':unittest.main(verbosity=2)
