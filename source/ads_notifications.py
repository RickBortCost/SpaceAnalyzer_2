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
# Pointers arrive as plain addresses: no ctypes objects are built while the
# router thread holds the GIL (ABI-identical to POINTER(AmsAddr/NotificationHeader)).
CALLBACK=getattr(C,'WINFUNCTYPE',C.CFUNCTYPE)(None,C.c_void_p,C.c_void_p,U32)
HEADER=struct.Struct('<IQI')
# A cyclic stream is gapped when a delta exceeds this many learned periods.
GAP_FACTOR=1.5;GAP_WARMUP=4
# Block notifications: one callback per PLC cycle for symbols in contiguous memory.
# Excluded symbol flags: BITVALUE, REFERENCETO, TCCOMIFACEPTR, ITFMETHODACCESS, METHODREF.
BLOCK_EXCLUDED_FLAGS=0x2|0x4|0x10|0x40|0x80;MAX_BLOCK=65536

def plan_blocks(entries,max_block=MAX_BLOCK):
    """Group (nid,symbol) by index group and nearby offsets: ([(group,start,length,members)], singles).

    Only symbols with an absolute address are eligible; a block spans at most
    `max_block` bytes and needs two members, otherwise the symbol keeps its own
    handle notification as before.
    """
    singles=[];candidates=[]
    for nid,symbol in entries:
        group=symbol.get('group');offset=symbol.get('offset');size=symbol.get('size',0)
        if group is None or offset is None or group in (0xF005,0xF003) or symbol.get('flags',0)&BLOCK_EXCLUDED_FLAGS or not 0<size<=max_block:
            singles.append((nid,symbol));continue
        candidates.append((group,offset,size,nid,symbol))
    candidates.sort(key=lambda item:(item[0],item[1]))
    planned=[]
    for group,offset,size,nid,symbol in candidates:
        block=planned[-1] if planned else None
        if block and block[0]==group and offset+size-block[1]<=max_block:
            block[2]=max(block[2],offset+size-block[1]);block[3].append((nid,symbol,offset-block[1]))
        else:planned.append([group,offset,size,[(nid,symbol,0)]])
    blocks=[]
    for group,start,length,members in planned:
        if len(members)<2:singles.extend((nid,symbol) for nid,symbol,_ in members)
        else:blocks.append((group,start,length,members))
    return blocks,singles

def grouping_enabled(cfg):
    import os
    value=str(cfg.get('ads_notify_grouping',os.environ.get('SPACE_ADS_NOTIFY_GROUPING','0'))).strip().lower()
    return value in ('1','true','on','yes')

def timestamp(ticks):
    """Keep the seventh fractional digit; float timestamps are only for plotting."""
    seconds,fraction=divmod(int(ticks)-FILETIME_EPOCH,10000000)
    date=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=seconds)
    return date.strftime('%Y-%m-%dT%H:%M:%S')+f'.{fraction:07d}+00:00'

class UTCText:
    """Exact integer formatting of UTC nanoseconds; the date part is cached per second."""
    def __init__(self):self.second=None;self.prefix=''
    def micro(self,ns):
        # Round half up to the microsecond, as the integer value is exact.
        us=(int(ns)+500)//1000;second,fraction=divmod(us,1000000)
        if second!=self.second:
            self.second=second;self.prefix=(datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=second)).strftime('%Y-%m-%dT%H:%M:%S')
        return f'{self.prefix}.{fraction:06d}+00:00'

def configure_notifications(dll):
    try:
        add=dll.AdsSyncAddDeviceNotificationReqEx
        delete=dll.AdsSyncDelDeviceNotificationReqEx
    except AttributeError as exc:
        raise ValueError('TcAdsDll non espone le notifiche ADS Ex richieste') from exc
    add.argtypes=[I32,C.POINTER(AmsAddr),U32,U32,C.POINTER(NotificationAttrib),CALLBACK,U32,C.POINTER(U32)]
    delete.argtypes=[I32,C.POINTER(AmsAddr),U32]
    add.restype=delete.restype=I32

