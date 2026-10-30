"""Run a jsdom UI script against the C# Space Fieldbus Analyzer service and a local asyncua test server.

Test tooling only (Python is not part of the 3.0 product). Usage:
  UI_SCRIPT=test_ui.cjs python3 ui_harness_cs.py
The service executable is SPACE_APP_PATH, else the Release build of source/app/SpaceAnalyzer.
"""
import asyncio
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
sys.path.insert(0, str(HERE))
from opcua_test_server import make_server
from asyncua import ua
import pyarrow as pa
import pyarrow.parquet as pq

COLUMNS = ["timestamp_utc", "timestamp_ms", "elapsed_ms", "cycle", "node_id", "signal_name", "value_numeric", "value_text", "data_type", "status", "good",
           "source_timestamp", "server_timestamp", "sample_ms", "quality", "status_code", "local_timestamp", "ads_filetime", "source_protocol",
           "acquisition_timestamp_ms", "acquisition_origin"]
SCRIPT = os.environ.get('UI_SCRIPT', 'test_ui.cjs')


def app_command():
    configured = os.environ.get('SPACE_APP_PATH')
    if configured:
        return [configured]
    build = SOURCE / 'app' / 'SpaceAnalyzer' / 'bin' / 'Release' / 'net8.0'
    exe = build / ('SpaceAnalyzer.exe' if os.name == 'nt' else 'SpaceAnalyzer')
    if exe.is_file():
        return [str(exe)]
    return [os.environ.get('DOTNET', 'dotnet'), str(build / 'SpaceAnalyzer.dll')]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Loop:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def run(self, coro, timeout=35):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


def iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def write_rows(path, rows):
    if path.suffix == '.csv':
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    else:
        pq.write_table(pa.Table.from_pylist(rows), path)


def multichart_fixtures(recordings):
    start = 1700000000000
    definitions = [{'nodeid': 'ns=2;s=Temperature', 'display_name': 'Temperatura'}, {'nodeid': 'ns=2;s=Power', 'display_name': 'Potenza'}]
    for filename, factor in [('fixture_A.csv', 1), ('fixture_B.parquet', 10)]:
        rows = []
        for i in range(6000):
            for k, node in enumerate(definitions):
                value = (i / 100 if k == 0 else 1000000 + i) * factor
                row = {c: None for c in COLUMNS}
                row.update(timestamp_utc=iso(start + i * 10), timestamp_ms=start + i * 10, elapsed_ms=i * 10, cycle=i + 1, node_id=node['nodeid'],
                           signal_name=node['display_name'], value_numeric=value, value_text=str(value), data_type='Double', status='Good', good=True, sample_ms=10)
                rows.append(row)
        path = recordings / filename
        write_rows(path, rows)
        path.with_suffix('.metadata.json').write_text(json.dumps({'signals': definitions, 'started_at': iso(start), 'ended_at': iso(start + 59990),
                                                                  'rows_written': len(rows), 'duration_seconds': 59.99}))


def scope_fixture(recordings, samples=1000, per_chunk=16):
    """TwinCAT Scope project (.svdx) with two REAL32 channels at 1 ms: 'Posizione' (ramp) and 'Velocita' (negative ramp)."""
    start = 134238281553100000
    period = 10000
    chunks = (samples + per_chunk - 1) // per_chunk
    levels = 2
    data_offset = 83 + 56 * levels + 128

    def block(value):
        head = b'01.00.00.40' + struct.pack('<QQQQ', start, 1234, start, start + samples * period) + struct.pack('<QQQ', levels, per_chunk, period)
        for k in range(levels):
            level = levels - 1 - k
            head += struct.pack('<QQII', chunks if level == 0 else 1, chunks if level == 0 else 1, level, data_offset if level == 0 else 83 + 56 * levels) + bytes(32)
        head += bytes(data_offset - len(head))
        for c in range(chunks):
            n = min(per_chunk, samples - c * per_chunk)
            head += struct.pack('<QI', start + c * per_chunk * period, n)
            for i in range(n):
                head += struct.pack('<If', i * period, value(c * per_chunk + i))
        return head + bytes(64)

    blocks = [block(lambda i: i * 0.5), block(lambda i: -float(i))]
    acquisitions = ''.join(
        f'<AdsAcquisition><DataType>REAL32</DataType><FileHandle>{h}</FileHandle><Guid>g{h}</Guid><Name>{n}</Name><BaseSampleTime>{period}</BaseSampleTime>'
        f'<SymbolName>Main.{n}</SymbolName></AdsAcquisition>' for h, n in ((1, 'Posizione'), (2, 'Velocita')))
    xml = ('\ufeff<?xml version="1.0" encoding="utf-8"?><ScopeProject><Title>Prova</Title><SubMember>' + acquisitions + '</SubMember></ScopeProject>').encode('utf-8')
    offset = 20 + 20 * len(blocks)
    table, body = b'', b''
    for handle, content in enumerate(blocks, 1):
        table += struct.pack('<QQI', offset + len(body), len(content), handle)
        body += content
    (recordings / 'fixture_C.svdx').write_bytes(struct.pack('<QQI', offset + len(body), len(xml), len(blocks)) + table + body + xml)


