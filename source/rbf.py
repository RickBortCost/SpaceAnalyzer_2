"""Reference-derived RBF v3 frames, as demonstrated by TEST_RBF.rbf.

No proprietary runtime is required. Unknown versions/layouts are rejected.
The twelve bytes per channel and final header word have undocumented meaning;
new recordings use the zero block and word 57600 observed in the reference.
External application compatibility must be validated separately.
"""
from __future__ import annotations

import itertools
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN

HEADER = struct.Struct('<5I')
TYPES = {0: (1, 'B', 'Boolean'), 2: (2, 'h', 'Int16'),
         7: (4, 'f', 'Float32'), 9: (81, '81s', 'String')}
QUALITY = {0: 'Bad', 1: 'Bad', 2: 'Uncertain', 3: 'Good'}
QUALITY_MARKER = 'SpaceAnalyzer RBF3 quality 0=missing,1=Bad,2=Uncertain,3=Good'
# Keep generated parts within the reference's 57600-frame header setting.
# Its exact external meaning is undocumented; do not extrapolate larger parts.
MAX_FRAMES = 57600


def read_exact(stream, size):
    raw = stream.read(size)
    if len(raw) != size:
        raise ValueError('File RBF troncato')
    return raw


def read_header(stream):
    version, data_bytes, count, period_us, reference_word = HEADER.unpack(read_exact(stream, HEADER.size))
    if version != 3 or not 1 <= count <= 4096 or not 1 <= period_us <= 0xffffffff:
        raise ValueError('Variante RBF non supportata: atteso RBF v3 del campione verificato')
    channels = []
    for i in range(count):
        size = read_exact(stream, 1)[0]
        if not size:
            raise ValueError('Nome canale RBF vuoto')
        name = read_exact(stream, size).decode('cp1252', errors='replace')
        tag, width, a, b = read_exact(stream, 4)
        if tag not in TYPES or width != TYPES[tag][0] or (a, b) != (1, 10):
            raise ValueError(f'Layout RBF non supportato nel canale {name}: tipo {tag}, {width} byte')
        channels.append(dict(name=name, type=tag, size=width, data_type=TYPES[tag][2],
                             nodeid=f'rbf:{name}:{i}'))
    reserved = read_exact(stream, 12 * count)
    # Nonzero blocks may encode scaling or other properties. Never guess them.
    if any(reserved):
        raise ValueError('RBF: metadati canali differenti dal profilo verificato')
    frame = struct.Struct('<II' + ''.join(TYPES[c['type']][1] for c in channels))
    if data_bytes % frame.size:
        raise ValueError('RBF: dimensione dati non multipla di un campione completo')
    return dict(version=version, channels=channels, data_bytes=data_bytes,
                frames=data_bytes // frame.size, data_offset=stream.tell(),
                frame=frame, sample_us=period_us, reference_word=reference_word)


def timestamp(sec, ns):
    if ns >= 1_000_000_000:
        raise ValueError('Timestamp RBF non valido: nanosecondi fuori intervallo')
    return sec * 1000 + ns / 1_000_000


