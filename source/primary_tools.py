"""Offline primary inspection/recovery/export; also shipped in the Windows runtime."""
import argparse
import itertools
import json
import time
import sys
import os
import math
from pathlib import Path
from primary_format import inspect,iter_samples,recover


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['inspect','recover','export']);p.add_argument('path',type=Path)
    p.add_argument('--accept-pda3-template-time',action='store_true')
    p.add_argument('--job',type=Path);p.add_argument('--background',action='store_true')
    p.add_argument('--format',choices=['csv','parquet','das','rbf','pda3','dat']);p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.background and os.name!='nt':
        try:os.nice(10)
        except OSError:pass
    if args.command=='inspect':result=inspect(args.path)
    elif args.command=='recover':result=recover(args.path,args.output)
    else:
        if not args.format or not args.output:p.error('export richiede --format e --output (cartella)')
        meta=inspect(args.path)['metadata'];job=json.loads(args.job.read_text('utf-8')) if args.job else {};nodes=job.get('nodes',meta['channels'])
        from recordings import Recorder,prepare_format
        options={**job.get('options',{}),'conversion_isolated':True,'acquisition_info':{**meta.get('acquisition_info',{}),'primary_capture_id':meta.get('session_id'),'primary_started_ns':meta.get('started_ns'),'reconstructed_from_primary':True}}
        if args.accept_pda3_template_time:options['pda3_accept_template_time']=True
        prepare_format(args.format,options);rec=Recorder(args.output,meta.get('application_version','primary-export'))
        rec.start(args.format,nodes,job.get('endpoint',meta.get('endpoint','')),sample_ms=job.get('sample_ms',10),options=options)
        if job.get('source_diagnostics'):rec.source_diagnostics(job['source_diagnostics'])
        try:
            source=(row for row in iter_samples(args.path) if not (job.get('exclude_notifications') and row.get('acquisition_origin')=='notification'))
            for t,group in itertools.groupby(source,lambda r:r['timestamp_ms']):
                rows=list(group)
                for row in rows:
                    value=row.get('source_value',row['value']);numeric=isinstance(value,(int,float)) and math.isfinite(value)
                    row.update(good=row['quality']=='Good',plot_value=float(value) if numeric else None)
                while rec.busy and rec.status().get('queue_cycles',0)>=max(1,rec.status().get('queue_capacity_cycles',2000)//2):time.sleep(.005)
                if not rec.enqueue(t,rows,rows[0].get('sample_ms') or 10):raise RuntimeError(rec.status().get('error') or 'Export queue overflow')
            result=rec.stop('primary_export',5)
            while result['busy']:result=rec.stop('primary_export',.25)
        finally:
            if rec.busy:rec.stop('export_failed',5)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__':main()
