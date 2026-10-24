"""Space Analyzer Primary Capture v1: typed JSON blocks, CRC32 and safe recovery.

The format never evaluates objects or deserializes executable Python values.
Original files are not rewritten by recovery; a verified prefix is copied.
"""
import base64
from contextlib import contextmanager
from datetime import date,datetime
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import shutil
import struct
import uuid
import zlib

MAGIC=b'SACAP001';VERSION=1
FILE=struct.Struct('<8sIII')
BLOCK=struct.Struct('<4sBBHQQIII')
IPC=struct.Struct('<BQQQI')
DATA=1;EVENT=2;FINISH=3;FLUSH=4
MAX_HEADER=8*1024**2;MAX_PAYLOAD=32*1024**2
FIELDS=['timestamp_ms','received_ns','channel','connection_id','sample_sequence','connection_sequence',
        'source_epoch','source_sequence','value','data_type','quality','status','status_code','source_ts',
        'server_ts','source_filetime','local_ts','source_value','source_picoseconds','server_picoseconds',
        'acquisition_origin','sample_ms','clock_discontinuity','acquisition_timestamp_ms','source_binary']


def typed(value):
    if value is None or type(value) in (bool,str):return value
    if type(value) is int:return {'$int':str(value)} if abs(value)>2**53 else value
    if type(value) is float:return value if math.isfinite(value) else {'$float_bits':struct.pack('>d',value).hex()}
    if isinstance(value,(bytes,bytearray,memoryview)):return {'$bytes':base64.b64encode(value).decode('ascii')}
    if isinstance(value,datetime):return {'$datetime':value.isoformat(),'fold':value.fold}
    if isinstance(value,date):return {'$date':value.isoformat()}
    if isinstance(value,Decimal):return {'$decimal':str(value)}
    if isinstance(value,uuid.UUID):return {'$uuid':str(value)}
    if isinstance(value,tuple):return {'$tuple':[typed(v) for v in value]}
    if isinstance(value,list):return [typed(v) for v in value]
    if isinstance(value,dict):return {'$map':[[typed(k),typed(v)] for k,v in value.items()]}
    raise TypeError('Tipo primario non supportato: '+type(value).__name__)


def untyped(value):
    if isinstance(value,list):return [untyped(v) for v in value]
    if not isinstance(value,dict):return value
    if '$int' in value:return int(value['$int'])
    if '$float_bits' in value:return struct.unpack('>d',bytes.fromhex(value['$float_bits']))[0]
    if '$bytes' in value:return base64.b64decode(value['$bytes'],validate=True)
    if '$datetime' in value:return datetime.fromisoformat(value['$datetime']).replace(fold=int(value.get('fold',0)))
    if '$date' in value:return date.fromisoformat(value['$date'])
    if '$decimal' in value:return Decimal(value['$decimal'])
    if '$uuid' in value:return uuid.UUID(value['$uuid'])
    if '$tuple' in value:return tuple(untyped(v) for v in value['$tuple'])
    if '$map' in value:return {untyped(k):untyped(v) for k,v in value['$map']}
    raise ValueError('Tipo primario sconosciuto')


