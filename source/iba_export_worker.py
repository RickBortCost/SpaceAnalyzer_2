"""Isolates conversion CPU/SQLite work from the acquisition interpreter."""
import json
import logging
from pathlib import Path
import sys
from iba_dat import export_parquet,write_json

def main():
    request=json.loads(Path(sys.argv[1]).read_text('utf-8'))
    try:result={'ok':True,'files':export_parquet(request['path'],request['nodes'],request['limit'])}
    except Exception as exc:
        logging.exception('Conversione iba DAT fallita')
        result={'ok':False,'error':str(exc)}
    write_json(request['answer'],result)
    return 0 if result['ok'] else 1

if __name__=='__main__':sys.exit(main())
