"""Background startup recovery and bounded diagnostic catalog. No legacy rewrites."""
from pathlib import Path
import threading
import json
import subprocess
import sys
from primary_format import recover


class RecoveryService:
    def __init__(self,directory,events):
        self.directory=Path(directory);self.events=events;self.lock=threading.Lock()
        self.results=[];self.running=False;self.thread=None
    def start(self):
        with self.lock:
            if self.running:return False
            self.running=True
            self.thread=threading.Thread(target=self._run,name='primary-recovery',daemon=True);self.thread.start()
        return True
    def _run(self):
        try:
            for path in sorted(self.directory.glob('*.sac.part')):
                try:
                    child=subprocess.run([sys.executable,str(Path(__file__).with_name('primary_tools.py')),'recover',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if child.returncode:raise RuntimeError(child.stderr.decode('utf-8',errors='replace')[-2048:])
                    result=json.loads(child.stdout)
                    if result.get('recovered'):self.events.emit('primary_recovered',**result)
                except Exception as exc:
                    result=dict(source=str(path),recovered=False,integrity='unrecoverable_header_or_io_error',error=str(exc))
                    self.events.emit('primary_recovery_failed',**result)
                with self.lock:self.results=(self.results+[result])[-256:]
        finally:
            with self.lock:self.running=False
    def status(self):
        with self.lock:return dict(running=self.running,results=list(self.results))
    def catalog(self,limit=100):
        # Metadata only: never scan sample blocks on an HTTP status request.
        paths=sorted(self.directory.glob('*.sac*'),key=lambda p:p.stat().st_mtime,reverse=True)
        return [dict(filename=p.name,bytes=p.stat().st_size,incomplete=p.name.endswith('.part')) for p in paths if p.name.endswith(('.sac','.part'))][:limit]
