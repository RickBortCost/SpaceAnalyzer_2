"""Direct known-profile DAT replay on either manifest UTC or an explicit import offset."""
from datetime import datetime,timedelta,timezone
from functools import lru_cache
import hashlib,json,math,struct
from pathlib import Path
from .reader import PDA3Reader
from .template_loader import profile,validate_profile,valid_sample_count
from .timestamp_handler import PDA3TimestampHandler

def is_pda3(path):
    with Path(path).open('rb') as stream:return stream.read(4)==b'PDA3'

@lru_cache(maxsize=4)
def _reader(path,size,mtime):
    r=PDA3Reader(path);validate_profile(r);return r

def reader(path):
    path=Path(path);s=path.stat();return _reader(str(path.resolve()),s.st_size,s.st_mtime_ns)

def details(path,meta):
    r=reader(path);p=profile();meta=dict(meta or {});count=valid_sample_count(r)
    if meta.get('pda3_profile'):
        if meta.get('pda3_profile')!=p['id'] or meta.get('dat_sha256')!=r.sha256:raise ValueError('PDA3: manifest non associato al DAT (hash/profilo diverso)')
        if meta.get('valid_samples_per_channel',count)!=count or meta.get('expected_samples_per_channel',count)!=count:
            raise ValueError('PDA3: durata/campioni del manifest diversi dal DAT')
        start=float(meta['real_start_ms'])
        if not math.isfinite(start):raise ValueError('PDA3: inizio reale non valido')
        supplied=meta.get('channel_mapping')
        if not isinstance(supplied,list) or not 1<=len(supplied)<=64:raise ValueError('PDA3: mappatura manifest non valida')
        signals=[];seen=set();ids=set()
        for n in supplied:
            index=n.get('template_index');nid=n.get('nodeid')
            if type(index) is not int or not 0<=index<64 or index in seen or not isinstance(nid,str) or not nid or nid in ids:
                raise ValueError('PDA3: canali manifest duplicati/non validi')
            seen.add(index);ids.add(nid);signals.append({**n,'nodeid':nid,'display_name':str(n.get('display_name') or nid),'unit':str(n.get('unit') or '')})
        note='Tempo reale dal manifest; valori Float32 letti direttamente dal DAT. ibaAnalyzer mantiene il calendario del modello.'
    else:
        offset=meta.get('pda3_timezone_offset_minutes')
        if offset is None:raise ValueError('PDA3: manca il manifest; importare il DAT scegliendo esplicitamente il fuso orario del calendario interno')
        offset=float(offset)
        if not math.isfinite(offset) or not -840<=offset<=840:raise ValueError('PDA3: fuso orario non valido')
        calendar=PDA3TimestampHandler.describe(r)['internal_start_calendar']
        start=datetime.fromisoformat(calendar).replace(tzinfo=timezone(timedelta(minutes=offset))).timestamp()*1000
        signals=[{'nodeid':'pda3:'+c['id'],'display_name':c['display_name'],'unit':c['unit'],'template_index':i} for i,c in enumerate(p['channels'])]
        note=f'Valori Float32 letti dal DAT; calendario interno interpretato con offset UTC {offset:+g} minuti scelto all’importazione. Qualita PLC originale non disponibile.'
    return {**meta,'format':'dat','pda3':True,'started_at':datetime.fromtimestamp(start/1000,timezone.utc).isoformat(),
        'ended_at':datetime.fromtimestamp((start+(count-1)*10)/1000,timezone.utc).isoformat(),'duration_seconds':count/100,
        'rows_written':count*len(signals),'initial_sample_ms':10,'signals':signals,'real_start_ms':start,
        'valid_samples_per_channel':count,'real_end_exclusive_ms':start+count*10,
        'storage':{'format':'pda3','label':'iba DAT PDA3 · Float32 · 100 Hz','lossless':False},
        'view_source_note':note,'timestamp_semantics':note,'quality_semantics':'Validita numerica del campione decodificato; per qualita PLC e timestamp originali usare il Parquet associato.'}

