"""FDA DAS4 subset derived from the supplied reference, not an official SDK.

Read the demonstrated CDasChan schema 2 with UInt32 milliseconds, scaled
Int16 and Float32 channels (including mixed records). The exporter continues
to write the previously demonstrated Int16 profile. Original acquisition
precision/quality/timestamps stay in companion Parquet recordings.
"""
import itertools
import json
import math
import os
import shutil
import sqlite3
import struct
import tempfile
import unicodedata
from datetime import datetime,timezone
from pathlib import Path

FIXED=struct.Struct('<22s5sIddIdHIH')
CHANNEL=struct.Struct('<HHBBHHddI')
QUALITY={0:'Bad',1:'Bad',2:'Uncertain',3:'Good'}
MAX_ABS_INT16=32767
MAX_TICKS=0xffffffff


def choose_gain(peak):
    """Smallest 1-2-5 scale step so that peak/gain fits Int16 (>= ~40% of full scale used).

    Powers of ten alone could waste up to 90% of the range (peak 32768 -> gain 10).
    """
    if not peak or not math.isfinite(peak):return 1.
    exponent=math.floor(math.log10(peak/MAX_ABS_INT16))
    for e in range(exponent-1,exponent+3):
        for m in ('1','2','5'):
            gain=float(m+'e'+str(e))
            if gain>0 and peak/gain<=MAX_ABS_INT16:return gain
    raise ValueError('Scala FDA fuori dai limiti numerici')


def enough_space(directory,minimum_mb=None):
    """True when the recording drive keeps a safety margin for the sort database/output."""
    minimum=int(os.environ.get('SPACE_FDA_MIN_FREE_MB',256) if minimum_mb is None else minimum_mb)*1024**2
    try:return shutil.disk_usage(directory).free>=minimum
    except OSError:return True


def text(value):
    raw=str(value or '').encode('cp1252',errors='replace')[:254]
    return bytes([len(raw)])+raw


def read_text(stream):
    n=stream.read(1)
    if not n:raise ValueError('Intestazione FDA troncata')
    size=n[0]
    if size==255:
        extra=stream.read(2)
        if len(extra)!=2:raise ValueError('Stringa FDA troncata')
        size=struct.unpack('<H',extra)[0]
        if size>=65534:raise ValueError('Variante stringhe FDA non supportata')
    raw=stream.read(size)
    if len(raw)!=size:raise ValueError('Stringa FDA troncata')
    return raw.decode('cp1252',errors='replace')


def header(channels,epoch,sample_ms=10,settings=None):
    if not 0<=epoch<=0xffffffff or not 1<=len(channels)<=4096:raise ValueError('Limiti intestazione FDA superati')
    settings=settings or (0.,1000/max(1,sample_ms),20.,0,120)
    record_size=4+sum(c['size'] for c in channels[1:])
    data=bytearray(FIXED.pack(b'\0'*22,b'DAS4\0',epoch,settings[0],settings[1],record_size,settings[2],settings[3],settings[4],len(channels)))
    offset=0
    for i,c in enumerate(channels):
        data+=struct.pack('<HHH',65535,2,8)+b'CDasChan' if i==0 else b'\x01\x80'
        data+=text(c['name'])+text(c.get('description',''))+text(c.get('unit',''))
        data+=CHANNEL.pack(c.get('index',i-1 if i else 65535),c.get('enabled',1),c.get('kind',0 if i else 255),
                           c.get('type',2 if i else 0),c['size'],1,c.get('gain',1.),c.get('offset',0.),offset)
        offset+=c['size']
    data+=struct.pack('<HI',0,len(data)+6)
    return bytes(data)


