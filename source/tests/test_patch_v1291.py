"""Prove di regressione V1.29.1: writer FDA/DAS4 e acquisizione ADS a notifiche.

Eseguire dalla cartella `source/` del progetto:
    python -m unittest discover -s tests -p test_patch_v1291.py -v
Non servono PLC, TwinCAT o pyarrow: il lettore Parquet e il router ADS sono simulati.
"""
import asyncio, ctypes as C, json, math, os, random, struct, sys, tempfile, threading, time, types, unittest
from pathlib import Path

SRC = Path(os.environ.get('SPACE_SOURCE_DIR') or Path(__file__).resolve().parents[1])
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import fda_das
import ads_notifications as ads

T0 = 1_790_000_000_000.0


class FakeLibrary:
    rows = []
    def __init__(self, directory): pass
    def _metadata(self, path): return {'started_at': '2026-10-24T08:00:00+00:00', 'initial_sample_ms': 10}
    def _iter_parquet(self, path, columns=None, **kwargs):
        for row in FakeLibrary.rows:
            yield dict(row)


def sample(t, cycle, nid, value, quality='Good'):
    return dict(timestamp_ms=t, cycle=cycle, node_id=nid, value_numeric=value, quality=quality, good=quality == 'Good')


class FdaTests(unittest.TestCase):
    def setUp(self):
        self.saved = sys.modules.get('recording_reader')
        fake = types.ModuleType('recording_reader'); fake.RecordingLibrary = FakeLibrary
        sys.modules['recording_reader'] = fake
        self.dir = tempfile.TemporaryDirectory(); self.orig = Path(self.dir.name) / 'rec.parquet'
        self.orig.write_bytes(b'x')

    def tearDown(self):
        if self.saved is None: sys.modules.pop('recording_reader', None)
        else: sys.modules['recording_reader'] = self.saved
        self.dir.cleanup()

    def export(self, rows, nodes, max_bytes=0):
        FakeLibrary.rows = rows
        return fda_das.export_parquet(self.orig, nodes, max_bytes)

    @staticmethod
    def nodes(n): return [{'nodeid': f'ads:MAIN.v{i}', 'display_name': f'v{i}'} for i in range(n)]

    def test_frames_group_by_millisecond_not_by_float_timestamp(self):
        nodes = self.nodes(64)
        rows = [sample(T0 + c * 10 + i * 0.0005, c * 64 + i, nodes[i]['nodeid'], float(i)) for c in range(100) for i in range(64)]
        files = self.export(rows, nodes)
        self.assertEqual(files[0]['frames'], 100)       # prima: 6400 frame (64x)

    def test_gain_uses_1_2_5_steps_and_keeps_resolution(self):
        self.assertEqual(fda_das.choose_gain(32767), 1.0)
        self.assertEqual(fda_das.choose_gain(32768), 2.0)   # prima 10.0
        self.assertEqual(fda_das.choose_gain(0), 1.0)
        rnd = random.Random(7)
        for _ in range(2000):
            peak = 10 ** rnd.uniform(-6, 12)
            gain = fda_das.choose_gain(peak)
            self.assertLessEqual(peak / gain, 32767)
            self.assertGreaterEqual(peak / gain, 32767 * 0.4)

    def test_round_trip_error_is_within_half_gain(self):
        rnd = random.Random(3); nodes = self.nodes(2)
        rows = [sample(T0 + c * 10, c, nodes[i]['nodeid'], rnd.uniform(-40000, 40000) if i == 0 else rnd.uniform(0, 3.3)) for c in range(300) for i in range(2)]
        files = self.export(rows, nodes)
        das = Path(self.dir.name) / files[0]['name']
        gains = {m['nodeid']: m['gain'] for m in json.loads(das.with_suffix('.metadata.json').read_text())['fda_channels']}
        sent = {(r['node_id'], int(r['timestamp_ms'])): r['value_numeric'] for r in rows}
        count = 0
        for r in fda_das.iter_rows(das):
            if r['quality'] != 'Good': continue
            original = sent[(r['node_id'], int(r['timestamp_ms']))]
            self.assertLessEqual(abs(r['value_numeric'] - original), gains[r['node_id']] / 2 + 1e-12); count += 1
        self.assertEqual(count, 600)

    def test_uncertain_keeps_its_value(self):
        nodes = self.nodes(1)
        rows = [sample(T0 + c * 10, c, nodes[0]['nodeid'], 100.0 + c, 'Uncertain' if c % 2 else 'Good') for c in range(6)]
        das = Path(self.dir.name) / self.export(rows, nodes)[0]['name']
        got = [(r['value_text'], r['quality']) for r in fda_das.iter_rows(das)]
        self.assertEqual(got[1], ('101.0', 'Uncertain'))
        # Bad e non numerici restano 0 con Q=1
        rows = [sample(T0, 0, nodes[0]['nodeid'], None, 'Bad'), sample(T0 + 10, 1, nodes[0]['nodeid'], 5.0)]
        das2 = Path(self.dir.name) / self.export(rows, nodes)[0]['name']
        self.assertEqual(next(iter(fda_das.iter_rows(das2)))['quality'], 'Bad')

    def test_recording_longer_than_uint32_ms_continues_in_new_part(self):
        nodes = self.nodes(1); big = 49.8 * 86400 * 1000
        rows = [sample(T0, 0, nodes[0]['nodeid'], 1.0), sample(T0 + big, 1, nodes[0]['nodeid'], 2.0)]
        files = self.export(rows, nodes)
        self.assertEqual([f['frames'] for f in files], [1, 1])
        times = []
        for f in files:
            times += [r['timestamp_ms'] for r in fda_das.iter_rows(Path(self.dir.name) / f['name'])]
        self.assertAlmostEqual(times[1] - times[0], big, delta=1)

    def test_stale_part_file_does_not_block_export(self):
        (Path(self.dir.name) / 'rec_FDA0001.das.part').write_bytes(b'stale')
        files = self.export([sample(T0, 0, 'ads:MAIN.v0', 1.0)], self.nodes(1))
        self.assertEqual(files[0]['frames'], 1)
        self.assertFalse((Path(self.dir.name) / 'rec_FDA0001.das.part').exists())

    def test_backwards_clock_is_reported_in_metadata(self):
        nodes = self.nodes(1)
        rows = [sample(T0 + c * 10, c, nodes[0]['nodeid'], 1.0) for c in range(5)] + [sample(T0 - 5000 + c * 10, 5 + c, nodes[0]['nodeid'], 2.0) for c in range(5)]
        files = self.export(rows, nodes)
        meta = json.loads((Path(self.dir.name) / files[0]['name']).with_suffix('.metadata.json').read_text())
        self.assertEqual(meta['time_discontinuities']['count'], 1)
        self.assertEqual(meta['time_discontinuities']['examples'][0]['signal'], nodes[0]['nodeid'])

    def test_low_disk_space_aborts_cleanly_and_keeps_original(self):
        original = fda_das.enough_space
        fda_das.enough_space = lambda *a, **k: False
        try:
            with self.assertRaises(ValueError): self.export([sample(T0, 0, 'ads:MAIN.v0', 1.0)], self.nodes(1))
        finally: fda_das.enough_space = original
        self.assertTrue(self.orig.exists())
        self.assertEqual([p.name for p in Path(self.dir.name).iterdir()], ['rec.parquet'])

    def test_long_nodeid_description_round_trips_without_sidecar(self):
        nid = 'ads:' + 'GVL.Machine.Axis.' * 12 + 'Pos'
        self.assertLess(len(nid), 251)
        das = Path(self.dir.name) / self.export([sample(T0, 0, nid, 1.0)], [{'nodeid': nid, 'display_name': 'p'}])[0]['name']
        das.with_suffix('.metadata.json').unlink()
        self.assertEqual(fda_das.channel_map(fda_das.inspect(das))[0]['nodeid'], nid)


