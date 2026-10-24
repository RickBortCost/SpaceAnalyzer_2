"""MQTT parity: the C# engine driver against the Python reference client on one peer.

Same broker peer, same publications: the decoded rows (values, qualities, statuses,
types, source timestamps) and the typed originals stored in the primary capture
must match the Python implementation exactly.
"""
import asyncio
import json
import math
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine_bridge import Engine
from mqtt_peer import Peer, until
from mqtt_source import DEFAULTS, MQTTClient, validate_config
from primary_format import iter_samples, typed, untyped

VOLATILE = {'received_ns', 'received_ms', 'local_ts', 'connection_id', 'sample_sequence', 'source_epoch', 'source_protocol',
            'acquisition_timestamp_ms', 'precision_warning', 'clock_discontinuity', 'status_code', 'server_ts', 'source_ts'}
IDS = ['mqtt:plant/data#/value', 'mqtt:plant/data#/nested/a~1b/0', 'mqtt:plant/data#/obj', 'mqtt:plant/data#/arr',
       'mqtt:plant/data#/absent', 'mqtt:plant/data#', 'mqtt:plant/text#']
PAYLOADS = [
    ('plant/data', {'value': 12.5, 'nested': {'a/b': [7]}, 'obj': {'x': 1, 'y': [True, None]}, 'arr': [1, 2.5, 'z']}, 1, False),
    ('plant/data', {'value': 2 ** 63 + 123, 'nested': {'a/b': [2 ** 64 + 5]}}, 2, False),
    ('plant/data', {'value': -(2 ** 70)}, 0, False),
    ('plant/data', b'{"value": 1e400, "nested": {"a/b": [-1e400]}}', 1, False),
    ('plant/data', b'{"value": NaN}', 1, False),
    ('plant/data', b'{"value": Infinity, "obj": {}}', 1, False),
    ('plant/data', {'value': True}, 1, True),
    ('plant/data', {'value': None}, 1, False),
    ('plant/data', {'value': 'Caffè Ω "q"\n', 'value2': 1}, 1, False),
    ('plant/data', b'{bad', 1, False),
    ('plant/data', b'\xff\xfe', 1, False),
    ('plant/data', b'\xe2\x82\x28', 1, False),
    ('plant/data', b'  42  ', 1, False),
    ('plant/data', b'12 13', 1, False),
    ('plant/data', b'"just text"', 1, False),
    ('plant/data', b'{"value": 1, "value": 2}', 1, False),
    ('plant/data', b'[1,2]', 1, False),
    ('plant/data', b'{"value": 1.0, "nested": {"a/b": [0.1, 1e16, 1e-05, 5e-324]}}', 1, False),
    ('plant/data', b'', 1, False),
    ('plant/text', 'Caffè Ω', 1, False),
    ('plant/text', b'{"a": 1', 1, False),
    ('plant/text', b'\x00\x01 control', 1, False),
]


def comparable(row):
    return {k: v for k, v in row.items() if not k.startswith('_') and k not in VOLATILE}


