"""Shared lifecycle: original acquisition first, compatibility export after Stop."""
import json
import subprocess
import sys
import tempfile
import logging
import threading
import time
from pathlib import Path


EXPORT_SLOT=threading.BoundedSemaphore(1)


class ConvertedRecording:
    def __init__(self,directory,version,capacity,recorder_factory,*,exporter,fmt,title,suffix,phase_key):
        self.exporter=exporter;self.fmt=fmt;self.title=title;self.suffix=suffix;self.phase_key=phase_key
        self.original=recorder_factory(directory,version,capacity)
        self.lock=threading.RLock();self.worker=None;self.files=[];self.error='';self.phase='idle'
        self.directory=Path(directory)

    @property
    def busy(self):
        return self.original.busy or self.worker is not None and self.worker.is_alive()

    def start(self,nodes,endpoint,sample_ms,options):
        self.nodes=[dict(n) for n in nodes];self.options=dict(options or {})
        self.options['parquet_profile']='fast'
        if not options:self.options['_queue_capacity_cycles']=self.original.capacity
        self.original.start('parquet',nodes,endpoint,sample_ms,self.options or None)
        self.phase='recording'
        self.worker=threading.Thread(target=self._run,name=self.title+'-Finalizer',daemon=False)
        try:self.worker.start()
        except Exception:
            self.original.stop('fda_start_error',10)
            raise
        return self.status()

    def enqueue(self,*args):return self.original.enqueue(*args)

    def stop(self,reason='user',timeout=5):
        start=time.monotonic();self.original.stop(reason,timeout)
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(max(0,timeout-(time.monotonic()-start)))
        return self.status()

    def status(self):
        state=self.original.status()
        with self.lock:
            files=list(self.files);error=self.error;phase=self.phase
        active=self.original.busy
        busy=active or self.worker is not None and self.worker.is_alive()
        original=state['path'];filename=files[-1]['name'] if files else ''
        state.update(format=self.fmt,format_label=self.title+' + Parquet originali',
                     storage={'format':self.fmt,'label':self.title+' + Parquet originali','lossless':False,'original_archive_lossless':True},
                     original_files=state['files'],original_path=original,files=files,
                     filename=filename or Path(original).stem+self.suffix,
                     path=str(self.directory/filename) if filename else original,
                     state='recording' if state['state']=='recording' else 'stopping' if busy else 'error' if error or state['error'] else 'stopped',
                     busy=busy,active=state['state']=='recording',error=error or state['error'],conversion_phase=phase,
                     status_note='Originali in acquisizione; parti chiuse convertite in background.' if phase=='recording' and state['state']=='recording'
                     else 'Salvataggio originali in background.' if phase=='recording'
                     else 'Generazione '+self.title+' in corso; archivio originale gia salvato.' if phase=='converting'
                     else self.title+' generato; conservare anche archivio Parquet e metadati.' if phase=='ready' else '')
        state[self.phase_key]=phase
        return state

    def _export(self,path):
        if not self.options.get('conversion_isolated') or self.fmt not in ('das','rbf'):
            return self.exporter(path,self.nodes,self.original.max_bytes)
        with tempfile.TemporaryDirectory(prefix='space-convert-') as folder:
            job=Path(folder)/'job.json';answer=Path(folder)/'answer.json'
            job.write_text(json.dumps(dict(format=self.fmt,path=str(path),nodes=self.nodes,
                limit=self.original.max_bytes,answer=str(answer)),ensure_ascii=False),encoding='utf-8')
            process=subprocess.run([sys.executable,str(Path(__file__).with_name('conversion_worker.py')),str(job)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if not answer.is_file():raise ValueError('Processo conversione interrotto; originali conservati')
            result=json.loads(answer.read_text('utf-8'))
            if process.returncode or not result.get('ok'):raise ValueError(result.get('error') or 'Errore conversione')
            return result['files']

    def _run(self):
        seen=set()
        try:
            # Convert rotated, finalized parts during acquisition. Never read a
            # .part or publish a file before its own footer/metadata is complete.
            while True:
                state=self.original.status()
                for part in state['files']:
                    if part['name'] in seen:continue
                    with self.lock:self.phase='queued'
                    with EXPORT_SLOT:
                        with self.lock:self.phase='converting'
                        result=self._export(self.directory/part['name'])
                    with self.lock:self.files.extend(result)
                    seen.add(part['name'])
                if not self.original.busy:
                    if state['state']=='error':raise ValueError('Acquisizione originale incompleta: '+state['error']+'; file parziale conservato')
                    # Re-read after busy changed: the writer may have finalized
                    # its last part between the previous status and busy reads.
                    final=self.original.status()
                    if any(p['name'] not in seen for p in final['files']):continue
                    break
                with self.lock:self.phase='recording'
                threading.Event().wait(.05)
            with self.lock:self.phase='ready'
            logging.info('%s conversion completed: files=%d original_rows=%d',self.title,len(self.files),state['rows_written'])
        except Exception as exc:
            with self.lock:self.error='Errore generazione '+self.title+': '+str(exc)+'; archivio Parquet originale conservato';self.phase='error'
            logging.exception('%s conversion failure',self.title)
