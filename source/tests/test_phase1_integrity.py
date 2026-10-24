"""Phase 1 integrity additions: ADS block notifications, upstream gap detection,
integrity events inside the primary capture, sliding rates. Simulated DLL/PLC only."""
import ctypes as C
import struct
import tempfile
import time
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from test_ads_notifications import NotifyDLL, until, FILETIME_EPOCH, app
from ads_simulator import Peer
from ads_native import configure_library
from ads_notifications import NotificationAcquisition, NotificationAttrib, UTCText, gap_report, plan_blocks
from acquisition_health import EventStore, RateWindow
from durable_recording import DurableRecorder
from primary_format import inspect, iter_events, iter_samples, recover
from primary_recording import PrimaryRecorder
from recording_manager import RecordingManager

GROUP = 0x4040


class MemoryPeer(Peer):
    """Symbols at real addresses in one index group; values change every cycle."""
    def __init__(self, channels):
        super().__init__()
        self.cycle = 0; self.unreadable = set(); self.layout = {}
        self.symbols = {}
        for i in range(channels):
            name = 'GVL.Channel%d' % i; self.symbols[name] = (5, 'LREAL', 8); self.layout[name] = (GROUP, 1000 + 8 * i, 0)
        # Bit-addressed symbol: never part of a block.
        self.symbols['GVL.Flag'] = (33, 'BOOL', 1); self.layout['GVL.Flag'] = (0x4021, 77, 0x2)
    def entry(self, name):
        dt, typ, size = self.symbols[name]; group, offset, flags = self.layout[name]
        text = [name.encode(), typ.encode(), b'Test symbol']; n, t, c = map(len, text)
        return struct.pack('<IIIIIIHHH', 33 + n + t + c, group, offset, size, dt, flags, n, t, c) + b'\0'.join(text) + b'\0'
    def value(self, name):
        if name == 'GVL.Flag': return bytes([self.cycle % 2])
        return struct.pack('<d', self.cycle * 1000.0 + int(name[11:]))
    def area(self, group, offset, size):
        data = bytearray(size)
        for name, (g, o, _) in self.layout.items():
            if g == group and offset <= o and o + self.symbols[name][2] <= offset + size:
                data[o - offset:o - offset + self.symbols[name][2]] = self.value(name)
        return bytes(data)
    def read(self, g, o, n):
        if g == GROUP:
            if GROUP in self.unreadable: return 1793, b''
            return 0, self.area(g, o, n)
        return super().read(g, o, n)


class BlockDLL(NotifyDLL):
    def __init__(self, channels):
        super().__init__(); self.peer = MemoryPeer(channels)
    def add(self, port, addr, group, offset, attr, callback, user, out):
        a = C.cast(attr, C.POINTER(NotificationAttrib)).contents
        self.attrs.append((group, offset, a.length))
        notification = user + 1000; self.subs[notification] = (callback, user, group, offset, a.length)
        C.memmove(out, struct.pack('<I', notification), 4); return 0
    def emit(self, ticks, cycle=None):
        if cycle is not None: self.peer.cycle = cycle
        for notification, (cb, user, group, offset, length) in list(self.subs.items()):
            data = self.peer.value(self.peer.handles[offset]) if group == 0xF005 else self.peer.area(group, offset, length)
            buffer = C.create_string_buffer(struct.pack('<IQI', notification, ticks, length) + data)
            cb(None, C.addressof(buffer), user)


class Harness(unittest.TestCase):
    def start(self, dll, nids, grouping=False, period=10):
        configure_library(dll); self.dll = dll
        self.mock = patch('ads_native.load_library', return_value=(dll, 'fake64.dll')); self.mock.start(); self.addCleanup(self.mock.stop)
        cfg = {**app.DEFAULT_CONFIG, 'source': 'ads', 'ads_transport': 'native', 'ads_mode': 'notify', 'ads_notify_grouping': grouping}
        self.rows = []; self.diag = {}
        w = NotificationAcquisition(cfg, lambda: (nids, period, (1, 1)), lambda t, rows, ms, g: self.rows.extend(rows), lambda d: self.diag.update(d))
        w.start(); self.addCleanup(lambda: (w.stop(), w.thread.join(5))); self.worker = w
        return w


