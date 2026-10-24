from primary_format import typed
from ads_types import DatatypeBrowser, blocked_type
"""Read-only ADS/TCP client. No TwinCAT DLL or PLC writes are required.
Wire layout: Beckhoff ADS AdsLib/standalone/AdsDef.h and AmsHeader.
ADS Write is used exclusively to release previously acquired symbol handles.
"""
import asyncio
import ipaddress
import math
import re
import socket
import struct
from contextlib import suppress

MAX_PACKET = 64 * 1024 * 1024
MAX_VALUE = 1024 * 1024
ERRORS = {6:'Porta ADS non trovata: verificare il runtime (es. 851)',7:'Target AMS non trovato: verificare Net ID e route',
    13:'Porta AMS sorgente non valida',15:'Net ID AMS non valido',18:'Porta ADS disabilitata: verificare il servizio TwinCAT',
    1793:'Servizio non supportato',1794:'Index Group non valido',1795:'Index Offset non valido',
    1796:'Accesso non consentito',1797:'Dimensione non valida',1800:'Dispositivo occupato',
    1808:'Simbolo non trovato',1809:'Versione simboli cambiata',1810:'Stato ADS non valido',
    1811:'Modalità notifica non supportata',1812:'Handle notifica non valido',1813:'Client notifica sconosciuto',1817:'Timeout dispositivo',1826:'Simbolo non attivo',1861:'Timeout ADS'}
STATES = {0:'INVALID',1:'IDLE',2:'RESET',3:'INIT',4:'START',5:'RUN',6:'STOP',7:'SAVECFG',8:'LOADCFG',9:'POWERFAILURE',10:'POWERGOOD',11:'ERROR',12:'SHUTDOWN',13:'SUSPEND',14:'RESUME',15:'CONFIG',16:'RECONFIG',17:'STOPPING'}
# Size and signedness come from ADS data type, not names chosen by a PLC program.
SCALARS = {2:('h','Int16'),3:('i','Int32'),4:('f','Float'),5:('d','Double'),16:('b','SByte'),17:('B','Byte'),18:('H','UInt16'),19:('I','UInt32'),20:('q','Int64'),21:('Q','UInt64'),33:('?','Boolean')}
BASE_TYPES = {'BOOL':33,'BYTE':17,'SINT':16,'USINT':17,'INT':2,'UINT':18,'WORD':18,'DINT':3,'UDINT':19,'DWORD':19,'REAL':4,'LREAL':5,'LINT':20,'ULINT':21,'LWORD':21,'TIME':19,'TOD':19,'TIME_OF_DAY':19,'DATE':19,'DT':19,'DATE_AND_TIME':19,'LTIME':21}

class ADSError(Exception):
    def __init__(self, code, context='ADS'):
        self.code = code
        super().__init__(f'{context}: {ERRORS.get(code,"Errore ADS")} (0x{code:08X})')


def netid(value):
    try:
        parts = str(value).split('.')
        if len(parts)!=6 or any(not p.isdigit() for p in parts): raise ValueError()
        return bytes(int(p) for p in parts)
    except (ValueError, OverflowError):
        raise ValueError('AMS Net ID: servono 6 numeri tra 0 e 255, es. 192.168.1.10.1.1') from None


def symbol_name(nid):
    if not isinstance(nid,str) or not nid.startswith('ads:'): raise ValueError('Simbolo ADS richiesto: ads:MAIN.Temperatura')
    name=nid[4:]
    if not name or '\0' in name or len(name)>2048: raise ValueError('Nome simbolo ADS non valido')
    return name


