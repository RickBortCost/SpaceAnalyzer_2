"""Primary capture takes priority over the existing optional format writer.

All external-format I/O is behind a separate bounded pump. If that pump fails,
canonical capture continues and the failure is explicit. Primary loss instead
terminates the session in a controlled manner.
"""
import threading
import time
import json
import subprocess
import sys
from primary_format import atomic_json
from pathlib import Path
from recordings import Recorder
from primary_recording import PrimaryRecorder
from acquisition_buffers import ConnectionBuffer,size_hint

class DurableRecorder(Recorder):
    events=None
    def __init__(self,directory,version,queue_capacity=2000):
        super().__init__(directory,version,queue_capacity)
        self.external=Recorder(directory,version,queue_capacity);self.primary=None
        self.pump=None;self.pump_done=threading.Event();self.pump_done.set();self.stop_requested=False
        self.external_error='';self.external_lost=0;self.logical_cycles=0;self.logical_rows=0;self.rebuild_running=False;self.rebuilt_state=None;self.rebuild_failed='';self.primary_only=False;self.final_diagnostics={};self.accepted_batches=0;self.starting=False
    @property
    def acquiring(self):return self.starting or self.primary is not None and self.primary.accepting
    @property
    def busy(self):return self.acquiring or self.primary is not None and not self.primary.done.is_set() or not self.pump_done.is_set() or self.external.busy
    def start(self,fmt,nodes,endpoint='',sample_ms=500,options=None):
        options=dict(options or {});self.starting=True
        self.export_spec=dict(format=fmt,nodes=nodes,endpoint=endpoint,sample_ms=sample_ms,options=options)
        try:
            self.external.start(fmt,nodes,endpoint,sample_ms,options)
            self.primary=PrimaryRecorder(self.directory/'.primary',flush_ms=int(options.get('primary_flush_ms',250)),
                max_samples=int(options.get('primary_queue_samples',65536)),max_bytes=int(options.get('primary_queue_mb',32))*1024**2,events=self.events)
            self.primary.failure_callback=self._primary_failed
            self.primary.start(nodes,dict(application_version=self.version,selected_format=fmt,endpoint=endpoint,
                connections=options.get('primary_connections',{}),acquisition_info=options.get('acquisition_info',{}),calculation_formulas=options.get('calculation_formulas',[]),
                session={k:options.get(k,'') for k in ('session_name','machine','operator','description')}))
            self.buffer=ConnectionBuffer(max_samples=65536,max_bytes=32*1024**2)
            self.pump_done.clear();self.stop_requested=False
            self._event('recording_started',format=fmt,channels=len(nodes))
            self.pump=threading.Thread(target=self._pump,name='compatibility-recorder',daemon=False);self.pump.start()
        except BaseException:
            self.external.stop('primary_start_failed',5)
            if self.primary:self.primary.stop(5)
            raise
        finally:self.starting=False
        return self.status()
    def _event(self,kind,**details):
        if self.events:self.events.emit(kind,session_id=self.primary.identity if self.primary else None,**details)
    def _primary_failed(self,message):
        self.stop_requested=True
        self._event('recording_integrity_failure',error=str(message))
    def enqueue_primary(self,t,rows,sample_ms):
        self.primary_only=True
        return self.primary.enqueue(t,rows,sample_ms,backpressure_timeout=.5) if self.primary and self.primary.accepting else False
    def enqueue(self,t,rows,sample_ms):
        if not self.primary or not self.primary.accepting:return False
        if not self.primary.enqueue(t,rows,sample_ms,backpressure_timeout=.5):return False
        selected=sum(r.get('nodeid') in self.primary.channels for r in rows)
        if selected:self.logical_cycles+=1;self.logical_rows+=selected
        if self.external_error:self.external_lost+=selected;return True
        if not self.buffer.put((t,[dict(r) for r in rows],sample_ms),len(rows),rows[0].get('_buffer_budget') or size_hint(rows)):
            self.external_lost+=len(rows);self.external_error='Formato esterno in ritardo: originale primario conservato'
            self._event('external_writer_overflow',samples=len(rows),error=self.external_error)
        return True
    def _pump(self):
        try:
            while True:
                item=self.buffer.get()
                if item is None:
                    if self.stop_requested or self.primary.error:break
                    if self.external_error:self.external.fail(self.external_error)
                    time.sleep(.001);continue
                serial,(t,rows,ms),count,queued=item
                if self.external_error:self.external_lost+=count
                if not self.external_error:
                    if not self.external.enqueue(t,rows,ms):
                        self.external_lost+=count
                        self.external_error=self.external.status().get('error') or 'Formato esterno non disponibile; originale primario conservato'
                        self._event('external_writer_failure',error=self.external_error)
                self.buffer.done(serial)
            if self.primary.error:self.external.fail('Integrità registrazione primaria: '+self.primary.error)
            elif self.external_error:self.external.fail(self.external_error)
            self.external.stop(getattr(self,'stop_reason','user'),0)
            while self.external.busy:self.external.stop(getattr(self,'stop_reason','user'),.05)
            final_error=self.external.status().get('error')
            if final_error and not self.external_error:self.external_error=final_error;self._event('external_finalization_failed',error=final_error)
            if self.external_error and not self.primary.error:self._safe_rebuild()
        except BaseException as exc:
            self.external_error=str(exc);self.external.fail(str(exc));self.external.stop('external_error',0)
            self._event('external_writer_failure',error=str(exc))
            if self.primary and not self.primary.error:self._safe_rebuild()
        finally:
            self.pump_done.set()
            self._event('external_writer_closed',error=self.rebuild_failed or ('' if self.rebuilt_state else self.external_error),unrecovered_format_samples=0 if self.rebuilt_state else self.external_lost,rebuilt=bool(self.rebuilt_state))
    def _safe_rebuild(self):
        try:self._rebuild()
        except Exception as exc:
            self.rebuild_failed=str(exc);self._event('external_rebuild_failed',error=str(exc))
        finally:self.rebuild_running=False

    def _rebuild(self):
        # The durable primary is the spool. Never retain an unbounded format queue.
        self._event('external_rebuild_waiting',format=self.export_spec['format'],reason=self.external_error)
        self.primary.done.wait()
        state=self.primary.status()
        if self.primary.error or state.get('integrity')!='complete':return
        while self.external.busy:self.external.stop('deferred_export',.05)
        job=self.primary.path.with_name(self.primary.identity+'.export.json')
        spec={**self.export_spec,'path':state['path'],'directory':str(self.directory),
            'exclude_notifications':self.primary_only,'source_diagnostics':self.final_diagnostics}
        atomic_json(job,spec)
        self.rebuild_running=True
        self._event('external_rebuild_started',format=spec['format'],samples=state['durable_samples'])
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)
        process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('primary_tools.py')),'export',state['path'],
            '--format',spec['format'],'--output',str(self.directory),'--job',str(job),'--background'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=flags)
        output,error=process.communicate()
        if process.returncode:
            self.rebuild_failed=error.decode('utf-8',errors='replace')[-2048:] or 'Ricostruzione formato fallita'
            self._event('external_rebuild_failed',error=self.rebuild_failed);return
        rebuilt=json.loads(output)
        if rebuilt.get('error') or rebuilt.get('state')!='stopped':
            self.rebuild_failed=rebuilt.get('error') or 'Ricostruzione formato incompleta'
            self._event('external_rebuild_failed',error=self.rebuild_failed);return
        if rebuilt.get('rows_written')!=self.logical_rows:
            self.rebuild_failed='Conteggio ricostruzione differente dagli originali: '+str(rebuilt.get('rows_written'))+' / '+str(self.logical_rows)
            self._event('external_rebuild_failed',error=self.rebuild_failed);return
        self.rebuilt_state=rebuilt
        self._event('external_rebuild_finished',format=spec['format'],path=rebuilt['path'],rows=rebuilt['rows_written'])

    def fail(self,message,reason='source_acquisition_error'):
        if self.primary:self.primary.fail(message)
        self.external.fail(message,reason)
        self.stop_reason=reason;self.stop_requested=True
    def source_diagnostics(self,diagnostics):
        self.final_diagnostics=dict(diagnostics);self.external.source_diagnostics(diagnostics)
    def stop(self,reason='user',timeout=5):
        self.stop_reason=reason;self.stop_requested=True
        start=time.monotonic()
        if self.primary:self.primary.stop(0)
        if self.pump:self.pump.join(max(0,timeout))
        remaining=max(0,timeout-(time.monotonic()-start))
        if self.pump_done.is_set():self.external.stop(reason,remaining)
        if self.primary and remaining:self.primary.stop(max(0,timeout-(time.monotonic()-start)))
        return self.status()
    def status(self):
        result=dict(self.rebuilt_state) if self.rebuilt_state else self.external.status();p=self.primary.status() if self.primary else {}
        deferred=bool(self.external_error and not self.primary.error and not self.rebuilt_state and not self.rebuild_failed)
        if deferred:
            phase='converting' if self.rebuild_running else 'queued'
            result.update(cycles_received=self.logical_cycles,error='',conversion_phase=phase,status_note='')
            for key in ('fda_phase','rbf_phase','pda3_phase'):
                if key in result:result[key]='recording' if self.acquiring else phase
        result.update(primary=p,external_buffer=self.buffer.snapshot() if hasattr(self,'buffer') else {},
            primary_original=p.get('path',''),active=self.acquiring,busy=self.busy,
            external_error=self.rebuild_failed or ('' if deferred or self.rebuilt_state else self.external_error),external_warning=self.external_error,
            external_lost_samples=0 if self.rebuilt_state else self.external_lost,external_rebuilt_from_primary=bool(self.rebuilt_state),
            deferred_export_pending=deferred,deferred_export_error=self.rebuild_failed)
        if self.acquiring:result['state']='recording'
        elif self.busy:result['state']='stopping'
        if self.primary and self.primary.error:result.update(error=self.primary.error,state='stopping' if self.busy else 'error')
        elif self.rebuild_failed:result.update(error=self.rebuild_failed,state='error' if not self.busy else 'stopping')
        elif self.external_error and not deferred and not self.rebuilt_state:result['error']=self.external_error
        return result
    def __getattr__(self,name):
        external=self.__dict__.get('external')
        if external is not None:return getattr(external,name)
        raise AttributeError(name)
