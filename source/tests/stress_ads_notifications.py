"""Paced DLL callback simulation: real sink, Parquet and UI API; not PLC qualification."""
import json,struct,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
from test_ads_notifications import app,NotifyDLL,until,configure_library,NotificationAcquisition,FILETIME_EPOCH
from recordings import Recorder
import pyarrow.parquet as pq
count=64;cycles=10000
dll=NotifyDLL();configure_library(dll)
nids=['ads:MAIN.Test'+str(i) for i in range(count)]
dll.peer.symbols={n[4:]:(5,'LREAL',8) for n in nids}
dll.peer.value=lambda name:struct.pack('<d',float(name[9:]))
original=dll.AdsSyncReadStateReqEx.fn;calls=[0]
def control(*args):
    calls[0]+=1
    if calls[0]==2:time.sleep(2);return 0x745
    return original(*args)
dll.AdsSyncReadStateReqEx.fn=control
cfg={**app.DEFAULT_CONFIG,'source':'ads','ads_transport':'native','ads_mode':'notify','sample_ms':1}
app.runtime.want_connected=True;app.runtime.active_config=cfg
app.runtime.selected=nids;app.runtime.sample_ms=1
app.runtime.window_seconds=30;app.runtime.max_samples=20000
app.runtime.buffer_mode='samples';app.runtime.samples={}
gen=(app.runtime.selection_revision,app.runtime.buffer_epoch)
poll_stop=threading.Event();polls=[];poll_errors=[]
def poll():
    http=app.app.test_client()
    while not poll_stop.is_set():
        start=time.monotonic()
        try:
            r=http.get('/api/values');assert r.status_code==200
        except Exception as exc:poll_errors.append(str(exc))
        polls.append(time.monotonic()-start);poll_stop.wait(.25)
with tempfile.TemporaryDirectory() as tmp,patch('ads_native.load_library',return_value=(dll,'fake64.dll')):
    rec=Recorder(tmp,'stress')
    rec.start('parquet',[{'nodeid':n,'display_name':n} for n in nids],sample_ms=1,options={'buffer_seconds':10})
    with patch.object(app,'recording',rec):
        w=NotificationAcquisition(cfg,lambda:(nids,1,gen),app.ads_notification_sink,app.ads_notification_report)
        w.start();until(lambda:len(dll.subs)==count,10)
        ui=threading.Thread(target=poll);ui.start()
        start=time.monotonic();base=FILETIME_EPOCH+time.time_ns()//100;late=[]
        try:
            for i in range(cycles):
                assert w.error is None,w.error
                late.append(max(0,time.monotonic()-(start+i*.001)))
                dll.emit(base+i*10000)
                remaining=start+(i+1)*.001-time.monotonic()
                if remaining>0:time.sleep(remaining)
            emit_seconds=time.monotonic()-start
            until(lambda:w.processed==cycles*count,20)
        finally:
            poll_stop.set();ui.join(10);w.stop();w.thread.join(10);status=rec.stop()
        assert not w.thread.is_alive() and not ui.is_alive()
        assert w.error is None,w.error
        assert w.dropped==0 and status['dropped_cycles']==0
        assert not poll_errors,poll_errors
        seen={n:0 for n in nids}
        for batch in pq.ParquetFile(status['path']).iter_batches(columns=['node_id','ads_filetime','sample_ms']):
            for r in batch.to_pylist():
                nid=r['node_id'];assert int(r['ads_filetime'])==base+seen[nid]*10000
                assert int(r['sample_ms'])==1;seen[nid]+=1
        assert all(v==cycles for v in seen.values()),seen
        assert w.control_timeouts==1 and w.control_error is None
        print(json.dumps(dict(result='PASS',signals=count,cycles_per_signal=cycles,rows=sum(seen.values()),
            producer_elapsed_seconds=emit_seconds,producer_lateness_max_ms=max(late)*1000,
            queue_high_water=w.queue_high_water,dropped=w.dropped,recording_dropped_cycles=status['dropped_cycles'],
            control_timeouts=w.control_timeouts,control_recovered=w.control_error is None,
            api_polls=len(polls),api_max_ms=max(polls)*1000,original_timestamps_verified=True,
            limitations='Linux, simulated native callbacks, 1 ms source timestamps; no physical PAC/Windows validation'),indent=2))
