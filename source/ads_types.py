"""ADS datatype declarations and read-only symbolic member traversal.
Header: 8 uint32 + 5 uint16 (42 bytes), strings, array bounds, subitems.
Unknown trailing extensions (GUID, attributes, RPC metadata) are skipped by
entryLength: browsing never invokes methods or dereferences PLC pointers.
"""
import asyncio
import math
import re
import struct

MAX_TYPES_BYTES=64*1024*1024-64
MAX_DEPTH=64
NATIVE={'BOOL':33,'SINT':16,'USINT':17,'BYTE':17,'INT':2,'UINT':18,'WORD':18,'DINT':3,'UDINT':19,'DWORD':19,'LINT':20,'ULINT':21,'LWORD':21,'REAL':4,'LREAL':5,'TIME':19,'DATE':19,'TOD':19,'DT':19,'LTIME':21}

def parse_type(data,offset=0,encoding='utf-8',depth=0,budget=None):
    budget=budget if budget is not None else [100000]
    budget[0]-=1
    if depth>MAX_DEPTH or budget[0]<0:raise ValueError('Datatype ADS troppo complesso')
    if len(data)-offset<42:raise ValueError('Header datatype ADS troncato')
    length,version,hash_value,type_hash,size,address,datatype,flags,n,t,c,dim,count=struct.unpack_from('<8I5H',data,offset)
    end=offset+length
    if length<45+n+t+c+8*dim or end>len(data):raise ValueError('Lunghezza datatype ADS non valida')
    pos=offset+42;text=[]
    for size_text in (n,t,c):
        if data[pos+size_text]!=0:raise ValueError('Nome datatype ADS non terminato')
        text.append(bytes(data[pos:pos+size_text]).decode(encoding,errors='replace'));pos+=size_text+1
    bounds=[]
    for _ in range(dim):
        lo,number=struct.unpack_from('<iI',data,pos);pos+=8
        if number==0:raise ValueError('Array ADS a dimensione aperta non espandibile')
        bounds.append((lo,lo+number-1))
    children=[]
    for _ in range(count):
        child,used=parse_type(memoryview(data)[:end],pos,encoding,depth+1,budget)
        children.append(child);pos+=used
    return {'name':text[0],'type':text[1],'comment':text[2],'size':size,'offset':address,'datatype':datatype,'flags':flags,'bounds':bounds,'children':children},length


def blocked_type(name):
    return bool(re.match(r'^(POINTER\s+TO|REFERENCE\s+TO|PVOID\b|INTERFACE\b)',name.strip(),re.I))


def array_info(name):
    m=re.fullmatch(r'ARRAY\s*\[([^]]+)\]\s+OF\s+(.+)',name.strip(),re.I)
    if not m:return None
    bounds=[]
    for d in m[1].split(','):
        match=re.fullmatch(r'\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*',d)
        if not match:raise ValueError('Limiti array ADS non riconosciuti')
        a,b=map(int,match.groups())
        if b<a:raise ValueError('Limiti array ADS non validi')
        bounds.append((a,b))
    return bounds,m[2].strip()


