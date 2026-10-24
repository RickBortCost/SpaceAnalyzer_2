"""Read actual CSV/Parquet files and exercise recorder failure boundaries."""
import csv
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recordings import Recorder, COLUMNS
import pyarrow.parquet as pq

NODES = [{"nodeid": "ns=2;s=Temperature", "display_name": 'Temperatura, "°C"'},
         {"nodeid": "ns=2;s=Count", "display_name": "Contatore"}]
ROWS = [{"nodeid": NODES[0]["nodeid"], "value": 21.5, "plot_value": 21.5,
         "data_type": "Double", "status": "Good", "good": True,
         "source_ts": "2026-09-10T00:00:00+00:00", "server_ts": "2026-09-10T00:00:00+00:00"},
        {"nodeid": NODES[1]["nodeid"], "value": str(2**63-1), "plot_value": float(2**63-1),
         "data_type": "Int64", "status": "Good", "good": True}]


class Files(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="opcua-record-test-")
        self.rec = Recorder(Path(self.temp.name) / "Recordings", "1.1.11")

    def tearDown(self):
        self.rec.stop(timeout=10)
        self.temp.cleanup()

    def test_csv_and_parquet_roundtrip(self):
        for fmt in ("csv", "parquet"):
            with self.subTest(format=fmt):
                status = self.rec.start(fmt, NODES, sample_ms=10)
                self.assertTrue(status["busy"])
                with self.assertRaises(ValueError):
                    self.rec.start(fmt, NODES)
                base = time.time() * 1000
                for cycle in range(150):
                    self.assertTrue(self.rec.enqueue(base + cycle*10, ROWS, 10))
                # Quality failure must produce an explicit gap, not a misleading number.
                bad = {**ROWS[0], "good": False, "status": "BadOutOfService", "value": None}
                self.rec.enqueue(base+1500, [bad], 10)
                end = self.rec.stop()
                self.assertEqual(end["state"], "stopped", end)
                self.assertEqual(end["rows_written"], 301)
                self.assertEqual(end["cycles_received"], 151)
                path = Path(end["path"])
                self.assertEqual(path.parent, Path(self.temp.name)/"Recordings")
                self.assertFalse(self.rec.partial.exists())
                if fmt == "csv":
                    with path.open(encoding="utf-8-sig", newline="") as stream:
                        reader = csv.DictReader(stream)
                        self.assertEqual(reader.fieldnames, COLUMNS)
                        rows = list(reader)
                    self.assertEqual(rows[-1]["value_numeric"], "")
                else:
                    table = pq.read_table(path)
                    self.assertEqual(table.column_names, COLUMNS)
                    self.assertEqual(str(table.schema.field("timestamp_utc").type), "timestamp[us, tz=UTC]")
                    rows = table.to_pylist()
                    self.assertIsNone(rows[-1]["value_numeric"])
                self.assertEqual(len(rows), 301)
                self.assertEqual(rows[0]["signal_name"], NODES[0]["display_name"])
                self.assertEqual(rows[1]["value_text"], str(2**63-1))
                self.assertEqual(rows[-1]["status"], "BadOutOfService")
                meta = json.loads(path.with_suffix(".metadata.json").read_text())
                self.assertEqual(meta["rows_written"], 301)
                self.assertEqual(meta["reason"], "user")
                self.assertEqual(self.rec.stop()["path"], str(path))

    def test_empty_parquet_has_valid_footer(self):
        self.rec.start("parquet", NODES)
        state = self.rec.stop()
        self.assertEqual(pq.read_table(state["path"]).num_rows, 0)

    def test_bad_format_and_unwritable_directory(self):
        with self.assertRaises(ValueError):
            self.rec.start("xlsx", NODES)
        with self.assertRaises(ValueError):
            self.rec.start("csv", [])
        self.rec.directory.write_text("not a directory")
        with self.assertRaisesRegex(ValueError, "Impossibile creare"):
            self.rec.start("csv", NODES)
        self.assertFalse(self.rec.busy)

    def test_initial_metadata_failure_is_recoverable(self):
        with patch.object(self.rec, "_save_metadata", side_effect=OSError("metadata read-only")):
            with self.assertRaises(ValueError):
                self.rec.start("parquet", NODES)
        self.assertFalse(self.rec.busy)
        self.assertTrue(self.rec.stream.closed)
        self.rec.start("csv", NODES)
        self.assertEqual(self.rec.stop()["state"], "stopped")

    def test_overflow_is_explicit_and_drains_accepted_cycles(self):
        self.rec.capacity = 2
        gate = threading.Event()
        real_run = self.rec._run
        with patch.object(self.rec, "_run", side_effect=lambda: (gate.wait(5), real_run())):
            self.rec.start("csv", NODES)
            for _ in range(2):
                self.assertTrue(self.rec.enqueue(time.time()*1000, ROWS, 10))
            self.assertFalse(self.rec.enqueue(time.time()*1000, ROWS, 10))
            gate.set()
            state = self.rec.stop()
        self.assertEqual(state["state"], "error")
        self.assertEqual(state["reason"], "buffer_overflow")
        self.assertEqual(state["rows_written"], 4)
        self.assertEqual(state["dropped_cycles"], 1)
        self.assertTrue(Path(state["path"]).name.endswith(".part"))

    def test_write_failure_does_not_claim_success(self):
        with patch.object(self.rec, "_flush", side_effect=OSError("disk full")):
            self.rec.start("csv", NODES)
            self.rec.enqueue(time.time()*1000, ROWS, 10)
            state = self.rec.stop()
        self.assertEqual(state["state"], "error")
        self.assertIn("disk full", state["error"])
        self.assertEqual(state["rows_written"], 0)
        self.assertFalse(self.rec.final.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
