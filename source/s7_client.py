from primary_format import typed
"""Read-only classic S7comm over ISO-on-TCP (TPKT/COTP), no native DLL.

Only Setup Communication (0xF0) and Read Var (0x04) jobs are emitted.
Absolute DB / M / I / Q addresses; no S7comm Plus or symbolic/optimized DB access.
"""
import asyncio
import math
import re
import struct
from contextlib import suppress
from datetime import datetime, timezone

DEFAULTS = dict(s7_host='192.168.0.1', s7_tcp_port=102, s7_rack=0, s7_slot=1,
                s7_family='1200/1500', s7_connection_type=3, s7_local_tsap='0100',
                s7_remote_tsap='', s7_timeout_ms=3000, s7_pdu=480,
                s7_root='s7:/', s7_tags=[])
SCALARS = {'BOOL':('B',1), 'BYTE':('B',1), 'USINT':('B',1), 'SINT':('b',1),
           'WORD':('H',2), 'UINT':('H',2), 'INT':('h',2), 'DWORD':('I',4),
           'UDINT':('I',4), 'DINT':('i',4), 'REAL':('f',4), 'LREAL':('d',8),
           'LINT':('q',8), 'ULINT':('Q',8), 'LWORD':('Q',8), 'TIME':('i',4)}
ERRORS = {1:'Errore hardware', 3:'Accesso negato: verificare autorizzazione PUT/GET',
          5:'Indirizzo fuori area: verificare DB, offset e dimensione',
          6:'Tipo/trasporto non supportato', 7:'Dimensione non coerente',
          10:'DB o area non disponibile: verificare DB standard non ottimizzato'}


def protocol_of(nid):
    return 'mqtt' if str(nid).startswith('mqtt:') else 's7' if str(nid).startswith('s7:') else 'ads' if str(nid).startswith('ads:') else 'opcua'


def parse_tag(value, allow_folder=False):
    if not isinstance(value, str): raise ValueError('Indirizzo S7 richiesto')
    value = value.strip()
    if value.startswith('s7:'): value = value[3:]
    value = value.upper()
    if allow_folder and re.fullmatch(r'/|/(?:DB[1-9]\d{0,4}|M|I|Q)', value):
        if value.startswith('/DB') and int(value[3:])>65535: raise ValueError('Numero DB massimo 65535')
        return {'nodeid':'s7:'+value, 'folder':True, 'group':value.lstrip('/')}
    match = re.fullmatch(r'(DB([1-9]\d{0,4})\.DB([XBWD])|(M|I|Q|E|A)([BWD]?))(\d{1,7})(?:\.([0-7]))?:([A-Z]+)(?:\[(\d{1,3})\])?', value)
    if not match: raise ValueError('S7: usare DB1.DBD0:REAL, DB1.DBX4.0:BOOL, MW10:INT oppure DB1.DBB20:STRING[40]')
    _, db, width, area, memory_width, offset, bit, kind, length = match.groups()
    db = int(db or 0); offset = int(offset); area = {'E':'I','A':'Q'}.get(area, area)
    width = width or memory_width or 'X'
    if db > 65535: raise ValueError('Numero DB massimo 65535')
    if kind == 'STRING':
        if length is None or not 1 <= int(length) <= 254: raise ValueError('STRING: lunghezza dichiarata da 1 a 254')
        size = int(length)+2
    elif kind in SCALARS and length is None: size = SCALARS[kind][1]
    else: raise ValueError('Tipo S7 non supportato: '+kind)
    if kind == 'BOOL':
        if width != 'X' or bit is None: raise ValueError('BOOL richiede un bit: DB1.DBX0.0 oppure M0.0')
    elif bit is not None or width == 'X': raise ValueError('Il tipo numerico richiede un indirizzo byte/word/dword senza bit')
    elif width != ('B' if size == 1 or kind == 'STRING' else 'W' if size == 2 else 'D'):
        raise ValueError('Larghezza indirizzo non coerente con il tipo: B=byte, W=16 bit, D=32/64 bit')
    if offset + size > 2097152: raise ValueError('Offset S7 oltre il campo indirizzi a 24 bit')
    group = 'DB'+str(db) if db else area
    address = f'DB{db}.DB{width}{offset}' if db else area + ('' if width=='X' else width) + str(offset)
    if bit is not None: address += '.'+bit
    datatype = kind + (f'[{int(length)}]' if length else '')
    return dict(nodeid='s7:'+address+':'+datatype, address=address, kind=kind, data_type=datatype,
                size=size, offset=offset, bit=int(bit or 0), db=db, group=group,
                area=0x84 if db else {'I':0x81,'Q':0x82,'M':0x83}[area])