def gap_report(previous,current):
    """New upstream gaps between two stats() snapshots: (missing samples, ranges)."""
    events=current.get('source_gap_events',0)-(previous or {}).get('source_gap_events',0)
    if events<=0:return 0,[]
    return current.get('source_gap_samples',0)-(previous or {}).get('source_gap_samples',0),list(current.get('source_gaps',[]))[-min(events,128):]

class NotificationAcquisition:
    def __init__(self,cfg,selection,sink,report,client_factory=NativeADSClient,capacity=None,byte_limit=None):
        self.cfg=dict(cfg);self.selection=selection;self.sink=sink;self.report=report;self.client_factory=client_factory
        import os
        capacity=int(capacity or os.environ.get('SPACE_ADS_QUEUE_SAMPLES',32768));byte_limit=int(byte_limit or int(os.environ.get('SPACE_ADS_QUEUE_MB',16))*1024**2)
        if not 1<=capacity<=1000000 or not 1024<=byte_limit<=256*1024**2:raise ValueError('Limiti buffer ADS non validi')
        self.capacity=capacity;self.byte_limit=byte_limit;self.queue=deque();self.queued_bytes=0
        # Configured values are a floor: the queue grows with channels/rate (see scale_queue).
        self.base_capacity=capacity;self.base_byte_limit=byte_limit
        self.headroom_s=max(0.1,float(os.environ.get('SPACE_ADS_QUEUE_SECONDS',2)));self.paused_not_run=False
        self.lock=threading.Lock();self.stop_event=threading.Event();self.thread=None;self.error=None
        self.accepting=False;self.members={};self.subscriptions=[];self.received=0;self.accepted=0;self.dropped=0;self.sample_sequences={};self.source_epoch=0;self.clock_backwards=0;self.timestamp_errors=0;self.decode_errors=0;self.lost_ranges=deque(maxlen=128)
        self.last_received={};self.last_source={};self.periods=deque(maxlen=2048);self.arrivals=deque(maxlen=2048)
        self.latencies=deque(maxlen=2048);self.queue_delays=deque(maxlen=2048);self.last_arrival={}
        self.callback_fault=None;self.reference=None;self.reference_key=None;self.callback=CALLBACK(self._callback)
        self.started=False;self.restarts=0;self.generation=None;self.info={};self.control_ms=0;self.sequence=0
        self.control_error=None;self.control_timeouts=0;self.control_last_ok=None
        self.queue_high_water=0;self.processed=0
        # Notifications lost upstream of the callback (router/DLL/PLC) are only
        # visible as holes in each channel's cyclic PLC timestamps.
        self.cycle_state={};self.source_gap_samples=0;self.source_gap_events=0;self.source_gaps=deque(maxlen=128);self.utc=UTCText()
        self.grouping=grouping_enabled(self.cfg);self.watch_key={};self.layout=dict(blocks=0,block_members=0,singles=0,fallback_members=0)

    def _callback(self,addr,header,user):
        # Runs on the router thread holding the GIL: copy bytes and clocks only.
        try:
            received_ns=time.time_ns();received_mono=time.monotonic()
            if header.__class__ is not int:header=C.cast(header,C.c_void_p).value
            _,ticks,size=HEADER.unpack(C.string_at(header,16))
            with self.lock:
                if not self.accepting:return
                member=self.members.get(user)
                if member is None:return
                nid,symbol=member
                # A block notification (nid None) carries one sample of each member.
                key=nid if nid is not None else symbol['label'];count=1 if nid is not None else symbol['count']
                self.received+=count;sequence=self.sample_sequences.get(key,0)+1;self.sample_sequences[key]=sequence
                if size!=symbol['size'] or size>MAX_BLOCK:
                    self.dropped+=count;self.lost_ranges.append((key,sequence,sequence,'invalid_size'));self.callback_fault='Dimensione notifica ADS incoerente: '+key;return
                if len(self.queue)>=self.capacity or self.queued_bytes+size>self.byte_limit:
                    self.dropped+=count;self.lost_ranges.append((key,sequence,sequence,'overflow'));self.callback_fault='Overflow coda ADS: campioni persi; acquisizione interrotta';return
                data=C.string_at(header+16,size)
                self.queue.append((self.generation,nid,symbol,ticks,received_ns,received_mono,sequence,self.source_epoch,data))
                self.queued_bytes+=size;self.accepted+=count;self.last_received[key]=received_mono
                if len(self.queue)>self.queue_high_water:self.queue_high_water=len(self.queue)
        except Exception as exc:
            self.callback_fault='Callback ADS: '+str(exc)

    def scale_queue(self,count,sample_ms):
        """Size the callback queue for about `headroom_s` seconds of traffic.

        A fixed 32,768 samples is only 0.33 s at 1,000 channels x 10 ms; any short stall
        (antivirus, GC, disk) would abort acquisition. The configured limits stay a floor.
        """
        rate=count*1000.0/max(1.0,float(sample_ms))
        capacity=min(1000000,max(self.base_capacity,int(rate*self.headroom_s)))
        byte_limit=min(256*1024**2,max(self.base_byte_limit,capacity*64))
        with self.lock:self.capacity=capacity;self.byte_limit=byte_limit
        return capacity,byte_limit

    @staticmethod
    def watchdog_expired(times,now,sample_ms):
        return any(now-t>max(5,sample_ms/1000*5) for t in times)

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
        self.sample_ms=sample_ms;self.scale_queue(len(nids),sample_ms)
        self.source_epoch+=1;self.generation=generation;self.last_received={};self.last_source={};self.last_arrival={};self.cycle_state={}
        self.periods.clear();self.arrivals.clear();self.latencies.clear();self.queue_delays.clear()
        self.reference=nids[0] if nids else None;self.watch_key={}
        with self.lock:self.accepting=True
        entries=[]
        for nid in nids:
            if self.stop_event.is_set():break
            name=symbol_name(nid);symbol=await c.get_symbol(name)
            if not 0<symbol['size']<=65536:raise ValueError('Notifiche ADS: selezionare membri di dimensione 1..65536 byte: '+name)
            entries.append((nid,symbol))
        blocks,singles=plan_blocks(entries) if self.grouping else ([],entries)
        layout=dict(blocks=0,block_members=0,singles=0,fallback_members=0)
        for group,start,length,members in blocks:
            if self.stop_event.is_set():break
            try:
                # The range must be readable as one area before it replaces per-symbol handles.
                if len(await c.read(group,start,length))!=length:raise ValueError('lettura parziale')
            except (ADSError,ValueError) as exc:
                logging.warning('Notifica a blocco ADS non disponibile (0x%X+%d, %d byte): %s',group,start,length,exc)
                singles.extend((nid,symbol) for nid,symbol,_ in members);layout['fallback_members']+=len(members);continue
            self.sequence+=1;user=self.sequence;label=f'block:0x{group:X}+{start}[{length}]'
            descriptor=dict(size=length,count=len(members),label=label,group=group,offset=start,members=members)
            with self.lock:
                self.members[user]=(None,descriptor)
                for nid,_,_ in members:self.watch_key[nid]=label
            attr=NotificationAttrib(length,3,int(sample_ms*10000),int(sample_ms*10000));notification=U32()
            c.check(await c.threaded(c.dll.AdsSyncAddDeviceNotificationReqEx,c.port,C.byref(c.address),group,start,C.byref(attr),self.callback,user,C.byref(notification)))
            self.subscriptions.append(notification.value);layout['blocks']+=1;layout['block_members']+=len(members)
        for nid,symbol in singles:
            if self.stop_event.is_set():break
            name=symbol_name(nid);layout['singles']+=1
            raw=await c.readwrite(0xF003,0,4,name.encode('utf-8')+b'\0')
            if len(raw)!=4:raise ValueError('Handle ADS non valido')
            handle=struct.unpack('<I',raw)[0];c.handles[name]=handle
            self.sequence+=1;user=self.sequence
            with self.lock:self.members[user]=(nid,symbol)
            # Native DLL units are 100 ns; SERVERCYCLE=3. Max delay one period.
            attr=NotificationAttrib(symbol['size'],3,int(sample_ms*10000),int(sample_ms*10000));notification=U32()
            c.check(await c.threaded(c.dll.AdsSyncAddDeviceNotificationReqEx,c.port,C.byref(c.address),0xF005,handle,C.byref(attr),self.callback,user,C.byref(notification)))
            self.subscriptions.append(notification.value)
        self.layout=layout;self.reference_key=self.watch_key.get(self.reference,self.reference);self.started=True

    def check_cycle(self,key,ticks,previous,sample_ms,members=1):
        """Count samples missing from a cyclic (SERVERCYCLE) stream: one channel or one block.

        The PLC rounds the requested cycle up to its task cycle, so the period is
        learned per stream (minimum positive delta, never below the request).
        """
        delta=ticks-previous
        if delta<=0:return 0
        state=self.cycle_state.get(key)
        if state is None:state=self.cycle_state[key]=[delta,0]
        elif delta<state[0]:state[0]=delta
        state[1]+=1
        period=max(int(sample_ms*10000),state[0])
        if state[1]<=GAP_WARMUP or delta<=period*GAP_FACTOR:return 0
        cycles=max(1,round(delta/period)-1);missing=cycles*members
        self.source_gap_samples+=missing;self.source_gap_events+=1
        self.source_gaps.append(dict(nodeid=key,after_filetime=str(previous),before_filetime=str(ticks),missing_samples=missing,
            missing_cycles=cycles,members=members,period_ms=period/10000,plc_not_run=self.paused_not_run))
        return missing

    def drain(self,c,sample_ms):
        with self.lock:
            batch=[self.queue.popleft() for _ in range(min(4096,len(self.queue)))]
            self.queued_bytes-=sum(len(item[-1]) for item in batch)
        groups={};iso={};utc=self.utc
        for generation,nid,symbol,ticks,received_ns,mono,sequence,source_epoch,data in batch:
            # Clocks and continuity belong to the notification: a block shares one PLC timestamp.
            key,count=(nid,1) if nid is not None else (symbol['label'],symbol['count'])
            if ticks<FILETIME_EPOCH:self.timestamp_errors+=count
            previous=self.last_source.get(key)
            backwards=previous is not None and ticks<previous
            if backwards:self.clock_backwards+=count
            if previous is not None:
                self.check_cycle(key,ticks,previous,sample_ms,count)
                if key==self.reference_key:
                    self.periods.append((ticks-previous)/10000)
                    self.arrivals.append((mono-self.last_arrival[key])*1000)
            self.last_source[key]=ticks;self.last_arrival[key]=mono
            ts=(ticks-FILETIME_EPOCH)/10000
            source_ts=iso.get(ticks)
            if source_ts is None and ticks>=FILETIME_EPOCH:source_ts=iso[ticks]=timestamp(ticks)
            shared=dict(source_ts=source_ts,server_ts=None,source_filetime=str(ticks),
                        source_sequence=sequence,source_epoch=source_epoch,_received_mono_ns=int(mono*1e9),clock_discontinuity=backwards or ticks<FILETIME_EPOCH,received_ns=received_ns,
                        local_ts=utc.micro(received_ns),received_ms=received_ns/1e6,timestamp_origin='ADS notification')
            self.latencies.append(received_ns/1e6-ts);self.queue_delays.append((time.monotonic()-mono)*1000)
            rows=groups.setdefault((generation,ticks),[])
            members=((nid,symbol,data),) if nid is not None else ((n,s,data[o:o+s['size']]) for n,s,o in symbol['members'])
            for member,member_symbol,payload in members:
                try:row=c.row(member,member_symbol,payload)
                except Exception as exc:
                    from primary_format import typed
                    self.decode_errors+=1;self.callback_fault='ADS decode: '+str(exc)
                    row=dict(nodeid=member,value=None,plot_value=None,data_type=member_symbol.get('type','Unknown'),quality='Bad',good=False,status='ADSDecodeError',error=str(exc),_source_binary_typed=typed(payload))
                row.update(shared);rows.append(row)
        for (generation,ticks),rows in groups.items():self.sink((ticks-FILETIME_EPOCH)/10000,rows,sample_ms,generation)
        self.processed+=sum(1 if item[1] is not None else item[2]['count'] for item in batch)

    def stats(self):
        values=sorted(self.periods);arrivals=list(self.arrivals)
        with self.lock:pending=len(self.queue);size=self.queued_bytes
        return dict(mode='ADS notifiche cicliche',received=self.received,accepted=self.accepted,dropped=self.dropped,lost_ranges=list(self.lost_ranges),clock_backwards=self.clock_backwards,timestamp_errors=self.timestamp_errors,queue_samples=pending,
                    queue_bytes=size,queue_capacity=self.capacity,plc_not_run=self.paused_not_run,reference_signal=self.reference,
                    processed=self.processed,decode_errors=self.decode_errors,queue_high_water=self.queue_high_water,
                    control_error=self.control_error,control_timeouts=self.control_timeouts,control_last_ok=self.control_last_ok,
                    source_period_ms=sum(values)/len(values) if values else None,
                    source_min_ms=min(values) if values else None,source_max_ms=max(values) if values else None,
                    source_p95_ms=values[min(len(values)-1,int(.95*(len(values)-1)))] if values else None,
                    arrival_period_ms=sum(arrivals)/len(arrivals) if arrivals else None,
                    clock_delta_ms=sum(self.latencies)/len(self.latencies) if self.latencies else None,
                    queue_delay_ms=max(self.queue_delays) if self.queue_delays else None,
                    notify_grouping=self.grouping,notify_layout=dict(self.layout),
                    source_gap_samples=self.source_gap_samples,source_gap_events=self.source_gap_events,source_gaps=list(self.source_gaps),
                    source_gap_policy='missing PLC cycles counted per channel from FILETIME deltas; recording continues and the gap is documented',
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
            self.info=dict(c.info);current=None;heartbeat=0;version=None;configured=time.monotonic();reported=0;watched=0
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
                # Drain what is already copied in one time slice: the checks below are
                # O(channels) and must not run once per small batch at 1,000 channels.
                slice_end=time.monotonic()+.02
                while True:
                    self.drain(c,sample_ms)
                    with self.lock:pending=bool(self.queue)
                    if not pending or self.callback_fault or time.monotonic()>=slice_end:break
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
                if current and nids and now-watched>=.1:
                    # Thresholds are >=5 s: a 100 ms evaluation period keeps them exact enough.
                    watched=now;state=c.info.get('ads_state')
                    if state is not None and state!=5:
                        # PLC not in RUN (STOP/CONFIG...): silence is expected, do not abort the
                        # acquisition; restart the watchdog baseline until RUN returns.
                        now_m=time.monotonic()
                        with self.lock:
                            for n in nids:self.last_received[self.watch_key.get(n,n)]=now_m
                        configured=now_m;self.paused_not_run=True
                    else:
                        self.paused_not_run=False
                        with self.lock:times=[self.last_received.get(self.watch_key.get(n,n),configured) for n in nids]
                        if self.watchdog_expired(times,time.monotonic(),sample_ms):
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
