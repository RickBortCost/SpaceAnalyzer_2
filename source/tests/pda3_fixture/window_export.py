"""Complete windows and normal short final segments, with explicit genuine gap errors."""
from datetime import datetime,timezone
import json,math,struct,tempfile,time
from pathlib import Path
from pda3_fixture.exporter import PDA3TemplateWriter
from pda3.template_loader import template,profile
from pda3.timestamp_handler import PDA3TimestampHandler
from pda3.storage import atomic_json,digest
from pda3.errors import PDA3CapacityError

def process_job(job_path):
    from recording_reader import RecordingLibrary
    job_path=Path(job_path).resolve();root=job_path.parents[2]
    if job_path.parent.parent.name!='DAT':raise ValueError('PDA3: cartella job non valida')
    job=json.loads(job_path.read_text('utf-8'));nodes=job['nodes'];start=float(job['start_ms']);tolerance=float(job.get('tolerance_ms',1))
    if not math.isfinite(start) or not 0<=tolerance<5 or not 1<=len(nodes)<=64:raise ValueError('PDA3: parametri job non validi')
    name=job['name']
    if Path(name).name!=name or not name.endswith('.dat'):raise ValueError('PDA3: nome output non valido')
    destination=job_path.parent/name;manifest=destination.with_suffix('.json');started=time.perf_counter()
    sources=[]
    for relative in job['sources']+(job.get('context_sources',[]) if job.get('resampling') else []):
        path=(root/relative).resolve()
        if path.parent!=(root/'Parquet').resolve() or path.suffix!='.parquet' or not path.is_file():raise ValueError('PDA3: archivio originale non valido')
        sources.append(path)
    if not sources:raise ValueError('PDA3: archivi originali mancanti')
    ids={n['nodeid']:i for i,n in enumerate(nodes)}
    if len(ids)!=len(nodes):raise ValueError('PDA3: selezione duplicata')
    requested=job.get('expected_samples')
    if requested is not None and (type(requested) is not int or not 1<=requested<=60000):raise ValueError('PDA3: numero campioni richiesto non valido')
    data=[bytearray(240000) for _ in nodes];seen=[bytearray(60000) for _ in nodes]
    counts=[0]*len(nodes);rows=[0]*len(nodes);max_error=[0.]*len(nodes);grid_error=[0.]*len(nodes)
    issues={'duplicates':0,'off_grid':0,'outside_window':0,'bad_or_non_numeric':0,'float32_overflows':0}
    examples=[];first=math.inf;last=-math.inf;last_index=-1
    def issue(kind,nid,t):
        issues[kind]+=1
        if len(examples)<100:examples.append({'kind':kind,'nodeid':nid,'timestamp_ms':t})
    projection=None
    if job.get('resampling'):
        from pda3_fixture.resampling import project
        projection=project(sources,ids,start,requested,job.get('allow_partial'),job['resampling'])
        data=projection['data'];counts=projection['counts'];rows=projection['rows'];max_error=projection['max_error']
        first=projection['first'];last=projection['last'];expected=projection['expected']
    else:
        for path in sources:
            for row in RecordingLibrary(path.parent)._iter_parquet(path):
                nid=row.get('node_id')
                if nid not in ids:continue
                i=ids[nid];t=float(row['timestamp_ms']);rows[i]+=1
                if not math.isfinite(t):raise ValueError('PDA3: timestamp originale non finito')
                first=min(first,t);last=max(last,t);index=round((t-start)/10)
                if not 0<=index<(requested or 60000):issue('outside_window',nid,t);continue
                last_index=max(last_index,index)
                error=abs(t-(start+index*10));grid_error[i]=max(grid_error[i],error)
                if error>tolerance+.000001:issue('off_grid',nid,t);continue
                if seen[i][index]:issue('duplicates',nid,t);continue
                seen[i][index]=1;counts[i]+=1
                v=row.get('value_numeric')
                if row.get('quality','Good')!='Good' or not isinstance(v,(float,int)) or not math.isfinite(v):issue('bad_or_non_numeric',nid,t);continue
                try:packed=struct.pack('<f',v)
                except (OverflowError,struct.error):issue('float32_overflows',nid,t);continue
                fv=struct.unpack('<f',packed)[0]
                if not math.isfinite(fv):issue('float32_overflows',nid,t);continue
                data[i][index*4:index*4+4]=packed;max_error[i]=max(max_error[i],abs(v-fv))
        expected=requested or (max(1,last_index+1) if job.get('allow_partial') else 60000)
    duration=expected/100
    mapping=[{**n,'template_index':i,'pda3_channel_id':profile()['channels'][i]['id']} for i,n in enumerate(nodes)]
    record={**job.get('session',{}),'format':'pda3','pda3_profile':profile()['id'],'template_sha256':profile()['template_sha256'],
        'recording_id':job['id'],'name':name,'real_start_ms':start,'real_end_exclusive_ms':start+expected*10,
        'real_first_sample_ms':None if math.isinf(first) else first,'real_last_sample_ms':None if math.isinf(last) else last,
        'real_started_at':datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),
        'real_ended_at':datetime.fromtimestamp((start+expected*10)/1000,timezone.utc).isoformat(),
        'internal_clock':PDA3TimestampHandler.describe(template()),'timestamp_compatibility':'experimental: internal model time, real UTC in manifest',
        'sample_ms':10,'sample_rate_hz':100,'duration_seconds':duration,'short_final_segment':expected<60000,'active_channels':len(nodes),'channel_mapping':mapping,
        'expected_samples_per_channel':expected,'actual_samples_per_channel':dict(zip(ids,counts)),
        'received_rows_per_channel':dict(zip(ids,rows)),'missing_samples_per_channel':{nid:max(0,expected-counts[i]) for nid,i in ids.items()},
        'source_archives':[{'path':str(p.relative_to(root)).replace('\\','/'),'sha256':digest(p)} for p in sources],
        'gap_policy':'Skip entire DAT window on missing, duplicate, Bad/null or off-grid samples; preserve original Parquet',
        'missing_whole_windows_before':job.get('missing_whole_windows_before',0),
        'grid_tolerance_ms':tolerance,'max_grid_deviation_ms':dict(zip(ids,grid_error)),
        'timestamp_semantics':'Nominal 10 ms DAT axis. Original acquisition/source/server/local/FILETIME remain in Parquet. Nearest grid association only within explicit tolerance; values never interpolated.',
        'float32_max_cast_error':dict(zip(ids,max_error)),'issues':issues,'issue_examples_first_100':examples,
        'original_archive_lossless':True,'unused_channels':'constant Float32 +0, original channel metadata retained',
        'template_probe_opened_by_user':True,'new_writer_ibaAnalyzer_validated':False}
    failures=[f'{kind}: {number}' for kind,number in issues.items() if number]
    if projection:
        record.update(resampling=job['resampling'],projection_statistics={k:v for k,v in projection.items() if k not in ('data','counts','rows','max_error','first','last','expected')},
            gap_policy='Previous Good numeric value held for max_age_ms; absent, expired, Bad/Uncertain/non-numeric = NaN. Original quality remains in Parquet.',
            timestamp_semantics='Causal previous-sample projection on 10 ms PC UTC arrival grid; original clocks in Parquet',
            quality_semantics='Finite decoded values valid on the compatibility grid; original quality exclusively in Parquet')
    if not projection and any(n!=expected for n in counts):failures.append(f'Gap nei dati acquisiti: richiesti {expected} campioni distinti per segnale nell’intervallo effettivo')
    try:
        if failures:raise ValueError('; '.join(failures))
        if job.get('source_error'):raise ValueError('Acquisizione originale incompleta: '+job['source_error'])
        with tempfile.TemporaryDirectory(prefix='.pda3_convert_',dir=job_path.parent) as temp:
            candidate=Path(temp)/'candidate.dat';signals={i:bytes(v[:expected*4]) for i,v in enumerate(data)}
            try:result=PDA3TemplateWriter().write(candidate,signals,sample_count=expected)
            except PDA3CapacityError as exc:
                if not job.get('auto_split',True):raise
                from .segmented_export import write_segmented
                result=write_segmented(job_path,signals,expected,record,exc)
            else:
                if destination.exists():
                    if digest(destination)!=result['dat_sha256']:raise ValueError('DAT esistente diverso: nessuna sovrascrittura')
                else:
                    import os
                    os.link(candidate,destination)
        record.update(result,status='ready',validation='exact Float32 readback; metadata/indexes/counts preserved')
    except Exception as exc:record.update(status='skipped',error=str(exc),validation='DAT not published; original archives retained')
    record['processing_seconds']=round(time.perf_counter()-started,3)
    atomic_json(manifest,record)
    return record
