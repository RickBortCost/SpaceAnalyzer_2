"""Recorder-first handoff. Browser locks and live history never run on this path.

One bounded queue per connection, fair draining, a separate lossy *display*
queue. Overflow of originals stops the affected recording and is attributable.
The driver callback only freezes rows, timestamps and queues them.
"""
from collections import deque
import os
import threading
import time
import uuid
from acquisition_buffers import ConnectionBuffer, size_hint
from acquisition_health import SourceMetrics,source_clock_ns


class LiveDistribution:
    def __init__(self, deliver, capacity=8192, max_bytes=16*1024**2):
        self.deliver=deliver;self.capacity=capacity;self.max_bytes=max_bytes
        self.lock=threading.Lock();self.items=deque();self.samples=self.bytes=self.skipped=0
        self.error='';self.on_drop=None;self.closed=False;self.wake=threading.Event();self.thread=None
    def put(self,item,count,size):
        with self.lock:
            if self.closed:return False
            if count>self.capacity or size>self.max_bytes or self.samples+count>self.capacity or self.bytes+size>self.max_bytes:
                self.skipped+=count
                if self.on_drop:self.on_drop(item,count)
                return False
            self.items.append((item,count,size));self.samples+=count;self.bytes+=size
            if self.thread is None:
                self.thread=threading.Thread(target=self._run,name='live-distribution',daemon=True);self.thread.start()
        self.wake.set();return True
    def _run(self):
        while not self.closed:
            with self.lock:
                entry=self.items.popleft() if self.items else None
                if entry:self.samples-=entry[1];self.bytes-=entry[2]
            if entry:
                try:self.deliver(*entry[0])
                except Exception as exc:self.error=str(exc)
            else:self.wake.wait(.05);self.wake.clear()
    def status(self):
        with self.lock:return dict(backlog_samples=self.samples,backlog_bytes=self.bytes,skipped_samples=self.skipped,error=self.error,policy='skip display only; originals already submitted to recorder')
    def close(self):self.closed=True;self.wake.set()


