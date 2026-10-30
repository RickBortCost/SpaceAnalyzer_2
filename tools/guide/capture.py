"""Cattura delle schermate della guida (strumento di documentazione, non fa parte del prodotto).

Avvia il servizio reale, un server OPC UA dimostrativo (otto segnali di impianto), il server InfluxDB di prova e un PostgreSQL temporaneo (se installato),
guida l'interfaccia con Chromium (Playwright) e salva in tools/guide/figures le immagini con i riquadri numerati dei controlli descritti nelle tabelle.

Uso:  python tools/guide/capture.py [--only id1,id2] [--list]
Requisiti: playwright (Chromium), pillow, asyncua, pyarrow, il servizio compilato (dotnet build source/app/SpaceAnalyzer -c Release).
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / 'source'
FIGURES = HERE / 'figures'
sys.path.insert(0, str(SOURCE / 'tests'))
sys.path.insert(0, str(HERE))

import demo_opcua  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

RED = (214, 40, 40)
FONT_PATHS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 'C:/Windows/Fonts/arialbd.ttf']


def font(size):
    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def app_command():
    configured = os.environ.get('SPACE_APP_PATH')
    if configured:
        return [configured]
    dll = SOURCE / 'app/SpaceAnalyzer/bin/Release/net8.0/SpaceAnalyzer.dll'
    if not dll.exists():
        subprocess.run(['dotnet', 'build', str(SOURCE / 'app/SpaceAnalyzer'), '-c', 'Release', '-v', 'q'], check=True)
    return ['dotnet', str(dll)]


class Stack:
    """Servizio + server dimostrativi, in cartelle temporanee."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='guida-'))
        self.data, self.app = self.tmp / 'data', self.tmp / 'app'
        self.data.mkdir()
        self.app.mkdir()
        self.recordings = self.app / 'Recordings'
        self.recordings.mkdir()
        self.peers = {}
        self.process = None
        self.opc_close = None

    # ---- registrazioni di esempio ----------------------------------------------------------------------------------
    def seed_recordings(self):
        """Copia le registrazioni di esempio del repository e crea la registrazione sintetica a 1 kHz per la FFT."""
        import csv
        import math
        import random
        from datetime import datetime, timedelta, timezone
        for name in ('Confronto_1ms.csv', 'Confronto_1ms.metadata.json', 'Confronto_10ms.parquet', 'Confronto_10ms.metadata.json'):
            shutil.copy(ROOT / 'examples/Esempi_Confronto' / name, self.recordings / name)
        from scope_demo import write_scope_project
        write_scope_project(self.recordings / 'Scope_Accelerometri.svdx')
        start = datetime(2026, 10, 21, 9, 30, 0, tzinfo=timezone.utc)
        rows, rnd = 10000, random.Random(7)
        header = ['timestamp_utc', 'timestamp_ms', 'elapsed_ms', 'cycle', 'node_id', 'signal_name', 'value_numeric', 'value_text', 'data_type', 'status', 'good',
                  'source_timestamp', 'server_timestamp', 'sample_ms', 'quality', 'status_code', 'local_timestamp', 'ads_filetime']
        path = self.recordings / 'Vibrazione_1kHz.csv'
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(header)
            for i in range(rows):
                t = start + timedelta(milliseconds=i)
                v = (math.sin(2 * math.pi * 50 * i / 1000) + .5 * math.sin(2 * math.pi * 150 * i / 1000) + .25 * math.sin(2 * math.pi * 250 * i / 1000) + rnd.gauss(0, .08))
                w.writerow([t.isoformat(), f'{t.timestamp() * 1000:.1f}', i, i + 1, 'demo:Vibration', 'Vibrazione', f'{v:.6f}', f'{v:.6f}', 'Double', 'Good', 'True', '', '', 1, 'Good', '', '', ''])
        end = start + timedelta(milliseconds=rows - 1)
        (self.recordings / 'Vibrazione_1kHz.metadata.json').write_text(json.dumps({
            'signals': [{'nodeid': 'demo:Vibration', 'display_name': 'Vibrazione', 'unit': 'mm/s'}], 'started_at': start.isoformat(), 'ended_at': end.isoformat(),
            'duration_seconds': (rows - 1) / 1000, 'rows_written': rows, 'initial_sample_ms': 1, 'session_name': 'Vibrazione sintetica 1 kHz'}, indent=2), encoding='utf-8')

    # ---- server dimostrativi ---------------------------------------------------------------------------------------
    def start_opcua(self):
        ready = threading.Event()
        box = {}

        def run():
            async def main():
                endpoint, close = await demo_opcua.start()
                box['endpoint'], box['close'] = endpoint, close
                box['stop'] = asyncio.Event()
                ready.set()
                await box['stop'].wait()
                await close()
            asyncio.run(main())
        threading.Thread(target=run, daemon=True).start()
        if not ready.wait(30):
            raise RuntimeError('server OPC UA non avviato')
        self.opc_endpoint = box['endpoint']
        self.opc_box = box

    def start_influx(self):
        from influx_peer import Peer
        self.peers['influx'] = Peer()
        self.influx_url = f'http://127.0.0.1:{self.peers["influx"].port}'
        from influx_peer import TOKEN
        self.influx_token = TOKEN

    def start_postgres(self):
        try:
            from postgres_peer import Peer, PASSWORD, USER, DATABASE
            self.peers['postgres'] = Peer()
        except Exception as exc:  # PostgreSQL non installato: i capitoli relativi usano le immagini già presenti
            print('PostgreSQL non disponibile:', exc)
            return
        self.pg = {'host': '127.0.0.1', 'port': self.peers['postgres'].port, 'database': DATABASE, 'user': USER, 'password': PASSWORD}

    # ---- servizio ---------------------------------------------------------------------------------------------------
    def start_service(self):
        port = free_port()
        env = dict(os.environ, OPCUA_DATA_DIR=str(self.data), OPCUA_APP_DIR=str(self.app), OPCUA_NO_BROWSER='1', OPCUA_WEB_PORT=str(port), SPACE_BASE_DIR=str(SOURCE))
        self.log = (self.tmp / 'service.log').open('w')
        self.process = subprocess.Popen(app_command(), env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.base = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                urllib.request.urlopen(self.base + '/', timeout=2).read(10)
                return
            except Exception:
                time.sleep(.3)
        raise RuntimeError('servizio non avviato')

    def stop(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(10)
            except Exception:
                self.process.kill()
        if getattr(self, 'opc_box', None):
            self.opc_box['stop'].set()
        for peer in self.peers.values():
            try:
                peer.close()
            except Exception:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---- annotazione ----------------------------------------------------------------------------------------------------------

def annotate(png: Path, out: Path, boxes, scale=1.0):
    """Riquadri rossi con numeri (boxes: [(n, x, y, w, h)]) sull'immagine; salva in JPEG leggero."""
    image = Image.open(png).convert('RGB')
    draw = ImageDraw.Draw(image)
    f = font(max(11, int(13 * scale)))
    r = max(9, int(10 * scale))
    for n, x, y, w, h in boxes:
        draw.rectangle([x - 1, y - 1, x + w + 1, y + h + 1], outline=RED, width=2)
        cx, cy = x + r - 2, y - r + 2
        cx, cy = max(r + 1, cx), max(r + 1, cy)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=RED, outline=(255, 255, 255))
        text = str(n)
        tw = draw.textlength(text, font=f)
        draw.text((cx - tw / 2, cy - f.size / 2 - 1), text, fill=(255, 255, 255), font=f)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, 'JPEG', quality=84, optimize=True)


