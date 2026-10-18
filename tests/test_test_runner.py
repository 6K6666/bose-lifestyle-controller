"""Exercise the shell runner's dependency failure and complete-suite inventory."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RunnerTests(unittest.TestCase):
    def run_fixture(self, *, node):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'tools').mkdir()
            (root / 'tests').mkdir()
            (root / 'bin').mkdir()
            source = (ROOT / 'run_tests.sh').read_text()
            source = re.sub(r'^STDLIB_SUITES="[^"]*"', 'STDLIB_SUITES="test_example.py"', source, flags=re.M)
            (root / 'run_tests.sh').write_text(source)
            shutil.copyfile(ROOT / 'tools/results_record.py', root / 'tools/results_record.py')
            (root / 'tests/test_example.py').write_text('')
            (root / 'tests/test_bose_card.js').write_text('')
            for command in ('git', 'date', 'dirname'):
                (root / 'bin' / command).symlink_to(shutil.which(command))
            (root / 'bin/python3').symlink_to(sys.executable)
            if node:
                executable = root / 'bin/node'
                executable.write_text('#!/bin/sh\nprintf executed > card-ran\n')
                executable.chmod(0o755)
            for args in (['init', '-q'], ['add', '.'],
                         ['-c', 'user.name=Runner Test', '-c', 'user.email=' + '@'.join(('runner', 'example.invalid')),
                          '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture']):
                subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)
            results = root / 'build/test-results.json'
            results.parent.mkdir()
            results.write_text('{"stale": true}')
            env = {**os.environ, 'PATH': str(root / 'bin')}
            run = subprocess.run(['/bin/sh', 'run_tests.sh', '--stdlib-only'], cwd=root,
                                 env=env, text=True, capture_output=True, timeout=30)
            record = json.loads(results.read_text()) if results.exists() else None
            return run, record, (root / 'card-ran').exists()

    def test_missing_node_fails_and_clears_stale_results(self):
        run, record, card_ran = self.run_fixture(node=False)
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('node', run.stdout + run.stderr)
        self.assertIsNone(record)
        self.assertFalse(card_ran)

    def test_available_node_runs_and_records_card_suite(self):
        run, record, card_ran = self.run_fixture(node=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(card_ran)
        self.assertEqual(record['planned'], 2)
        self.assertEqual([suite['name'] for suite in record['suites']],
                         ['test_example.py', 'test_bose_card.js'])

    def test_every_public_suite_is_in_runner_inventory(self):
        source = (ROOT / 'run_tests.sh').read_text()
        listed = set()
        for name in ('STDLIB_SUITES', 'CARD_SUITE', 'VENV_SUITES'):
            listed.update(re.search(r'^' + name + r'="([^"]*)"', source, re.M)[1].split())
        actual = {path.name for path in (ROOT / 'tests').glob('test_*')
                  if path.suffix in ('.py', '.js')}
        self.assertEqual(listed, actual)


if __name__ == '__main__':
    unittest.main()