def fft_fixture(recordings, nodeid, fmt):
    """256 samples of a 125 Hz sine at 1 kHz, exported by the application's own FDA/RBF tool."""
    base = 1700000000000
    rows = []
    for i in range(256):
        value = math.sin(2 * math.pi * 125 * i / 1000)
        row = {c: None for c in COLUMNS}
        row.update(timestamp_utc=iso(base + i), timestamp_ms=float(base + i), elapsed_ms=float(i), cycle=i + 1, node_id=nodeid, signal_name='FFT 125 Hz',
                   value_numeric=value, value_text=repr(value), data_type='Double', status='Good', good=True, sample_ms=1, quality='Good', source_protocol='opcua')
        rows.append(row)
    original = recordings / 'fft_fixture.parquet'
    write_rows(original, rows)
    original.with_suffix('.metadata.json').write_text(json.dumps({'signals': [{'nodeid': nodeid, 'display_name': 'FFT 125 Hz'}], 'initial_sample_ms': 1,
                                                                   'started_at': iso(base), 'state': 'stopped'}))
    nodes = recordings / 'fft_nodes.json'
    nodes.write_text(json.dumps([{'nodeid': nodeid, 'display_name': 'FFT 125 Hz'}]))
    output = subprocess.run(app_command() + ['--tool', 'export-' + fmt, str(original), str(nodes)], capture_output=True, text=True, encoding='utf-8', timeout=120)
    if output.returncode:
        raise RuntimeError(output.stdout + output.stderr)
    nodes.unlink()
    return json.loads(output.stdout)[0]['name']


