"""Continuous original acquisition + serialized, isolated completed-window exports."""
from datetime import datetime,timezone
import json,logging,math,re,subprocess,sys,threading,time,uuid
from pathlib import Path
from .template_loader import template,status as template_status
from .storage import atomic_json

EXPORT_LOCK=threading.Lock()
RECOVERIES={}

def normal_short_stop(root,job):
    """Only finalized user/shutdown parts may explain a normal short last window."""
    if job.get('source_error'):return False
    for relative in job.get('sources',[]):
        path=(Path(root)/relative).resolve()
        if path.parent!=(Path(root)/'Parquet').resolve() or path.suffix!='.parquet':return False
        if path.with_name(path.name+'.part').exists():return False
        meta_path=path.with_suffix('.metadata.json')
        if not meta_path.is_file():continue
        meta=json.loads(meta_path.read_text('utf-8'))
        if meta.get('state')=='stopped' and not meta.get('error') and meta.get('reason') in ('user','shutdown','disconnect','application_close'):
            return True
    return False

def run_job(path):
    with EXPORT_LOCK:
        path=Path(path)
        result_path=path.with_name(path.name+'.result');result_path.unlink(missing_ok=True)
        path.with_name(path.name+'.progress.json').unlink(missing_ok=True)
        process=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'pda3_worker.py'),str(path)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if not result_path.is_file():raise ValueError('Processo PDA3 interrotto; job e originali conservati per il riavvio')
        result=json.loads(result_path.read_text('utf-8'))
        if process.returncode or not result.get('ok'):raise ValueError(result.get('error') or 'Errore processo PDA3')
        result_path.unlink(missing_ok=True)
        record=result['recording'];job=json.loads(path.read_text('utf-8'));job['state']=record['status'];job['last_error']=record.get('error','');atomic_json(path,job)
        logging.info('PDA3 window %s: %s, seconds=%s, dat_files=%s, note=%s, error=%s',path.name,record['status'],record.get('processing_seconds'),len(record.get('dat_files',[])) or (1 if record['status']=='ready' else 0),record.get('status_note',''),record.get('error',''))
        return record

def rebuild_pending(directory):
    """Recover the journal gap between a closed original and controller scheduling.

    Incomplete Parquet parts are kept and reported; never renamed or guessed valid.
    """
    root=Path(directory);groups={};partials=[]
    for meta_path in sorted((root/'Parquet').glob('*.metadata.json')):
        meta=json.loads(meta_path.read_text('utf-8'))
        if not meta.get('pda3_requested'):continue
        sid=meta.get('session_id','');index=meta.get('pda3_window_index');origin=meta.get('pda3_window_origin_ms')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,220}',sid):continue
        original=meta_path.with_name(meta_path.name.removesuffix('.metadata.json')+'.parquet')
        if original.with_name(original.name+'.part').is_file():partials.append(str(original.with_name(original.name+'.part')))
        if not original.is_file() or type(index) is not int or index<0 or not isinstance(origin,(int,float)) or not math.isfinite(origin):continue
        entry=groups.setdefault((sid,index),{'meta':meta,'sources':[],'origin':origin})
        if entry['origin']!=origin:raise ValueError('PDA3 recovery: origine temporale incoerente')
        entry['sources'].append(original.relative_to(root).as_posix())
    created=0
    for (sid,index),entry in sorted(groups.items()):
        meta=entry['meta'];start=entry['origin']+index*600000;calendar=datetime.fromtimestamp(start/1000,timezone.utc)
        identity=sid+f'_w{index+1:05d}';folder=root/'DAT'/calendar.strftime('%Y-%m-%d');path=folder/(identity+'.pda3job.json')
        if path.exists():continue
        folder.mkdir(parents=True,exist_ok=True)
        source_error=meta.get('error','')
        if any(Path(p).name.startswith(sid+'_p') for p in partials) and index==max(i for (s,i) in groups if s==sid):
            source_error=source_error or 'Riavvio con parte Parquet incompleta: nessun campione ricostruito'
        atomic_json(path,{'id':identity,'name':calendar.strftime('%Y%m%d_%H%M%S_')+identity+'.dat',
            'sources':entry['sources'],'nodes':meta['signals'],'start_ms':start,'tolerance_ms':meta.get('pda3_tolerance_ms',1),
            'state':'queued','source_error':source_error,'recovered_after_restart':True,
            'allow_partial':normal_short_stop(root,{'sources':entry['sources'],'source_error':source_error}),
            'auto_split':meta.get('pda3_auto_split',True),
            'resampling':meta.get('pda3_resampling'),
            'context_sources':groups.get((sid,index-1),{}).get('sources',[]) if meta.get('pda3_resampling') else [],
            'session':{k:meta.get(k,'') for k in ('session_name','machine','operator','description')}})
        created+=1
    return {'rebuilt_jobs':created,'partial_originals':partials}

