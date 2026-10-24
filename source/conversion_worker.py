"""Isolated FDA/RBF conversion; only whitelisted local codecs are invoked."""
import json
from pathlib import Path
import sys

if __name__=='__main__':
    job=json.loads(Path(sys.argv[1]).read_text('utf-8'));answer=Path(job['answer'])
    try:
        if job['format']=='das':from fda_das import export_parquet
        elif job['format']=='rbf':from rbf import export_parquet
        else:raise ValueError('Formato conversione non supportato')
        result={'ok':True,'files':export_parquet(Path(job['path']),job['nodes'],job['limit'])}
    except Exception as exc:result={'ok':False,'error':str(exc)}
    answer.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    raise SystemExit(0 if result['ok'] else 1)
