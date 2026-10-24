"""Repeatable FDA/Parquet conversion and replay benchmark, with validity checks.

Run once with --source pointing to the previous source tree and once to the
updated tree. Files are local deterministic fixtures, not PLC recordings.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--directory',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
parser.add_argument('--frames',type=int,default=60000)
args=parser.parse_args();sys.path.insert(0,str(args.source.resolve()))
import pyarrow as pa
import pyarrow.parquet as pq
from fda_das import export_parquet,inspect
from recording_reader import RecordingLibrary
args.directory.mkdir(parents=True,exist_ok=True)
path=args.directory/'benchmark.parquet';count=args.frames;channels=4;base=1700000000000.
nodes=[{'nodeid':'ads:MAIN.Load'+str(n),'display_name':'Load '+str(n),'unit':'kN'} for n in range(channels)]
data={key:[] for key in ('timestamp_ms','cycle','node_id','signal_name','value_numeric','quality','good','data_type','sample_ms')}
for i in range(count):
 for n,node in enumerate(nodes):
  for key,value in dict(timestamp_ms=base+i*10,cycle=i+1,node_id=node['nodeid'],signal_name=node['display_name'],
       value_numeric=100*math.sin(i/40+n),quality='Good',good=True,data_type='Double',sample_ms=10).items():data[key].append(value)
pq.write_table(pa.table(data),path,row_group_size=16384,compression='zstd',compression_level=3)
path.with_suffix('.metadata.json').write_text(json.dumps({'signals':nodes,'started_at':'2023-11-14T22:13:20+00:00','initial_sample_ms':10,'duration_seconds':(count-1)*.01,'rows_written':count*channels}))
report={'frames':count,'signals':channels,'rows':count*channels,'source':str(args.source),'seconds':{},'result':'FAIL'}
def measure(name,action):
 start=time.perf_counter();result=action();report['seconds'][name]=round(time.perf_counter()-start,6);return result
files=measure('fda_conversion',lambda:export_parquet(path,nodes));fda=args.directory/files[0]['name'];assert inspect(fda)['frames']==count
library=RecordingLibrary(args.directory)
for name,p in [('fda',fda),('parquet',path)]:
 view=measure(name+'_cold_full',lambda:library.load(p.name,max_points=1000))
 assert view['numeric_rows']==count*channels
 assert all(s['points_original']==count for s in view['signals'])
 assert all(points[0][0]==base and points[-1][0]==base+(count-1)*10 for points in view['series'].values())
 repeat=measure(name+'_repeat_full',lambda:library.load(p.name,max_points=1000));assert repeat==view
 narrow=measure(name+'_narrow_interval',lambda:library.load(p.name,[nodes[0]['nodeid']],base+200000,base+201000,max_points=1000))
 assert narrow['numeric_rows']==101
 assert narrow['series'][nodes[0]['nodeid']][0][0]==base+200000
report['result']='PASS';args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