class FakeClient:
    """Router TwinCAT simulato: stessa interfaccia usata da NotificationAcquisition."""
    last = None
    def __init__(self, cfg):
        FakeClient.last = self
        self.cfg = cfg; self.info = {}; self.writer = True; self.port = 1; self.address = ads.AmsAddr()
        self.handles = {}; self.symbols = {}; self.catalog_loaded = True; self.datatypes = None
        self.state = 5; self.subs = {}; self.emitting = threading.Event(); self.alive = True
        def add(port, addr, group, handle, attr, callback, user, reference):
            number = len(self.subs) + 1; reference._obj.value = number; self.subs[number] = (callback, user); return 0
        def delete(port, addr, handle): return 0
        self.dll = types.SimpleNamespace(AdsSyncAddDeviceNotificationReqEx=add, AdsSyncDelDeviceNotificationReqEx=delete)
        threading.Thread(target=self._emit_loop, daemon=True).start()
    def _emit_loop(self):
        while self.alive:
            if self.emitting.is_set():
                for callback, user in list(self.subs.values()):
                    buf = C.create_string_buffer(C.sizeof(ads.NotificationHeader) + 4)
                    h = ads.NotificationHeader.from_buffer(buf)
                    h.handle = user; h.timestamp = int(time.time() * 1e7) + ads.FILETIME_EPOCH; h.size = 4
                    buf[C.sizeof(ads.NotificationHeader):] = struct.pack('<f', 1.5)
                    callback(C.pointer(ads.AmsAddr()), C.cast(buf, C.POINTER(ads.NotificationHeader)), user)
            time.sleep(0.01)
    async def connect(self): self.info['ads_state'] = self.state
    async def threaded(self, function, *args): return function(*args)
    def check(self, code):
        if code: raise RuntimeError(code)
    async def get_symbol(self, name): return dict(name=name, size=4, type='REAL', datatype=4)
    async def readwrite(self, group, offset, size, data): return b'\x01\0\0\0'
    async def read(self, group, offset, size): return b'\0\0\0\0'
    async def read_state(self): self.info['ads_state'] = self.state; return self.state
    async def release_handles(self): pass
    async def abort(self): pass
    async def disconnect(self): self.alive = False
    def row(self, nid, symbol, data): return dict(nodeid=nid, value=struct.unpack('<f', data)[0], good=True, quality='Good')


