from primary_format import typed
"""Read-only MQTT acquisition: Paho network loop and a bounded decoding worker.

Only incoming publications produce physical samples. No value polling, holding,
interpolation or broker writes. Topic/JSON addresses are stable across sessions.
"""
import asyncio
import base64
import json
import math
import queue
import re
import secrets
import ssl
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

DEFAULTS = dict(mqtt_host='127.0.0.1', mqtt_port=1883, mqtt_version='3.1.1',
    mqtt_client_id='', mqtt_username='', mqtt_password='', mqtt_tls=False,
    mqtt_ca_file='', mqtt_cert_file='', mqtt_key_file='', mqtt_keepalive=30,
    mqtt_timeout_ms=5000, mqtt_topics='#', mqtt_qos=1, mqtt_root='mqtt:/',
    mqtt_timestamp_pointer='', mqtt_timestamp_unit='iso', mqtt_time_basis='arrival',
    mqtt_quality_pointer='', mqtt_include_retained=False,
    mqtt_queue=2048, mqtt_max_payload_kb=256)
MAX_CATALOG = 10000
MAX_LEAVES = 4096
MAX_QUEUE_BYTES = 32 * 1024 * 1024


def integer(value, name, low, high):
    try:
        n = float(value)
        if isinstance(value, bool) or not math.isfinite(n) or not n.is_integer() or not low <= n <= high:
            raise ValueError()
        return int(n)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'{name}: intero richiesto tra {low} e {high}') from None


def pointer_tokens(pointer):
    if not isinstance(pointer, str) or len(pointer) > 2048 or '\0' in pointer:
        raise ValueError('Percorso JSON MQTT non valido')
    if not pointer:
        return []
    if not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
        raise ValueError('Usare un JSON Pointer: /value, /machine/pressure, ~1 per / e ~0 per ~')
    return [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]


def pointer_value(data, pointer):
    value = data
    for token in pointer_tokens(pointer):
        if isinstance(value, list) and token.isdigit() and str(int(token)) == token:
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise KeyError(pointer)
    return value


def topic_name(topic):
    if not isinstance(topic, str) or not topic or any(c in topic for c in ('\0', '#', '+')) or len(topic.encode('utf-8')) > 2048:
        raise ValueError('Topic MQTT concreto richiesto, senza + o #')
    return topic


def address(topic, pointer=''):
    topic_name(topic); pointer_tokens(pointer)
    nid='mqtt:' + topic + '#' + pointer
    if len(nid)>4096:raise ValueError('Indirizzo MQTT troppo lungo')
    return nid


def parse_address(nid, allow_folder=False):
    if not isinstance(nid, str) or not nid.startswith('mqtt:') or len(nid) > 4096:
        raise ValueError('Indirizzo MQTT richiesto: mqtt:plant/topic#/value')
    if allow_folder and nid == 'mqtt:/':
        return dict(nodeid=nid, folder=True, topic=None)
    if allow_folder and nid.startswith('mqtt:topic:') and '#' not in nid:
        try:
            encoded = nid[len('mqtt:topic:'):]
            topic = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode('utf-8')
            if topic_folder(topic) != nid:
                raise ValueError()
            return dict(nodeid=nid, folder=True, topic=topic_name(topic))
        except (ValueError, UnicodeError):
            raise ValueError('Cartella topic MQTT non valida') from None
    if '#' not in nid[5:]:
        raise ValueError('MQTT: usare mqtt:plant/topic# per payload semplice o mqtt:plant/topic#/value per JSON')
    topic, pointer = nid[5:].split('#', 1)
    topic_name(topic); pointer_tokens(pointer)
    return dict(nodeid=nid, folder=False, topic=topic, pointer=pointer)


def topic_folder(topic):
    return 'mqtt:topic:' + base64.urlsafe_b64encode(topic.encode('utf-8')).decode('ascii').rstrip('=')


