"""OPC UA parity: C# engine (OPC Foundation stack) against asyncua on a local server.

Rows (values, types, statuses, timestamps, typed/binary originals), browse and inspect
match the 2.x asyncua implementation; security Basic256Sha256 with a pinned server
certificate and a user identity; notifications kept for the primary capture only.
"""
import asyncio
import datetime as dt
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

TMP = tempfile.TemporaryDirectory(prefix='engine-opcua-')
os.environ['OPCUA_DATA_DIR'] = TMP.name + '/data'; os.environ['OPCUA_APP_DIR'] = TMP.name + '/app'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import app
from asyncua import Client, Server, ua
from engine_bridge import Engine, EngineError
from mqtt_peer import until
from primary_format import iter_samples, typed
from test_integration import make_server

VOLATILE = {'received_ns', 'received_ms', 'local_ts', 'connection_id', 'sample_sequence', 'source_epoch', 'source_protocol',
            'acquisition_timestamp_ms', 'clock_discontinuity', 'acquisition_origin', 'server_ts', 'server_picoseconds', 'source_picoseconds', 'error', 'precision_warning'}


def comparable(row):
    return {k: v for k, v in row.items() if not k.startswith('_') and k not in VOLATILE}


class EngineOPCUA(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.thread = threading.Thread(target=cls.loop.run_forever, daemon=True); cls.thread.start()
        cls.server, cls.endpoint, cls.root, nodes = cls.go(make_server())
        cls.ids = [n.nodeid.to_string() for n in nodes]
        cls.extra = cls.go(cls.add_types())
        cls.rows = []; cls.events = []; cls.lock = threading.Lock()
        def on_rows(message):
            with cls.lock: cls.rows.append(message)
        cls.engine = Engine(on_event=cls.events.append, on_rows=on_rows)
        app.ensure_client_certificate('urn:space-analyzer:engine-test')

    @classmethod
    def go(cls, coro, timeout=40):
        return asyncio.run_coroutine_threadsafe(coro, cls.loop).result(timeout)

    @classmethod
    async def add_types(cls):
        ns = await cls.server.register_namespace('urn:engine-test:types')
        folder = await cls.server.nodes.objects.add_folder(ua.NodeId('Types', ns), 'Tipi')
        values = [('Big', ua.Variant(2 ** 63 - 5, ua.VariantType.Int64)), ('UBig', ua.Variant(2 ** 64 - 1, ua.VariantType.UInt64)),
                  ('Float', ua.Variant(1.1, ua.VariantType.Float)), ('Int16', ua.Variant(-7, ua.VariantType.Int16)),
                  ('Date', ua.Variant(dt.datetime(2026, 10, 21, 12, 30, 45, 123456, tzinfo=dt.timezone.utc), ua.VariantType.DateTime)),
                  ('Bytes', ua.Variant(b'\x00\x01\'a', ua.VariantType.ByteString)),
                  ('Guid', ua.Variant(uuid.UUID('12345678-1234-1234-1234-123456789abc'), ua.VariantType.Guid)),
                  ('Text', ua.Variant(ua.LocalizedText('ciao', 'it'), ua.VariantType.LocalizedText)),
                  ('Ints', ua.Variant([1, -2, 3], ua.VariantType.Int32)), ('Empty', ua.Variant(None, ua.VariantType.Null))]
        nodes = []
        for name, variant in values:
            nodes.append(await folder.add_variable(ua.NodeId(name, ns), name, variant))
        return [n.nodeid.to_string() for n in nodes] + [f'ns={ns};s=Missing']

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()
        cls.go(cls.server.stop())
        cls.loop.call_soon_threadsafe(cls.loop.stop)

    def setUp(self):
        with self.lock: self.rows.clear()
        self.events.clear()

    def tearDown(self):
        self.engine.call('acq.disconnect')

    def cfg(self, **extra):
        return {'source': 'opcua', 'endpoint': self.endpoint, 'security': 'None', 'security_mode': 'None', 'username': '', 'password': '',
                'application_uri': 'urn:space-analyzer:engine-test', 'sample_ms': 50, 'auto_reconnect': True,
                '_client_cert': str(app.CERT_FILE), '_client_key': str(app.KEY_FILE), '_server_cert': str(app.SERVER_CERT_FILE), **extra}

    def python_rows(self, ids):
        async def read():
            c = Client(url=self.endpoint, timeout=8); await c.connect()
            try:
                result = []
                for nid in ids:
                    dv = await c.get_node(nid).read_data_value(raise_on_bad_status=False)
                    result.append(app.value_row(nid, dv))
                return result
            finally: await c.disconnect()
        return self.go(read())

    def test_values_types_and_originals(self):
        ids = self.ids + self.extra
        python = self.python_rows(ids)
        with tempfile.TemporaryDirectory(prefix='engine-opcua-rec-') as temp:
            self.engine.call('acq.select', ids=ids, period=50, generation=[1, 1])
            self.engine.call('acq.connect', cfg=self.cfg(), protocols=['opcua'], wait_first=True)
            state = self.engine.call('record.start', arm=True, directory=temp, version='3.0.0', format='csv',
                                     nodes=[{'nodeid': n, 'display_name': n} for n in ids], endpoint=self.endpoint, sample_ms=50, options={})
            until(lambda: len(self.rows) >= 3, 15)
            self.engine.call('acq.fence', timeout=5)
            self.engine.call('record.stop', session=state['engine_session'], reason='user', timeout=30)
            for _ in range(400):
                status = self.engine.call('record.status', session=state['engine_session'])
                if not status['busy']: break
                time.sleep(.05)
            engine = self.rows[-1]['rows']
            self.assertEqual([comparable(r) for r in python], [comparable(r) for r in engine])
            self.assertTrue(all(r['acquisition_origin'] == 'subscription_snapshot' for r in engine if r['nodeid'] in self.ids[:4]))
            # Originals: typed() for plain values, the OPC UA binary Variant otherwise (as asyncua).
            first = {}
            for sample in iter_samples(status['primary']['path']):
                # Static values notify only before Record: the periodic snapshots carry the same originals.
                if sample['acquisition_origin'] == 'subscription_snapshot': first.setdefault(sample['nodeid'], sample)
            checked = 0
            for row in python:
                sample = first.get(row['nodeid'])
                if sample is None: continue
                checked += 1
                if '_source_typed' in row:
                    self.assertEqual(json.dumps(typed(sample['source_value'])), json.dumps(row['_source_typed']), row['nodeid'])
                else:
                    self.assertEqual(typed(sample['source_binary']), row['_source_binary_typed'], row['nodeid'])
            self.assertGreaterEqual(checked, len(ids) - 2)
            # The CSV (selected format) holds snapshots only, as in 2.x single-source acquisition.
            csv_path = Path(status['path'])
            self.assertTrue(csv_path.exists())

    def test_browse_inspect_and_errors(self):
        async def python():
            c = Client(url=self.endpoint, timeout=8); await c.connect()
            app.runtime.client = c; app.runtime.connected = True
            try:
                pages = []
                for nid in ('i=85', self.root.nodeid.to_string()):
                    items, seen = [], set()
                    async for refs in app.reference_pages(nid):
                        for ref in refs:
                            row = app.reference_item(ref)
                            if row['nodeid'] not in seen: seen.add(row['nodeid']); items.append(row)
                    pages.append(items)
                inspected = [await app.inspect_node(n) for n in ('i=85', self.ids[0])]
                return pages, inspected
            finally:
                app.runtime.client = None; app.runtime.connected = False
                await c.disconnect()
        pages, inspected = self.go(python())
        self.engine.call('acq.select', ids=[], period=50, generation=[1, 1])
        self.engine.call('acq.connect', cfg=self.cfg(), protocols=['opcua'], wait_first=True)
        self.assertEqual(pages, [self.engine.call('catalog.browse', protocol='opcua', nodeid=n) for n in ('i=85', self.root.nodeid.to_string())])
        self.assertEqual(inspected, [self.engine.call('catalog.inspect', protocol='opcua', nodeid=n) for n in ('i=85', self.ids[0])])
        with self.assertRaises(EngineError):
            self.engine.call('acq.connect', cfg=self.cfg(endpoint='opc.tcp://127.0.0.1:1'), protocols=['opcua'], wait_first=True)

    def test_secure_sign_and_encrypt_with_user(self):
        async def fetch():
            return await app.fetch_server_certificate_async(self.endpoint, 'Basic256Sha256', 'SignAndEncrypt')
        self.go(fetch())
        for mode in ('SignAndEncrypt', 'Sign'):
            with self.subTest(mode=mode):
                with self.lock: self.rows.clear()
                self.engine.call('acq.select', ids=[self.ids[0]], period=50, generation=[1, 1])
                self.engine.call('acq.connect', cfg=self.cfg(security='Basic256Sha256', security_mode=mode, username='test_operator', password='test_password'),
                                 protocols=['opcua'], wait_first=True)
                until(lambda: self.rows, 10)
                self.assertEqual(self.rows[-1]['rows'][0]['value'], 21.5)
                self.engine.call('acq.disconnect')
        with self.assertRaises(EngineError):
            self.engine.call('acq.connect', cfg=self.cfg(security='Basic256Sha256', security_mode='Sign', username='test_operator', password='wrong'),
                             protocols=['opcua'], wait_first=True)

    def test_value_change_notifications_primary_only(self):
        node_id = self.ids[0]
        self.engine.call('acq.select', ids=[node_id], period=20, generation=[1, 1])
        self.engine.call('acq.connect', cfg=self.cfg(sample_ms=20), protocols=['opcua'], wait_first=True)
        until(lambda: self.rows, 10)
        async def write(v):
            await self.server.get_node(node_id).write_value(v)
        with tempfile.TemporaryDirectory(prefix='engine-opcua-notify-') as temp:
            state = self.engine.call('record.start', arm=True, directory=temp, version='3.0.0', format='csv',
                                     nodes=[{'nodeid': node_id, 'display_name': 'T'}], endpoint=self.endpoint, sample_ms=20, options={})
            for v in (22.5, 23.5, 21.5):
                self.go(write(v)); time.sleep(.15)
            self.engine.call('acq.fence', timeout=5)
            self.engine.call('record.stop', session=state['engine_session'], reason='user', timeout=30)
            for _ in range(400):
                status = self.engine.call('record.status', session=state['engine_session'])
                if not status['busy']: break
                time.sleep(.05)
            samples = list(iter_samples(status['primary']['path']))
            notified = [s['value'] for s in samples if s['acquisition_origin'] == 'notification']
            snapshots = [s for s in samples if s['acquisition_origin'] == 'subscription_snapshot']
            self.assertEqual(notified, [22.5, 23.5, 21.5])
            self.assertEqual(status['rows_written'], len(snapshots))
        values = [r['value'] for m in list(self.rows) for r in m['rows']]
        self.assertIn(22.5, values); self.assertIn(23.5, values)
        self.assertEqual(self.engine.call('acq.status')['sources']['opcua']['mode'], 'OPC UA Subscription')

if __name__ == '__main__':
    unittest.main(verbosity=2)
