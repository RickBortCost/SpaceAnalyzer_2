"""Independent recording sessions: one acquisition and detached file finalizers.

The compatibility `busy` flag includes saves; `selection_locked`/`acquiring`
only cover the current acquisition. A finalizer never receives the next session.
"""
from collections import OrderedDict
from pathlib import Path
import threading
import time
import uuid
from recordings import Recorder


class RecordingManager:
    def __init__(self,directory,version,queue_capacity=2000,factory=Recorder,max_pending=32):
        self.directory=Path(directory).resolve();self.version=version;self.capacity=queue_capacity
        self.factory=factory;self.max_pending=max_pending
        self.lock=threading.RLock();self.sessions=OrderedDict();self.active=None;self.latest=None
        self.starting=False;self.closed=False;self.latest_id=None;self.revision=0

    @staticmethod
    def _acquiring(rec):
        if hasattr(type(rec),'acquiring'):return rec.acquiring
        session=rec.fda_session
        return session.original.busy if session is not None else rec.busy

    @property
    def acquiring(self):
        with self.lock:rec=self.active;starting=self.starting
        return starting or rec is not None and self._acquiring(rec)

    @property
    def busy(self):
        with self.lock:records=list(self.sessions.values());starting=self.starting
        return starting or any(rec.busy for rec in records if isinstance(rec,Recorder))

    def __getattr__(self,name):
        # Existing diagnostics and tests can still inspect the latest recorder.
        rec=self.__dict__.get('latest')
        if rec is not None:return getattr(rec,name)
        raise AttributeError(name)

    def start(self,fmt,nodes,endpoint='',sample_ms=500,options=None):
        with self.lock:
            if self.closed:raise ValueError('Servizio registrazione in chiusura')
            if self.acquiring:raise ValueError('Una registrazione è già in corso')
            pending=sum(isinstance(r,Recorder) and r.busy for r in self.sessions.values())
            if pending>=self.max_pending:raise ValueError('Troppi salvataggi in attesa: attendere il completamento di un file')
            self.starting=True
        try:
            rec=self.factory(self.directory,self.version,self.capacity)
            rec.start(fmt,nodes,endpoint,sample_ms,options)
            ident=uuid.uuid4().hex
            with self.lock:
                self.sessions[ident]=rec;self.active=self.latest=rec;self.latest_id=ident;self.revision+=1
        finally:
            with self.lock:self.starting=False
        return self.status()

    def enqueue(self,*args):
        with self.lock:rec=self.active
        return rec.enqueue(*args) if rec is not None else False

    def enqueue_primary(self,*args):
        with self.lock:rec=self.active
        return rec.enqueue_primary(*args) if rec is not None and hasattr(rec,'enqueue_primary') else False

    def annotate(self,event):
        # Integrity events belong to the acquiring session only, never to a finalizer.
        with self.lock:rec=self.active
        return rec.annotate(event) if rec is not None and hasattr(rec,'annotate') else False

    def fail(self,*args,**kwargs):
        with self.lock:rec=self.active
        if rec is not None:return rec.fail(*args,**kwargs)

    def source_diagnostics(self,*args):
        with self.lock:rec=self.active
        if rec is not None:return rec.source_diagnostics(*args)

    def stop(self,reason='user',timeout=5):
        with self.lock:rec=self.active;self.active=None
        if rec is not None:
            with self.lock:self.revision+=1
            rec.stop(reason,0)
        # Waiting is optional. The HTTP Stop path uses zero; shutdown may wait.
        if timeout:
            deadline=time.monotonic()+timeout
            while self.busy and time.monotonic()<deadline:threading.Event().wait(.01)
        return self.status()

    def status(self):
        with self.lock:items=list(self.sessions.items());active=self.active;latest_id=self.latest_id;revision=self.revision;starting=self.starting
        jobs=[];finished={};current=None;pending=0
        for ident,rec in items:
            if isinstance(rec,dict):state=dict(rec)
            else:
                state=rec.status();state['recording_id']=ident
                if not state['busy'] and rec is not active:finished[ident]=state
            if ident==latest_id:current=dict(state)
            if rec is not active or not self._acquiring(rec):
                pending+=int(state['busy']);jobs.append(state)
        with self.lock:
            for ident,state in finished.items():
                if self.sessions.get(ident) is not self.active:self.sessions[ident]=state
            # Retain recent results without retaining threads/queues forever.
            removable=[i for i,r in self.sessions.items() if isinstance(r,dict)]
            for ident in removable[:-64]:self.sessions.pop(ident,None)
        if current is None:
            current={'state':'idle','active':False,'busy':False,'format':'','filename':'','path':'','error':'',
                     'rows_written':0,'files':[],'nodes':[],'directory':str(self.directory)}
        locked=bool(starting or active is not None and self._acquiring(active))
        current.update(revision=revision,selection_locked=locked,can_start=not locked and not self.closed,
                       busy=bool(locked or pending),finalizing_count=pending,
                       finalizations=sorted([j for j in jobs if j['busy']]+[j for j in jobs if not j['busy']][-16:],key=lambda j:j.get('started_at') or ''))
        return current

    def close(self):
        with self.lock:self.closed=True;records=[r for r in self.sessions.values() if not isinstance(r,dict)]
        self.stop('application_close',0)
        for rec in records:
            while rec.busy:rec.stop('application_close',.25)
