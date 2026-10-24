"""Cyclic TcAdsDll subscriptions, isolated acquisition thread and bounded FIFO.

Callbacks only copy bytes and clocks. No ADS request, plotting or disk I/O is
performed on the router callback thread. A dedicated thread decodes and delivers
every sample; the browser never clocks acquisition.
"""
import asyncio
import ctypes as C
import threading
import time
import struct
import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from contextlib import suppress
from ads_native import NativeADSClient, AmsAddr, I32, U32
from ads_client import symbol_name, ADSError

FILETIME_EPOCH=116444736000000000
class NotificationAttrib(C.Structure):
    _pack_=1
    _fields_=[('length',U32),('mode',U32),('max_delay',U32),('cycle',U32)]
class NotificationHeader(C.Structure):
    _pack_=1
    _fields_=[('handle',U32),('timestamp',C.c_uint64),('size',U32)]
CALLBACK=getattr(C,'WINFUNCTYPE',C.CFUNCTYPE)(None,C.POINTER(AmsAddr),C.POINTER(NotificationHeader),U32)

def timestamp(ticks):
    """Keep the seventh fractional digit; float timestamps are only for plotting."""
    seconds,fraction=divmod(int(ticks)-FILETIME_EPOCH,10000000)
    date=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds)
    return date.strftime('%Y-%m-%dT%H:%M:%S')+f'.{fraction:07d}+00:00'

def configure_notifications(dll):
    try:
        add=dll.AdsSyncAddDeviceNotificationReqEx
        delete=dll.AdsSyncDelDeviceNotificationReqEx
    except AttributeError as exc:
        raise ValueError('TcAdsDll non espone le notifiche ADS Ex richieste') from exc
    add.argtypes=[I32,C.POINTER(AmsAddr),U32,U32,C.POINTER(NotificationAttrib),CALLBACK,U32,C.POINTER(U32)]
    delete.argtypes=[I32,C.POINTER(AmsAddr),U32]
    add.restype=delete.restype=I32

