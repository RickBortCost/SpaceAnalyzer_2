"""Test-only S7CommPlus wire fixture for EXPLORE/type-info and symbolic reads.

Uses provider emulator for session framing; independently encodes the OMS tree
and resolves LID paths without classic byte-offset reads. Not a real CPU.
"""
import socket
import struct
from s7commplus import Server
from s7commplus.protocol import FunctionCode, ElementID, DataType, Ids
from s7commplus.codec import encode_pvalue_blob
from s7commplus.vlq import encode_uint32_vlq as u32, encode_uint64_vlq as u64, decode_uint32_vlq
from s7commplus.typeinfo import VARTYPE_LIST,VARNAME_LIST


def obj(rid,body=b'',cls=0):
    return bytes([ElementID.START_OF_OBJECT])+struct.pack('>I',rid)+u32(cls)+b'\0\0'+body+bytes([ElementID.TERMINATING_OBJECT])


def vte(lid,kind,code=8,info=None):
    return struct.pack('<IIB',lid,123,kind)+struct.pack('>H',code<<12)+b'\0'+(info or struct.pack('<HH',0,0))


def type_obj(rid,names,elements):
    block=struct.pack('<I',0)+b''.join(elements)
    labels=b''.join(bytes([len(n.encode())])+n.encode()+b'\0' for n in names)
    body=bytes([VARTYPE_LIST])+struct.pack('>H',len(block))+block+b'\0\0'
    body+=bytes([VARNAME_LIST])+struct.pack('>H',len(labels))+labels+b'\0\0'
    return obj(rid,body)


def requests(data):
    offset=4
    def read():
        nonlocal offset
        value,n=decode_uint32_vlq(data,offset);offset+=n;return value
    count=read();read();items=[]
    for _ in range(count):
        crc=read();area=read();n=read();sub=read();lids=tuple(read() for _ in range(n-1))
        items.append((area,sub,lids,crc))
    return items


class Peer(Server):
    def __init__(self):
        super().__init__();self.register_db(1,{},64)
        self.reads=[];self.variable_writes=0;self.bad_lid=None;self.phase=0
        self.values={(10,):struct.pack('>f',42.5),(20,):struct.pack('>f',12.25),(30,):b'\x01',
            (40,):struct.pack('>Q',18446744073709551615),(50,10):struct.pack('>h',-12),
            (60,0):struct.pack('>f',1.25),(60,1):struct.pack('>f',2.25),(60,2):struct.pack('>f',3.25)}
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
        self.start('127.0.0.1',self.port)

    def _handle_explore(self,seq,session,data):
        explore_id=struct.unpack_from('>I',data)[0]
        if explore_id!=Ids.OBJECT_OMS_TYPE_INFO_CONTAINER: return super()._handle_explore(seq,session,data)
        elements=[vte(10,8),vte(20,8),vte(30,1),vte(40,49),
            vte(50,17,5,struct.pack('<HHIII4I',0,0,0,0,0x90000002,0,0,0,0)),
            vte(60,8,10,struct.pack('<HHIIiI',0,0,0,0,1,3))]
        root=type_obj(0x90000001,['CaricoA','CaricoB','Enabled','Count64','Data','Array'],elements)
        child=type_obj(0x90000002,['Status'],[vte(10,5)])
        return self._build_response_header(FunctionCode.EXPLORE,seq)+u32(0)+obj(explore_id,root+child,534)+bytes(4)

    def _handle_get_multi_variables(self,seq,session,data):
        items=requests(data)
        if not items or any(area!=Ids.DB_ACCESS_AREA_BASE|1 for area,_,_,_ in items):
            return super()._handle_get_multi_variables(seq,session,data)
        out=bytearray(self._build_response_header(FunctionCode.GET_MULTI_VARIABLES,seq)+u64(0))
        bad=[]
        for i,(_,_,lids,crc) in enumerate(items,1):
            self.reads.append((lids,crc))
            raw=struct.pack('>I',0x90000001) if lids==(1,) else self.values.get(lids)
            if raw is None or lids==self.bad_lid:bad.append(i)
            else:out+=u32(i)+encode_pvalue_blob(raw)
        out+=u32(0)
        for i in bad:out+=u32(i)+u64(0x8104)
        out+=u32(0)+u32(0)
        return bytes(out)

    def _handle_set_multi_variables(self,seq,session,data):
        # SessionServerVersion echo is required protocol setup, not a tag write.
        if b'\x8a\x0e' in data:self.variable_writes+=1
        return super()._handle_set_multi_variables(seq,session,data)
