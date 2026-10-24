"""ADS parity: C# engine against the Python reference client on the ADS/TCP test peer.

Polling (SumRead, individual Read, PLC not in RUN, online change, malformed replies)
and cyclic notifications. Notifications run over ADS/TCP against the simulator here;
in production the same logic runs through the local TwinCAT router (TcAdsDll).
"""
import asyncio
import json
import ctypes as C
import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ads_simulator import Peer
from engine_bridge import Engine, EngineError
from mqtt_peer import until

BASE_CFG = {'source': 'ads', 'ads_host': '127.0.0.1', 'ads_target_netid': '127.0.0.1.1.1', 'ads_port': 851, 'ads_source_netid': '',
            'ads_source_port': 32905, 'ads_bind_ip': '', 'ads_timeout_ms': 1000, 'ads_mode': 'sum', 'ads_transport': 'tcp',
            'ads_encoding': 'cp1252', 'ads_root': 'ads:/', 'ads_dll_path': '', 'sample_ms': 20, 'auto_reconnect': True}
VOLATILE = {'received_ns', 'received_ms', 'local_ts', 'connection_id', 'sample_sequence', 'source_epoch', 'source_protocol',
            'acquisition_timestamp_ms', 'clock_discontinuity', 'protocol', 'timestamp_origin', 'precision_warning'}
SYMBOLS = ['ads:MAIN.Wave', 'ads:MAIN.Running', 'ads:MAIN.Counter', 'ads:MAIN.Text', 'ads:MAIN.Wide', 'ads:MAIN.Array',
           'ads:MAIN.Array[1]', 'ads:MAIN.Struct', 'ads:MAIN.Struct.Value', 'ads:MAIN.Bad', 'ads:MAIN.Nonfinite', 'ads:MAIN.Missing']


def comparable(row, wave=True):
    result = {k: v for k, v in row.items() if not k.startswith('_') and k not in VOLATILE}
    if not wave and row['nodeid'] == 'ads:MAIN.Wave':
        result.pop('value', None); result.pop('plot_value', None)
    return result


class PeerThread:
    """The asyncio ADS peer on its own loop, so blocking engine calls never stall it."""
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True); self.thread.start()
        self.peer = self.run(Peer().start())
    def run(self, coro): return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)
    def close(self):
        self.run(self.peer.close()); self.loop.call_soon_threadsafe(self.loop.stop); self.thread.join(5)