def normalize_tags(tags):
    if not isinstance(tags, list) or len(tags)>5000: raise ValueError('Catalogo S7: massimo 5000 definizioni')
    result, seen = [], set()
    for row in tags:
        if not isinstance(row, dict): raise ValueError('Definizione variabile S7 non valida')
        tag = parse_tag(row.get('nodeid') or (str(row.get('address',''))+':'+str(row.get('data_type','REAL'))))
        if tag['nodeid'] in seen: raise ValueError('Variabile S7 duplicata: '+tag['nodeid'])
        seen.add(tag['nodeid'])
        name = str(row.get('display_name') or row.get('name') or tag['address']).strip()
        if not name or len(name)>160: raise ValueError('Nome variabile: da 1 a 160 caratteri')
        result.append(dict(nodeid=tag['nodeid'], display_name=name, data_type=tag['data_type']))
    return result


def validate_config(cfg):
    for key, value in DEFAULTS.items(): cfg.setdefault(key, value)
    if cfg['s7_family'] not in ('1200/1500','300','400','custom'): raise ValueError('Famiglia S7 non valida')
    host = cfg['s7_host']
    if not isinstance(host,str) or not host.strip() or any(c in host for c in '/\\\0 \r\n'):
        raise ValueError('S7: inserire IP o hostname del PLC')
    cfg['s7_host'] = host.strip()
    for key, lo, hi in [('s7_tcp_port',1,65535),('s7_rack',0,7),('s7_slot',0,31),
                        ('s7_connection_type',1,3),('s7_timeout_ms',100,10000),('s7_pdu',240,960)]:
        v=cfg[key]
        if isinstance(v,bool): raise ValueError(key+': numero intero richiesto')
        try:
            n=float(v)
            if not math.isfinite(n) or not n.is_integer() or not lo<=n<=hi: raise ValueError()
        except (ValueError,TypeError): raise ValueError(f'{key}: intero tra {lo} e {hi}') from None
        cfg[key]=int(n)
    for key in ('s7_local_tsap','s7_remote_tsap'):
        if not isinstance(cfg[key],str): raise ValueError(key+': testo richiesto')
        v=cfg[key].strip().upper().removeprefix('0X')
        if (key=='s7_local_tsap' or v) and not re.fullmatch('[0-9A-F]{4}',v):
            raise ValueError(key+': quattro cifre esadecimali, es. 0100')
        cfg[key]=v
    cfg['s7_root']=parse_tag(cfg['s7_root'] or 's7:/',True)['nodeid']
    cfg['s7_tags']=normalize_tags(cfg['s7_tags'])
    return cfg


def decode(tag, raw):
    if len(raw)!=tag['size']: raise ValueError('Lunghezza valore S7 inattesa')
    if tag['kind']=='STRING':
        maximum, length = raw[:2]
        if maximum != tag['size']-2 or length>maximum: raise ValueError('STRING: lunghezza dichiarata diversa dal DB')
        return raw[2:2+length].decode('cp1252',errors='replace')
    value=struct.unpack('>'+SCALARS[tag['kind']][0],raw)[0]
    return bool(value & (1<<tag['bit'])) if tag['kind']=='BOOL' else value


