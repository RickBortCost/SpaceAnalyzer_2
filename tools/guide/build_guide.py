"""Costruisce il PDF della guida illustrata bilingue (italiano + inglese) in docs/guida/.

Uso:  python tools/guide/build_guide.py [--version X.Y.Z] [--output FILE]
Il testo sta in tools/guide/content/<it|en>/*.md, le schermate in tools/guide/figures/*.jpg (tools/guide/capture.py).
La guida va rigenerata a ogni versione minore (vedi CLAUDE.md): il nome del file contiene solo MAJOR.MINOR.
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import render  # noqa: E402

LABELS = {
    'subtitle': 'Guida illustrata · Illustrated guide',
    'intro': 'Questo documento contiene due edizioni complete / This document contains two complete editions:<br/>'
             'edizione italiana (pagina indicata nell\'indice) e English edition.',
    'index': 'Indice / Contents',
}
EDITIONS = [
    ('it', 'Edizione italiana', HERE / 'content/it', 'Space Fieldbus Analyzer', 'Guida illustrata al funzionamento e agli strumenti',
     'Versione {{version}} · applicazione interamente in C# (.NET 8)'),
    ('en', 'English edition', HERE / 'content/en', 'Space Fieldbus Analyzer', 'Illustrated guide to operation and tools',
     'Version {{version}} · application written entirely in C# (.NET 8)'),
]


def minor_of(version):
    return '.'.join(version.split('.')[:2])


def output_name(version):
    return f'Space_Fieldbus_Analyzer_{minor_of(version)}_Guida_Guide_IT_EN.pdf'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default=(ROOT / 'VERSION').read_text(encoding='utf-8').strip())
    parser.add_argument('--output')
    args = parser.parse_args()
    out = Path(args.output) if args.output else ROOT / 'docs/guida' / output_name(args.version)
    out.parent.mkdir(parents=True, exist_ok=True)
    footer = f'Space Fieldbus Analyzer {args.version} · Guida illustrata / Illustrated guide'
    render.build(out, args.version, EDITIONS, None, LABELS, footer, f'Space Fieldbus Analyzer {args.version} · Guida / Guide')
    print(out, out.stat().st_size, 'byte')


if __name__ == '__main__':
    main()
