"""Real local services + JSDOM page; canvas mocked, no Windows or PLC claim."""
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from werkzeug.serving import make_server
from test_s7_extended import app,Peer,free_udp_port,packet

if __name__=='__main__':
    app.initialize();peer=Peer();port=free_udp_port();stop=threading.Event()
    def send():
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender:
            seq=0;source_us=int(time.time()*1000000);deadline=time.monotonic()
            while not stop.is_set():
                sender.sendto(packet(seq&65535,source_us+seq*10000,a=math.sin(seq/10)),('127.0.0.1',port))
                seq+=1;deadline+=.01;stop.wait(max(0,deadline-time.monotonic()))
    sender=threading.Thread(target=send,daemon=True);sender.start()
    http=make_server('127.0.0.1',0,app.app,threaded=True)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        r=subprocess.run(['node',str(Path(__file__).with_name('test_s7_modes_ui.cjs')),
            f'http://127.0.0.1:{http.server_port}',str(peer.port),str(port)],env=os.environ,capture_output=True,text=True,timeout=120)
        print(r.stdout);print(r.stderr)
        if r.returncode:raise RuntimeError('S7 symbolic/UDP UI test failed')
    finally:
        stop.set();sender.join();app.runtime.run(app.disconnect_command());app.calculations.close()
        peer.stop();http.shutdown();http.server_close()