class S7Client:
    def __init__(self, cfg):
        self.cfg=dict(cfg); validate_config(self.cfg)
        self.reader=None; self.writer=None; self.sequence=0; self.pdu=240; self.lock=asyncio.Lock()
        self.info={'transport':'S7 ISO-on-TCP', 'read_mode':'S7 ReadVar · polling aggregato',
                   'timestamps':'Timestamp locale di ricezione; timestamp PLC non fornito da ReadVar',
                   'quality_origin':'Esito lettura S7; stato RUN/STOP CPU non interrogato', 'read_only':True}
        self.catalog={t['nodeid']:t for t in self.cfg['s7_tags']}

    async def disconnect(self):
        writer,self.writer=self.writer,None; self.reader=None
        if writer:
            writer.close()
            with suppress(Exception): await asyncio.wait_for(writer.wait_closed(),1)

    async def _packet(self):
        header=await self.reader.readexactly(4)
        version,reserved,length=struct.unpack('>BBH',header)
        if version!=3 or reserved or not 7<=length<=65535: raise ValueError('Header TPKT non valido')
        return await self.reader.readexactly(length-4)

    async def _send(self,payload):
        if not self.writer or self.writer.is_closing(): raise ConnectionError('Connessione S7 chiusa')
        self.writer.write(struct.pack('>BBH',3,0,len(payload)+4)+payload)
        await self.writer.drain()

    async def connect(self):
        cfg=self.cfg
        remote=int(cfg['s7_remote_tsap'],16) if cfg['s7_remote_tsap'] else cfg['s7_connection_type']*256+cfg['s7_rack']*32+cfg['s7_slot']
        try:
            async with asyncio.timeout(cfg['s7_timeout_ms']/1000):
                self.reader,self.writer=await asyncio.open_connection(cfg['s7_host'],cfg['s7_tcp_port'])
                # ISO transport connection request, class 0, TPDU size 1024.
                await self._send(b'\x11\xe0\x00\x00\x00\x01\x00\xc1\x02'+bytes.fromhex(cfg['s7_local_tsap'])+b'\xc2\x02'+struct.pack('>H',remote)+b'\xc0\x01\x0a')
                cc=await self._packet()
                if len(cc)<7 or cc[0]+1!=len(cc) or cc[1]!=0xd0 or cc[2:4]!=b'\x00\x01' or cc[6]!=0:
                    raise ConnectionError('Connessione COTP rifiutata: controllare rack, slot e TSAP')
            params,_=await self._job(b'\xf0\x00\x00\x01\x00\x01'+struct.pack('>H',cfg['s7_pdu']))
            if len(params)!=8 or params[:2]!=b'\xf0\x00': raise ValueError('Negoziazione PDU S7 non valida')
            self.pdu=struct.unpack_from('>H',params,6)[0]
            if not 128<=self.pdu<=cfg['s7_pdu']: raise ValueError('Dimensione PDU S7 inattesa')
            self.info.update(host=cfg['s7_host'],port=cfg['s7_tcp_port'],rack=cfg['s7_rack'],slot=cfg['s7_slot'],
                             local_tsap=cfg['s7_local_tsap'],remote_tsap=f'{remote:04X}',pdu=self.pdu)
        except TimeoutError as exc:
            await self.disconnect()
            raise ConnectionError('Timeout S7: verificare IP, porta TCP, rack/slot, TSAP e firewall') from exc
        except BaseException:
            await self.disconnect(); raise

    async def _job(self, parameters):
        async with self.lock:
            try:
                async with asyncio.timeout(self.cfg['s7_timeout_ms']/1000):
                    self.sequence=(self.sequence%65535)+1
                    header=struct.pack('>BBHHHH',0x32,1,0,self.sequence,len(parameters),0)
                    await self._send(b'\x02\xf0\x80'+header+parameters)
                    parts=[]; total=0
                    for _ in range(64):
                        packet=await self._packet()
                        if packet[:2]!=b'\x02\xf0': raise ValueError('Risposta COTP Data non valida')
                        total+=len(packet)-3
                        if total>max(self.pdu,240): raise ValueError('Risposta oltre la PDU negoziata')
                        parts.append(packet[3:])
                        if packet[2]&0x80: break
                    else: raise ValueError('Troppi segmenti COTP')
                    response=b''.join(parts)
                    if len(response)<12: raise ValueError('Risposta S7 troncata')
                    proto,rosctr,reserved,sequence,plen,dlen,errclass,errcode=struct.unpack_from('>BBHHHHBB',response)
                    if proto!=0x32 or rosctr!=3 or reserved or sequence!=self.sequence or len(response)!=12+plen+dlen:
                        raise ValueError('Header o sequenza S7 incoerente')
                    if errclass or errcode: raise ConnectionError(f'S7 errore 0x{errclass:02X}{errcode:02X}: verificare protezione CPU, PUT/GET e DB standard')
                    return response[12:12+plen],response[12+plen:]
            except TimeoutError as exc:
                await self.disconnect()
                raise ConnectionError('Timeout risposta S7: verificare rete, carico CPU e timeout configurato') from exc
            except BaseException:
                await self.disconnect(); raise

    async def read_state(self):
        if not self.writer or self.writer.is_closing() or self.reader.at_eof(): raise ConnectionError('Connessione S7 interrotta')

    async def check_symbols(self): pass

    async def inspect(self,nid):
        tag=parse_tag(nid,True)
        if tag.get('folder'):
            return dict(nodeid=tag['nodeid'],display_name=tag['group'] or 'Variabili S7 configurate',node_class='Object',has_children=True)
        name=self.catalog.get(tag['nodeid'],{}).get('display_name',tag['address'])
        return dict(nodeid=tag['nodeid'],display_name=name,browse_name=name,node_class='Variable',
                    has_children=False,data_type=tag['data_type'],path=tag['group']+'/'+name,
                    comment=tag['address']+' · '+tag['data_type'])

    async def browse(self,nid):
        tag=parse_tag(nid,True)
        if not tag.get('folder'): return []
        if not tag['group']:
            groups=sorted({parse_tag(n)['group'] for n in self.catalog})
            return [await self.inspect('s7:/'+group) for group in groups]
        return [await self.inspect(n) for n in self.catalog if parse_tag(n)['group']==tag['group']]

    async def read_values(self,nids):
        tags=[parse_tag(n) for n in nids]; rows=[]
        # A single large STRING may exceed a small negotiated PDU: split its byte reads.
        pieces=[]; payloads=[bytearray() for _ in tags]; failures={}
        for i,t in enumerate(tags):
            for offset in range(0,t['size'],self.pdu-18):
                pieces.append((i,t,t['offset']+offset,min(self.pdu-18,t['size']-offset)))
        cursor=0
        while cursor<len(pieces):
            batch=[]; response_size=14
            while cursor<len(pieces) and len(batch)<20:
                piece=pieces[cursor]; size=piece[3]
                if batch and (12+12*(len(batch)+1)>self.pdu or response_size+4+size+(size%2)>self.pdu): break
                batch.append(piece); response_size+=4+size+(size%2); cursor+=1
            request=b'\x04'+bytes([len(batch)])
            for _,tag,offset,size in batch:
                request+=b'\x12\x0a\x10\x02'+struct.pack('>HHB',size,tag['db'],tag['area'])+(offset*8).to_bytes(3,'big')
            params,data=await self._job(request)
            if params!=bytes([4,len(batch)]):
                await self.disconnect(); raise ValueError('Conteggio ReadVar S7 incoerente')
            pos=0
            try:
                for j,(i,tag,_,size) in enumerate(batch):
                    if pos+4>len(data): raise ValueError('ReadVar S7 troncato')
                    code,transport,length=struct.unpack_from('>BBH',data,pos); pos+=4
                    if code!=255:
                        # Error items have no payload (null transport). Some peers
                        # retain the requested byte count in the length field.
                        if transport: raise ValueError('Errore S7 con trasporto inatteso')
                        failures[i]=(code,ERRORS.get(code,'Errore lettura S7'))
                        continue
                    count=(length+7)//8 if transport in (3,4,5) else length if transport in (7,9) else -1
                    if count!=size or pos+count>len(data): raise ValueError('Lunghezza/trasporto ReadVar non valido')
                    payloads[i].extend(data[pos:pos+count]); pos+=count
                    if count%2 and j<len(batch)-1: pos+=1
                if pos!=len(data): raise ValueError('Dati ReadVar in eccesso o padding mancante')
            except BaseException:
                await self.disconnect(); raise
        local=datetime.now(timezone.utc).isoformat(timespec='microseconds')
        for i,tag in enumerate(tags):
            value=None; code,message=failures.get(i,(0,'S7_OK'))
            if not code:
                try: value=decode(tag,payloads[i])
                except ValueError as exc: code,message=0xffff,str(exc)
            numeric=isinstance(value,(int,float)) and math.isfinite(value)
            exact=not isinstance(value,int) or abs(value)<=9007199254740991
            safe=value if exact and (not isinstance(value,float) or math.isfinite(value)) else str(value)
            rows.append(dict(nodeid=tag['nodeid'],value=safe,_source_typed=typed(value),plot_value=float(value) if numeric and not code else None,
                             data_type=tag['data_type'],numeric=numeric,good=not code,quality='Bad' if code else 'Good',
                             status=message,status_code=code,error=message if code else '',precision_warning=not exact,
                             source_ts=None,server_ts=None,local_ts=local,protocol='s7',timestamp_origin='local read completion'))
        return rows
