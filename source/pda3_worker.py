"""Isolated export entry point, kept at the runtime root for embedded CPython."""
import json,sys
from pathlib import Path
from pda3.window_export import process_job
from pda3.storage import atomic_json

if __name__=='__main__':
    path=Path(sys.argv[1])
    try:
        job=json.loads(path.read_text('utf-8'))
        if job.get('historical_source'):
            from pda3.historical_export import materialize
            materialize(path,job)
        result=process_job(path);atomic_json(path.with_name(path.name+'.result'),{'ok':True,'recording':result})
    except Exception as exc:
        atomic_json(path.with_name(path.name+'.result'),{'ok':False,'error':str(exc)})
        sys.exit(1)