class NotificationAcquisition:
    def __init__(self,cfg,selection,sink,report,client_factory=NativeADSClient,capacity=None,byte_limit=None):
        self.cfg=dict(cfg);self.selection=selection;self.sink=sink;self.report=report;self.client_factory=client_factory
        import os
        capacity=int(capacity or os.environ.get('SPACE_ADS_QUEUE_SAMPLES',32768));byte_limit=int(byte_limit or int(os.environ.get('SPACE_ADS_QUEUE_MB',16))*1024**2)
        if not 1<=capacity<=1000000 or not 1024<=byte_limit<=256*1024**2:raise ValueError('Limiti buffer ADS non validi')
        self.capacity=capacity;self.byte_limit=byte_limit;self.queue=deque();self.queued_bytes=0
        self.lock=threading.Lock();self.stop_event=threading.Event();self.thread=None;self.error=None
        self.accepting=False;self.members={};self.subscriptions=[];self.received=0;self.accepted=0;self.dropped=0;self.sample_sequences={};self.source_epoch=0;self.clock_backwards=0;self.timestamp_errors=0;self.decode_errors=0;self.lost_ranges=deque(maxlen=128)
        self.last_received={};self.last_source={};self.periods=deque(maxlen=2048);self.arrivals=deque(maxlen=2048)
        self.latencies=deque(maxlen=2048);self.queue_delays=deque(maxlen=2048);self.last_arrival={}
        self.callback_fault=None;self.reference=None;self.callback=CALLBACK(self._callback)
        self.started=False;self.restarts=0;self.generation=None;self.info={};self.control_ms=0;self.sequence=0
        self.control_error=None;self.control_timeouts=0;self.control_last_ok=None
        self.queue_high_water=0;self.processed=0

    def _callback(self,addr,header,user):
        try:
            received_ns=time.time_ns();received_mono=time.monotonic()
            with self.lock:
                if not self.accepting:return
                member=self.members.get(int(user))
                if member is None:return
                nid,symbol=member;h=header.contents
                self.received+=1;sequence=self.sample_sequences.get(nid,0)+1;self.sample_sequences[nid]=sequence
                if h.size!=symbol['size'] or h.size>65536:
                    self.dropped+=1;self.lost_ranges.append((nid,sequence,sequence,'invalid_size'));self.callback_fault='Dimensione notifica ADS incoerente: '+nid;return
                if len(self.queue)>=self.capacity or self.queued_bytes+h.size>self.byte_limit:
                    self.dropped+=1;self.lost_ranges.append((nid,sequence,sequence,'overflow'));self.callback_fault='Overflow coda ADS: campioni persi; acquisizione interrotta';return
                data=C.string_at(C.addressof(h)+C.sizeof(NotificationHeader),h.size)
                self.queue.append((self.generation,nid,symbol,int(h.timestamp),received_ns,received_mono,sequence,self.source_epoch,data))
                self.queued_bytes+=len(data);self.accepted+=1;self.last_received[nid]=received_mono
                self.queue_high_water=max(self.queue_high_water,len(self.queue))
        except Exception as exc:
            self.callback_fault='Callback ADS: '+str(exc)

    def start(self):
        self.thread=threading.Thread(target=self._run,name='ADS-Cyclic-Acquisition',daemon=True);self.thread.start()

    def stop(self):self.stop_event.set()

    def _run(self):
        try:asyncio.run(self.run())
        except Exception as exc:self.error=str(exc) or type(exc).__name__
        finally:self.report({**self.stats(),'error':self.error})

    async def remove_subscriptions(self,c):
        with self.lock:self.accepting=False
        failures=[]
        for handle in self.subscriptions:
            try:
                c.check(await c.threaded(c.dll.AdsSyncDelDeviceNotificationReqEx,c.port,C.byref(c.address),handle))
            except Exception as exc:
                failures.append(exc);break
        self.subscriptions=[]
        if failures:
            # Close the ADS port before symbol handles/callback storage can be reused.
            await c.abort();raise failures[0]
        # Deliver already copied samples before releasing handles/reconfiguring.
        # Selection generations prevent old data entering a new workspace buffer.
        while self.queue:self.drain(c,getattr(self,'sample_ms',10))
        await c.release_handles()
        with self.lock:self.queue.clear();self.queued_bytes=0;self.members={}

    async def subscribe(self,c,nids,sample_ms,generation):
        await self.remove_subscriptions(c)
        if self.started:self.restarts+=1
        self.sample_ms=sample_ms
        self.source_epoch+=1;self.generation=generation;self.last_received={};self.last_source={};self.last_arrival={}
        self.periods.clear();self.arrivals.clear();self.latencies.clear();self.queue_delays.clear()
        self.reference=nids[0] if nids else None
        with self.lock:self.accepting=True
        for nid in nids:
            if self.stop_event.is_set():break
            name=symbol_name(nid);symbol=await c.get_symbol(name)
            if not 0<symbol['size']<=65536:raise ValueError('Notifiche ADS: selezionare membri di dimensione 1..65536 byte: '+name)
            raw=await c.readwrite(0xF003,0,4,name.encode('utf-8')+b'\0')
            if len(raw)!=4:raise ValueError('Handle ADS non valido')
            handle=struct.unpack('<I',raw)[0];c.handles[name]=handle
            self.sequence+=1;user=self.sequence
            with self.lock:self.members[user]=(nid,symbol)
            # Native DLL units are 100 ns; SERVERCYCLE=3. Max delay one period.
            attr=NotificationAttrib(symbol['size'],3,int(sample_ms*10000),int(sample_ms*10000));notification=U32()
            c.check(await c.threaded(c.dll.AdsSyncAddDeviceNotificationReqEx,c.port,C.byref(c.address),0xF005,handle,C.byref(attr),self.callback,user,C.byref(notification)))
            self.subscriptions.append(notification.value)
        self.started=True

    def drain(self,c,sample_ms):
        with self.lock:
            batch=[self.queue.popleft() for _ in range(min(4096,len(self.queue)))]
            self.queued_bytes-=sum(len(item[-1]) for item in batch)
        groups={}
        for generation,nid,symbol,ticks,received_ns,mono,sequence,source_epoch,data in batch:
            if ticks<FILETIME_EPOCH:self.timestamp_errors+=1
            previous=self.last_source.get(nid)
            backwards=previous is not None and ticks<previous
            if backwards:self.clock_backwards+=1
            if previous is not None and nid==self.reference:
                self.periods.append((ticks-previous)/10000)
                self.arrivals.append((mono-self.last_arrival[nid])*1000)
            self.last_source[nid]=ticks;self.last_arrival[nid]=mono
            ts=(ticks-FILETIME_EPOCH)/10000
            try:row=c.row(nid,symbol,data)
            except Exception as exc:
                from primary_format import typed
                self.decode_errors+=1;self.callback_fault='ADS decode: '+str(exc)
                row=dict(nodeid=nid,value=None,plot_value=None,data_type=symbol.get('type','Unknown'),quality='Bad',good=False,status='ADSDecodeError',error=str(exc),_source_binary_typed=typed(data))
            row.update(source_ts=timestamp(ticks) if ticks>=FILETIME_EPOCH else None,server_ts=None,source_filetime=str(ticks),
                       source_sequence=sequence,source_epoch=source_epoch,_received_mono_ns=int(mono*1e9),clock_discontinuity=backwards or ticks<FILETIME_EPOCH,received_ns=received_ns,
                       local_ts=datetime.fromtimestamp(received_ns/1e9,timezone.utc).isoformat(timespec='microseconds'),
                       received_ms=received_ns/1e6,timestamp_origin='ADS notification')
            self.latencies.append(received_ns/1e6-ts);self.queue_delays.append((time.monotonic()-mono)*1000)
            groups.setdefault((generation,ticks),[]).append(row)
        for (generation,ticks),rows in groups.items():self.sink((ticks-FILETIME_EPOCH)/10000,rows,sample_ms,generation)
        self.processed+=len(batch)

    def stats(self):
        values=sorted(self.periods);arrivals=list(self.arrivals)
        with self.lock:pending=len(self.queue);size=self.queued_bytes
        return dict(mode='ADS notifiche cicliche',received=self.received,accepted=self.accepted,dropped=self.dropped,lost_ranges=list(self.lost_ranges),clock_backwards=self.clock_backwards,timestamp_errors=self.timestamp_errors,queue_samples=pending,
                    queue_bytes=size,queue_capacity=self.capacity,reference_signal=self.reference,
                    processed=self.processed,decode_errors=self.decode_errors,queue_high_water=self.queue_high_water,
                    control_error=self.control_error,control_timeouts=self.control_timeouts,control_last_ok=self.control_last_ok,
                    source_period_ms=sum(values)/len(values) if values else None,
                    source_min_ms=min(values) if values else None,source_max_ms=max(values) if values else None,
                    source_p95_ms=values[min(len(values)-1,int(.95*(len(values)-1)))] if values else None,
                    arrival_period_ms=sum(arrivals)/len(arrivals) if arrivals else None,
                    clock_delta_ms=sum(self.latencies)/len(self.latencies) if self.latencies else None,
                    queue_delay_ms=max(self.queue_delays) if self.queue_delays else None,
                    control_ms=self.control_ms,subscription_restarts=self.restarts,**self.info)

    async def monitor(self,c):
        # Requests run in the native client's executor. The consumer must NEVER
        # await their network latency while notifications continue arriving.
        start=time.monotonic()
        try:
            await c.read_state()
            return await c.read(0xF008,0,4)
        finally:self.control_ms=(time.monotonic()-start)*1000

    async def run(self):
        c=self.client_factory(self.cfg)
        monitor=None
        try:
            await c.connect();configure_notifications(c.dll)
            c.info.update(read_mode='ADS notifiche cicliche',timestamps='ADS FILETIME UTC + ricezione locale')
            self.info=dict(c.info);current=None;heartbeat=0;version=None;configured=time.monotonic();reported=0
            while not self.stop_event.is_set():
                nids,sample_ms,generation=self.selection();new=(tuple(nids),sample_ms,generation)
                if new!=current:
                    if monitor is not None:
                        with self.lock:self.accepting=False
                        monitor.cancel()
                        with suppress(asyncio.CancelledError,Exception):await monitor
                        monitor=None
                    self.drain(c,sample_ms)
                    await self.subscribe(c,nids,sample_ms,generation);current=new;configured=time.monotonic()
                if self.callback_fault:raise RuntimeError(self.callback_fault)
                self.drain(c,sample_ms)
                now=time.monotonic()
                if monitor is not None and monitor.done():
                    finished=monitor;monitor=None;heartbeat=now
                    try:new_version=finished.result()
                    except ADSError as exc:
                        if exc.code not in (0x745,0x719):raise
                        # A control timeout is not proof of notification loss.
                        # The independent per-signal watchdog remains authoritative.
                        self.control_timeouts+=1;self.control_error=str(exc)
                        logging.warning('ADS controllo in timeout; consumo notifiche attivo: %s',exc)
                    else:
                        if self.control_error:logging.info('ADS controllo ripristinato')
                        self.control_error=None;self.control_last_ok=time.time()*1000
                        if version is not None and new_version!=version:
                            await self.remove_subscriptions(c)
                            c.symbols.clear();c.catalog_loaded=False
                            from ads_types import DatatypeBrowser
                            c.datatypes=DatatypeBrowser(c);current=None
                        version=new_version;self.info=dict(c.info)
                if monitor is None and current and now-heartbeat>=(5 if self.control_error else 1):
                    monitor=asyncio.create_task(self.monitor(c))
                if current and nids:
                    with self.lock:times=[self.last_received.get(n,configured) for n in nids]
                    if any(time.monotonic()-t>max(5,sample_ms/1000*5) for t in times):
                        raise TimeoutError('Watchdog ADS: flusso notifiche assente per almeno un segnale')
                if now-reported>=.25:
                    self.report(self.stats());reported=now
                # Catch up without an obligatory 5 ms pause per full batch.
                with self.lock:pending=bool(self.queue)
                await asyncio.sleep(0 if pending else .001)
        finally:
            with self.lock:self.accepting=False
            if monitor is not None:
                monitor.cancel()
                # NativeADSClient.threaded waits for an in-flight DLL call on
                # cancellation, before handles or the port can be released.
                with suppress(asyncio.CancelledError,Exception):await monitor
            try:
                if c.writer:await self.remove_subscriptions(c)
            finally:
                await c.disconnect()
                # Keep callback and members alive until the native port is closed.
                with self.lock:self.accepting=False
