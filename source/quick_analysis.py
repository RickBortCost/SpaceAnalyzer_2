"""Streaming interval statistics and formulas using the shared causal engine."""
import itertools
import math
from formula_engine import Engine


class Statistics:
    def __init__(self,x1,x2):
        self.x1=x1;self.x2=x2;self.a=self.b=None;self.da=self.db=math.inf
        self.count=0;self.excluded=0;self.scale=0.;self.mean=0.;self.m2=0.;self.squares=0.
        self.low=math.inf;self.high=-math.inf;self.last=None;self.previous=None
        self.integral=0.;self.duration=0.;self.status=''
    @staticmethod
    def valid(r):
        return r.get('quality','Good')=='Good' and isinstance(r.get('value_numeric'),(int,float)) and math.isfinite(r['value_numeric'])
    def add(self,r):
        t=r['timestamp_ms'];good=self.valid(r);v=r.get('value_numeric');self.status=r.get('status','')
        for key,target in [('a',self.x1),('b',self.x2)]:
            distance=abs(t-target);dkey='d'+key
            if distance<=getattr(self,dkey):
                setattr(self,dkey,distance);setattr(self,key,{'timestamp_ms':t,'value':v} if good else None)
        left=self.previous
        if left is not None:
            dt=(t-left['timestamp_ms'])/1000
            limit=3*max(float(left.get('sample_ms') or 0),float(r.get('sample_ms') or 0))/1000
            if good and self.valid(left) and 0<dt<=limit:
                self.integral+=(left['value_numeric']/2+v/2)*dt;self.duration+=dt
        self.previous=r
        if not good:self.excluded+=1;return
        v=float(v);self.count+=1;self.last=v;self.low=min(self.low,v);self.high=max(self.high,v)
        scale=max(self.scale,abs(v),1e-300)
        if scale!=self.scale:
            ratio=self.scale/scale;self.mean*=ratio;self.m2*=ratio*ratio;self.squares*=ratio*ratio;self.scale=scale
        x=v/self.scale;delta=x-self.mean;self.mean+=delta/self.count
        self.m2+=delta*(x-self.mean);self.squares+=x*x
    def result(self,nid):
        stats={}
        if self.count:
            delta=self.b['value']-self.a['value'] if self.a and self.b else None
            stats=dict(last=self.last,min=self.low,max=self.high,mean=self.mean*self.scale,
                rms=math.sqrt(max(0,self.squares/self.count))*self.scale,
                std=math.sqrt(max(0,self.m2/self.count))*self.scale,p2p=self.high-self.low,
                delta=delta,slope=delta/((self.x2-self.x1)/1000) if delta is not None and self.x2!=self.x1 else None,
                integral=self.integral if self.duration else None)
        return dict(nodeid=nid,x1=self.a,x2=self.b,count=self.count,excluded=self.excluded,last_status=self.status,
            statistics={k:v if v is not None and math.isfinite(v) else None for k,v in stats.items()})


def summarize(rows,ids,start,end,x1,x2):
    groups={nid:Statistics(x1,x2) for nid in ids}
    for row in rows:
        if row['node_id'] in groups and start<=row['timestamp_ms']<=end:groups[row['node_id']].add(row)
    return [stats.result(nid) for nid,stats in groups.items()]


def formula_rows(rows,plan,nid,start,end,period):
    engine=Engine(plan)
    for t,group in itertools.groupby(rows,key=lambda r:r['timestamp_ms']):
        output=engine.process(t,list(group))
        if start<=t<=end:
            for row in output:
                if row['node_id']==nid:row['sample_ms']=period;yield row
