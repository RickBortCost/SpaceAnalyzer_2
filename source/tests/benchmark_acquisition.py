"""Paced numbered source -> connection queues -> isolated primary writer.

Run --duration 86400 --segment-seconds 60 for the separate 24-hour soak.
The report states measured rates; no industrial ADS network is simulated here.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from acquisition_engine import AcquisitionEngine
from acquisition_health import EventStore,quantiles
from primary_recording import PrimaryRecorder
from primary_format import iter_samples,inspect,atomic_json
from primary_worker import usage


def segment(root,channels,seconds,stress,period_ms=10,offset=0):
    protocols=['ads','opcua','mqtt','s7'];groups={p:[] for p in protocols}
    for i in range(channels):
        p=protocols[i%4];nid=f'{p}:channel{i}' if p!='opcua' else f'ns=2;s=channel{i}';groups[p].append(dict(nodeid=nid))
    nodes=[n for values in groups.values() for n in values];events=EventStore(root/'events.sqlite')
    rec=PrimaryRecorder(root,events=events,flush_ms=100);rec.start(nodes,dict(benchmark=True))
    quit_live=threading.Event();live_calls=[0]
    def live(*args):
        live_calls[0]+=1
        if stress:quit_live.wait(.2) # A deliberately slow renderer/history consumer.
    engine=AcquisitionEngine(lambda t,rows,ms,g:rec.enqueue(t,rows,ms),live,rec.fail,events)
    started=time.monotonic();cpu_started=time.process_time();cycles=round(seconds*1000/period_ms);produced=0;lateness=[];memory=[]
    source_base=time.time_ns();max_backlog=0
    try:
        for cycle in range(cycles):
            due=started+cycle*period_ms/1000
            if due>time.monotonic():time.sleep(due-time.monotonic())
            lateness.append(max(0,(time.monotonic()-due)*1000))
            received_ns=time.time_ns()
            for p,nodes_p in groups.items():
                rows=[dict(nodeid=n['nodeid'],value=cycle+offset,_source_typed=cycle+offset,source_sequence=cycle+1,source_epoch=1,
                    received_ns=received_ns,source_filetime=str(116444736000000000+source_base//100+cycle*period_ms*10000),data_type='Int32',quality='Good') for n in nodes_p]
                produced+=len(rows);engine.submit(p,p,source_base/1e6+cycle*period_ms,rows,period_ms)
            if cycle%50==0:
                memory.append(usage()['rss_bytes']);max_backlog=max(max_backlog,sum(x['buffer']['samples'] for x in engine.status()['connections'].values()))
        produced_seconds=time.monotonic()-started
        drained=engine.flush(60);state=rec.stop(60);elapsed=time.monotonic()-started;acquisition_cpu=time.process_time()-cpu_started
        seen=Counter();mismatches=0
        for row in iter_samples(state['path']):
            nid=row['nodeid'];expected=seen[nid]+offset
            mismatches+=row['source_value']!=expected or row['sample_sequence']!=seen[nid]+1;seen[nid]+=1
        saved=sum(seen.values());target_met=produced_seconds<=seconds*1.05 and saved==produced and mismatches==0 and not state['error'] and drained
        result=dict(result='PASS' if target_met else 'TARGET_NOT_MET',channels=channels,period_ms=period_ms,requested_seconds=seconds,
            requested_values_per_second=channels*1000/period_ms,produced=produced,saved=saved,missing=produced-saved,
            sequence_mismatches=mismatches,all_channels_complete=all(seen[n['nodeid']]==cycles for n in nodes),
            produced_seconds=produced_seconds,producer_values_per_second=produced/max(.001,produced_seconds),total_seconds=elapsed,
            parent_cpu_seconds=acquisition_cpu,parent_cpu_percent_of_one_core=acquisition_cpu/elapsed*100,
            parent_rss_peak_observed=max((x for x in memory if x),default=None),writer_usage=state.get('usage'),
            producer_lateness_ms={**quantiles(lateness),'max':max(lateness,default=0)},max_backlog_samples_observed=max_backlog,
            primary=state,acquisition=engine.status(),ui_stress=stress,live_calls=live_calls[0],
            limits='Synthetic adapters; actual PLC, ADS DLL, network and disk require on-site qualification')
        return result
    finally:
        quit_live.set();engine.close();rec.stop(10);events.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--duration',type=float,default=10);p.add_argument('--channels',type=int,default=1000)
    p.add_argument('--segment-seconds',type=float,default=60);p.add_argument('--ui-stress',action='store_true');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--directory',type=Path);p.add_argument('--keep-captures',action='store_true');args=p.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True);segments=[];done=0;wall=time.monotonic()
    while done<args.duration:
        duration=min(args.segment_seconds,args.duration-done)
        with tempfile.TemporaryDirectory(dir=args.directory) as directory:
            result=segment(Path(directory),args.channels,duration,args.ui_stress,offset=round(done*100))
            if args.keep_captures:
                import shutil
                target=args.output.parent/('capture_'+str(len(segments)));shutil.copytree(directory,target)
        segments.append(result);done+=duration
        report=dict(result='PASS' if all(s['result']=='PASS' for s in segments) else 'TARGET_NOT_MET',
            measured_wall_seconds=time.monotonic()-wall,requested_seconds=args.duration,completed_simulated_seconds=done,segments=segments,
            platform=sys.platform,python=sys.version,cpu_count=os.cpu_count(),run_at_ns=str(time.time_ns()))
        atomic_json(args.output,report)
        print(json.dumps(dict(segment=len(segments),result=result['result'],produced=result['produced'],saved=result['saved'],rate=result['producer_values_per_second'],report=str(args.output))),flush=True)
        if result['result']!='PASS':break
    return 0 if report['result']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
