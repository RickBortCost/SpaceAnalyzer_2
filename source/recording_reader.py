"""Read-only access to CSV/Parquet recordings created by OPC UA Web Client."""
from __future__ import annotations

import csv
import json
import math
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED = {".csv": "csv", ".parquet": "parquet",'.das':'das','.rbf':'rbf','.dat':'dat','.pda3seq':'dat'}
MAX_SIGNALS = 64
MAX_POINTS_PER_SIGNAL = 100_000


@lru_cache(maxsize=64)
def _summary(path, size, mtime_ns, metadata_mtime_ns):
    from recording_compare import scan_recording
    p = Path(path)
    library=RecordingLibrary(p.parent)
    if p.suffix.lower()=='.parquet':
        class SummaryLibrary(RecordingLibrary):
            def _iter_rows(self,path,*args):return self._iter_parquet(path,columns=['timestamp_ms','node_id','value_numeric','signal_name','data_type','sample_ms'])
        library=SummaryLibrary(p.parent)
    return scan_recording(library,p,allow_empty=True,allow_unordered=True)

@lru_cache(maxsize=32)
def _original_hash(path,size,mtime_ns):
    from iba_dat import digest
    return digest(path)


def _finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _truth(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "good"}


def _decimate_minmax(points, limit):
    """Peak-preserving time-bucket decimation. Keeps first/last and local min/max."""
    n = len(points)
    if n <= limit or limit < 4:
        return points
    step = max(1, math.ceil(n / max(1, limit // 6)))
    result = []
    for offset in range(0,n,step):
        chunk=points[offset:offset+step]
        picks={0,len(chunk)-1}
        valid=[i for i,p in enumerate(chunk) if p[1] is not None]
        if valid:
            picks.update((min(valid,key=lambda i:chunk[i][1]),max(valid,key=lambda i:chunk[i][1])))
        for quality in ('Bad','Uncertain'):
            i=next((i for i,p in enumerate(chunk) if len(p)>2 and p[2]==quality),None)
            if i is not None:picks.add(i)
        result.extend(chunk[i] for i in sorted(picks))
    return result



class RecordingLibrary:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def _path(self, name):
        if not isinstance(name, str) or not name or '\\' in name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError("Nome registrazione non valido")
        path = (self.directory / name).resolve()
        if not path.is_relative_to(self.directory) or path.suffix.lower() not in SUPPORTED:
            raise ValueError("Sono ammessi file CSV, Parquet, FDA DAS4, RBF v3 o iba DAT nella cartella Recordings")
        if not path.is_file():
            raise ValueError("Registrazione non trovata")
        return path

    @staticmethod
    def _metadata_path(path):
        return path.with_suffix(".metadata.json")

    def _metadata(self, path):
        meta = self._metadata_path(path)
        if not meta.is_file() and path.suffix.lower()=='.dat':meta=path.with_suffix('.json')
        if not meta.is_file():
            return {}
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def describe(self,name,*,display_name=None,source='repository'):
        from recording_preview import DESCRIPTIONS,fingerprint
        path=self._path(name);key=fingerprint(path,self._metadata(path))
        cached=None if path.suffix.lower()=='.pda3seq' else DESCRIPTIONS.get(key)
        if cached is None:
            cached=self._describe(name)
            if path.suffix.lower()!='.pda3seq':DESCRIPTIONS.put(key,cached)
        cached['source']=source
        if display_name is not None:cached['display_name']=str(display_name)
        return cached

    def _describe(self, name, *, display_name=None, source="repository"):
        path = self._path(name)
        stat = path.stat()
        meta = self._metadata(path)
        if path.suffix.lower()=='.pda3seq':
            from pda3.service import sequence_info
            _,meta=sequence_info(self,path)
        elif path.suffix.lower()=='.dat':
            from pda3.service import is_pda3,details
            if is_pda3(path):meta=details(path,meta)
        if path.suffix.lower()=='.das':
            from fda_das import inspect,channel_map
            h=inspect(path)
            if not meta.get('signals'):
                channels=channel_map(h)
                meta={**meta,'signals':[dict(nodeid=c['nodeid'],display_name=c['display_name'],unit=c['unit']) for c in channels],
                      'rows_written':h['frames']*len(channels),
                      'started_at':datetime.fromtimestamp(h['epoch'],timezone.utc).isoformat(),
                      'storage':{'format':'das','label':'FDA DAS4 · Float32 / Int16' if any(c['type']==4 for c in h['channels']) else 'FDA DAS4 · Int16 scalato','lossless':False}}
            if h['frames']:
                import struct
                with path.open('rb') as stream:
                    stream.seek(h['data_offset']); first=struct.unpack('<I',stream.read(4))[0]
                    stream.seek(h['data_offset']+(h['frames']-1)*h['record_size']); last=struct.unpack('<I',stream.read(4))[0]
                meta={**meta, 'started_at':datetime.fromtimestamp(h['epoch']+first/1000,timezone.utc).isoformat(),
                    'ended_at':datetime.fromtimestamp(h['epoch']+last/1000,timezone.utc).isoformat(),
                    'duration_seconds':(last-first)/1000}
        if path.suffix.lower()=='.rbf':
            from rbf import inspect,channel_map
            h=inspect(path)
            mapped=channel_map(h,meta)
            meta={**meta,'signals':[dict(nodeid=c['nodeid'],display_name=c['display_name'],unit=c.get('unit',''),data_type=c.get('data_type','')) for c in mapped],
                  'rows_written':h['frames']*len(mapped),'initial_sample_ms':h['sample_us']/1000,
                  'storage':{'format':'rbf','label':'RBF v3','lossless':False}}
            if h['frames']:
                meta.update(start_ms=h['start_ms'],end_ms=h['end_ms'],
                            started_at=datetime.fromtimestamp(h['start_ms']/1000,timezone.utc).isoformat(),
                            ended_at=datetime.fromtimestamp(h['end_ms']/1000,timezone.utc).isoformat(),
                            duration_seconds=(h['end_ms']-h['start_ms'])/1000)
        storage = meta.get("storage") if isinstance(meta.get("storage"), dict) else {}
        if path.suffix.lower() in ('.csv', '.parquet') and (not meta.get('signals') or not meta.get('started_at') or meta.get('duration_seconds') is None):
            sidecar = self._metadata_path(path)
            summary = _summary(str(path), stat.st_size, stat.st_mtime_ns, sidecar.stat().st_mtime_ns if sidecar.is_file() else 0)
            meta = {**summary, **meta}
            if not meta.get('signals'): meta['signals'] = summary['signals']
            if not meta.get('started_at') and summary['start_ms'] is not None: meta['started_at'] = datetime.fromtimestamp(summary['start_ms'] / 1000, timezone.utc).isoformat()
            if not meta.get('ended_at') and summary['end_ms'] is not None: meta['ended_at'] = datetime.fromtimestamp(summary['end_ms'] / 1000, timezone.utc).isoformat()
            if meta.get('duration_seconds') is None: meta['duration_seconds'] = summary['duration_seconds']
        if path.suffix.lower() == ".parquet" and not storage:
            # A copied binary recording remains recognizable without its sidecar.
            try:
                import pyarrow.parquet as pq
                footer = pq.ParquetFile(path).schema_arrow.metadata or {}
                value = json.loads(footer.get(b"storage", b"{}"))
                if isinstance(value, dict): storage = value
            except (ImportError, OSError, ValueError, TypeError):
                pass
        signals = []
        for row in meta.get("signals") or []:
            if isinstance(row, dict) and row.get("nodeid"):
                signals.append({"nodeid": str(row["nodeid"]), "display_name": str(row.get("display_name") or row["nodeid"]),
                    'unit': str(row.get('unit') or ''), 'calculated': bool(row.get('calculated') or str(row['nodeid']).startswith('calc:'))})
        return {
            "name": path.relative_to(self.directory).as_posix(),
            "display_name": str(display_name or meta.get('display_name') or path.name),
            "source": source,
            "format": SUPPORTED[path.suffix.lower()],
            "storage": storage,
            "format_label": storage.get("label") or SUPPORTED[path.suffix.lower()].upper(),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "started_at": meta.get("started_at"),
            "ended_at": meta.get("ended_at"),
            "start_ms": meta.get('start_ms'),
            "end_ms": meta.get('end_ms'),
            "duration_seconds": meta.get("duration_seconds"),
            "rows_written": meta.get("rows_written"),
            "sample_ms": meta.get("initial_sample_ms"),
            "endpoint": meta.get("endpoint") or "",
            "signals": signals,
            "view_source_note": meta.get('view_source_note',''),
            "comparison": meta.get('comparison'),
            "session": {k: meta.get(k) for k in ("session_name", "machine", "operator", "description", "session_id", "part", "cycles_in_file")},
        }

    def list(self,source='repository'):
        self.directory.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(self.directory.rglob('*'), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
            if any(p.startswith('.pda3_') for p in path.relative_to(self.directory).parts):continue
            if not path.is_file() or path.suffix.lower() not in SUPPORTED:
                continue
            name=path.relative_to(self.directory).as_posix()
            try:files.append(self.describe(name,source=source))
            except (ValueError,OSError) as exc:
                files.append({'name':name,'display_name':name,'source':source,'format':SUPPORTED[path.suffix.lower()],
                    'size_bytes':path.stat().st_size,'signals':[],'read_error':str(exc),'view_source_note':str(exc)})
        return files

    def _iter_csv(self, path):
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            missing = {'timestamp_ms', 'node_id', 'value_numeric'} - set(reader.fieldnames or [])
            if missing:
                raise ValueError('CSV non compatibile: colonne mancanti ' + ', '.join(sorted(missing)) + '. Usare il CSV esportato da Space Analyzer.')
            yield from reader

    def _iter_parquet(self, path,columns=None,signal_ids=None,start_ms=None,end_ms=None):
        try:
            import pyarrow.parquet as pq
        except (ImportError, OSError) as exc:
            raise ValueError("Supporto Apache Parquet non disponibile nel runtime") from exc
        parquet = pq.ParquetFile(path, page_checksum_verification=True)
        wanted = list(columns) if columns is not None else list(parquet.schema.names)
        available = set(parquet.schema.names)
        columns = [c for c in wanted if c in available]
        missing = {"timestamp_ms", "node_id", "value_numeric"} - available
        if missing:
            raise ValueError("File Parquet non compatibile: colonne mancanti " + ", ".join(sorted(missing)))
        import pyarrow as pa
        import pyarrow.compute as pc
        groups=[];time_index=parquet.schema.names.index('timestamp_ms')
        for index in range(parquet.num_row_groups):
            stats=parquet.metadata.row_group(index).column(time_index).statistics
            if stats is not None and stats.has_min_max:
                if start_ms is not None and stats.max<start_ms or end_ms is not None and stats.min>end_ms:continue
            groups.append(index)
        for batch in parquet.iter_batches(batch_size=16384,columns=columns,row_groups=groups):
            mask=None
            if signal_ids is not None and 'node_id' in batch.schema.names:mask=pc.is_in(batch.column('node_id'),value_set=pa.array(signal_ids,type=pa.string()))
            for bound,op in ((start_ms,pc.greater_equal),(end_ms,pc.less_equal)):
                if bound is not None:
                    match=op(batch.column('timestamp_ms'),bound);mask=pc.and_(mask,match) if mask is not None else match
            if mask is not None:batch=batch.filter(mask)
            data = batch.to_pydict()
            count = batch.num_rows
            for i in range(count):
                yield {key: values[i] for key, values in data.items()}

    def _iter_rows(self, path,signal_ids=None,start_ms=None,end_ms=None):
        if path.suffix.lower()=='.pda3seq':
            from pda3.service import iter_sequence
            return iter_sequence(self,path,signal_ids,start_ms,end_ms)
        if path.suffix.lower()=='.dat':
            from pda3.service import is_pda3,iter_rows
            return iter_rows(path,self._metadata(path),signal_ids,start_ms,end_ms) if is_pda3(path) else self._iter_dat_original(path)
        if path.suffix.lower()=='.das':
            from fda_das import iter_rows
            return iter_rows(path,self._metadata(path),signal_ids,start_ms,end_ms)
        if path.suffix.lower()=='.rbf':
            from rbf import iter_rows
            return iter_rows(path,self._metadata(path),signal_ids,start_ms,end_ms)
        return self._iter_csv(path) if path.suffix.lower() == ".csv" else self._iter_parquet(path,signal_ids=signal_ids,start_ms=start_ms,end_ms=end_ms)

    def _dat_source(self,path):
        meta=self._metadata(path);name=meta.get('original_archive')
        if not isinstance(name,str) or not name or '/' in name or '\\' in name or Path(name).name!=name or Path(name).suffix.lower()!='.parquet':
            raise ValueError('DAT: archivio originale associato non valido; non e un lettore DAT esterno universale')
        original=(path.parent/name).resolve()
        if original.parent!=path.parent.resolve() or not original.is_file():raise ValueError('DAT: conservare nella stessa cartella il Parquet originale associato')
        stat=original.stat()
        if _original_hash(str(original),stat.st_size,stat.st_mtime_ns)!=meta.get('original_sha256'):
            raise ValueError('DAT: checksum del Parquet originale non corrispondente')
        bounds=meta.get('original_range_keys')
        if not isinstance(bounds,list) or len(bounds)!=2:raise ValueError('DAT: intervallo originale mancante')
        for key in bounds:
            if not isinstance(key,list) or len(key)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in key):raise ValueError('DAT: intervallo originale non valido')
        if bounds[0]>bounds[1]:raise ValueError('DAT: intervallo invertito')
        ids={str(n['nodeid']) for n in meta.get('signals',[]) if isinstance(n,dict) and n.get('nodeid')}
        return meta,original,bounds,ids

    def _iter_dat_original(self,path):
        meta,original,bounds,ids=self._dat_source(path)
        for ordinal,row in enumerate(self._iter_parquet(original)):
            if str(row.get('node_id') or '') not in ids:continue
            key=[float(row['timestamp_ms']),int(row.get('cycle') or 0),ordinal]
            if bounds[0]<=key<=bounds[1]:yield row

    def load(self,name,signal_ids=None,start_ms=None,end_ms=None,max_points=25000):
        from recording_preview import VIEWS,fingerprint,load
        path=self._path(name)
        if signal_ids is not None and (not isinstance(signal_ids,list) or len(signal_ids)>MAX_SIGNALS):raise ValueError('Selezionare al massimo 64 segnali')
        ids=None if signal_ids is None else list(dict.fromkeys(str(n) for n in signal_ids if str(n)))
        start=_finite_float(start_ms) if start_ms is not None else None;end=_finite_float(end_ms) if end_ms is not None else None
        if start is not None and end is not None and end<start:raise ValueError("L'intervallo temporale deve avere una fine non precedente all'inizio")
        try:limit=max(1000,min(MAX_POINTS_PER_SIGNAL,int(max_points)))
        except (TypeError,ValueError,OverflowError):limit=25000
        meta=self._metadata(path)
        selected_count=len(ids) if ids is not None else len(meta.get('signals') or [])
        limit=min(limit,max(1000,250000//max(1,selected_count)))
        identity=fingerprint(path,meta);key=(identity,tuple(ids) if ids is not None else None,start,end,limit)
        cached=None if path.suffix.lower()=='.pda3seq' else VIEWS.get(key)
        if cached is not None:return cached
        result=self._load_legacy(name,ids,start,end,limit) if meta.get('comparison') else load(self,path,ids,start,end,limit)
        if path.suffix.lower()!='.pda3seq' and identity==fingerprint(path,self._metadata(path)):VIEWS.put(key,result)
        return result

    def _load_legacy(self, name, signal_ids=None, start_ms=None, end_ms=None, max_points=25000):
        path = self._path(name)
        if signal_ids is not None:
            if not isinstance(signal_ids, list) or len(signal_ids) > MAX_SIGNALS:
                raise ValueError(f"Selezionare al massimo {MAX_SIGNALS} segnali")
            selected = {str(x) for x in signal_ids if str(x)}
        else:
            selected = None
        start = _finite_float(start_ms) if start_ms is not None else None
        end = _finite_float(end_ms) if end_ms is not None else None
        if start is not None and end is not None and end < start:
            raise ValueError("L'intervallo temporale deve avere una fine non precedente all'inizio")
        try:
            limit = int(max_points)
        except (TypeError, ValueError):
            limit = 25000
        limit = max(1000, min(MAX_POINTS_PER_SIGNAL, limit))
        series, names, types = {}, {}, {}
        rows_scanned = numeric_rows = 0
        global_min = math.inf
        global_max = -math.inf
        samples = []
        for row in self._iter_rows(path,signal_ids,start,end):
            rows_scanned += 1
            nid = str(row.get("node_id") or "")
            if not nid or (selected is not None and nid not in selected):
                continue
            t = _finite_float(row.get("timestamp_ms"))
            if t is None or (start is not None and t < start) or (end is not None and t > end):
                continue
            value = _finite_float(row.get("value_numeric"))
            good = row.get("good", True)
            quality = row.get("quality") or ("Good" if _truth(good) else "Uncertain" if str(row.get("status", "")).startswith("Uncertain") else "Bad")
            series.setdefault(nid, []).append([t, value if quality=="Good" else None, quality])
            names[nid] = str(row.get("signal_name") or nid)
            types[nid] = str(row.get("data_type") or "Double")
            sm = _finite_float(row.get("sample_ms"))
            if sm is not None and len(samples) < 1000:
                samples.append(sm)
            numeric_rows += int(value is not None and quality=="Good")
            global_min = min(global_min, t)
            global_max = max(global_max, t)
        if selected is not None:
            # Preserve selected signals in the response even when a signal has no valid numeric samples.
            meta = self._metadata(path)
            meta_names = {str(n.get("nodeid")): str(n.get("display_name") or n.get("nodeid")) for n in (meta.get("signals") or []) if isinstance(n, dict) and n.get("nodeid")}
            for nid in selected:
                series.setdefault(nid, [])
                names.setdefault(nid, meta_names.get(nid, nid))
                types.setdefault(nid, "Double")
        original_counts = {nid: len(points) for nid, points in series.items()}
        for points in series.values(): points.sort(key=lambda p: p[0])
        returned = {nid: _decimate_minmax(points, limit) for nid, points in series.items()}
        meta = self._metadata(path)
        if meta.get('comparison'):
            info = {s['nodeid']: s for s in meta.get('signals', [])}
            for nid, points in series.items():
                period = _finite_float(info.get(nid, {}).get('sample_ms'))
                if period and period > 0:
                    gaps = [(a[0], b[0]) for a, b in zip(points, points[1:]) if b[0] - a[0] > period * 1.5]
                    if not gaps: continue
                    reduced = _decimate_minmax(points, max(4, limit // 2))
                    # Never draw a line through missing acquisition time. These
                    # display-only nulls are not included in exports or FFT rows.
                    gap_index, display = 0, []
                    for point in reduced:
                        first_gap = None
                        while gap_index < len(gaps) and gaps[gap_index][1] <= point[0]:
                            if first_gap is None: first_gap = gaps[gap_index]
                            gap_index += 1
                        if first_gap is not None:
                            display.append([(first_gap[0] + first_gap[1]) / 2, None, 'Bad'])
                        display.append(point)
                    returned[nid] = display
        return {
            "name": path.relative_to(self.directory).as_posix(),
            "format": SUPPORTED[path.suffix.lower()],
            "series": returned,
            "signals": [{"nodeid": nid, "display_name": names.get(nid, nid), "data_type": types.get(nid, "Double"), "points_original": original_counts.get(nid, 0), "points_returned": len(returned.get(nid, []))} for nid in returned],
            "rows_scanned": rows_scanned,
            "numeric_rows": numeric_rows,
            "start_ms": None if math.isinf(global_min) else global_min,
            "end_ms": None if math.isinf(global_max) else global_max,
            "max_points_per_signal": limit,
            "sample_ms": (sum(samples) / len(samples)) if samples else None,
            "decimated": any(original_counts.get(n, 0) > len(returned.get(n, [])) for n in returned),
        }

    def markers(self,name,signal_ids,x1,x2):
        from recording_preview import MARKERS,fingerprint
        path=self._path(name)
        if not isinstance(signal_ids,list) or not 1<=len(signal_ids)<=MAX_SIGNALS:raise ValueError('Selezionare almeno un segnale valido')
        a=_finite_float(x1);b=_finite_float(x2)
        if a is None or b is None:raise ValueError('Marker temporali non validi')
        key=(fingerprint(path,self._metadata(path)),tuple(str(n) for n in signal_ids),a,b)
        cached=None if path.suffix.lower()=='.pda3seq' else MARKERS.get(key)
        if cached is not None:return cached
        result=self._markers_uncached(name,signal_ids,a,b)
        if path.suffix.lower()!='.pda3seq' and key[0]==fingerprint(path,self._metadata(path)):MARKERS.put(key,result)
        return result

    def _markers_uncached(self, name, signal_ids, x1, x2):
        path = self._path(name)
        if not isinstance(signal_ids, list) or not signal_ids or len(signal_ids) > MAX_SIGNALS:
            raise ValueError("Selezionare almeno un segnale valido")
        wanted = {str(x) for x in signal_ids}
        targets = {"x1": _finite_float(x1), "x2": _finite_float(x2)}
        if any(v is None for v in targets.values()):
            raise ValueError("Marker temporali non validi")
        best = {nid: {key: None for key in targets} for nid in wanted}
        distance = {nid: {key: math.inf for key in targets} for nid in wanted}
        names = {nid: nid for nid in wanted}
        from recording_preview import plot_rows
        for nid,t,value,quality,name,_,_ in plot_rows(self,path,signal_ids):
            if nid not in wanted or value is None or quality!='Good':continue
            names[nid]=name
            for key, target in targets.items():
                d = abs(t - target)
                if d < distance[nid][key]:
                    distance[nid][key] = d
                    best[nid][key] = {"timestamp_ms": t, "value": value, "offset_ms": t - target}
        metadata = self._metadata(path)
        if metadata.get('comparison'):
            info = {s['nodeid']: s for s in metadata.get('signals', [])}
            for nid in wanted:
                signal = info.get(nid, {})
                period = _finite_float(signal.get('sample_ms'))
                for key, target in targets.items():
                    if target < signal.get('start_ms', -math.inf) or target > signal.get('end_ms', math.inf) or (period and distance[nid][key] > period * .75):
                        best[nid][key] = None
        return {"x1": targets["x1"], "x2": targets["x2"], "signals": [{"nodeid": nid, "display_name": names[nid], "x1": best[nid]["x1"], "x2": best[nid]["x2"]} for nid in signal_ids if nid in best]}
