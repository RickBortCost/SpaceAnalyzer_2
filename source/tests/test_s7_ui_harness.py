"""Real Flask + native Snap7 simulator; DOM workflow, not Windows/pixel QA."""
import os
from pathlib import Path
import subprocess
import threading
from werkzeug.serving import make_server
from test_s7 import app, start_server

if __name__=='__main__':
    app.initialize();peer,port=start_server()
    http=make_server('127.0.0.1',0,app.app,threaded=True)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        result=subprocess.run(['node',str(Path(__file__).with_name('test_s7_ui.cjs')),
                               f'http://127.0.0.1:{http.server_port}',str(port)],
                              env=os.environ,capture_output=True,text=True,timeout=90)
        print(result.stdout);print(result.stderr)
        if result.returncode:raise RuntimeError('S7 UI test failed')
    finally:
        app.runtime.run(app.disconnect_command());app.calculations.close()
        peer.stop();http.shutdown();http.server_close()
