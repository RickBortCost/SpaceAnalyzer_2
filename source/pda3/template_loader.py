"""The single externally mapped PDA3 model. Unknown variants are rejected."""
from functools import lru_cache
import hashlib,json,struct
from pathlib import Path
from .reader import PDA3Reader
from .channel_mapper import validate_mapping

ASSETS=Path(__file__).resolve().parent/'assets'

@lru_cache(maxsize=1)
def profile():return json.loads((ASSETS/'profile_64_10ms.json').read_text('utf-8'))

@lru_cache(maxsize=1)
def model_header():
    with (ASSETS/'template_64_10ms.dat').open('rb') as stream:return stream.read(56)

def valid_sample_count(reader):
    delta=reader.end_ticks-reader.start_ticks
    count,remainder=divmod(delta,100000)  # 10 ms in 100 ns ticks
    if remainder or not 1<=count<=60000:raise ValueError('PDA3: durata fuori dal profilo 10 ms / massimo 600 s')
    return count

def validate_profile(reader):
    p=profile()
    valid_sample_count(reader)
    # Only the exclusive end tick may differ. Start, opaque metadata and every
    # index/offset retain the template identity; a short DAT has reserved NaN tail.
    prefix=bytearray(reader.raw[:reader.data_start]);prefix[48:56]=model_header()[48:56]
    if hashlib.sha256(prefix).hexdigest()!=p['metadata_sha256'] or hashlib.sha256(b''.join(reader.raw[x.offset:x.end] for x in reader.pages)).hexdigest()!=p['index_sha256']:
        raise ValueError('PDA3: metadati/indice non corrispondono al modello 64 canali validato; variante non supportata')
    if [b.size for b in reader.blocks]!=p['block_sizes'] or [b.samples for b in reader.blocks]!=p['block_samples']:
        raise ValueError('PDA3: blocchi o campioni diversi dal modello validato')
    return p

@lru_cache(maxsize=1)
def template():
    r=PDA3Reader(ASSETS/'template_64_10ms.dat')
    if r.sha256!=profile()['template_sha256']:raise ValueError('PDA3: checksum del modello non valido')
    validate_profile(r);validate_mapping(r,{c['id']:c['blocks'] for c in profile()['channels']},60000)
    return r

def status():
    r=template()
    return {'ok':True,'template_id':profile()['id'],'sha256':r.sha256,'channels':64,'sample_ms':10,'segment_seconds':600,
        'samples_per_channel':60000,'channel_mapping_verified':True,'timestamp_mode':'template_with_real_manifest',
        'minimum_samples':1,'short_recordings':True,'short_recording_tail':'reserved NaN outside declared end, excluded from replay',
        'internal_end_rewrite_experimental':True,'internal_timestamp_rewrite_supported':False,
        'writer_validation':'experimental: new reencoded outputs and shortened end require ibaAnalyzer verification'}