class AcquisitionEngine:
    def __init__(self,record,display,fail,events,*,calculate=None,max_samples=None,max_bytes=None):
        self.record=record;self.fail=fail;self.events=events;self.calculate=calculate
        self.max_samples=int(max_samples or os.environ.get('SPACE_ACQ_QUEUE_SAMPLES',32768))
        self.max_bytes=int(max_bytes or os.environ.get('SPACE_ACQ_QUEUE_MB',32))* (1024**2 if max_bytes is None else 1)
        if not 1<=self.max_samples<=1000000 or not 1024<=self.max_bytes<=256*1024**2:raise ValueError('Limiti buffer acquisizione non validi')
        self.live=LiveDistribution(display);self.lock=threading.RLock();self.sources={}
        self.wake=threading.Event();self.thread=None;self.closed=False;self.last_timestamp=None
    def connection(self,key,protocol):
        with self.lock:
            if key not in self.sources:
                identity=uuid.uuid4().hex
                self.sources[key]=(ConnectionBuffer(self.max_samples,self.max_bytes),SourceMetrics(identity,protocol))
                self.events.emit('connection_registered',identity,source=key,protocol=protocol)
            return self.sources[key]
    def submit(self,key,protocol,t,rows,period,generation=None,backpressure_timeout=0):
        if not rows or self.closed:return False
        buffer,metrics=self.connection(key,protocol);now=time.time_ns()
        copied=[dict(r,received_ns=r.get('received_ns') or (int(r['received_ms']*1000000) if r.get('received_ms') is not None else now)) for r in rows]
        count=len(copied);size=size_hint(copied)+count*256
        copied[0]['_buffer_budget']=size
        for row in copied:row.setdefault('_received_mono_ns',time.monotonic_ns())
        started_wait=time.monotonic();deadline=started_wait+backpressure_timeout
        while backpressure_timeout and count<=buffer.max_samples and size<=buffer.max_bytes and not buffer.failed and not buffer.has_room(count,size) and time.monotonic()<deadline:
            self.wake.set();time.sleep(.001)
        waited=time.monotonic()-started_wait
        with metrics.lock:
            if waited>=.001:metrics.backpressure_waits+=1;metrics.backpressure_ms+=waited*1000
            metrics.received+=count
            accepted=buffer.put((t,copied,period,generation),count,size)
            if not accepted:
                metrics.lost+=count
                if metrics.error:metrics.rejected_after_failure+=count
                else:
                    metrics.error='Overflow buffer acquisizione: sessione arrestata; '+str(count)+' campioni rifiutati'
                    self.events.emit('acquisition_overflow',metrics.connection_id,source=key,lost_samples=count,buffer=buffer.snapshot())
                    self.fail(metrics.error)
        if accepted:
            with self.lock:
                if self.thread is None:
                    self.thread=threading.Thread(target=self._run,name='acquisition-engine',daemon=True);self.thread.start()
            self.wake.set()
        return accepted
    def _run(self):
        while not self.closed:
            work=False
            with self.lock:sources=list(self.sources.items())
            for key,(buffer,m) in sources:
                entry=buffer.get()
                if entry is None:continue
                work=True;serial,(t,rows,period,generation),count,queued=entry
                try:
                    with m.lock:
                        for row in rows:
                            received=(int(row['received_ns']),int(row['_received_mono_ns']))
                            previous_receive=m.last_receive
                            if previous_receive is not None:
                                if received[1]<previous_receive[1]:m.receive_reordered+=1
                                elif received[0]<previous_receive[0]:
                                    m.receive_clock_backwards+=1;row['clock_discontinuity']=True
                                    self.events.emit('receive_clock_backwards',m.connection_id,previous_ns=str(previous_receive[0]),received_ns=str(received[0]),policy='recording interval uses monotonic clock; original UTC preserved')
                            if previous_receive is None or received[1]>=previous_receive[1]:m.last_receive=received
                            nid=row['nodeid'];m.sequences[nid]=m.sequences.get(nid,0)+1
                            row.update(connection_id=m.connection_id,sample_sequence=m.sequences[nid],source_protocol=m.protocol)
                            prior=m.last_time.get(nid);source_time=source_clock_ns(row)
                            if source_time is not None:
                                backwards=prior is not None and source_time<prior
                                duplicate=prior is not None and source_time==prior
                                if backwards:m.clock_backwards+=1;row['clock_discontinuity']=True
                                if duplicate:m.duplicates+=1
                                m.last_time[nid]=source_time
                            sequence=row.get('source_sequence');epoch=row.get('source_epoch')
                            previous=m.last_source_sequence.get(nid)
                            if sequence is not None and previous and previous[0]==epoch and sequence>previous[1]+1:
                                missing=sequence-previous[1]-1;m.sequence_gaps+=missing
                                self.events.emit('source_sequence_gap',m.connection_id,nodeid=nid,first=previous[1]+1,last=sequence-1)
                                self.fail('Sequenza sorgente incompleta: '+nid)
                            if sequence is not None:m.last_source_sequence[nid]=(epoch,sequence)
                        m.queue_latency.append((time.monotonic_ns()-queued)/1e6)
                    # Neither callback may acquire a browser/live lock.
                    accepted=self.record(t,rows,period,generation)
                    if accepted is False:
                        with m.lock:m.recording_rejected+=count
                    self.last_timestamp=max(t,self.last_timestamp) if self.last_timestamp is not None else t
                    if self.calculate:self.calculate(t,rows,period,generation)
                    with m.lock:m.dispatched+=count
                    if not self.live.put((t,rows,period,generation),count,rows[0]['_buffer_budget']):
                        with m.lock:m.display_skipped+=count
                except Exception as exc:
                    with m.lock:m.lost+=count;m.error=str(exc)
                    self.events.emit('acquisition_dispatch_error',m.connection_id,samples=count,error=str(exc));self.fail(str(exc))
                finally:buffer.done(serial)
            if not work:self.wake.wait(.01);self.wake.clear()
    def flush(self,timeout=5):
        with self.lock:targets=[(b,b.snapshot()['accepted_batches']) for b,_ in self.sources.values()]
        deadline=time.monotonic()+timeout
        while any(b.snapshot()['completed_batches']<target for b,target in targets):
            if time.monotonic()>=deadline:return False
            self.wake.set();time.sleep(.001)
        return True
    def status(self):
        with self.lock:sources=list(self.sources.items())
        result={}
        for key,(b,m) in sources:
            state=b.snapshot();row=m.snapshot();row['buffer']=state;result[key]=row
        return dict(connections=result,live=self.live.status(),policy='bounded; stop recording on original overflow; recorder before calculations and display')
    def reset_failed(self):
        if not self.flush():raise RuntimeError('Buffer non drenati: impossibile riarmare acquisizione')
        with self.lock:
            for key,(buffer,metrics) in list(self.sources.items()):
                if buffer.failed:
                    self.events.emit('connection_rearmed',metrics.connection_id,source=key,lost_samples=metrics.lost)
                    del self.sources[key]
    def catalog(self):
        with self.lock:return {m.protocol:m.connection_id for _,m in self.sources.values()}
    def close(self):
        self.flush();self.closed=True;self.wake.set();self.live.close()
        if self.thread:self.thread.join(5)
