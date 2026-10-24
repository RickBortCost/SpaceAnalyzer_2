"""Paced ctypes ADS notification ABI -> real decode -> queues -> primary.

DLL/PLC are simulated. This measures callbacks, not ADS network or Windows timing.
"""
import argparse
import json
from pathlib import Path
import struct
import tempfile
import time
from unittest.mock import patch
from collections import Counter
from test_ads_notifications import NotifyDLL,NotificationAcquisition,FILETIME_EPOCH,configure_library,until,app
from acquisition_engine import AcquisitionEngine
from acquisition_health import EventStore,quantiles
from primary_recording import PrimaryRecorder
from primary_format import iter_samples,atomic_json
from primary_worker import usage


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--channels',type=int,default=1000);p.add_argument('--duration',type=float,default=10);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--grouping',action='store_true',help='opt-in block notifications over contiguous simulated PLC memory')
    args=p.parse_args();cycle_box=[0]
    if args.grouping:
        from test_phase1_integrity import BlockDLL
        dll=BlockDLL(args.channels);nids=['ads:GVL.Channel'+str(i) for i in range(args.channels)]
        dll.peer.value=lambda name:struct.pack('<d',float(cycle_box[0]*1000000+int(name[11:])))
    else:
        dll=NotifyDLL();nids=['ads:MAIN.Channel'+str(i) for i in range(args.channels)]
        dll.peer.symbols={n[4:]:(5,'LREAL',8) for n in nids};dll.peer.value=lambda name:struct.pack('<d',float(cycle_box[0]*1000000+int(name[12:])))
    configure_library(dll);expected_subs=1 if args.grouping else args.channels
    with tempfile.TemporaryDirectory() as tmp,patch('ads_native.load_library',return_value=(dll,'simulated.dll')):
        events=EventStore(Path(tmp)/'events.sqlite');rec=PrimaryRecorder(tmp,flush_ms=100,events=events);rec.start([dict(nodeid=n) for n in nids])
        engine=AcquisitionEngine(lambda t,rows,ms,g:rec.enqueue(t,rows,ms,backpressure_timeout=.5),  # same bound as DurableRecorder.enqueue
            lambda *a:None,rec.fail,events)
        worker=NotificationAcquisition({**app.DEFAULT_CONFIG,'source':'ads','ads_transport':'native','ads_mode':'notify','ads_notify_grouping':args.grouping},lambda:(nids,10,(1,1)),lambda t,rows,ms,g:engine.submit('ads','ads',t,rows,ms,g),lambda d:None)
        worker.start();until(lambda:len(dll.subs)==expected_subs,60)
        started=time.monotonic();cpu=time.process_time();cycles=round(args.duration*100);produced=0;late=[];memory=[];base=FILETIME_EPOCH+time.time_ns()//100
        try:
            for cycle in range(cycles):
                due=started+cycle*.01
                if due>time.monotonic():time.sleep(due-time.monotonic())
                late.append(max(0,(time.monotonic()-due)*1000));cycle_box[0]=cycle
                dll.emit(base+cycle*100000);produced+=args.channels
                if cycle%50==0:memory.append(usage()['rss_bytes'])
                if worker.error or rec.error:break
            producer_seconds=time.monotonic()-started
            worker.stop();worker.thread.join(15);engine.flush(30);state=rec.stop(30);acquisition_cpu=time.process_time()-cpu
            seen=Counter();mismatches=0
            for row in iter_samples(state['path']):
                nid=row['nodeid'];index=int(nid.split('Channel')[1]);mismatches+=row['source_value']!=float(seen[nid]*1000000+index);seen[nid]+=1
            saved=sum(seen.values());passed=produced==saved==cycles*args.channels and not mismatches and not worker.error and not state['error'] and producer_seconds<=args.duration*1.05
            result=dict(result='PASS' if passed else 'TARGET_NOT_MET',simulated_dll=True,hardware_tested=False,channels=args.channels,period_ms=10,
                notify_grouping=args.grouping,notifications=len(dll.subs),callbacks_per_cycle=expected_subs,
                requested_seconds=args.duration,produced=produced,saved=saved,missing=produced-saved,mismatches=mismatches,producer_seconds=producer_seconds,
                producer_values_per_second=produced/producer_seconds,producer_lateness_ms={**quantiles(late),'max':max(late)},
                parent_cpu_seconds=acquisition_cpu,parent_rss_peak_observed=max(memory),ads=worker.stats(),ads_error=worker.error,
                primary=state,acquisition=engine.status(),limits='ctypes callback ABI simulation on Linux; no real PLC, DLL, router or network qualification')
            atomic_json(args.output,result);print(json.dumps({k:result[k] for k in ('result','channels','produced','saved','producer_values_per_second')}))
        finally:worker.stop();worker.thread.join(15);engine.close();rec.stop(10);events.close()
    return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
