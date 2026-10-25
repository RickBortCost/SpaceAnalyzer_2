"""Exercise the actual standalone Windows EXE through its local HTTP service.

The optional --app mode validates this harness against a development build of the
C# service (any OS); it does not claim a Windows executable test. All peers and
data are local fixtures.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'source/tests'))
from mqtt_peer import Peer


class TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'meta' and values.get('name') == 'opcua-token':
            self.token = values.get('content')


def until(predicate, timeout=90):
    end = time.monotonic()+timeout
    last = None
    while time.monotonic()<end:
        try:
            result=predicate()
            if result:return result
        except (OSError, ValueError) as exc:
            last=exc
        time.sleep(.1)
    raise RuntimeError('Timeout during executable verification'+(': '+str(last) if last else ''))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    launch=parser.add_mutually_exclusive_group(required=True)
    launch.add_argument('--exe',type=Path)
    launch.add_argument('--app',type=Path,help='SpaceAnalyzer service executable (or .dll run through dotnet)')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    packaged=args.exe is not None
    if packaged:
        if sys.platform!='win32':raise RuntimeError('The EXE must be executed on Windows')
        executable=args.exe.resolve()
        if executable.read_bytes()[:2]!=b'MZ':raise RuntimeError('Not a Windows PE executable')
        command=[str(executable)]
    else:
        app=args.app.absolute()
        command=[os.environ.get('DOTNET','dotnet'),str(app)] if app.suffix=='.dll' else [str(app)]
    build=json.loads((args.exe.parent/'BUILD_MANIFEST.json').read_text('utf-8')) if packaged else {'version':'3.0.4'}
    report={'version':build['version'],'tested_at':datetime.now(timezone.utc).isoformat(),
            'platform':sys.platform,'packaged_exe_executed':packaged,'formats':{},
            'hardware_tested':False,'result':'FAIL'}
    if packaged:
        actual=hashlib.sha256(executable.read_bytes()).hexdigest()
        if actual!=build['sha256']:raise RuntimeError('EXE differs from the build manifest')
        if not build.get('embedded_payload_verified'):raise RuntimeError('Embedded payload was not verified')
        report.update(exe=executable.name,sha256=actual,embedded_payload_verified=build.get('embedded_payload_verified',False))
    peer=Peer()
    process=None
    opener=build_opener(ProxyHandler({}))
    try:
        with tempfile.TemporaryDirectory(prefix='SpaceAnalyzer-EXE-test-',ignore_cleanup_errors=True) as directory:
            data=Path(directory)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            env={**os.environ,'OPCUA_DATA_DIR':str(data/'settings'),
                'OPCUA_APP_DIR':str(data/'application'),'OPCUA_NO_BROWSER':'1','OPCUA_WEB_PORT':str(port)}
            if packaged:
                # The child receives no installed Python/.NET/Go path: the service and
                # its self-contained .NET runtime must come from the embedded ZIP.
                env['PATH']=os.pathsep.join([str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']])
                for key in ('PYTHONPATH','PYTHONHOME','DOTNET_ROOT','DOTNET_ROOT_X64'):env.pop(key,None)
            else:
                env['SPACE_BASE_DIR']=str(ROOT/'source')
            with (data/'test-process.log').open('wb') as log:
                process=subprocess.Popen(command,env=env,stdout=log,stderr=log)
                instance=data/'settings/instance.json'
                def ready():
                    if process.poll() is not None:
                        raise RuntimeError('Service exited during startup: '+(data/'test-process.log').read_text(errors='replace'))
                    return json.loads(instance.read_text()) if instance.is_file() else None
                info=until(ready)
                base='http://127.0.0.1:'+str(info['port'])
                with opener.open(base,timeout=10) as response:html=response.read().decode('utf-8')
                token=TokenParser();token.feed(html)
                if not token.token:raise RuntimeError('Missing local API token')
                def api(path,body=None):
                    request=Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json','X-OPCUA-Token':token.token})
                    try:
                        with opener.open(request,timeout=35) as response:return json.load(response)
                    except HTTPError as exc:raise RuntimeError(path+': '+exc.read().decode()) from exc
                status=api('/api/status')
                if status['version']!=build['version']:raise RuntimeError('The running service has an older version')
                for protocol in ('opcua','ads','mqtt','s7'):
                    if 'id="enable-'+protocol+'"' not in html:raise RuntimeError('Missing protocol control '+protocol)
                if html.count('name="record-format"')!=4:raise RuntimeError('The bundled UI does not offer the four 3.0 recording formats')
                if 'id="global-live-zoom"' not in html:raise RuntimeError('The bundled UI is missing the Zoom live control')
                for control in ('quick-bindings','quick-expression','quick-run','quick-save'):
                    if 'id="'+control+'"' not in html:raise RuntimeError('Missing interval formula control '+control)
                report['interval_formula_controls_bundled']=True
                report['live_zoom_control_bundled']=True
                report['http_startup_verified']=True
                node='mqtt:smoke/value#'
                api('/api/select',{'nodeids':[node]})
                api('/api/connect',{'source':'mqtt','enabled_sources':['opcua','ads','mqtt','s7'],
                    'source_intervals':{'opcua':20,'ads':20,'mqtt':20,'s7':20},'sample_ms':20,
                    'security':'None','security_mode':'None','endpoint':'opc.tcp://127.0.0.1:1',
                    'ads_host':'127.0.0.1','ads_tcp_port':1,'s7_host':'127.0.0.1','s7_tcp_port':1,
                    'mqtt_host':'127.0.0.1','mqtt_port':peer.port,'mqtt_topics':'smoke/#',
                    'auto_reconnect':False,'buffer_mode':'time','window_seconds':10})
                until(lambda:api('/api/values')['sources'].get('mqtt',{}).get('state')=='connected')
                for _ in range(5):peer.publish('smoke/value',1.5,qos=1);time.sleep(.02)
                until(lambda:api('/api/values')['samples'].get(node))
                originals=api('/api/values')['samples'][node];x1=originals[0][0];x2=originals[-1][0]
                sessions=api('/api/values')['sources']
                if set(sessions)!={'opcua','ads','mqtt','s7'}:raise RuntimeError('Missing independent source sessions')
                report['four_sessions_and_isolated_connection_errors_verified']=True
                for fmt in ('csv','parquet','das','rbf'):
                    api('/api/record/start',{'format':fmt,'selected_nodes':[{'nodeid':node,'display_name':'Smoke value'}],
                        'options':{'buffer_seconds':30}})
                    for i in range(30):peer.publish('smoke/value',float(i)+.25,qos=1);time.sleep(.01)
                    until(lambda:api('/api/record/status')['recording']['cycles_received']>=30)
                    api('/api/record/stop',{})
                    until(lambda:not api('/api/record/status')['recording']['busy'])
                    recording=api('/api/record/status')['recording']
                    if recording['state']!='stopped' or recording['error']:raise RuntimeError(str(recording))
                    replay=api('/api/recordings/data',{'source':'repository','name':recording['files'][0]['name'],'max_points':200})['recording']
                    if replay['numeric_rows']<1:raise RuntimeError(fmt+': no numeric samples in replay')
                    health=api('/api/acquisition/health')
                    primary=recording.get('primary',{})
                    if primary.get('integrity')!='complete' or primary.get('durable_samples')!=recording['rows_written']:
                        raise RuntimeError('Primary integrity differs from selected format: '+str(primary))
                    # The capture is verified with the packaged service's own offline reader.
                    tool=subprocess.run(command+['--tool','inspect',primary['path']],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
                    if tool.returncode:raise RuntimeError('Primary inspection failed: '+tool.stderr)
                    inspected=json.loads(tool.stdout)
                    if inspected['error'] or inspected['samples']!=30:raise RuntimeError('Primary packaged-reader verification failed')
                    if inspected['source_values']!=[i+.25 for i in range(30)]:raise RuntimeError('Primary original sequence differs')
                    if health['events']['persistence_error']:raise RuntimeError('Critical events persistence failed')
                    report['primary_capture_and_original_sequence_verified']=True
                    report['formats'][fmt]={'result':'PASS','rows_recorded':recording['rows_written'],'numeric_rows_replayed':replay['numeric_rows']}
                for fmt in ('pda3','dat'):
                    rejected=False
                    try:api('/api/record/start',{'format':fmt,'selected_nodes':[{'nodeid':node,'display_name':'Smoke value'}]})
                    except Exception:rejected=True
                    if not rejected:raise RuntimeError(fmt+': DAT recording must be rejected in 3.0')
                report['dat_recording_removed_verified']=True
                # Force the first interval beyond the configured raw FIFO. This
                # executes SQLite and the shared formula engine inside the EXE.
                def old_interval_evicted():
                    peer.publish('smoke/value',9.,qos=1)
                    return api('/api/values?view=window')['samples'][node][0][0]>x2
                until(old_interval_evicted,timeout=25)
                full=api('/api/values')['samples'][node]
                snap=api('/api/analysis/live-snapshot',{'signal_ids':[node],'x1':full[0][0],'x2':full[-1][0]})['snapshot_id']
                body={'source':'live','snapshot_id':snap,'signal_ids':[node],'x1':x1,'x2':x2}
                marker=api('/api/analysis/markers',body)['results'][0]
                if marker['x1']['value']!=1.5 or marker['x2']['value']!=1.5:raise RuntimeError('Original full-session markers differ')
                formula={'expression':'[A]*2','bindings':{'A':{'nodeid':node}}}
                stats=api('/api/analysis/quick',{**body,'formula':formula})['results'][0]
                if stats['count']<1 or abs(stats['statistics']['mean']-3)>1e-9:raise RuntimeError('Original interval formula differs')
                api('/api/analysis/live-snapshot/release',{'snapshot_id':snap})
                report['original_full_session_markers_verified']=True
                report['interval_formula_after_fifo_eviction_verified']=True
                # A substantial FDA conversion must remain independent from
                # the next acquisition inside the actual packaged executable.
                batch_ids=['mqtt:smoke/batch#/v'+str(i) for i in range(64)]
                api('/api/select',{'nodeids':batch_ids})
                batch_nodes=[{'nodeid':n,'display_name':n} for n in batch_ids]
                first=api('/api/record/start',{'format':'das','selected_nodes':batch_nodes,'options':{'session_name':'Overlap FDA','buffer_seconds':120}})['recording']
                for index in range(2000):peer.publish('smoke/batch',{'v'+str(i):float(index+i) for i in range(64)},qos=1)
                until(lambda:api('/api/record/status')['recording']['cycles_received']>=2000)
                stopped=api('/api/record/stop',{'recording_id':first['recording_id']})['recording']
                if stopped['selection_locked'] or not stopped['can_start']:raise RuntimeError('Previous save blocks the next recording')
                if not stopped['finalizing_count']:raise RuntimeError('Overlap fixture finalized too early to verify concurrency')
                second=api('/api/record/start',{'format':'csv','selected_nodes':batch_nodes,'options':{'session_name':'Overlap next','buffer_seconds':120}})['recording']
                if second['recording_id']==first['recording_id']:raise RuntimeError('Recording session identity reused')
                if not second['finalizing_count']:raise RuntimeError('Previous save did not overlap the new acquisition')
                for index in range(10):peer.publish('smoke/batch',{'v'+str(i):float(9000+index+i) for i in range(64)},qos=1)
                until(lambda:api('/api/record/status')['recording']['cycles_received']>=10)
                api('/api/record/stop',{'recording_id':second['recording_id']})
                until(lambda:not api('/api/record/status')['recording']['busy'],timeout=90)
                jobs=api('/api/record/status')['recording']['finalizations']
                old=next(j for j in jobs if j['recording_id']==first['recording_id']);new=next(j for j in jobs if j['recording_id']==second['recording_id'])
                if old['state']!='stopped' or new['state']!='stopped':raise RuntimeError('An overlapped recording failed')
                if old['rows_written']!=128000 or new['rows_written']!=640:raise RuntimeError('Samples crossed recording boundaries or were lost')
                for job in (old,new):
                    replay=api('/api/recordings/data',{'source':'repository','name':job['files'][0]['name'],'max_points':1000})['recording']
                    if set(replay['series'])!=set(batch_ids) or replay['numeric_rows']!=job['rows_written']:raise RuntimeError('Overlapped replay differs from its original acquisition')
                for job in (old,new):
                    if job['primary']['integrity']!='complete' or job['primary']['durable_samples']!=job['rows_written']:raise RuntimeError('Overlapping primary samples differ')
                report['overlap_acquisition_health']=api('/api/acquisition/health')['acquisition']
                report['overlapping_fda_save_and_new_csv_recording_verified']=True
                report['overlapping_recordings_rows']={'fda':old['rows_written'],'csv':new['rows_written']}
                api('/api/disconnect',{})
                duplicate=subprocess.run(command,env=env,stdout=log,stderr=log,timeout=90)
                if duplicate.returncode!=0:raise RuntimeError('Second launch did not reopen the existing service')
                if api('/api/status')['version']!=build['version']:raise RuntimeError('Existing instance unavailable')
                report['single_instance_verified']=True
                api('/api/shutdown',{})
                process.wait(timeout=45)
                if process.returncode!=0:raise RuntimeError('Service/launcher shutdown failed')
                if list((data/'settings/live_analysis_cache').rglob('*.sqlite')):raise RuntimeError('Temporary original history was not cleaned on shutdown')
                report.update(shutdown_verified=True,temporary_history_cleanup_verified=True,result='PASS')
    except Exception as exc:
        report['error']=str(exc)
        try:report['failure_health']=api('/api/acquisition/health')
        except Exception as health_error:report['health_error']=str(health_error)
        raise
    finally:
        if process is not None and process.poll() is None:
            if sys.platform=='win32':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
            else:process.terminate()
            process.wait(timeout=15)
        peer.close()
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
