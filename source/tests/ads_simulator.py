"""Deterministic ADS/TCP test peer. It is not a Beckhoff runtime."""
import asyncio
import math
import struct
import time
from contextlib import suppress

class Peer:
    def __init__(self):
        self.server=None;self.writers=set();self.handles={};self.next_handle=100
        self.ads_state=5;self.version=1;self.sum_enabled=True;self.bad=set();self.commands=[]
        self.released=[];self.fault=None;self.delay=0
        # ADS/TCP notifications (AddDeviceNotification 6, Delete 7, DeviceNotification 8).
        self.subscriptions={};self.write_locks={};self.next_notification=500;self.current=None;self.loop=None;self.attrs=[]
        self.symbols={
            'MAIN.Wave':(5,'LREAL',8), 'MAIN.Running':(33,'BOOL',1),
            'MAIN.Counter':(21,'ULINT',8), 'MAIN.Text':(30,'STRING(15)',16),
            'MAIN.Wide':(31,'WSTRING(15)',32), 'MAIN.Array':(65,'ARRAY [0..2] OF REAL',12),
            'MAIN.Array[0]':(4,'REAL',4), 'MAIN.Array[1]':(4,'REAL',4), 'MAIN.Array[2]':(4,'REAL',4),
            'MAIN.Struct':(65,'ST_Test',8), 'MAIN.Struct.Value':(3,'DINT',4),
            'MAIN.Bad':(5,'LREAL',8), 'MAIN.Nonfinite':(5,'LREAL',8)}
        self.bad.add('MAIN.Bad')
    async def start(self):
        self.loop=asyncio.get_running_loop()
        self.server=await asyncio.start_server(self.accept,'127.0.0.1',0)
        self.port=self.server.sockets[0].getsockname()[1];return self
    async def close(self):
        for w in list(self.writers):w.close()
        self.server.close();await self.server.wait_closed();await asyncio.sleep(.02)
    def entry(self,name):
        dt,typ,size=self.symbols[name];text=[name.encode(),typ.encode(),b'Test symbol']
        n,t,c=map(len,text)
        return struct.pack('<IIIIIIHHH',33+n+t+c,0x4020,4,size,dt,0,n,t,c)+b'\0'.join(text)+b'\0'
    def value(self,name):
        if name=='MAIN.Wave':return struct.pack('<d',math.sin(time.monotonic()*2*math.pi*3))
        if name=='MAIN.Running':return b'\1'
        if name=='MAIN.Counter':return struct.pack('<Q',2**63+123)
        if name=='MAIN.Text':return b'Caff\xe8\0'.ljust(16,b'\0')
        if name=='MAIN.Wide':return 'PLC Ω'.encode('utf-16-le').ljust(32,b'\0')
        if name=='MAIN.Array':return struct.pack('<fff',1.,2.,3.)
        if name.startswith('MAIN.Array['):return struct.pack('<f',float(name[-2])+1)
        if name=='MAIN.Struct':return b'\x01'*8
        if name=='MAIN.Struct.Value':return struct.pack('<i',123)
        return struct.pack('<d',float('nan'))
    def read(self,g,o,n):
        if g==0xF008:return 0,struct.pack('<I',self.version)
        if g==0xF00C:
            data=b''.join(self.entry(k) for k in self.symbols);return 0,struct.pack('<II',len(self.symbols),len(data))
        if g==0xF00B:return 0,b''.join(self.entry(k) for k in self.symbols)
        if g==0xF005:
            name=self.handles.get(o)
            if name is None:return 1809,b''
            if name in self.bad:return 1796,b''
            return 0,self.value(name)[:n]
        return 1794,b''
    def process(self,cmd,payload):
        self.commands.append(cmd)
        if cmd==1:return struct.pack('<IBBH16s',0,3,1,4026,b'ADS test peer')
        if cmd==4:return struct.pack('<IHH',0,self.ads_state,0)
        if cmd==2:
            g,o,n=struct.unpack('<III',payload);e,data=self.read(g,o,n)
        elif cmd==3:
            g,o,n=struct.unpack_from('<III',payload)
            assert g==0xF006 and n==4,'Unexpected PLC write in read-only client'
            h=struct.unpack_from('<I',payload,12)[0];self.released.append(h);self.handles.pop(h,None)
            return struct.pack('<I',0)
        elif cmd==9:
            g,o,n,w=struct.unpack_from('<IIII',payload);data=payload[16:];assert len(data)==w
            if g in (0xF003,0xF009):
                name=data.rstrip(b'\0').decode();e=0
                if name not in self.symbols:e,data=1808,b''
                elif g==0xF009:data=self.entry(name)
                else:
                    self.next_handle+=1;self.handles[self.next_handle]=name;data=struct.pack('<I',self.next_handle)
            elif g==0xF080:
                if not self.sum_enabled:e,data=1793,b''
                else:
                    e=0;errs=[];values=[]
                    for i in range(o):
                        ig,io,size=struct.unpack_from('<III',data,i*12);err,value=self.read(ig,io,size)
                        errs.append(struct.pack('<I',err));values.append(value.ljust(size,b'\0'))
                    data=b''.join(errs+values)
            else:e,data=1794,b''
        elif cmd==6:
            g,o,n,mode,delay,cycle=struct.unpack_from('<IIIIII',payload);self.attrs.append((n,mode,delay,cycle))
            if g!=0xF005 or o not in self.handles:return struct.pack('<I',1810)+struct.pack('<I',0)
            self.next_notification+=1;self.subscriptions[self.next_notification]=(self.current,o,n)
            return struct.pack('<II',0,self.next_notification)
        elif cmd==7:
            h=struct.unpack_from('<I',payload)[0];self.subscriptions.pop(h,None);return struct.pack('<I',0)
        else:raise AssertionError('Unsupported command')
        return struct.pack('<II',e,len(data))+data
    def emit(self,ticks,size_override=None):
        """Push one DeviceNotification per subscription (thread-safe; returns when written)."""
        async def send():
            for notification,((w,client,server),handle,size) in list(self.subscriptions.items()):
                name=self.handles.get(handle)
                if name is None or w.is_closing():continue
                data=self.value(name)[:size]
                if size_override is not None:data=data.ljust(size_override,b'\0')[:size_override]
                sample=struct.pack('<II',notification,len(data))+data
                stamp=struct.pack('<QI',ticks,1)+sample
                body=struct.pack('<II',len(stamp)+4,1)+stamp
                header=client+server+struct.pack('<HHIII',8,4,len(body),0,0)
                # Whole frames only: never inside a fragmented response.
                async with self.write_locks.setdefault(w,asyncio.Lock()):w.write(struct.pack('<HI',0,len(header)+len(body))+header+body)
        asyncio.run_coroutine_threadsafe(send(),self.loop).result(5)
    async def accept(self,r,w):
        self.writers.add(w)
        try:
            while True:
                zero,length=struct.unpack('<HI',await r.readexactly(6));assert zero==0
                raw=await r.readexactly(length);cmd,flags,n,err,invoke=struct.unpack_from('<HHIII',raw,16)
                assert flags==4 and n==len(raw)-32
                if self.delay:await asyncio.sleep(self.delay)
                self.current=(w,raw[8:16],raw[:8]);data=self.process(cmd,raw[32:]);fault,self.fault=self.fault,None
                response=raw[8:16]+raw[:8]+struct.pack('<HHIII',cmd,5,len(data),0,invoke+(fault=='invoke'))+data
                if fault=='length':w.write(struct.pack('<HI',0,0xffffffff));await w.drain();continue
                header=struct.pack('<HI',0,len(response))
                # Deliberately fragmented TCP writes (header and payload).
                async with self.write_locks.setdefault(w,asyncio.Lock()):
                    w.write(header[:2]);await w.drain();await asyncio.sleep(0)
                    w.write(header[2:]+response[:19]);await w.drain();await asyncio.sleep(0)
                    w.write(response[19:]);await w.drain()
        except (asyncio.IncompleteReadError,ConnectionError,asyncio.CancelledError):pass
        finally:
            self.writers.discard(w);w.close()
            with suppress(Exception):await w.wait_closed()
