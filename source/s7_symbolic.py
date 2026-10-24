"""Read-only S7CommPlus adapter. Wire I/O runs on one dedicated worker.

The pinned provider owns EXPLORE, LIDs, SymbolCRC and TLS/session authentication.
This adapter never emits variable writes or CPU RUN/STOP commands.
"""
import asyncio
import math
import socket
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from s7commplus import Client, S7CommPlusConnection
from s7_catalog import SymbolTree, parse_node, value_row
from s7_client import SCALARS


class TimedClient(Client):
    """Supply the public Connection timeout missing from Client.connect in 0.1.0.

The small construction hook deliberately targets the pinned provider version.
Its family-key handling and catalog refresh remain in the provider's Client.
"""
    def __init__(self, timeout):
        super().__init__(); self.timeout=timeout

    def _open_connection_once(self, fingerprint=None):
        p=self._connect_params
        if p is None: raise ConnectionError('Sessione S7 simbolica chiusa')
        self._connection=S7CommPlusConnection(host=p['host'],port=p['port'])
        self._connection.connect(timeout=self.timeout,use_tls=p['use_tls'],tls_cert=p['tls_cert'],
            tls_key=p['tls_key'],tls_ca=p['tls_ca'],password=p['password'] or '',
            legacy_session_key_refresh_interval=p['legacy_session_key_refresh_interval'],
            _session_key_fingerprint=fingerprint)
        if p['password'] is not None and self._connection.tls_active and not self._connection.requires_substreamed:
            self._connection.authenticate(p['password'])


def decode_symbol(kind, raw):
    if kind in ('BOOL','BBOOL'):
        if len(raw)!=1: raise ValueError('BOOL simbolico di lunghezza inattesa')
        return bool(raw[0])
    aliases={'TIMEOFDAY':'UDINT','DATE':'UINT','S5TIME':'WORD','LTIME':'LINT','LTOD':'ULINT','LDT':'LINT'}
    scalar=aliases.get(kind,kind)
    if scalar in SCALARS:
        fmt,size=SCALARS[scalar]
        if len(raw)!=size: raise ValueError(f'{kind}: lunghezza {len(raw)}, attesa {size}')
        value=struct.unpack('>'+fmt,raw)[0]
        if isinstance(value,float) and not math.isfinite(value): raise ValueError('Valore PLC non finito')
        return value
    if kind in ('STRING','WSTRING'):
        header=2 if kind=='STRING' else 4
        if len(raw)<header: raise ValueError('Stringa S7 troncata')
        maximum,length=(raw[0],raw[1]) if kind=='STRING' else struct.unpack('>HH',raw[:4])
        size=length*(1 if kind=='STRING' else 2)
        if length>maximum or header+size>len(raw): raise ValueError('Lunghezza stringa S7 incoerente')
        return raw[header:header+size].decode('cp1252' if kind=='STRING' else 'utf-16-be',errors='replace')
    if kind=='CHAR': return raw.decode('cp1252',errors='replace')
    if kind=='WCHAR': return raw.decode('utf-16-be',errors='replace')
    # DateAndTime/DTL are retained exactly as text; not mislabelled as numeric trend.
    return raw.hex().upper()


