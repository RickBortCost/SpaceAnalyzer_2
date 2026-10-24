"""Lossless fixed-template overflow fallback: consecutive short DATs plus a sequence.

No block sizes, sample counts, index entries or start ticks are changed. Each
piece uses the existing short-DAT representation, with an excluded NaN tail.
"""
from datetime import datetime,timezone
import json,os,shutil,tempfile,time,math,struct
from pathlib import Path
from .exporter import PDA3TemplateWriter
from .template_loader import template,profile
from .storage import atomic_json,digest

SAFE_SAMPLES=1000  # 10 s: arbitrary 1000 Float32 words + NaN tail fit the mapped first block.

def split_capacity():
    r=template()
    for c in profile()['channels']:
        b=r.blocks[c['blocks'][0]]
        if b.samples<SAFE_SAMPLES+100 or b.size<4*SAFE_SAMPLES+(SAFE_SAMPLES+126)//127+256:
            raise ValueError('PDA3: modello non adatto alla suddivisione lossless da 10 s')
    return SAFE_SAMPLES

def write_segmented(job_path,signals,count,record,reason):
    job_path=Path(job_path);root=job_path.parents[2];folder=job_path.parent
    safe=split_capacity();total=(count+safe-1)//safe
    if total<2:raise ValueError('PDA3: blocco non rappresentabile anche nel segmento breve; nessun dato approssimato')
    stem=Path(record['name']).stem;target=folder/(stem+'_parts');sequence=folder/(stem+'.pda3seq')
    free=shutil.disk_usage(folder).free;required=(total+1)*len(template().raw)+5*1024*1024
    if free<required:raise OSError(f'PDA3: spazio insufficiente per {total} DAT ({required} byte richiesti); Parquet conservato')
    progress=job_path.with_name(job_path.name+'.progress.json');files=[];started=time.perf_counter()
    def update(done):atomic_json(progress,{'phase':'split_lossless','completed':done,'total':total,'segment_seconds':safe/100,
        'reason':str(reason),'logical_dat_bytes':total*len(template().raw),'elapsed_seconds':round(time.perf_counter()-started,1)})
    update(0)
    with tempfile.TemporaryDirectory(prefix='.pda3_split_',dir=folder) as tmp:
        staging=Path(tmp)/'parts';staging.mkdir()
        for i,offset in enumerate(range(0,count,safe)):
            n=min(safe,count-offset);name=stem+f'_s{i+1:04d}.dat';path=staging/name
            result=PDA3TemplateWriter().write(path,{k:v[offset*4:(offset+n)*4] for k,v in signals.items()},sample_count=n)
            start=record['real_start_ms']+offset*10
            part={**record,**result,'name':name,'recording_id':record['recording_id']+f'_s{i+1:04d}',
                'real_start_ms':start,'real_end_exclusive_ms':start+n*10,'duration_seconds':n/100,
                'real_started_at':datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),
                'real_ended_at':datetime.fromtimestamp((start+n*10)/1000,timezone.utc).isoformat(),
                'real_first_sample_ms':start,'real_last_sample_ms':start+(n-1)*10,
                'real_sample_bounds_basis':'Nominal 10 ms DAT grid; exact source timestamps and deviations remain in the original Parquet',
                'expected_samples_per_channel':n,'actual_samples_per_channel':{k:n for k in record['actual_samples_per_channel']},
                'received_rows_per_channel':{k:n for k in record['actual_samples_per_channel']},
                'missing_samples_per_channel':{k:0 for k in record['actual_samples_per_channel']},
                'source_sample_range':[offset,offset+n],'parent_recording_id':record['recording_id'],
                'short_final_segment':True,'status':'ready','segmentation_reason':str(reason),
                'validation':'exact Float32 readback; native template indexes and block sizes unchanged',
                'float32_cast_error_scope':'bound from the original complete window'}
            if record.get('resampling'):
                keys=list(record['actual_samples_per_channel'])
                valid={nid:sum(math.isfinite(v[0]) for v in struct.iter_unpack('<f',signals[index][offset*4:(offset+n)*4])) for index,nid in enumerate(keys)}
                part.update(actual_samples_per_channel=valid,
                    missing_samples_per_channel={nid:n-valid[nid] for nid in keys},
                    received_rows_per_channel=record['received_rows_per_channel'],
                    received_rows_scope='Original row counts for the complete parent window; projection validity counts are local to this segment')
            atomic_json(path.with_suffix('.json'),part)
            relative=(target/name).relative_to(root).as_posix()
            files.append({'name':relative,'format':'dat','bytes':result['size_bytes'],'rows':n*len(signals),
                'duration_seconds':n/100,'samples_per_channel':n,'dat_sha256':result['dat_sha256']})
            update(i+1)
        # All pieces have been verified before a collection becomes visible.
        # A completed directory from an interrupted earlier attempt is reused only
        # when every native DAT matches its deterministic expected hash/clock/map.
        if target.exists():
            if not target.is_dir():raise ValueError('PDA3: percorso parti gia esistente')
            wanted={p.name for p in staging.iterdir()}
            if {p.name for p in target.iterdir()}!=wanted:raise ValueError('PDA3: cartella parti esistente diversa; nessuna sovrascrittura')
            for f in files:
                p=root/f['name'];expected=json.loads((staging/p.name).with_suffix('.json').read_text('utf-8'))
                existing=json.loads(p.with_suffix('.json').read_text('utf-8'))
                if digest(p)!=f['dat_sha256'] or any(existing.get(k)!=expected.get(k) for k in ('real_start_ms','real_end_exclusive_ms','channel_mapping','expected_samples_per_channel')):
                    raise ValueError('PDA3: parti esistenti diverse; nessuna sovrascrittura')
        else:os.rename(staging,target)
    document={'parts':[f['name'] for f in files]}
    if sequence.exists() and json.loads(sequence.read_text('utf-8'))!=document:raise ValueError('PDA3: sequenza esistente diversa')
    atomic_json(sequence,document)
    from recording_reader import RecordingLibrary
    from .service import sequence_info
    _,info=sequence_info(RecordingLibrary(root),sequence)
    if info['rows_written']!=count*len(signals) or abs(info['duration_seconds']-count/100)>.000001:
        raise ValueError('PDA3: sequenza esportata non copre tutti i campioni')
    atomic_json(sequence.with_suffix('.metadata.json'),{'display_name':stem+f' · sequenza lossless ({total} DAT)',
        **{k:record.get(k,'') for k in ('session_name','machine','operator','description')},
        'segmentation_reason':str(reason),'source_archives':record['source_archives']})
    return {'segmented':True,'segmentation_reason':str(reason),'capacity_details':getattr(reason,'details',{}),
        'dat_files':files,'segment_count':total,'safe_segment_samples':safe,
        'sequence_name':sequence.relative_to(root).as_posix(),'sequence_path':str(sequence),
        'size_bytes':sum(f['bytes'] for f in files),'float32_roundtrip_exact':True,
        'valid_samples_per_channel':count,'metadata_and_all_indexes_preserved':True,
        'ibaAnalyzer_new_output_validated':False,
        'status_note':f'Suddivisione lossless automatica in {total} DAT consecutivi (massimo 10 s); nessun campione perso. La struttura di ciascun file resta di 13,19 MiB.'}
