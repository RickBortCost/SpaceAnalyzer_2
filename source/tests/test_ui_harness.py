"""Use the actual OPC UA server and Flask service for the JS component workflow."""
import asyncio
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import threading

from test_integration import client, make_server
from asyncua import ua
from werkzeug.serving import make_server as make_http
import pyarrow.parquet as pq

client.initialize()
if os.environ.get("UI_SCRIPT")=="test_advanced_ui.cjs":
    import logging
    logging.disable(logging.NOTSET)
if os.environ.get('MULTICHART_TEST'):
    import csv
    import pyarrow as pa
    from recordings import COLUMNS
    from datetime import datetime, timezone
    client.RECORDINGS_DIR.mkdir(parents=True,exist_ok=True)
    start=1700000000000
    definitions=[{'nodeid':'ns=2;s=Temperature','display_name':'Temperatura'},{'nodeid':'ns=2;s=Power','display_name':'Potenza'}]
    for filename,factor in [('fixture_A.csv',1),('fixture_B.parquet',10)]:
        rows=[]
        for i in range(6000):
            for k,node in enumerate(definitions):
                value=(i/100 if k==0 else 1000000+i)*factor
                row={c:None for c in COLUMNS}
                row.update(timestamp_utc=datetime.fromtimestamp((start+i*10)/1000,timezone.utc).isoformat(),timestamp_ms=start+i*10,elapsed_ms=i*10,cycle=i+1,node_id=node['nodeid'],signal_name=node['display_name'],value_numeric=value,value_text=str(value),data_type='Double',status='Good',good=True,sample_ms=10)
                rows.append(row)
        path=client.RECORDINGS_DIR/filename
        if path.suffix=='.csv':
            with path.open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)
        else:pq.write_table(pa.Table.from_pylist(rows),path)
        path.with_suffix('.metadata.json').write_text(json.dumps({'signals':definitions,'started_at':datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),'ended_at':datetime.fromtimestamp((start+59990)/1000,timezone.utc).isoformat(),'rows_written':len(rows),'duration_seconds':59.99}))
if os.environ.get('UI_SCRIPT')=='test_rbf_ui.cjs':
    import shutil
    client.RECORDINGS_DIR.mkdir(parents=True,exist_ok=True)
    references=Path(os.environ.get('SPACE_ANALYZER_REFERENCE_DIR',Path(__file__).resolve().parents[4]/'upload'))
    for original,target in [('TEST_RBF.rbf','TEST_RBF.rbf'),('TEST DAS 2.DAS','TEST_DAS_2.das')]:
        if (references/original).is_file():shutil.copy2(references/original,client.RECORDINGS_DIR/target)
server, endpoint, root, nodes = client.runtime.run(make_server(), timeout=35)

async def add_signal():
    return await root.add_variable(ua.NodeId('Power',2),'Potenza',1000000.)

power = client.runtime.run(add_signal())

if os.environ.get('UI_SCRIPT') in ('test_fda_ui.cjs','test_rbf_ui.cjs'):
    # Deterministic positive FFT fixture. Real OPC polling may repeat source times;
    # those duplicates must remain intact and the FFT correctly rejects them.
    from recordings import Recorder
    from fda_das import export_parquet
    from test_binary_recordings import sample,BASE
    definitions=[{'nodeid':nodes[0].nodeid.to_string(),'display_name':'FFT 125 Hz'}]
    r=Recorder(client.RECORDINGS_DIR,'UI fixture');r.start('parquet',definitions,sample_ms=1)
    for i in range(256):
        row=sample(math.sin(2*math.pi*125*i/1000));row['nodeid']=definitions[0]['nodeid'];row['source_filetime']=None
        r.enqueue(BASE+i,[row],1)
    state=r.stop(timeout=10)
    if os.environ.get('UI_SCRIPT')=='test_rbf_ui.cjs':
        from rbf import export_parquet as export_rbf
        fixture=export_rbf(Path(state['path']),definitions)[0]
    else:fixture=export_parquet(Path(state['path']),definitions)[0]
    os.environ['UI_FFT_FIXTURE']=fixture['name']

async def vary():
    phase=0
    while True:
        phase+=.05
        await nodes[0].write_value(21.5+math.sin(phase))
        await power.write_value(1000000.+100000.*math.sin(phase))
        await asyncio.sleep(.01)

task = asyncio.run_coroutine_threadsafe(vary(),client.runtime.loop)
http=make_http('127.0.0.1',0,client.app,threaded=True)
thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
ads_peer=None
ssh_peer=None
mqtt_peer=None
mqtt_publisher_stop=threading.Event()
if os.environ.get('UI_SCRIPT') in ('test_mqtt_ui.cjs','test_live_extensions_ui.cjs'):
    from mqtt_peer import Peer as MQTTPeer
    import time
    mqtt_peer=MQTTPeer(auth=('operator',' test-session '));os.environ['TEST_MQTT_PORT']=str(mqtt_peer.port)
    def publish_mqtt():
        i=0
        while not mqtt_publisher_stop.wait(.02):
            i+=1;payload=dict(pressure=20+math.sin(i/10),load=10+i%7,counter=i,ts=time.time_ns())
            if os.environ.get('UI_SCRIPT')=='test_live_extensions_ui.cjs':payload['newfield'+str(i//100)]=i
            mqtt_peer.publish('plant/motor',payload,qos=1)
    mqtt_publisher=threading.Thread(target=publish_mqtt,daemon=True);mqtt_publisher.start()
if os.environ.get('UI_SCRIPT')=='test_ssh_ui.cjs':
    from ssh_peer import Peer as SSHPeer
    ssh_peer=SSHPeer();os.environ['TEST_SSH_PORT']=str(ssh_peer.port)
if os.environ.get('UI_SCRIPT')=='test_ads_ui.cjs':
    from ads_simulator import Peer
    ads_peer=client.runtime.run(Peer().start())
    os.environ['TEST_ADS_PORT']=str(ads_peer.port)
try:
    result=subprocess.run(['node',str(Path(__file__).with_name(os.environ.get('UI_SCRIPT','test_ui.cjs'))),
                           f'http://127.0.0.1:{http.server_port}',endpoint,root.nodeid.to_string(),
                           nodes[0].nodeid.to_string(),power.nodeid.to_string()],
                          env=os.environ,capture_output=True,text=True,timeout=90)
    if result.returncode:
        raise RuntimeError(result.stdout+'\n'+result.stderr)
    report=json.loads(result.stdout)
    for info in report['files']:
        path=Path(info['path'])
        if info['format']=='csv':
            with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        else:rows=pq.read_table(path).to_pylist()
        assert len(rows)==info['rows_written']
        if os.environ.get('UI_SCRIPT')!='test_calculations_ui.cjs':assert info['rows_written']==info['cycles_received']*2
        else:assert any(str(r['node_id']).startswith('calc:') for r in rows)
        assert len({r['value_numeric'] for r in rows if r['node_id']==nodes[0].nodeid.to_string()})>3
        info['path']=str(Path('Recordings')/path.name)
    print(json.dumps(report,indent=2))
except subprocess.TimeoutExpired as exc:
    print('UI TIMEOUT stdout:', exc.stdout, 'stderr:', exc.stderr, flush=True)
    raise
finally:
    task.cancel()
    client.runtime.run(client.disconnect_command())
    client.ssh_service.close()
    if ssh_peer:ssh_peer.close()
    if ads_peer:client.runtime.run(ads_peer.close())
    if mqtt_peer:
        mqtt_publisher_stop.set();mqtt_publisher.join(2);mqtt_peer.close()
    client.runtime.run(server.stop())
    http.shutdown();http.server_close()
