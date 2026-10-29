"""Guida illustrata: il PDF della versione minore corrente deve esistere ed essere completo (tools/guide/build_guide.py)."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSION = (REPO / 'VERSION').read_text(encoding='utf-8').strip()
MINOR = '.'.join(VERSION.split('.')[:2])
PDF = REPO / 'docs/guida' / f'Space_Fieldbus_Analyzer_{MINOR}_Guida_Guide_IT_EN.pdf'
FIGURES = REPO / 'tools/guide/figures'


class Guida(unittest.TestCase):
    def test_pdf_della_versione_minore_presente(self):
        self.assertTrue(PDF.is_file(), f'{PDF.name} mancante: rigenerare la guida a ogni versione minore (python tools/guide/build_guide.py)')
        data = PDF.read_bytes()
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertGreater(len(data), 500_000)

    def test_pdf_ha_entrambe_le_edizioni(self):
        pages = len(re.findall(rb'/Type\s*/Page[^s]', PDF.read_bytes()))
        self.assertGreaterEqual(pages, 60, 'il PDF deve contenere l\'edizione italiana e quella inglese')

    def test_figure_citate_esistono(self):
        for lang in ('it', 'en'):
            for md in sorted((REPO / 'tools/guide/content' / lang).glob('*.md')):
                for figure in re.findall(r'^!\[([^\]]+)\]', md.read_text(encoding='utf-8'), re.M):
                    self.assertTrue((FIGURES / f'{figure}.jpg').is_file(), f'{md.name}: figura {figure} mancante')

    def test_edizioni_con_gli_stessi_capitoli_e_figure(self):
        def outline(lang):
            files = sorted((REPO / 'tools/guide/content' / lang).glob('[0-9][0-9]_*.md'))
            return [re.findall(r'^!\[([^\]]+)\]', f.read_text(encoding='utf-8'), re.M) for f in files]
        self.assertEqual(outline('it'), outline('en'))

    def test_regola_nel_claude_md(self):
        self.assertIn('build_guide.py', (REPO / 'CLAUDE.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
