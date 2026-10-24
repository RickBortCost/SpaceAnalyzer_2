"""Lossless binary round trips, real codec metadata and bounded file rotation."""
import json
import math
import random
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyarrow.parquet as pq
from recordings import Recorder, prepare_format
from recording_reader import RecordingLibrary
from analysis_tools import export_rows, spectrum

BASE = 1700000000000.125
NODES = [{"nodeid": "ads:PlcSlow.fb.Values.Double", "display_name": "Carico"},
         {"nodeid": "ads:PlcSlow.fb.Values.Counter", "display_name": "Contatore"}]


def sample(value, node=NODES[0], quality="Good"):
    return {"nodeid": node["nodeid"], "value": value, "plot_value": float(value),
            "data_type": "LREAL" if node is NODES[0] else "ULINT", "good": quality == "Good",
            "quality": quality, "status": quality, "status_code": 0 if quality == "Good" else 0x80000000,
            "source_ts": "2026-10-02T00:00:00.1234567+00:00",
            "server_ts": "2026-10-02T00:00:00.123456789+00:00",
            "local_ts": "2026-10-02T00:00:00.123457+00:00", "source_filetime": "134037504001234567"}


class BinaryRecordings(unittest.TestCase):
    def test_all_profiles_preserve_numeric_bits_original_values_and_timestamps(self):
        values = [-0.0, 0.0, 0.1, math.nextafter(1.0, 2.0),
                  5e-324, sys.float_info.max, -sys.float_info.max, 1.2345678901234567]
        for profile in ("compact", "maximum", "fast", "compatible"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                rec = Recorder(directory, "1.10.0")
                try:
                    prepare_format("parquet", {"parquet_profile": profile})
                    rec.start("parquet", NODES, sample_ms=10, endpoint="ads://192.168.5.220/192.168.100.12.1.1:851",
                              options={"parquet_profile": profile, "max_file_mb": 0})
                    for i, value in enumerate(values):
                        self.assertTrue(rec.enqueue(BASE+i*10, [sample(value), sample(2**64-1-i, NODES[1])], 10))
                    rec.enqueue(BASE+80, [sample(8.25, quality="Uncertain")], 10)
                    rec.enqueue(BASE+90, [sample(9.5, quality="Bad")], 10)
                    state = rec.stop(timeout=30)
                    self.assertEqual(state["state"], "stopped", state)
                    self.assertEqual(state["dropped_cycles"], 0)
                    self.assertEqual(state["pending_rows"], 0)
                    parquet = pq.ParquetFile(state["path"], page_checksum_verification=True)
                    rows = list(RecordingLibrary(directory)._iter_rows(Path(state["path"])))
                    self.assertEqual(len(rows), 18)
                    for i, value in enumerate(values):
                        row, counter = rows[i*2:i*2+2]
                        self.assertEqual(struct.pack("!d", row["value_numeric"]), struct.pack("!d", value))
                        self.assertEqual(struct.pack("!d", row["timestamp_ms"]), struct.pack("!d", BASE+i*10))
                        self.assertEqual(row["value_text"], str(value))
                        self.assertEqual(counter["value_text"], str(2**64-1-i))
                        self.assertEqual(row["sample_ms"], 10)
                        for key, original in (("source_timestamp", "source_ts"), ("server_timestamp", "server_ts"),
                                              ("local_timestamp", "local_ts"), ("ads_filetime", "source_filetime")):
                            self.assertEqual(row[key], sample(value)[original])
                    self.assertEqual([row["quality"] for row in rows[-2:]], ["Uncertain", "Bad"])
                    self.assertEqual([row["value_numeric"] for row in rows[-2:]], [None, None])
                    self.assertEqual([row["value_text"] for row in rows[-2:]], ["8.25", "9.5"])
                    numeric = parquet.metadata.row_group(0).column(parquet.schema.names.index("value_numeric"))
                    self.assertEqual(numeric.compression, "SNAPPY" if profile == "compatible" else "ZSTD")
                    if profile != "compatible": self.assertIn("BYTE_STREAM_SPLIT", numeric.encodings)
                    footer = json.loads(parquet.schema_arrow.metadata[b"storage"])
                    self.assertEqual(footer["profile"], profile)
                    meta = json.loads(Path(state["path"]).with_suffix(".metadata.json").read_text())
                    self.assertEqual(meta["storage"], footer)
                finally:
                    rec.stop(timeout=30)

    def test_empty_binary_and_footer_profile_without_sidecar(self):
        for profile in ("compact", "maximum", "fast", "compatible"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                rec = Recorder(directory, "1.10.0")
                rec.start("parquet", NODES, options={"parquet_profile": profile})
                state = rec.stop(timeout=30)
                self.assertEqual(state["state"], "stopped", state)
                self.assertEqual(pq.read_table(state["path"]).num_rows, 0)
                Path(state["path"]).with_suffix(".metadata.json").unlink()
                file = RecordingLibrary(directory).describe(Path(state["path"]).name)
                self.assertEqual(file["storage"]["profile"], profile)

    def test_rotation_uses_compressed_size_and_never_splits_a_cycle(self):
        for profile in ("compact", "maximum", "fast", "compatible"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                rec = Recorder(directory, "1.10.0")
                try:
                    rec.start("parquet", NODES, sample_ms=10,
                              options={"parquet_profile": profile, "max_file_mb": .03, "buffer_seconds": 120})
                    rng = random.Random(912)
                    for i in range(1800):
                        self.assertTrue(rec.enqueue(BASE+i*10, [sample(rng.uniform(-1e8, 1e8)), sample(2**64-1-i, NODES[1])], 10))
                    state = rec.stop(timeout=30)
                    self.assertEqual(state["state"], "stopped", state)
                    self.assertGreater(len(state["files"]), 1)
                    rows, seen = [], set()
                    for part in state["files"]:
                        path = Path(directory)/part["name"]
                        self.assertLessEqual(path.stat().st_size, int(.03*1024*1024))
                        block = pq.read_table(path).to_pylist()
                        cycles = {r["cycle"] for r in block}
                        self.assertFalse(cycles & seen)
                        seen |= cycles
                        self.assertTrue(all(sum(r["cycle"] == cycle for r in block) == 2 for cycle in cycles))
                        rows.extend(block)
                    self.assertEqual(len(rows), 3600)
                    self.assertEqual([row["cycle"] for row in rows], [i for i in range(1,1801) for _ in NODES])
                    self.assertEqual([row["timestamp_ms"] for row in rows], [BASE+i*10 for i in range(1800) for _ in NODES])
                finally:
                    rec.stop(timeout=30)

    def test_invalid_profile_and_unavailable_codec_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder(directory, "1.10.0")
            for options in ({"parquet_profile": "unknown"}, {"parquet_profile": []}, []):
                with self.assertRaises(ValueError): rec.start("parquet", NODES, options=options)
            with patch("pyarrow.Codec") as codec:
                codec.is_available.return_value = False
                with self.assertRaisesRegex(ValueError, "ZSTD"): prepare_format("parquet")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_binary_overflow_drains_accepted_cycles_without_silent_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder(directory, "1.10.0", queue_capacity=2)
            gate, run = threading.Event(), rec._run
            with patch.object(rec, "_run", side_effect=lambda: (gate.wait(5), run())):
                rec.start("parquet", NODES)
                self.assertTrue(rec.enqueue(BASE, [sample(1.0)], 10))
                self.assertTrue(rec.enqueue(BASE+10, [sample(2.0)], 10))
                self.assertFalse(rec.enqueue(BASE+20, [sample(3.0)], 10))
                gate.set()
                state = rec.stop(timeout=30)
            self.assertEqual(state["state"], "error")
            self.assertEqual(state["dropped_cycles"], 1)
            self.assertEqual(pq.read_table(state["path"])["value_numeric"].to_pylist(), [1.0,2.0])

    def test_binary_recording_fft_export_and_original_timestamp_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            rec = Recorder(directory, "1.10.0")
            rec.start("parquet", NODES[:1], sample_ms=10, options={"max_file_mb": 0})
            for i in range(256):
                row = sample(math.sin(2*math.pi*12.5*i/100))
                row["source_filetime"] = str(134037504001234567+i*100000)
                self.assertTrue(rec.enqueue(BASE+i*10, [row], 10))
            state = rec.stop(timeout=30)
            library = RecordingLibrary(directory)
            rows = list(library._iter_rows(Path(state["path"])))
            self.assertEqual([row["timestamp_ms"] for row in rows], [BASE+i*10 for i in range(256)])
            self.assertEqual(library.load(Path(state["path"]).name)["signals"][0]["points_original"], 256)
            fft = spectrum(iter(rows), NODES[0]["nodeid"], BASE, BASE+2550,
                           options={"block_size": 256})
            self.assertEqual(fft["sample_hz"], 100)
            self.assertAlmostEqual(fft["peak_hz"], 12.5)
            self.assertFalse(fft["resampled"])
            export = Path(directory)/"selection.parquet"
            self.assertEqual(export_rows(iter(rows[32:96]), export, "parquet"), 64)
            out = list(library._iter_rows(export))
            self.assertEqual([row["timestamp_ms"] for row in out], [BASE+i*10 for i in range(32,96)])
            self.assertEqual([struct.pack("!d", row["value_numeric"]) for row in out],
                             [struct.pack("!d", row["value_numeric"]) for row in rows[32:96]])
            self.assertEqual(pq.ParquetFile(export).metadata.row_group(0).column(0).compression, "ZSTD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