class AdsTests(unittest.TestCase):
    def test_queue_scales_with_channels_and_rate(self):
        a = ads.NotificationAcquisition({}, lambda: None, None, None)
        self.assertEqual(a.scale_queue(1000, 10)[0], 200000)    # 2 s a 100.000 campioni/s (prima: 32.768 = 0,33 s)
        self.assertEqual(a.scale_queue(64, 10)[0], 32768)       # il valore configurato resta il minimo
        a.base_capacity = 40000
        self.assertEqual(a.scale_queue(10, 1000)[0], 40000)

    def test_queue_limits_are_capped(self):
        a = ads.NotificationAcquisition({}, lambda: None, None, None)
        capacity, byte_limit = a.scale_queue(100000, 1)
        self.assertEqual(capacity, 1000000); self.assertLessEqual(byte_limit, 256 * 1024 ** 2)

    def test_watchdog_threshold(self):
        expired = self.original                      # soglia reale (setUp la accorcia per gli altri test)
        self.assertTrue(expired([0.0], 5.1, 10))
        self.assertFalse(expired([0.0], 4.9, 10))
        self.assertFalse(expired([0.0], 20.0, 5000))   # 5 periodi di 5 s

    def _worker(self):
        FakeClient.last = None                         # mai riusare il client simulato del test precedente
        received = []; reports = []
        worker = ads.NotificationAcquisition({}, lambda: (['ads:MAIN.a', 'ads:MAIN.b'], 10, ('g',)),
                                             lambda ts, rows, ms, generation: received.append(len(rows)), reports.append, client_factory=FakeClient)
        return worker, received

    @staticmethod
    def wait_for(condition, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition(): return True
            time.sleep(0.05)
        return False

    def setUp(self):
        self.original = ads.NotificationAcquisition.watchdog_expired
        ads.NotificationAcquisition.watchdog_expired = staticmethod(lambda times, now, ms: any(now - t > 0.6 for t in times))

    def tearDown(self):
        ads.NotificationAcquisition.watchdog_expired = staticmethod(self.original)

    def test_plc_stop_does_not_abort_acquisition(self):
        worker, received = self._worker(); worker.start()
        try:
            self.assertTrue(self.wait_for(lambda: FakeClient.last is not None, 5))
            client = FakeClient.last; client.emitting.set()
            self.assertTrue(self.wait_for(lambda: len(received) > 10, 5))
            client.state = 6                                  # PLC in STOP: lo stato viene letto entro 1 s
            self.assertTrue(self.wait_for(lambda: worker.paused_not_run, 5))
            client.emitting.clear()                           # ...poi le notifiche cessano
            time.sleep(3)                                     # molto oltre la soglia del watchdog
            self.assertTrue(worker.thread.is_alive()); self.assertIsNone(worker.error)
            before = len(received); client.state = 5; client.emitting.set()
            self.assertTrue(self.wait_for(lambda: len(received) > before + 10, 5))
            self.assertTrue(self.wait_for(lambda: not worker.paused_not_run, 5))
        finally:
            worker.stop(); worker.thread.join(5)
        self.assertIsNone(worker.error)

    def test_silent_notifications_in_run_still_trigger_watchdog(self):
        worker, received = self._worker(); worker.start()
        try:
            self.assertTrue(self.wait_for(lambda: FakeClient.last is not None, 5))
            client = FakeClient.last; client.emitting.set()
            self.assertTrue(self.wait_for(lambda: len(received) > 10, 5))
            client.emitting.clear()                           # stato RUN, ma nessun dato
            self.assertTrue(self.wait_for(lambda: not worker.thread.is_alive(), 6))
            self.assertIn('Watchdog', worker.error or '')
        finally:
            worker.stop(); worker.thread.join(5)


if __name__ == '__main__':
    unittest.main()
