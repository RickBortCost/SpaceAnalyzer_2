import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recording_reader import RecordingLibrary


class RecordingReaderTests(unittest.TestCase):
    def test_csv_list_load_decimate_and_exact_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv"
            fields = ["timestamp_ms", "node_id", "signal_name", "value_numeric", "good", "data_type", "sample_ms"]
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for i in range(10000):
                    for node, factor in (("A", 1), ("B", 1000)):
                        writer.writerow({"timestamp_ms": 1_000_000 + i * 10, "node_id": node,
                                        "signal_name": "Signal " + node, "value_numeric": i * factor,
                                        "good": True, "data_type": "Double", "sample_ms": 10})
            path.with_suffix(".metadata.json").write_text(json.dumps({
                "signals": [{"nodeid": "A", "display_name": "Signal A"}, {"nodeid": "B", "display_name": "Signal B"}],
                "duration_seconds": 100, "rows_written": 20000
            }), encoding="utf-8")
            library = RecordingLibrary(directory)
            self.assertEqual(library.list()[0]["name"], "sample.csv")
            described = library.describe("sample.csv", display_name="Scelto dal PC.csv", source="local")
            self.assertEqual(described["display_name"], "Scelto dal PC.csv")
            self.assertEqual(described["source"], "local")
            loaded = library.load("sample.csv", ["A", "B"], max_points=5000)
            self.assertTrue(loaded["decimated"])
            self.assertEqual(loaded["numeric_rows"], 20000)
            self.assertLessEqual(len(loaded["series"]["A"]), 5000)
            markers = library.markers("sample.csv", ["A", "B"], 1_000_123, 1_000_456)
            self.assertEqual(markers["signals"][0]["x1"]["timestamp_ms"], 1_000_120)
            self.assertEqual(markers["signals"][0]["x2"]["timestamp_ms"], 1_000_460)
            with self.assertRaises(ValueError):
                library.load("../sample.csv")


if __name__ == "__main__":
    unittest.main(verbosity=2)
