"""Run a jsdom UI script against the C# Space Analyzer service and a local asyncua test server.

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
    output = subprocess.run(app_command() + ['--tool', 'export-' + fmt, str(original), str(nodes)], capture_output=True, text=True, timeout=120)
    if output.returncode:
        raise RuntimeError(output.stdout + output.stderr)
    nodes.unlink()
    return json.loads(output.stdout)[0]['name']


def main():
    work = Path(tempfile.mkdtemp(prefix='space-ui-cs-'))
    data, application = work / 'data', work / 'application'
    recordings = application / 'Recordings'
    recordings.mkdir(parents=True)
    loop = Loop()
    server, endpoint, root, nodes = loop.run(make_server())

    async def add_signal():
        return await root.add_variable(ua.NodeId('Power', 2), 'Potenza', 1000000.)
    power = loop.run(add_signal())
    if os.environ.get('MULTICHART_TEST'):
        multichart_fixtures(recordings)
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
    if SCRIPT == 'test_ssh_ui.cjs':
        from ssh_peer import Peer as SSHPeer
        peers['ssh'] = SSHPeer()
        os.environ['TEST_SSH_PORT'] = str(peers['ssh'].port)
    if SCRIPT == 'test_ads_ui.cjs':
        from ads_simulator import Peer
        peers['ads'] = loop.run(Peer().start())
        os.environ['TEST_ADS_PORT'] = str(peers['ads'].port)

    port = free_port()
    env = dict(os.environ, OPCUA_DATA_DIR=str(data), OPCUA_APP_DIR=str(application), OPCUA_NO_BROWSER='1', OPCUA_WEB_PORT=str(port),
               SPACE_BASE_DIR=str(SOURCE))
    log = (work / 'service.log').open('w')
    service = subprocess.Popen(app_command(), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(base + '/api/status', timeout=1) as response:
                    if json.load(response).get('app') == 'Space Analyzer':
                        break
            except OSError:
                pass
            if service.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Servizio C# non avviato:\n' + (work / 'service.log').read_text())
            time.sleep(.1)
        result = subprocess.run(['node', str(HERE / SCRIPT), base, endpoint, root.nodeid.to_string(), nodes[0].nodeid.to_string(), power.nodeid.to_string()],
                                env=os.environ, capture_output=True, text=True, timeout=int(os.environ.get('UI_TIMEOUT', '120')))
        if result.returncode:
            raise RuntimeError(result.stdout + '\n' + result.stderr + '\n--- service log ---\n' + (data / 'opcua-client.log').read_text(errors='replace')[-6000:])
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
        try:
            html = urllib.request.urlopen(base, timeout=2).read().decode()
            token = re.search(r'content="([^"]+)" name="opcua-token"', html).group(1)
            request = urllib.request.Request(base + '/api/shutdown', data=b'{}', method='POST', headers={'X-OPCUA-Token': token, 'Content-Type': 'application/json'})
            urllib.request.urlopen(request, timeout=5).read()
            service.wait(60)
        except Exception:
            service.kill()
            service.wait(10)
        publisher_stop.set()
        if 'mqtt' in peers:
            peers['mqtt'].close()
        if 'ssh' in peers:
            peers['ssh'].close()
        if 'ads' in peers:
            loop.run(peers['ads'].close())
        loop.run(server.stop())
        log.close()
        if not os.environ.get('UI_KEEP'):
            shutil.rmtree(work, ignore_errors=True)
        else:
            print('work dir:', work, file=sys.stderr)


if __name__ == '__main__':
    main()