class EngineADS(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.rows = []; cls.events = []; cls.lock = threading.Lock()
        def on_rows(message):
            with cls.lock: cls.rows.append(message)
        cls.engine = Engine(on_event=cls.events.append, on_rows=on_rows)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        with self.lock: self.rows.clear()
        self.events.clear()
        self.server = PeerThread(); self.peer = self.server.peer
        self.cfg = {**BASE_CFG, 'ads_tcp_port': self.peer.port}

    def tearDown(self):
        self.engine.call('acq.disconnect')
        self.server.close()

    def python_read(self, ids, **cfg):
        from ads_client import ADSClient
        async def read():
            c = ADSClient({**self.cfg, **cfg}); await c.connect()
            try: return await c.read_values(ids), dict(c.info)
            finally: await c.disconnect()
        return self.server.run(read())

    def engine_first(self, ids, **cfg):
        with self.lock: self.rows.clear()
        self.engine.call('acq.select', ids=ids, period=20, generation=[1, 1])
        state = self.engine.call('acq.connect', cfg={**self.cfg, **cfg}, protocols=['ads'], wait_first=True)
        until(lambda: self.rows, 10)
        rows = self.rows[0]['rows']
        info = self.engine.call('acq.status')['sources']['ads']['info']
        self.engine.call('acq.disconnect')
        return rows, info, state

    def test_polling_parity_sum_individual_and_not_run(self):
        for cfg in ({}, {'ads_mode': 'individual'}, {'ads_encoding': 'utf-8'}):
            with self.subTest(cfg=cfg):
                python, pinfo = self.python_read(SYMBOLS, **cfg)
                engine, einfo, _ = self.engine_first(SYMBOLS, **cfg)
                self.assertEqual([comparable(r, wave=False) for r in python], [comparable(r, wave=False) for r in engine])
                self.assertEqual(pinfo['read_mode'], einfo['read_mode'])
                self.assertEqual(pinfo['device'], einfo['device']); self.assertEqual(pinfo['version'], einfo['version'])
        self.peer.ads_state = 6; self.peer.sum_enabled = False
        python, pinfo = self.python_read(SYMBOLS[:3])
        engine, einfo, _ = self.engine_first(SYMBOLS[:3])
        self.assertEqual([comparable(r, wave=False) for r in python], [comparable(r, wave=False) for r in engine])
        self.assertEqual(engine[0]['quality'], 'Uncertain'); self.assertEqual(einfo['read_mode'], 'ADS Read individuale')
        self.assertFalse(self.peer.handles, 'handles released on disconnect')

    def test_catalog_browse_and_inspect_parity(self):
        from ads_client import ADSClient
        async def python():
            c = ADSClient(self.cfg, catalog=True); await c.connect()
            try:
                return [await c.inspect(n) for n in ('ads:/', 'ads:MAIN', 'ads:MAIN.Struct.Value', 'ads:MAIN.Array')], \
                       [await c.browse_page(n) for n in ('ads:/', 'ads:MAIN', 'ads:MAIN.Array')]
            finally: await c.disconnect()
        inspected, pages = self.server.run(python())
        self.assertEqual(inspected, [self.engine.call('catalog.inspect', protocol='ads', nodeid=n, separate=True, cfg=self.cfg)
                                     for n in ('ads:/', 'ads:MAIN', 'ads:MAIN.Struct.Value', 'ads:MAIN.Array')])
        self.assertEqual([p['nodes'] for p in pages], [self.engine.call('catalog.browse', protocol='ads', nodeid=n, separate=True, cfg=self.cfg)
                                                       for n in ('ads:/', 'ads:MAIN', 'ads:MAIN.Array')])

    def test_online_change_invalid_handle_and_malformed_reply(self):
        self.engine.call('acq.select', ids=['ads:MAIN.Wave'], period=20, generation=[1, 1])
        self.engine.call('acq.connect', cfg=self.cfg, protocols=['ads'], wait_first=True)
        until(lambda: len(self.rows) > 3, 10)
        self.peer.handles.clear()
        until(lambda: any(r['rows'][0]['status_code'] == 1809 for r in list(self.rows)), 10)
        count = len(self.rows)
        until(lambda: len(self.rows) > count + 3 and self.rows[-1]['rows'][0]['quality'] == 'Good', 10)
        self.peer.version += 1
        until(lambda: self.engine.call('acq.status')['sources']['ads']['info'].get('symbol_changes') == 1, 10)
        self.peer.fault = 'invoke'
        until(lambda: any(e['kind'] == 'source_disconnected' for e in self.events), 10)
        until(lambda: self.engine.call('acq.status')['sources']['ads']['state'] == 'connected', 10)
        self.peer.delay = 1.5
        until(lambda: 'Timeout ADS' in self.engine.call('acq.status')['sources']['ads']['error'], 10)
        self.peer.delay = 0

    def test_connection_errors(self):
        with self.assertRaisesRegex(EngineError, 'Router AMS|Porta|Target|ADS'):
            self.peer.fault = None
            bad = {**self.cfg, 'ads_tcp_port': 1}
            self.engine.call('acq.connect', cfg=bad, protocols=['ads'], wait_first=True)

    def test_notifications_parity_with_python_worker(self):
        """Same FILETIME sequence through the Python worker (fake TcAdsDll) and the engine (ADS/TCP)."""
        from test_ads_notifications import NotifyDLL
        from ads_native import configure_library
        from ads_notifications import NotificationAcquisition, FILETIME_EPOCH
        ids = ['ads:MAIN.Running', 'ads:MAIN.Counter', 'ads:MAIN.Array[1]']
        base = FILETIME_EPOCH + 1700000000 * 10000000 + 1234567
        ticks = [base + i * 100000 for i in range(12)] + [base + 15 * 100000, base + 16 * 100000]
        dll = NotifyDLL(); configure_library(dll)
        samples = []; diagnostics = {}
        with patch('ads_native.load_library', return_value=(dll, 'fake64.dll')):
            w = NotificationAcquisition({**self.cfg, 'ads_transport': 'native', 'ads_mode': 'notify'}, lambda: (ids, 10, (1, 1)),
                                        lambda ts, rows, ms, gen: samples.append((ts, rows, ms)), diagnostics.update)
            w.start()
            try:
                until(lambda: len(dll.subs) == 3)
                for t in ticks: dll.emit(t)
                until(lambda: w.processed == 3 * len(ticks))
                time.sleep(.3)
            finally:
                w.stop(); w.thread.join(5)
        self.engine.call('acq.select', ids=ids, period=10, generation=[1, 1])
        self.engine.call('acq.connect', cfg={**self.cfg, 'ads_mode': 'notify'}, protocols=['ads'], wait_first=True)
        until(lambda: len(self.peer.subscriptions) == 3, 10)
        for t in ticks: self.peer.emit(t)
        try:
            until(lambda: self.engine.call('acq.status')['sources']['ads']['diagnostics'].get('processed') == 3 * len(ticks), 10)
        except AssertionError:
            status = self.engine.call('acq.status')['sources']['ads']
            self.fail('notifications not processed: ' + json.dumps({k: status['diagnostics'].get(k) for k in ('received', 'accepted', 'processed', 'dropped', 'subscription_restarts', 'queue_samples')}) + ' state=' + status['state'] + ' error=' + status['error'] + ' events=' + json.dumps([e for e in self.events if e['kind'] == 'source_disconnected']))
        time.sleep(.5)
        diag = self.engine.call('acq.status')['sources']['ads']['diagnostics']
        with self.lock: engine = [(m['t'], m['rows']) for m in self.rows]
        def flat(batches): return sorted([(t, comparable(r)) for t, rows in batches for r in rows], key=lambda x: (x[0], x[1]['nodeid']))
        def keep(row): return {k: v for k, v in row.items() if k not in ('source_epoch', 'source_sequence')}
        a = [(t, keep(r)) for t, r in flat([(t, rows) for t, rows, _ in samples])]
        b = [(t, keep(r)) for t, r in flat(engine)]
        self.assertEqual(a, b)
        self.assertEqual(engine[0][1][0]['source_filetime'], str(base))
        for key in ('received', 'accepted', 'dropped', 'source_gap_samples', 'source_gap_events', 'timestamp_errors', 'clock_backwards'):
            self.assertEqual(diagnostics[key], diag[key], key)
        self.assertEqual(diag['source_gap_samples'], 9)  # ticks 12..14 missing on 3 signals
        self.assertAlmostEqual(diag['source_period_ms'], diagnostics['source_period_ms'])
        self.assertTrue(any(e['kind'] == 'ads_source_gap' for e in self.events))
        self.assertTrue(all(a[1:] == (3, 100000, 100000) for a in self.peer.attrs))
        # PLC in STOP: samples are Uncertain and the watchdog does not abort.
        self.peer.ads_state = 6
        until(lambda: self.engine.call('acq.status')['sources']['ads']['info'].get('ads_state') == 6, 10)
        with self.lock: self.rows.clear()
        self.peer.emit(base + 20 * 100000)
        until(lambda: self.rows, 5)
        self.assertEqual(self.rows[-1]['rows'][0]['quality'], 'Uncertain')
        self.engine.call('acq.disconnect')
        self.assertFalse(self.peer.subscriptions)

    def test_notification_watchdog_and_size_fault(self):
        self.engine.call('acq.select', ids=['ads:MAIN.Running'], period=10, generation=[1, 1])
        self.engine.call('acq.connect', cfg={**self.cfg, 'ads_mode': 'notify'}, protocols=['ads'], wait_first=True)
        until(lambda: self.peer.subscriptions, 10)
        self.peer.emit(116444736000000000 + 1700000000 * 10000000, size_override=9)
        until(lambda: 'Dimensione notifica ADS incoerente' in self.engine.call('acq.status')['sources']['ads']['error'], 10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
