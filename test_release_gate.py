"""Release gate (release plan, Task 3). Run: python3 -B test_release_gate.py

Every check is tested against a synthetic release tree: a temporary git repository with the minimal
files the gate reads, so the tests never depend on the real repository. Planted values are assembled
at run time. A planted violation is expected to fail exactly its own check; where one violation
also trips a check that reads the same input (a symlink poisons every check that reads HEAD), the
test lists those checks as allowed.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
import contextlib
import hashlib
import io
import dataclasses

ROOT = Path(__file__).resolve().parent
GATE = ROOT / 'tools' / 'release_gate.py'
sys.path.insert(0, str(ROOT / 'tools'))
import release_gate  # noqa: E402
from build_release import build_release  # noqa: E402
from public_manifest import DISPLAY_NAME  # noqa: E402
from results_record import clear_record, write_record  # noqa: E402

LITERAL = 'Quartz' + 'kite' + '42'
AUTHOR = 'release' + '@' + 'synthetic' + '.test'
DEFAULT_IDENTITY = 'owner' + '@' + 'machine' + '.local'
TOKEN = '[' + '[' + 'PROJECT-URL' + ']' + ']'
TODO_WORD = 'TO' + 'DO'
FULL = ('Unofficial community project. Not affiliated with or endorsed by Bose Corporation. '
        '"Bose" and "Lifestyle" are trademarks of Bose Corporation, used here only to identify compatible products.')
PNG_A = b'\x89PNG\r\n\x1a\n' + b'A' * 16
PNG_B = b'\x89PNG\r\n\x1a\n' + b'B' * 16
REGISTRY = '# Release placeholders\n\n| Placeholder | Where | Who | What |\n|---|---|---|---|\n'
COMP = 'custom_components/bose_lifestyle'
COMP_BRAND = ('icon.png', 'icon@2x.png', 'logo.png', 'logo@2x.png', 'dark_logo.png', 'dark_logo@2x.png')
ALL_RESULTS = {'results-file', 'results-mode', 'results-head', 'results-clean', 'results-suites'}
SYNTH_SUITES = ['test_docs.py', 'test_fake_card.js', 'test_fake_venv.py']
HEAD_READERS = {'neutrality', 'placeholders', 'todo', 'archive-build', 'archive-reproducible',
                'archive-scan', 'archive-members'}


def base_files():
    files = {
        '.gitignore': 'build/\n__pycache__/\n',
        'VERSION': '1.0.0\n',
        'README.md': '# Bose Lifestyle Controller\n\nSynthetic readme.\n\n' + FULL + '\n',
        'CHANGELOG.md': '# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-10-11\n\n- First release.\n',
        'hacs.json': json.dumps({'name': DISPLAY_NAME, 'homeassistant': '2026.10.0'}, indent=2) + '\n',
        f'{COMP}/manifest.json': json.dumps({'domain': 'bose_lifestyle', 'name': DISPLAY_NAME,
                                             'version': '1.0.0'}) + '\n',
        'brand/icon.png': PNG_A,
        'brand/icon@2x.png': PNG_B,
        'docs/RELEASE-PLACEHOLDERS.md': REGISTRY,
        'docs/start-here.md': 'Synthetic page.\n',
        # Test data that must not look like a placeholder: a bare example host and the source of the
        # token pattern itself, as the public test files spell them.
        'docs/fixture-note.md': 'sample host: ' + 'example' + '.invalid\n'
                                 + 'pattern: ' + r'\[\[[^\]]+\]\]' + '\n',
        # Like the real test_docs.py, this lists tracked files with git, so it needs a repository.
        'test_docs.py': 'import subprocess, sys\n'
                        'sys.exit(subprocess.run(["git", "ls-files"], capture_output=True).returncode)\n',
        # The suite lists the gate reads from HEAD (the real script declares the full lists in the same form).
        'run_tests.sh': 'STDLIB_SUITES="test_docs.py"\nCARD_SUITE="test_fake_card.js"\n'
                        'VENV_SUITES="test_fake_venv.py"\nMAINTAINER_GLOB="maintainer/*/test_*.py"\n',
    }
    for name in COMP_BRAND:
        files[f'{COMP}/brand/{name}'] = PNG_B if '2x' in name else PNG_A
    return files


def git(repo, *args, env=None):
    full_env = dict(os.environ, **(env or {}))
    subprocess.run(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', *args],
                   check=True, capture_output=True, env=full_env)


def head_of(repo):
    return subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                          check=True, capture_output=True, text=True).stdout.strip()


class GateCase(unittest.TestCase):
    def tmp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def release_tree(self, files=None, author=AUTHOR, record=None, symlinks=None):
        """Commit a synthetic release, then write its results record (after the commit, so the record
        names the real HEAD unless the case overrides it)."""
        repo = self.tmp()
        spec = base_files()
        for rel, body in (files or {}).items():
            if body is None:
                spec.pop(rel, None)
            else:
                spec[rel] = body
        git(repo, 'init', '-q')
        for rel, body in spec.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body if isinstance(body, bytes) else body.encode('utf-8'))
        for rel, target in (symlinks or {}).items():
            os.symlink(target, repo / rel)
        git(repo, 'add', '-A')
        env = {'GIT_AUTHOR_NAME': 'release', 'GIT_AUTHOR_EMAIL': author,
               'GIT_COMMITTER_NAME': 'release', 'GIT_COMMITTER_EMAIL': author}
        git(repo, 'commit', '-q', '-m', 'synthetic release', env=env)
        opts = {'present': True, 'mode': 'full', 'dirty': False, 'exit': 0, 'head': None, 'omit': [],
                'runner_sha': None}
        opts.update(record or {})
        if opts['present']:
            path = repo / 'build' / 'test-results.json'
            runner_sha = opts['runner_sha'] or hashlib.sha256((repo / 'run_tests.sh').read_bytes()).hexdigest()
            suites = [name for name in SYNTH_SUITES if name not in opts['omit']]
            write_record(path, opts['mode'], '2026-10-11T00:00:00Z', suites,
                         opts['head'] or head_of(repo), opts['dirty'], planned=len(SYNTH_SUITES),
                         runner_sha=runner_sha)
            if opts['exit']:
                data = json.loads(path.read_text(encoding='utf-8'))
                data['suites'][0]['exit'] = opts['exit']
                path.write_text(json.dumps(data), encoding='utf-8')
        return repo

    def patterns_file(self):
        path = self.tmp() / 'private.txt'
        path.write_text('# private literals\n' + LITERAL + '\n', encoding='utf-8')
        return path

    def crafted_archive(self, members, name='bose-lifestyle-1.0.0.zip'):
        path = self.tmp() / name
        with zipfile.ZipFile(path, 'w') as zf:
            for rel, (body, attr) in members.items():
                info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = attr
                zf.writestr(info, body)
        return path

    def gate(self, repo, *args):
        proc = subprocess.run([sys.executable, '-B', str(GATE), '--root', str(repo), *args],
                              capture_output=True, text=True)
        results = {}
        for line in proc.stdout.splitlines():
            found = re.match(r'(PASS|FAIL|SKIPPED) ([a-z-]+)(?:: (.*))?$', line)
            if found:
                results[found.group(2)] = (found.group(1), found.group(3) or '')
        return proc.returncode, results, proc.stdout + proc.stderr

    def failing(self, results):
        return {name for name, (status, _reason) in results.items() if status == 'FAIL'}


class GateTests(GateCase):
    def test_placeholder_check_ignores_release_time_checklist_tables(self):
        registry = (REGISTRY + '\n## Release-time checks\n\n'
                    '| Item | Where | Who | What |\n|---|---|---|---|\n'
                    '| HACS validation action | workflow | Maintainer | Verify brands. |\n')
        repo = self.release_tree(files={'docs/RELEASE-PLACEHOLDERS.md': registry})
        gate = release_gate.Gate(root=repo, scratch=self.tmp())
        self.assertEqual(release_gate.c_placeholders(gate), (True, ''))

    def test_explicit_github_noreply_release_identity_is_allowed(self):
        email = '68512888+6K6666' + chr(64) + 'users.noreply.github.com'
        gate = release_gate.Gate(root=self.tmp(), scratch=self.tmp(), release_identity=email)
        with mock.patch.object(release_gate, 'git', return_value=email):
            self.assertEqual(release_gate.c_identity(gate), (True, ''))
            gate.release_identity = None
            self.assertFalse(release_gate.c_identity(gate)[0])
            gate.release_identity = email
            gate.deny_identity = [email.lower()]
            self.assertFalse(release_gate.c_identity(gate)[0])

    def test_gate_passes_on_a_clean_synthetic_release(self):
        repo = self.release_tree()
        code, results, text = self.gate(repo, '--patterns', str(self.patterns_file()),
                                        '--review-hash', self.review_hash(repo), '--strict')
        self.assertEqual(code, 0, text)
        self.assertEqual(self.failing(results), set())
        self.assertEqual(results['review-hash'][0], 'PASS')
        self.assertEqual(results['results-file'][0], 'PASS')

    def review_hash(self, repo):
        return build_release(repo, self.tmp() / 'review', '1.0.0').sha256

    def crafted_symlink_zip(self):
        return self.crafted_archive({'link': (b'README.md', stat.S_IFLNK << 16 | 0o777)})

    def test_one_violation_per_check_fails_only_that_check(self):
        """Each case plants one violation and asserts the failing checks."""
        cases = {
            'results-file': dict(record={'present': False}, allowed=ALL_RESULTS),
            'results-mode': dict(record={'mode': 'stdlib'}),
            'results-head': dict(record={'head': '0' * 40}),
            'results-clean': dict(record={'dirty': True}),
            'results-suites': dict(record={'exit': 1}),
            'neutrality': dict(files={'docs/notes.md': 'value ' + LITERAL.lower() + '\n'}),
            'placeholders': dict(files={'README.md': '# Title\n\n' + TOKEN + '\n' + FULL + '\n'}),
            'docs-lint': dict(files={'test_docs.py': 'import sys\nsys.exit(1)\n'}),
            'archive-build': dict(symlinks={'link.txt': 'README.md'}, allowed=HEAD_READERS | {'archive-build'}),
            'archive-scan': dict(archive=lambda self: self.crafted_symlink_zip()),
            'archive-members': dict(archive=lambda self: self.crafted_archive(
                {'maintainer/notes.txt': (b'owner note\n', 0o100644 << 16)}),
                allowed={'archive-members', 'archive-scan'}),
            'versions': dict(files={'CHANGELOG.md': '## [1.0.1] - 2026-10-12\n\n- Other.\n'}),
            'brand': dict(files={'brand/icon@2x.png': None}),
            'disclaimer': dict(files={'README.md': '# Bose Lifestyle Controller\n\nSynthetic readme.\n'}),
            'display-name': dict(files={f'{COMP}/manifest.json':
                                        '{"name": "Other Name", "version": "1.0.0"}\n'}),
            'identity': dict(author=DEFAULT_IDENTITY),
            'todo': dict(files={'README.md': '# Title\n\n' + TODO_WORD + ' later\n\n' + FULL + '\n'}),
            'review-hash': dict(review='0' * 64),
        }
        for check, case in cases.items():
            with self.subTest(check=check):
                repo = self.release_tree(files=case.get('files'), author=case.get('author', AUTHOR),
                                         record=case.get('record'), symlinks=case.get('symlinks'))
                args = ['--patterns', str(self.patterns_file())]
                if case.get('review'):
                    args += ['--review-hash', case['review']]
                if callable(case.get('archive')):
                    args += ['--archive', str(case['archive'](self))]
                code, results, text = self.gate(repo, *args)
                self.assertNotEqual(code, 0, check)
                allowed = case.get('allowed', {check})
                self.assertIn(check, self.failing(results), f'{check} did not fail: {results}')
                self.assertLessEqual(self.failing(results), allowed, f'{check}: {results}')
                self.assertNotIn(LITERAL, text)
                self.assertNotIn(LITERAL.lower(), text)

    def test_dirty_tree_fails_the_gate(self):
        repo = self.release_tree()
        (repo / 'VERSION').write_text('1.0.1\n', encoding='utf-8')   # uncommitted
        code, results, text = self.gate(repo, '--patterns', str(self.patterns_file()))
        self.assertNotEqual(code, 0, text)
        self.assertEqual(results['tree-clean'][0], 'FAIL', results)
        self.assertEqual(results['versions'][0], 'PASS', results)   # the committed VERSION is what counts

    def test_checks_read_head_not_the_working_tree(self):
        repo = self.release_tree()
        (repo / 'VERSION').write_text('9.9.9\n', encoding='utf-8')
        # Hide the change from git so only a working-tree read could see it; the checks must still read HEAD.
        git(repo, 'update-index', '--assume-unchanged', 'VERSION')
        gate = release_gate.Gate(root=repo, scratch=self.tmp())
        self.assertTrue(release_gate.c_versions(gate)[0])

    def test_missing_card_suite_fails_gate(self):
        repo = self.release_tree(record={'omit': ['test_fake_card.js']})
        _code, results, _text = self.gate(repo, '--patterns', str(self.patterns_file()))
        self.assertEqual(results['results-suites'][0], 'FAIL', results)

    def test_record_from_different_run_tests_sh_hash_fails(self):
        repo = self.release_tree(record={'runner_sha': '0' * 64})
        _code, results, _text = self.gate(repo, '--patterns', str(self.patterns_file()))
        self.assertEqual(results['results-suites'][0], 'FAIL', results)
        self.assertIn('run_tests.sh', results['results-suites'][1])

    def test_hand_written_record_without_runner_env_refused(self):
        path = self.tmp() / 'build' / 'test-results.json'
        argv = [sys.executable, '-B', str(ROOT / 'tools' / 'results_record.py'), 'write', '--results', str(path),
                '--mode', 'full', '--started', '2026-10-11T00:00:00Z', '--head', 'a' * 40, '--dirty', 'false',
                '--planned', '3', '--suites', 'test_docs.py']
        env = dict(os.environ)
        env.pop('BOSE_RUNNER', None)
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(path.exists())
        repo = self.release_tree()
        record = json.loads((repo / 'build' / 'test-results.json').read_text(encoding='utf-8'))
        del record['runner']
        (repo / 'build' / 'test-results.json').write_text(json.dumps(record), encoding='utf-8')
        _code, results, _text = self.gate(repo, '--patterns', str(self.patterns_file()))
        self.assertEqual(results['results-suites'][0], 'FAIL', results)

    def test_release_identity_must_match_the_head_author(self):
        repo = self.release_tree()
        patterns = ['--patterns', str(self.patterns_file())]
        _, results, _ = self.gate(repo, *patterns, '--release-identity', AUTHOR)
        self.assertEqual(results['identity'][0], 'PASS', results)
        _, results, _ = self.gate(repo, *patterns, '--release-identity', 'other' + '@' + 'synthetic.test')
        self.assertEqual(results['identity'][0], 'FAIL', results)
        self.assertIn('--release-identity', results['identity'][1])

    def test_stale_results_file_is_rejected(self):
        for check, record in (('results-head', {'head': '0' * 40}), ('results-clean', {'dirty': True}),
                              ('results-mode', {'mode': 'stdlib'})):
            with self.subTest(check=check):
                repo = self.release_tree(record=record)
                _code, results, _text = self.gate(repo, '--patterns', str(self.patterns_file()))
                self.assertEqual(results[check][0], 'FAIL')
                self.assertEqual(self.failing(results) & ALL_RESULTS, {check})

    def test_gate_refuses_when_review_hash_differs(self):
        repo = self.release_tree()
        code, results, _text = self.gate(repo, '--patterns', str(self.patterns_file()), '--review-hash', '0' * 64)
        self.assertNotEqual(code, 0)
        self.assertEqual(results['review-hash'][0], 'FAIL')

    def test_gate_refuses_an_archive_that_is_not_a_fresh_build(self):
        repo = self.release_tree()
        stale = self.crafted_archive({'README.md': (b'old\n', 0o100644 << 16)})
        code, results, _text = self.gate(repo, '--patterns', str(self.patterns_file()),
                                         '--archive', str(stale), '--review-hash', self.review_hash(repo))
        self.assertNotEqual(code, 0)
        self.assertEqual(results['review-hash'][0], 'FAIL')

    def test_gate_strict_fails_when_review_hash_or_patterns_missing(self):
        repo = self.release_tree()
        code, results, text = self.gate(repo)
        self.assertEqual(code, 0, text)
        self.assertEqual(results['neutrality'][0], 'SKIPPED')
        self.assertEqual(results['review-hash'][0], 'SKIPPED')
        self.assertIn('SKIPPED: neutrality, review-hash', text)
        code, results, text = self.gate(repo, '--strict')
        self.assertNotEqual(code, 0)
        self.assertEqual(self.failing(results) - {'strict'}, set())
        self.assertIn('FAIL strict:', text)

    def test_archive_reproducibility_fails_when_two_builds_differ(self):
        repo = self.release_tree()
        real = build_release
        calls = []

        def flaky(root, out, version):
            info = real(root, out, version)
            calls.append(1)
            return dataclasses.replace(info, sha256='f' * 64) if len(calls) > 1 else info

        buf = io.StringIO()
        with mock.patch.object(release_gate, 'build_release', side_effect=flaky), \
                contextlib.redirect_stdout(buf):
            code = release_gate.main(['--root', str(repo), '--patterns', str(self.patterns_file())])
        self.assertEqual(code, 1)
        self.assertIn('FAIL archive-reproducible', buf.getvalue())
        self.assertNotIn('FAIL archive-scan', buf.getvalue())

    def test_no_private_literal_in_gate_output(self):
        repo = self.release_tree(files={'docs/notes.md': 'value ' + LITERAL.lower() + '\n'})
        code, _results, text = self.gate(repo, '--patterns', str(self.patterns_file()), '--strict')
        self.assertNotEqual(code, 0)
        self.assertNotIn(LITERAL, text)
        self.assertNotIn(LITERAL.lower(), text)


class ResultsRecordTests(GateCase):
    def test_failing_run_removes_results_file(self):
        path = self.tmp() / 'build' / 'test-results.json'
        write_record(path, 'full', '2026-10-11T00:00:00Z', ['test_a.py'], '1' * 40, False, planned=1,
                     runner_sha='2' * 64)
        self.assertTrue(path.is_file())
        clear_record(path)
        self.assertFalse(path.exists())
        clear_record(path)   # clearing a missing record is not an error

    def test_results_record_has_contract_fields(self):
        path = self.tmp() / 'r.json'
        record = write_record(path, 'stdlib', '2026-10-11T00:00:00Z', ['test_a.py'], 'a' * 40, True, planned=1,
                              runner_sha='2' * 64)
        self.assertEqual(set(record), {'head', 'dirty', 'mode', 'started', 'finished', 'suites', 'runner',
                                       'runner_sha256', 'planned'})
        self.assertEqual(record['runner'], 'run_tests.sh')
        self.assertEqual(record['suites'], [{'name': 'test_a.py', 'exit': 0}])
        self.assertIs(record['dirty'], True)
        with self.assertRaises(ValueError):
            write_record(path, 'quick', '2026-10-11T00:00:00Z', [], 'a' * 40, False, planned=0, runner_sha='2' * 64)


class RealRunnerAgreement(unittest.TestCase):
    """The gate must count the suites the real run_tests.sh plans, including the Home Assistant maintainer suites."""

    def test_expected_suites_include_both_maintainer_globs_from_the_real_runner(self):
        paths = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        runner = (ROOT / 'run_tests.sh').read_text(encoding='utf-8')

        class Head:
            def head_text(self, name): return runner
            def head_paths(self): return paths

        names = release_gate.expected_suites(Head())
        import fnmatch
        for pattern in ('maintainer/*/test_*.py', 'maintainer/*/check_*.py'):   # both globs the runner uses
            for path in paths:
                if fnmatch.fnmatchcase(path, pattern) and path.count('/') == pattern.count('/'):
                    self.assertIn(path, names)


if __name__ == '__main__':
    unittest.main()
