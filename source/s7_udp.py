"""Passive cyclic S7 UDP Direct Generic receiver (iba documented six-byte header).

No proprietary iba Request negotiation. One configured sender/module, a bounded
handoff queue, independent socket thread, and unmodified per-datagram timing.
"""
import asyncio
import logging
import math
import queue
import socket
import statistics
import struct
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from s7_catalog import parse_node, value_row
from s7_client import SCALARS, decode


def decode_packet(data,cfg):
    if len(data)<8 or len(data)>4096 or len(data)%2: raise ValueError('Lunghezza UDP deve essere pari, tra 8 e 4096 byte')
    length,module,sequence=struct.unpack_from('>HHH',data)
    if length!=len(data) or length!=cfg['s7_udp_length']: raise ValueError('message_length diverso dal telegramma configurato/ricevuto')
    if module!=cfg['s7_udp_module']: raise LookupError('Modulo UDP diverso')
    payload=data[6:]; offset=cfg['s7_udp_timestamp_offset']; source_ms=source_ts=None
    if offset>=0:
        micros=struct.unpack_from(('>' if cfg['s7_udp_endian']=='big' else '<')+'q',payload,offset)[0]
        stamp=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(microseconds=micros)
        if not datetime(2000,1,1,tzinfo=timezone.utc)<=stamp<datetime(2200,1,1,tzinfo=timezone.utc):
            raise ValueError('Timestamp PLC UTC in microsecondi fuori intervallo 2000–2199')
        source_ms=micros/1000; source_ts=stamp.isoformat(timespec='microseconds')
    return sequence,payload,source_ms,source_ts


