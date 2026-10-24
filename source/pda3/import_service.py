"""Imports and optional verified chronological joining, using the normal recording library."""
import json,secrets,shutil
from pathlib import Path
from .service import is_pda3,details,sequence_info
from .storage import atomic_json

def import_files(request,directory,library):
    uploads=request.files.getlist('files') or request.files.getlist('file')
    if not uploads or not any(f.filename for f in uploads):raise ValueError('Seleziona CSV, Parquet, FDA DAS4, RBF v3 o DAT PDA3')
    if len(uploads)>128:raise ValueError('Importazione: massimo 64 registrazioni e relativi manifest')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    manifests={};items=[];errors=[];warnings=[]
    for f in uploads:
        original=Path(str(f.filename or '').replace('\\','/')).name
        if original.lower().endswith('.json'):
            raw=f.stream.read(1024*1024+1)
            if len(raw)>1024*1024:raise ValueError('Manifest JSON oltre 1 MiB')
            try:meta=json.loads(raw.decode('utf-8-sig'))
            except (ValueError,UnicodeError):raise ValueError('Manifest JSON non valido: '+original) from None
            if not isinstance(meta,dict):raise ValueError('Manifest JSON non valido')
            manifests[original.removesuffix('.json').removesuffix('.metadata')]=meta
    for f in uploads:
        original=Path(str(f.filename or '').replace('\\','/')).name.strip() or 'registrazione'
        suffix=Path(original).suffix.lower()
        if suffix=='.json':continue
        if suffix not in ('.csv','.parquet','.das','.rbf','.dat'):
            errors.append(original+': formato non supportato');continue
        target=directory/(secrets.token_hex(16)+suffix)
        try:
            with target.open('xb') as stream:shutil.copyfileobj(f.stream,stream,length=1024*1024)
            if not target.stat().st_size:raise ValueError('file vuoto')
            meta=dict(manifests.get(Path(original).stem,{ }));meta['display_name']=original
            if suffix=='.dat':
                if not is_pda3(target):raise ValueError('Import DAT: solo PDA3 del modello 64 canali validato; varianti legacy/ignote non supportate')
                if not meta.get('pda3_profile'):
                    try:meta['pda3_timezone_offset_minutes']=float(request.form['pda3_timezone_offset_minutes'])
                    except (KeyError,ValueError):raise ValueError('DAT senza manifest: scegliere il fuso orario del calendario interno') from None
                details(target,meta)
            atomic_json(target.with_suffix('.metadata.json'),meta)
            items.append(library.describe(target.name,display_name=original,source='local'))
        except Exception as exc:
            target.unlink(missing_ok=True);target.with_suffix('.metadata.json').unlink(missing_ok=True)
            errors.append(original+': '+str(exc))
    if not items:raise ValueError('; '.join(errors) or 'Nessuna registrazione selezionata')
    sequence=None
    if request.form.get('join_pda3')=='true':
        dats=[f for f in items if f['format']=='dat']
        if len(dats)>=2:
            seq=directory/(secrets.token_hex(16)+'.pda3seq')
            try:
                atomic_json(seq,{'parts':[f['name'] for f in dats]})
                sequence_info(library,seq)
                atomic_json(seq.with_suffix('.metadata.json'),{'display_name':f'Sequenza PDA3 · {len(dats)} file'})
                sequence=library.describe(seq.name,display_name=f'Sequenza PDA3 · {len(dats)} file',source='local')
            except (ValueError,OSError) as exc:
                seq.unlink(missing_ok=True);seq.with_suffix('.metadata.json').unlink(missing_ok=True)
                warnings.append('File importati separatamente: '+str(exc))
    return {'ok':True,'recording':sequence or items[0],'recordings':items,'sequence':sequence,'warnings':warnings,'errors':errors}