def recover_pending(directory):
    key=str(Path(directory).resolve())
    if key in RECOVERIES:return RECOVERIES[key]
    state={'running':True,'recovered':0,'errors':[],'rebuilt_jobs':0,'partial_originals':[]};RECOVERIES[key]=state
    def run():
        try:
            state.update(rebuild_pending(directory))
            for path in sorted(Path(directory).glob('DAT/*/*.pda3job.json')):
                try:
                    job=json.loads(path.read_text('utf-8'))
                    # Upgrade old V1.18.0 short stops rejected only by the 60000
                    # minimum. Full validation still rejects real gaps/Bad data.
                    if job.get('state')=='skipped' and '60000' in job.get('last_error','') and normal_short_stop(directory,job):
                        job.update(state='queued',allow_partial=True,upgraded_short_stop=True);atomic_json(path,job)
                    elif job.get('state')=='skipped' and job.get('auto_split',True) and any(s in job.get('last_error','') for s in ('dati non comprimibili nel blocco','nessuna codifica lossless alla capacita')):
                        job.update(state='queued',auto_split=True,upgraded_capacity_split=True);atomic_json(path,job)
                    elif job.get('state')=='queued' and not job.get('allow_partial') and normal_short_stop(directory,job):
                        job['allow_partial']=True;atomic_json(path,job)
                    if job.get('state')!='queued':continue
                    run_job(path);state['recovered']+=1
                except Exception as exc:
                    state['errors'].append(path.name+': '+str(exc));logging.exception('PDA3 recovery failed')
        except Exception as exc:
            state['errors'].append(str(exc));logging.exception('PDA3 journal reconstruction failed')
        finally:state['running']=False
    threading.Thread(target=run,name='PDA3-Recovery',daemon=True).start();return state

