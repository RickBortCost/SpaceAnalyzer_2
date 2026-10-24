"""Reproducible storage comparison on 30 s / 100 Hz / 8 synthetic signals.

Measures this host and dataset, not a real PAC performance guarantee.
Run with the application requirements installed; optionally pass a JSON output.
"""
import hashlib
import json
import math
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recordings import Recorder
from recording_reader import RecordingLibrary

BASE = 1700000000000.125
NODES = [{"nodeid": f"ads:PlcSlow.fbEntryPoint.fbAdsProductionDataRepository._aDtoWsgProdFieldData[1]._stWsgProdFieldData.Channel{i}",
          "display_name": f"Segnale {i}"} for i in range(8)]


def cycles():
    rng = random.Random(8102)
    for i in range(3000):
        stamp = BASE+i*10
        utc = datetime.fromtimestamp(stamp/1000, timezone.utc).isoformat(timespec="microseconds")
        values = [50+2*math.sin(i*.03)+rng.uniform(-.005,.005),
                  120+20*math.sin(i*.02), 1.2345678901234567, rng.uniform(-100,100),
                  bool(i//50 % 2), bool(i//250 % 2), 2**63-1-i, float(i//100)]
        rows = []
        for k, value in enumerate(values):
            q = "Bad" if k == 7 and i % 127 == 0 else "Good"
            rows.append({"nodeid": NODES[k]["nodeid"], "value": value, "plot_value": float(value),
                         "data_type": "BOOL" if k in (4,5) else "ULINT" if k == 6 else "LREAL",
                         "good": q == "Good", "quality": q, "status": q,
                         "status_code": 0 if q == "Good" else 0x80000000,
                         "source_ts": utc, "server_ts": utc, "local_ts": utc,
                         "source_filetime": str(116444736000000000+1700000000*10000000+1250+i*100000)})
        yield stamp, rows


def main():
    results, reference = [], None
    for fmt, profile in (("csv", None), ("parquet", "compatible"), ("parquet", "fast"),
                         ("parquet", "compact"), ("parquet", "maximum")):
        with tempfile.TemporaryDirectory(prefix="space-binary-bench-") as directory:
            rec = Recorder(directory, "1.10.0")
            options = {"buffer_seconds":120, "max_file_mb":1024}
            if profile: options["parquet_profile"] = profile
            begin = time.monotonic()
            rec.start(fmt, NODES, endpoint="ads://synthetic", sample_ms=10, options=options)
            enqueue_us = []
            for stamp, rows in cycles():
                tick = time.perf_counter()
                accepted = rec.enqueue(stamp, rows, 10)
                enqueue_us.append((time.perf_counter()-tick)*1e6)
                assert accepted, rec.status()
            state = rec.stop(timeout=60)
            seconds = time.monotonic()-begin
            assert state["state"] == "stopped" and state["dropped_cycles"] == 0, state
            assert state["rows_written"] == 24000 and state["cycles_received"] == 3000
            path = Path(state["path"])
            digest = hashlib.sha256()
            for row in RecordingLibrary(directory)._iter_rows(path):
                canonical = [float(row["timestamp_ms"]), int(row["cycle"]), row["node_id"], row["value_text"],
                             row["source_timestamp"], row["server_timestamp"], row["local_timestamp"], row["ads_filetime"]]
                digest.update(json.dumps(canonical, ensure_ascii=False).encode("utf-8"))
            actual = digest.hexdigest()
            if reference is None: reference = actual
            assert actual == reference, "Original values/timestamp sequence differ between profiles"
            result = {"format":fmt, "profile":profile or "text", "bytes":path.stat().st_size,
                      "bytes_with_metadata":path.stat().st_size+path.with_suffix(".metadata.json").stat().st_size,
                      "write_wall_seconds":round(seconds,4), "enqueue_mean_us":round(statistics.mean(enqueue_us),3),
                      "enqueue_p95_us":round(sorted(enqueue_us)[int(len(enqueue_us)*.95)],3),
                      "rows":state["rows_written"], "dropped_cycles":state["dropped_cycles"],
                      "original_values_and_timestamps_sha256":actual}
            results.append(result)
            print(json.dumps(result), flush=True)
    baseline = results[0]["bytes"]
    snappy = results[1]["bytes"]
    for result in results:
        result["saving_vs_csv_percent"] = round((1-result["bytes"]/baseline)*100,2)
        result["saving_vs_snappy_percent"] = round((1-result["bytes"]/snappy)*100,2)
    report = {"workload":"Synthetic ADS-like rows: 30 s, 100 Hz, 8 signals, 24000 rows",
              "lossless_verified":True, "resampling":False,
              "note":"Size/CPU results depend on data and hardware. Samples were submitted faster than real time.",
              "results":results}
    if len(sys.argv)>1: Path(sys.argv[1]).write_text(json.dumps(report,indent=2),encoding="utf-8")
    else: print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
