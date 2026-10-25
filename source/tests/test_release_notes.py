"""Prove delle note di rilascio (tools/release_notes.py): ogni pubblicazione elenca le modifiche della versione."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools'))
spec = importlib.util.spec_from_file_location('release_notes', REPO / 'tools' / 'release_notes.py')
release_notes = importlib.util.module_from_spec(spec); spec.loader.exec_module(release_notes)

CHANGELOG = '# Changelog\n\n## 1.1.0\n\n- **Nuova funzione** con `codice`.\n- Correzione.\n\n## 1.0.0\n\n- Prima versione.\n'


class ReleaseNotes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        (self.root / 'CHANGELOG.md').write_text(CHANGELOG, encoding='utf-8')
        (self.root / 'LEGGIMI_AVVIO_WINDOWS.md').write_text('# Avvio\n\nGuida.\n', encoding='utf-8')
        (self.root / 'source').mkdir(); (self.root / 'source' / 'README.txt').write_text('Note generali.\n', encoding='utf-8')
        for args in (['init', '-q'], ['config', 'user.email', 't@t'], ['config', 'user.name', 't']): self.git(*args)
        self.commit('chore: base'); self.git('tag', 'v1.0.0')

    def tearDown(self): self.tmp.cleanup()

    def git(self, *args): return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def commit(self, message):
        path = self.root / 'log.txt'; path.write_text((path.read_text() if path.exists() else '') + message + '\n')
        self.git('add', '-A'); self.git('commit', '-q', '-m', message)

    def test_section_of_the_version(self):
        self.assertEqual(release_notes.changes('1.1.0', self.root), '- **Nuova funzione** con `codice`.\n- Correzione.')
        self.assertEqual(release_notes.changes('1.0.0', self.root), '- Prima versione.')

    def test_release_page_starts_with_the_changes_then_the_guide(self):
        text = release_notes.notes('1.1.0', self.root)
        self.assertTrue(text.startswith('# Modifiche della versione 1.1.0\n\n- **Nuova funzione**'))
        self.assertIn('# Avvio', text)

    def test_missing_section_lists_the_commits_since_the_previous_release(self):
        self.commit('fix: corretto il salvataggio'); self.commit('feat: nuovo grafico')
        text = release_notes.changes('1.2.0', self.root)
        self.assertIn('ricavato dai commit', text)
        self.assertIn('- feat: nuovo grafico', text); self.assertIn('- fix: corretto il salvataggio', text)
        self.assertNotIn('chore: base', text)

    def test_zip_readme_is_plain_text(self):
        text = release_notes.readme('1.1.0', self.root)
        self.assertTrue(text.startswith('SPACE FIELDBUS ANALYZER V1.1.0 WINDOWS x64'))
        self.assertIn('MODIFICHE DI QUESTA VERSIONE\n\n- Nuova funzione con codice.', text)
        self.assertIn('Note generali.', text)

    def test_current_changelog_documents_the_current_version(self):
        version = (REPO / 'VERSION').read_text(encoding='utf-8').strip()
        self.assertIsNotNone(release_notes.section(version), f'CHANGELOG.md senza la sezione "## {version}"')


if __name__ == '__main__':
    unittest.main()
