"""Bounded, chronological previous-sample projection onto the PDA3 10 ms axis.

This is a compatibility view. Originals, source clocks and quality stay in
Parquet. No extrapolation into the past, no interpolation, no held Bad values.
"""
import math
import os
import sqlite3
import struct
import tempfile


def project(sources, ids, start, requested, partial, policy):
    from recording_reader import RecordingLibrary
    limits = policy.get('max_age_ms', {})
    if policy.get('method') != 'previous' or policy.get('grid_ms') != 10:
        raise ValueError('PDA3: politica di ricampionamento non valida')
    if any(not isinstance(limits.get(n), (int, float)) or not math.isfinite(limits[n]) or limits[n]<10 for n in ids):
        raise ValueError('PDA3: scadenza del campione non valida')
    fd, path = tempfile.mkstemp(prefix='.pda3_grid_', suffix='.sqlite', dir=sources[0].parent)
    os.close(fd)
    db = sqlite3.connect(path)
    try:
        db.execute('PRAGMA temp_store=FILE')
        db.execute('PRAGMA cache_size=-8192')
        db.execute('CREATE TABLE samples(n TEXT,t REAL,v REAL,q TEXT)')
        rows = [0]*len(ids)
        first, last = math.inf, -math.inf
        batch = []
        for source in sources:
            for row in RecordingLibrary(source.parent)._iter_parquet(source):
                nid = row.get('node_id')
                if nid not in ids: continue
                t = float(row['timestamp_ms'])
                if not math.isfinite(t): raise ValueError('PDA3: timestamp non finito')
                if start<=t<start+600000:
                    rows[ids[nid]] += 1
                    first, last = min(first,t), max(last,t)
                if t<start-limits[nid] or t>=start+600000:continue
                batch.append((nid,t,row.get('value_numeric'),row.get('quality') or ('Good' if row.get('good') else 'Bad')))
                if len(batch)>=8192:
                    db.executemany('INSERT INTO samples VALUES(?,?,?,?)',batch);batch.clear()
        if batch: db.executemany('INSERT INTO samples VALUES(?,?,?,?)',batch)
        db.commit()
        db.execute('CREATE INDEX chronology ON samples(n,t)')
        expected = requested or (min(60000,max(1,math.ceil((last-start)/10)+1)) if partial and not math.isinf(last) else 60000)
        data = [bytearray(expected*4) for _ in ids]
        counts, errors = [0]*len(ids), [0.]*len(ids)
        held, duplicates, expired, invalid = 0, 0, 0, 0
        nan = bytes.fromhex('0000c07f')
        for nid, i in ids.items():
            cursor = iter(db.execute('SELECT t,v,q FROM samples WHERE n=? ORDER BY t,rowid',(nid,)))
            upcoming = next(cursor,None)
            previous = None
            for index in range(expected):
                stamp = start+index*10
                while upcoming and upcoming[0]<=stamp:
                    if previous and upcoming[0]==previous[0]: duplicates+=1
                    previous, upcoming = upcoming, next(cursor,None)
                packed = nan
                if previous is None or stamp-previous[0]>limits[nid]:
                    expired += 1
                elif previous[2]!='Good' or previous[1] is None or not math.isfinite(previous[1]):
                    invalid += 1
                else:
                    try:
                        packed=struct.pack('<f',previous[1])
                        value=struct.unpack('<f',packed)[0]
                        if not math.isfinite(value): raise OverflowError()
                    except (OverflowError,struct.error):
                        packed=nan;invalid+=1
                    else:
                        counts[i]+=1
                        held+=stamp>previous[0]
                        errors[i]=max(errors[i],abs(value-previous[1]))
                data[i][index*4:index*4+4]=packed
        return dict(data=data, counts=counts, rows=rows, max_error=errors, expected=expected,
                    first=first, last=last, held_grid_values=held, duplicate_original_times=duplicates,
                    expired_or_missing_grid_values=expired, invalid_grid_values=invalid)
    finally:
        db.close()
        os.unlink(path)
