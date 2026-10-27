"""Deterministic local InfluxDB 2.x HTTP peer for the UI tests (test tooling only, not part of the product).

Speaks the parts of the 2.x API that the application uses: GET /health, GET /api/v2/buckets, GET /api/v2/orgs and
POST /api/v2/query (JSON body, annotated CSV answer, RFC3339Nano). The Flux text is not executed: the few shapes the
application sends are recognised and answered from a fixed data set, and string literals are unescaped as strict as Flux
(an unknown escape such as \\$ is an error), so quoting mistakes show up as failures.
"""
import json
import math
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = 'test-token'
ORG_ID = '0a1b2c3d4e5f6071'
ORG_NAME = 'danieli'
T0 = int(datetime(2026, 10, 20, 8, 0, 0, tzinfo=timezone.utc).timestamp())   # first sample (1 Hz, one hour)
COUNT = 3600
NOW = T0 + 2 * 3600                                                           # the server's "now": relative ranges start here
TRICKY = 'Linea "A" ${x} \\ fine'

BUCKETS = [
    {'id': 'b1', 'orgID': ORG_ID, 'type': 'user', 'name': 'plant', 'retentionRules': [{'type': 'expire', 'everySeconds': 0}]},
    {'id': 'b2', 'orgID': ORG_ID, 'type': 'system', 'name': '_monitoring', 'retentionRules': [{'type': 'expire', 'everySeconds': 604800}]},
    {'id': 'b3', 'orgID': ORG_ID, 'type': 'system', 'name': '_tasks', 'retentionRules': [{'type': 'expire', 'everySeconds': 259200}]},
]