def iter_rows(path,meta,signal_ids=None,start_ms=None,end_ms=None):
    r=reader(path);info=details(path,meta);start=info['real_start_ms'];p=profile();count=info['valid_samples_per_channel']
    selected=None if signal_ids is None else set(signal_ids)
    # Channel-major iteration avoids a 3.84-million-row Python object matrix.
    for signal in info['signals']:
        if selected is not None and signal['nodeid'] not in selected:continue
        channel=p['channels'][signal['template_index']];position=0
        for bi in channel['blocks']:
            if position>=count:break
            block=r.blocks[bi]
            if (start_ms is not None and start+(position+block.samples-1)*10<float(start_ms)) or (end_ms is not None and start+position*10>float(end_ms)):
                position+=block.samples;continue
            for (value,) in struct.iter_unpack('<f',r.block_bits(block)):
                if position>=count:break
                t=start+position*10;good=math.isfinite(value)
                position+=1
                if (start_ms is not None and t<float(start_ms)) or (end_ms is not None and t>float(end_ms)):continue
                yield {'timestamp_ms':t,'timestamp_utc':datetime.fromtimestamp(t/1000,timezone.utc).isoformat(),
                    'elapsed_ms':(position-1)*10,'cycle':position,'node_id':signal['nodeid'],'signal_name':signal['display_name'],
                    'value_numeric':value if good else None,'value_text':str(value),'data_type':'Float',
                    'good':good,'quality':'Good' if good else 'Bad','status':'PDA3DecodedNumeric' if good else 'PDA3NonFinite',
                    'sample_ms':10,'source_timestamp':None,'server_timestamp':None,'local_timestamp':None,'ads_filetime':None}
        if min(position,count)!=count:raise ValueError('PDA3: conteggio canale non valido')

@lru_cache(maxsize=512)
def _sequence_part_info(path,size,mtime,metadata_json):
    # Keep validated descriptors without retaining all native DAT buffers. The
    # file stat and complete sidecar content invalidate a changed sequence part.
    return details(path,json.loads(metadata_json))

def sequence_info(library,path):
    document=json.loads(Path(path).read_text('utf-8'));names=document.get('parts')
    if not isinstance(names,list) or not 2<=len(names)<=256 or any(not isinstance(n,str) or n.endswith('.pda3seq') for n in names):raise ValueError('PDA3: sequenza non valida')
    parts=[]
    for name in names:
        part=library._path(name)
        if not is_pda3(part):raise ValueError('PDA3: la sequenza ammette solo DAT del profilo noto')
        stat=part.stat();meta=library._metadata(part)
        parts.append((name,_sequence_part_info(str(part),stat.st_size,stat.st_mtime_ns,json.dumps(meta,sort_keys=True))))
    parts.sort(key=lambda row:row[1]['real_start_ms']);ids=[n['nodeid'] for n in parts[0][1]['signals']]
    for (a,pa),(b,pb) in zip(parts,parts[1:]):
        if [n['nodeid'] for n in pb['signals']]!=ids:raise ValueError('PDA3: segnali differenti nella sequenza')
        delta=pb['real_start_ms']-pa['real_end_exclusive_ms']
        if abs(delta)>.001:raise ValueError(f'PDA3: sequenza non continua; gap/sovrapposizione di {delta:g} ms tra {a} e {b}')
    first=parts[0][1];last=parts[-1][1]
    return parts,{**first,**library._metadata(Path(path)),'format':'dat','ended_at':last['ended_at'],'real_end_exclusive_ms':last['real_end_exclusive_ms'],'duration_seconds':sum(p['duration_seconds'] for _,p in parts),
        'rows_written':sum(p['rows_written'] for _,p in parts),'view_source_note':'Sequenza PDA3 con continuita verificata. '+first['view_source_note']}

def iter_sequence(library,path,signal_ids=None,start_ms=None,end_ms=None):
    parts,_=sequence_info(library,path)
    for name,meta in parts:yield from iter_rows(library._path(name),meta,signal_ids,start_ms,end_ms)