def read_header(stream):
    raw=stream.read(FIXED.size)
    if len(raw)!=FIXED.size:raise ValueError('File FDA troppo breve')
    prefix,magic,epoch,a,b,stride,d,e,f,count=FIXED.unpack(raw)
    if prefix!=b'\0'*22 or magic!=b'DAS4\0' or not 1<=count<=4096 or not 4<=stride<=65536:
        raise ValueError('Variante FDA non supportata: atteso DAS4')
    channels=[];offset=0
    for i in range(count):
        tag=stream.read(2)
        if i==0:
            if tag!=b'\xff\xff' or stream.read(4)!=struct.pack('<HH',2,8) or stream.read(8)!=b'CDasChan':
                raise ValueError('Schema canali FDA non supportato')
        elif tag!=b'\x01\x80':raise ValueError('Classe canale FDA non supportata')
        name,description,unit=read_text(stream),read_text(stream),read_text(stream)
        raw=stream.read(CHANNEL.size)
        if len(raw)!=CHANNEL.size:raise ValueError('Canale FDA troncato')
        idx,enabled,kind,dtype,size,items,gain,bias,pos=CHANNEL.unpack(raw)
        if items!=1 or pos!=offset or not math.isfinite(gain) or not math.isfinite(bias) or gain<=0:
            raise ValueError('Layout o scala canale FDA non supportati')
        if (i==0 and (kind,dtype,size)!=(255,0,4)) or (i and (kind,dtype,size) not in ((0,2,2),(0,4,4))):
            raise ValueError(f'Tipo numerico FDA non supportato nel canale {name}: tipo {dtype}, {size} byte; supportati Int16 e Float32')
        channels.append(dict(name=name,description=description,unit=unit,index=idx,enabled=enabled,
                             kind=kind,type=dtype,size=size,gain=gain,offset=bias))
        offset+=size
    tail=stream.read(6)
    if len(tail)!=6:raise ValueError('Intestazione FDA troncata')
    reserved,start=struct.unpack('<HI',tail)
    if reserved or start!=stream.tell() or stride!=offset:raise ValueError('Dimensioni intestazione FDA incoerenti')
    return dict(channels=channels,epoch=epoch,record_size=stride,data_offset=start,settings=(a,b,d,e,f))


def inspect(path):
    path=Path(path)
    with path.open('rb') as stream:h=read_header(stream)
    payload=path.stat().st_size-h['data_offset']
    if payload<0 or payload%h['record_size']:raise ValueError('File FDA troncato o dati di dimensione incoerente')
    h['frames']=payload//h['record_size']
    return h


def channel_map(h):
    """Recover our explicitly described _Q companions even without a sidecar."""
    result=[];i=1
    while i<len(h['channels']):
        c=h['channels'][i];q=h['channels'][i+1] if i+1<len(h['channels']) else None
        paired=q is not None and q['name']==c['name']+'_Q' and q['description']=='Quality: 0=missing, 1=Bad, 2=Uncertain, 3=Good' and q['gain']==1. and q['offset']==0.
        nodeid=c['description'] if paired and 0<len(c['description'])<251 else 'das:'+c['name']+':'+str(i)
        result.append(dict(value_channel=i,quality_channel=i+1 if paired else None,nodeid=nodeid,display_name=c['name'],unit=c['unit']))
        i+=2 if paired else 1
    return result


def frame_struct(h):
    return struct.Struct('<I'+''.join('f' if c['type']==4 else 'h' for c in h['channels'][1:]))


def iter_rows(path,metadata=None,signal_ids=None,start_ms=None,end_ms=None):
    path=Path(path);meta=metadata or {};mapped=meta.get('fda_channels') or []
    with path.open('rb') as stream:
        h=read_header(stream);fmt=frame_struct(h)
        if fmt.size!=h['record_size']:raise ValueError('Dimensione record FDA non valida')
        previous=None;cycle=0
        wanted=set(signal_ids) if signal_ids is not None else None
        definitions=[s for s in (mapped or channel_map(h)) if wanted is None or s['nodeid'] in wanted]
        while True:
            raw=stream.read(fmt.size)
            if not raw:break
            if len(raw)!=fmt.size:raise ValueError('Record FDA troncato')
            values=fmt.unpack(raw);t=h['epoch']*1000+values[0];cycle+=1
            period=t-previous if previous is not None and t>previous else 0;previous=t
            if start_ms is not None and t<start_ms or end_ms is not None and t>end_ms:continue
            stamp=datetime.fromtimestamp(t/1000,timezone.utc).isoformat(timespec='milliseconds')
            for signal in definitions:
                i=signal.get('value_channel');qi=signal.get('quality_channel')
                if not isinstance(i,int) or not 1<=i<len(values) or qi is not None and (not isinstance(qi,int) or not 1<=qi<len(values)):
                    raise ValueError('Mappa canali FDA non valida')
                c=h['channels'][i];q=values[qi] if qi is not None else 3
                quality=QUALITY.get(q,'Bad');number=values[i]*c['gain']+c['offset']
                if not math.isfinite(number):quality='Bad'
                yield dict(timestamp_ms=t,timestamp_utc=stamp,node_id=signal['nodeid'],signal_name=signal.get('display_name') or c['name'],
                           value_numeric=number if quality=='Good' else None,value_text=str(number),data_type='Double',
                           good=quality=='Good',quality=quality,status='FDA_Missing' if q==0 else 'FDA_'+quality,
                           sample_ms=meta.get('initial_sample_ms') or period,cycle=cycle,source_timestamp=None,server_timestamp=None)