def validate_config(cfg):
    if cfg.get('ads_transport','tcp') not in ('tcp','native'):raise ValueError('Trasporto ADS non valido')
    if not isinstance(cfg.get('ads_dll_path',''),str):raise ValueError('Percorso DLL non valido')
    if cfg.get('source') not in ('opcua','ads','s7','mqtt'): raise ValueError('Fonte dati non supportata')
    for key in ('ads_host','ads_target_netid','ads_source_netid','ads_bind_ip','ads_root'):
        if not isinstance(cfg[key],str): raise ValueError(f'{key}: testo richiesto')
        cfg[key]=cfg[key].strip()
    if cfg['source']=='ads':
        if not cfg['ads_host'] or any(c in cfg['ads_host'] for c in '/\\\0 '): raise ValueError('Inserire IP o hostname del dispositivo ADS')
        netid(cfg['ads_target_netid'])
    elif cfg['ads_target_netid']: netid(cfg['ads_target_netid'])
    if cfg['ads_source_netid']: netid(cfg['ads_source_netid'])
    if cfg['ads_bind_ip']: ipaddress.IPv4Address(cfg['ads_bind_ip'])
    for key,lo,hi in (('ads_port',1,65535),('ads_tcp_port',1,65535),('ads_source_port',1,65534),('ads_timeout_ms',100,10000)):
        try:
            n=float(cfg[key])
            if not math.isfinite(n) or not n.is_integer() or not lo<=n<=hi: raise ValueError()
            cfg[key]=int(n)
        except (TypeError,ValueError): raise ValueError(f'{key}: intero tra {lo} e {hi}') from None
    if cfg['ads_mode'] not in ('sum','individual','notify'): raise ValueError('Modalità ADS non valida')
    if cfg['source']=='ads' and cfg['ads_mode']=='notify' and cfg.get('ads_transport')!='native':raise ValueError('Le notifiche cicliche richiedono TwinCAT locale. Selezionare questo collegamento oppure SumRead per ADS/TCP.')
    if cfg['ads_encoding'] not in ('cp1252','utf-8'): raise ValueError('Codifica STRING non valida')
    if cfg['ads_root'] and not cfg['ads_root'].startswith('ads:'): cfg['ads_root']='ads:'+cfg['ads_root']
    cfg['ads_root']=cfg['ads_root'] or 'ads:/'
    symbol_name(cfg['ads_root'])
    return cfg


def parse_symbol(data, offset=0):
    if len(data)-offset<30: raise ValueError('Tabella simboli ADS troncata')
    length,group,addr,size,datatype,flags,n,t,c=struct.unpack_from('<IIIIIIHHH',data,offset)
    if length<33+n+t+c or offset+length>len(data): raise ValueError('Lunghezza simbolo ADS non valida')
    pos=offset+30
    texts=[]
    for count in (n,t,c):
        if data[pos+count]!=0: raise ValueError('Stringa simbolo ADS non terminata')
        texts.append(data[pos:pos+count].decode('utf-8',errors='replace'));pos+=count+1
    return dict(name=texts[0],type=texts[1],comment=texts[2],group=group,offset=addr,size=size,datatype=datatype,flags=flags),length


def array_shape(type_name):
    m=re.fullmatch(r'ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)',type_name.strip(),re.I)
    if not m:return None
    dims=[]
    for part in m[1].split(','):
        r=re.fullmatch(r'\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*',part)
        if not r:return None
        lo,hi=map(int,r.groups())
        if hi<lo:return None
        dims.append((lo,hi))
    return dims,m[2].strip()


_SCALAR_STRUCTS={}

def decode_value(data,symbol,encoding):
    datatype=symbol['datatype']
    if datatype in SCALARS:
        fmt,label=SCALARS[datatype];packer=_SCALAR_STRUCTS.get(fmt)
        if packer is None:packer=_SCALAR_STRUCTS[fmt]=struct.Struct('<'+fmt)
        if len(data)==packer.size:return packer.unpack(data)[0],label
    kind=symbol['type'].upper()
    if datatype==30:return data.split(b'\0',1)[0].decode(encoding,errors='replace'),'String'
    if datatype==31:
        end=next((i for i in range(0,len(data)-1,2) if data[i:i+2]==b'\0\0'),len(data))
        return data[:end].decode('utf-16-le',errors='replace'),'String'
    shape=array_shape(kind)
    if shape:
        dims,base=shape;dt=BASE_TYPES.get(base)
        if dt in SCALARS:
            fmt,_=SCALARS[dt];count=math.prod(hi-lo+1 for lo,hi in dims)
            if count<=65536 and len(data)==count*struct.calcsize('<'+fmt):return list(struct.unpack('<'+str(count)+fmt,data)),'Array'
    # Never interpret unknown structures, packed bits or extended precision as floats.
    return data.hex(),'Bytes ('+symbol['type']+')'


def symbol_item(s):
    return {'nodeid':'ads:'+s['name'],'display_name':s['name'],'browse_name':s['name'],'node_class':'Variable',
            'has_children':not blocked_type(s['type']) and (s['datatype']==65 or bool(array_shape(s['type']))),'path':s['name'],'data_type':s['type'],
            'size':s['size'],'comment':s['comment'],'index_group':s['group'],'index_offset':s['offset']}


