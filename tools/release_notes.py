"""Modifiche di ogni versione per la pagina della release e per il README dello ZIP.

    python tools/release_notes.py changes X.Y.Z   # elenco delle modifiche (Markdown)
    python tools/release_notes.py notes X.Y.Z [file]   # testo della release: modifiche + guida di avvio
    python tools/release_notes.py readme X.Y.Z    # README.txt dello ZIP: modifiche + note generali

Le modifiche vengono dalla sezione "## X.Y.Z" di CHANGELOG.md. Se la sezione manca (versione
assegnata dalla pipeline senza aggiornare il CHANGELOG) si usano i titoli dei commit dall'ultima
release, così ogni pubblicazione elenca sempre quello che è cambiato.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import versioning  # noqa: E402


def section(version, cwd=ROOT):
    """Testo della sezione "## version" del CHANGELOG, None se assente o vuota."""
    text = (Path(cwd) / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(r'^## ' + re.escape(version) + r'[ \t]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    body = match.group(1).strip() if match else ''
    return body or None


def from_commits(version, cwd=ROOT):
    """Titoli dei commit dall'ultimo tag precedente a version (esclusi merge)."""
    target = versioning.parse(version)
    previous = [name for parsed, name in versioning.tags(cwd) if parsed < target]
    span = f'{previous[-1]}..HEAD' if previous else 'HEAD'
    subjects = versioning.git('log', span, '--no-merges', '--format=%s', cwd=cwd).splitlines()
    lines = [s.strip() for s in subjects if s.strip() and not s.lower().startswith('merge ')]
    return '\n'.join('- ' + s for s in dict.fromkeys(lines)) or None


def changes(version, cwd=ROOT):
    version = versioning.fmt(versioning.parse(version))
    body = section(version, cwd)
    if body:
        return body
    commits = from_commits(version, cwd)
    if commits:
        return '_Sezione assente nel CHANGELOG: elenco ricavato dai commit._\n\n' + commits
    return '- Nessuna modifica registrata.'


def notes(version, cwd=ROOT):
    guide = (Path(cwd) / 'LEGGIMI_AVVIO_WINDOWS.md').read_text(encoding='utf-8')
    return f'# Modifiche della versione {version}\n\n{changes(version, cwd)}\n\n---\n\n{guide}'


def plain(markdown):
    """Markdown essenziale in testo leggibile nel Blocco note."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', markdown)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return text.replace('_Sezione assente', 'Sezione assente').replace('commit._', 'commit.')


def readme(version, cwd=ROOT):
    general = (Path(cwd) / 'source' / 'README.txt').read_text(encoding='utf-8')
    title = f'SPACE FIELDBUS ANALYZER V{version} WINDOWS x64'
    return (f'{title}\n{"=" * len(title)}\n\nMODIFICHE DI QUESTA VERSIONE\n\n{plain(changes(version, cwd))}\n\n'
            f'Elenco completo delle versioni: CHANGELOG.md nel repository.\n\n{"-" * len(title)}\n\n{general}')


def main(argv):
    commands = {'changes': changes, 'notes': notes, 'readme': readme}
    if len(argv) not in (3, 4) or argv[1] not in commands:
        print(__doc__, file=sys.stderr)
        return 2
    text = commands[argv[1]](argv[2])
    if len(argv) == 4:
        Path(argv[3]).write_text(text + '\n', encoding='utf-8')
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