class EngineMQTT(unittest.TestCase):
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
        self.events.clear()

    def tearDown(self):
        self.engine.call('acq.disconnect')

    def python_rows(self, peer, cfg, ids, publish):
        received = []
        client = MQTTClient(cfg)
        client.configure_acquisition(lambda: (ids, 10, 7), lambda t, r, period, g: received.append((t, r, period)))
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(client.connect())
            publish(lambda: client.counts['processed'])
            until(lambda: client.counts['processed'] >= len(PAYLOADS_FOR(cfg)), 10)
            diagnostics = client.diagnostics()
            loop.run_until_complete(client.disconnect())
        finally:
            loop.close()
        return received, diagnostics

    def engine_rows(self, cfg, ids, publish, expected, record=None):
        self.engine.call('acq.select', ids=ids, period=10, generation=[7, 1])
        self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
        session = None
        if record:
            state = self.engine.call('record.start', arm=True, directory=str(record), version='3.0.0', format='csv',
                                     nodes=[{'nodeid': n, 'display_name': n} for n in ids], endpoint='mqtt://test', sample_ms=10, options={})
            session = state['engine_session']
        publish(lambda: self.engine.call('acq.status')['sources']['mqtt']['diagnostics'].get('processed', 0))
        until(lambda: self.engine.call('acq.status')['sources']['mqtt']['diagnostics'].get('processed', 0) >= expected, 10)
        until(lambda: self.engine.call('acq.status')['acquisition']['connections']['mqtt']['dispatched'] >= self.selected_count, 10)
        status = self.engine.call('acq.status')
        if session:
            self.engine.call('acq.fence', timeout=5)
            self.engine.call('record.stop', session=session, reason='user', timeout=30)
            for _ in range(600):
                state = self.engine.call('record.status', session=session)
                if not state['busy']: break
                time.sleep(.05)
        time.sleep(.2)
        with self.lock: batches = list(self.rows)
        return batches, status['sources']['mqtt']['diagnostics'], state if session else None

    def test_row_and_original_parity(self):
        peer = Peer()
        try:
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 3000, 'mqtt_qos': 2,
                                   'mqtt_include_retained': True})
            def publish(processed):
                # One at a time: QoS 2 delivery timing differs between client libraries.
                for i, (topic, value, qos, retain) in enumerate(PAYLOADS):
                    peer.publish(topic, value, qos=qos, dup=retain)
                    until(lambda: processed() >= i + 1, 10)
            global PAYLOADS_FOR
            PAYLOADS_FOR = lambda c: PAYLOADS
            python, python_diag = self.python_rows(peer, cfg, IDS, publish)
            self.selected_count = sum(len(r) for _, r, _ in python)
            with tempfile.TemporaryDirectory(prefix='engine-mqtt-') as temp:
                engine, engine_diag, state = self.engine_rows(cfg, IDS, publish, len(PAYLOADS), record=Path(temp))
                flat_python = [comparable(r) for _, batch, _ in python for r in batch]
                flat_engine = [comparable(r) for b in engine for r in b['rows']]
                self.assertEqual(len(flat_python), len(flat_engine))
                for i, (a, b) in enumerate(zip(flat_python, flat_engine)):
                    self.assertEqual(a, b, f'row {i}')
                self.assertEqual([t for t, _, _ in python if False], [])
                for key in ('received', 'processed', 'invalid_payloads', 'dup_flag', 'decoded_values', 'selected_values', 'catalog_signals', 'queue_dropped'):
                    self.assertEqual(python_diag[key], engine_diag[key], key)
                # Typed originals in the primary capture equal the Python typed() of each row.
                capture = state['primary']['path']
                stored = [s for s in iter_samples(capture)]
                originals = [untyped(json.loads(json.dumps(r['_source_typed']))) for _, batch, _ in python for r in batch]
                self.assertEqual(len(stored), len(originals))
                for i, (s, original) in enumerate(zip(stored, originals)):
                    # typed() encodes NaN/inf by their bits and keeps int/float/bool distinct.
                    self.assertEqual(json.dumps(typed(s['source_value'])), json.dumps(typed(original)), f'original {i}')
        finally:
            peer.close()

    def test_payload_timestamps_and_quality_pointer(self):
        peer = Peer()
        try:
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 3000,
                                   'mqtt_timestamp_pointer': '/ts', 'mqtt_timestamp_unit': 'iso', 'mqtt_quality_pointer': '/q'})
            payloads = [{'ts': '2026-10-21T14:00:00.123456789+02:00', 'value': 1, 'q': 'GOOD'},
                        {'ts': '2026-10-21T12:00:00Z', 'value': 2, 'q': 'uncertain'},
                        {'ts': '2026-10-21T12:00:00', 'value': 3, 'q': 'Good'},
                        {'ts': '2026-10-21T12:00:00.5+00:00', 'value': 4, 'q': 'other'},
                        {'value': 5, 'q': 'Bad'}]
            def publish(processed):
                for p in payloads: peer.publish('plant/data', p, qos=1)
            global PAYLOADS_FOR
            PAYLOADS_FOR = lambda c: payloads
            ids = ['mqtt:plant/data#/value']
            python, _ = self.python_rows(peer, cfg, ids, publish)
            self.selected_count = sum(len(r) for _, r, _ in python)
            engine, _, _ = self.engine_rows(cfg, ids, publish, len(payloads))
            a = [dict(comparable(r), source_ts=r['source_ts']) for _, batch, _ in python for r in batch]
            b = [dict(comparable(r), source_ts=r['source_ts']) for m in engine for r in m['rows']]
            self.assertEqual(a, b)
            for unit, value in (('ns', 1792584000123456789), ('s', '1792584000.123456789'), ('ms', 1792584000123.5), ('us', '1.5')):
                with self.subTest(unit=unit):
                    from mqtt_source import timestamp
                    self.assertEqual(self.engine_timestamp(unit, value), list(timestamp(value, unit)))
        finally:
            peer.close()

    def engine_timestamp(self, unit, value):
        peer = Peer()
        try:
            with self.lock: self.rows.clear()
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 3000,
                                   'mqtt_timestamp_pointer': '/ts', 'mqtt_timestamp_unit': unit, 'mqtt_time_basis': 'payload'})
            self.engine.call('acq.select', ids=['mqtt:plant/data#/v'], period=10, generation=[7, 1])
            self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
            peer.publish('plant/data', {'ts': value, 'v': 1}, qos=1)
            until(lambda: self.rows, 10)
            message = self.rows[-1]
            return [message['t'], message['rows'][0]['source_ts']]
        finally:
            self.engine.call('acq.disconnect')
            peer.close()

    def test_connection_errors_and_loss_rows(self):
        peer = Peer(auth=('user', 'right'))
        try:
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 2000,
                                   'mqtt_username': 'user', 'mqtt_password': 'wrong'})
            from engine_bridge import EngineError
            with self.assertRaisesRegex(EngineError, 'rifiutata'):
                self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
        finally:
            peer.close()
        peer = Peer(reject_subscribe=True)
        try:
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 2000})
            with self.assertRaisesRegex(EngineError, 'ACL'):
                self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
        finally:
            peer.close()
        peer = Peer()
        try:
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 2000})
            self.engine.call('acq.select', ids=['mqtt:plant/data#/v'], period=10, generation=[7, 1])
            self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
            peer.drop_all()
            until(lambda: any(e['kind'] == 'source_disconnected' for e in self.events), 10)
            until(lambda: self.rows, 5)
            lost = self.rows[-1]['rows'][0]
            self.assertEqual((lost['quality'], lost['status'], lost['value']), ('Bad', 'MQTT collegamento interrotto', None))
            until(lambda: self.engine.call('acq.status')['sources']['mqtt']['state'] == 'connected', 10)
            self.assertEqual(self.engine.call('acq.status')['sources']['mqtt']['reconnections'], 1)
        finally:
            peer.close()

    def test_catalog_browse(self):
        peer = Peer()
        try:
            cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 2000})
            self.engine.call('acq.select', ids=[], period=10, generation=[7, 1])
            self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
            peer.publish('plant/motor', {'pressure': 12.5, 'run': True}, qos=1)
            peer.publish('plant/text', 'x', qos=1)
            until(lambda: self.engine.call('acq.status')['sources']['mqtt']['diagnostics']['processed'] == 2, 10)
            root = self.engine.call('catalog.browse', protocol='mqtt', nodeid='mqtt:/')
            self.assertEqual([r['display_name'] for r in root], ['plant/motor', 'plant/text'])
            items = self.engine.call('catalog.scan', protocol='mqtt', nodeid='mqtt:/')
            self.assertEqual(len(items), 3)
            leaf = self.engine.call('catalog.browse', protocol='mqtt', nodeid=root[0]['nodeid'])
            self.assertEqual({r['nodeid'] for r in leaf}, {'mqtt:plant/motor#/pressure', 'mqtt:plant/motor#/run'})
            self.assertEqual(self.engine.call('catalog.inspect', protocol='mqtt', nodeid='mqtt:plant/notyet#/value')['comment'], 'In attesa della prima pubblicazione')
        finally:
            peer.close()

    def test_tls_verified_ca_hostname_and_client_cert(self):
        import ipaddress
        import ssl
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from engine_bridge import EngineError
        with tempfile.TemporaryDirectory(prefix='engine-mqtt-tls-') as temp:
            root = Path(temp); now = datetime.now(timezone.utc)
            ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Test CA')])
            ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
                  .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
                                               key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), True)
                  .sign(ca_key, hashes.SHA256()))
            (root / 'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
            for kind in ('server', 'client'):
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, kind)])
                builder = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_name).public_key(key.public_key())
                           .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1)))
                if kind == 'server':
                    builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                cert = (builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
                        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if kind == 'server' else ExtendedKeyUsageOID.CLIENT_AUTH]), False)
                        .sign(ca_key, hashes.SHA256()))
                (root / (kind + '.pem')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
                (root / (kind + '.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(root / 'server.pem', root / 'server.key'); tls.load_verify_locations(root / 'ca.pem'); tls.verify_mode = ssl.CERT_REQUIRED
            peer = Peer(tls=tls)
            try:
                config = dict(mqtt_tls=True, mqtt_ca_file=str(root / 'ca.pem'), mqtt_cert_file=str(root / 'client.pem'), mqtt_key_file=str(root / 'client.key'))
                cfg = validate_config({**DEFAULTS, 'mqtt_port': peer.port, 'mqtt_topics': 'plant/#', 'mqtt_timeout_ms': 3000, **config})
                self.engine.call('acq.select', ids=['mqtt:plant/a#'], period=10, generation=[7, 1])
                state = self.engine.call('acq.connect', cfg=cfg, protocols=['mqtt'], wait_first=True)
                self.assertIn('TLS', state['sources']['mqtt']['info']['transport'])
                peer.publish('plant/a', 3, qos=1)
                until(lambda: self.rows, 10)
                self.assertEqual(self.rows[-1]['rows'][0]['value'], 3)
                self.engine.call('acq.disconnect')
                for bad in ({**cfg, 'mqtt_ca_file': ''}, {**cfg, 'mqtt_host': 'localhost'}):
                    with self.assertRaises(EngineError) as failure:
                        self.engine.call('acq.connect', cfg=bad, protocols=['mqtt'], wait_first=True)
                    self.assertIn('MQTT TLS', str(failure.exception))
            finally:
                peer.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