def safe_name(name,index):
    value=unicodedata.normalize('NFKD',str(name)).encode('ascii',errors='ignore').decode('ascii')
    return (value[:100] or 'Signal')+'_'+str(index+1)


def export_parquet(original,nodes,max_bytes=0):
    """Convert finalized originals off acquisition threads, using bounded RAM.

    SQLite sorts timestamps on disk (asynchronous calculated rows can arrive
    late). Disjoint channel updates at the same source time share an FDA frame;
    repeated updates of the same channel remain distinct frames. Absent inputs
    have Q=0, never carried forward. Integer-ms rounding does not drop rows.
    """
    from recording_reader import RecordingLibrary
    original=Path(original);ids={n['nodeid']:i for i,n in enumerate(nodes)}
    if not 1<=len(ids)<=64:raise ValueError('FDA: selezionare da 1 a 64 segnali')
    library=RecordingLibrary(original.parent);source_meta=library._metadata(original)
    limits=[0.]*len(ids);staged=[];files=[];stream=None;partial=None
    if not enough_space(original.parent):raise ValueError('Spazio disco insufficiente per la conversione FDA: archivio originale conservato')
    last_seen={};discont=dict(count=0,tolerance_ms=1.0,examples=[]);staged_rows=0
    handle,temp=tempfile.mkstemp(prefix='.fda_sort_',suffix='.sqlite',dir=original.parent);os.close(handle)
    db=sqlite3.connect(temp)
    try:
        db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA synchronous=OFF');db.execute('PRAGMA temp_store=FILE');db.execute('PRAGMA cache_size=-8192')
        db.execute('CREATE TABLE samples(t REAL,c INTEGER,n INTEGER,v REAL,q INTEGER)')
        for row in library._iter_parquet(original,columns=['timestamp_ms', 'cycle', 'node_id', 'value_numeric', 'quality', 'good']):
            nid=row.get('node_id')
            if nid not in ids:continue
            t=float(row['timestamp_ms'])
            if not math.isfinite(t):raise ValueError('Timestamp FDA non finito')
            # A backwards source clock (PLC/NTP step) is reported, never silently hidden.
            previous_t=last_seen.get(ids[nid])
            if previous_t is not None and t<previous_t-discont['tolerance_ms']:
                discont['count']+=1
                if len(discont['examples'])<16:discont['examples'].append(dict(signal=nid,previous_ms=previous_t,next_ms=t))
            last_seen[ids[nid]]=t
            q={'Good':3,'Uncertain':2,'Bad':1}.get(row.get('quality'),3 if row.get('good') else 1)
            value=row.get('value_numeric')
            # Uncertain keeps its numeric value (Q=2); only Bad/missing/non-numeric become 0.
            if q in (2,3) and isinstance(value,(int,float)) and math.isfinite(value):
                v=float(value);limits[ids[nid]]=max(limits[ids[nid]],abs(v))
            else:v=0.;q=2 if q==2 else 1
            staged_rows+=1
            if staged_rows%500000==0 and not enough_space(original.parent):raise ValueError('Spazio disco insufficiente durante la conversione FDA: archivio originale conservato')
            staged.append((t,int(row['cycle']),ids[nid],v,q))
            if len(staged)>=8192:
                db.executemany('INSERT INTO samples VALUES(?,?,?,?,?)',staged);staged.clear()
        if staged:db.executemany('INSERT INTO samples VALUES(?,?,?,?,?)',staged)
        db.commit();db.execute('CREATE INDEX chronology ON samples(t,c)')
        first=db.execute('SELECT MIN(t) FROM samples').fetchone()[0]
        epoch=int(first//1000) if first is not None else int(datetime.fromisoformat(source_meta['started_at']).timestamp())
        channels=[dict(name='INTERNAL TIME',description='Time from start...',unit='Milliseconds',size=4)]
        mapping=[]
        for i,node in enumerate(nodes):
            peak=limits[i];gain=choose_gain(peak)
            if gain<=0 or not math.isfinite(gain):raise ValueError('Scala FDA fuori dai limiti numerici')
            name=safe_name(node.get('display_name') or node['nodeid'],i)
            channels.append(dict(name=name,description=node['nodeid'][:250],unit=node.get('unit',''),size=2,gain=gain))
            value_channel=len(channels)-1
            channels.append(dict(name=name+'_Q',description='Quality: 0=missing, 1=Bad, 2=Uncertain, 3=Good',unit='',size=2,gain=1.))
            mapping.append(dict(nodeid=node['nodeid'],display_name=node.get('display_name') or node['nodeid'],unit=node.get('unit',''),
                                value_channel=value_channel,quality_channel=len(channels)-1,gain=gain,offset=0.,maximum_rounding_error=gain/2))
        period=source_meta.get('initial_sample_ms') or 10;raw_header=header(channels,epoch,period)
        fmt=struct.Struct('<I'+'h'*(len(channels)-1))
        if max_bytes and len(raw_header)+fmt.size>max_bytes:raise ValueError('Limite FDA inferiore a intestazione e un campione completo')
        part=0;frames=0;first_t=last_t=None

        def finish():
            nonlocal stream,partial
            if stream is None:return
            stream.flush();os.fsync(stream.fileno());stream.close();stream=None
            destination=Path(str(partial)[:-5])
            info={**source_meta,'format':'das','fda_chronological':True,'layout':'FDA DAS4 wide frames','signals':nodes,'fda_channels':mapping,
                  'original_archive':original.name,'rows_written':frames*len(nodes),'frames':frames,
                  'started_at':datetime.fromtimestamp(first_t/1000 if first_t is not None else epoch,timezone.utc).isoformat(),
                  'ended_at':datetime.fromtimestamp(last_t/1000 if last_t is not None else epoch,timezone.utc).isoformat(),
                  'duration_seconds':(last_t-first_t)/1000 if first_t is not None else 0,
                  'storage':{'format':'das','label':'FDA DAS4 · Int16 scalato','lossless':False},
                  'fda_compatibility':'reference-derived; external FDA validation pending',
                  'timestamp_semantics':'UInt32 relative milliseconds, nearest ms; original precision in companion Parquet',
                  'quality_semantics':'Per-signal _Q: 0 missing, 1 Bad, 2 Uncertain, 3 Good; absent values are zero with Q=0; Uncertain keeps its value',
                  'fda_gain_policy':'1-2-5 scale steps, peak<=32767','time_discontinuities':discont}
            meta=destination.with_suffix('.metadata.json');tmp=meta.with_suffix('.tmp')
            tmp.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
            # Metadata first: a visible .das always has its sidecar, even after a crash.
            os.replace(tmp,meta);os.replace(partial,destination);partial=None
            files.append(dict(name=destination.name,rows=frames*len(nodes),frames=frames,bytes=destination.stat().st_size,part=part))

        def frames_at_source_time():
            for t,group in itertools.groupby(db.execute('SELECT t,c,n,v,q FROM samples ORDER BY t,c'),key=lambda x:int(round(x[0]))):
                values=[0]*(len(channels)-1);seen=set()
                for _,_,n,v,q in group:
                    if n in seen:
                        yield t,values
                        values=[0]*(len(channels)-1);seen=set()
                    gain=mapping[n]['gain'];encoded=round(v/gain)
                    if not -32768<=encoded<=32767 or not math.isfinite(encoded*gain):raise ValueError('Overflow numerico FDA: archivio originale conservato')
                    values[2*n]=encoded;values[2*n+1]=q;seen.add(n)
                yield t,values

        for t,values in frames_at_source_time():
            ticks=int(t-epoch*1000)
            if not 0<=ticks<=MAX_TICKS:
                # UInt32 ms lasts 49.7 days: continue in a new part with its own epoch.
                finish();epoch=int(t//1000);raw_header=header(channels,epoch,period);ticks=int(t-epoch*1000)
                if not 0<=ticks<=MAX_TICKS:raise ValueError('Durata FDA oltre il limite UInt32 millisecondi')
            if stream is not None and max_bytes and stream.tell()+fmt.size>max_bytes:finish()
            if stream is None:
                part+=1;frames=0;first_t=last_t=None
                destination=original.with_name(original.stem+f'_FDA{part:04d}.das');partial=Path(str(destination)+'.part')
                stream=partial.open('wb');stream.write(raw_header)
            stream.write(fmt.pack(ticks,*values));frames+=1
            if first_t is None:first_t=t
            last_t=t
        if not part:
            part=1;destination=original.with_name(original.stem+'_FDA0001.das');partial=Path(str(destination)+'.part')
            stream=partial.open('wb');stream.write(raw_header)
        finish();return files
    finally:
        if stream is not None:stream.close()
        if partial is not None:partial.unlink(missing_ok=True)
        db.close();Path(temp).unlink(missing_ok=True)


def plot_rows(path,metadata=None,signal_ids=None,start_ms=None,end_ms=None):
    """Decode packed frames in blocks, without constructing timestamps/text rows."""
    path=Path(path);meta=metadata or {};wanted=None if signal_ids is None else set(signal_ids)
    with path.open('rb') as stream:
        h=read_header(stream);fmt=frame_struct(h)
        if fmt.size!=h['record_size']:raise ValueError('Dimensione record FDA non valida')
        payload=path.stat().st_size-h['data_offset']
        if payload<0 or payload%fmt.size:raise ValueError('Record FDA troncato')
        definitions=[]
        for s in meta.get('fda_channels') or channel_map(h):
            if wanted is not None and s['nodeid'] not in wanted:continue
            i=s.get('value_channel');qi=s.get('quality_channel')
            if not isinstance(i,int) or not 1<=i<len(h['channels']) or qi is not None and (not isinstance(qi,int) or not 1<=qi<len(h['channels'])):raise ValueError('Mappa canali FDA non valida')
            c=h['channels'][i];definitions.append((s['nodeid'],i,qi,c['gain'],c['offset'],s.get('display_name') or c['name']))
        if not definitions:return
        first=0;last=payload//fmt.size
        if meta.get('fda_chronological'):
            def lower(bound,inclusive):
                lo,hi=0,last
                while lo<hi:
                    mid=(lo+hi)//2;stream.seek(h['data_offset']+mid*fmt.size)
                    t=h['epoch']*1000+struct.unpack('<I',stream.read(4))[0]
                    if t<bound or inclusive and t==bound:lo=mid+1
                    else:hi=mid
                return lo
            if start_ms is not None:first=lower(start_ms,False)
            if end_ms is not None:last=lower(end_ms,True)
        stream.seek(h['data_offset']+first*fmt.size);remaining=max(0,last-first);previous=None
        while remaining:
            count=min(8192,max(1,4*1024**2//fmt.size),remaining);raw=stream.read(count*fmt.size)
            if len(raw)!=count*fmt.size:raise ValueError('Record FDA troncato')
            remaining-=count
            for values in fmt.iter_unpack(raw):
                t=h['epoch']*1000+values[0];period=meta.get('initial_sample_ms') or (t-previous if previous is not None and t>previous else 0);previous=t
                if start_ms is not None and t<start_ms or end_ms is not None and t>end_ms:continue
                for nid,i,qi,gain,bias,name in definitions:
                    q=QUALITY.get(values[qi] if qi is not None else 3,'Bad');v=values[i]*gain+bias
                    if not math.isfinite(v):q='Bad'
                    yield nid,t,v if q=='Good' else None,q,name,'Double',period