class BlockNotifications(Harness):
    def test_plan_groups_contiguous_symbols_and_keeps_ineligible_ones_single(self):
        s = lambda g, o, size, flags=0: dict(group=g, offset=o, size=size, flags=flags)
        entries = [('a', s(GROUP, 0, 8)), ('b', s(GROUP, 8, 4)), ('far', s(GROUP, 70000, 8)), ('bit', s(0x4021, 5, 1, 0x2)),
                   ('ref', s(GROUP, 16, 8, 0x4)), ('handle', s(0xF005, 1, 8)), ('c', s(GROUP, 65528, 8)), ('other', s(0x4020, 0, 2))]
        blocks, singles = plan_blocks(entries)
        self.assertEqual([(g, start, length, [m[0] for m in members]) for g, start, length, members in blocks], [(GROUP, 0, 65536, ['a', 'b', 'c'])])
        self.assertEqual({n for n, _ in singles}, {'far', 'bit', 'ref', 'handle', 'other'})
        self.assertEqual([m[2] for m in blocks[0][3]], [0, 8, 65528])

    def test_one_callback_per_cycle_delivers_every_member_with_exact_values(self):
        channels = 50; nids = ['ads:GVL.Channel%d' % i for i in range(channels)] + ['ads:GVL.Flag']
        self.start(BlockDLL(channels), nids, grouping=True)
        until(lambda: len(self.dll.subs) == 2, 10)
        self.assertEqual(sorted(a[2] for a in self.dll.attrs), [1, 400])  # one 400-byte block, the bit symbol alone
        base = FILETIME_EPOCH + 1700000000 * 10000000
        for cycle in range(20): self.dll.emit(base + cycle * 100000, cycle)
        until(lambda: len(self.rows) == 20 * (channels + 1), 10)
        seen = Counter()
        for row in self.rows:
            nid = row['nodeid']; cycle = (int(row['source_filetime']) - base) // 100000
            expected = (cycle % 2 == 1) if nid == 'ads:GVL.Flag' else cycle * 1000.0 + int(nid[15:])
            self.assertEqual(row['value'], expected, row); seen[nid] += 1
            self.assertEqual(row['source_sequence'], cycle + 1)
        self.assertEqual(set(seen.values()), {20})
        until(lambda: self.diag.get('notify_layout', {}).get('blocks') == 1)
        self.assertEqual(self.diag['notify_layout'], dict(blocks=1, block_members=50, singles=1, fallback_members=0))
        self.assertEqual(self.worker.accepted, self.worker.processed); self.assertEqual(self.diag['dropped'], 0)
        self.assertEqual(self.diag['source_gap_samples'], 0)

    def test_unreadable_area_falls_back_to_handle_notifications(self):
        dll = BlockDLL(4); dll.peer.unreadable.add(GROUP)
        self.start(dll, ['ads:GVL.Channel%d' % i for i in range(4)], grouping=True)
        until(lambda: len(self.dll.subs) == 4, 10)
        until(lambda: self.diag.get('notify_layout', {}).get('fallback_members') == 4)
        self.assertTrue(all(a[0] == 0xF005 for a in self.dll.attrs))
        self.dll.emit(FILETIME_EPOCH + 1700000000 * 10000000, 3)
        until(lambda: len(self.rows) == 4)
        self.assertEqual(sorted(r['value'] for r in self.rows), [3000.0, 3001.0, 3002.0, 3003.0])

    def test_grouping_is_off_by_default(self):
        self.start(BlockDLL(3), ['ads:GVL.Channel%d' % i for i in range(3)])
        until(lambda: len(self.dll.subs) == 3, 10)
        self.assertTrue(all(a[0] == 0xF005 for a in self.dll.attrs))


class UpstreamGaps(Harness):
    def emit_cycles(self, base, step, cycles):
        for cycle in cycles: self.dll.emit(base + cycle * step, cycle)

    def test_missing_plc_cycle_is_counted_per_channel_and_reported_once(self):
        nids = ['ads:GVL.Channel0', 'ads:GVL.Channel1']; self.start(BlockDLL(2), nids)
        until(lambda: len(self.dll.subs) == 2, 10)
        base = FILETIME_EPOCH + 1700000000 * 10000000
        self.emit_cycles(base, 100000, [c for c in range(14) if c != 9])
        until(lambda: len(self.rows) == 26)
        until(lambda: self.diag.get('source_gap_samples') == 2)
        self.assertEqual(self.diag['source_gap_events'], 2)
        gap = self.diag['source_gaps'][0]
        self.assertEqual((gap['missing_samples'], gap['after_filetime'], gap['before_filetime']), (1, str(base + 8 * 100000), str(base + 10 * 100000)))
        missing, gaps = gap_report({}, self.diag); self.assertEqual((missing, len(gaps)), (2, 2))
        self.assertEqual(gap_report(self.diag, self.diag), (0, []))

    def test_task_rounded_period_is_learned_without_false_positives(self):
        # Requested 10 ms; the PLC task rounds the cycle to 12 ms.
        self.start(BlockDLL(1), ['ads:GVL.Channel0'])
        until(lambda: len(self.dll.subs) == 1, 10)
        base = FILETIME_EPOCH + 1700000000 * 10000000
        self.emit_cycles(base, 120000, range(30))
        until(lambda: len(self.rows) == 30); time.sleep(.3)
        self.assertEqual(self.worker.source_gap_samples, 0)
        self.emit_cycles(base, 120000, [33, 34])  # cycles 30..32 missing
        until(lambda: self.worker.source_gap_samples == 3)

    def test_application_report_persists_gap_event(self):
        previous = getattr(app.runtime, 'ads_notification_diag', {})
        try:
            app.runtime.ads_notification_diag = {}
            gaps = [dict(nodeid='ads:X', after_filetime='1', before_filetime='3', missing_samples=1, period_ms=10, plc_not_run=False)]
            app.ads_notification_report(dict(dropped=0, source_gap_samples=1, source_gap_events=1, source_gaps=gaps))
            event = [e for e in app.acquisition_events.recent if e['kind'] == 'ads_source_gap'][-1]
            self.assertEqual((event['details']['missing_samples'], event['details']['gaps']), (1, gaps))
        finally:
            app.runtime.ads_notification_diag = previous


