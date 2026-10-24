"""Generate a Windows AMD64 COFF icon resource without external build tools."""
import struct
from pathlib import Path


def build_icon_resource(ico_path, output):
    data=Path(ico_path).read_bytes()
    reserved,kind,count=struct.unpack_from('<HHH',data)
    if reserved!=0 or kind!=1 or not 1<=count<=64:
        raise ValueError('Invalid ICO header')
    icons={};group=bytearray(struct.pack('<HHH',0,1,count))
    for i in range(count):
        w,h,colors,res,planes,bits,size,offset=struct.unpack_from('<BBBBHHII',data,6+16*i)
        if offset+size>len(data):raise ValueError('Invalid ICO image length')
        icons[i+1]={1033:data[offset:offset+size]}
        group.extend(struct.pack('<BBBBHHIH',w,h,colors,res,planes,bits,size,i+1))
    tree={3:icons,14:{1:{1033:bytes(group)}}}
    section=bytearray();leaves=[]
    def directory(nodes):
        pos=len(section);keys=sorted(nodes)
        section.extend(struct.pack('<IIHHHH',0,0,0,0,0,len(keys))+bytes(8*len(keys)))
        for i,key in enumerate(keys):
            value=nodes[key]
            if isinstance(value,dict):entry=directory(value)|0x80000000
            else:
                entry=len(section);section.extend(bytes(16));leaves.append((entry,value))
            struct.pack_into('<II',section,pos+16+8*i,key,entry)
        return pos
    directory(tree)
    relocations=bytearray()
    for entry,value in leaves:
        section.extend(bytes((-len(section))%4));offset=len(section)
        section.extend(value)
        struct.pack_into('<IIII',section,entry,offset,len(value),0,0)
        # IMAGE_REL_AMD64_ADDR32NB: relocate the blob offset to its image RVA.
        relocations.extend(struct.pack('<IIH',entry,0,3))
    raw_offset=60;reloc_offset=raw_offset+len(section);symbol_offset=reloc_offset+len(relocations)
    header=struct.pack('<HHIIIHH',0x8664,1,0,symbol_offset,1,0,0)
    sh=struct.pack('<8sIIIIIIHHI',b'.rsrc\0\0\0',0,0,len(section),raw_offset,reloc_offset,0,len(leaves),0,0x40000040)
    symbol=struct.pack('<8sIhHBB',b'.rsrc\0\0\0',0,1,0,3,0)
    Path(output).write_bytes(header+sh+section+relocations+symbol+struct.pack('<I',4))
