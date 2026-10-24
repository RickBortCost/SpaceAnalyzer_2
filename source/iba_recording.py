"""Original recording followed by an isolated native iba conversion process."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from converted_recording import ConvertedRecording

def export_in_worker(path,nodes,limit):
    with tempfile.TemporaryDirectory(prefix='SpaceAnalyzer_iba_worker_') as directory:
        root=Path(directory);job=root/'request.json';answer=root/'result.json'
        job.write_text(json.dumps({'path':str(path),'nodes':nodes,'limit':limit,'answer':str(answer)},ensure_ascii=False),encoding='utf-8')
        # Only the native DLL job has a deadline. Large source archives may take longer to stage.
        process=subprocess.run([sys.executable,str(Path(__file__).with_name('iba_export_worker.py')),str(job)],
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if not answer.is_file():raise ValueError('Processo conversione iba interrotto; originali conservati')
        result=json.loads(answer.read_text('utf-8'))
        if process.returncode or not result.get('ok'):raise ValueError(result.get('error') or 'Errore processo conversione iba')
        return result['files']

class IBARecording(ConvertedRecording):
    def __init__(self,directory,version,capacity,recorder_factory):
        super().__init__(directory,version,capacity,recorder_factory,exporter=export_in_worker,
            fmt='dat',title='iba DAT',suffix='_IBA0001.dat',phase_key='iba_phase')