class Shooter:
    """Pagina Playwright con le funzioni comuni alle figure."""

    def __init__(self, stack, page):
        self.s, self.page = stack, page

    def open(self, width=1920, height=1080):
        self.page.set_viewport_size({'width': width, 'height': height})
        self.page.goto(self.s.base + '/')
        self.page.wait_for_function('window.opcuaConfigReady!==undefined')
        self.page.wait_for_timeout(800)

    def js(self, script, *args):
        return self.page.evaluate(script, *args)

    def click(self, selector):
        self.page.click(selector)

    def wait(self, ms=500):
        self.page.wait_for_timeout(ms)

    def box(self, selector):
        b = self.page.locator(selector).first.bounding_box()
        return b

    def shot(self, name, callouts=(), clip=None, selector=None, pad=0, full=False):
        """callouts: [(n, selector)]; clip: (x, y, w, h) oppure selector del contenitore da ritagliare."""
        self.wait(250)
        raw = self.s.tmp / f'{name}.png'
        area = None
        if selector:
            b = self.box(selector)
            area = (max(0, b['x'] - pad), max(0, b['y'] - pad), b['width'] + 2 * pad, b['height'] + 2 * pad)
        elif clip:
            area = clip
        vw = self.page.viewport_size
        if area:
            area = (max(0, area[0]), max(0, area[1]), min(area[2], vw['width'] - area[0]), min(area[3], vw['height'] - area[1]))
        self.page.screenshot(path=str(raw), clip=dict(x=area[0], y=area[1], width=area[2], height=area[3]) if area else None)
        ox, oy = (area[0], area[1]) if area else (0, 0)
        boxes = []
        for n, sel in callouts:
            if isinstance(sel, tuple):  # rettangolo fisso relativo alla pagina
                x, y, w, h = sel
            else:
                loc = self.page.locator(sel)
                if loc.count() == 0:
                    print(f'  ! {name}: selettore {n} non trovato: {sel}')
                    continue
                b = loc.first.bounding_box()
                if not b:
                    print(f'  ! {name}: selettore {n} non visibile: {sel}')
                    continue
                x, y, w, h = b['x'], b['y'], b['width'], b['height']
            boxes.append((n, x - ox, y - oy, w, h))
        annotate(raw, FIGURES / f'{name}.jpg', boxes)
        print('  figura', name, Image.open(FIGURES / f'{name}.jpg').size)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--no-postgres', action='store_true')
    args = parser.parse_args()
    import figures
    if args.list:
        print('\n'.join(figures.ORDER))
        return
    only = [x for x in args.only.split(',') if x]
    from playwright.sync_api import sync_playwright
    stack = Stack()
    try:
        stack.seed_recordings()
        stack.start_opcua()
        stack.start_influx()
        if not args.no_postgres:
            stack.start_postgres()
        stack.start_service()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='it-IT', timezone_id='Europe/Rome')
            page = context.new_page()
            page.on('pageerror', lambda e: print('  pageerror', e))
            shooter = Shooter(stack, page)
            figures.run(shooter, stack, only)
            browser.close()
    finally:
        stack.stop()


if __name__ == '__main__':
    main()