def topic_filters(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 16384:
        raise ValueError('Specificare almeno un filtro topic MQTT, uno per riga')
    filters = list(dict.fromkeys(p.strip() for p in value.splitlines() if p.strip()))
    if len(filters) > 32:
        raise ValueError('MQTT: massimo 32 filtri topic')
    for topic in filters:
        if '\0' in topic or len(topic.encode('utf-8')) > 2048:
            raise ValueError('Filtro topic MQTT troppo lungo o non valido')
        levels = topic.split('/')
        if any('+' in p and p != '+' or '#' in p and (p != '#' or i != len(levels)-1) for i, p in enumerate(levels)):
            raise ValueError('MQTT: + deve occupare un livello e # deve essere l’ultimo livello')
    return filters


def validate_config(cfg):
    for key, default in DEFAULTS.items():
        cfg.setdefault(key, default)
    for key in ('mqtt_host','mqtt_client_id','mqtt_username','mqtt_password','mqtt_ca_file','mqtt_cert_file','mqtt_key_file'):
        if not isinstance(cfg[key], str) or '\0' in cfg[key] or len(cfg[key]) > 4096:
            raise ValueError('Parametro MQTT non valido: ' + key)
    cfg['mqtt_host'] = cfg['mqtt_host'].strip()
    if not cfg['mqtt_host'] or any(c in cfg['mqtt_host'] for c in '/\\@ \r\n'):
        raise ValueError('MQTT: inserire solo IP/hostname del broker, senza schema o credenziali')
    for key in ('mqtt_tls','mqtt_include_retained'):
        if not isinstance(cfg[key], bool):
            raise ValueError(key + ': valore booleano richiesto')
    for key, low, high in [('mqtt_port',1,65535),('mqtt_qos',0,2),('mqtt_keepalive',5,3600),
                         ('mqtt_timeout_ms',1000,30000),('mqtt_queue',32,16384),('mqtt_max_payload_kb',1,8192)]:
        cfg[key] = integer(cfg[key], key, low, high)
    if cfg['mqtt_version'] not in ('3.1.1','5') or cfg['mqtt_time_basis'] not in ('arrival','payload'):
        raise ValueError('Versione o base tempi MQTT non valida')
    if cfg['mqtt_timestamp_unit'] not in ('iso','s','ms','us','ns'):
        raise ValueError('Unità timestamp MQTT non valida')
    for key in ('mqtt_timestamp_pointer','mqtt_quality_pointer'):
        pointer_tokens(cfg[key])
    if cfg['mqtt_time_basis'] == 'payload' and not cfg['mqtt_timestamp_pointer']:
        raise ValueError('Base tempi payload: indicare il JSON Pointer del timestamp')
    if cfg['mqtt_password'] and not cfg['mqtt_username']:
        raise ValueError('MQTT: indicare anche lo username')
    if bool(cfg['mqtt_cert_file']) != bool(cfg['mqtt_key_file']):
        raise ValueError('MQTT TLS client: servono certificato e chiave privata insieme')
    topic_filters(cfg['mqtt_topics'])
    parse_address(cfg['mqtt_root'], True)
    return cfg


def endpoint(cfg):
    host = cfg['mqtt_host']
    if ':' in host: host = '[' + host.strip('[]') + ']'
    return ('mqtts' if cfg['mqtt_tls'] else 'mqtt') + f'://{host}:{cfg["mqtt_port"]}'


def timestamp(value, unit):
    if unit == 'iso':
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('Timestamp MQTT ISO 8601 con fuso orario richiesto')
        d = datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
        elapsed = d - datetime(1970,1,1,tzinfo=timezone.utc)
        fraction = re.search(r'\.(\d+)', value)
        extra = int((fraction.group(1)[6:] if fraction else '').ljust(3,'0') or '0')
        ns = (elapsed.days*86400+elapsed.seconds)*1_000_000_000 + elapsed.microseconds*1000 + extra
    else:
        if isinstance(value, bool): raise ValueError('Timestamp MQTT booleano non valido')
        try:
            ns = Decimal(str(value)) * {'s':10**9,'ms':10**6,'us':10**3,'ns':1}[unit]
            if not ns.is_finite() or ns != ns.to_integral_value(): raise ValueError()
            ns = int(ns)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError('Timestamp MQTT numerico non valido per l’unità scelta') from None
    if not 0 <= ns < 7_258_118_400 * 10**9:
        raise ValueError('Timestamp MQTT fuori intervallo 1970–2199')
    sec, nano = divmod(ns, 10**9)
    text = datetime.fromtimestamp(sec,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{nano:09d}+00:00'
    return sec*1000+nano/1e6, text


def flatten(data):
    out=[];visited=0
    def visit(path,value,depth):
        nonlocal visited
        visited+=1
        if depth > 32 or visited > 16384:
            raise ValueError('JSON MQTT troppo profondo o con troppi campi')
        if isinstance(value, dict):
            for k,v in value.items():visit(path+'/'+str(k).replace('~','~0').replace('/','~1'),v,depth+1)
        elif isinstance(value, list):
            for i,v in enumerate(value):visit(path+'/'+str(i),v,depth+1)
        else:
            if len(out)>=MAX_LEAVES:raise ValueError('JSON MQTT con troppi campi')
            pointer_tokens(path); out.append((path,value))
    visit('',data,0)
    return out


def value_row(nid, value, quality, status, source_ts, received_ms):
    kind = value_type(value)
    numeric = isinstance(value,(int,float)) and (not isinstance(value,float) or math.isfinite(value))
    if isinstance(value,float) and not math.isfinite(value): quality,status='Bad','MQTT valore non finito'
    if value is None:
        if quality!='Bad':status='MQTT valore null o campo assente'
        quality='Bad'
    try:plot = float(value) if numeric and quality=='Good' else None
    except OverflowError:plot=None;quality,status='Bad','MQTT numero oltre il limite grafico'
    if plot is not None and not math.isfinite(plot): plot=None;quality,status='Bad','MQTT numero oltre il limite grafico'
    precision_warning=isinstance(value,int) and not isinstance(value,bool) and abs(value)>2**53
    safe=str(value) if precision_warning or isinstance(value,float) and not math.isfinite(value) else value
    return dict(nodeid=nid,value=safe,_source_typed=typed(value),plot_value=plot,numeric=numeric,good=quality=='Good',quality=quality,
                status=status,data_type=kind,status_code=None,source_ts=source_ts,server_ts=None,
                local_ts=datetime.fromtimestamp(received_ms/1000,timezone.utc).isoformat(timespec='microseconds'),
                received_ms=received_ms,precision_warning=precision_warning)


def value_type(value):
    if isinstance(value,bool):return 'Boolean'
    if isinstance(value,int):return 'Int64' if -(2**63)<=value<2**63 else 'UInt64' if 0<=value<2**64 else 'Integer'
    return 'Double' if isinstance(value,float) else 'String' if isinstance(value,str) else 'Null'


class MQTTClient:
    def __init__(self,cfg):
        self.cfg=dict(cfg);self.guard=threading.RLock();self.done=threading.Condition(self.guard)
        self.queue=queue.Queue(maxsize=cfg['mqtt_queue']);self.stop_event=threading.Event();self.ready=threading.Event()
        self.lost=threading.Event();self.client=None;self.consumer=None;self.selection=None;self.sink=None
        self.catalog={};self.catalog_revision=0;self.catalog_session=secrets.token_hex(8);self.latest={};self.error='';self.connection_error='';self.queue_bytes=0;self.serial=0;self.processed_serial=0
        self.last_received=None;self.last_arrival=None;self.periods=deque(maxlen=512);self.topic_times={};self.sample_periods=deque(maxlen=256)
        self.counts={k:0 for k in ('received','processed','invalid_payloads','retained','retained_excluded','dup_flag',
            'queue_dropped','oversized','catalog_limited','decoded_values','selected_values','late_samples')}
        self.info=dict(mode='mqtt',transport='MQTT '+cfg['mqtt_version']+(' TLS' if cfg['mqtt_tls'] else ' TCP'),
            read_mode='MQTT · ogni pubblicazione',read_only=True,endpoint=endpoint(cfg),topics=topic_filters(cfg['mqtt_topics']),
            qos=cfg['mqtt_qos'],clean_session=True,timestamps='Timestamp JSON' if cfg['mqtt_time_basis']=='payload' else 'Ricezione locale',
            quality_origin='Payload finito e comunicazione; qualità JSON solo se configurata')

    async def connect(self):
        try:
            await asyncio.wait_for(asyncio.to_thread(self._connect),self.cfg['mqtt_timeout_ms']/1000+1)
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        except Exception as exc:
            await self.disconnect()
            raise ValueError(str(exc) or 'Connessione MQTT non riuscita') from None

    def _connect(self):
        try: import paho.mqtt.client as mqtt
        except ImportError as exc: raise ValueError('Supporto MQTT non disponibile: usare l’EXE completo') from exc
        deadline=time.monotonic()+self.cfg['mqtt_timeout_ms']/1000
        class BoundedTLSClient(mqtt.Client):
            # Paho 2.1 uses keepalive for the TLS handshake and can leak the
            # wrapped socket on certificate failure. Bound/close it explicitly.
            def _ssl_wrap_socket(inner,tcp_sock):
                wrapped=None
                try:
                    wrapped=inner._ssl_context.wrap_socket(tcp_sock,server_hostname=inner._host,do_handshake_on_connect=False)
                    wrapped.settimeout(max(.05,deadline-time.monotonic()));wrapped.do_handshake()
                    return wrapped
                except BaseException:
                    (wrapped or tcp_sock).close()
                    raise
        c=BoundedTLSClient(mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.cfg['mqtt_client_id'] or 'SpaceAnalyzer-'+secrets.token_hex(8),
            protocol=mqtt.MQTTv5 if self.cfg['mqtt_version']=='5' else mqtt.MQTTv311,
            reconnect_on_failure=False,manual_ack=True)
        self.client=c;c.connect_timeout=self.cfg['mqtt_timeout_ms']/1000
        c.on_connect=self._on_connect;c.on_subscribe=self._on_subscribe;c.on_disconnect=self._on_disconnect;c.on_message=self._on_message
        if self.cfg['mqtt_username']:c.username_pw_set(self.cfg['mqtt_username'],self.cfg['mqtt_password'])
        if self.cfg['mqtt_tls']:
            context=ssl.create_default_context(cafile=self.cfg['mqtt_ca_file'] or None)
            context.minimum_version=ssl.TLSVersion.TLSv1_2
            if self.cfg['mqtt_cert_file']:
                def encrypted_key_not_supported():
                    raise ValueError('MQTT TLS: usare una chiave client PEM che non richieda password interattiva')
                context.load_cert_chain(self.cfg['mqtt_cert_file'],self.cfg['mqtt_key_file'],password=encrypted_key_not_supported)
            c.tls_set_context(context)
        c.connect(self.cfg['mqtt_host'],self.cfg['mqtt_port'],self.cfg['mqtt_keepalive'])
        if self.stop_event.is_set():
            c.disconnect();raise ConnectionError('Connessione MQTT annullata')
        c.loop_start()
        if not self.ready.wait(max(0,deadline-time.monotonic())):raise TimeoutError('MQTT: timeout CONNACK/SUBACK del broker')
        if self.connection_error:raise ConnectionError(self.connection_error)

    def _on_connect(self,c,userdata,flags,reason,properties):
        if self.stop_event.is_set():
            c.disconnect();self.ready.set();return
        if reason.is_failure:
            self.connection_error='MQTT: connessione rifiutata dal broker ('+str(reason)+')';self.ready.set();return
        result,_=c.subscribe([(topic,self.cfg['mqtt_qos']) for topic in topic_filters(self.cfg['mqtt_topics'])])
        if result:
            self.connection_error='MQTT: impossibile inviare SUBSCRIBE';self.ready.set()

    def _on_subscribe(self,c,userdata,mid,reasons,properties):
        if not reasons or any(r.is_failure for r in reasons):self.connection_error='MQTT: sottoscrizione rifiutata, verificare ACL/topic/QoS'
        else:self.info['granted_qos']=[r.value for r in reasons]
        self.ready.set()

    def _on_disconnect(self,c,userdata,flags,reason,properties):
        if not self.stop_event.is_set():
            if not self.connection_error:self.connection_error='MQTT: collegamento al broker interrotto ('+str(reason)+')'
            self.lost.set();self.ready.set()

    def _on_message(self,c,userdata,message):
        received_mono_ns=time.monotonic_ns();received_ns=time.time_ns();received_ms=received_ns/1e6
        snapshot=self.selection() if self.selection else None
        with self.guard:
            self.counts['received']+=1
            self.counts['retained']+=int(message.retain);self.counts['dup_flag']+=int(message.dup)
            if self.last_arrival is not None:self.periods.append(received_ms-self.last_arrival)
            self.last_arrival=received_ms;self.last_received=time.monotonic()
            size=len(message.payload)
            if size>self.cfg['mqtt_max_payload_kb']*1024:
                self.counts['oversized']+=1;self.error='MQTT: payload oltre il limite configurato';self.lost.set();return
            if self.queue_bytes+size>MAX_QUEUE_BYTES or self.queue.full():
                self.counts['queue_dropped']+=1;self.error='MQTT: coda di acquisizione piena; pubblicazioni non acquisite';self.lost.set();return
            self.serial+=1;serial=self.serial;self.queue_bytes+=size
            self.queue.put_nowait((serial,message.topic,bytes(message.payload),received_ms,bool(message.retain),bool(message.dup),message.mid,message.qos,snapshot,received_ns,received_mono_ns))

    def configure_acquisition(self,selection,sink):
        self.selection=selection;self.sink=sink
        if self.consumer is None:
            self.consumer=threading.Thread(target=self._consume,name='MQTT-Acquire',daemon=True);self.consumer.start()

    def _consume(self):
        try:
            while not self.stop_event.is_set():
                try:item=self.queue.get(timeout=.1)
                except queue.Empty:continue
                serial,topic,payload,received_ms,retained,dup,mid,qos,snapshot,received_ns,received_mono_ns=item
                try:
                    self._decode(topic,payload,received_ms,retained,dup,snapshot,received_ns,received_mono_ns)
                    if qos:self.client.ack(mid,qos)
                finally:
                    with self.done:
                        self.queue_bytes-=len(payload);self.processed_serial=serial;self.counts['processed']+=1;self.done.notify_all()
                    self.queue.task_done()
        except Exception as exc:
            self.error='MQTT: elaborazione interrotta ('+type(exc).__name__+')';self.lost.set()
            with self.done:self.done.notify_all()

    def _decode(self,topic,payload,received_ms,retained,dup,snapshot,received_ns=None,received_mono_ns=None):
        topic_name(topic)
        nids,period,generation=snapshot if snapshot is not None else self.selection() if self.selection else ([],self.cfg.get('sample_ms',10),None)
        selected=[]
        for nid in nids:
            tag=parse_address(nid)
            if tag['topic']==topic:selected.append(tag)
        quality='Good';status='MQTT DUP' if dup else 'Good';source_ts=None;ts=received_ms;error=''
        try:
            text=payload.decode('utf-8')
            try:data=json.loads(text)
            except json.JSONDecodeError:
                if text.lstrip().startswith(('{','[')):raise ValueError('JSON MQTT non valido')
                data=text
            leaves=flatten(data)
            if self.cfg['mqtt_timestamp_pointer']:
                source_ms,source_ts=timestamp(pointer_value(data,self.cfg['mqtt_timestamp_pointer']),self.cfg['mqtt_timestamp_unit'])
                if self.cfg['mqtt_time_basis']=='payload':ts=source_ms
            if self.cfg['mqtt_quality_pointer']:
                raw=pointer_value(data,self.cfg['mqtt_quality_pointer'])
                if not isinstance(raw,str) or raw.lower() not in ('good','uncertain','bad'):raise ValueError('Qualità JSON: atteso Good, Uncertain o Bad')
                quality=raw.capitalize();status='MQTT JSON '+quality
        except (ValueError,KeyError,IndexError,TypeError,OverflowError,RecursionError) as exc:
            leaves=[];data=None;quality='Bad';status='MQTT payload non valido: '+str(exc)[:160];error=status
            with self.guard:self.counts['invalid_payloads']+=1
            if self.cfg['mqtt_time_basis']=='payload':
                # Do not fabricate a source timestamp or poison its live timeline.
                self.error='MQTT: payload/timestamp non valido sulla base tempi sorgente';self.lost.set()
                return
        if retained:
            if quality=='Good':quality='Uncertain'
            status='MQTT retained · '+status
        with self.guard:
            for path,value in leaves:
                nid=address(topic,path)
                if nid not in self.catalog and len(self.catalog)>=MAX_CATALOG:
                    self.counts['catalog_limited']+=1;continue
                name=pointer_tokens(path)[-1] if path else topic.rstrip('/').split('/')[-1] or topic
                kind=value_type(value)
                metadata=dict(nodeid=nid,display_name=name,browse_name=name,node_class='Variable',has_children=False,
                    data_type=kind,path=topic+path,comment=topic+' · JSON Pointer '+(path or '(payload intero)'))
                if self.catalog.get(nid)!=metadata:
                    self.catalog[nid]=metadata
                    self.catalog_revision+=1
            self.counts['decoded_values']+=len(leaves)
            before=self.topic_times.get(topic)
            if before is not None and 0<ts-before<=60000:self.sample_periods.append(ts-before)
            if before is not None or len(self.topic_times)<MAX_CATALOG:
                self.topic_times[topic]=max(before,ts) if before is not None else ts
        rows=[]
        for tag in selected:
            try:value=pointer_value(data,tag['pointer']) if not error else None
            except (KeyError,IndexError,TypeError):value=None
            original_value=value
            if isinstance(value,(dict,list)):
                value=json.dumps(value,ensure_ascii=False,separators=(',',':'))
            row=value_row(tag['nodeid'],value,quality,status,source_ts,received_ms)
            if isinstance(original_value,(dict,list)):
                row['_source_typed']=typed(original_value);row['_source_data_type']='JSONObject' if isinstance(original_value,dict) else 'JSONArray'
            row['received_ns']=received_ns or int(received_ms*1000000)
            row['_received_mono_ns']=received_mono_ns or time.monotonic_ns()
            rows.append(row)
        with self.guard:self.latest.update({r['nodeid']:r for r in rows})
        if retained and not self.cfg['mqtt_include_retained']:
            with self.guard:self.counts['retained_excluded']+=1
            return
        if rows and self.sink:
            with self.guard:self.counts['selected_values']+=len(rows)
            self.sink(ts,rows,self.sample_period_ms(period),generation)

    def flush(self,timeout=5):
        deadline=time.monotonic()+timeout
        with self.done:
            target=self.serial
            while self.processed_serial<target:
                if not self.consumer or not self.consumer.is_alive():return False
                remaining=deadline-time.monotonic()
                if remaining<=0:return False
                self.done.wait(min(.1,remaining))
        return True

    async def disconnect(self):
        if self.consumer and not await asyncio.to_thread(self.flush,2):
            self.error=self.error or 'MQTT: coda non drenata prima della disconnessione; campioni non acquisiti'
            self.lost.set()
        self.stop_event.set()
        if self.client:
            self.client.disconnect()
            await asyncio.to_thread(self.client.loop_stop)
        if self.consumer:await asyncio.to_thread(self.consumer.join,2)

    async def read_state(self):
        if self.error or self.lost.is_set() or not self.client or not self.client.is_connected():
            raise ConnectionError(self.error or self.connection_error or 'MQTT non connesso')
        if self.consumer is not None and not self.consumer.is_alive():raise ConnectionError('MQTT: elaborazione terminata')
        thread=getattr(self.client,'_thread',None)
        if thread is None or not thread.is_alive():raise ConnectionError('MQTT: thread di rete terminato')

    async def inspect(self,nid):
        tag=parse_address(nid,True)
        if tag['folder']:
            return dict(nodeid=nid,display_name=tag['topic'] or 'Topic MQTT osservati',node_class='Object',has_children=True)
        with self.guard:
            known=self.catalog.get(nid)
            if known:return dict(known)
        name=pointer_tokens(tag['pointer'])[-1] if tag['pointer'] else tag['topic'].split('/')[-1]
        return dict(nodeid=nid,display_name=name,node_class='Variable',has_children=False,path=tag['topic']+tag['pointer'],comment='In attesa della prima pubblicazione')

    async def browse(self,nid):
        tag=parse_address(nid,True)
        with self.guard:
            if not tag['folder']:return []
            if tag['topic'] is not None:
                return [dict(v) for k,v in self.catalog.items() if parse_address(k)['topic']==tag['topic']]
            topics=sorted({parse_address(k)['topic'] for k in self.catalog})
        return [await self.inspect(topic_folder(topic)) for topic in topics]

    async def scan_items(self,root):
        tag=parse_address(root,True)
        if not tag['folder']:return [await self.inspect(root)]
        with self.guard:
            return [dict(v) for k,v in self.catalog.items() if not tag['topic'] or parse_address(k)['topic']==tag['topic']]

    def sample_period_ms(self,fallback=10):
        with self.guard:
            values=sorted(self.sample_periods)
            return max(1,min(60000,round(values[len(values)//2] if values else fallback)))

    def diagnostics(self):
        with self.guard:
            periods=list(self.periods)
            return dict(self.counts,queue_samples=self.queue.qsize(),queue_capacity=self.queue.maxsize,queue_bytes=self.queue_bytes,
                queue_bytes_limit=MAX_QUEUE_BYTES,catalog_signals=len(self.catalog),
                age_ms=(time.monotonic()-self.last_received)*1000 if self.last_received is not None else None,
                arrival_period_ms=sum(periods)/len(periods) if periods else None,
                sample_period_ms=self.sample_period_ms(),error=self.error or self.connection_error,
                timestamp_origin=self.info['timestamps'])
