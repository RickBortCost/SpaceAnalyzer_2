"""Fixed-template writer: immutable metadata/indexes, exact Float32 payloads."""
import hashlib,json,os,struct
from pathlib import Path
import tempfile
from functools import lru_cache
from pda3.reader import PDA3Reader
from pda3.template_loader import template,profile,validate_profile
from pda3_fixture.block_writer import replace_compatible_block
from pda3_fixture.rle_codec import encode_exact
from pda3.timestamp_handler import PDA3TimestampHandler
from pda3.errors import PDA3CapacityError

@lru_cache(maxsize=2)
def _constant_payloads(word):
    """Cache immutable exact zero/NaN blocks shared by consecutive short files."""
    original=template();out=bytearray(original.raw)
    for block in original.blocks:replace_compatible_block(original,block,word*block.samples,out)
    return tuple(bytes(out[b.offset+4:b.offset+4+b.size]) for b in original.blocks)

class PDA3TemplateWriter:
    def write(self,destination,signals,sample_count=60000):
        """Write 1..60000 real Float32 samples; reserved tail is never acquired data."""
        destination=Path(destination)
        if destination.exists():raise ValueError('PDA3: destinazione gia esistente')
        original=template();p=profile();out=bytearray(original.raw)
        if not 1<=len(signals)<=64 or any(type(i) is not int or not 0<=i<64 for i in signals):raise ValueError('PDA3: scegliere da 1 a 64 canali')
        if type(sample_count) is not int or not 1<=sample_count<=60000:raise ValueError('PDA3: numero campioni tra 1 e 60000')
        tail=bytes.fromhex('0000c07f')*(60000-sample_count)  # quiet NaN, never a fabricated Good value
        padded={}
        for index,data in signals.items():
            if not isinstance(data,(bytes,bytearray)) or len(data)!=sample_count*4:raise ValueError(f'PDA3: canale {index} richiede esattamente {sample_count} campioni')
            padded[index]=bytes(data)+tail
        struct.pack_into('<Q',out,48,original.start_ticks+sample_count*100000)
        zero=b'\0'*(60000*4);reencoded=0
        for index,c in enumerate(p['channels']):
            data=padded.get(index,zero)
            if len(data)!=60000*4:raise ValueError(f'PDA3: canale {index} richiede esattamente 60000 campioni')
            position=0
            for bi in c['blocks']:
                block=original.blocks[bi]
                constant=b'\0'*4 if index not in padded else bytes.fromhex('0000c07f') if position>=sample_count*4 else None
                if constant is not None:
                    out[block.offset+4:block.offset+4+block.size]=_constant_payloads(constant)[bi]
                    position+=block.samples*4;continue
                section=data[position:position+block.samples*4];position+=len(section)
                try:replace_compatible_block(original,block,section,out)
                except ValueError:
                    try:encoded=encode_exact(section,block.size)
                    except PDA3CapacityError as exc:
                        raise PDA3CapacityError(str(exc),**{**exc.details,'channel':index,'block':bi,'block_samples':block.samples}) from exc
                    out[block.offset+4:block.offset+4+block.size]=encoded;reencoded+=1
        checked=PDA3Reader(out);validate_profile(checked)
        for index,c in enumerate(p['channels']):
            data=b''.join(checked.block_bits(checked.blocks[bi]) for bi in c['blocks'])
            if data!=padded.get(index,zero):raise ValueError('PDA3: rilettura diversa dai bit Float32 richiesti')
        destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():raise ValueError('PDA3: destinazione gia esistente')
        # Exclusive publication; a failed write never leaves a .dat named as complete.
        fd,name=tempfile.mkstemp(prefix='.pda3_',suffix='.part',dir=destination.parent)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(out);stream.flush();os.fsync(stream.fileno())
            # link() is atomic and refuses an existing destination on Windows and Linux.
            os.link(name,destination)
        finally:Path(name).unlink(missing_ok=True)
        return {'dat_sha256':hashlib.sha256(out).hexdigest(),'template_sha256':original.sha256,'profile_id':p['id'],
            'size_bytes':len(out),'float32_roundtrip_exact':True,'reencoded_blocks':reencoded,'metadata_and_all_indexes_preserved':True,
            'ibaAnalyzer_new_output_validated':False,'internal_timestamps_changed':sample_count<60000,
            'internal_start_modified':False,'internal_end_modified':sample_count<60000,
            'internal_clock':PDA3TimestampHandler.describe(checked),'valid_samples_per_channel':sample_count,
            'storage_samples_per_channel':60000,'reserved_tail_samples':60000-sample_count,
            'reserved_tail_encoding':'IEEE-754 quiet NaN outside declared end; not acquired data'}
