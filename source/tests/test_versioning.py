"""Prove del versionamento automatico (tools/versioning.py). Servono solo Python e git."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('versioning', REPO / 'tools' / 'versioning.py')
versioning = importlib.util.module_from_spec(spec); spec.loader.exec_module(versioning)

FILES = {
    'source/app.py': 'APP_VERSION = "0.0.1"\n',
    'source/build_windows.py': "VERSION = '0.0.1'\n",
    'source/launcher/launcher_windows.go': 'package main\nconst version = "0.0.1"\n',
    'tools/windows/smoke_exe.py': "build={'version':'0.0.1'}\n",
}


class Versioning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        for relative, text in FILES.items():
            path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        (self.root / 'VERSION').write_text('2.0.0\n')
        self.git('init', '-q'); self.git('config', 'user.email', 't@t'); self.git('config', 'user.name', 't')
        self.commit('chore: base')

    def tearDown(self): self.tmp.cleanup()

    def git(self, *args): return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def commit(self, message):
        (self.root / 'log.txt').write_text((self.root / 'log.txt').read_text() + message + '\n' if (self.root / 'log.txt').exists() else message + '\n')
        self.git('add', '-A'); self.git('commit', '-q', '-m', message)

    def next(self): return versioning.fmt(versioning.next_version(self.root))

    def test_first_release_uses_version_file(self):
        self.assertEqual(self.next(), '2.0.0')

    def test_fix_bumps_patch(self):
        self.git('tag', 'v2.0.0'); self.commit('fix: coda ADS')
        self.assertEqual(self.next(), '2.0.1')

    def test_untyped_message_bumps_patch(self):
        self.git('tag', 'v2.0.0'); self.commit('Aggiornata la guida')
        self.assertEqual(self.next(), '2.0.1')

    def test_feat_bumps_minor_and_resets_patch(self):
        self.git('tag', 'v2.0.3'); self.commit('fix: x'); self.commit('feat: nuovo formato')
        self.assertEqual(self.next(), '2.1.0')

    def test_breaking_change_bumps_major(self):
        self.git('tag', 'v2.4.1'); self.commit('feat!: nuovo formato di configurazione')
        self.assertEqual(self.next(), '3.0.0')
        self.git('tag', 'v3.0.0'); self.commit('refactor: x\n\nBREAKING CHANGE: API rimossa')
        self.assertEqual(self.next(), '4.0.0')

    def test_rerun_on_tagged_commit_is_idempotent(self):
        self.git('tag', 'v2.0.0'); self.commit('fix: x'); self.git('tag', 'v2.0.1')
        self.assertEqual(self.next(), '2.0.1')

    def test_version_file_can_force_a_jump(self):
        self.git('tag', 'v2.3.4'); (self.root / 'VERSION').write_text('3.0.0\n'); self.commit('chore: 3.0')
        self.assertEqual(self.next(), '3.0.0')

    def test_non_semver_tags_are_ignored(self):
        self.git('tag', 'v2.0.0'); self.git('tag', 'vecchio'); self.commit('fix: x')
        self.assertEqual(self.next(), '2.0.1')

    def test_apply_updates_every_build_file(self):
        versioning.apply('2.7.3', self.root)
        self.assertEqual(set(versioning.current(self.root).values()), {'2.7.3'})
        with self.assertRaises(ValueError): versioning.apply('2.7', self.root)

    def test_apply_refuses_an_ambiguous_file(self):
        (self.root / 'source/app.py').write_text('APP_VERSION = "1.0.0"\nAPP_VERSION = "1.0.0"\n')
        with self.assertRaises(RuntimeError): versioning.apply('2.0.0', self.root)

    def test_repository_build_files_are_aligned(self):
        values = versioning.current(REPO)
        self.assertNotIn(None, values.values()); self.assertEqual(len(set(values.values())), 1, values)


if __name__ == '__main__':
    unittest.main()
