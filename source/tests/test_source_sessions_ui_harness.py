"""Run a DOM workflow against the production Flask application and local peers."""
import json
import os
from pathlib import Path
import subprocess
import threading
from werkzeug.serving import make_server
from test_source_sessions import Sources, app


def main():
    gate=threading.Event()
    script=os.environ.get('UI_SCRIPT','test_source_sessions_ui.cjs')
    if script=='test_recording_overlap_ui.cjs':
        from recordings import Recorder
        from unittest.mock import patch
        real_run=Recorder._run
        def blocked_writer(rec):
            if rec.metadata.get('session_name')=='Slow previous':gate.wait(60)
            return real_run(rec)
        writer_patch=patch.object(Recorder,'_run',blocked_writer);writer_patch.start()
        @app.app.post('/test/finalizers/release')
        def release_finalizer():
            gate.set();return {'ok':True}
    else:writer_patch=None
    Sources.setUpClass()
    case=Sources('test_all_nonempty_protocol_subsets_and_catalogs')
    case.setUp()
    http=make_server('127.0.0.1',0,app.app,threaded=True)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    stop=threading.Event()
    def publish():
        i=0
        while not stop.wait(.02):
            i+=1
            case.mqtt.publish('plant/motor',dict(load=i,**{'field'+str(i//30):i}),qos=1)
    thread=threading.Thread(target=publish,daemon=True);thread.start()
    try:
        env={**os.environ,'TEST_MULTI_CONFIG':json.dumps(case.cfg),'TEST_MULTI_SIGNALS':json.dumps(case.ids)}
        script=os.environ.get('UI_SCRIPT','test_source_sessions_ui.cjs')
        command=['node',str(Path(__file__).with_name(script)),f'http://127.0.0.1:{http.server_port}',case.endpoint,case.root.nodeid.to_string(),case.ids['opcua']]
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=90)
        print(result.stdout,end='');print(result.stderr,end='')
        if result.returncode:raise SystemExit(result.returncode)
    finally:
        gate.set()
        if writer_patch:writer_patch.stop()
        stop.set();thread.join(3)
        http.shutdown();http.server_close()
        case.tearDown();Sources.tearDownClass()


if __name__=='__main__':main()
