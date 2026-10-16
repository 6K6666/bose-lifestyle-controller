"""tools/set_identity.py: sets the repo-local git identity only (never --global or --system). Temp repos and a temp HOME."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / 'tools' / 'set_identity.py'
NAME = 'Release Owner'
EMAIL = 'owner' + '@' + 'synthetic' + '.test'
MACHINE = 'owner' + '@' + 'machine' + '.local'
NOREPLY = 'noreply' + '@' + 'github' + '.com'


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.repo = self.tmp / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.tmp / 'xdg'), GIT_CONFIG_NOSYSTEM='1')

    def global_state(self):
        proc = subprocess.run(['git', 'config', '--global', '--list'], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout

    def local(self, key):
        proc = subprocess.run(['git', '-C', str(self.repo), 'config', '--local', '--get', key], capture_output=True, text=True)
        return proc.returncode, proc.stdout.strip()

    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-B', str(TOOL), '--repo', str(self.repo), *args],
                              env=self.env, capture_output=True, text=True)

    def test_sets_repo_local_identity_and_leaves_global_config_alone(self):
        before = self.global_state()
        proc = self.run_tool('--name', NAME, '--email', EMAIL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.local('user.name'), (0, NAME))
        self.assertEqual(self.local('user.email'), (0, EMAIL))
        self.assertIn(EMAIL, proc.stdout)
        self.assertEqual(self.global_state(), before)
        self.assertFalse((self.home / '.gitconfig').exists())
        self.assertFalse((self.tmp / 'xdg' / 'git' / 'config').exists())

    def test_refuses_invalid_values_and_writes_nothing(self):
        bad = [('', EMAIL), ('   ', EMAIL), ('Bad\x07Name', EMAIL), ('x' * 101, EMAIL), ('<Owner>', EMAIL),
               (NAME, 'no-at-sign'), (NAME, 'a b' + '@' + 'c.test'), (NAME, 'x' + '@' + 'y'), (NAME, EMAIL + '\n'),
               (NAME, MACHINE), (NAME, NOREPLY)]
        before = self.global_state()
        for name, email in bad:
            with self.subTest(name=name, email=email):
                proc = self.run_tool('--name', name, '--email', email)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(self.local('user.name')[0], 1)
                self.assertEqual(self.local('user.email')[0], 1)
        self.assertEqual(self.global_state(), before)

    def test_machine_default_allowed_only_with_explicit_flag(self):
        proc = self.run_tool('--name', NAME, '--email', NOREPLY, '--allow-noreply')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.local('user.email'), (0, NOREPLY))

    def test_refuses_a_directory_that_is_not_a_repository(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        proc = subprocess.run([sys.executable, '-B', str(TOOL), '--repo', str(plain), '--name', NAME, '--email', EMAIL],
                              env=self.env, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((plain / '.git').exists())


if __name__ == '__main__':
    unittest.main(verbosity=1)
