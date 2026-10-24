"""Bounded peak/quality previews and fingerprinted, bounded replay caches.

Only display data is reduced. Original-row readers for exports, formulas and FFT
remain independent. Duplicate timestamps are retained until display reduction.
"""
from collections import OrderedDict
import copy
import math
import threading

class ReplayCache:
    def __init__(self,max_points=250000,max_items=32):
        self.lock=threading.RLock();self.items=OrderedDict();self.points=0
        self.max_points=max_points;self.max_items=max_items
    def get(self,key):
        with self.lock:
            item=self.items.get(key)
            if item is None:return None
            self.items.move_to_end(key);return copy.deepcopy(item[0])
    def put(self,key,result):
        weight=sum(len(points) for points in result.get('series',{}).values()) or 1
        if weight>self.max_points:return
        with self.lock:
            previous=self.items.pop(key,None)
            if previous:self.points-=previous[1]
            while self.items and (self.points+weight>self.max_points or len(self.items)>=self.max_items):
                _,(_,n)=self.items.popitem(last=False);self.points-=n
            self.items[key]=(copy.deepcopy(result),weight);self.points+=weight

VIEWS=ReplayCache()
MARKERS=ReplayCache(max_points=200,max_items=64)
DESCRIPTIONS=ReplayCache(max_points=256,max_items=256)


def fingerprint(path,metadata):
    paths=[path,path.with_suffix('.metadata.json')]
    if path.suffix.lower()=='.dat':paths.append(path.with_suffix('.json'))
    original=metadata.get('original_archive')
    if isinstance(original,str) and original and '/' not in original and '\\' not in original:paths.append(path.parent/original)
    result=[]
    for item in paths:
        try:s=item.stat();result.append((str(item),s.st_size,s.st_mtime_ns))
        except FileNotFoundError:result.append((str(item),None,None))
    return tuple(result)


def _envelope(points):
    # Ordinals distinguish two original updates with an identical timestamp.
    picks={points[0][0]:points[0],points[-1][0]:points[-1]}
    valid=[p for p in points if p[2] is not None]
    if valid:
        for p in (min(valid,key=lambda p:p[2]),max(valid,key=lambda p:p[2])):picks[p[0]]=p
    for quality in ('Bad','Uncertain'):
        p=next((p for p in points if p[3]==quality),None)
        if p is not None:picks[p[0]]=p
    p=next((p for p in points if p[2] is None),None)
    if p is not None:picks[p[0]]=p
    return [picks[i] for i in sorted(picks)]

class PeakSeries:
    def __init__(self,limit):
        self.limit=limit;self.count=0;self.earliest=self.latest=None;self.raw=[];self.buckets=[];self.pending=[];self.width=1
        self.capacity=max(2,(limit//7-2)//2*2)
    def add(self,t,value,quality):
        point=(self.count,t,value,quality);self.count+=1
        if self.earliest is None or t<self.earliest[1]:self.earliest=point
        if self.latest is None or t>=self.latest[1]:self.latest=point
        if self.raw is not None:
            self.raw.append(point)
            if len(self.raw)<=self.limit:return
            raw,self.raw=self.raw,None
            for p in raw:self._add(p)
        else:self._add(point)
    def _add(self,point):
        self.pending.append(point)
        # Keep at most seven representatives while counting the whole bucket.
        if len(self.pending)>14:self.pending=_envelope(self.pending)
        if (point[0]+1)%self.width==0:
            self.buckets.append(_envelope(self.pending));self.pending=[]
            if len(self.buckets)>=self.capacity:
                self.buckets=[_envelope(self.buckets[i]+self.buckets[i+1]) for i in range(0,len(self.buckets),2)]
                self.width*=2
    def result(self):
        points=self.raw if self.raw is not None else [p for bucket in self.buckets for p in bucket]+(_envelope(self.pending) if self.pending else [])
        if self.raw is None and self.earliest is not None:
            chosen={p[0]:p for p in points};chosen[self.earliest[0]]=self.earliest;chosen[self.latest[0]]=self.latest;points=list(chosen.values())
        return [[p[1],p[2],p[3]] for p in sorted(points,key=lambda p:(p[1],p[0]))]


def plot_rows(library,path,signal_ids=None,start=None,end=None):
    if path.suffix.lower()=='.das':
        from fda_das import plot_rows as fda_rows
        yield from fda_rows(path,library._metadata(path),signal_ids,start,end);return
    if path.suffix.lower()=='.parquet':
        rows=library._iter_parquet(path,columns=['timestamp_ms','node_id','value_numeric','quality','good','status','signal_name','data_type','sample_ms'],signal_ids=signal_ids,start_ms=start,end_ms=end)
    else:rows=library._iter_rows(path,signal_ids,start,end)
    wanted=None if signal_ids is None else set(signal_ids)
    for row in rows:
        nid=str(row.get('node_id') or '')
        if not nid or wanted is not None and nid not in wanted:continue
        try:t=float(row.get('timestamp_ms'))
        except (ValueError,TypeError,OverflowError):continue
        if not math.isfinite(t) or start is not None and t<start or end is not None and t>end:continue
        try:value=float(row.get('value_numeric'))
        except (ValueError,TypeError,OverflowError):value=None
        if value is not None and not math.isfinite(value):value=None
        good=str(row.get('good',True)).lower() in ('true','1','yes','good')
        quality=row.get('quality') or ('Good' if good else 'Uncertain' if str(row.get('status','')).startswith('Uncertain') else 'Bad')
        yield nid,t,value if quality=='Good' else None,quality,str(row.get('signal_name') or nid),str(row.get('data_type') or 'Double'),row.get('sample_ms')


def load(library,path,signal_ids,start,end,limit):
    series={};names={};types={};numeric=rows=0;low=math.inf;high=-math.inf;periods=[]
    for nid,t,value,q,name,dtype,period in plot_rows(library,path,signal_ids,start,end):
        if nid not in series:series[nid]=PeakSeries(limit)
        series[nid].add(t,value,q);names[nid]=name;types[nid]=dtype
        numeric+=int(value is not None and q=='Good');rows+=1;low=min(low,t);high=max(high,t)
        if len(periods)<1000:
            try:
                ms=float(period)
                if math.isfinite(ms):periods.append(ms)
            except (ValueError,TypeError,OverflowError):pass
    meta=library._metadata(path);meta_names={s['nodeid']:s.get('display_name',s['nodeid']) for s in meta.get('signals',[]) if isinstance(s,dict) and s.get('nodeid')}
    if signal_ids is not None:
        for nid in signal_ids:
            series.setdefault(nid,PeakSeries(limit));names.setdefault(nid,meta_names.get(nid,nid))
    returned={nid:s.result() for nid,s in series.items()}
    from recording_reader import SUPPORTED
    return dict(name=path.relative_to(library.directory).as_posix(),format=SUPPORTED[path.suffix.lower()],series=returned,
        signals=[dict(nodeid=nid,display_name=names[nid],data_type=types.get(nid,'Double'),points_original=series[nid].count,points_returned=len(points)) for nid,points in returned.items()],
        rows_scanned=rows,numeric_rows=numeric,start_ms=None if math.isinf(low) else low,end_ms=None if math.isinf(high) else high,
        max_points_per_signal=limit,sample_ms=sum(periods)/len(periods) if periods else None,
        decimated=any(series[n].count>len(points) for n,points in returned.items()))
