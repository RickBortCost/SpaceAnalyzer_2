"""Bounded asynchronous recording of acquisition samples, independent of the plot."""
import csv
import logging
import re
import json
import math
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from recording_storage import storage_profile, parquet_options, parquet_metadata

COLUMNS = ["timestamp_utc", "timestamp_ms", "elapsed_ms", "cycle", "node_id", "signal_name",
           "value_numeric", "value_text", "data_type", "status", "good",
           "source_timestamp", "server_timestamp", "sample_ms", "quality", "status_code", "local_timestamp", "ads_filetime", "source_protocol", "acquisition_timestamp_ms", "acquisition_origin"]


def source_protocol(endpoint):
    return 'mixed' if endpoint.startswith('mixed://') else 'mqtt' if endpoint.startswith(('mqtt://','mqtts://')) else 's7' if endpoint.startswith('s7://') else 'ads' if endpoint.startswith('ads://') else 'opcua'


def prepare_format(fmt, options=None):
    """Load optional native codecs before entering the acquisition event loop."""
    storage = storage_profile(fmt, options)
    if storage["format"] in ("parquet",'das','rbf','dat','pda3'):
        try:
            import pyarrow
            import pyarrow.parquet
        except (ImportError, OSError) as exc:
            raise ValueError("Supporto Parquet non disponibile. Avviare l'EXE completo oppure installare requirements.txt.") from exc
        codec='zstd' if storage['format'] in ('das','rbf','dat','pda3') else storage['codec']
        if not pyarrow.Codec.is_available(codec):
            raise ValueError("Codec binario " + storage["codec"].upper() + " non disponibile nel runtime")
    if storage['format']=='dat':
        from iba_dat import bridge
        bridge.require()
    if storage['format']=='pda3':
        from pda3.template_loader import template
        template()


