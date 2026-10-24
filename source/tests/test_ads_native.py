"""Exercise native ABI arguments through a fake DLL; not a Windows hardware test."""
import asyncio
import ctypes as C
import struct
import unittest
from unittest.mock import patch
from test_ads import app
from ads_simulator import Peer
from ads_native import NativeADSClient, AmsAddr, Version, configure_library, create_ads_client

class Function:
    def __init__(self,fn):self.fn=fn
    def __call__(self,*a):return self.fn(*a)

class DLL:
    def __init__(self):
        self.peer=Peer();self.closed=[];self.next=32000;self.open=set();self.delay=0;self.busy=False;self.close_while_busy=False
        for name in ['AdsPortOpenEx','AdsPortCloseEx','AdsGetLocalAddressEx','AdsSyncSetTimeoutEx','AdsSyncReadDeviceInfoReqEx','AdsSyncReadStateReqEx','AdsSyncReadReqEx2','AdsSyncReadWriteReqEx2','AdsSyncWriteReqEx']:
            setattr(self,name,Function(getattr(self,name)))
    def AdsPortOpenEx(self):self.next+=1;self.open.add(self.next);return self.next
    def AdsPortCloseEx(self,p):self.close_while_busy|=self.busy;self.closed.append(p);self.open.remove(p);return 0
    def AdsSyncSetTimeoutEx(self,p,t):return 0
    def AdsGetLocalAddressEx(self,p,a):C.memmove(a,bytes([192,168,92,35,1,1])+struct.pack('<H',p),8);return 0
    def AdsSyncReadDeviceInfoReqEx(self,p,a,name,ver):C.memmove(name,b'Native fake PLC\0\0',16);C.memmove(ver,struct.pack('<BBH',3,1,4026),4);return 0
    def AdsSyncReadStateReqEx(self,p,a,state,device):
        import time
        self.busy=True;time.sleep(self.delay)
        C.memmove(state,struct.pack('<H',5),2);C.memmove(device,b'\0\0',2);self.busy=False;return 0
    def read_result(self,raw,buffer,count):
        code,n=struct.unpack_from('<II',raw)
        if code:return code
        C.memmove(buffer,raw[8:],n);C.memmove(count,struct.pack('<I',n),4);return 0
    def AdsSyncReadReqEx2(self,p,a,g,o,n,buffer,count):return self.read_result(self.peer.process(2,struct.pack('<III',g,o,n)),buffer,count)
    def AdsSyncReadWriteReqEx2(self,p,a,g,o,n,buffer,w,write,count):return self.read_result(self.peer.process(9,struct.pack('<IIII',g,o,n,w)+C.string_at(write,w)),buffer,count)
    def AdsSyncWriteReqEx(self,p,a,g,o,n,buffer):return struct.unpack('<I',self.peer.process(3,struct.pack('<III',g,o,n)+C.string_at(buffer,n)))[0]

class Native(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dll=DLL();configure_library(self.dll)
        self.mock=patch('ads_native.load_library',return_value=(self.dll,'fake64.dll'));self.mock.start()
        self.cfg={**app.DEFAULT_CONFIG,'source':'ads','ads_transport':'native','ads_target_netid':'192.168.100.12.1.1'}
        self.client=create_ads_client(self.cfg);await self.client.connect()
    async def asyncTearDown(self):await self.client.disconnect();self.mock.stop()
    async def test_local_identity_and_abi(self):
        self.assertIsInstance(self.client,NativeADSClient);self.assertEqual(C.sizeof(AmsAddr),8);self.assertEqual(C.sizeof(Version),4)
        self.assertEqual(self.client.info['source_netid'],'192.168.92.35.1.1');self.assertEqual(self.client.info['source_port'],32001)
        self.assertEqual(self.client.info['target_netid'],'192.168.100.12.1.1');self.assertEqual(self.client.address.port,851)
        self.assertEqual(len(self.dll.AdsSyncReadWriteReqEx2.argtypes),9)
    async def test_saved_manual_fields_keep_effective_router_identity(self):
        cfg={**self.cfg,'ads_host':'192.168.5.221','ads_tcp_port':48899,
             'ads_source_netid':'192.168.92.36.1.1','ads_source_port':33011,'ads_bind_ip':'192.168.92.36'}
        c=create_ads_client(cfg)
        try:
            await c.connect()
            self.assertEqual(c.cfg,cfg)
            self.assertEqual(c.info['source_netid'],'192.168.92.35.1.1')
            self.assertEqual(c.info['source_port'],32002)
            self.assertEqual(c.info['target_netid'],cfg['ads_target_netid'])
            rows=await c.read_values(['ads:MAIN.Wave'])
            self.assertEqual(rows[0]['quality'],'Good')
        finally:await c.disconnect()
    async def test_catalog_sumread_fallback_and_release(self):
        self.assertGreater(len(await self.client.upload_symbols()),5)
        rows=await self.client.read_values(['ads:MAIN.Counter','ads:MAIN.Wave','ads:MAIN.Bad'])
        self.assertEqual(rows[0]['value'],str(2**63+123));self.assertEqual(rows[2]['quality'],'Bad')
        self.dll.peer.sum_enabled=False;await self.client.read_values(['ads:MAIN.Wave']);self.assertFalse(self.client.sum_supported)
        await self.client.disconnect();self.assertFalse(self.dll.open);self.assertFalse(self.dll.peer.handles)
    async def test_independent_ports_and_cancellation(self):
        second=create_ads_client(self.cfg,catalog=True);await second.connect();self.assertNotEqual(second.port,self.client.port);await second.disconnect()
        self.dll.delay=.1
        task=asyncio.create_task(self.client.read_state());await asyncio.sleep(.02);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        await self.client.disconnect();self.assertFalse(self.dll.close_while_busy)
    async def test_flask_acquisition_uses_native_factory(self):
        app.initialize();http=app.app.test_client();headers={'X-OPCUA-Token':app.SESSION_TOKEN}
        def post(url,data):
            response=http.post(url,json=data,headers=headers)
            self.assertEqual(response.status_code,200,response.get_json());return response.get_json()
        try:
            post('/api/select',{'nodeids':['ads:MAIN.Wave']})
            post('/api/connect',{**self.cfg,'sample_ms':10})
            for _ in range(50):
                values=http.get('/api/values').get_json()
                if values['diagnostics']['read_count']>3:break
                await asyncio.sleep(.02)
            self.assertGreater(values['diagnostics']['read_count'],3)
            self.assertEqual(values['ads']['transport'],'TwinCAT locale / TcAdsDll')
            self.assertEqual(values['values'][0]['quality'],'Good')
        finally:
            post('/api/disconnect',{});post('/api/select',{'nodeids':[]})

    async def test_disallow_plc_writes(self):
        with self.assertRaises(ValueError):await self.client.command(3,struct.pack('<III',0x4020,0,4)+b'\0'*4)
        with self.assertRaises(ValueError):await self.client.command(9,struct.pack('<IIII',0xF081,0,4,0))
    async def test_open_failure_closes_port(self):
        self.dll.AdsGetLocalAddressEx.fn=lambda *a:6
        c=create_ads_client(self.cfg)
        with self.assertRaises(Exception):await c.connect()
        self.assertEqual(len(self.dll.open),1)

if __name__=='__main__':unittest.main(verbosity=2)
