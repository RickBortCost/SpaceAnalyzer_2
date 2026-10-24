"""Materialize selected original rows using existing storage, then the same window engine."""
from datetime import datetime,timezone
import json,math,os,re,secrets,zipfile
from pathlib import Path
from .storage import atomic_json
from .recording import run_job

def materialize(job_path,job):
    from recording_reader import RecordingLibrary
    from analysis_tools import export_rows,selected_rows
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,220}',str(job.get('id',''))):raise ValueError('PDA3: identificativo job non valido')
    source=job['historical_source'];source_path=Path(source['path']).resolve()
    library=RecordingLibrary(source_path.parent);path=library._path(source_path.name)
    root=Path(job_path).parents[2];out=root/'Parquet'/('PDA3_Export_'+job['id']+'.parquet');out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():
        job['sources']=[out.relative_to(root).as_posix()];atomic_json(job_path,job);return
    temporary=out.with_name(out.name+'.'+secrets.token_hex(8)+'.part')
    start=job['start_ms'];end=start+job.get('expected_samples',60000)*10
    ids=[n['nodeid'] for n in job['nodes']]
    rows=(r for r in selected_rows(library._iter_rows(path,ids,start,end),ids,start,end) if float(r['timestamp_ms'])<end)
    try:
        export_rows(rows,temporary,'parquet');os.link(temporary,out)
    finally:temporary.unlink(missing_ok=True)
    atomic_json(out.with_suffix('.metadata.json'),{'signals':job['nodes'],'initial_sample_ms':10,'source':'historical PDA3 export; original selected rows','original':str(path),'format':'parquet'})
    job['sources']=[out.relative_to(root).as_posix()];atomic_json(job_path,job)

def export_interval(library,name,ids,start,end,recordings,exports,options=None):
    if not math.isfinite(start) or not math.isfinite(end) or not 0<=end-start<=600000.001:
        raise ValueError('PDA3: selezionare un intervallo fino a 600 s')
    count=min(60000,math.floor((end-start)/10+.000001)+1)
    info=library.describe(name);byid={n['nodeid']:n for n in info['signals']}
    if not ids or len(ids)>64 or any(n not in byid for n in ids):raise ValueError('PDA3: selezionare da 1 a 64 segnali disponibili')
    calendar=datetime.fromtimestamp(start/1000,timezone.utc);identity='Export_'+secrets.token_hex(8)
    root=Path(recordings);folder=root/'DAT'/calendar.strftime('%Y-%m-%d');folder.mkdir(parents=True,exist_ok=True)
    filename=calendar.strftime('%Y%m%d_%H%M%S_')+identity+'.dat';path=folder/(identity+'.pda3job.json')
    job={'id':identity,'name':filename,'sources':[],'nodes':[byid[n] for n in ids],'start_ms':start,'state':'queued','tolerance_ms':float((options or {}).get('pda3_tolerance_ms',1)),
        'expected_samples':count,'allow_partial':True,'auto_split':(options or {}).get('pda3_auto_split',True) is not False,
        'historical_source':{'path':str(library._path(name))},'session':{'session_name':'Export X1-X2','description':str(name)}}
    atomic_json(path,job);record=run_job(path)
    if record['status']!='ready':raise ValueError(record.get('error') or 'PDA3 non generato; originale e manifest diagnostico conservati')
    out=Path(exports)/(identity+'_PDA3.zip');out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,'x',zipfile.ZIP_DEFLATED) as archive:
        if record.get('segmented'):
            for f in record['dat_files']:
                p=root/f['name'];archive.write(p,p.name);archive.write(p.with_suffix('.json'),p.with_suffix('.json').name)
            archive.write((folder/filename).with_suffix('.json'),'SESSION_MANIFEST.json')
            archive.writestr('LEGGIMI.txt','Export lossless in DAT consecutivi da massimo 10 s: il modello non contiene i dati in un solo blocco.\nPer aprire tutto in Space Analyzer: selezionare tutti i DAT e JSON e abilitare Unisci DAT consecutivi.\nI file mantengono l inizio interno del modello; il tempo reale di ogni parte e nel JSON associato.\nCompatibilita con ibaAnalyzer ancora da verificare.\n')
        else:
            archive.write(folder/filename,filename);archive.write((folder/filename).with_suffix('.json'),Path(filename).with_suffix('.json').name)
    return {'name':out.name,'path':str(out),'rows':count*len(ids),'duration_seconds':count/100,'bytes':out.stat().st_size,'url':'/api/exports/'+out.name,
        'dat_files':len(record.get('dat_files',[])) or 1,
        'note':record.get('status_note','DAT sperimentale: timestamp reale nel manifest JSON. Aprire dopo estrazione dello ZIP.')}
