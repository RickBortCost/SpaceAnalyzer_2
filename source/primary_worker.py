"""Primary writer process, controlled only by inherited anonymous pipes."""
import json
import marshal
import os
from pathlib import Path
import queue
import sys
import threading
import time
from primary_format import DATA,EVENT,FINISH,FLUSH,IPC,MAX_PAYLOAD,atomic_json,json_bytes,ownership,sync_directory,write_header,write_block


def read_exact(fd,count):
    chunks=[]
    while count:
        block=os.read(fd,count)
        if not block:return b'' if not chunks else b''.join(chunks)
        chunks.append(block);count-=len(block)
    return b''.join(chunks)


def usage():
    result={'cpu_seconds':os.times().user+os.times().system,'rss_bytes':None}
    try:
        if sys.platform=='linux':
            result['rss_bytes']=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
        elif os.name=='nt':
            import ctypes as c
            class Counters(c.Structure):
                _fields_=[('cb',c.c_ulong),('faults',c.c_ulong)]+[(n,c.c_size_t) for n in ('peak','working','peak_page','page','peak_nonpage','nonpage','pagefile','peak_pagefile','private')]
            p=Counters();p.cb=c.sizeof(p)
            kernel=c.WinDLL('kernel32');kernel.GetCurrentProcess.restype=c.c_void_p
            api=c.WinDLL('psapi').GetProcessMemoryInfo;api.argtypes=[c.c_void_p,c.c_void_p,c.c_ulong];api.restype=c.c_int
            if api(kernel.GetCurrentProcess(),c.byref(p),p.cb):result['rss_bytes']=p.working
    except (OSError,ValueError,IndexError):pass
    return result


def send(value):
    data=memoryview(json_bytes(value)+b'\n')
    while data:
        written=os.write(sys.stdout.fileno(),data)
        if written<=0:raise BrokenPipeError('Pipe risposte chiusa')
        data=data[written:]


def run():
    first=read_exact(0,4)
    if len(first)!=4:raise ValueError('Configurazione writer mancante')
    import struct
    size=struct.unpack('<I',first)[0]
    if not 0<size<=8*1024**2:raise ValueError('Configurazione writer troppo grande')
    cfg=json.loads(read_exact(0,size));path=Path(cfg['path']);path.parent.mkdir(parents=True,exist_ok=True)
    status_path=Path(str(path)+'.status.json');messages=queue.Queue(maxsize=2)
    def receive():
        try:
            while True:
                header=read_exact(0,IPC.size)
                if len(header)!=IPC.size:messages.put(None);return
                kind,first,count,queued,size=IPC.unpack(header)
                if not 0<=size<=MAX_PAYLOAD:raise ValueError('Messaggio IPC troppo grande')
                payload=read_exact(0,size)
                if len(payload)!=size:messages.put(None);return
                messages.put((kind,first,count,queued,payload))
        except BaseException as exc:messages.put(exc)
    reader=threading.Thread(target=receive,name='primary-pipe',daemon=True)
    state=dict(state='recording',pid=os.getpid(),path=str(path),samples=0,blocks=0,durable_samples=0,
               bytes_written=0,last_flush_ns=None,error='',integrity='recording')
    stream=None;pending=[];last_flush=time.monotonic();interval=cfg['flush_ms']/1000
    marshalled=cfg.get('ipc_encoding')=='marshal'
    try:
        with ownership(str(path)+'.lock'):
            stream=path.open('xb');write_header(stream,cfg['metadata']);stream.flush();os.fsync(stream.fileno())
            atomic_json(status_path,state);send(dict(op='ready',**state));reader.start()
            def flush():
                nonlocal last_flush,pending
                stream.flush();os.fsync(stream.fileno());state['durable_samples']=state['samples']
                state['bytes_written']=stream.tell();state['last_flush_ns']=str(time.time_ns())
                state['usage']=usage();atomic_json(status_path,state)
                send(dict(op='flush',receipts=pending,**state));pending=[];last_flush=time.monotonic()
            while True:
                try:item=messages.get(timeout=max(.001,interval-(time.monotonic()-last_flush)))
                except queue.Empty:
                    if pending:flush()
                    else:last_flush=time.monotonic()
                    continue
                if isinstance(item,BaseException):raise item
                if item is None:
                    state.update(state='abandoned',integrity='incomplete',error='Controllore terminato: perdita RAM non quantificabile')
                    flush();send(dict(op='abandoned',**state));return
                kind,first,count,queued,payload=item
                if kind==DATA:
                    if count<1 or first!=state['samples']+1:raise ValueError('Sequenza IPC campioni non valida')
                    if marshalled:payload=json_bytes(marshal.loads(payload))
                    state['blocks']+=1;write_block(stream,DATA,state['blocks'],first,count,payload,cfg.get('compression',True))
                    state['samples']+=count;pending.append([queued,count])
                elif kind==EVENT:
                    state['blocks']+=1;write_block(stream,EVENT,state['blocks'],state['samples']+1,0,payload,False)
                elif kind==FLUSH:flush()
                elif kind==FINISH:
                    footer=json.loads(payload)
                    if footer.get('accepted_samples')!=state['samples']:raise ValueError('Conteggio finale campioni non valido')
                    state['blocks']+=1
                    write_block(stream,FINISH,state['blocks'],state['samples']+1,0,payload,False)
                    state['integrity']=footer.get('integrity','incomplete');flush();stream.close();stream=None
                    final=path.with_suffix('') if state['integrity']=='complete' else path.with_name(path.name.removesuffix('.sac.part')+'.incomplete.sac')
                    os.replace(path,final);sync_directory(final.parent);state.update(state='stopped' if state['integrity']=='complete' else 'error',path=str(final))
                    atomic_json(status_path,state);send(dict(op='finished',**state));return
                else:raise ValueError('Comando IPC sconosciuto')
                if time.monotonic()-last_flush>=interval:flush()
    except BaseException as exc:
        state.update(state='error',integrity='incomplete',error=str(exc))
        try:atomic_json(status_path,state)
        except OSError:pass
        try:send(dict(op='error',**state))
        except OSError:pass
        raise
    finally:
        if stream:
            try:stream.close()
            except OSError:pass

if __name__=='__main__':
    if os.name=='nt':
        import msvcrt
        msvcrt.setmode(0,os.O_BINARY);msvcrt.setmode(1,os.O_BINARY)
    try:run()
    except BaseException:raise SystemExit(2)