class PDA3SegmentRecorder:
    def __init__(self,directory,version,capacity,recorder_factory):
        self.root=Path(directory);self.original=recorder_factory(self.root/'Parquet',version,capacity)
        self.lock=threading.RLock();self.worker=None;self.files=[];self.manifests=[];self.error='';self.phase='idle';self.completed=set();self.job=None;self.sequences=[];self.note=''

    @property
    def busy(self):return self.original.busy or self.worker is not None and self.worker.is_alive()

    def start(self,nodes,endpoint,sample_ms,options):
        options=dict(options or {})
        if sample_ms!=10:raise ValueError('PDA3: impostare acquisizione 10 ms; 5/2/1 ms non validati per questo modello')
        if not 1<=len(nodes)<=64:raise ValueError('PDA3: selezionare da 1 a 64 segnali')
        if not options.get('pda3_accept_template_time'):raise ValueError('PDA3: accettare la modalita sperimentale con orari reali nel manifest')
        if not template.cache_info().currsize:raise ValueError('PDA3: preparare il modello prima di avviare Record')
        tolerance=float(options.get('pda3_tolerance_ms',1))
        if not math.isfinite(tolerance) or not 0<=tolerance<5:raise ValueError('PDA3: tolleranza griglia tra 0 e meno di 5 ms')
        limit=float(options.get('max_file_mb',1024))
        if limit and limit*1024*1024<len(template().raw):raise ValueError('PDA3: limite file inferiore al modello (13,19 MiB); aumentarlo o scegliere 0')
        self.resampling=options.get('pda3_resampling')
        self.nodes=[dict(n) for n in nodes];self.tolerance=tolerance;self.auto_split=options.get('pda3_auto_split',True) is not False
        self.session={k:str(options.get(k) or '') for k in ('session_name','machine','operator','description')}
        original_options={**options,'parquet_profile':'fast','max_duration_s':600,'rotation_time_basis':'pda3_timestamp'}
        self.original.start('parquet',nodes,endpoint,sample_ms,original_options);self.phase='recording'
        self.worker=threading.Thread(target=self._run,name='PDA3-WindowController',daemon=False);self.worker.start();return self.status()

    def enqueue(self,*args):return self.original.enqueue(*args)

    def stop(self,reason='user',timeout=5):
        start=time.monotonic();self.original.stop(reason,timeout)
        if self.worker and self.worker is not threading.current_thread():self.worker.join(max(0,timeout-(time.monotonic()-start)))
        return self.status()

    def status(self):
        state=self.original.status()
        with self.lock:files=list(self.files);manifests=list(self.manifests);error=self.error;phase=self.phase;job=self.job;sequences=list(self.sequences);note=self.note
        export_progress={}
        if job:
            try:export_progress=json.loads(job.with_name(job.name+'.progress.json').read_text('utf-8'))
            except (OSError,ValueError):pass
        origin=state.get('window_origin_ms');last=state.get('last_timestamp_ms');window=state.get('window_index',0)
        progress=0 if origin is None or last is None else max(0,min(600000,last-origin-window*600000+10))
        busy=self.busy;active=state['state']=='recording'
        state.update(format='pda3',format_label='iba DAT PDA3 · fino a 10 minuti + originali',directory=str(self.root),
            storage={'format':'pda3','label':'iba DAT PDA3 + Parquet originali','original_archive_lossless':True,'lossless':False},
            original_files=state.get('files',[]),original_path=state['path'],files=files,manifests=manifests,
            state='recording' if active else 'stopping' if busy else 'error' if error or state['error'] else 'stopped',
            active=active,busy=busy,error=error or state['error'],pda3_phase=phase,conversion_phase=phase,
            pda3={'template_ready':True,'selected_channels':len(getattr(self,'nodes',[])),'sample_ms':10,'segment_seconds':600,
                'window':window+1,'progress_samples':round(progress/10),'progress_percent':round(progress/6000,2),
                'export_job':str(job or ''),'completed_dat_files':len(files),'processed_windows':len(manifests),
                'last_dat_path':str(self.root/files[-1]['name']) if files else '',
                'export_progress':export_progress,'sequences':sequences,'auto_split':getattr(self,'auto_split',True),
                'timestamp_mode':'Sperimentale: calendario interno del modello; orari reali nel manifest'},
            status_note=note or 'DAT fino a 600 s; Stop salva anche segmenti brevi. Se necessario, suddivisione lossless automatica in file da 10 s.')
        if not busy and files and not state['error']:
            final_name=sequences[-1] if sequences else files[-1]['name']
            state.update(path=str(self.root/final_name),filename=Path(final_name).name,bytes_written=sum(f['bytes'] for f in files))
        return state

    def _run(self):
        groups={};seen=set();last_dispatched=-1
        try:
            while True:
                state=self.original.status();origin=state.get('window_origin_ms');active=self.original.busy
                for part in state.get('files',[]):
                    if part['name'] in seen:continue
                    seen.add(part['name']);groups.setdefault(part.get('window_index',0),[]).append('Parquet/'+part['name'])
                ready=[i for i in sorted(groups) if i not in self.completed and (not active or i<state.get('window_index',0))]
                for index in ready:
                    if origin is None:continue
                    start=origin+index*600000;calendar=datetime.fromtimestamp(start/1000,timezone.utc)
                    folder=self.root/'DAT'/calendar.strftime('%Y-%m-%d');folder.mkdir(parents=True,exist_ok=True)
                    identity=self.original.session_base+f'_w{index+1:05d}'
                    name=calendar.strftime('%Y%m%d_%H%M%S_')+identity+'.dat';path=folder/(identity+'.pda3job.json')
                    job={'id':identity,'name':name,'sources':groups[index],'nodes':self.nodes,'start_ms':start,'tolerance_ms':self.tolerance,
                        'missing_whole_windows_before':max(0,index-last_dispatched-1),
                        'allow_partial':not active and index==state.get('window_index',0),
                        'auto_split':self.auto_split,'resampling':self.resampling,
                        'context_sources':groups.get(index-1,[]) if self.resampling else [],
                        'session':self.session,'state':'queued','source_error':state['error'] if not active else ''}
                    atomic_json(path,job)
                    with self.lock:self.job=path;self.phase='converting'
                    try:record=run_job(path)
                    except Exception as exc:
                        with self.lock:self.error=str(exc)+'; job accodato per recupero al riavvio';self.phase='error';self.job=None
                        logging.exception('PDA3 export failed; original acquisition continues')
                        self.completed.add(index);last_dispatched=index
                        continue
                    with self.lock:
                        self.manifests.append(str(path.parent/name).removesuffix('.dat')+'.json')
                        if record['status']=='ready':
                            self.files.extend(record.get('dat_files') or [{'name':str((path.parent/name).relative_to(self.root)).replace('\\','/'),'format':'dat','bytes':record['size_bytes'],'rows':record['expected_samples_per_channel']*len(self.nodes),'duration_seconds':record['duration_seconds']}])
                            if record.get('sequence_name'):self.sequences.append(record['sequence_name'])
                            if record.get('status_note'):self.note=record['status_note']
                        else:self.error='Finestra PDA3 non esportata: '+record.get('error','consultare il manifest')
                        if index>last_dispatched+1:self.error='Gap temporale tra finestre; originali conservati. '+self.error
                        self.phase='recording' if active else 'ready';self.job=None
                    self.completed.add(index);last_dispatched=index
                if not active:
                    with self.lock:self.phase='error' if self.error else 'ready'
                    break
                threading.Event().wait(.2)
        except Exception as exc:
            with self.lock:self.error=str(exc)+'; originali e job conservati per il riavvio';self.phase='error'
            logging.exception('PDA3 controller failure')
