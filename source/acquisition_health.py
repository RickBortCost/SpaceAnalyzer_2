"""Bounded diagnostics and persistent critical events; no UI dependency."""
from collections import deque
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time


def quantiles(values):
    ordered=sorted(values)
    return {k:ordered[min(len(ordered)-1,int(p*(len(ordered)-1)))] if ordered else None for k,p in [('p95',.95),('p99',.99)]}

class EventStore:
    def __init__(self,path,capacity=2048):
        self.path=Path(path);self.q=queue.Queue(capacity);self.lock=threading.RLock()
        self.recent=deque(maxlen=256);self.error='';self.overflow=0;self.thread=None;self.closed=False
    def emit(self,kind,connection_id=None,**details):
        event=dict(at_ns=str(time.time_ns()),kind=kind,connection_id=connection_id,details=details)
        with self.lock:
            self.recent.append(event)
            if self.closed:return False
            if self.thread is None:
                self.thread=threading.Thread(target=self._run,name='acquisition-events',daemon=True);self.thread.start()
            try:self.q.put_nowait(event);return True
            except queue.Full:self.overflow+=1;return False
    def _run(self):
        db=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(self.path)
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at_ns TEXT,kind TEXT,connection_id TEXT,details TEXT)');db.commit()
            while True:
                item=self.q.get()
                if item is None:break
                batch=[item]
                while len(batch)<128:
                    try:next_item=self.q.get_nowait()
                    except queue.Empty:break
                    if next_item is None:self.q.put(None);break
                    batch.append(next_item)
                with self.lock:
                    overflow=self.overflow
                if overflow>getattr(self,'reported_overflow',0):
                    batch.append(dict(at_ns=str(time.time_ns()),kind='event_queue_overflow',connection_id=None,details=dict(lost_events=overflow-getattr(self,'reported_overflow',0),total_lost_events=overflow)))
                    self.reported_overflow=overflow
                db.executemany('INSERT INTO events(at_ns,kind,connection_id,details) VALUES(?,?,?,?)',
                    [(e['at_ns'],e['kind'],e['connection_id'],json.dumps(e['details'],ensure_ascii=False,allow_nan=False)) for e in batch]);db.commit()
        except Exception as exc:self.error=str(exc)
        finally:
            if db:db.close()
    def history(self,limit=100):
        limit=max(1,min(1000,int(limit)))
        if self.path.is_file():
            try:
                with sqlite3.connect(self.path,timeout=1) as db:
                    rows=db.execute('SELECT id,at_ns,kind,connection_id,details FROM events ORDER BY id DESC LIMIT ?',(limit,)).fetchall()
                    return [dict(id=i,at_ns=t,kind=k,connection_id=c,details=json.loads(d)) for i,t,k,c,d in rows]
            except sqlite3.Error:pass
        with self.lock:return list(self.recent)[-limit:][::-1]
    def close(self):
        with self.lock:self.closed=True;thread=self.thread
        if thread and thread.is_alive():
            try:self.q.put(None,timeout=5)
            except queue.Full:self.error=self.error or 'Timeout chiusura eventi: coda non drenata'
            thread.join(5)

def source_clock_ns(row):
    if row.get('source_filetime') is not None:return (int(row['source_filetime'])-116444736000000000)*100
    value=row.get('source_ts')
    if not value:return None
    from datetime import datetime,timezone
    import re
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:return None
        delta=dt.astimezone(timezone.utc).replace(microsecond=0)-datetime(1970,1,1,tzinfo=timezone.utc)
        fraction=re.search(r'\.(\d+)',value)
        ns=int((fraction[1]+'000000000')[:9]) if fraction else 0
        return (delta.days*86400+delta.seconds)*1000000000+ns
    except (ValueError,TypeError,OverflowError):return None

class SourceMetrics:
    def __init__(self,connection_id,protocol):
        self.connection_id=connection_id;self.protocol=protocol;self.lock=threading.RLock();self.start=time.monotonic()
        self.recording_rejected=0;self.backpressure_waits=0;self.backpressure_ms=0;self.receive_clock_backwards=0;self.receive_reordered=0;self.last_receive=None
        self.received=self.dispatched=self.lost=self.rejected_after_failure=self.display_skipped=0
        self.clock_backwards=self.duplicates=self.sequence_gaps=0;self.last_time={};self.last_source_sequence={};self.sequences={}
        self.queue_latency=deque(maxlen=4096);self.high_water=0;self.backlog=self.backlog_bytes=0;self.error='';self.connections=0
    def snapshot(self):
        with self.lock:
            seconds=max(.001,time.monotonic()-self.start)
            return dict(connection_id=self.connection_id,protocol=self.protocol,received=self.received,dispatched=self.dispatched,
                lost=self.lost,receive_clock_backwards=self.receive_clock_backwards,receive_reordered=self.receive_reordered,backpressure_waits=self.backpressure_waits,backpressure_ms=self.backpressure_ms,recording_rejected_samples=self.recording_rejected,rejected_after_failure=self.rejected_after_failure,display_skipped=self.display_skipped,
                received_per_second=self.received/seconds,clock_backwards=self.clock_backwards,duplicates=self.duplicates,
                sequence_gaps=self.sequence_gaps,backlog=self.backlog,backlog_bytes=self.backlog_bytes,
                high_water=self.high_water,error=self.error,queue_latency_ms=quantiles(self.queue_latency))
