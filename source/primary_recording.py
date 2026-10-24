"""Bounded primary producer and isolated append-only writer, no UI or codecs."""
from collections import deque
import copy
import os
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import uuid
from acquisition_buffers import size_hint
from acquisition_health import quantiles
from primary_format import DATA,EVENT,FINISH,FLUSH,IPC,FIELDS,MAX_PAYLOAD,atomic_json,json_bytes,typed

class PrimaryRecorder:
    def __init__(self,directory,*,flush_ms=250,max_samples=65536,max_bytes=32*1024**2,max_batches=4096,events=None):
        if not 10<=flush_ms<=10000 or not 1<=max_samples<=1000000 or not 1024<=max_bytes<=256*1024**2:raise ValueError('Limiti recorder primario non validi')
        self.directory=Path(directory);self.flush_ms=flush_ms;self.max_samples=max_samples;self.max_bytes=max_bytes
        self.events=events;self.lock=threading.RLock();self.q=queue.Queue(max_batches)
        self.accepting=False;self.started=False;self.process=None;self.done=threading.Event();self.ready=threading.Event()
        self.queued_samples=self.queued_bytes=self.received=self.lost=0;self.sequence={};self.connections={}
        self.latencies=deque(maxlen=4096);self.start_mono=0;self.error='';self.writer_state={};self.stop_sent=False
        self.high_water=0;self.failure_callback=None;self.backpressure_waits=0;self.backpressure_ms=0
    def start(self,nodes,metadata=None):
        if self.started:raise ValueError('Un recorder primario gestisce una sola sessione')
        if not 1<=len(nodes)<=10000 or len({n['nodeid'] for n in nodes})!=len(nodes):raise ValueError('Canali primari non validi')
        self.started=True;self.directory.mkdir(parents=True,exist_ok=True);self.identity=uuid.uuid4().hex
        self.path=self.directory/(self.identity+'.sac.part');self.channels={n['nodeid']:i for i,n in enumerate(nodes)}
        for node in nodes:
            nid=node['nodeid'];protocol=nid.split(':',1)[0] if nid.startswith(('ads:','mqtt:','s7:','calc:')) else 'opcua'
            self.connections.setdefault(protocol,(metadata or {}).get('connections',{}).get(protocol) or uuid.uuid4().hex)
        cfg=dict(path=str(self.path),flush_ms=self.flush_ms,metadata={**(metadata or {}),
            'session_id':self.identity,'started_ns':str(time.time_ns()),'channels':nodes,
            'connections':self.connections,'timestamp_policy':'Original source clocks preserved; received_ns is integer UTC, no resampling'})
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('primary_worker.py'))],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.start_mono=time.monotonic();self.reader=threading.Thread(target=self._read,name='primary-acks',daemon=True);self.reader.start()
        data=json_bytes(cfg);self._write_all(struct.pack('<I',len(data))+data)
        if not self.ready.wait(15) or self.error:
            self.process.kill();self.process.wait();raise ValueError('Avvio recorder primario: '+(self.error or 'timeout'))
        self.accepting=True;self.sender=threading.Thread(target=self._send,name='primary-sender',daemon=True);self.sender.start()
        return self.status()
    def _event(self,kind,**details):
        if self.events:self.events.emit(kind,session_id=getattr(self,'identity',None),**details)
    def _fail(self,message):
        with self.lock:
            first=not self.error;self.error=self.error or str(message);self.accepting=False
        if first:
            self._request_finish('failure')
            self._event('primary_failure',error=self.error,accepted_samples=self.received,durable_samples=self.writer_state.get('durable_samples',0),uncommitted_samples=max(0,self.received-self.writer_state.get('durable_samples',0)))
            if self.failure_callback:self.failure_callback(self.error)
    def enqueue(self,t,rows,period,backpressure_timeout=0):
        queued=time.monotonic_ns()
        if not rows:return True
        budget=rows[0].get('_buffer_budget') or size_hint(rows)
        started_wait=time.monotonic();deadline=started_wait+backpressure_timeout
        while backpressure_timeout and len(rows)<=self.max_samples and budget<=self.max_bytes:
            with self.lock:
                if not self.accepting:return False
                room=self.queued_samples+len(rows)<=self.max_samples and self.queued_bytes+budget<=self.max_bytes and not self.q.full()
            if room or time.monotonic()>=deadline:break
            time.sleep(.001)
        waited=time.monotonic()-started_wait
        if waited>=.001:
            with self.lock:self.backpressure_waits+=1;self.backpressure_ms+=waited*1000
        received_ns=time.time_ns();captured=[]
        with self.lock:
            if not self.accepting:return False
            try:
                for source in rows:
                    nid=source.get('nodeid')
                    if nid not in self.channels:continue
                    row=dict(source);self.sequence[nid]=self.sequence.get(nid,0)+1
                    protocol=row.get('source_protocol') or (nid.split(':',1)[0] if nid.startswith(('ads:','mqtt:','s7:','calc:')) else 'opcua')
                    values=dict(timestamp_ms=t,received_ns=str(row.get('received_ns') or received_ns),channel=self.channels[nid],
                        connection_id=row.get('connection_id') or self.connections.get(protocol,protocol),
                        sample_sequence=row.get('sample_sequence',self.sequence[nid]),connection_sequence=row.get('connection_sequence'),
                        source_epoch=row.get('source_epoch'),source_sequence=row.get('source_sequence'),value=row.get('value'),
                        data_type=row.get('_source_data_type',row.get('data_type')),quality=row.get('quality') or ('Good' if row.get('good') else 'Bad'),
                        status=row.get('status'),status_code=row.get('status_code'),source_ts=row.get('source_ts'),server_ts=row.get('server_ts'),
                        source_filetime=row.get('source_filetime'),local_ts=row.get('local_ts'),
                        source_value=row.get('_source_value',row.get('source_value',row.get('value'))),source_picoseconds=row.get('source_picoseconds'),server_picoseconds=row.get('server_picoseconds'),
                        acquisition_origin=row.get('acquisition_origin'),sample_ms=period,clock_discontinuity=row.get('clock_discontinuity',False),
                        acquisition_timestamp_ms=row.get('acquisition_timestamp_ms'),source_binary=row.get('_source_binary_typed',typed(row.get('source_binary'))))
                    values['value']=typed(values['value']);values['source_value']=row['_source_typed'] if '_source_typed' in row else typed(values['source_value'])
                    captured.append([values[k] for k in FIELDS])
            except Exception as exc:self.lost+=len(rows);self._fail(exc);return False
            count=len(captured)
            if not count:return True
            budget=rows[0].get('_buffer_budget') or size_hint(rows)
            if self.queued_samples+count>self.max_samples or self.queued_bytes+budget>self.max_bytes or self.q.full():
                self.lost+=count;self._fail('Overflow recorder primario: '+str(count)+' campioni non accettati');self._request_finish('overflow');return False
            first=self.received+1
            try:self.q.put_nowait((DATA,first,count,queued,captured,budget))
            except queue.Full:self.lost+=count;self._fail('Overflow messaggi recorder primario');return False
            self.received+=count;self.queued_samples+=count;self.queued_bytes+=budget;self.high_water=max(self.high_water,self.queued_samples)
            return True
    def _write_all(self,data):
        view=memoryview(data)
        while view:
            written=os.write(self.process.stdin.fileno(),view)
            if written<=0:raise BrokenPipeError('Pipe primaria chiusa')
            view=view[written:]
    def _write(self,kind,first,count,queued,value):
        data=json_bytes(value)
        if len(data)>MAX_PAYLOAD:raise ValueError('Blocco IPC oltre il limite configurato')
        header=IPC.pack(kind,first,count,queued,len(data))
        self._write_all(header+data)
    def _send(self):
        try:
            while True:
                try:item=self.q.get(timeout=.02)
                except queue.Empty:
                    with self.lock:finishing=self.stop_sent
                    if finishing:
                        self._write(FINISH,self.received+1,0,0,dict(integrity='incomplete' if self.error else 'complete',
                            accepted_samples=self.received,lost_samples=self.lost,error=self.error,ended_ns=str(time.time_ns())))
                        return
                    continue
                kind,first,count,queued,value,budget=item
                if kind==DATA:
                    combined=list(value);last=first+count
                    while len(combined)<4096 and budget<2*1024**2:
                        with self.q.mutex:
                            if not self.q.queue or self.q.queue[0][0]!=DATA:break
                        try:nxt=self.q.get_nowait()
                        except queue.Empty:break
                        _,ordinal,n,stamp,values,size=nxt
                        if ordinal!=last:raise ValueError('Sequenza coda primaria interrotta')
                        combined.extend(values);count+=n;last+=n;budget+=size;queued=min(queued,stamp)
                    value=combined
                self._write(kind,first,count,queued,value)
                with self.lock:self.queued_samples-=count;self.queued_bytes-=budget
        except BaseException as exc:self._fail('IPC recorder: '+str(exc));self.done.set()
        finally:
            try:self.process.stdin.close()
            except OSError:pass
    def _read(self):
        import json
        try:
            for line in self.process.stdout:
                message=json.loads(line);op=message.pop('op')
                with self.lock:
                    self.writer_state={k:v for k,v in message.items() if k!='receipts'}
                    now=time.monotonic_ns()
                    for queued,count in message.get('receipts',[]):
                        # Weighted by sample count, bounded to the latest receipts.
                        self.latencies.append(((now-queued)/1e6,count))
                if op=='ready':self.ready.set()
                if op=='error':self._fail(message.get('error','Writer primario terminato'))
                if op in ('finished','error','abandoned'):self.done.set()
        except BaseException as exc:self._fail('Risposta recorder: '+str(exc))
        finally:
            self.process.stdout.close()
            if not self.done.is_set():self._fail('Processo recorder terminato senza finalizzazione');self.done.set()
            self.ready.set()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self._fail('Processo writer non terminato')
    def _request_finish(self,reason):
        with self.lock:self.accepting=False;self.stop_sent=True
    def fail(self,message):self._fail(message);self._request_finish('error')
    def stop(self,timeout=5):
        if not self.started:return self.status()
        self._request_finish('stop');self.done.wait(timeout)
        if self.done.is_set() and self.process:
            try:self.process.wait(timeout=max(.1,timeout))
            except subprocess.TimeoutExpired:self._fail('Timeout arresto recorder primario')
        return self.status()
    def flush(self,timeout=5):
        with self.lock:target=self.received
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            with self.lock:
                if self.writer_state.get('durable_samples',0)>=target:return True
                if self.error:return False
            time.sleep(.005)
        return False
    def status(self):
        with self.lock:
            state=dict(self.writer_state);elapsed=max(.001,time.monotonic()-self.start_mono) if self.start_mono else 0
            weighted=sorted(self.latencies);total=sum(n for _,n in weighted)
            def percentile(p):
                seen=0
                for value,n in weighted:
                    seen+=n
                    if seen>=total*p:return value
                return None
            state.update(recording_id=getattr(self,'identity',None),active=self.accepting,busy=self.started and not self.done.is_set(),
                received_samples=self.received,lost_samples=self.lost,inflight_samples=max(0,self.received-state.get('durable_samples',0)),queue_samples=self.queued_samples,queue_bytes=self.queued_bytes,
                queue_capacity_samples=self.max_samples,queue_capacity_bytes=self.max_bytes,queue_high_water=self.high_water,backpressure_waits=self.backpressure_waits,backpressure_ms=self.backpressure_ms,
                error=self.error or state.get('error',''),uncommitted_at_failure=max(0,self.received-state.get('durable_samples',0)) if self.error else 0,latency_measurement='sample-weighted batch upper bound: ingress to durable fsync acknowledgement',latency_ms={'p95':percentile(.95),'p99':percentile(.99),'receipt_window':len(weighted)},
                samples_per_second=state.get('durable_samples',0)/elapsed if elapsed else 0,
                bytes_per_second=state.get('bytes_written',0)/elapsed if elapsed else 0)
            return state