def json_bytes(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')


def sync_directory(path):
    if os.name=='nt':return False # Directory fsync is not exposed by Python on Windows.
    fd=os.open(str(path),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return True


def atomic_json(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(tmp,path);sync_directory(path.parent)


class WriterActive(BlockingIOError):pass


@contextmanager
def ownership(path,blocking=False):
    """An OS lock, automatically released on crash; no PID-reuse heuristic."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);stream=path.open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            stream.seek(0,os.SEEK_END)
            if stream.tell()==0:stream.write(b'\0');stream.flush()
            stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK,1)
            except OSError as exc:raise WriterActive(str(exc)) from exc
        else:
            import fcntl
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as exc:raise WriterActive(str(exc)) from exc
        yield
    finally:stream.close()


def write_header(stream,metadata):
    payload=json_bytes({**metadata,'format':'Space Analyzer Primary Capture','version':VERSION,'fields':FIELDS})
    if len(payload)>MAX_HEADER:raise ValueError('Metadata primari troppo grandi')
    stream.write(FILE.pack(MAGIC,VERSION,len(payload),zlib.crc32(payload)));stream.write(payload)


def read_header(stream):
    raw=stream.read(FILE.size)
    if len(raw)!=FILE.size:raise ValueError('Header primario troncato')
    magic,version,size,crc=FILE.unpack(raw)
    if magic!=MAGIC or version!=VERSION or not 0<size<=MAX_HEADER:raise ValueError('Formato/versione primario non supportato')
    payload=stream.read(size)
    if len(payload)!=size or zlib.crc32(payload)!=crc:raise ValueError('Checksum header primario non valido')
    meta=json.loads(payload)
    if meta.get('fields')!=FIELDS or not isinstance(meta.get('channels'),list):raise ValueError('Schema primario non valido')
    return meta


def write_block(stream,kind,sequence,first,count,payload,compress=True):
    if len(payload)>MAX_PAYLOAD:raise ValueError('Blocco primario oltre il limite')
    raw_size=len(payload);flags=int(compress and raw_size>1024)
    stored=zlib.compress(payload,1) if flags else payload
    prefix=BLOCK.pack(b'SAB1',kind,flags,0,sequence,first,count,len(stored),raw_size)
    stream.write(prefix);stream.write(struct.pack('<I',zlib.crc32(stored,zlib.crc32(prefix))));stream.write(stored)


def iter_blocks(stream,*,validate_rows=True):
    expected=1;next_sample=1
    while True:
        offset=stream.tell();raw=stream.read(BLOCK.size)
        if not raw:return
        if len(raw)!=BLOCK.size:raise ValueError('Header blocco incompleto a '+str(offset))
        magic,kind,flags,reserved,seq,first,count,size,unpacked=BLOCK.unpack(raw)
        if magic!=b'SAB1' or kind not in (DATA,EVENT,FINISH) or flags not in (0,1) or reserved or seq!=expected or first!=next_sample:
            raise ValueError('Sequenza/schema blocco non valido a '+str(offset))
        if not 0<size<=MAX_PAYLOAD or not 0<unpacked<=MAX_PAYLOAD:raise ValueError('Dimensione blocco non valida')
        crc=stream.read(4);payload=stream.read(size)
        if len(crc)!=4 or len(payload)!=size:raise ValueError('Payload blocco incompleto a '+str(offset))
        if zlib.crc32(payload,zlib.crc32(raw))!=struct.unpack('<I',crc)[0]:raise ValueError('Checksum blocco non valido a '+str(offset))
        if flags:
            decoder=zlib.decompressobj();payload=decoder.decompress(payload,MAX_PAYLOAD+1)
            if len(payload)>MAX_PAYLOAD or not decoder.eof or decoder.unused_data:raise ValueError('Compressione blocco non valida')
        if len(payload)!=unpacked:raise ValueError('Lunghezza blocco non valida')
        value=json.loads(payload)
        if kind==DATA:
            if not isinstance(value,list) or len(value)!=count or any(not isinstance(row,list) or len(row)!=len(FIELDS) for row in value):raise ValueError('Conteggio/schema campioni non valido')
        elif count:raise ValueError('Blocco di controllo con campioni')
        yield dict(kind=kind,sequence=seq,first_sample=first,samples=count,value=value,end=stream.tell(),offset=offset)
        expected+=1;next_sample+=count


def inspect(path):
    path=Path(path);count=blocks=0;last=None;error=''
    with path.open('rb') as stream:
        meta=read_header(stream);valid=stream.tell();finished=False
        try:
            for block in iter_blocks(stream):
                if finished:raise ValueError('Dati dopo finalizzazione')
                if block['kind']==DATA:
                    for values in block['value']:
                        channel=values[2]
                        if type(channel) is not int or not 0<=channel<len(meta['channels']):raise ValueError('Canale primario non valido')
                finished=block['kind']==FINISH
                blocks+=1;count+=block['samples'];valid=block['end'];last=block
        except (ValueError,zlib.error,UnicodeError,json.JSONDecodeError) as exc:error=str(exc)
    return dict(metadata=meta,samples=count,blocks=blocks,valid_bytes=valid,file_bytes=path.stat().st_size,
                discarded_bytes=path.stat().st_size-valid,error=error,
                finalized=bool(last and last['kind']==FINISH),footer=last['value'] if last and last['kind']==FINISH else None,
                footer_offset=last['offset'] if last and last['kind']==FINISH else None)


def iter_samples(path):
    with Path(path).open('rb') as stream:
        meta=read_header(stream);channels=meta['channels']
        for block in iter_blocks(stream):
            if block['kind']!=DATA:continue
            for values in block['value']:
                row=dict(zip(FIELDS,values));index=row.pop('channel')
                if type(index) is not int or not 0<=index<len(channels):raise ValueError('Canale primario non valido')
                row['nodeid']=channels[index]['nodeid'];row['value']=untyped(row['value']);row['source_value']=untyped(row['source_value']);row['source_binary']=untyped(row['source_binary'])
                yield row


def recover(path,output=None):
    path=Path(path)
    try:
        with ownership(str(path)+'.lock'):
            report=inspect(path)
            if report['finalized'] and not report['error']:
                # A crash may occur between footer fsync and the final rename.
                integrity=report['footer'].get('integrity','unknown')
            else:integrity='recovered_incomplete'
            output=Path(output) if output else path.with_name(path.name.removesuffix('.part')+'.recovered.sac')
            marker=path.with_name(path.name+'.recovery.json')
            fingerprint=[path.stat().st_size,path.stat().st_mtime_ns]
            if marker.is_file():
                previous=json.loads(marker.read_text('utf-8'))
                if previous.get('source_fingerprint')==fingerprint and Path(previous.get('output','')).is_file():return previous
            if output.exists():output=output.with_name(output.stem+'_'+uuid.uuid4().hex[:8]+output.suffix)
            temporary=output.with_name(output.name+'.recovery_'+uuid.uuid4().hex+'.part')
            with path.open('rb') as source,temporary.open('xb') as target:
                remaining=report['footer_offset'] if report['finalized'] and report['error'] else report['valid_bytes']
                while remaining:
                    chunk=source.read(min(1024**2,remaining))
                    if not chunk:raise ValueError('Sorgente modificata durante recovery')
                    target.write(chunk);remaining-=len(chunk)
                if not report['finalized'] or report['error']:
                    write_block(target,FINISH,report['blocks']+(0 if report['finalized'] else 1),report['samples']+1,0,json_bytes(dict(integrity=integrity,
                        reason='crash_or_incomplete_tail',recovered_samples=report['samples'],discarded_bytes=report['discarded_bytes'],
                        unknown_uncommitted_loss=True)),False)
                target.flush();os.fsync(target.fileno())
            if [path.stat().st_size,path.stat().st_mtime_ns]!=fingerprint:raise ValueError('Sorgente modificata durante recovery')
            os.replace(temporary,output);sync_directory(output.parent)
            result={k:v for k,v in report.items() if k not in ('metadata','footer')}
            result.update(source=str(path),output=str(output),source_fingerprint=fingerprint,integrity=integrity,
                          unknown_uncommitted_loss=integrity!='complete',recovered=True)
            atomic_json(marker,result);return result
    except WriterActive as exc:
        return dict(source=str(path),recovered=False,state='writer_active',error=str(exc))