class Service:
    """The C# service in an isolated data/application folder."""

    def __init__(self, work, extra=None):
        self.work = work
        self.data, self.application = work / 'data', work / 'application'
        (self.application / 'Recordings').mkdir(parents=True, exist_ok=True)
        port = free_port()
        env = dict(os.environ, OPCUA_DATA_DIR=str(self.data), OPCUA_APP_DIR=str(self.application), OPCUA_NO_BROWSER='1', OPCUA_WEB_PORT=str(port),
                   SPACE_BASE_DIR=str(SOURCE), SPACE_TEST_HOOKS='1', **(extra or {}))
        self.log = (work / 'service.log').open('w')
        self.process = subprocess.Popen(app_command(), env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.base = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(self.base + '/api/status', timeout=1) as response:
                    if json.load(response).get('app') == 'Space Fieldbus Analyzer':
                        break
            except OSError:
                pass
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Servizio C# non avviato:\n' + (work / 'service.log').read_text())
            time.sleep(.1)

    def log_tail(self):
        path = self.data / 'opcua-client.log'
        return path.read_text(errors='replace')[-6000:] if path.is_file() else ''

    def defaults(self):
        with urllib.request.urlopen(self.base + '/api/config', timeout=5) as response:
            cfg = json.load(response)
        for key in ('client_cert_abs', 'client_key_abs', 'server_cert_abs', 'server_cert_present', 'server_cert_sha256', 'version', 'max_selected',
                    'saved_settings_present', 'saved_at', 'selected_nodes', 'saved_server_data', 'data_dir', 'recordings_dir', 'workspace',
                    'calculations', 'recording_busy'):
            cfg.pop(key, None)
        return cfg

    def stop(self):
        try:
            html = urllib.request.urlopen(self.base, timeout=2).read().decode()
            token = re.search(r'content="([^"]+)" name="opcua-token"', html).group(1)
            request = urllib.request.Request(self.base + '/api/shutdown', data=b'{}', method='POST', headers={'X-OPCUA-Token': token, 'Content-Type': 'application/json'})
            urllib.request.urlopen(request, timeout=5).read()
            self.process.wait(60)
        except Exception:
            self.process.kill()
            self.process.wait(10)
        self.log.close()


def run_node(service, args, env=None):
    result = subprocess.run(['node', str(HERE / SCRIPT), service.base, *args], env=env or os.environ, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=int(os.environ.get('UI_TIMEOUT', '150')))
    if result.returncode:
        raise RuntimeError(result.stdout + '\n' + result.stderr + '\n--- service log ---\n' + service.log_tail())
    return result


MULTI = ('test_source_sessions_ui.cjs', 'test_live_interval_ui.cjs', 'test_recording_overlap_ui.cjs')


def multi():
    """Four protocols against local peers (test_source_sessions_ui_harness of 2.x)."""
    from s7_test_server import start_server
    from ads_simulator import Peer as ADSPeer
    from mqtt_peer import Peer as MQTTPeer
    work = Path(tempfile.mkdtemp(prefix='space-ui-cs-'))
    loop = Loop()
    server, endpoint, root, nodes = loop.run(make_server())
    s7_server, s7_port = start_server()
    ads = loop.run(ADSPeer().start())
    mqtt = MQTTPeer()

    async def vary():
        i = 0
        while True:
            i += 1
            await nodes[0].write_value(float(i))
            await asyncio.sleep(.02)
    task = asyncio.run_coroutine_threadsafe(vary(), loop.loop)
    stop = threading.Event()

    def publish():
        i = 0
        while not stop.wait(.02):
            i += 1
            mqtt.publish('plant/motor', dict(load=i, **{'field' + str(i // 30): i}), qos=1)
    threading.Thread(target=publish, daemon=True).start()
    service = Service(work)
    try:
        ids = {'opcua': nodes[0].nodeid.to_string(), 'ads': 'ads:MAIN.Wave', 'mqtt': 'mqtt:plant/motor#/load', 's7': 's7:DB1.DBD0:REAL'}
        cfg = {**service.defaults(), 'source': 'opcua', 'enabled_sources': ['opcua', 'ads', 'mqtt', 's7'], 'endpoint': endpoint, 'security': 'None',
               'security_mode': 'None', 'sample_ms': 20, 'source_intervals': {'opcua': 20, 'ads': 30, 'mqtt': 40, 's7': 50},
               'ads_tcp_port': ads.port, 'mqtt_port': mqtt.port, 'mqtt_topics': 'plant/#', 's7_host': '127.0.0.1', 's7_tcp_port': s7_port,
               's7_tags': [{'address': 'DB1.DBD0', 'data_type': 'REAL'}]}
        env = {**os.environ, 'TEST_MULTI_CONFIG': json.dumps(cfg), 'TEST_MULTI_SIGNALS': json.dumps(ids)}
        result = run_node(service, [endpoint, root.nodeid.to_string(), ids['opcua']], env)
        print(result.stdout, end='')
    finally:
        task.cancel()
        stop.set()
        service.stop()
        loop.run(ads.close())
        mqtt.close()
        s7_server.stop()
        s7_server.destroy()
        loop.run(server.stop())
        if not os.environ.get('UI_KEEP'):
            shutil.rmtree(work, ignore_errors=True)


def s7():
    """Classic S7 against the Snap7 simulator (test_s7_ui_harness of 2.x)."""
    from s7_test_server import start_server
    work = Path(tempfile.mkdtemp(prefix='space-ui-cs-'))
    peer, port = start_server()
    service = Service(work)
    try:
        print(run_node(service, [str(port)]).stdout)
    finally:
        service.stop()
        peer.stop()
        peer.destroy()
        if not os.environ.get('UI_KEEP'):
            shutil.rmtree(work, ignore_errors=True)


def main():
    if SCRIPT in MULTI:
        return multi()
    if SCRIPT == 'test_s7_ui.cjs':
        return s7()
    work = Path(tempfile.mkdtemp(prefix='space-ui-cs-'))
    data, application = work / 'data', work / 'application'
    recordings = application / 'Recordings'
    recordings.mkdir(parents=True)
    loop = Loop()
    server, endpoint, root, nodes = loop.run(make_server())

    async def add_signal():
        return await root.add_variable(ua.NodeId('Power', 2), 'Potenza', 1000000.)
    power = loop.run(add_signal())
    if os.environ.get('MULTICHART_TEST') or 'fixture_A' in (HERE / SCRIPT).read_text(encoding='utf-8'):
        multichart_fixtures(recordings)
        scope_fixture(recordings)
    if SCRIPT == 'test_rbf_ui.cjs':
        references = Path(os.environ.get('SPACE_ANALYZER_REFERENCE_DIR', Path(__file__).resolve().parents[4] / 'upload'))
        for original, target in [('TEST_RBF.rbf', 'TEST_RBF.rbf'), ('TEST DAS 2.DAS', 'TEST_DAS_2.das')]:
            if (references / original).is_file():
                shutil.copy2(references / original, recordings / target)
    if SCRIPT in ('test_fda_ui.cjs', 'test_rbf_ui.cjs'):
        os.environ['UI_FFT_FIXTURE'] = fft_fixture(recordings, nodes[0].nodeid.to_string(), 'rbf' if SCRIPT == 'test_rbf_ui.cjs' else 'fda')

    async def vary():
        phase = 0
        while True:
            phase += .05
            await nodes[0].write_value(21.5 + math.sin(phase))
            await power.write_value(1000000. + 100000. * math.sin(phase))
            await asyncio.sleep(.01)
    task = asyncio.run_coroutine_threadsafe(vary(), loop.loop)

    peers = {}
    publisher_stop = threading.Event()
    if SCRIPT in ('test_mqtt_ui.cjs', 'test_live_extensions_ui.cjs'):
        from mqtt_peer import Peer as MQTTPeer
        peers['mqtt'] = MQTTPeer(auth=('operator', ' test-session '))
        os.environ['TEST_MQTT_PORT'] = str(peers['mqtt'].port)

        def publish():
            i = 0
            while not publisher_stop.wait(.02):
                i += 1
                payload = dict(pressure=20 + math.sin(i / 10), load=10 + i % 7, counter=i, ts=time.time_ns())
                if SCRIPT == 'test_live_extensions_ui.cjs':
                    payload['newfield' + str(i // 100)] = i
                peers['mqtt'].publish('plant/motor', payload, qos=1)
        threading.Thread(target=publish, daemon=True).start()
    if SCRIPT == 'test_influx_ui.cjs':
        from influx_peer import Peer as InfluxPeer
        peers['influx'] = InfluxPeer()
        os.environ['TEST_INFLUX_PORT'] = str(peers['influx'].port)
    if SCRIPT == 'test_postgres_ui.cjs':
        from postgres_peer import Peer as PostgresPeer, PASSWORD as PG_PASSWORD
        peers['postgres'] = PostgresPeer()
        os.environ['TEST_PG_PORT'] = str(peers['postgres'].port)
        os.environ['TEST_PG_PASSWORD'] = PG_PASSWORD
    if SCRIPT == 'test_ssh_ui.cjs':
        from ssh_peer import Peer as SSHPeer
        peers['ssh'] = SSHPeer()
        os.environ['TEST_SSH_PORT'] = str(peers['ssh'].port)
    if SCRIPT == 'test_ads_ui.cjs':
        from ads_simulator import Peer
        peers['ads'] = loop.run(Peer().start())
        os.environ['TEST_ADS_PORT'] = str(peers['ads'].port)

    service = Service(work)
    base = service.base
    try:
        result = run_node(service, [endpoint, root.nodeid.to_string(), nodes[0].nodeid.to_string(), power.nodeid.to_string()])
        report = json.loads(result.stdout)
        for info in report.get('files', []):
            path = Path(info['path'])
            if info['format'] == 'csv':
                with path.open(encoding='utf-8-sig', newline='') as f:
                    rows = list(csv.DictReader(f))
            else:
                rows = pq.read_table(path).to_pylist()
            assert len(rows) == info['rows_written'], (len(rows), info['rows_written'])
            if SCRIPT != 'test_calculations_ui.cjs':
                assert info['rows_written'] == info['cycles_received'] * 2, (info['rows_written'], info['cycles_received'])
            else:
                assert any(str(r['node_id']).startswith('calc:') for r in rows)
            assert len({r['value_numeric'] for r in rows if r['node_id'] == nodes[0].nodeid.to_string()}) > 3
            info['path'] = str(Path('Recordings') / path.name)
        print(json.dumps(report, indent=2))
    finally:
        task.cancel()
        service.stop()
        publisher_stop.set()
        if 'mqtt' in peers:
            peers['mqtt'].close()
        if 'ssh' in peers:
            peers['ssh'].close()
        if 'influx' in peers:
            peers['influx'].close()
        if 'postgres' in peers:
            peers['postgres'].close()
        if 'ads' in peers:
            loop.run(peers['ads'].close())
        loop.run(server.stop())
        if not os.environ.get('UI_KEEP'):
            shutil.rmtree(work, ignore_errors=True)
        else:
            print('work dir:', work, file=sys.stderr)


if __name__ == '__main__':
    main()
