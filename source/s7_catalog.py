"""Stable S7 symbolic/UDP identities and a shared hierarchical presentation."""
import math
import re
from s7_client import SCALARS, parse_tag


def integer(value, name, low, high):
    try:
        n = float(value)
        if isinstance(value, bool) or not math.isfinite(n) or not n.is_integer() or not low <= n <= high:
            raise ValueError()
        return int(n)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'{name}: intero tra {low} e {high}') from None


def parse_node(value, allow_folder=False):
    if not isinstance(value, str): raise ValueError('Identificativo S7 richiesto')
    value = value.strip()
    if value.startswith('s7:sym:'):
        name = value[7:]
        folder = name.startswith('/')
        if not name or len(name) > 2048 or any(ord(c) < 32 for c in name):
            raise ValueError('Nome simbolico S7 non valido')
        if folder and not allow_folder: raise ValueError('Selezionare una variabile, non un ramo S7')
        return dict(nodeid=value, name=name.lstrip('/') if folder else name, folder=folder, mode='symbolic')
    if value == 's7:udp:/':
        if not allow_folder: raise ValueError('Selezionare un campo del telegramma UDP')
        return dict(nodeid=value, folder=True, mode='udp', name='')
    if value.startswith('s7:udp:'):
        match = re.fullmatch(r's7:udp:(\d{1,4})(?:\.([0-7]))?:([A-Za-z]+)(?:\[(\d{1,3})\])?', value)
        if not match: raise ValueError('UDP: usare s7:udp:0:REAL o s7:udp:4.0:BOOL (offset dal payload)')
        offset, bit, kind, length = match.groups(); kind = kind.upper()
        # Reuse the exact classic S7 type/length checks and big-endian decoder.
        width = 'X' if kind == 'BOOL' else 'B' if kind == 'STRING' or SCALARS.get(kind,('',0))[1] == 1 else 'W' if SCALARS.get(kind,('',0))[1] == 2 else 'D'
        address = f'DB1.DB{width}{int(offset)}' + ('.'+bit if bit is not None else '')
        tag = parse_tag(address+':'+kind+(f'[{length}]' if length else ''))
        if tag['offset']+tag['size'] > 4090: raise ValueError('Campo UDP oltre il payload massimo di 4090 byte')
        tag.update(nodeid=f's7:udp:{int(offset)}'+('.'+bit if bit is not None else '')+':'+tag['data_type'], mode='udp', folder=False)
        return tag
    result = parse_tag(value, allow_folder)
    result['mode'] = 'classic'
    return result


def normalize_udp_tags(rows, payload_bytes):
    if not isinstance(rows, list) or len(rows) > 5000: raise ValueError('Catalogo UDP: massimo 5000 campi')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Campo UDP non valido')
        nid = row.get('nodeid')
        if not nid:
            offset = integer(row.get('offset'), 'Offset UDP', 0, 4089)
            kind = str(row.get('data_type', 'REAL')).upper()
            bit = '.'+str(integer(row.get('bit',0),'Bit UDP',0,7)) if kind == 'BOOL' else ''
            nid = f's7:udp:{offset}{bit}:{kind}'
        tag = parse_node(nid)
        if tag['mode'] != 'udp': raise ValueError('Il catalogo UDP accetta solo campi s7:udp:')
        if tag['offset']+tag['size'] > payload_bytes: raise ValueError('Campo oltre la lunghezza configurata del telegramma: '+nid)
        if tag['nodeid'] in seen: raise ValueError('Campo UDP duplicato: '+nid)
        seen.add(tag['nodeid'])
        name = str(row.get('display_name') or row.get('name') or tag['nodeid']).strip()
        if not name or len(name)>160: raise ValueError('Nome UDP: da 1 a 160 caratteri')
        result.append(dict(nodeid=tag['nodeid'],display_name=name,data_type=tag['data_type']))
    return result


def value_row(nid, kind, value=None, error='', quality='Good', status=None, local_ts=None, source_ts=None, received_ms=None):
    numeric = isinstance(value, (int,float,bool)) and math.isfinite(value)
    exact = isinstance(value, int) and not isinstance(value,bool) and abs(value)>9007199254740991
    from primary_format import typed
    return dict(nodeid=nid, _source_typed=typed(value),value=str(value) if exact else value,
                plot_value=float(value) if numeric and quality != 'Bad' else None, numeric=numeric,
                good=quality=='Good', quality=quality, status=status or ('Good' if not error else error),
                status_code=None, error=error, data_type=kind, source_ts=source_ts,
                server_ts=None, local_ts=local_ts, received_ms=received_ms,
                precision_warning='Intero 64 bit conservato come testo esatto; grafico float64' if exact else '')


class SymbolTree:
    """Flat resolved leaves -> lazy UI branches; paths are never shortened as IDs."""
    def __init__(self, items):
        self.catalog = {r['nodeid']:r for r in items}
        self.children = {'':{}}
        for item in items:
            path = item['path']; parts = path.split('.')
            parent = ''
            for i,part in enumerate(parts):
                name = '.'.join(parts[:i+1]); leaf = i==len(parts)-1
                entry = dict(item) if leaf else dict(nodeid='s7:sym:/'+name, display_name=part,
                    node_class='Object', has_children=True, path=name)
                self.children.setdefault(parent,{})[entry['nodeid']] = entry
                if not leaf: self.children.setdefault(name,{})
                parent = name

    def browse(self, folder):
        return list(self.children.get(folder,{}).values())

    def scan(self, node):
        path = node.get('name','')
        if not node.get('folder'): return [self.catalog[node['nodeid']]]
        return [r for r in self.catalog.values() if not path or r['path'].startswith(path+'.')]
