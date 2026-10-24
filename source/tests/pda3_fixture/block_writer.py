"""Exact compatible-token substitution, useful for validated probes and templates.

Arbitrary reallocation/recoding is intentionally unavailable until a channel map
and ibaAnalyzer tests exist for the 64-channel model. No averaging or rounding
to satisfy a repeated run is allowed: each requested Float32 bit pattern must fit.
"""
import struct

def replace_compatible_block(reader,block,wanted_bits,out):
    if len(wanted_bits)!=block.samples*4:raise ValueError('PDA3: numero campioni diverso dal modello')
    offset=0;patches=[]
    for t in reader.tokens(block):
        section=wanted_bits[offset:offset+t.count*4]
        if t.literal:encoded=section
        else:
            value=section[:4]
            if section!=value*t.count:raise ValueError('PDA3: dati non rappresentabili lossless con la struttura RLE validata; originale da conservare')
            encoded=value
        patches.append((t.payload,encoded))
        offset+=t.count*4
    # Validate the entire block first; a rejected request leaves the buffer intact.
    for start,encoded in patches:out[start:start+len(encoded)]=encoded

def block_identity_probe(reader):
    out=bytearray(reader.raw)
    for block in reader.blocks:
        bits=struct.pack('<f',block.index+1)
        for t in reader.tokens(block):out[t.payload:t.payload+(t.count*4 if t.literal else 4)]=bits*(t.count if t.literal else 1)
    return bytes(out)
