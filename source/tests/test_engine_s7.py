"""S7 parity: C# engine drivers against the Python reference clients.

Classic S7comm against the native Snap7 server (test dependency python-snap7);
S7 UDP Direct with the same datagrams sent to both receivers.
"""
import asyncio
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_bridge import Engine, EngineError
from mqtt_peer import until
from s7_protocols import DEFAULTS, create_s7_client, validate_config
from test_s7 import start_server
from test_s7_extended import free_udp_port, packet

VOLATILE = {'received_ns', 'received_ms', 'local_ts', 'connection_id', 'sample_sequence', 'source_epoch', 'source_protocol',
            'acquisition_timestamp_ms', 'clock_discontinuity', 'protocol', 'timestamp_origin', 'precision_warning'}
CLASSIC = ['s7:DB1.DBD0:REAL', 's7:DB1.DBW4:INT', 's7:DB1.DBX6.7:BOOL', 's7:DB1.DBD8:LREAL', 's7:DB1.DBD16:LINT',
           's7:DB99.DBD0:REAL', 's7:DB1.DBB32:STRING[254]', 's7:MW4:INT', 's7:I6.7:BOOL', 's7:QD0:REAL', 's7:DB1.DBD8:UDINT',
           's7:DB1.DBB6:BYTE', 's7:DB1.DBB6:SINT', 's7:DB1.DBD16:ULINT', 's7:DB1.DBD0:TIME']


def comparable(row):
    return {k: v for k, v in row.items() if not k.startswith('_') and k not in VOLATILE}


