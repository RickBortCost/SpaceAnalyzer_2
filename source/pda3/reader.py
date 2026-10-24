"""Read the observed PDA3 block/page container and Float32 RLE bit patterns.

Does not infer channel assignments or decrypt opaque metadata/index entries.
Every length and sample expansion is bounded before allocation.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

@dataclass(frozen=True)
class Token:
    offset:int
    payload:int
    end:int
    count:int
    literal:bool

@dataclass(frozen=True)
class Block:
    index:int
    page:int
    offset:int
    size:int
    samples:int

@dataclass(frozen=True)
class Page:
    offset:int
    end:int
    next_offset:int
    count:int

class PDA3Reader:
    MAX_FILE=256*1024*1024
    MAX_BLOCKS=100000
    MAX_SAMPLES_PER_BLOCK=2000000

    def __init__(self,path_or_bytes):
        if isinstance(path_or_bytes,(bytes,bytearray)):
            raw=bytes(path_or_bytes)
        else:
            path=Path(path_or_bytes)
            if path.stat().st_size>self.MAX_FILE:raise ValueError('PDA3: file oltre il limite di analisi di 256 MiB')
            raw=path.read_bytes()
        if not 96<=len(raw)<=self.MAX_FILE or raw[:4]!=b'PDA3':raise ValueError('PDA3: firma o dimensione non supportata')
        if struct.unpack_from('<I',raw,8)[0]!=64:raise ValueError('PDA3: variante header non supportata')
        self.raw=raw;self.sha256=hashlib.sha256(raw).hexdigest();self.blocks=[];self.pages=[]
        self.start_ticks,self.end_ticks=struct.unpack_from('<QQ',raw,40)
        if self.end_ticks<=self.start_ticks:raise ValueError('PDA3: intervallo temporale non valido')
        self.duration_s=(self.end_ticks-self.start_ticks)/10000000
        self.data_start=80+struct.unpack_from('<I',raw,72)[0]
        footer=struct.unpack_from('<Q',raw,16)[0];cursor=self.data_start;seen=set()
        if not 80<=cursor<footer<len(raw):raise ValueError('PDA3: offset iniziali non validi')
        while True:
            if footer in seen or footer+16>len(raw):raise ValueError('PDA3: catena indici ciclica o troncata')
            seen.add(footer);next_offset,count=struct.unpack_from('<QQ',raw,footer)
            if count<1 or count>256:raise ValueError('PDA3: dimensione pagina indice non supportata')
            end=footer+16+count*16
            if end>len(raw):raise ValueError('PDA3: pagina indice troncata')
            number=0
            while cursor<footer:
                if cursor+4>footer or len(self.blocks)>=self.MAX_BLOCKS:raise ValueError('PDA3: catena blocchi non valida')
                length=struct.unpack_from('<I',raw,cursor)[0]
                if not length or cursor+4+length>footer:raise ValueError('PDA3: confine blocco non valido')
                block=Block(len(self.blocks),len(self.pages),cursor,length,0)
                total=0
                for token in self.tokens(block):
                    total+=token.count
                    if total>self.MAX_SAMPLES_PER_BLOCK:raise ValueError('PDA3: espansione RLE oltre limite')
                self.blocks.append(Block(block.index,block.page,block.offset,block.size,total))
                number+=1;cursor+=4+length
            if cursor!=footer or number!=count:raise ValueError('PDA3: numero blocchi e indice non corrispondenti')
            self.pages.append(Page(footer,end,next_offset,count));cursor=end
            if not next_offset:
                if end!=len(raw):raise ValueError('PDA3: dati non riconosciuti dopo l’ultimo indice')
                break
            if not end<next_offset<len(raw):raise ValueError('PDA3: riferimento pagina successiva non valido')
            footer=next_offset
        self.total_samples=sum(b.samples for b in self.blocks)

    def tokens(self,block):
        p=block.offset+4;end=p+block.size;raw=self.raw
        while p<end:
            offset=p;op=raw[p];p+=1;payload=p
            if op==127:
                if p+6>end:raise ValueError('PDA3: RLE16 troncato')
                count=struct.unpack_from('<H',raw,p+4)[0];p+=6;literal=False
            elif 1<=op<=126:
                count=op;p+=4;literal=False
            elif 129<=op<=255:
                count=op-128;p+=4*count;literal=True
            else:raise ValueError(f'PDA3: record RLE 0x{op:02x} non supportato all’offset {offset}')
            if not count or p>end:raise ValueError('PDA3: record RLE non valido o troncato')
            yield Token(offset,payload,p,count,literal)
        if p!=end:raise ValueError('PDA3: confine RLE non rispettato')

    def block_bits(self,block):
        out=bytearray()
        for t in self.tokens(block):
            out.extend(self.raw[t.payload:t.end] if t.literal else self.raw[t.payload:t.payload+4]*t.count)
        return bytes(out)

    def summary(self):
        return {'format':'PDA3','size_bytes':len(self.raw),'sha256':self.sha256,
            'duration_seconds':self.duration_s,'start_ticks':self.start_ticks,'end_ticks':self.end_ticks,
            'data_start':self.data_start,'metadata_bytes':self.data_start-80,'page_count':len(self.pages),
            'block_count':len(self.blocks),'decoded_sample_values':self.total_samples,
            'channel_mapping_verified':False,'sample_period_verified':False,
            'note':'Il conteggio totale dei valori non identifica da solo i canali o la loro frequenza.'}
