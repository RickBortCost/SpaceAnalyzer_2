"""Versionamento automatico di Space Fieldbus Analyzer (Semantic Versioning, tag Git vX.Y.Z).

    python tools/versioning.py next          # prossima versione da tag e messaggi di commit
    python tools/versioning.py apply X.Y.Z   # scrive la versione nei file di build
    python tools/versioning.py check         # verifica che i file coincidano con VERSION

Regole per `next`:
- il commit corrente ha già un tag vX.Y.Z  -> quella versione (riesecuzione idempotente);
- nessun tag, oppure VERSION maggiore dell'ultimo tag -> VERSION (salto manuale, es. 3.0.0);
- altrimenti, dai messaggi dei commit dopo l'ultimo tag:
  "BREAKING CHANGE", "tipo!:" o "major:" -> MAJOR; "feat:" o "minor:" -> MINOR; tutto il resto -> PATCH.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
# File, espressione (con un gruppo per la versione) e numero di occorrenze attese.
TARGETS = [
    ('source/app/SpaceAnalyzer/Config/Defaults.cs', r'(public const string Version = ")([^"]+)(")', 1),
    ('source/app/SpaceAnalyzer/SpaceAnalyzer.csproj', r'(<Version>)([^<]+)(</Version>)', 1),
    ('source/engine/SpaceAnalyzer.Engine/SpaceAnalyzer.Engine.csproj', r'(<Version>)([^<]+)(</Version>)', 1),
    ('tools/windows/build_windows.py', r"^(VERSION = ')([^']+)(')", 1),
    ('source/launcher/launcher_windows.go', r'^(const version = ")([^"]+)(")', 1),
    ('tools/windows/smoke_exe.py', r"(\{'version':')([^']+)('\})", 1),
]
MAJOR = re.compile(r'BREAKING[ -]CHANGE|^[a-zA-Z]+(\([^)]*\))?!:|^major:', re.M)
MINOR = re.compile(r'^(feat(\([^)]*\))?|minor):', re.M | re.I)


def parse(text):
    match = SEMVER.match(text.strip())
    if not match:
        raise ValueError(f'Versione non valida: {text!r} (atteso X.Y.Z)')
    return tuple(int(part) for part in match.groups())


def fmt(version):
    return '.'.join(map(str, version))


def git(*args, cwd=ROOT):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def tags(cwd=ROOT):
    found = []
    for name in git('tag', '--list', 'v*', cwd=cwd).split():
        try:
            found.append((parse(name[1:]), name))
        except ValueError:
            pass
    return sorted(found)


def bump(version, messages):
    major, minor, patch = version
    if MAJOR.search(messages):
        return (major + 1, 0, 0)
    if MINOR.search(messages):
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def next_version(cwd=ROOT):
    base = parse((Path(cwd) / 'VERSION').read_text(encoding='utf-8'))
    head = [n for n in git('tag', '--points-at', 'HEAD', '--list', 'v*', cwd=cwd).split() if SEMVER.match(n[1:])]
    if head:
        return max(parse(n[1:]) for n in head)
    existing = tags(cwd)
    if not existing or base > existing[-1][0]:
        return base
    latest, name = existing[-1]
    messages = git('log', f'{name}..HEAD', '--format=%B', cwd=cwd)
    return bump(latest, messages) if messages.strip() else latest


def apply(version, cwd=ROOT):
    text = fmt(parse(version) if isinstance(version, str) else version)
    for relative, pattern, count in TARGETS:
        path = Path(cwd) / relative
        source = path.read_text(encoding='utf-8')
        updated, n = re.subn(pattern, lambda m: m.group(1) + text + m.group(3), source, flags=re.M)
        if n != count:
            raise RuntimeError(f'{relative}: attese {count} occorrenze della versione, trovate {n}')
        if updated != source:
            path.write_text(updated, encoding='utf-8', newline='')
    return text


def current(cwd=ROOT):
    values = {}
    for relative, pattern, _ in TARGETS:
        match = re.search(pattern, (Path(cwd) / relative).read_text(encoding='utf-8'), re.M)
        values[relative] = match.group(2) if match else None
    return values


def main(argv):
    if len(argv) >= 2 and argv[1] == 'next':
        print(fmt(next_version()))
    elif len(argv) == 3 and argv[1] == 'apply':
        print(apply(argv[2]))
    elif len(argv) == 2 and argv[1] == 'check':
        expected = fmt(parse((ROOT / 'VERSION').read_text(encoding='utf-8')))
        wrong = {k: v for k, v in current().items() if v != expected}
        if wrong:
            print(f'Versioni diverse da VERSION ({expected}): {wrong}', file=sys.stderr)
            return 1
        print(expected)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