class S7UDPClient:
    def __init__(self,cfg):
        self.cfg=dict(cfg); self.catalog={r['nodeid']:r for r in cfg['s7_udp_tags']}
        self.tags={nid:parse_node(nid) for nid in self.catalog}
        self.sock=None; self.stop_event=threading.Event(); self.first_packet=threading.Event()
        self.queue=queue.Queue(maxsize=cfg['s7_udp_queue']); self.thread=None; self.consumer=None
        self.selection=None; self.sink=None; self.latest={}; self.guard=threading.RLock()
        self.done=threading.Condition();self.serial=0;self.processed_serial=0
        self.last_received=None; self.last_seq=None; self.last_source_ms=None; self.error=''
        self.sequence_time=None; self.stale=False; self.last_dispatch_ms=None
        self.periods=deque(maxlen=1000)
        self.sample_periods=deque(maxlen=128)
        self.counts={k:0 for k in ('received','accepted','lost','duplicates','out_of_order','invalid',
            'filtered','queue_dropped','sequence_resets','timeouts','timestamp_errors')}
        self.info=dict(mode='udp',transport='S7 UDP Direct Generic',read_mode='UDP ciclico · ogni telegramma',
            read_only=True,host=cfg['s7_host'],port=cfg['s7_udp_port'],bind_ip=cfg['s7_udp_bind'],
            module_index=cfg['s7_udp_module'],length=cfg['s7_udp_length'],
            timestamps=('Timestamp PLC UTC µs nel payload + timestamp locale di ricezione' if cfg['s7_udp_timestamp_offset']>=0 else 'Timestamp locale di ricezione; header Direct senza timestamp PLC'),
            quality_origin='Validità telegramma, contatore sequenza e watchdog; qualità interna CPU non trasmessa')

    async def connect(self):
        cfg=self.cfg
        sender=await asyncio.to_thread(socket.gethostbyname,cfg['s7_host'])
        self.sender_ip=sender
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            if hasattr(socket,'SO_EXCLUSIVEADDRUSE'): sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
            sock.bind((cfg['s7_udp_bind'],cfg['s7_udp_port'])); sock.settimeout(min(.1,cfg['s7_udp_watchdog_ms']/2000))
        except BaseException: sock.close(); raise
        self.sock=sock; self.info['socket_buffer_bytes']=sock.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF)
        self.started=time.monotonic(); self.thread=threading.Thread(target=self._receive,name='S7-UDP-receive',daemon=True)
        self.thread.start()

    def configure_acquisition(self,selection,sink):
        self.selection=selection; self.sink=sink
        self.consumer=threading.Thread(target=self._consume,name='S7-UDP-acquire',daemon=True); self.consumer.start()

    def _receive(self):
        try:
            while not self.stop_event.is_set():
                self._check_timeout()
                try: data,peer=self.sock.recvfrom(65535)
                except socket.timeout:
                    self._check_timeout(); continue
                except OSError:
                    if self.stop_event.is_set(): break
                    raise
                received_ns=time.time_ns();received_ms=received_ns/1e6;received_mono_ns=time.monotonic_ns();mono=received_mono_ns/1e9
                with self.guard:
                    self.counts['received']+=1
                    if peer[0]!=self.sender_ip or (self.cfg['s7_udp_sender_port'] and peer[1]!=self.cfg['s7_udp_sender_port']):
                        self.counts['filtered']+=1; continue
                    try: seq,payload,source_ms,source_ts=decode_packet(data,self.cfg)
                    except LookupError: self.counts['filtered']+=1; continue
                    except (ValueError,OverflowError,struct.error): self.counts['invalid']+=1; continue
                    gap=0; reset=False
                    if self.last_seq is not None:
                        distance=(seq-self.last_seq)&0xffff
                        timed_out=mono-self.sequence_time>=self.cfg['s7_udp_watchdog_ms']/1000
                        if distance==0 and not timed_out: self.counts['duplicates']+=1; continue
                        if timed_out:
                            reset=True
                        elif distance>=32768: self.counts['out_of_order']+=1; continue
                        else: gap=distance-1
                    if source_ms is not None and self.last_source_ms is not None and source_ms<=self.last_source_ms:
                        self.counts['timestamp_errors']+=1; continue
                    if self.last_received is not None and not self.stale and not reset:
                        elapsed=(source_ms-self.last_source_ms) if source_ms is not None and self.last_source_ms is not None else (mono-self.last_received)*1000
                        period=elapsed/(gap+1)
                        if 0<period<=60000: self.sample_periods.append(period)
                    self.last_seq=seq; self.sequence_time=mono
                    self.counts['lost']+=gap
                    if reset: self.counts['sequence_resets']+=1
                    if self.last_received is not None and not self.stale: self.periods.append((mono-self.last_received)*1000)
                    self.last_received=mono; self.last_source_ms=source_ms; self.stale=False
                    self.counts['accepted']+=1
                    quality='Uncertain' if gap or reset else 'Good'
                    status=f'UDP sequence gap: {gap}' if gap else 'UDP sequence restarted after timeout' if reset else 'Good'
                selection=self.selection() if self.selection else (list(self.catalog),self.cfg.get('sample_ms',10),None)
                nids,sample_ms,generation=selection
                sample_ms=self.sample_period_ms(sample_ms)
                self._enqueue((payload,source_ms,source_ts,received_ms,nids,sample_ms,generation,quality,status,received_ns,received_mono_ns))
                self.first_packet.set()
                self._check_timeout()
        except Exception as exc:
            self.error=str(exc); logging.exception('Ricevitore S7 UDP interrotto')

    def _enqueue(self,item):
        try:
            with self.done:
                self.queue.put_nowait(item);self.serial+=1
        except queue.Full:
            with self.guard: self.counts['queue_dropped']+=1

    def flush(self,timeout=5):
        deadline=time.monotonic()+timeout
        with self.done:
            target=self.serial
            while self.processed_serial<target:
                if not self.consumer or not self.consumer.is_alive():return False
                remaining=deadline-time.monotonic()
                if remaining<=0:return False
                self.done.wait(min(.1,remaining))
        return True

    def _check_timeout(self):
        now=time.monotonic()
        with self.guard:
            if self.stale or now-(self.last_received or self.started)<self.cfg['s7_udp_watchdog_ms']/1000: return
            self.stale=True; self.counts['timeouts']+=1
        if self.selection:
            nids,sample_ms,generation=self.selection()
            sample_ms=self.sample_period_ms(sample_ms)
            self._enqueue((None,None,None,time.time()*1000,nids,sample_ms,generation,'Bad','UDP timeout: nessun telegramma valido',time.time_ns(),time.monotonic_ns()))

    def _consume(self):
        try:
            dropped=0
            while not self.stop_event.is_set():
                try: item=self.queue.get(timeout=.2)
                except queue.Empty: continue
                try:
                    payload,source_ms,source_ts,received_ms,nids,sample_ms,generation,quality,status,received_ns,received_mono_ns=item
                    with self.guard:
                        if self.counts['queue_dropped']>dropped and quality!='Bad':
                            dropped=self.counts['queue_dropped']; quality='Uncertain'; status='UDP acquisition queue overflow'
                    rows=self._rows(payload,nids,quality,status,source_ts,received_ms)
                    for row in rows:row.update(received_ns=received_ns,_received_mono_ns=received_mono_ns)
                    ts=source_ms if source_ms is not None else received_ms
                    # A local timeout uses the next source-timeline instant, keeping
                    # plot/calculation time monotonic even with unsynchronized clocks.
                    if payload is None and self.last_dispatch_ms is not None: ts=self.last_dispatch_ms+sample_ms
                    with self.guard: self.latest.update({r['nodeid']:r for r in rows})
                    if rows and self.sink: self.sink(ts,rows,sample_ms,generation)
                    self.last_dispatch_ms=ts
                finally:
                    self.queue.task_done()
                    with self.done:
                        self.processed_serial+=1;self.done.notify_all()
        except Exception as exc:
            self.error=str(exc); logging.exception('Elaborazione S7 UDP interrotta')

    def _rows(self,payload,nids,quality,status,source_ts,received_ms):
        rows=[]; local=datetime.fromtimestamp(received_ms/1000,timezone.utc).isoformat()
        for nid in nids:
            tag=self.tags.get(nid); kind=tag['data_type'] if tag else 'Null'
            error='' if payload is not None else status; value=None; q=quality
            if not tag: error='Campo non presente nel catalogo UDP'; q='Bad'
            elif payload is not None:
                try:
                    raw=payload[tag['offset']:tag['offset']+tag['size']]
                    if self.cfg['s7_udp_endian']=='little' and tag['kind'] not in ('BOOL','STRING'):
                        value=struct.unpack('<'+SCALARS[tag['kind']][0],raw)[0]
                    else: value=decode(tag,raw)
                    if isinstance(value,float) and not math.isfinite(value): raise ValueError('Valore UDP non finito')
                except (ValueError,struct.error) as exc: error=str(exc); q='Bad'
            rows.append(value_row(nid,kind,value,error,q,status if not error else error,local,source_ts,received_ms))
        return rows

    async def disconnect(self):
        self.stop_event.set()
        if self.sock: self.sock.close()
        for thread in (self.thread,self.consumer):
            if thread: await asyncio.to_thread(thread.join,2)
        self.sock=None

    async def read_state(self):
        if self.error: raise ConnectionError(self.error)
        if not self.thread or not self.thread.is_alive(): raise ConnectionError('Ricevitore UDP chiuso')

    async def check_symbols(self): pass

    async def inspect(self,nid):
        node=parse_node(nid,True)
        if node['mode']!='udp': raise ValueError('In modalità UDP scegliere un campo s7:udp:')
        if node.get('folder'): return dict(nodeid=nid,display_name='Campi telegramma UDP',node_class='Object',has_children=True)
        if nid not in self.catalog: raise ValueError('Campo non presente nel catalogo UDP')
        tag=self.catalog[nid]
        return dict(tag,node_class='Variable',has_children=False,path=tag['display_name'],comment=nid+' · offset dal payload')

    async def browse(self,nid):
        node=await self.inspect(nid)
        return [await self.inspect(n) for n in self.catalog] if node['node_class']=='Object' else []

    async def scan_items(self,root):
        return await self.browse(root) if root=='s7:udp:/' else [await self.inspect(root)]

    async def read_values(self,nids):
        with self.guard:
            return [dict(self.latest.get(n) or value_row(n,'Null',error='In attesa di un telegramma UDP',quality='Bad')) for n in nids]

    def sample_period_ms(self,fallback=10):
        """Integer recorder/buffer hint; exact timing stays in sample timestamps."""
        with self.guard:
            value=statistics.median(self.sample_periods) if self.sample_periods else fallback
            return max(1,min(60000,round(value)))

    def diagnostics(self):
        with self.guard:
            period=list(self.periods); age=(time.monotonic()-self.last_received)*1000 if self.last_received is not None else None
            return dict(self.counts,mode='udp',queue_samples=self.queue.qsize(),queue_capacity=self.queue.maxsize,
                age_ms=round(age,3) if age is not None else None,receiving=self.last_received is not None and not self.stale,
                arrival_period_ms=sum(period)/len(period) if period else None,
                min_ms=min(period) if period else None,max_ms=max(period) if period else None,
                p95_ms=sorted(period)[int((len(period)-1)*.95)] if period else None,
                sample_period_ms=statistics.median(self.sample_periods) if self.sample_periods else None,
                timestamp_origin='PLC UTC µs' if self.cfg['s7_udp_timestamp_offset']>=0 else 'Ricezione locale',error=self.error)
