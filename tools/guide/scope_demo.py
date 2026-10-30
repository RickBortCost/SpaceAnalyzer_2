"""File .svdx di esempio per la guida: tre canali REAL32 a 1 ms (accelerometri) nella disposizione letta da SvdxReader."""
import math
import struct
from pathlib import Path


def write_scope_project(path: Path, samples: int = 5000, per_chunk: int = 16) -> None:
    start = 134238281553100000   # 2026-05-21 09:09:15.310 UTC
    period = 10000               # 100 ns: 1 ms
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

    names = ['Accelerometro X', 'Accelerometro Y', 'Accelerometro Z']
    blocks = [block(lambda i, k=k: math.sin(2 * math.pi * (20 + 15 * k) * i / 1000) * (1 + .3 * k)) for k in range(3)]
    acquisitions = ''.join(
        f'<AdsAcquisition><DataType>REAL32</DataType><FileHandle>{h}</FileHandle><Guid>g{h}</Guid><Name>{n}</Name><BaseSampleTime>{period}</BaseSampleTime>'
        f'<SymbolName>PlcMain.Accelerometer.f{"XYZ"[h - 1]}_ms2</SymbolName><RawUnit><Unit><Symbol>m/s2</Symbol></Unit></RawUnit></AdsAcquisition>'
        for h, n in enumerate(names, 1))
    xml = ('﻿<?xml version="1.0" encoding="utf-8"?><ScopeProject><Title>Scope Project</Title><SubMember>' + acquisitions + '</SubMember></ScopeProject>').encode('utf-8')
    offset = 20 + 20 * len(blocks)
    table, body = b'', b''
    for handle, content in enumerate(blocks, 1):
        table += struct.pack('<QQI', offset + len(body), len(content), handle)
        body += content
    path.write_bytes(struct.pack('<QQI', offset + len(body), len(xml), len(blocks)) + table + body + xml)
