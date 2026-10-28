"""PostgreSQL di prova per test_postgres_ui.cjs: cluster temporaneo (initdb + pg_ctl) con utente e password, dati deterministici.

Binari: variabile PGBIN (runner Windows di GitHub), /usr/lib/postgresql/<versione>/bin o pg_ctl nel PATH.
Tabelle:
- plant.line_data: una riga al secondo per un'ora da T0, colonne temperature (double), pressure (real), speed (integer), running (boolean), note (text);
- plant.hot: vista sulle righe con temperature > 22;
- public.samples: formato "lungo" (time timestamp senza fuso, sensor text, value double), sensori A e B ogni 10 s;
- public.config: nessuna colonna di tempo, non deve comparire.
"""
import glob
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

USER, PASSWORD, DATABASE = 'tester', 'test-password', 'plant'
T0 = '2026-10-20 08:00:00'

SQL = f"""
CREATE SCHEMA plant;
CREATE TABLE plant.line_data(ts timestamptz PRIMARY KEY, temperature double precision, pressure real, speed integer, running boolean, note text);
INSERT INTO plant.line_data SELECT timestamptz '{T0}+00' + i * interval '1 second', 20 + 5 * sin(i / 60.0), 100 + (i % 10), i, (i / 600) % 2 = 0, 'ok'
  FROM generate_series(0, 3599) AS i;
CREATE VIEW plant.hot AS SELECT ts, temperature FROM plant.line_data WHERE temperature > 22;
CREATE TABLE public.samples(time timestamp, sensor text, value double precision);
INSERT INTO public.samples SELECT timestamp '{T0}' + i * interval '10 second', s, CASE WHEN s = 'A' THEN i ELSE -i END
  FROM generate_series(0, 359) AS i, (VALUES ('A'), ('B')) AS v(s);
CREATE INDEX ON public.samples(time);
CREATE TABLE public.config(id integer, name text);
"""


def binaries():
    candidates = [os.environ.get('PGBIN', '')] + sorted(glob.glob('/usr/lib/postgresql/*/bin'), reverse=True)
    which = shutil.which('pg_ctl')
    if which:
        candidates.append(str(Path(which).parent))
    for folder in candidates:
        if folder and (Path(folder) / ('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl')).is_file():
            return Path(folder)
    raise RuntimeError('PostgreSQL non trovato: impostare PGBIN')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Peer:
    def __init__(self):
        self.bin = binaries()
        self.root = Path(tempfile.mkdtemp(prefix='space-pg-'))
        self.port = free_port()
        # PostgreSQL non si avvia come root: su Linux il cluster appartiene all'utente postgres.
        self.prefix = []
        if os.name != 'nt' and os.geteuid() == 0:
            self.prefix = ['runuser', '-u', 'postgres', '--']
            os.chmod(self.root, 0o777)
        data, pwfile = self.root / 'data', self.root / 'pw.txt'
        pwfile.write_text(PASSWORD)
        os.chmod(pwfile, 0o644)
        self.run('initdb', '-D', str(data), '-U', USER, '--auth=scram-sha-256', '--pwfile=' + str(pwfile), '-E', 'UTF8', '--locale=C')
        options = f'-p {self.port} -c listen_addresses=127.0.0.1'
        if os.name != 'nt':
            options += ' -k ' + str(self.root)
        self.run('pg_ctl', '-D', str(data), '-o', options, '-l', str(self.root / 'server.log'), '-w', 'start')
        self.data = data
        self.psql('postgres', f'CREATE DATABASE {DATABASE}')
        self.psql(DATABASE, SQL)

    def run(self, tool, *args):
        exe = str(self.bin / (tool + ('.exe' if os.name == 'nt' else '')))
        env = dict(os.environ, PGPASSWORD=PASSWORD)
        result = subprocess.run(self.prefix + [exe, *args], capture_output=True, text=True, env=env, timeout=120)
        if result.returncode:
            raise RuntimeError(f'{tool} non riuscito: {result.stdout}{result.stderr}')
        return result.stdout

    def psql(self, database, sql):
        return self.run('psql', '-h', '127.0.0.1', '-p', str(self.port), '-U', USER, '-d', database, '-v', 'ON_ERROR_STOP=1', '-q', '-c', sql)

    def close(self):
        try:
            self.run('pg_ctl', '-D', str(self.data), '-m', 'fast', '-w', 'stop')
        finally:
            shutil.rmtree(self.root, ignore_errors=True)


if __name__ == '__main__':
    peer = Peer()
    print('porta', peer.port)
    print(peer.psql(DATABASE, 'SELECT count(*) FROM plant.line_data'))
    peer.close()