def series_table():
    return [
        dict(bucket='plant', m='pressure', f='value', tags={'line': '1'}, type='double', gen=lambda i: 100 + 10 * math.sin(i / 30)),
        dict(bucket='plant', m='pressure', f='value', tags={'line': '2'}, type='double', gen=lambda i: 80 + 5 * math.cos(i / 45)),
        dict(bucket='plant', m='temp', f='alarm', tags={}, type='boolean', gen=lambda i: (i // 600) % 2 == 1),
        dict(bucket='plant', m='counter', f='count', tags={'site': TRICKY}, type='long', gen=lambda i: 1000 + i),
        dict(bucket='plant', m='state', f='mode', tags={}, type='string', gen=lambda i: 'run' if i % 2 else 'stop'),
    ]


SERIES = series_table()


def rfc3339(seconds):
    whole = int(seconds)
    frac = seconds - whole
    text = datetime.fromtimestamp(whole, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    if frac:
        text += ('%.9f' % frac)[1:].rstrip('0')
    return text + 'Z'


def parse_time(value):
    value = value.rstrip('Z')
    base, _, frac = value.partition('.')
    t = datetime.strptime(base, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp()
    return t + (float('0.' + frac) if frac else 0.0)


def unescape(literal):
    """The body of a Flux string literal, strictly."""
    out, i = [], 0
    while i < len(literal):
        c = literal[i]
        if c == '\\':
            i += 1
            nxt = literal[i:i + 1]
            if nxt == '$' and literal[i:i + 2] == '${':
                out.append('${'); i += 2; continue
            table = {'\\': '\\', '"': '"', 'n': '\n', 'r': '\r', 't': '\t'}
            if nxt not in table:
                raise ValueError('invalid escape sequence \\' + nxt)
            out.append(table[nxt]); i += 1
            continue
        if c == '$' and literal[i:i + 2] == '${':
            raise ValueError('string interpolation is not allowed here')
        out.append(c); i += 1
    return ''.join(out)


STR = r'"((?:[^"\\]|\\.)*)"'


def csv_value(v, kind):
    if kind == 'boolean':
        return 'true' if v else 'false'
    if kind in ('long', 'unsignedLong'):
        return str(int(v))
    if kind == 'string':
        return '"' + str(v).replace('"', '""') + '"' if (',' in str(v) or '"' in str(v)) else str(v)
    return repr(float(v))


def quote(text):
    return '"' + text.replace('"', '""') + '"' if any(ch in text for ch in ',"\n') else text


def block(columns, kinds, rows, group_flags=None):
    group_flags = group_flags or ['false'] * len(columns)
    lines = ['#group,false,false,' + ','.join(group_flags),
             '#datatype,string,long,' + ','.join(kinds),
             '#default,_result,,' + ','.join([''] * len(columns)),
             ',result,table,' + ','.join(columns)]
    for t, row in rows:
        lines.append(',,%d,%s' % (t, ','.join(row)))
    return '\r\n'.join(lines) + '\r\n\r\n'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send(self, status, body, ctype='application/json'):
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def error(self, status, code, message):
        self.send(status, json.dumps({'code': code, 'message': message}))

    def authorized(self):
        return self.headers.get('Authorization') == 'Token ' + self.server.token

    def do_GET(self):
        path = urlparse(self.path)
        self.server.requests.append(('GET', path.path))
        if path.path == '/health':
            return self.send(200, json.dumps({'name': 'influxdb', 'message': 'ready for queries and writes', 'status': 'pass', 'checks': [], 'version': 'v2.7.12', 'commit': 'test'}))
        if not self.authorized():
            return self.error(401, 'unauthorized', 'unauthorized access')
        if path.path == '/api/v2/buckets':
            offset = int(parse_qs(path.query).get('offset', ['0'])[0])
            return self.send(200, json.dumps({'buckets': BUCKETS if offset == 0 else []}))
        if path.path == '/api/v2/orgs':
            if self.server.hide_orgs:
                return self.error(403, 'forbidden', 'read:orgs is unauthorized')
            return self.send(200, json.dumps({'orgs': [{'id': ORG_ID, 'name': ORG_NAME}]}))
        self.error(404, 'not found', 'path not found')

    def do_POST(self):
        path = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.requests.append(('POST', path.path))
        if not self.authorized():
            return self.error(401, 'unauthorized', 'unauthorized access')
        if path.path != '/api/v2/query':
            return self.error(404, 'not found', 'path not found')
        if parse_qs(path.query).get('orgID', [''])[0] != ORG_ID:
            return self.error(400, 'invalid', 'organization not found')
        request = json.loads(body)
        self.server.queries.append(request['query'])
        try:
            csv = self.run_flux(request['query'])
        except KeyError as exc:
            return self.error(404, 'not found', 'bucket %s not found' % exc)
        except ValueError as exc:
            return self.error(400, 'invalid', 'compilation failed: ' + str(exc))
        self.send(200, csv, 'text/csv; charset=utf-8')

    # -- Flux shapes ---------------------------------------------------------------------------------------------------
    def run_flux(self, flux):
        bucket = unescape(re.search(r'from\(bucket: ' + STR + r'\)', flux).group(1))
        if bucket not in {b['name'] for b in BUCKETS}:
            raise KeyError('"%s"' % bucket)
        if 'range(start: -' in flux:
            start = NOW - int(re.search(r'start: -(\d+)s', flux).group(1)); stop = NOW
        else:
            m = re.search(r'range\(start: ([^,]+), stop: ([^)]+)\)', flux)
            start, stop = parse_time(m.group(1)), parse_time(m.group(2))
        pool = [s for s in SERIES if s['bucket'] == bucket]
        if '|> last()' in flux:
            return self.catalog(pool, start, stop)
        m = re.search(r'r\._measurement == ' + STR + r' and r\._field == ' + STR, flux)
        measurement, field = unescape(m.group(1)), unescape(m.group(2))
        tags = {unescape(a): unescape(b) for a, b in re.findall(r'and r\[' + STR + r'\] == ' + STR, flux)}
        match = [s for s in pool if s['m'] == measurement and s['f'] == field and all(s['tags'].get(k) == v for k, v in tags.items())]
        if not match:
            return ''
        s = match[0]
        points = [(T0 + i, s['gen'](i)) for i in range(COUNT) if start <= T0 + i < stop]
        if '|> count()' in flux:
            return block(['_value'], ['long'], [(0, [str(len(points))])]) if points else ''
        agg = re.search(r'aggregateWindow\(every: (\d+)s, fn: (\w+), createEmpty: false\)', flux)
        kind = s['type']
        if agg:
            every, fn = int(agg.group(1)), agg.group(2)
            if kind == 'string':
                raise ValueError('mean is not supported for strings')
            if kind == 'boolean' and 'if r._value then 1.0 else 0.0' not in flux:
                raise ValueError('boolean values need a numeric conversion before aggregation')
            if kind == 'boolean':
                points = [(t, 1.0 if v else 0.0) for t, v in points]
            windows = {}
            for t, v in points:
                windows.setdefault((int(t) // every + 1) * every, []).append(float(v))
            fns = {'mean': lambda a: sum(a) / len(a), 'min': min, 'max': max, 'last': lambda a: a[-1], 'first': lambda a: a[0],
                   'sum': sum, 'median': lambda a: sorted(a)[len(a) // 2]}
            rows = [(0, [rfc3339(min(stop_t, stop)), repr(float(fns[fn](vals)))]) for stop_t, vals in sorted(windows.items())]
            return block(['_time', '_value'], ['dateTime:RFC3339Nano', 'double'], rows)
        rows = [(0, [rfc3339(t), csv_value(v, kind)]) for t, v in points]
        return block(['_time', '_value'], ['dateTime:RFC3339Nano', kind], rows) if rows else ''

    def catalog(self, pool, start, stop):
        blocks = {}
        for number, s in enumerate(pool):
            points = [i for i in range(COUNT) if start <= T0 + i < stop]
            if not points:
                continue
            i = points[-1]
            cols = ['_time', '_value', '_field', '_measurement'] + sorted(s['tags'])
            kinds = ['dateTime:RFC3339Nano', s['type'], 'string', 'string'] + ['string'] * len(s['tags'])
            row = [rfc3339(T0 + i), csv_value(s['gen'](i), s['type']), s['f'], s['m']] + [quote(s['tags'][k]) for k in sorted(s['tags'])]
            blocks.setdefault((tuple(cols), tuple(kinds)), []).append((number, row))
        out = ''
        for (cols, kinds), rows in blocks.items():
            flags = ['false', 'false', 'true', 'true'] + ['true'] * (len(cols) - 4)
            out += block(list(cols), list(kinds), rows, flags)
        return out


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Peer:
    def __init__(self, token=TOKEN, hide_orgs=False):
        self.httpd = Server(('127.0.0.1', 0), Handler)
        self.httpd.token = token
        self.httpd.hide_orgs = hide_orgs
        self.httpd.requests = []
        self.httpd.queries = []
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def queries(self):
        return self.httpd.queries

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


if __name__ == '__main__':
    peer = Peer()
    print('http://127.0.0.1:%d' % peer.port, 'token', TOKEN, flush=True)
    peer.thread.join()
