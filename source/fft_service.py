"""Bounded FFT subprocess; no ADS or HTTP services are imported by the worker."""
import json
from pathlib import Path
import subprocess
import sys
import threading
_busy=threading.Lock()
def calculate(rows,nodeid,start,end,window,detrend,options):
    if not _busy.acquire(False):raise ValueError('Un calcolo FFT e gia in corso')
    try:
        points=[];available_start=None;available_end=None
        for r in rows:
            if r.get('node_id')!=nodeid:continue
            try:t=float(r['timestamp_ms'])
            except (KeyError,TypeError,ValueError):continue
            available_start=t if available_start is None else min(available_start,t);available_end=t if available_end is None else max(available_end,t)
            if not start<=t<=end:continue
            q=r.get('quality') or ('Good' if str(r.get('good',True)).lower() in ('1','true') else 'Bad')
            points.append([t,r.get('value_numeric'),q,r.get('ads_filetime')])
            if len(points)>262144:raise ValueError('FFT: massimo 262.144 campioni; ridurre intervallo')
        if len(points)<16:
            from datetime import datetime,timezone
            def stamp(t):return datetime.fromtimestamp(t/1000,timezone.utc).isoformat(timespec='milliseconds')
            coverage=(' Nessun campione disponibile per questo segnale.' if available_start is None else ' Campioni disponibili da '+stamp(available_start)+' a '+stamp(available_end)+'.')
            raise ValueError(f'FFT: nell’intervallo richiesto ci sono {len(points)} campioni originali (minimo 16).'+coverage+' Se la vista live e precedente al buffer, riprendere Play oppure analizzare la registrazione.')
        payload=dict(points=points,nodeid=nodeid,start=start,end=end,window=window,detrend=detrend,options=options)
        p=subprocess.run([sys.executable,str(Path(__file__).with_name('fft_worker.py'))],input=json.dumps(payload),text=True,encoding='utf-8',capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode:raise ValueError('Processo FFT terminato con errore: '+p.stderr[-1000:])
        result=json.loads(p.stdout)
        if not result['ok']:raise ValueError(result['error'])
        return result['spectrum']
    except subprocess.TimeoutExpired:raise ValueError('FFT: superati 30 secondi; ridurre numero blocchi') from None
    finally:_busy.release()
