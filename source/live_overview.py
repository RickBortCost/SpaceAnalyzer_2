"""Bounded full-session display history, independent of the original analysis FIFO.

Callers own runtime.lock. Only immutable (time, value, quality) references are
retained here; this is a display envelope, never input for FFT or recordings.
First/last samples, extrema and quality/gap representatives survive compaction.
"""
import math
from collections import deque


def envelope(points):
    ordered=sorted({p[0]:p for p in points}.values(),key=lambda p:p[0])
    if not ordered:return ()
    chosen={0,len(ordered)-1}
    numeric=[i for i,p in enumerate(ordered) if isinstance(p[1],(int,float)) and math.isfinite(p[1])]
    if numeric:
        chosen.add(min(numeric,key=lambda i:ordered[i][1]))
        chosen.add(max(numeric,key=lambda i:ordered[i][1]))
    for quality in ('Bad','Uncertain'):
        i=next((i for i,p in enumerate(ordered) if p[2]==quality),None)
        if i is not None:chosen.add(i)
    gap=next((i for i,p in enumerate(ordered) if p[1] is None),None)
    if gap is not None:chosen.add(gap)
    return tuple(ordered[i] for i in sorted(chosen))


class Bucket:
    __slots__=('first','last','low','high','gap','bad','uncertain')
    def __init__(self):
        self.first=self.last=self.low=self.high=self.gap=self.bad=self.uncertain=None

    def append(self,p):
        if self.first is None:self.first=p
        self.last=p;v=p[1]
        if v is not None and isinstance(v,(int,float)) and math.isfinite(v):
            if self.low is None or v<self.low[1]:self.low=p
            if self.high is None or v>self.high[1]:self.high=p
        elif self.gap is None:self.gap=p
        if p[2]=='Bad' and self.bad is None:self.bad=p
        if p[2]=='Uncertain' and self.uncertain is None:self.uncertain=p

    def points(self):
        return sorted({p[0]:p for p in (self.first,self.low,self.high,self.gap,self.bad,self.uncertain,self.last) if p is not None}.values(),key=lambda p:p[0])


class Overview:
    def __init__(self,bucket_limit=256):
        if bucket_limit<2 or bucket_limit%2:raise ValueError('Even bucket limit required')
        self.limit=bucket_limit;self.width=1;self.buckets=[];self.active=Bucket();self.active_count=0
        self.pending=None;self.count=0

    def append(self,point):
        p=tuple(point[:3])
        if len(p)<3:p=(*p,'Good' if p[1] is not None else 'Bad')
        if not isinstance(p[0],(int,float)) or not math.isfinite(p[0]):return
        # Formula workers may revise their newest timestamp. Delay its aggregation
        # until the next timestamp so old extrema are not retained after revision.
        if self.pending is not None and self.pending[0]==p[0]:
            self.pending=p;return
        if self.pending is not None:self._finalize(self.pending)
        self.pending=p;self.count+=1

    def _finalize(self,p):
        self.active.append(p);self.active_count+=1
        if self.active_count>=self.width:
            self.buckets.append(self.active.points());self.active=Bucket();self.active_count=0
            if len(self.buckets)>=self.limit:
                self.buckets=[envelope((*self.buckets[i],*self.buckets[i+1])) for i in range(0,len(self.buckets),2)]
                self.width*=2

    def points(self):
        points=[p for bucket in self.buckets for p in bucket]
        points.extend(self.active.points())
        if self.pending is not None:points.append(self.pending)
        return sorted({p[0]:p for p in points}.values(),key=lambda p:p[0])


class LiveBuffer(deque):
    """Existing bounded original FIFO plus a bounded display envelope."""
    def __init__(self,iterable=(),maxlen=None,on_append=None,archive_generation=None):
        previous=getattr(iterable,'overview',None)
        # Use the supplied deque directly in C, without replaying the overridden
        # append method when buffer limits change.
        super().__init__(iterable,maxlen=maxlen)
        self.on_append=on_append;self.archive_generation=archive_generation
        self.overview=previous if previous is not None else Overview()
        if previous is None:
            for p in self:self.overview.append(p)

    def append(self,point):
        super().append(point);self.overview.append(point)
        if self.on_append:self.on_append(point)

    def __setitem__(self,index,point):
        super().__setitem__(index,point)
        if index in (-1,len(self)-1):self.overview.append(point)
        if index in (-1,len(self)-1) and self.on_append:self.on_append(point)