def stamp(sec, ns):
    return datetime.fromtimestamp(sec, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{ns:09d}+00:00'


def inspect(path):
    path = Path(path)
    with path.open('rb') as stream:
        h = read_header(stream)
        end = h['data_offset'] + h['data_bytes']
        if end > path.stat().st_size:
            raise ValueError('File RBF troncato: campioni mancanti')
        stream.seek(end)
        footer = stream.read(1_048_577)
        if len(footer) > 1_048_576 or not footer.startswith(b'\r\nPlotter = ') or not footer.endswith(b'END SAVESTATE\r\n'):
            raise ValueError('RBF: configurazione finale assente, troncata o non supportata')
        h['quality_pairs'] = QUALITY_MARKER.encode('ascii') in footer
        h['start_ms'] = h['end_ms'] = None
        if h['frames']:
            stream.seek(h['data_offset'])
            sec, ns = struct.unpack('<II', read_exact(stream, 8))
            h['start_ms'] = timestamp(sec, ns)
            stream.seek(end - h['frame'].size)
            sec, ns = struct.unpack('<II', read_exact(stream, 8))
            h['end_ms'] = timestamp(sec, ns)
            if h['end_ms'] < h['start_ms']:
                raise ValueError('RBF: intervallo temporale invertito')
    return h


def channel_map(h, metadata=None):
    supplied = (metadata or {}).get('rbf_channels')
    if supplied:
        if not isinstance(supplied, list):
            raise ValueError('Mappa canali RBF non valida')
        result = supplied
    else:
        result = []
        i = 0
        while i < len(h['channels']):
            c = h['channels'][i]
            q = h['channels'][i + 1] if i + 1 < len(h['channels']) else None
            paired = h.get('quality_pairs') and q and q['name'] == c['name'] + '_Q' and q['type'] == 2
            result.append(dict(nodeid=c['nodeid'], display_name=c['name'], unit='',
                               value_channel=i, quality_channel=i + 1 if paired else None,
                               data_type=c['data_type']))
            i += 2 if paired else 1
    for s in result:
        if not isinstance(s, dict):
            raise ValueError('Mappa canali RBF non valida')
        i, q = s.get('value_channel'), s.get('quality_channel')
        if not isinstance(i, int) or not 0 <= i < len(h['channels']) or not s.get('nodeid'):
            raise ValueError('Mappa canali RBF non valida')
        if q is not None and (not isinstance(q, int) or not 0 <= q < len(h['channels']) or h['channels'][q]['type'] != 2):
            raise ValueError('Mappa qualita RBF non valida')
    return result


def iter_rows(path, metadata=None, signal_ids=None, start_ms=None, end_ms=None):
    h = inspect(path)
    meta = metadata or {}
    wanted = set(signal_ids) if signal_ids is not None else None
    selected = [s for s in channel_map(h, meta) if wanted is None or s['nodeid'] in wanted]
    with Path(path).open('rb') as stream:
        stream.seek(h['data_offset'])
        previous = None
        for cycle in range(1, h['frames'] + 1):
            values = h['frame'].unpack(read_exact(stream, h['frame'].size))
            sec, ns = values[:2]
            t = timestamp(sec, ns)
            ticks = sec * 1_000_000_000 + ns
            if previous is not None and ticks < previous:
                raise ValueError('RBF: timestamp non ordinati')
            previous = ticks
            if start_ms is not None and t < start_ms or end_ms is not None and t > end_ms:
                continue
            text_time = stamp(sec, ns)
            for s in selected:
                index = s['value_channel']
                c = h['channels'][index]
                value = values[index + 2]
                qi = s.get('quality_channel')
                q = values[qi + 2] if qi is not None else 3
                quality = QUALITY.get(q, 'Bad')
                if c['type'] == 9:
                    value = value.split(b'\0', 1)[0].decode('cp1252', errors='replace')
                    number = None
                else:
                    number = float(value)
                    if not math.isfinite(number) or c['type'] == 0 and value not in (0, 1):
                        quality = 'Bad'
                yield dict(timestamp_ms=t, timestamp_utc=text_time, node_id=s['nodeid'],
                           signal_name=s.get('display_name') or c['name'],
                           value_numeric=number if quality == 'Good' else None,
                           value_text=str(value), data_type=c['data_type'],
                           good=quality == 'Good', quality=quality,
                           status='RBF_Missing' if q == 0 else 'RBF_' + quality,
                           sample_ms=meta.get('initial_sample_ms') or h['sample_us'] / 1000,
                           cycle=cycle, source_timestamp=text_time, server_timestamp=None)


def header(channels, data_bytes, sample_us, reference_word=57600):
    if not 1 <= len(channels) <= 4096 or not 0 <= data_bytes <= 0xffffffff or not 1 <= sample_us <= 0xffffffff:
        raise ValueError('Limiti intestazione RBF superati')
    out = bytearray(HEADER.pack(3, data_bytes, len(channels), sample_us, reference_word))
    for c in channels:
        name = c['name'].encode('cp1252')
        if not 1 <= len(name) <= 254 or '\0' in c['name']:
            raise ValueError('Nome canale RBF non valido')
        out += bytes([len(name)]) + name + bytes([c['type'], TYPES[c['type']][0], 1, 10])
    out += b'\0' * (12 * len(channels))
    return bytes(out)


def saved_state(channels, mapping):
    lines = ['', 'Plotter = 1', '# Plot X Axes', 'X-Axis00 = 16777215 1 3 10000 0 0.000000 0.000000',
             '# Plot Y Axes', 'Y-Axis00 = 16777215 0.000000 0.000000 19 0 -1', '# Plot Channels']
    for i, s in enumerate(mapping[:16]):
        c = s['value_channel']
        lines.append(f"Channel{i:02d} = {channels[c]['name']} {c} 65535 {i} 0 -1 -1")
    lines += ['# Properties', 'Property0 = Space Analyzer', 'Property5 = ' + QUALITY_MARKER, 'END SAVESTATE', '']
    return '\r\n'.join(lines).encode('cp1252')


def export_parquet(original, nodes, max_bytes=0):
    """Write bounded wide frames, with no interpolation or carried values.

    Acquisition is already finalized in lossless Parquet. SQLite sorts all
    updates by their acquisition timestamp. Repeated updates of one signal at
    the same time create additional frames; disjoint updates can share a frame.
    Every value has an explicit _Q channel, including missing values.
    """
    from recording_reader import RecordingLibrary
    from fda_das import safe_name

    original = Path(original)
    ids = {n['nodeid']: i for i, n in enumerate(nodes)}
    if not 1 <= len(ids) <= 64 or len(ids) != len(nodes):
        raise ValueError('RBF: selezionare da 1 a 64 segnali con identificativi distinti')
    library = RecordingLibrary(original.parent)
    source_meta = library._metadata(original)
    kinds = [None] * len(nodes)
    descriptor, temp = tempfile.mkstemp(prefix='.rbf_sort_', suffix='.sqlite', dir=original.parent)
    os.close(descriptor)
    db = sqlite3.connect(temp)
    stream = partial = None
    files = []
    try:
        db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA synchronous=OFF');db.execute('PRAGMA temp_store=FILE')
        db.execute('PRAGMA cache_size=-8192')
        db.execute('CREATE TABLE samples(t INTEGER,c INTEGER,n INTEGER,v REAL,s TEXT,q INTEGER)')
        staged = []
        for row in library._iter_parquet(original,columns=['timestamp_ms', 'cycle', 'node_id', 'value_numeric', 'value_text', 'data_type', 'quality', 'good']):
            if row.get('node_id') not in ids:
                continue
            i = ids[row['node_id']]
            t = Decimal(str(row['timestamp_ms']))
            if not t.is_finite():
                raise ValueError('Timestamp RBF non finito')
            ticks = int((t * 1_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))
            if not 0 <= ticks < (0x100000000 * 1_000_000_000):
                raise ValueError('Timestamp RBF oltre il limite UInt32 secondi')
            dtype = str(row.get('data_type') or '').upper()
            kind = 0 if dtype in ('BOOL', 'BOOLEAN') else 9 if dtype in ('STRING', 'WSTRING', 'CHAR', 'WCHAR') else 7
            if dtype not in ('', 'NULL', 'NONE'):
                if kinds[i] is not None and kinds[i] != kind:
                    raise ValueError('RBF: tipo del segnale cambiato durante la registrazione')
                kinds[i] = kind
            q = {'Good': 3, 'Uncertain': 2, 'Bad': 1}.get(row.get('quality'), 3 if row.get('good') else 1)
            value, value_text = row.get('value_numeric'), row.get('value_text')
            if q == 3 and kind != 9 and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                q = 1
            if kind == 9:
                value_text = str(value_text or '')
                if q == 3:
                    try:
                        encoded = value_text.encode('cp1252')
                    except UnicodeEncodeError as exc:
                        raise ValueError('RBF: stringa non rappresentabile in Windows-1252; originali conservati') from exc
                    if len(encoded) > 80 or b'\0' in encoded:
                        raise ValueError('RBF: stringa oltre 80 byte o contenente NUL; originali conservati')
                value = 0.
            elif q != 3:
                value = 0.
            elif kind == 0 and value not in (0, 1):
                raise ValueError('RBF: valore Boolean fuori da 0/1')
            staged.append((ticks, int(row.get('cycle') or 0), i, value, value_text, q))
            if len(staged) >= 8192:
                db.executemany('INSERT INTO samples VALUES(?,?,?,?,?,?)', staged)
                staged.clear()
        if staged:
            db.executemany('INSERT INTO samples VALUES(?,?,?,?,?,?)', staged)
        db.commit()
        db.execute('CREATE INDEX chronology ON samples(t,c)')
        channels, mapping = [], []
        for i, node in enumerate(nodes):
            name = safe_name(node.get('display_name') or node['nodeid'], i)
            kind = kinds[i] if kinds[i] is not None else 7
            channels += [dict(name=name, type=kind), dict(name=name + '_Q', type=2)]
            mapping.append(dict(nodeid=node['nodeid'], display_name=node.get('display_name') or node['nodeid'],
                                unit=node.get('unit', ''), value_channel=2*i, quality_channel=2*i+1,
                                data_type=TYPES[kind][2]))
        frame = struct.Struct('<II' + ''.join(TYPES[c['type']][1] for c in channels))
        sample_us = max(1, round(float(source_meta.get('initial_sample_ms') or 10) * 1000))
        raw_header = header(channels, 0, sample_us)
        footer = saved_state(channels, mapping)
        if max_bytes and len(raw_header) + frame.size + len(footer) > max_bytes:
            raise ValueError('Limite RBF inferiore a intestazione e un campione completo')
        frames = part = 0
        first = last = None

        def finish():
            nonlocal stream, partial
            if stream is None:
                return
            stream.write(footer)
            stream.seek(4)
            stream.write(struct.pack('<I', frames * frame.size))
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            stream = None
            destination = Path(str(partial)[:-5])
            info = {**source_meta, 'format': 'rbf', 'layout': 'RBF v3 wide frames', 'signals': nodes,
                    'rbf_channels': mapping, 'frames': frames, 'rows_written': frames*len(nodes),
                    'original_archive': original.name, 'duration_seconds': (last-first)/1e9 if first is not None else 0,
                    'storage': {'format': 'rbf', 'label': 'RBF v3 · Float32 + qualità', 'lossless': False},
                    'rbf_compatibility': 'reference-derived v3; external application validation pending',
                    'timestamp_semantics': 'UInt32 seconds + UInt32 nanoseconds; original timestamps also in Parquet',
                    'quality_semantics': QUALITY_MARKER, 'reference_header_word': 57600}
            if first is not None:
                info['started_at'] = stamp(*divmod(first, 1_000_000_000))
                info['ended_at'] = stamp(*divmod(last, 1_000_000_000))
            meta = destination.with_suffix('.metadata.json')
            tmp = meta.with_suffix('.tmp')
            tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(partial, destination)
            os.replace(tmp, meta)
            partial = None
            files.append(dict(name=destination.name, rows=frames*len(nodes), frames=frames,
                              bytes=destination.stat().st_size, part=part))

        def source_frames():
            for ticks, group in itertools.groupby(db.execute('SELECT t,c,n,v,s,q FROM samples ORDER BY t,c,rowid'), key=lambda r: r[0]):
                values = [b'' if c['type'] == 9 else 0 for c in channels]
                seen = set()
                for _, _, i, v, s, q in group:
                    if i in seen:
                        yield ticks, values
                        values = [b'' if c['type'] == 9 else 0 for c in channels]
                        seen = set()
                    kind = channels[2*i]['type']
                    value = (str(s or '').encode('cp1252') if q == 3 else b'') if kind == 9 else int(v) if kind == 0 else v
                    values[2*i], values[2*i+1] = value, q
                    seen.add(i)
                yield ticks, values

        for ticks, values in source_frames():
            if stream is not None and (frames >= MAX_FRAMES or max_bytes and stream.tell()+frame.size+len(footer) > max_bytes or (frames+1)*frame.size > 0xffffffff):
                finish()
            if stream is None:
                part += 1
                frames = 0
                first = last = None
                destination = original.with_name(original.stem+f'_RBF{part:04d}.rbf')
                partial = Path(str(destination)+'.part')
                stream = partial.open('xb')
                stream.write(raw_header)
            try:
                raw = frame.pack(*divmod(ticks, 1_000_000_000), *values)
            except (OverflowError, struct.error) as exc:
                raise ValueError('RBF: valore oltre i limiti Float32; archivio originale conservato') from exc
            stream.write(raw)
            frames += 1
            if first is None:
                first = ticks
            last = ticks
        if not part:
            part = 1
            destination = original.with_name(original.stem+'_RBF0001.rbf')
            partial = Path(str(destination)+'.part')
            stream = partial.open('xb')
            stream.write(raw_header)
        finish()
        return files
    finally:
        if stream is not None:
            stream.close()
        if partial is not None:
            partial.unlink(missing_ok=True)
        db.close()
        Path(temp).unlink(missing_ok=True)
