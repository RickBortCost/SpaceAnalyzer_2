"""Exact interval export and single-sided amplitude FFT on original acquisition rows."""
import cmath
import csv
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from recordings import COLUMNS
from recording_storage import storage_profile, parquet_options, parquet_metadata


def interval(data):
    try: a, b = float(data['x1']), float(data['x2'])
    except (KeyError, TypeError, ValueError): raise ValueError('Posizionare X1 e X2 prima di analizzare o esportare') from None
    if not math.isfinite(a) or not math.isfinite(b) or a == b:
        raise ValueError('Intervallo X1–X2 non valido')
    return min(a,b), max(a,b)


def selected_rows(rows, ids, start, end):
    ids=set(ids)
    if not ids or len(ids)>64: raise ValueError('Selezionare da 1 a 64 segnali')
    for row in rows:
        try: t=float(row.get('timestamp_ms'))
        except (ValueError,TypeError): continue
        if row.get('node_id') in ids and start<=t<=end:
            yield row


def spectrum(rows,nodeid,start,end,window='hann',detrend=True,options=None):
    from fft_engine import analyze
    return analyze(rows,nodeid,start,end,window,detrend,options)


def export_rows(rows, path, fmt):
    """Stream complete original rows; preserve bad quality and all timestamps."""
    from recordings import Recorder
    count=0
    stream=None;writer=None
    try:
        if fmt=='csv':
            stream=Path(path).open('x',encoding='utf-8-sig',newline='')
            writer=csv.DictWriter(stream,fieldnames=COLUMNS,extrasaction='ignore');writer.writeheader()
            for row in rows:
                writer.writerow(row);count+=1
        elif fmt=='parquet':
            import pyarrow as pa
            import pyarrow.parquet as pq
            schema=pa.schema([(c, pa.float64() if c in ('timestamp_ms','elapsed_ms','value_numeric') else pa.int64() if c in ('cycle','sample_ms','status_code') else pa.bool_() if c=='good' else pa.string()) for c in COLUMNS])
            storage=storage_profile('parquet')
            schema=schema.with_metadata(parquet_metadata(storage))
            writer=pq.ParquetWriter(str(path),schema,**parquet_options(schema,storage));batch=[]
            for row in rows:
                clean={}
                for c in COLUMNS:
                    v=row.get(c)
                    if v is None or v=='': clean[c]=None
                    elif c=='good':clean[c]=str(v).lower() in ('true','1')
                    elif c in ('cycle','sample_ms','status_code'):clean[c]=int(v)
                    elif c in ('timestamp_ms','elapsed_ms','value_numeric'):clean[c]=float(v)
                    else:clean[c]=v.isoformat() if isinstance(v,datetime) else str(v)
                batch.append(clean);count+=1
                if len(batch)>=storage['row_group_rows']:writer.write_table(pa.Table.from_pylist(batch,schema=schema));batch=[]
            if batch:writer.write_table(pa.Table.from_pylist(batch,schema=schema))
        else:raise ValueError('Formato export non valido')
        if count==0:raise ValueError('Nessun campione nell’intervallo X1–X2')
    finally:
        if fmt=='parquet' and writer:writer.close()
        if stream:stream.close()
    return count
