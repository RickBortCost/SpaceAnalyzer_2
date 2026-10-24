"""Temporary original-sample history; acquisition only enqueues immutable points.

SQLite writes run on one worker, readers use independent WAL transactions. The
queue and disk usage are bounded; an incomplete cache is never used as complete
analysis input. Starting a session/clearing Live invalidates previous references.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import queue
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone


def point_rows(points,default_period=500,names=None):
    for nid,point in points:
        p=list(point)+[None]*12;t=p[0]
        stamp=datetime.fromtimestamp(t/1000,timezone.utc).isoformat(timespec='microseconds')
        quality=p[2] or ('Good' if p[1] is not None else 'Bad')
        yield dict(timestamp_ms=t,timestamp_utc=stamp,node_id=nid,signal_name=(names or {}).get(nid,nid),
            value_numeric=p[1],value_text=p[6],data_type=p[7],good=quality=='Good',quality=quality,
            status_code=p[3],status=p[8] or quality,source_timestamp=p[4],server_timestamp=p[5],
            sample_ms=p[9] or default_period,local_timestamp=p[10] or stamp,ads_filetime=p[11])


@dataclass(frozen=True)
class Reference:
    archive: object
    generation: int
    sequence: int
    ids: tuple
    start: float
    end: float
    count: int


class LiveArchive:
    def __init__(self, directory, max_pending=32768, max_bytes=8*1024**3,max_pending_bytes=32*1024**2):
        self.directory=Path(directory);self.max_bytes=max_bytes
        self.queue=queue.Queue(max_pending);self.lock=threading.RLock()
        self.generation=0;self.sequence=0;self.counts=Counter()
        self.pending_bytes=0;self.max_pending_bytes=max_pending_bytes
        self.error='';self.fatal='';self.active=False;self.thread=None;self.path=None;self.closed=False

    def new_session(self):
        with self.lock:
            self.generation+=1;generation=self.generation
            self.sequence=0;self.counts.clear();self.error=self.fatal
            if self.closed or self.fatal:return generation
            if not self.thread:
                try:
                    self.directory.mkdir(parents=True,exist_ok=True)
                    self.path=Path(tempfile.mkdtemp(prefix='session-',dir=self.directory))/'originals.sqlite'
                    self.thread=threading.Thread(target=self._run,daemon=True,name='live-originals')
                    self.thread.start()
                except OSError as exc:
                    self.error=str(exc);return generation
            self.active=True
        done=threading.Event()
        self.queue.put(('reset',generation,done),timeout=10)
        if not done.wait(10):self._fail('Timeout inizializzazione storico Live')
        return generation

    def _fail(self,message):
        with self.lock:self.error=self.error or str(message)

    def append(self,generation,nid,point):
        with self.lock:
            if not self.active or self.closed or generation!=self.generation or self.error:return
            seq=self.sequence+1
            size=160+sum(4*len(v) for v in point if isinstance(v,str))
            if self.pending_bytes+size>self.max_pending_bytes:
                self.error='Storico Live incompleto: coda di scrittura piena';return
            try:self.queue.put_nowait(('point',generation,seq,nid,tuple(point),size))
            except queue.Full:
                self.error='Storico Live incompleto: coda di scrittura piena';return
            self.sequence=seq;self.counts[nid]+=1;self.pending_bytes+=size

    def reference(self,ids,start,end):
        with self.lock:
            self._check(self.generation)
            return Reference(self,self.generation,self.sequence,tuple(ids),start,end,sum(self.counts[n] for n in ids))

    def _check(self,generation):
        if self.error:raise ValueError('Storico originale Live non disponibile: '+self.error)
        if not self.active or self.closed or generation!=self.generation:
            raise ValueError('Acquisizione Live cambiata: riposizionare i marker o riprendere Play')

    def flush(self,generation):
        self._check(generation);done=threading.Event()
        self.queue.put(('barrier',done),timeout=10)
        if not done.wait(10):raise ValueError('Storico Live occupato: riprovare il calcolo')
        self._check(generation)

    @contextmanager
    def reader(self,reference):
        self.flush(reference.generation)
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            connection.execute('PRAGMA cache_size=-8192')
            connection.execute('BEGIN')
            yield connection
            self._check(reference.generation)
        finally:connection.close()

    def points(self,reference,ids,start,end):
        ids=tuple(n for n in ids if n in reference.ids)
        if not ids:return
        start=max(start,reference.start);end=min(end,reference.end)
        with self.reader(reference) as c:
            sql='SELECT nid,point FROM points WHERE generation=? AND seq<=? AND stamp>=? AND stamp<=? AND nid IN ('+','.join('?' for _ in ids)+') ORDER BY stamp,seq'
            cursor=c.execute(sql,(reference.generation,reference.sequence,start,end,*ids))
            stamp=None;revisions={}
            for nid,point in cursor:
                p=json.loads(point)
                if stamp is not None and p[0]!=stamp:
                    yield from revisions.items();revisions={}
                stamp=p[0]
                # Derived workers may revise their newest timestamp. Frozen
                # references still see the last revision before their watermark.
                if nid.startswith('calc:'):revisions[nid]=p
                else:yield nid,p
            yield from revisions.items()

    def markers(self,reference,ids,x1,x2):
        result=[]
        with self.reader(reference) as c:
            for nid in ids:
                row={'nodeid':nid,'x1':None,'x2':None}
                if nid not in reference.ids:result.append(row);continue
                for key,t in [('x1',x1),('x2',x2)]:
                    candidates=[]
                    for operator,direction in [('<=','DESC'),('>=','ASC')]:
                        hit=c.execute('SELECT point FROM points WHERE generation=? AND nid=? AND seq<=? AND stamp>=? AND stamp<=? AND stamp '+operator+' ? ORDER BY stamp '+direction+',seq DESC LIMIT 1',
                            (reference.generation,nid,reference.sequence,reference.start,reference.end,t)).fetchone()
                        if hit:candidates.append(json.loads(hit[0]))
                    if not candidates:continue
                    p=min(candidates,key=lambda p:abs(p[0]-t));period=p[9] if len(p)>9 and p[9] else 500
                    if abs(p[0]-t)>3*max(1,period) or p[2]!='Good' or p[1] is None:continue
                    row[key]={'timestamp_ms':p[0],'value':p[1]}
                result.append(row)
        return result

    def _run(self):
        c=None
        try:
            c=sqlite3.connect(self.path)
            c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA synchronous=NORMAL')
            c.execute('CREATE TABLE points(generation INTEGER,seq INTEGER,stamp REAL,nid TEXT,point TEXT)')
            c.execute('CREATE INDEX source_time ON points(nid,stamp,seq)')
            c.execute('CREATE INDEX time_order ON points(stamp,seq)');c.commit()
            while True:
                item=self.queue.get();kind=item[0]
                if kind=='stop':break
                if kind=='reset':
                    try:c.execute('DELETE FROM points');c.commit();c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    except Exception as exc:self._fail(exc)
                    finally:item[2].set()
                    continue
                if kind=='barrier':c.commit();item[1].set();continue
                batch=[item]
                while len(batch)<1024:
                    # Control messages must keep their FIFO position.
                    with self.queue.mutex:
                        if not self.queue.queue or self.queue.queue[0][0]!='point':break
                    batch.append(self.queue.get())
                with self.lock:self.pending_bytes-=sum(item[5] for item in batch)
                if self.error:continue
                try:
                    rows=[]
                    for _,generation,seq,nid,p,_ in batch:
                        if generation!=self.generation:continue
                        if not math.isfinite(p[0]):raise ValueError('Timestamp originale non finito')
                        p=list(p)
                        if p[1] is not None and not math.isfinite(p[1]):p[1]=None
                        rows.append((generation,seq,p[0],nid,json.dumps(p,ensure_ascii=False,allow_nan=False)))
                    if not rows:continue
                    c.executemany('INSERT INTO points VALUES(?,?,?,?,?)',rows);c.commit()
                    if sum(p.stat().st_size for p in self.path.parent.iterdir() if p.is_file())>self.max_bytes:
                        raise ValueError('Limite spazio storico Live raggiunto')
                except Exception as exc:
                    c.rollback();self._fail(exc)
        except Exception as exc:
            self.fatal=str(exc)
            self._fail(exc)
            # Unblock control callers after a startup/storage failure.
            while True:
                item=self.queue.get()
                if item[0]=='stop':break
                if item[0]=='reset':item[2].set()
                if item[0]=='barrier':item[1].set()
        finally:
            if c:c.close()

    def close(self):
        if self.closed:return
        self.closed=True;self.active=False
        if self.thread:
            self.queue.put(('stop',));self.thread.join(10)
        if self.path and (not self.thread or not self.thread.is_alive()):
            shutil.rmtree(self.path.parent,ignore_errors=True)