class ADSClient:
    def __init__(self,cfg,catalog=False):
        self.cfg=dict(cfg);self.reader=None;self.writer=None;self.lock=asyncio.Lock();self.invoke=0
        self.target=netid(cfg['ads_target_netid'])+struct.pack('<H',cfg['ads_port'])
        self.source=None;self.catalog_session=catalog;self.symbols={};self.handles={};self.catalog_loaded=False
        self.datatypes=DatatypeBrowser(self);self.info={};self.symbol_version=None;self.sum_supported=True

    async def connect(self):
        try:
            local=(self.cfg['ads_bind_ip'],0) if self.cfg['ads_bind_ip'] else None
            self.reader,self.writer=await asyncio.wait_for(asyncio.open_connection(self.cfg['ads_host'],self.cfg['ads_tcp_port'],local_addr=local,family=socket.AF_INET),self.cfg['ads_timeout_ms']/1000)
            local_ip=self.writer.get_extra_info('sockname')[0]
            source_id=self.cfg['ads_source_netid'] or local_ip+'.1.1'
            self.source=netid(source_id)+struct.pack('<H',self.cfg['ads_source_port']+int(self.catalog_session))
            self.info={'source_netid':source_id,'source_port':self.cfg['ads_source_port']+int(self.catalog_session),'local_ip':local_ip,'target_netid':self.cfg['ads_target_netid'],'target_port':self.cfg['ads_port'],'transport':'ADS/TCP','timestamps':'Local only (ADS Read)'}
            device=await self.command(1,b'')
            self.check_result(device)
            if len(device)!=24:raise ValueError('ReadDeviceInfo ADS non valido')
            self.info.update(device=device[8:24].split(b'\0')[0].decode('utf-8',errors='replace'),version=f'{device[4]}.{device[5]}.{struct.unpack_from("<H",device,6)[0]}')
            await self.read_state()
        except BaseException:
            await self.abort();raise

    @staticmethod
    def check_result(reply):
        if len(reply)<4:raise ValueError('Risposta ADS troncata')
        result=struct.unpack_from('<I',reply)[0]
        if result:raise ADSError(result)

    async def abort(self):
        writer,self.writer=self.writer,None;self.reader=None
        if writer:
            writer.close()
            with suppress(Exception):await asyncio.wait_for(writer.wait_closed(),.5)

    async def command(self,command,data):
        async with self.lock:
            if not self.writer:raise ConnectionError('Trasporto ADS chiuso')
            self.invoke=(self.invoke+1)&0xffffffff
            header=self.target+self.source+struct.pack('<HHIII',command,4,len(data),0,self.invoke)
            async def exchange():
                self.writer.write(struct.pack('<HI',0,len(header)+len(data))+header+data);await self.writer.drain()
                reserved,length=struct.unpack('<HI',await self.reader.readexactly(6))
                if reserved or not 32<=length<=MAX_PACKET:raise ValueError('Frame ADS/TCP non valido')
                raw=await self.reader.readexactly(length)
                cmd,flags,n,error,invoke=struct.unpack_from('<HHIII',raw,16)
                if raw[:8]!=self.source or raw[8:16]!=self.target or cmd!=command or flags&5!=5 or invoke!=self.invoke or n!=length-32:
                    raise ValueError('Risposta AMS non corrispondente alla richiesta')
                if error:raise ADSError(error,'Router AMS')
                return raw[32:]
            try:return await asyncio.wait_for(exchange(),self.cfg['ads_timeout_ms']/1000)
            except ADSError:raise
            except BaseException as exc:
                await self.abort()
                if isinstance(exc,asyncio.IncompleteReadError):raise ConnectionError('Il router ha chiuso ADS/TCP prima della risposta. Se TwinCAT e installato sul PC e stai usando il suo AMS Net ID, seleziona TwinCAT locale. Verificare altrimenti route e trasporto consentito sul PAC.') from None
                if isinstance(exc,TimeoutError):raise TimeoutError('Timeout ADS: verificare IP, firewall TCP, route AMS di ritorno e runtime PLC') from None
                raise

    async def read(self,group,offset,size):
        return self.read_reply(await self.command(2,struct.pack('<III',group,offset,size)),size)

    async def readwrite(self,group,offset,size,data):
        return self.read_reply(await self.command(9,struct.pack('<IIII',group,offset,size,len(data))+data),size)

    def read_reply(self,reply,maximum):
        self.check_result(reply)
        if len(reply)<8:raise ValueError('Risposta ADS Read troncata')
        size=struct.unpack_from('<I',reply,4)[0]
        if size>maximum or size!=len(reply)-8:raise ValueError('Lunghezza ADS Read non valida')
        return reply[8:]

    async def read_state(self):
        data=await self.command(4,b'');self.check_result(data)
        if len(data)!=8:raise ValueError('ReadState ADS non valido')
        state,device=struct.unpack_from('<HH',data,4)
        self.info.update(ads_state=state,ads_state_name=STATES.get(state,str(state)),device_state=device)
        return state

    async def check_symbols(self):
        try:
            version=await self.read(0xF008,0,4)
            if self.symbol_version is not None and version!=self.symbol_version:
                await self.release_handles();self.symbols.clear();self.catalog_loaded=False;self.datatypes=DatatypeBrowser(self)
                self.info['symbol_changes']=self.info.get('symbol_changes',0)+1
            self.symbol_version=version
        except ADSError as exc:
            if exc.code not in (1793,1794):raise

    async def get_symbol(self,name):
        if name not in self.symbols:
            try:
                s,_=parse_symbol(await self.readwrite(0xF009,0,65536,name.encode('utf-8')+b'\0'))
            except ADSError as exc:
                if exc.code!=1808:raise
                try:s=await self.datatypes.resolve(name)
                except ValueError:raise exc
            # Read by requested name (case-sensitive runtimes may return qualified aliases).
            s['name']=name;self.symbols[name]=s
        return self.symbols[name]

    async def upload_symbols(self):
        data=await self.read(0xF00C,0,8)
        if len(data)!=8:raise ValueError('UploadInfo ADS non valido')
        count,size=struct.unpack('<II',data)
        if size>MAX_PACKET-40 or count>1000000:raise ValueError('Tabella ADS troppo grande (massimo 64 MiB / 1 milione di simboli)')
        data=await self.read(0xF00B,0,size)
        pos=0;symbols={}
        for i in range(count):
            s,n=parse_symbol(data,pos);pos+=n;symbols[s['name']]=s
            if i%500==0:await asyncio.sleep(0)
        if pos!=len(data):raise ValueError('Conteggio tabella simboli ADS non valido')
        self.symbols.update(symbols);self.catalog_loaded=True;self.info['symbol_count']=count
        return list(symbols.values())

    async def inspect(self,nid):
        name=symbol_name(nid)
        if name=='/':return {'nodeid':'ads:/','display_name':'Simboli ADS','browse_name':'ADS','node_class':'Object','has_children':True}
        try:return symbol_item(await self.get_symbol(name))
        except ADSError as exc:
            if exc.code!=1808:raise
            if not self.catalog_loaded:await self.upload_symbols()
            if any(n.startswith(name+'.') for n in self.symbols):return {'nodeid':nid,'display_name':name,'browse_name':name,'node_class':'Object','has_children':True}
            raise

    async def browse(self,nid):
        return (await self.browse_page(nid))['nodes']

    async def browse_page(self,nid,offset=0,count=250):
        offset=max(0,int(offset));count=max(1,min(1000,int(count)))
        name=symbol_name(nid)
        if not self.catalog_loaded:await self.upload_symbols()
        prefix='' if name=='/' else name+'.'
        nodes={};warning=None
        for n,s in list(self.symbols.items()):
            if not n.startswith(prefix):continue
            part=n[len(prefix):].split('.')[0];full=prefix+part
            if full==n:nodes[full]=symbol_item(s)
            elif full not in nodes:nodes[full]={'nodeid':'ads:'+full,'display_name':full,'browse_name':full,'node_class':'Object','has_children':True}
        if name!='/':
            try:
                symbol=await self.get_symbol(name)
            except ADSError as exc:
                if exc.code!=1808 or not nodes:raise
                symbol=None
            if symbol is not None:
                try:
                    desc=await self.datatypes.description(symbol)
                    if desc and desc.get('bounds'):
                        items,total=await self.datatypes.child_symbols(symbol,offset,count)
                        for item in items:self.symbols[item['name']]=item
                        return dict(nodes=[symbol_item(i) for i in items],total=total,next_offset=offset+count if offset+count<total else None)
                    items,_=await self.datatypes.child_symbols(symbol,0,100000)
                    for item in items:
                        self.symbols.setdefault(item['name'],item)
                        nodes[item['name']]=symbol_item(self.symbols[item['name']])
                except ValueError as exc:
                    if not nodes:raise
                    warning=str(exc)
        values=sorted(nodes.values(),key=lambda n:n['nodeid'].lower());total=len(values)
        return dict(nodes=values[offset:offset+count],total=total,next_offset=offset+count if offset+count<total else None,warning=warning)

    async def release_handles(self,keep=()):
        for name,handle in list(self.handles.items()):
            if name in keep:continue
            self.handles.pop(name,None)
            if self.writer:
                with suppress(ADSError):self.check_result(await self.command(3,struct.pack('<III',0xF006,0,4)+struct.pack('<I',handle)))

    async def disconnect(self):
        try:
            with suppress(Exception):await asyncio.wait_for(self.release_handles(),1.5)
        finally:await self.abort()

    def row(self,nid,s,data=None,error=0):
        value,kind=(None,s.get('type','Null')) if error else decode_value(data,s,self.cfg['ads_encoding'])
        run=self.info.get('ads_state')==5
        quality='Bad' if error else 'Good' if run else 'Uncertain'
        numeric=isinstance(value,(int,float)) and math.isfinite(value)
        exact=not isinstance(value,int) or abs(value)<=9007199254740991
        safe=value if exact else str(value)
        if isinstance(value,float) and not math.isfinite(value):safe=str(value)
        if isinstance(value,list):safe=[str(x) if isinstance(x,int) and abs(x)>9007199254740991 or isinstance(x,float) and not math.isfinite(x) else x for x in value]
        status=f'ADS_0x{error:08X}' if error else 'ADS_OK' if run else 'ADS_STATE_'+self.info.get('ads_state_name','UNKNOWN')
        return {'nodeid':nid,'value':safe,'_source_typed':typed(value),'_source_binary_typed':typed(data),'plot_value':float(value) if numeric and quality=='Good' else None,
                'data_type':kind,'good':quality=='Good','quality':quality,'numeric':numeric,'precision_warning':not exact,
                'status':status,'status_code':error,'source_ts':None,'server_ts':None,'protocol':'ads',
                **({'error':str(ADSError(error))} if error else {})}

    async def read_values(self,nids):
        await self.release_handles(keep={symbol_name(n) for n in nids})
        ready=[];rows={}
        for nid in nids:
            name=symbol_name(nid)
            try:
                s=await self.get_symbol(name)
                if s['size']>MAX_VALUE:
                    rows[nid]=self.row(nid,s,error=1797)
                    rows[nid].update(status='ClientValueTooLarge',status_code=None,error='Simbolo > 1 MiB: selezionare singoli elementi')
                    continue
                if name not in self.handles:
                    handle=await self.readwrite(0xF003,0,4,name.encode('utf-8')+b'\0')
                    if len(handle)!=4:raise ValueError('Handle ADS non valido')
                    self.handles[name]=struct.unpack('<I',handle)[0]
                ready.append((nid,s,self.handles[name]))
            except ADSError as exc:rows[nid]=self.row(nid,{},error=exc.code)
        if ready and self.cfg['ads_mode']=='sum' and self.sum_supported:
            try:
                # Small batches limit one ADS request and PLC execution time.
                batch_size=max(1,min(500,int(__import__('os').environ.get('SPACE_ADS_SUM_BATCH',32))))
                for first in range(0,len(ready),batch_size):
                    batch=ready[first:first+batch_size];size=sum(s['size'] for _,s,_ in batch)
                    data=await self.readwrite(0xF080,len(batch),4*len(batch)+size,b''.join(struct.pack('<III',0xF005,h,s['size']) for _,s,h in batch))
                    if len(data)!=4*len(batch)+size:raise ValueError('Risposta ADS SumRead incompleta')
                    pos=4*len(batch)
                    for i,(nid,s,handle) in enumerate(batch):
                        error=struct.unpack_from('<I',data,i*4)[0]
                        rows[nid]=self.row(nid,s,data[pos:pos+s['size']],error);pos+=s['size']
                self.info['read_mode']='ADS SumRead'
            except ADSError as exc:
                if exc.code not in (1793,1794):raise
                self.sum_supported=False;self.info['sum_fallback']=str(exc)
        if ready and (self.cfg['ads_mode']=='individual' or not self.sum_supported):
            self.info['read_mode']='ADS Read individuale'
            for nid,s,h in ready:
                try:rows[nid]=self.row(nid,s,await self.read(0xF005,h,s['size']))
                except ADSError as exc:rows[nid]=self.row(nid,s,error=exc.code)
        # Invalidated handles are refreshed on the next cycle, retaining a Bad sample now.
        for nid,row in rows.items():
            if row['status_code'] in (1808,1809,1826):
                name=symbol_name(nid)
                if name in self.handles:
                    handle=self.handles.pop(name)
                    with suppress(ADSError):self.check_result(await self.command(3,struct.pack('<III',0xF006,0,4)+struct.pack('<I',handle)))
                self.symbols.pop(name,None)
        return [rows[nid] for nid in nids]