class PrimaryAnnotations(unittest.TestCase):
    def rows(self, start, count):
        return [dict(nodeid='ads:A', value=float(i), quality='Good', received_ns=1700000000000000000 + i) for i in range(start, start + count)]

    def test_events_are_ordered_with_samples_and_survive_crash_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PrimaryRecorder(tmp, flush_ms=20); rec.start([dict(nodeid='ads:A')])
            for i in range(0, 30, 10): self.assertTrue(rec.enqueue(i, self.rows(i, 10), 10))
            self.assertTrue(rec.annotate('ads_source_gap', dict(nodeid='ads:A', missing_samples=2)))
            self.assertTrue(rec.enqueue(30, self.rows(30, 5), 10))
            self.assertTrue(rec.flush(10)); rec.process.kill(); rec.process.wait(); rec.done.wait(5)
            partial = rec.path
            self.assertTrue(partial.is_file())
            report = inspect(partial)
            self.assertEqual((report['samples'], report['events_count']), (35, 1))
            result = recover(partial); self.assertTrue(result['recovered'])
            self.assertNotIn('events', result)
            events = list(iter_events(result['output']))
            self.assertEqual([(e['kind'], e['after_sample'], e['details']['missing_samples']) for e in events], [('ads_source_gap', 30, 2)])
            self.assertEqual([r['value'] for r in iter_samples(result['output'])], [float(i) for i in range(35)])

    def test_annotation_never_blocks_or_fails_the_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = PrimaryRecorder(tmp, flush_ms=20, max_batches=2); rec.start([dict(nodeid='ads:A')])
            with rec.lock:  # sender cannot drain while the producer lock is held
                results = [rec.annotate('noise', {}) for _ in range(rec.q.maxsize + 3)]
            self.assertIn(False, results); self.assertGreater(rec.annotations_lost, 0); self.assertFalse(rec.error)
            state = rec.stop(10); self.assertEqual(state['integrity'], 'complete')
            self.assertEqual((state['annotations'], state['annotations_lost']), (rec.annotations, rec.annotations_lost))

    def test_event_store_tees_into_the_acquiring_session_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / 'events.sqlite'); previous = DurableRecorder.events; DurableRecorder.events = store
            try:
                manager = RecordingManager(Path(tmp) / 'rec', 'test', factory=DurableRecorder); store.listener = manager.annotate
                manager.start('csv', [dict(nodeid='ads:A', display_name='A')], sample_ms=10)
                rows = [dict(nodeid='ads:A', value=1.5, plot_value=1.5, good=True, quality='Good', data_type='LREAL', status='ADS_OK')]
                self.assertTrue(manager.enqueue(1.0, rows, 10))
                store.emit('source_loss', 'conn-1', protocol='ads', lost_samples=4)
                store.emit('source_connected', 'conn-2', at_ns='0')  # details may repeat a reserved key
                self.assertTrue(manager.enqueue(2.0, [dict(r) for r in rows], 10))
                manager.stop('user', 0); until(lambda: not manager.busy, 20)
                path = manager.status()['primary_original']
                kinds = [(e['kind'], e['details'].get('connection_id'), e['details'].get('lost_samples')) for e in iter_events(path)]
                self.assertIn(('source_loss', 'conn-1', 4), kinds)
                self.assertIn('source_connected', [k for k, _, _ in kinds])
                self.assertEqual(sum(1 for _ in iter_samples(path)), 2)
                store.emit('after_stop', None)  # no active session: nothing to annotate, no error
                self.assertEqual(store.listener_error, '')
            finally:
                DurableRecorder.events = previous; store.close()


class Rates(unittest.TestCase):
    def test_sliding_window_reports_recent_rate_not_lifetime_average(self):
        clock = [100.0]
        with patch('acquisition_health.time.monotonic', side_effect=lambda: clock[0]):
            window = RateWindow(seconds=10, interval=.25)
            for second in range(60):  # one minute at 1,000/s
                clock[0] = 100 + second; window.observe(received=second * 1000)
            for second in range(60, 70):  # then a stall
                clock[0] = 100 + second; window.observe(received=59000)
            snapshot = window.snapshot()
        self.assertAlmostEqual(snapshot['window_s'], 10.0)
        self.assertEqual(snapshot['received_per_second'], 0.0)

    def test_utc_text_is_exact_integer_rounding(self):
        text = UTCText(); epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        for ns in (0, 499, 500, 1700000000123456499, 1700000000123456500, 1700000000999999500, 1767225599999999999):
            expected = (epoch + timedelta(microseconds=(ns + 500) // 1000)).isoformat(timespec='microseconds')
            self.assertEqual(text.micro(ns), expected)


if __name__ == '__main__':
    unittest.main()
