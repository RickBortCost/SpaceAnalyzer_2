"""Continuous numbered acquisition with overlapping file rotation/verification.

24 hours is an explicit separate run, never implied by the short smoke test.
Example: python source/tests/soak_acquisition.py --duration 86400 --directory D:/Soak --report soak.json
Use a dedicated disposable directory. --delete-verified bounds test disk usage.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import queue
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from acquisition_engine import AcquisitionEngine
from acquisition_health import EventStore
from primary_recording import PrimaryRecorder
from primary_format import atomic_json,iter_samples
from primary_worker import usage


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--duration',type=float,default=86400);p.add_argument('--channels',type=int,default=1000)
    p.add_argument('--rotate-seconds',type=float,default=60);p.add_argument('--directory',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--delete-verified',action='store_true');args=p.parse_args()
    if args.duration<=0 or args.rotate_seconds<=0 or not 1<=args.channels<=10000:p.error('Durata/canali/rotazione non validi')
    args.directory.mkdir(parents=True,exist_ok=True);args.report.parent.mkdir(parents=True,exist_ok=True)
    nodes=[dict(nodeid='ads:soak'+str(i)) for i in range(args.channels)];events=EventStore(args.directory/'events.sqlite')
    lock=threading.Lock();active={};verify_queue=queue.Queue(2);stop=threading.Event();failed=[];seen=Counter();verified=[0];files=[0];peak_rss=[0];producer_late=[0.]
    def new_record():
        r=PrimaryRecorder(args.directory,flush_ms=100,events=events);r.start(nodes,dict(soak=True));return r
    active['record']=new_record()
    def failure(error):
        failed.append(str(error));events.emit('soak_failure',error=str(error));stop.set()
        with lock:active['record'].fail(str(error))
    def record(t,rows,ms,g):
        with lock:return active['record'].enqueue(t,rows,ms)
    engine=AcquisitionEngine(record,lambda *a:None,failure,events)
    def verify():
        try:
            while True:
                r=verify_queue.get()
                if r is None:return
                state=r.stop(60)
                if state['busy'] or state['error'] or state.get('integrity')!='complete':raise RuntimeError('Segmento non integro: '+str(state))
                for row in iter_samples(state['path']):
                    nid=row['nodeid']
                    if row['source_value']!=seen[nid] or row['sample_sequence']!=seen[nid]+1:raise RuntimeError('Sequenza persa/duplicata: '+nid)
                    seen[nid]+=1;verified[0]+=1
                files[0]+=1
                # An append-only audit for every verified segment; report holds bounded totals.
                with args.report.with_suffix('.segments.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps(dict(path=state['path'],samples=state['durable_samples'],latency_ms=state['latency_ms'],usage=state.get('usage')))+'\n')
                if args.delete_verified:
                    Path(state['path']).unlink()
                    for suffix in ('.lock','.status.json'):
                        Path(str(r.path)+suffix).unlink(missing_ok=True)
        except Exception as exc:failure(exc)
    verifier=threading.Thread(target=verify,name='soak-verify',daemon=True);verifier.start()
    def rotate():
        try:
            while not stop.wait(args.rotate_seconds):
                following=new_record() # Starts in parallel with ongoing acquisition.
                with lock:previous=active['record'];active['record']=following;previous.stop(0)
                try:verify_queue.put_nowait(previous)
                except queue.Full:following.fail('Backlog verifiche pieno');raise RuntimeError('Backlog verifiche pieno')
        except Exception as exc:failure(exc)
    rotator=threading.Thread(target=rotate,name='soak-rotation',daemon=True);rotator.start()
    started=time.monotonic();cpu=time.process_time();cycles=round(args.duration*100);produced=0
    try:
        for cycle in range(cycles):
            if stop.is_set():break
            due=started+cycle*.01
            if due>time.monotonic():stop.wait(due-time.monotonic())
            producer_late[0]=max(producer_late[0],(time.monotonic()-due)*1000)
            rows=[dict(nodeid=n['nodeid'],value=cycle,_source_typed=cycle,source_sequence=cycle+1,source_epoch=1,received_ns=time.time_ns(),quality='Good',data_type='Int32') for n in nodes]
            produced+=len(rows);engine.submit('soak','ads',time.time_ns()/1e6,rows,10)
            if cycle%100==0:peak_rss[0]=max(peak_rss[0],usage()['rss_bytes'] or 0)
    except KeyboardInterrupt:failed.append('Interrupted by operator')
    finally:
        stop.set();rotator.join(20);engine.flush(60)
        with lock:last=active['record'];last.stop(0)
        if verifier.is_alive():
            try:verify_queue.put(last,timeout=60);verify_queue.put(None,timeout=60)
            except queue.Full:failed.append('Verifier backlog at shutdown')
            verifier.join(120)
        else:last.stop(10)
        engine.close();events.close()
    result=dict(result='PASS' if not failed and not verifier.is_alive() and verified[0]==produced==cycles*args.channels else 'FAIL',
        requested_seconds=args.duration,measured_wall_seconds=time.monotonic()-started,channels=args.channels,period_ms=10,
        produced=produced,verified_samples=verified[0],verified_files=files[0],missing=produced-verified[0],errors=failed,
        parent_cpu_seconds=time.process_time()-cpu,parent_rss_peak_observed=peak_rss[0],producer_lateness_max_ms=producer_late[0],
        acquisition=engine.status(),hardware_tested=False,continuous_generator=True)
    atomic_json(args.report,result);print(json.dumps(result,indent=2));return 0 if result['result']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
