"""Independent SSH2 workers; bounded console, explicit host trust, no credential persistence."""
import base64
import codecs
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import logging
import math
from pathlib import Path
import re
import secrets
import socket
import threading
import time
import paramiko

DEFAULTS=dict(host='192.168.5.220',port=22,username='Administrator',auth='password',connect_timeout=10,keepalive=20,encoding='utf-8')
MAX_TEXT=128*1024
def now():return datetime.now(timezone.utc).isoformat(timespec='milliseconds')
def integer(value,lo,hi,label):
    try:
        n=float(value)
        if isinstance(value,bool) or not math.isfinite(n) or not n.is_integer() or not lo<=n<=hi:raise ValueError()
    except (ValueError,TypeError):raise ValueError(f'{label}: intero tra {lo} e {hi}') from None
    return int(n)
def profile(data):
    if not isinstance(data,dict):raise ValueError('Parametri SSH non validi')
    cfg={k:data.get(k,v) for k,v in DEFAULTS.items()}
    for key in ('host','username'):
        if not isinstance(cfg[key],str) or not cfg[key].strip() or len(cfg[key])>253 or re.search(r'[\s/\x00-\x1f]',cfg[key]):raise ValueError('SSH: IP/hostname e utente richiesti, senza spazi o URL')
        cfg[key]=cfg[key].strip()
    cfg['port']=integer(cfg['port'],1,65535,'Porta SSH');cfg['connect_timeout']=integer(cfg['connect_timeout'],2,60,'Timeout SSH');cfg['keepalive']=integer(cfg['keepalive'],5,300,'Keepalive SSH')
    if cfg['auth'] not in ('password','key'):raise ValueError('Autenticazione SSH: password o chiave')
    if cfg['encoding'] not in ('utf-8','cp1252','latin-1'):raise ValueError('Codifica SSH non supportata')
    return cfg
def fingerprint(key):return 'SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
def private_key(text,passphrase):
    if not isinstance(text,str) or not 1<=len(text.encode('utf-8'))<=MAX_TEXT:raise ValueError('Chiave privata OpenSSH/PEM richiesta (max 128 KiB)')
    if 'PuTTY-User-Key-File' in text:raise ValueError('Chiave PPK: esportarla in OpenSSH/PEM con PuTTYgen')
    for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
        try:return cls.from_private_key(io.StringIO(text),password=passphrase or None)
        except paramiko.PasswordRequiredException:raise ValueError('La chiave privata richiede una passphrase') from None
        except (paramiko.SSHException,ValueError,TypeError):pass
    raise ValueError('Chiave privata non supportata o passphrase errata: usare RSA, Ed25519 o ECDSA OpenSSH/PEM')