class DatatypeBrowser:
    def __init__(self,client):
        self.client=client;self.types={};self.uploaded=False;self.by_name=True;self.encoding='utf-8'

    async def declaration(self,name,trail=()):
        key=name.casefold()
        if key in trail or len(trail)>=MAX_DEPTH:raise ValueError('Ciclo alias datatype ADS: '+name)
        if key in self.types:return self.types[key]
        try:
            if not self.by_name:raise LookupError()
            data=await self.client.readwrite(0xF011,0,4*1024*1024,name.encode(self.encoding)+b'\0')
            result,used=parse_type(data,encoding=self.encoding)
            if used!=len(data):raise ValueError('Datatype ADS con lunghezza incoerente')
            self.types[key]=result;return result
        except Exception as exc:
            if not isinstance(exc,LookupError) and getattr(exc,'code',None) not in (1793,1794,1797,1808):raise
            if getattr(exc,'code',None) in (1793,1794):self.by_name=False
            await self.upload()
            if key not in self.types:raise ValueError('Datatype non pubblicato dal PLC: '+name) from exc
            return self.types[key]

    async def upload(self):
        if self.uploaded:return
        try:info=await self.client.read(0xF00F,0,64)
        except Exception as exc:
            if getattr(exc,'code',None)!=1797:raise ValueError('Il PLC non rende disponibile la tabella datatype ADS: '+str(exc)) from exc
            info=await self.client.read(0xF00F,0,24)
        if len(info)<24:raise ValueError('UploadInfo2 ADS non disponibile: impossibile espandere strutture/FB')
        count,size=struct.unpack_from('<II',info,8)
        if len(info)>=32:self.encoding='utf-8' if struct.unpack_from('<I',info,28)[0]==65001 else 'cp1252'
        if size>MAX_TYPES_BYTES or count>1000000:raise ValueError('Catalogo datatype ADS oltre il limite di 64 MiB')
        data=await self.client.read(0xF00E,0,size);pos=0;parsed={};budget=[1000000]
        for i in range(count):
            item,length=parse_type(data,pos,self.encoding,budget=budget);pos+=length
            parsed[item['name'].casefold()]=item
            if i%250==0:await asyncio.sleep(0)
        if pos!=len(data):raise ValueError('Conteggio datatype ADS incoerente')
        self.types.update(parsed);self.uploaded=True

    async def description(self,symbol,trail=()):
        name=symbol['type'];key=name.casefold()
        if blocked_type(name):return None
        if symbol.get('_declaration',{}).get('children') or symbol.get('_declaration',{}).get('bounds'):
            return symbol['_declaration']
        shape=array_info(name)
        if shape:return {'name':name,'type':shape[1],'bounds':shape[0],'children':[],'size':symbol['size'],'datatype':65}
        if name.upper() in NATIVE or re.match(r'^(W?STRING)(\s*\(|$)',name,re.I):return None
        # Native ADS scalars, including enums, have no browsable members.
        if symbol['datatype']!=65:return None
        if key in trail or len(trail)>=MAX_DEPTH:raise ValueError('Ciclo alias datatype ADS: '+name)
        desc=await self.declaration(name)
        if desc['children'] or desc['bounds']:return desc
        if desc['type'] and desc['type'].casefold()!=key:
            return await self.description({**symbol,'type':desc['type'],'datatype':desc['datatype']},trail+(key,))
        return None

    async def child_symbols(self,symbol,offset=0,limit=250):
        desc=await self.description(symbol)
        if desc is None:return [],0
        bounds=desc.get('bounds',[])
        result=[]
        if bounds:
            count=math.prod(b-a+1 for a,b in bounds)
            if not count or symbol['size']%count:raise ValueError('Dimensione array ADS incoerente')
            size=symbol['size']//count
            base=desc['type']
            for flat in range(offset,min(count,offset+limit)):
                indexes=[];remaining=flat
                for a,b in reversed(bounds):
                    remaining,index=divmod(remaining,b-a+1);indexes.append(a+index)
                indexes.reverse()
                name=symbol['name']+'['+','.join(map(str,indexes))+']'
                result.append(dict(name=name,type=base,size=size,datatype=NATIVE.get(base.upper(),65),flags=0,
                    group=symbol['group'],offset=symbol['offset']+flat*size,comment=symbol.get('comment',''),derived=True))
            return result,count
        children=desc['children']
        for member in children[offset:offset+limit]:
            # Metadata includes memory members only; RPC extensions are deliberately ignored.
            child=dict(name=symbol['name']+'.'+member['name'],type=member['type'] or member['name'],
                size=member['size'],datatype=member['datatype'],flags=0,group=symbol['group'],
                offset=symbol['offset']+member['offset'],comment=member['comment'],derived=True,_declaration=member)
            result.append(child)
        return result,len(children)

    async def resolve(self,name):
        c=self.client
        if not c.catalog_loaded:await c.upload_symbols()
        roots=[n for n in c.symbols if name==n or name.startswith(n+'.') or name.startswith(n+'[')]
        if not roots:raise ValueError('Simbolo ADS non trovato: '+name)
        current=c.symbols[max(roots,key=len)];tail=name[len(current['name']):];depth=0
        while tail:
            depth+=1
            if depth>MAX_DEPTH:raise ValueError('Profondita ADS oltre 64 livelli')
            if tail.startswith('.'):
                match=re.match(r'\.([^.[\]]+)',tail)
                if not match:raise ValueError('Percorso membro ADS non valido')
                part=match[1];desc=await self.description(current)
                if not desc or desc.get('bounds'):raise ValueError('Il tipo non contiene membri: '+current['type'])
                index=next((i for i,m in enumerate(desc['children']) if m['name'].casefold()==part.casefold()),None)
                if index is None:raise ValueError('Membro ADS non trovato: '+part)
                items,_=await self.child_symbols(current,index,1);current=items[0]
            elif tail.startswith('['):
                match=re.match(r'\[([^]]+)\]',tail)
                if not match:raise ValueError('Indice array non valido')
                desc=await self.description(current);bounds=(desc or {}).get('bounds',[])
                try:indexes=[int(p.strip()) for p in match[1].split(',')]
                except ValueError:raise ValueError('Indici array ADS non validi') from None
                if len(indexes)!=len(bounds):raise ValueError('Numero dimensioni array ADS errato')
                flat=0
                for index,(a,b) in zip(indexes,bounds):
                    if not a<=index<=b:raise ValueError('Indice fuori dai limiti array ADS')
                    flat=flat*(b-a+1)+index-a
                items,_=await self.child_symbols(current,flat,1);current=items[0]
            else:raise ValueError('Percorso ADS non valido')
            tail=tail[len(match[0]):];c.symbols[current['name']]=current
        return current
