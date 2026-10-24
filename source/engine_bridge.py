"""Bridge to the C# acquisition and recording engine (SpaceAnalyzer.Engine).

The engine is a child process: framed JSON on its stdin/stdout, no ports.
Frame: u32 little-endian payload length, one type byte, UTF-8 JSON payload.
"""
import json
import os
import struct
import subprocess
import sys
import threading
from concurrent.futures import Future
from pathlib import Path

REQUEST, RESPONSE, EVENT, ROWS, LOG = 1, 2, 3, 4, 5
HEADER = struct.Struct('<IB')
ROOT = Path(__file__).resolve().parent


class EngineError(RuntimeError):
    pass


def engine_command():
    """Packaged EXE runtime first, then a development build, then `dotnet <dll>`."""
    configured = os.environ.get('SPACE_ENGINE_PATH')
    candidates = [Path(configured)] if configured else []
    name = 'SpaceAnalyzer.Engine.exe' if os.name == 'nt' else 'SpaceAnalyzer.Engine'
    candidates += [ROOT / 'engine' / name]
    for config in ('Release', 'Debug'):
        candidates.append(ROOT / 'engine' / 'SpaceAnalyzer.Engine' / 'bin' / config / 'net8.0' / name)
    for path in candidates:
        if path.is_file():
            return [str(path)]
    for config in ('Release', 'Debug'):
        dll = ROOT / 'engine' / 'SpaceAnalyzer.Engine' / 'bin' / config / 'net8.0' / 'SpaceAnalyzer.Engine.dll'
        if dll.is_file():
            return [os.environ.get('DOTNET', 'dotnet'), str(dll)]
    raise EngineError('Motore di acquisizione C# non trovato: compilare source/engine o impostare SPACE_ENGINE_PATH')


class Engine:
    def __init__(self, on_event=None, on_rows=None, command=None):
        self.on_event = on_event
        self.on_rows = on_rows
        self.lock = threading.Lock()
        self.pending = {}
        self.next_id = 0
        self.error = ''
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        self.process = subprocess.Popen(command or engine_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None, bufsize=0, creationflags=flags)
        self.reader = threading.Thread(target=self._read, name='engine-reader', daemon=True)
        self.reader.start()
        self.info = self.call('hello')

    def _read_exact(self, count):
        chunks = []
        while count:
            block = self.process.stdout.read(count)
            if not block:
                return None
            chunks.append(block)
            count -= len(block)
        return b''.join(chunks)

    def _read(self):
        try:
            while True:
                header = self._read_exact(HEADER.size)
                if header is None:
                    break
                size, kind = HEADER.unpack(header)
                payload = self._read_exact(size)
                if payload is None:
                    break
                message = json.loads(payload)
                if kind == RESPONSE:
                    with self.lock:
                        future = self.pending.pop(message['id'], None)
                    if future is None:
                        continue
                    if message.get('ok'):
                        future.set_result(message.get('result'))
                    else:
                        future.set_exception(EngineError(message.get('error') or 'Errore motore'))
                elif kind == EVENT and self.on_event:
                    self.on_event(message)
                elif kind == ROWS and self.on_rows:
                    self.on_rows(message)
        except Exception as exc:  # the engine must never fail silently
            self.error = 'Lettura dal motore interrotta: ' + str(exc)
        finally:
            self.error = self.error or 'Motore di acquisizione terminato'
            with self.lock:
                pending, self.pending = self.pending, {}
            for future in pending.values():
                future.set_exception(EngineError(self.error))

    def call(self, op, timeout=30, **args):
        future = Future()
        with self.lock:
            if self.error:
                raise EngineError(self.error)
            self.next_id += 1
            ident = self.next_id
            payload = json.dumps({'id': ident, 'op': op, 'args': args}, ensure_ascii=False, allow_nan=True).encode('utf-8')
            self.pending[ident] = future
            try:
                self.process.stdin.write(HEADER.pack(len(payload), REQUEST) + payload)
                self.process.stdin.flush()
            except OSError as exc:
                self.pending.pop(ident, None)
                raise EngineError('Motore di acquisizione non raggiungibile: ' + str(exc)) from None
        return future.result(timeout)

    @property
    def alive(self):
        return self.process.poll() is None and not self.error

    def close(self, timeout=60):
        try:
            if self.alive:
                self.call('shutdown', timeout=timeout)
        except EngineError:
            pass
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