class SSHService:
    def __init__(self,directory,output_limit=2*1024*1024):
        self.directory=Path(directory);self.lock=threading.RLock();self.transport=None;self.channel=None
        self.conn_thread=None;self.run_thread=None;self.generation=0;self.cancel=threading.Event();self.stop_event=threading.Event();self.trust_event=threading.Event()
        self.trust_decision=False;self.pending=None;self.state='disconnected';self.error='';self.target={};self.job={};self.last_config=dict(DEFAULTS)
        self.events=deque();self.output_bytes=0;self.output_limit=output_limit;self.seq=0;self.dropped_bytes=0
    def _read(self,name,default):
        p=self.directory/name
        if not p.exists():return default
        try:
            data=json.loads(p.read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError()
            return data
        except (ValueError,OSError):raise ValueError('Impostazioni/chiavi SSH non leggibili: verificare '+name) from None
    def _write(self,name,data):
        self.directory.mkdir(parents=True,exist_ok=True);p=self.directory/name;tmp=p.with_name(p.name+'.'+secrets.token_hex(8)+'.tmp')
        try:tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8');tmp.chmod(0o600);tmp.replace(p)
        finally:tmp.unlink(missing_ok=True)
    def config(self):
        with self.lock:return profile(self._read('ssh_profile.json',DEFAULTS))
    def save_config(self,data):
        cfg=profile(data)
        with self.lock:self._write('ssh_profile.json',cfg)
        return cfg
    @staticmethod
    def _host_id(cfg):return json.dumps([cfg['host'].lower(),cfg['port']],separators=(',',':'))
    def known_key(self,cfg):
        with self.lock:return self._read('ssh_known_hosts.json',{}).get(self._host_id(cfg))
    def forget_key(self,data):
        cfg=profile(data)
        with self.lock:
            if self.state in ('connected','connecting','awaiting_trust'):raise ValueError('Disconnetti SSH prima di rimuovere la chiave salvata')
            keys=self._read('ssh_known_hosts.json',{});item=keys.get(self._host_id(cfg))
            if not item or data.get('fingerprint')!=item.get('fingerprint'):raise ValueError('Chiave cambiata/non trovata: aggiornare lo stato')
            del keys[self._host_id(cfg)];self._write('ssh_known_hosts.json',keys)
    def connect(self,data):
        cfg=profile(data);credentials={}
        if cfg['auth']=='password':
            password=data.get('password')
            if not isinstance(password,str) or not 1<=len(password)<=4096:raise ValueError('Inserire la password SSH')
            credentials['password']=password
        else:
            phrase=data.get('passphrase','')
            if not isinstance(phrase,str) or len(phrase)>4096:raise ValueError('Passphrase non valida')
            credentials['pkey']=private_key(data.get('private_key'),phrase)
        with self.lock:
            if self.state in ('connected','connecting','awaiting_trust') or (self.conn_thread and self.conn_thread.is_alive()):raise ValueError('Sessione SSH già aperta/in corso: disconnettere prima')
            self.save_config(cfg);self.last_config=cfg;self.generation+=1;generation=self.generation
            self.cancel=threading.Event();self.trust_event=threading.Event();self.trust_decision=False;self.pending=None;self.target={};self.state='connecting';self.error=''
            self.conn_thread=threading.Thread(target=self._connect,args=(cfg,credentials,generation,self.cancel,self.trust_event),name='SSH-Connect',daemon=True);self.conn_thread.start()
        return self.status()
    def _connect(self,cfg,credentials,generation,cancel,trust_event):
        transport=None;sock=None;success=False
        try:
            sock=socket.create_connection((cfg['host'],cfg['port']),timeout=cfg['connect_timeout']);transport=paramiko.Transport(sock)
            transport.banner_timeout=cfg['connect_timeout'];transport.auth_timeout=cfg['connect_timeout']
            with self.lock:
                if generation!=self.generation or cancel.is_set():return
                self.transport=transport
            transport.start_client(timeout=cfg['connect_timeout'])
            if not transport.is_active():raise ConnectionError('Handshake SSH2 non completato')
            key=transport.get_remote_server_key();fp=fingerprint(key);public=dict(algorithm=key.get_name(),key=key.get_base64(),fingerprint=fp)
            with self.lock:
                if generation!=self.generation or cancel.is_set():return
                known=self.known_key(cfg);self.target=dict(host=cfg['host'],port=cfg['port'],username=cfg['username'],fingerprint=fp,algorithm=key.get_name(),server_version=transport.remote_version)
                if known and (known.get('algorithm')!=key.get_name() or not hmac.compare_digest(known.get('key',''),key.get_base64())):raise ValueError('Chiave host SSH diversa da quella salvata. Verifica il target; rimuovi esplicitamente la vecchia chiave solo se il cambio è previsto.')
                if not known:self.state='awaiting_trust';self.pending={**self.target,'id':secrets.token_urlsafe(24)}
            if not known:
                if not trust_event.wait(120):raise ValueError('Verifica impronta SSH scaduta: ripetere la connessione')
                with self.lock:
                    if generation!=self.generation or cancel.is_set():return
                    if not self.trust_decision:raise ValueError('Chiave host SSH non accettata')
                    keys=self._read('ssh_known_hosts.json',{});keys[self._host_id(cfg)]={**public,'accepted_at':now()};self._write('ssh_known_hosts.json',keys);self.pending=None;self.state='connecting'
            if cancel.is_set():return
            if cfg['auth']=='password':transport.auth_password(cfg['username'],credentials['password'])
            else:transport.auth_publickey(cfg['username'],credentials['pkey'])
            if not transport.is_authenticated():raise ValueError('Autenticazione incompleta: MFA non supportata')
            transport.set_keepalive(cfg['keepalive'])
            with self.lock:
                if generation!=self.generation or cancel.is_set():return
                self.state='connected';self.target.update(connected_at=now(),cipher=transport.remote_cipher);self.error=''
            logging.info('SSH connected host=%s port=%s',cfg['host'],cfg['port']);success=True;transport=None
        except Exception as exc:
            if isinstance(exc,paramiko.AuthenticationException):message='Autenticazione SSH non riuscita: controllare utente, password/chiave e permessi del server'
            elif isinstance(exc,(socket.timeout,TimeoutError)):message='Timeout SSH: controllare IP, porta, rete e servizio sshd'
            elif isinstance(exc,OSError):message='Connessione SSH non disponibile: controllare IP, porta, rete e servizio sshd'
            elif isinstance(exc,ValueError):message=str(exc)
            else:message='Errore protocollo SSH2: '+str(exc)[:300]
            with self.lock:
                if generation==self.generation and not cancel.is_set():self.state='error';self.error=message;self.pending=None;self.transport=None
            logging.warning('SSH connection failed type=%s',type(exc).__name__)
        finally:
            credentials.clear()
            if transport:
                with suppress(Exception):transport.close()
            elif sock and (not success or cancel.is_set()):
                with suppress(Exception):sock.close()
    def trust(self,data):
        with self.lock:
            if self.state!='awaiting_trust' or not self.pending or data.get('id')!=self.pending['id'] or data.get('fingerprint')!=self.pending['fingerprint']:raise ValueError('Verifica SSH scaduta: aggiornare lo stato')
            if type(data.get('accept')) is not bool:raise ValueError('Specificare accetta o rifiuta chiave')
            self.trust_decision=data['accept'];self.trust_event.set()
    def disconnect(self):
        with self.lock:
            self.generation+=1;self.cancel.set();self.trust_event.set();self.stop_event.set();transport,self.transport=self.transport,None;channel,self.channel=self.channel,None
            self.state='disconnected';self.pending=None;self.error=''
            if self.job.get('state') in ('starting','running'):self.job.update(state='cancelled',ended_at=now(),reason='disconnected')
        if channel:
            with suppress(Exception):channel.close()
        if transport:
            with suppress(Exception):transport.close()
    def close(self):
        self.disconnect()
        for thread in (self.conn_thread,self.run_thread):
            if thread and thread is not threading.current_thread():thread.join(timeout=5)
    def _append(self,job,stream,text):
        if not text:return
        with self.lock:
            self.seq+=1;self.events.append(dict(seq=self.seq,job_id=job,stream=stream,text=text,at=now()));self.output_bytes+=len(text.encode('utf-8'))
            while self.events and (self.output_bytes>self.output_limit or len(self.events)>8192):
                n=len(self.events.popleft()['text'].encode('utf-8'));self.output_bytes-=n;self.dropped_bytes+=n
    def status(self,after=None):
        with self.lock:
            if self.state=='connected' and (not self.transport or not self.transport.is_active()):self.state='error';self.error='Collegamento SSH interrotto; riconnettere esplicitamente'
            r=dict(state=self.state,error=self.error,target=dict(self.target),pending=dict(self.pending) if self.pending else None,job=dict(self.job),last_seq=self.seq,first_seq=self.events[0]['seq'] if self.events else self.seq+1,dropped_bytes=self.dropped_bytes,output_limit_bytes=self.output_limit)
            if after is not None:
                after=integer(after,0,2**53-1,'Posizione output');events=[];size=0
                for item in self.events:
                    if item['seq']>after:
                        events.append(dict(item));size+=len(item['text'])
                        if len(events)>=200 or size>=128*1024:break
                r.update(events=events,next_seq=events[-1]['seq'] if events else max(after,self.seq),gap=bool(self.events and after<self.events[0]['seq']-1))
            return r
    def execute(self,data):
        mode=data.get('mode','command');text=data.get('text','');pty=data.get('pty',False);timeout=integer(data.get('timeout',300),0,86400,'Durata massima (s)')
        if mode not in ('command','script','shell') or type(pty) is not bool:raise ValueError('Modalità comando/script/shell non valida')
        if not isinstance(text,str) or '\0' in text or len(text.encode('utf-8'))>MAX_TEXT or (mode!='shell' and not text.strip()):raise ValueError('Inserire comando/script (max 128 KiB, senza NUL)')
        interpreter=data.get('interpreter','/bin/sh')
        if mode=='script' and (not isinstance(interpreter,str) or not re.fullmatch(r'/[A-Za-z0-9_./+-]{1,200}',interpreter)):raise ValueError('Interprete: percorso assoluto senza argomenti, es. /bin/sh')
        with self.lock:
            if self.status()['state']!='connected':raise ValueError('Connettere SSH prima di eseguire')
            if self.run_thread and self.run_thread.is_alive():raise ValueError('Un comando SSH è già in esecuzione: attendere o premere Stop')
            ident=secrets.token_hex(12);self.stop_event=threading.Event();self.job=dict(id=ident,mode=mode,pty=pty or mode=='shell',state='starting',started_at=now(),ended_at=None,exit_code=None,reason='',error='',bytes_received=0,timeout=timeout)
            self.run_thread=threading.Thread(target=self._execute,args=(ident,self.generation,self.transport,self.stop_event,mode,text,interpreter,pty,timeout),name='SSH-Console',daemon=True);self.run_thread.start();return dict(self.job)
    def _execute(self,ident,generation,transport,stop,mode,text,interpreter,pty,timeout):
        channel=None;deadline=time.monotonic()+timeout if timeout else float('inf');state='finished';reason='';error='';code=None;total=0
        expired=threading.Event()
        def expire():
            expired.set();stop.set()
            with self.lock:c=self.channel if self.job.get('id')==ident else None
            if c:
                with suppress(Exception):c.close()
        watchdog=threading.Timer(timeout,expire) if timeout else None
        if watchdog:watchdog.daemon=True;watchdog.start()
        decoders={s:codecs.getincrementaldecoder(self.last_config['encoding'])(errors='replace') for s in ('stdout','stderr')}
        try:
            channel=transport.open_session(timeout=min(self.last_config['connect_timeout'],timeout or 60));channel.settimeout(1)
            with self.lock:
                if generation!=self.generation or stop.is_set():state='cancelled';reason='stopped';return
                self.channel=channel
            if mode=='shell' or pty:channel.get_pty(term='dumb',width=120,height=40)
            if mode=='shell':channel.invoke_shell()
            else:channel.exec_command(interpreter+' -s' if mode=='script' else text)
            with self.lock:
                if generation!=self.generation or stop.is_set():state='cancelled';reason='stopped';return
                self.job['state']='running'
            pending=(text.replace('\r\n','\n').replace('\r','\n')+'\n').encode('utf-8') if mode=='script' else b'';offset=0;eof_sent=False
            while True:
                if stop.is_set():state='cancelled';reason='stopped';break
                if time.monotonic()>=deadline:state='timed_out';reason='timeout';break
                if mode=='script' and not eof_sent:
                    if offset<len(pending) and channel.send_ready():offset+=channel.send(pending[offset:offset+16384])
                    if offset>=len(pending):channel.shutdown_write();eof_sent=True
                activity=False
                for stream,ready,recv in (('stdout',channel.recv_ready,channel.recv),('stderr',channel.recv_stderr_ready,channel.recv_stderr)):
                    for _ in range(8):
                        if not ready():break
                        raw=recv(16384)
                        if not raw:break
                        total+=len(raw);self._append(ident,stream,decoders[stream].decode(raw));activity=True
                with self.lock:
                    if self.job.get('id')==ident:self.job['bytes_received']=total
                if (channel.exit_status_ready() or channel.closed or channel.eof_received) and not channel.recv_ready() and not channel.recv_stderr_ready():
                    if channel.exit_status_ready():
                        received=channel.recv_exit_status();code=received if received!=-1 else None
                    reason='completed' if code is not None else 'remote_eof';break
                if not transport.is_active():raise ConnectionError('Connessione SSH interrotta durante il comando')
                if not activity:stop.wait(.01)
            for stream in decoders:self._append(ident,stream,decoders[stream].decode(b'',final=True))
        except Exception as exc:
            if stop.is_set():state='timed_out' if expired.is_set() else 'cancelled';reason='timeout' if expired.is_set() else 'stopped'
            else:state='failed';error='Esecuzione SSH non riuscita: '+str(exc)[:300];reason='error'
            logging.warning('SSH execution failed type=%s',type(exc).__name__)
        finally:
            if watchdog:watchdog.cancel()
            if expired.is_set():state='timed_out';reason='timeout'
            if channel:
                with suppress(Exception):channel.close()
            with self.lock:
                if self.channel is channel:self.channel=None
                if self.job.get('id')==ident and self.job.get('state')!='cancelled':self.job.update(state=state,ended_at=now(),exit_code=code,reason=reason,error=error,bytes_received=total)
            logging.info('SSH job completed mode=%s state=%s exit=%s bytes=%s',mode,state,code,total)
    def stop(self,ident):
        with self.lock:
            if self.job.get('id')!=ident or self.job.get('state') not in ('starting','running'):raise ValueError('Comando SSH non più attivo')
            self.stop_event.set();channel=self.channel
        if channel:
            with suppress(Exception):channel.close()
    def send_input(self,data):
        text=data.get('text');kind=data.get('kind','line')
        if kind not in ('line','interrupt','eof') or (kind=='line' and (not isinstance(text,str) or len(text.encode('utf-8'))>4096 or '\0' in text)):raise ValueError('Input SSH: riga di massimo 4096 byte, Ctrl+C o EOF')
        with self.lock:
            channel=self.channel
            if data.get('id')!=self.job.get('id') or self.job.get('state')!='running' or not channel:raise ValueError('Comando/shell SSH non attivo')
            if self.job['mode']=='script':raise ValueError('Lo stdin dello script è riservato al contenuto dello script')
            if kind=='interrupt' and not self.job['pty']:raise ValueError('Ctrl+C richiede PTY; usare Stop per chiudere il canale')
            if kind=='eof':channel.shutdown_write();return
            raw=b'\x03' if kind=='interrupt' else (text+'\n').encode('utf-8')
        channel.sendall(raw)
    def clear(self):
        with self.lock:
            if self.job.get('state') in ('starting','running'):raise ValueError('Fermare il comando prima di cancellare la console')
            self.events.clear();self.output_bytes=0;self.dropped_bytes=0
