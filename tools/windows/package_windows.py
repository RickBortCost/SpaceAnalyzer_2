"""Package only the verified executable, documentation and license notices."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import release_notes  # noqa: E402


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dist',type=Path,required=True)
    args=parser.parse_args();dist=args.dist.resolve()
    build=json.loads((dist/'BUILD_MANIFEST.json').read_text('utf-8'))
    smoke=json.loads((dist/'WINDOWS_EXE_VERIFICATION.json').read_text('utf-8'))
    exe=dist/build['exe'];version=build['version']
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    if smoke['result']!='PASS' or not smoke['packaged_exe_executed'] or digest(exe)!=smoke['sha256']:
        raise RuntimeError('Refusing to publish an unverified executable')
    root='Space_Fieldbus_Analyzer_V'+version+'_WINDOWS'
    # Guida illustrata bilingue: un PDF per versione minore (tools/guide/build_guide.py), inclusa nel pacchetto di ogni versione.
    guide=ROOT/'docs/guida'/('Space_Fieldbus_Analyzer_'+'.'.join(version.split('.')[:2])+'_Guida_Guide_IT_EN.pdf')
    if not guide.is_file():raise RuntimeError('Guida PDF mancante: '+guide.name+' (python tools/guide/build_guide.py)')
    archive=dist/(root+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path,name in [(exe,exe.name),(ROOT/'LEGGIMI_AVVIO_WINDOWS.md','LEGGIMI_AVVIO_WINDOWS.md'),
                          (ROOT/'source/THIRD_PARTY_NOTICES.txt','THIRD_PARTY_NOTICES.txt'),
                          (ROOT/'source/GUIDA_ACQUISIZIONE_SIMULTANEA.md','GUIDA_ACQUISIZIONE_SIMULTANEA.md'),
                          (ROOT/'source/GUIDA_ZOOM_LIVE.md','GUIDA_ZOOM_LIVE.md'),
                          (ROOT/'source/GUIDA_CALCOLI_INTERVALLO.md','GUIDA_CALCOLI_INTERVALLO.md'),
                          (ROOT/'source/GUIDA_REGISTRAZIONI_RAPIDE.md','GUIDA_REGISTRAZIONI_RAPIDE.md'),
                          (ROOT/'VERSIONE_3.0.md','VERSIONE_3.0.md'),(ROOT/'FASE1_ACQUISIZIONE_REGISTRAZIONE.md','FASE1_ACQUISIZIONE_REGISTRAZIONE.md'),
                          (guide,guide.name),
                          (dist/'BUILD_MANIFEST.json','BUILD_MANIFEST.json'),
                          (dist/'WINDOWS_EXE_VERIFICATION.json','WINDOWS_EXE_VERIFICATION.json')]:
            z.write(path,root+'/'+name)
        # README.txt: le modifiche di questa versione in testa (CHANGELOG, o commit se la sezione manca).
        z.writestr(root+'/README.txt',release_notes.readme(version).replace('\n','\r\n').encode('utf-8-sig'))
        for path in sorted((ROOT/'examples/licenses').rglob('*')):
            if path.is_file():z.write(path,root+'/licenses/'+path.relative_to(ROOT/'examples/licenses').as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise RuntimeError('Corrupt Windows ZIP')
        if hashlib.sha256(z.read(root+'/'+exe.name)).hexdigest()!=build['sha256']:raise RuntimeError('ZIP executable mismatch')
    files=[exe,archive,dist/'BUILD_MANIFEST.json',dist/'WINDOWS_EXE_VERIFICATION.json']
    (dist/'SHA256SUMS.txt').write_text(''.join(digest(path)+'  '+path.name+'\n' for path in files),encoding='utf-8')
    print(json.dumps({'windows_zip':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive)},indent=2))


if __name__=='__main__':main()
