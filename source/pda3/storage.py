import hashlib,json,os,uuid
from pathlib import Path

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.part')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)
