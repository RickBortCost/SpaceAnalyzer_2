"""Check every opaque byte and block boundary after an allowed value substitution."""
from .reader import PDA3Reader

def validate_payload_replacement(original,modified):
    reader=PDA3Reader(modified)
    if len(original.raw)!=len(reader.raw) or original.blocks!=reader.blocks or original.pages!=reader.pages:
        raise ValueError('PDA3: struttura, conteggi o dimensioni alterati')
    if original.raw[:original.data_start]!=reader.raw[:reader.data_start]:raise ValueError('PDA3: header o metadati alterati')
    for page in original.pages:
        if original.raw[page.offset:page.end]!=reader.raw[page.offset:page.end]:raise ValueError('PDA3: indice alterato')
    for b in original.blocks:
        a=list(original.tokens(b));c=list(reader.tokens(b))
        if a!=c:raise ValueError('PDA3: forma RLE alterata')
        for t in a:
            allowed_end=t.end if t.literal else t.payload+4
            if original.raw[t.offset:t.payload]!=reader.raw[t.offset:t.payload] or original.raw[allowed_end:t.end]!=reader.raw[allowed_end:t.end]:
                raise ValueError('PDA3: opcode o contatore RLE alterato')
    return reader
