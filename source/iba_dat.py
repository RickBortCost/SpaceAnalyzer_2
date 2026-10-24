"""Native ibaFiles 3.2.0 export. Originals remain lossless companion Parquet files.

No license emulation: only a successful native roundtrip enables recording.
The legacy DAT axis is nominal/Float32; replay and FFT use the original archive.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import struct
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

DLL_HASH='63c86505734e56aaac6eb0e845c9a0bad16e55c12abd7922754157fdcb12469b'
VIEW_NOTE='Grafici, marker, FFT e calcoli leggono il Parquet originale associato, limitato a questa parte DAT; non i valori Float32 del DAT.'

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def write_json(path,data):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,allow_nan=False,indent=2)
    temporary.replace(path)

class IBABridge:
    def __init__(self):
        self.root=Path(__file__).resolve().parent/'iba_bridge'
        self.lock=threading.RLock();self.last={};self.checked=0;self.report_path=None

    def _invoke(self,args,timeout):
        if os.name!='nt':raise ValueError('iba DAT richiede Windows e una licenza di scrittura riconosciuta dalla DLL 3.2.0')
        if digest(self.root/'ibaFiles.dll')!=DLL_HASH:raise ValueError('ibaFiles.dll diversa dalla versione 3.2.0 analizzata')
        executable=self.root/'SpaceAnalyzerIbaBridge.exe'
        if not executable.is_file():raise ValueError('Bridge iba DAT mancante: estrarre lo ZIP completo')
        # Spool output so a malfunctioning DLL cannot grow a Python pipe without bounds.
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as error:
            try:process=subprocess.run([str(executable),*map(str,args)],cwd=self.root,stdout=output,stderr=error,
                timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired:raise ValueError('Timeout nel processo ibaFiles: verificare DLL, driver e licenza') from None
            if output.tell()>1024*1024:raise ValueError('Risposta bridge troppo grande')
            output.seek(0)
            try:result=json.loads(output.read().decode('utf-8'))
            except (ValueError,UnicodeError):raise ValueError('Il processo DLL non ha restituito un risultato valido') from None
        if not isinstance(result,dict):raise ValueError('Risposta DLL non valida')
        if process.returncode and result.get('ok'):raise ValueError('Processo DLL terminato con errore')
        return result

    def check(self,force=False):
        with self.lock:
            if not force and self.last.get('ok') and time.monotonic()-self.checked<15:return dict(self.last)
            try:
                with tempfile.TemporaryDirectory(prefix='SpaceAnalyzer_iba_check_') as directory:
                    raw=self._invoke(['--mode','check','--out',directory],30)
                ok=(raw.get('ok') is True and raw.get('native_windows_test') is True and raw.get('licensed') is True
                    and raw.get('samples_written')==512 and raw.get('samples_read')==512
                    and raw.get('float32_exact') is True and raw.get('timebase_matches') is True)
                result={'ok':ok,'native':raw.get('native_windows_test') is True,'simulated':False,
                    'licensed':raw.get('licensed') is True,'version':raw.get('writer_version',''),
                    'roundtrip_verified':ok,'sample_count':raw.get('samples_read',0),
                    'error':'' if ok else raw.get('error') or 'Verifica nativa di scrittura/rilettura non superata'}
            except (OSError,ValueError) as exc:result={'ok':False,'native':False,'simulated':False,'licensed':False,'error':str(exc)}
            self.checked=time.monotonic();result['checked_at']=datetime.now(timezone.utc).isoformat()
            self.last=result
            if self.report_path:
                try:
                    self.report_path.parent.mkdir(parents=True,exist_ok=True)
                    write_json(self.report_path,result)
                except OSError:pass
            return dict(result)

    def status(self):
        with self.lock:return dict(self.last) or {'ok':False,'error':'Verifica DLL non ancora eseguita','licensed':False}

    def require(self):
        result=self.check()
        if not(result.get('ok') is True and result.get('native') is True and result.get('licensed') is True and result.get('simulated') is False):
            raise ValueError(result.get('error') or 'Verifica licenza/DLL non superata')
        return result

    def require_prepared(self):
        # Acquisition thread must never execute or wait for native DLL calls.
        # last is replaced atomically (never mutated), so no wait on a DLL-held lock.
        r=self.last;fresh=time.monotonic()-self.checked<60
        if not(fresh and r.get('ok') is True and r.get('native') is True and r.get('licensed') is True and r.get('simulated') is False):
            raise ValueError('Verificare prima la DLL/licenza dal popup Record e riprovare')

    def write(self,job,path):
        with self.lock:
            result=self._invoke(['--mode','export','--input',job,'--out',path],900)
        if not(result.get('ok') is True and result.get('native') is True and result.get('licensed') is True
               and result.get('simulated') is False and result.get('roundtrip_verified') is True):
            raise ValueError(result.get('error') or 'Verifica nativa del DAT non superata')
        if not Path(path).is_file() or Path(path).stat().st_size==0:raise ValueError('DAT assente o vuoto')
        return result

bridge=IBABridge()

def _float32(value):
    try:raw=struct.pack('<f',value)
    except (ValueError,OverflowError,struct.error):raise ValueError('Valore fuori scala Float32: DAT non generato, originale conservato') from None
    if not math.isfinite(struct.unpack('<f',raw)[0]):raise ValueError('Valore DAT non finito')
    return raw

def _key(row):
    return [row[0],row[1],row[2]]

def export_parquet(path,nodes,max_bytes=0,*,native_bridge=None):
    """Bounded-memory chronological staging; no resampling, loss or timestamp deduplication."""
    from recording_reader import RecordingLibrary
    path=Path(path).resolve();native=native_bridge or bridge
    ids=list(dict.fromkeys(str(n['nodeid']) for n in nodes));definitions={str(n['nodeid']):n for n in nodes}
    if not 1<=len(ids)<=64:raise ValueError('DAT: selezionare da 1 a 64 variabili (3 canali per variabile)')
    original_meta=path.with_suffix('.metadata.json')
    metadata=json.loads(original_meta.read_text('utf-8')) if original_meta.is_file() else {}
    sha=digest(path);limit=min(int(max_bytes) or 128*1024*1024,128*1024*1024)
    files=[];serial=0
    with tempfile.TemporaryDirectory(prefix='.iba_export_',dir=path.parent) as directory:
        temporary=Path(directory);db=sqlite3.connect(temporary/'samples.sqlite')
        try:
            db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA cache_size=-8192');db.execute('PRAGMA temp_store=FILE')
            db.execute('CREATE TABLE input(t REAL,c INTEGER,original_id INTEGER,n TEXT,v REAL,q INTEGER)')
            batch=[]
            for ordinal,row in enumerate(RecordingLibrary(path.parent)._iter_parquet(path)):
                nid=str(row.get('node_id') or '')
                if nid not in definitions:continue
                t=float(row['timestamp_ms'])
                if not math.isfinite(t):raise ValueError('Timestamp originale non finito')
                try:v=float(row['value_numeric']);valid=math.isfinite(v)
                except (ValueError,TypeError,OverflowError):v=0.;valid=False
                quality=str(row.get('quality') or ('Good' if row.get('good') else 'Bad'))
                q=3 if quality=='Good' else 2 if quality=='Uncertain' else 1
                batch.append((t,int(row.get('cycle') or 0),ordinal,nid,v if valid else 0.,q if valid or quality!='Good' else 0))
                if len(batch)>=8192:db.executemany('INSERT INTO input VALUES(?,?,?,?,?,?)',batch);batch=[]
            if batch:db.executemany('INSERT INTO input VALUES(?,?,?,?,?,?)',batch)
            db.execute('CREATE TABLE samples(t REAL,c INTEGER,original_id INTEGER,n TEXT,v REAL,q INTEGER)')
            db.execute('INSERT INTO samples SELECT * FROM input ORDER BY t,c,original_id')
            db.execute('DROP TABLE input');db.execute('CREATE INDEX by_node ON samples(n)');db.commit()
            total=db.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
            if not total:raise ValueError('Nessun campione da esportare in DAT')

            def divide(lo,hi):
                middle=(lo+hi)//2
                t,c=db.execute('SELECT t,c FROM samples WHERE rowid=?',(middle,)).fetchone()
                a,b=db.execute('SELECT MIN(rowid),MAX(rowid) FROM samples WHERE rowid BETWEEN ? AND ? AND t=? AND c=?',(lo,hi,t,c)).fetchone()
                cut=b if b<hi else a-1
                if cut<lo:raise ValueError('Un singolo ciclo DAT supera il limite richiesto')
                split(lo,cut);split(cut+1,hi)

            def split(lo,hi):
                nonlocal serial
                first=db.execute('SELECT t,c,original_id FROM samples WHERE rowid=?',(lo,)).fetchone()
                last=db.execute('SELECT t,c,original_id FROM samples WHERE rowid=?',(hi,)).fetchone()
                counts=dict(db.execute('SELECT n,COUNT(*) FROM samples WHERE rowid BETWEEN ? AND ? GROUP BY n',(lo,hi)))
                # 3 native channels per original variable. Bound native memory and legacy DLL limits.
                if hi-lo+1>8000000 or max(counts.values())>2000000:
                    divide(lo,hi);return
                channels=[]
                for i,nid in enumerate(ids):
                    count=counts.get(nid,0)
                    if not count:continue
                    times=[r[0] for r in db.execute('SELECT t FROM samples WHERE rowid BETWEEN ? AND ? AND n=? ORDER BY rowid LIMIT 4097',(lo,hi,nid))]
                    deltas=[(b-a)/1000 for a,b in zip(times,times[1:]) if b>a]
                    period=statistics.median(deltas) if deltas else max(.000001,float(metadata.get('initial_sample_ms') or 1)/1000)
                    label=str(definitions[nid].get('display_name') or nid).split('.')[-1][:100]
                    for kind,unit in (('value',str(definitions[nid].get('unit') or '')),('quality',''),('time','s')):
                        suffix={'value':'','quality':'_Q','time':'_T'}[kind]
                        channels.append({'name':f'{i+1:03d}_{label}'+suffix,'unit':unit,'node_id':nid,'count':count,
                            'period':struct.unpack('<f',_float32(period))[0],
                            'offset':struct.unpack('<f',_float32((times[0]-first[0])/1000))[0],'kind':kind})
                epoch=datetime.fromtimestamp(first[0]/1000,timezone.utc)
                header={'channels':channels,'info':{'starttime':epoch.strftime('%d.%m.%Y %H:%M:%S.%f')[:-3],
                    '$PDA_UtcOffset':'0','OriginalStartUTC':epoch.isoformat(),'OriginalArchive':path.name,
                    'TimestampSemantics':'Nominal Float32 channel axis; _T = actual relative seconds; original UTC/source/server/local timestamps in companion Parquet'}}
                raw=json.dumps(header,ensure_ascii=False,allow_nan=False).encode('utf-8')
                if len(raw)>1024*1024:raise ValueError('Metadati canali DAT troppo grandi')
                job=temporary/'job.bin'
                with job.open('wb') as output:
                    output.write(struct.pack('<I',len(raw)));output.write(raw)
                    for c in channels:
                        buffer=bytearray()
                        for t,v,q in db.execute('SELECT t,v,q FROM samples WHERE rowid BETWEEN ? AND ? AND n=? ORDER BY rowid',(lo,hi,c['node_id'])):
                            buffer.extend(_float32(v if c['kind']=='value' else q if c['kind']=='quality' else (t-first[0])/1000))
                            if len(buffer)>=32768:output.write(buffer);buffer.clear()
                        if buffer:output.write(buffer)
                candidate=temporary/'candidate.dat'
                candidate.unlink(missing_ok=True)
                verification=native.write(job,candidate)
                size=candidate.stat().st_size
                if size<1:raise ValueError('File DAT vuoto')
                if size>limit:
                    if lo==hi:raise ValueError('Un singolo campione DAT supera la dimensione massima richiesta')
                    divide(lo,hi);return
                serial+=1;destination=path.with_name(path.stem+f'_IBA{serial:04d}.dat')
                if destination.exists() or destination.with_suffix('.metadata.json').exists():raise ValueError('Destinazione DAT gia esistente; nessun file sovrascritto')
                sidecar={**metadata,'format':'dat','storage':{'format':'dat','label':'iba DAT Float32 + archivio originale','lossless':False,'original_archive_lossless':True},
                    'signals':[definitions[n] for n in ids if n in counts],'original_archive':path.name,'original_sha256':sha,
                    'original_range_keys':[_key(first),_key(last)],'rows_written':hi-lo+1,
                    'started_at':epoch.isoformat(),'ended_at':datetime.fromtimestamp(last[0]/1000,timezone.utc).isoformat(),
                    'duration_seconds':(last[0]-first[0])/1000,'native_channels':channels,'native_verification':verification,
                    'view_source_note':VIEW_NOTE,'conversion_note':'ibaFiles.dll 3.2.0 legacy; valori Float32, asse nominale; compatibilita con ibaPDA/ibaAnalyzer da verificare. Nessun ricampionamento. _Q: 0=non numerico, 1=Bad, 2=Uncertain, 3=Good. _T: secondi relativi effettivi.'}
                write_json(destination.with_suffix('.metadata.json'),sidecar)
                try:candidate.rename(destination)
                except OSError:
                    destination.with_suffix('.metadata.json').unlink(missing_ok=True);raise
                files.append({'name':destination.name,'path':str(destination),'format':'dat','bytes':size,'rows':hi-lo+1,'original_archive':path.name})
            split(1,total)
        finally:db.close()
    return files