class S7SymbolicClient:
    def __init__(self,cfg):
        self.cfg=dict(cfg); self.client=TimedClient(cfg['s7_timeout_ms']/1000)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='S7-symbolic')
        self.lock=asyncio.Lock(); self.catalog_lock=asyncio.Lock()
        self.tree=None; self.catalog={}; self.closed=False
        self.info=dict(transport='S7CommPlus · simbolico',read_mode='S7 simbolico · lettura aggregata',
            mode='symbolic',provider='s7commplus 0.1.0 (beta)',read_only=True,
            timestamps='Timestamp locale di ricezione; GetMultiVariables non fornisce il timestamp PLC',
            quality_origin='Esito lettura simbolica; stato RUN/STOP non interrogato')

    async def _call(self,fn,*args):
        if self.closed: raise ConnectionError('Connessione S7 simbolica chiusa')
        async with self.lock:
            return await asyncio.get_running_loop().run_in_executor(self.executor,lambda:fn(*args))

    async def connect(self):
        cfg=self.cfg
        for key in ('s7_tls_ca','s7_tls_cert','s7_tls_key'):
            if cfg[key] and not Path(cfg[key]).is_file(): raise ValueError('File TLS non trovato: '+cfg[key])
        try:
            await self._call(lambda:self.client.connect(cfg['s7_host'],port=cfg['s7_tcp_port'],
                use_tls=cfg['s7_symbolic_tls'],tls_ca=cfg['s7_tls_ca'] or None,
                tls_cert=cfg['s7_tls_cert'] or None,tls_key=cfg['s7_tls_key'] or None,
                password=cfg['s7_symbolic_password'] or None,
                allow_legacy_key_fallback=cfg['s7_legacy_key_fallback']))
            self.info.update(host=cfg['s7_host'],port=cfg['s7_tcp_port'],
                version=self.client.protocol_version,tls=self.client.tls_active,
                certificate_verified=bool(cfg['s7_tls_ca']) if self.client.tls_active else False,
                protection_level=self.client.protection_level,symbols=0)
        except BaseException:
            await self.disconnect(); raise

    async def disconnect(self):
        if self.closed: return
        self.closed=True
        # Abort outstanding socket I/O before serializing final session teardown.
        self.client._connect_params=None
        connection=self.client._connection
        if connection:
            sock=getattr(connection._iso_conn,'socket',None)
            if sock:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        try:
            await asyncio.get_running_loop().run_in_executor(self.executor,self.client.disconnect)
        finally: self.executor.shutdown(wait=False,cancel_futures=True)

    async def ensure_catalog(self,refresh=False):
        async with self.catalog_lock:
            if self.tree is not None and not refresh: return
            await self._load_catalog()

    async def _load_catalog(self):
        def load():
            catalog=self.client.refresh_tag_catalog()
            items=[]
            for tag in catalog:
                if len(items)>=50000: raise ValueError('Il PLC espone oltre 50000 simboli: restringere il programma da acquisire')
                nid='s7:sym:'+tag.name
                parse_node(nid)
                items.append(dict(nodeid=nid,display_name=tag.name.rsplit('.',1)[-1],
                    browse_name=tag.name,path=tag.name,data_type=tag.softdatatype.name,
                    node_class='Variable',has_children=False,
                    comment=tag.name+' · '+tag.softdatatype.name))
            return SymbolTree(items)
        tree=await self._call(load)
        self.tree=tree; self.catalog=tree.catalog; self.info['symbols']=len(self.catalog)

    async def read_state(self):
        if not self.client.connected or self.closed: raise ConnectionError('Sessione S7CommPlus interrotta')

    async def check_symbols(self): pass

    async def inspect(self,nid):
        node=parse_node(nid,True)
        if node['mode']!='symbolic': raise ValueError('In modalità simbolica scegliere un simbolo S7')
        await self.ensure_catalog()
        if node['folder']:
            if node['name'] and node['name'] not in self.tree.children: raise ValueError('Ramo simbolico non trovato nel PLC')
            return dict(nodeid=nid,display_name=node['name'].rsplit('.',1)[-1] or 'Simboli CPU S7',
                path=node['name'],node_class='Object',has_children=True)
        if nid not in self.catalog: raise ValueError('Simbolo non trovato nel PLC: '+node['name'])
        return dict(self.catalog[nid])

    async def browse(self,nid):
        node=parse_node(nid,True); await self.inspect(nid)
        return self.tree.browse(node['name']) if node['folder'] else []

    async def scan_items(self,root):
        await self.inspect(root)
        return self.tree.scan(parse_node(root,True))

    async def read_values(self,nids):
        await self.ensure_catalog()
        for nid in nids:
            if parse_node(nid)['mode']!='symbolic': raise ValueError('Segnale incompatibile con S7 simbolico')
        def read():
            rows={}; known=[n for n in nids if n in self.catalog]
            # Bound one request by both item count and encoded LID path length.
            batch=[]; budget=0
            def finish():
                if not batch: return
                result=self.client.read_tags([parse_node(n)['name'] for n in batch])
                for nid,r in zip(batch,result):
                    kind=r.tag.softdatatype.name
                    if r.error: rows[nid]=value_row(nid,kind,error=str(r.error),quality='Bad')
                    else:
                        try: rows[nid]=value_row(nid,kind,decode_symbol(kind,r.value))
                        except (ValueError,TypeError,struct.error) as exc:
                            rows[nid]=value_row(nid,kind,error=str(exc),quality='Bad')
                batch.clear()
            for nid in known:
                tag=self.client.resolve_tag(parse_node(nid)['name']); cost=32+5*len(tag.lids)
                if batch and (len(batch)>=20 or budget+cost>1200): finish(); budget=0
                batch.append(nid); budget+=cost
            finish()
            local=datetime.now(timezone.utc).isoformat()
            for nid in nids:
                row=rows.get(nid) or value_row(nid,'Null',error='Simbolo non presente nel catalogo CPU',quality='Bad')
                row['local_ts']=local; rows[nid]=row
            return [rows[n] for n in nids]
        return await self._call(read)