class EngineS7(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.rows = []
        cls.events = []
        cls.lock = threading.Lock()
        def on_rows(message):
            with cls.lock: cls.rows.append(message)
        cls.engine = Engine(on_event=cls.events.append, on_rows=on_rows)

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        with self.lock: self.rows.clear()

    def tearDown(self):
        self.engine.call('acq.disconnect')

    def test_classic_readvar_parity_and_catalog(self):
        server, port = start_server()
        try:
            for pdu in (240, 480):
                with self.subTest(pdu=pdu):
                    with self.lock: self.rows.clear()
                    cfg = validate_config({**DEFAULTS, 's7_host': '127.0.0.1', 's7_tcp_port': port, 's7_pdu': pdu, 'sample_ms': 50,
                                           's7_tags': [{'address': 'DB1.DBD0', 'data_type': 'REAL', 'name': 'Carico A'},
                                                       {'address': 'MW4', 'data_type': 'INT', 'name': 'Velocità'}]})
                    client = create_s7_client(dict(cfg))
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(client.connect())
                        python = loop.run_until_complete(client.read_values(CLASSIC))
                        catalog = [loop.run_until_complete(client.inspect(n)) for n in ('s7:/', 's7:/DB1', 's7:DB1.DBD0:REAL')]
                        browse = loop.run_until_complete(client.browse('s7:/'))
                        loop.run_until_complete(client.disconnect())
                    finally:
                        loop.close()
                    self.engine.call('acq.select', ids=CLASSIC, period=50, generation=[3, 1])
                    state = self.engine.call('acq.connect', cfg=cfg, protocols=['s7'], wait_first=True)
                    self.assertEqual(state['sources']['s7']['info']['pdu'], client.info['pdu'])
                    until(lambda: self.rows, 10)
                    engine = self.rows[0]['rows']
                    self.assertEqual([comparable(r) for r in python], [comparable(r) for r in engine])
                    self.assertEqual(catalog, [self.engine.call('catalog.inspect', protocol='s7', nodeid=n) for n in ('s7:/', 's7:/DB1', 's7:DB1.DBD0:REAL')])
                    self.assertEqual(browse, self.engine.call('catalog.browse', protocol='s7', nodeid='s7:/'))
                    self.engine.call('acq.disconnect')
            # Polling continues at the requested period: several cycles in half a second.
            with self.lock: self.rows.clear()
            self.engine.call('acq.select', ids=CLASSIC[:3], period=20, generation=[3, 2])
            self.engine.call('acq.connect', cfg=cfg, protocols=['s7'], wait_first=True)
            time.sleep(.5)
            self.assertGreater(len(self.rows), 10)
            self.assertEqual(self.rows[-1]['generation'], [3, 2])
        finally:
            self.engine.call('acq.disconnect')
            server.stop()

    def test_classic_connection_errors_and_loss(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        cfg = validate_config({**DEFAULTS, 's7_host': '127.0.0.1', 's7_tcp_port': port, 's7_timeout_ms': 500})
        with self.assertRaises(EngineError):
            self.engine.call('acq.connect', cfg=cfg, protocols=['s7'], wait_first=True)
        server, port = start_server()
        try:
            cfg = validate_config({**DEFAULTS, 's7_host': '127.0.0.1', 's7_tcp_port': port, 'sample_ms': 20})
            self.engine.call('acq.select', ids=['s7:MD0:REAL'], period=20, generation=[3, 1])
            self.engine.call('acq.connect', cfg=cfg, protocols=['s7'], wait_first=True)
            until(lambda: self.rows, 5)
            server.stop()
            until(lambda: any(r['rows'][0]['status'] == 'ConnectionLost' for r in list(self.rows)), 10)
        finally:
            self.engine.call('acq.disconnect')

    def udp_cfg(self, **extra):
        return validate_config({**DEFAULTS, 's7_mode': 'udp', 's7_host': '127.0.0.1', 's7_udp_bind': '127.0.0.1',
                                's7_udp_port': free_udp_port(), 's7_udp_length': 24, 's7_udp_timestamp_offset': 0, 'sample_ms': 500,
                                's7_udp_tags': [dict(offset=8, data_type='REAL', display_name='CaricoA'), dict(offset=12, data_type='REAL', display_name='CaricoB'),
                                                dict(offset=16, bit=0, data_type='BOOL', display_name='Enabled')], **extra})

    def udp_parity(self, datagrams, wait_ms=0, **extra):
        """Send the same datagrams to the Python receiver and to the engine; return both row lists and diagnostics."""
        cfg = self.udp_cfg(**extra)
        ids = [t['nodeid'] for t in cfg['s7_udp_tags']]
        python_rows = []
        client = create_s7_client(dict(cfg))
        loop = asyncio.new_event_loop()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            loop.run_until_complete(client.connect())
            client.configure_acquisition(lambda: (ids, 500, (1, 1)), lambda ts, rows, ms, g: python_rows.append((ts, rows, ms)))
            for d in datagrams:
                if d is None: time.sleep(wait_ms / 1000); continue
                sender.sendto(d, ('127.0.0.1', cfg['s7_udp_port'])); time.sleep(.003)
            time.sleep(.3)
            python_diag = client.diagnostics()
            loop.run_until_complete(client.disconnect())
        finally:
            loop.close()
        with self.lock: self.rows.clear()
        self.engine.call('acq.select', ids=ids, period=500, generation=[1, 1])
        self.engine.call('acq.connect', cfg=cfg, protocols=['s7'], wait_first=True)
        try:
            for d in datagrams:
                if d is None: time.sleep(wait_ms / 1000); continue
                sender.sendto(d, ('127.0.0.1', cfg['s7_udp_port'])); time.sleep(.003)
            time.sleep(.3)
            engine_diag = self.engine.call('acq.status')['sources']['s7']['diagnostics']
        finally:
            self.engine.call('acq.disconnect')
            sender.close()
        with self.lock: engine_rows = [(m['t'], m['rows'], m['period']) for m in self.rows]
        return python_rows, engine_rows, python_diag, engine_diag

    def assert_udp_equal(self, python_rows, engine_rows, python_diag, engine_diag, timed=True):
        self.assertTrue(python_rows)
        a = [[comparable(r) for r in rows] for _, rows, _ in python_rows]
        b = [[comparable(r) for r in rows] for _, rows, _ in engine_rows]
        self.assertEqual(a, b)
        if timed: self.assertEqual([t for t, _, _ in python_rows], [t for t, _, _ in engine_rows])
        self.assertEqual([p for _, _, p in python_rows], [p for _, _, p in engine_rows])
        for key in ('received', 'accepted', 'lost', 'duplicates', 'out_of_order', 'invalid', 'filtered', 'queue_dropped',
                    'sequence_resets', 'timeouts', 'timestamp_errors'):
            self.assertEqual(python_diag[key], engine_diag[key], key)

    def test_udp_sequences_gaps_duplicates_invalid(self):
        datagrams = [packet(i, 1700000000000001 + i * 10000, a=i + .5, b=-i) for i in range(40)]
        datagrams += [packet(65535, 1700000001000001), packet(0, 1700000001010001), packet(0, 1700000001010001),
                      packet(65535, 1700000001020001), packet(3, 1700000001040001), b'bad', packet(4, module=201),
                      packet(5, 1700000001030001), packet(6, -1), packet(7, 1700000001050001, a=float('nan')),
                      packet(8, 1700000001060001, a=float('inf'))]
        result = self.udp_parity(datagrams)
        self.assert_udp_equal(*result)
        self.assertGreaterEqual(len(result[0]), 40)
        self.assertGreater(result[2]['out_of_order'] + result[2]['invalid'] + result[2]['filtered'], 2)

    def test_udp_watchdog_and_little_endian(self):
        datagrams = [packet(1, 1700000000000001), None, packet(2, 1700000000300001), packet(3, 1700000000310001)]
        python_rows, engine_rows, pd, ed = self.udp_parity(datagrams, wait_ms=300, s7_udp_watchdog_ms=100)
        # Timeout rows carry local arrival times; compare their content, not their instants.
        self.assert_udp_equal(python_rows, engine_rows, pd, ed, timed=False)
        self.assertTrue(any(r[1][0]['status'].startswith('UDP timeout') for r in engine_rows))
        little = [struct.pack('>HHH', 16, 200, i) + struct.pack('<d', 12.125 + i) + b'\0\0' for i in range(5)]
        self.assert_udp_equal(*self.udp_parity(little, s7_udp_length=16, s7_udp_timestamp_offset=-1, s7_udp_endian='little',
                                               s7_udp_tags=[dict(offset=0, data_type='LREAL', display_name='X')]), timed=False)

    def test_udp_catalog_and_exclusive_port(self):
        cfg = self.udp_cfg()
        self.engine.call('acq.select', ids=[], period=500, generation=[1, 1])
        self.engine.call('acq.connect', cfg=cfg, protocols=['s7'], wait_first=True)
        items = self.engine.call('catalog.scan', protocol='s7', nodeid='s7:udp:/')
        self.assertEqual([i['display_name'] for i in items], ['CaricoA', 'CaricoB', 'Enabled'])
        client = create_s7_client(dict(cfg))
        loop = asyncio.new_event_loop()
        try:
            self.assertEqual(loop.run_until_complete(client.scan_items('s7:udp:/')), items)
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