class Recorder:
    def __init__(self, directory, version, queue_capacity=2000):
        self.directory = Path(directory).resolve()
        self.version = version
        self.capacity = queue_capacity
        self.lock = threading.RLock()
        self.worker = None
        self._state = {"state": "idle", "format": "", "filename": "", "path": "",
                       "rows_written": 0, "cycles_received": 0, "dropped_cycles": 0,
                       "error": "", "started_at": None, "ended_at": None, "reason": ""}
        self.stop_event = threading.Event()
        self.node_names = {}
        self.node_ids = []
        self.started_mono = 0
        self.fda_session = None

    @property
    def busy(self):
        if self.fda_session is not None:return self.fda_session.busy
        with self.lock:
            return self._state["state"] in {"recording", "stopping"}

    def status(self):
        if self.fda_session is not None:return self.fda_session.status()
        with self.lock:
            result = dict(self._state)
            result["part"] = getattr(self, "segment", 0)
            result["files"] = list(getattr(self, "part_files", []))
            result["queue_capacity_cycles"] = self.capacity
            result["buffer_seconds_effective"] = self.capacity * getattr(self,"current_sample_ms",500) / 1000
            result["queue_cycles"] = self.q.qsize() if hasattr(self, "q") else 0
            result["session"] = {k: getattr(self, "metadata", {}).get(k, "") for k in ("session_name", "machine", "operator", "description")}
            result["directory"] = str(self.directory)
            result["storage"] = dict(getattr(self, "storage", {}))
            result["format_label"] = result["storage"].get("label") or result["format"].upper()
            result["pending_rows"] = getattr(self, "pending_rows", 0)
            result["node_ids"] = list(self.node_ids)
            result["nodes"] = [{"nodeid": nid, "display_name": self.node_names[nid], "node_class": "Variable"} for nid in self.node_ids]
            result["active"] = result["state"] == "recording"
            result["busy"] = result["state"] in {"recording", "stopping"}
            if getattr(self,'pda3_rotation',False):
                result.update(window_index=getattr(self,'timestamp_window',0),window_origin_ms=getattr(self,'timestamp_origin',None),last_timestamp_ms=getattr(self,'last_timestamp',None))
            if result["busy"]:
                result["duration_seconds"] = round(time.monotonic() - self.started_mono, 3)
            else:
                result.setdefault("duration_seconds", 0)
            current = self.partial if result["busy"] else Path(result["path"]) if result["path"] else None
            try:
                result["bytes_written"] = current.stat().st_size if current else 0
            except OSError:
                result["bytes_written"] = 0
            return result

    def start(self, fmt, nodes, endpoint="", sample_ms=500, options=None):
        fmt = str(fmt).lower()
        storage = storage_profile(fmt, options)
        if not nodes:
            raise ValueError("Selezionare almeno una variabile prima di avviare Record")
        if fmt in ('das','rbf','dat','pda3'):
            if fmt=='pda3':
                from pda3.recording import PDA3SegmentRecorder as Session
            elif fmt=='dat':
                if len(nodes)>64:raise ValueError('iba DAT: massimo 64 variabili selezionate, con 3 canali DAT per variabile')
                from iba_dat import bridge
                bridge.require_prepared()
                from iba_recording import IBARecording as Session
            elif fmt=='rbf':
                if len(nodes)>64:raise ValueError('RBF: massimo 64 variabili selezionate')
                from rbf_recording import RBFRecording as Session
            else:
                from fda_recording import FDARecording as Session
            with self.lock:
                if self.busy or self.worker and self.worker.is_alive():raise ValueError('Una registrazione e gia in corso o in fase di chiusura')
                session=Session(self.directory,self.version,self.capacity,Recorder)
                result=session.start(nodes,endpoint,sample_ms,options)
                self.fda_session=session
                return result
        with self.lock:
            if self.busy or self.worker and self.worker.is_alive():
                raise ValueError("Una registrazione è già in corso o in fase di chiusura")
            self.fda_session=None
            options = options or {}
            self.pda3_rotation=options.get('rotation_time_basis')=='pda3_timestamp'
            self.timestamp_origin=None;self.timestamp_window=0;self.last_timestamp=None
            if self.pda3_rotation:storage={**storage,'flush_seconds':1}
            self.storage = storage
            self.pending_rows = 0
            def limit(key, default, low, high):
                try: value = float(options.get(key, default))
                except (ValueError, TypeError): raise ValueError(f"Parametro registrazione non valido: {key}") from None
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"{key}: valore tra {low} e {high}")
                return value
            self.max_bytes = int(limit("max_file_mb", 1024, 0, 102400) * 1024 * 1024)
            self.max_duration = limit("max_duration_s", 0, 0, 86400 * 7)
            buffer_s = limit("buffer_seconds", 10, 1, 120)
            self.capacity = max(1, min(50000, 200000 // len(nodes), math.ceil(buffer_s * 1000 / sample_ms))) if options else self.capacity
            if '_queue_capacity_cycles' in options:self.capacity=max(1,min(50000,200000//len(nodes),int(options['_queue_capacity_cycles'])))
            labels = {key: str(options.get(key) or "")[:size] for key, size in
                      (("session_name", 100), ("machine", 100), ("operator", 100), ("description", 2000))}
            self.segment = 1
            self.segment_rows = 0
            self.segment_first = self.segment_last = self.segment_elapsed = None
            self.segment_cycle_count=0;self.segment_last_cycle=None
            self.part_files = []
            self.parquet_pending = []
            self.parquet_bound = 0
            pa = pq = None
            if fmt == "parquet":
                try:
                    import pyarrow as pa
                    import pyarrow.parquet as pq
                except (ImportError, OSError) as exc:
                    raise ValueError("Supporto Parquet non disponibile. Avviare l'EXE completo oppure installare requirements.txt.") from exc
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                now = datetime.now(timezone.utc)
                name = ("MIXED_" if endpoint.startswith("mixed://") else "MQTT_" if endpoint.startswith(("mqtt://","mqtts://")) else "S7_" if endpoint.startswith('s7://') else "ADS_" if endpoint.startswith("ads://") else "OPCUA_") + now.strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
                prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", labels["session_name"]).strip("_")[:60]
                self.session_base = (prefix + "_" if prefix else "") + name
                self.final = self.directory / (self.session_base + "_p0001." + fmt)
                self.partial = Path(str(self.final) + ".part")
                self.stream = self.partial.open("x", newline="", encoding="utf-8-sig") if fmt == "csv" else self.partial.open("xb")
                self.writer = None
                if fmt == "csv":
                    self.writer = csv.DictWriter(self.stream, fieldnames=COLUMNS, lineterminator="\r\n")
                    self.writer.writeheader()
                    self.stream.flush()
                else:
                    self.schema = pa.schema([
                        ("timestamp_utc", pa.timestamp("us", tz="UTC")), ("timestamp_ms", pa.float64()),
                        ("elapsed_ms", pa.float64()), ("cycle", pa.int64()), ("node_id", pa.string()),
                        ("signal_name", pa.string()), ("value_numeric", pa.float64()), ("value_text", pa.string()),
                        ("data_type", pa.string()), ("status", pa.string()), ("good", pa.bool_()),
                        ("source_timestamp", pa.string()), ("server_timestamp", pa.string()), ("sample_ms", pa.int32()),
                        ("quality", pa.string()), ("status_code", pa.uint32()), ("local_timestamp", pa.string()), ("ads_filetime", pa.string()), ("source_protocol", pa.string()), ("acquisition_timestamp_ms", pa.float64()), ("acquisition_origin", pa.string())
                    ], metadata={b"application": b"OPC UA Web Client", b"version": self.version.encode(),
                                 b"layout": b"long: one row per node per acquisition cycle", b"time_zone": b"UTC",
                                 b"source_protocol": source_protocol(endpoint).encode('ascii'),
                                 **({b'acquisition_info':json.dumps(options['acquisition_info'],ensure_ascii=False).encode('utf-8')}
                                    if isinstance(options.get('acquisition_info'),dict) else {}),
                                 **parquet_metadata(storage)})
                    self.parquet_options = parquet_options(self.schema, storage)
                    self.writer = pq.ParquetWriter(self.stream, self.schema, **self.parquet_options)
            except Exception as exc:
                if getattr(self, "stream", None):
                    self.stream.close()
                raise ValueError(f"Impossibile creare la registrazione in {self.directory}: {exc}. Spostare l'applicativo in una cartella scrivibile.") from exc
            self.pa, self.pq = pa, pq
            self.q = queue.Queue(maxsize=self.capacity)
            self.stop_event = threading.Event()
            self.node_ids = [n["nodeid"] for n in nodes]
            self.node_names = {n["nodeid"]: n.get("display_name") or n["nodeid"] for n in nodes}
            self.started_mono = time.monotonic()
            self.current_sample_ms = sample_ms
            self._state = {"state": "recording", "format": fmt, "filename": self.final.name, "path": str(self.final),
                           "rows_written": 0, "cycles_received": 0, "dropped_cycles": 0, "error": "",
                           "started_at": now.isoformat(), "ended_at": None, "reason": "", "duration_seconds": 0}
            self.metadata = {"version": self.version, "format": fmt, "started_at": now.isoformat(),
                             "endpoint": endpoint, "initial_sample_ms": sample_ms, "signals": nodes,
                             "source_protocol": source_protocol(endpoint),
                             "quality_semantics": "S7 ReadVar result; CPU RUN/STOP state not queried" if endpoint.startswith('s7://') else "ADS result code; Uncertain when runtime not RUN" if endpoint.startswith("ads://") else "OPC UA StatusCode",
                             "timestamp_semantics": "Local read completion; source/server timestamps unavailable in S7 ReadVar; reads are not a PLC-cycle snapshot" if endpoint.startswith('s7://') else "Local acquisition time; source/server timestamps unavailable in ADS Read" if endpoint.startswith("ads://") else "OPC UA source/server plus local acquisition time",
                             "layout": "long", "timestamps": "UTC", "source": "acquisition before chart decimation",
                             "storage": dict(storage), "format_label": storage["label"],
                             **labels, "session_id": self.session_base, "max_file_bytes": self.max_bytes,
                             "max_duration_s": self.max_duration, "queue_capacity_cycles": self.capacity}
            if self.pda3_rotation:
                self.metadata.update(pda3_requested=True,pda3_tolerance_ms=options.get('pda3_tolerance_ms',1),pda3_auto_split=options.get('pda3_auto_split',True),pda3_resampling=options.get('pda3_resampling'))
            if options.get('calculation_formulas'):
                self.metadata['calculation_formulas'] = options['calculation_formulas']
                self.metadata['calculation_timestamp_semantics'] = 'SourceTimestamp = calculation timeline; local timestamp = computation time; ServerTimestamp unavailable'
            if isinstance(options.get('acquisition_info'),dict):
                info=options['acquisition_info']
                self.metadata['acquisition_info']=dict(info)
                if info.get('protocols'):
                    self.metadata['timestamp_order']='arrival'
                    self.metadata['quality_semantics']='Per-row protocol quality and status code; no cross-protocol quality synthesis'
                    self.metadata['timestamp_semantics']='PC UTC arrival shared axis; original transport/source/server clocks in each row'
                if info.get('mode') in ('symbolic','udp'):
                    self.metadata['quality_semantics']=info.get('quality_origin',self.metadata['quality_semantics'])
                    self.metadata['timestamp_semantics']=info.get('timestamps',self.metadata['timestamp_semantics'])
            try:
                self._save_metadata()
                self.worker = threading.Thread(target=self._run, name="OPCUA-Recorder", daemon=False)
                self.worker.start()
            except Exception as exc:
                self._state.update(state="error", error=f"Impossibile avviare la registrazione: {exc}",
                                   reason="start_error", path=str(self.partial), ended_at=datetime.now(timezone.utc).isoformat())
                try:
                    if fmt == "parquet":
                        self.writer.close()
                finally:
                    self.stream.close()
                self.worker = None
                raise ValueError(self._state["error"]) from exc
            logging.info("Recording started: format=%s signals=%d", fmt, len(nodes))
            return self.status()

    def enqueue(self, timestamp_ms, rows, sample_ms):
        """Never wait for file I/O on the acquisition loop."""
        if self.fda_session is not None:return self.fda_session.enqueue(timestamp_ms,rows,sample_ms)
        with self.lock:
            if self._state["state"] != "recording":
                return False
            self.current_sample_ms = sample_ms
            selected = [dict(r) for r in rows if r.get("nodeid") in self.node_names]
            if not selected:
                return True
            cycle = self._state["cycles_received"] + 1
            item = (timestamp_ms, (time.monotonic() - self.started_mono) * 1000, cycle, selected, sample_ms)
            try:
                self.q.put_nowait(item)
                self._state["cycles_received"] = cycle
                return True
            except queue.Full:
                self._state["dropped_cycles"] += 1
                self._state["error"] = "Disco troppo lento: buffer di registrazione pieno. Registrazione interrotta; controllare il file parziale."
                self._state["state"], self._state["reason"] = "stopping", "buffer_overflow"
                self.stop_event.set()
                return False

    def fail(self, message, reason='source_acquisition_error'):
        """An upstream acquisition failure must not leave a complete-looking file."""
        if self.fda_session is not None:return self.fda_session.original.fail(message,reason)
        with self.lock:
            if self._state['state']=='recording':
                self._state.update(error=message,state='stopping',reason=reason)
                self.stop_event.set()

    def source_diagnostics(self, diagnostics):
        if self.fda_session is not None:return self.fda_session.original.source_diagnostics(diagnostics)
        with self.lock:
            if hasattr(self,'metadata'):self.metadata['acquisition_final']=dict(diagnostics)

    def stop(self, reason="user", timeout=5):
        if self.fda_session is not None:return self.fda_session.stop(reason,timeout)
        with self.lock:
            if self._state["state"] == "recording":
                self._state["state"], self._state["reason"] = "stopping", reason
                self.stop_event.set()
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=timeout)
        return self.status()

    def _rows(self, item):
        ts, elapsed, cycle, rows, sample_ms = item
        stamp = datetime.fromtimestamp(ts / 1000, timezone.utc)
        for row in rows:
            value = row.get("value")
            if value is None:
                text = None
            elif isinstance(value, (dict, list, tuple, bool)):
                text = json.dumps(value, ensure_ascii=False, default=str)
            else:
                text = str(value)
            numeric = row.get("plot_value")
            if not row.get("good") or not isinstance(numeric, (float, int)) or not math.isfinite(numeric):
                numeric = None
            yield {"timestamp_utc": stamp if self._state["format"] == "parquet" else stamp.isoformat(timespec="milliseconds"),
                   "timestamp_ms": ts, "elapsed_ms": elapsed, "cycle": cycle,
                   "node_id": row["nodeid"], "signal_name": self.node_names[row["nodeid"]],
                   "value_numeric": numeric, "value_text": text, "data_type": row.get("data_type") or "Null",
                   "status": row.get("status") or row.get("error") or "Unknown", "good": bool(row.get("good")),
                   "source_timestamp": row.get("source_ts"), "server_timestamp": row.get("server_ts"), "sample_ms": sample_ms,
                   "quality": row.get("quality") or ("Good" if row.get("good") else "Uncertain" if str(row.get("status", "")).startswith("Uncertain") else "Bad"),
                   "status_code": row.get("status_code"), "local_timestamp": row.get("local_ts") or stamp.isoformat(timespec="microseconds"), "ads_filetime": row.get("source_filetime"),
                   "source_protocol": row.get("source_protocol") or ("calculated" if row["nodeid"].startswith("calc:") else row["nodeid"].split(":",1)[0] if row["nodeid"].startswith(("ads:","s7:","mqtt:")) else "opcua"),
                   "acquisition_timestamp_ms": row.get("acquisition_timestamp_ms", ts),
                   "acquisition_origin": row.get("acquisition_origin") or "acquisition"}

    def _commit_rows(self, rows):
        if not rows: return
        if self.segment_first is None:
            self.segment_first = rows[0]["timestamp_ms"]
            self.segment_elapsed = rows[0]["elapsed_ms"]
        # MQTT publishers can deliver old source times late. The original file
        # keeps arrival order; its advertised bounds must include all samples.
        times=[row['timestamp_ms'] for row in rows]
        self.segment_first=min(self.segment_first,min(times))
        self.segment_last=max(self.segment_last,max(times)) if self.segment_last is not None else max(times)
        self.segment_rows += len(rows)
        for row in rows:
            if row['cycle']!=self.segment_last_cycle:
                self.segment_cycle_count+=1;self.segment_last_cycle=row['cycle']
        with self.lock:
            self._state["rows_written"] += len(rows)
            self.pending_rows = max(0, self.pending_rows - len(rows))

    def _flush(self, batch):
        # Never split a complete acquisition cycle between files.
        import itertools
        import io
        if self._state['format']=='csv' and not self.pda3_rotation and not self.max_duration and batch:
            buffer=io.StringIO(newline='');csv.DictWriter(buffer,fieldnames=COLUMNS,lineterminator='\r\n').writerows(batch)
            block=buffer.getvalue()
            if not self.max_bytes or self.stream.tell()+len(block.encode('utf-8'))<=self.max_bytes:
                self.stream.write(block);self._commit_rows(batch);self.stream.flush();return
        for _, group in itertools.groupby(batch, key=lambda row: row["cycle"]):
            rows = list(group)
            if self.pda3_rotation:
                t=rows[0]['timestamp_ms']
                if self.timestamp_origin is None:self.timestamp_origin=t
                window=max(0,int((t-self.timestamp_origin)//600000))
                if window>self.timestamp_window:
                    if self._state['format']=='parquet':self._flush_parquet()
                    if self.segment_rows:self._rotate()
                    self.timestamp_window=window
                self.last_timestamp=t
            elapsed = rows[0]["elapsed_ms"]
            origin = self.segment_elapsed
            if origin is None and self.parquet_pending: origin = self.parquet_pending[0]["elapsed_ms"]
            if not self.pda3_rotation and origin is not None and self.max_duration and elapsed - origin >= self.max_duration * 1000:
                if self._state["format"] == "parquet": self._flush_parquet()
                if self.segment_rows: self._rotate()
            if self._state["format"] == "csv":
                buffer = io.StringIO(newline="")
                csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\r\n").writerows(rows)
                block = buffer.getvalue(); size = len(block.encode("utf-8"))
                if self.max_bytes and self.stream.tell() + size > self.max_bytes:
                    if self.segment_rows: self._rotate()
                    if self.stream.tell() + size > self.max_bytes:
                        raise ValueError("Limite file inferiore a un ciclo completo CSV; aumentare la dimensione")
                self.stream.write(block)
                self._commit_rows(rows)
            else:
                self.parquet_pending.extend(rows)
                if len(self.parquet_pending) >= self.storage["row_group_rows"]: self._flush_parquet()
        if self._state["format"] == "parquet": self._flush_parquet()
        self.stream.flush()

    def _flush_parquet(self):
        if not self.parquet_pending:return
        rows,self.parquet_pending=self.parquet_pending,[]
        self._write_parquet_rows(rows)
        self.stream.flush()

    def _write_parquet_rows(self, rows):
        table = self.pa.Table.from_pylist(rows, schema=self.schema)
        encoded_size = 0
        # Encode only once when comfortably below the size limit. Exact
        # preflight compression is retained near rotation, including small caps.
        reserve=max(16*1024**2,4*table.nbytes+4*len(self.schema.serialize())+65536)
        if self.max_bytes>64*1024**2 and self.stream.tell()+reserve<self.max_bytes:
            self.writer.write_table(table,row_group_size=len(rows))
            self.parquet_bound=self.stream.tell()+reserve;self._commit_rows(rows);return
        if self.max_bytes:
            # Size a complete standalone row group, including schema and footer.
            # Summed standalone files overestimate a multi-group footer; extra
            # per-group headroom covers larger absolute offsets in merged files.
            sink = self.pa.BufferOutputStream()
            self.pq.write_table(table, sink, row_group_size=len(rows), **self.parquet_options)
            encoded_size = sink.tell()
            if encoded_size > self.max_bytes:
                cycles = [i for i in range(1,len(rows)) if rows[i]["cycle"] != rows[i-1]["cycle"]]
                if not cycles:raise ValueError("Limite Parquet inferiore a un ciclo completo e al suo footer; aumentare la dimensione")
                split=min(cycles,key=lambda i:abs(i-len(rows)/2))
                self._write_parquet_rows(rows[:split]);self._write_parquet_rows(rows[split:]);return
            bound = encoded_size if not self.segment_rows else self.parquet_bound + encoded_size + 4096
            if self.segment_rows and bound > self.max_bytes:self._rotate()
        self.writer.write_table(table, row_group_size=len(rows))
        self.parquet_bound = encoded_size if not self.segment_rows else self.parquet_bound + encoded_size + 4096
        self._commit_rows(rows)

    def _finish_part(self):
        if self._state["format"] == "parquet":
            self._flush_parquet()
            self.writer.close()
        self.stream.flush(); os.fsync(self.stream.fileno()); self.stream.close()
        if self.max_bytes and self.partial.stat().st_size > self.max_bytes:
            raise ValueError("Limite file superato durante la finalizzazione; file parziale conservato")
        os.replace(self.partial, self.final)
        self._save_metadata(finalized=True)
        self.part_files.append({"name": self.final.name, "rows": self.segment_rows,
                                "bytes": self.final.stat().st_size, "part": self.segment,
                                **({'window_index':self.timestamp_window} if self.pda3_rotation else {})})
        logging.info("Record part finalized: %s rows=%d", self.final.name, self.segment_rows)

    def _rotate(self):
        self._finish_part()
        self.segment += 1
        self.parquet_bound = 0
        self.segment_rows = 0
        self.segment_cycle_count=0;self.segment_last_cycle=None
        self.segment_first = self.segment_last = self.segment_elapsed = None
        fmt = self._state["format"]
        self.final = self.directory / (self.session_base + f"_p{self.segment:04d}." + fmt)
        self.partial = Path(str(self.final) + ".part")
        self.stream = self.partial.open("x", newline="", encoding="utf-8-sig") if fmt == "csv" else self.partial.open("xb")
        if fmt == "csv":
            self.writer = csv.DictWriter(self.stream, fieldnames=COLUMNS, lineterminator="\r\n")
            self.writer.writeheader(); self.stream.flush()
        else:
            self.writer = self.pq.ParquetWriter(self.stream, self.schema, **self.parquet_options)
        with self.lock:
            self._state.update(filename=self.final.name, path=str(self.final))
        self._save_metadata()

    def _save_metadata(self, finalized=False):
        meta = dict(self.metadata)
        meta.update(self._state)
        meta.update(part=self.segment, session_rows=self._state["rows_written"],
                    rows_written=self.segment_rows, cycles_in_file=self.segment_cycle_count)
        if self.pda3_rotation:meta.update(pda3_window_index=self.timestamp_window,pda3_window_origin_ms=self.timestamp_origin)
        if self.segment_first is not None:
            meta["started_at"] = datetime.fromtimestamp(self.segment_first / 1000, timezone.utc).isoformat()
            meta["ended_at"] = datetime.fromtimestamp(self.segment_last / 1000, timezone.utc).isoformat()
            meta["duration_seconds"] = (self.segment_last - self.segment_first) / 1000
        if finalized: meta.update(state="stopped", path=str(self.final), reason="rotation" if self.busy else self._state["reason"])
        path = self.final.with_suffix(".metadata.json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _run(self):
        batch, last_flush, failed = [], time.monotonic(), False
        try:
            while not self.stop_event.is_set() or not self.q.empty():
                try:
                    item = self.q.get(timeout=.1)
                    batch.extend(self._rows(item))
                    with self.lock: self.pending_rows = len(batch)
                except queue.Empty:
                    pass
                if batch and (len(batch) >= self.storage["row_group_rows"] or time.monotonic() - last_flush >= self.storage["flush_seconds"]):
                    self._flush(batch)
                    batch = []
                    with self.lock: self.pending_rows = 0
                    last_flush = time.monotonic()
            self._flush(batch)
            with self.lock: self.pending_rows = 0
        except Exception as exc:
            failed = True
            logging.exception("Recording write failure")
            with self.lock:
                self._state["error"] = f"Errore di scrittura: {exc}"
                self._state["state"], self._state["reason"] = "stopping", "write_error"
                self.stop_event.set()
        finally:
            try:
                if self._state["format"] == "parquet":
                    self._flush_parquet()
                    self.writer.close()
                self.stream.flush()
                os.fsync(self.stream.fileno())
            except Exception as exc:
                failed = True
                with self.lock:
                    self._state["error"] = self._state["error"] or f"Impossibile finalizzare il file: {exc}"
            finally:
                try:
                    self.stream.close()
                except Exception as exc:
                    failed = True
                    with self.lock:
                        self._state["error"] = self._state["error"] or f"Impossibile chiudere il file: {exc}"
            with self.lock:
                if not failed and not self._state["error"] and self.max_bytes and self.partial.stat().st_size > self.max_bytes:
                    self._state["error"] = "Limite file superato durante la finalizzazione; file parziale conservato"
                if not failed and not self._state["error"]:
                    try:
                        os.replace(self.partial, self.final)
                        self.part_files.append({"name": self.final.name, "rows": self.segment_rows, "bytes": self.final.stat().st_size, "part": self.segment,
                            **({'window_index':self.timestamp_window} if self.pda3_rotation else {})})
                        logging.info("Recording stopped: %s rows=%d parts=%d", self.session_base, self._state["rows_written"], self.segment)
                    except OSError as exc:
                        self._state["error"] = f"Impossibile finalizzare il nome del file: {exc}"
                if self._state["error"]:
                    logging.error("Recording error: %s", self._state["error"])
                    self._state["state"], self._state["path"] = "error", str(self.partial)
                else:
                    self._state["state"] = "stopped"
                self._state["ended_at"] = datetime.now(timezone.utc).isoformat()
                self._state["duration_seconds"] = round(time.monotonic() - self.started_mono, 3)
                try:
                    self._save_metadata()
                except OSError as exc:
                    self._state["error"] = self._state["error"] or f"File dati salvato; metadati non aggiornati: {exc}"
                    self._state["state"] = "error"
