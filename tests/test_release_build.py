"""Release builder and archive scan (release plan, Task 1).

Run: python3 -B tests/test_release_build.py
Synthetic repos are built in temporary directories; planted values are assembled at runtime.
"""
import _bootstrap
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_release import build_release, scan_archive  # noqa: E402
from public_manifest import public_entries  # noqa: E402

IP_TEXT = '10.' + '0.0.' + '242'
MAIL_TEXT = 'owner' + '@' + 'example' + '.com'
SERIAL_TEXT = 'usb-' + 'FTDI_' + 'X-if00-port0'
LITERAL = 'Quartzkite' + '42'
PLANTED = (IP_TEXT, MAIL_TEXT, SERIAL_TEXT, LITERAL)
EXCLUDED_PREFIXES = ('maintainer/', 'reports/', 'backups/', 'dist/', 'docs/superpowers/', '.superpowers/')


def git(repo, *args):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=' + 'test@' + 'example.invalid',
                    *args], check=True, capture_output=True)


def write_zip(path, members, comment=b''):
    """members: name -> bytes, or (bytes, external_attr). Hand-crafted archives for the scan tests."""
    with zipfile.ZipFile(path, 'w') as zf:
        zf.comment = comment
        for name, value in members.items():
            body, attr = value if isinstance(value, tuple) else (value, 0o100644 << 16)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = attr
            zf.writestr(info, body)
    return path


class ReleaseTestCase(unittest.TestCase):
    def tmp(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def make_repo(self, files, symlinks=(), modes=None):
        repo = self.tmp()
        git(repo, 'init', '-q')
        for rel, body in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body if isinstance(body, bytes) else body.encode('utf-8'))
        for rel, target in symlinks:
            os.symlink(target, repo / rel)
        for rel, mode in (modes or {}).items():
            os.chmod(repo / rel, mode)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'init')
        return repo


class ReleaseBuildTests(ReleaseTestCase):
    def test_archive_has_only_manifest_files(self):
        info = build_release(ROOT, self.tmp() / 'out', '9.9.9')
        expected = [rel for rel, _mode, _sha in public_entries(ROOT)]
        self.assertEqual(info.members, expected)
        with zipfile.ZipFile(info.archive) as zf:
            self.assertEqual(zf.namelist(), expected)

    def test_symlink_in_tree_fails_build(self):
        repo = self.make_repo({'a.txt': 'a\n'}, symlinks=[('link.txt', 'a.txt')])
        with self.assertRaises(ValueError):
            build_release(repo, self.tmp() / 'out', '1.0.0')

    def test_planted_state_files_are_excluded_or_fail(self):
        repo = self.make_repo({
            'README.md': 'hello\n',
            '.github/workflows/tests.yml': 'name: tests\n',
            'config.json': '{}',
            'ir-config.json': '{}',
            'ir-state.json': '{}',
            'pkg/__pycache__/m.cpython-39.pyc': b'\x00\xff',
            '.DS_Store': b'\x00\xff',
            '.gitignore': '*.pyc\n',
            'notes.py.bak': 'old\n',
            'bose-2026-10-01.json': '{}',
            'tests/fixtures/owner_capture.json': '{}',
            '.superpowers/sdd/private/progress.md': 'private working notes\n',
            'runtime.log': 'x\n',
            'store.sqlite3': 'x\n',
            'status.sidecar.json': '{}',
        })
        info = build_release(repo, self.tmp() / 'out', '1.0.0')
        self.assertEqual(info.members, ['.github/workflows/tests.yml', '.gitignore', 'README.md'])

    def test_github_and_gitignore_ship_in_the_archive(self):
        repo = self.make_repo({'.gitignore': '__pycache__/\n', '.github/workflows/validate.yml': 'on: push\n',
                               '.gitconfig': '[user]\n', '.git-credentials': 'x\n'})
        info = build_release(repo, self.tmp() / 'out', '1.0.0')
        self.assertEqual(info.members, ['.github/workflows/validate.yml', '.gitignore'])

    def test_entries_are_stored_not_deflated(self):
        repo = self.make_repo({'a.txt': 'a\n' * 500})
        info = build_release(repo, self.tmp() / 'out', '1.0.0')
        with zipfile.ZipFile(info.archive) as zf:
            self.assertEqual({i.compress_type for i in zf.infolist()}, {zipfile.ZIP_STORED})

    def test_archive_hash_stable_across_two_builds_with_different_cwd(self):
        script = ROOT / 'tools' / 'build_release.py'
        hashes = []
        for _ in range(2):
            cwd, out = self.tmp(), self.tmp() / 'out'
            run = subprocess.run([sys.executable, '-B', str(script), '--out', str(out), '--root', str(ROOT)],
                                 cwd=cwd, capture_output=True, text=True, check=True)
            hashes.append(next(line.split(': ', 1)[1] for line in run.stdout.splitlines()
                               if line.startswith('sha256: ')))
        self.assertEqual(hashes[0], hashes[1])

    def test_version_defaults_to_version_file(self):
        from build_release import default_version
        version = default_version(ROOT)
        info = build_release(ROOT, self.tmp() / 'out', version)
        self.assertEqual(info.archive.name, f'bose-lifestyle-{version}.zip')

    def test_non_utf8_unknown_text_fails(self):
        repo = self.make_repo({'data.dat': b'\xff\xfe\x00bad'})
        with self.assertRaises(ValueError):
            build_release(repo, self.tmp() / 'out', '1.0.0')

    def test_scan_flags_planted_ip_serial_email_by_shape_member_only(self):
        archive = write_zip(self.tmp() / 'a.zip', {
            'ip.txt': ('host = "' + IP_TEXT + '"\n').encode(),
            'serial.txt': ('path = "' + SERIAL_TEXT + '"\n').encode(),
            'mail.txt': ('to = "' + MAIL_TEXT + '"\n').encode(),
        }, comment=b'')
        found = scan_archive(archive)
        self.assertEqual(len(found), 3, found)
        self.assertEqual(sorted(f.split(':')[0] for f in found), ['ip.txt', 'mail.txt', 'serial.txt'])
        for item in found:
            for value in PLANTED:
                self.assertNotIn(value, item)

    def test_scan_with_private_list_finds_planted_literal_without_echo(self):
        patterns = self.tmp() / 'literals.txt'
        patterns.write_text('# private\n' + LITERAL + '\n', encoding='utf-8')
        archive = write_zip(self.tmp() / 'a.zip', {
            'notes.md': ('the value is ' + LITERAL.lower() + '\n').encode(),
            'clean.md': b'nothing here\n',
        })
        self.assertEqual(scan_archive(archive), [])
        found = scan_archive(archive, patterns)
        self.assertEqual(found, ['notes.md:line 1: literal'])
        self.assertNotIn(LITERAL.lower(), ' '.join(found))

    def test_scan_reads_zip_comment_and_member_names(self):
        patterns = self.tmp() / 'literals.txt'
        patterns.write_text(LITERAL + '\n', encoding='utf-8')
        archive = write_zip(self.tmp() / 'a.zip', {
            LITERAL + '.txt': (b'clean\n', 0o100644 << 16),
        }, comment=('comment ' + LITERAL).encode())
        found = scan_archive(archive, patterns)
        self.assertTrue(any('comment' in f for f in found), found)
        self.assertTrue(any(f.startswith('#') or f.startswith('(archive)') for f in found), found)
        self.assertNotIn(LITERAL, ' '.join(found))

    def test_scan_flags_unnormalized_metadata(self):
        archive = write_zip(self.tmp() / 'a.zip', {
            'link': (b'target', stat.S_IFLNK << 16 | 0o777),
            'secret.txt': (b'x', 0o100600 << 16),
        })
        found = scan_archive(archive)
        self.assertTrue(any(f.startswith('link:') for f in found), found)
        self.assertTrue(any(f.startswith('secret.txt:') for f in found), found)

    def test_archive_is_reproducible(self):
        first = build_release(ROOT, self.tmp() / 'one', '1.0.0')
        second = build_release(ROOT, self.tmp() / 'two', '1.0.0')
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.sha256, hashlib.sha256(first.archive.read_bytes()).hexdigest())

    def test_maintainer_and_reports_absent(self):
        info = build_release(ROOT, self.tmp() / 'out', '1.0.0')
        for member in info.members:
            self.assertFalse(member.startswith(EXCLUDED_PREFIXES), member)
            self.assertFalse(member.startswith('.git') and member != '.gitignore' and not member.startswith('.github/'), member)

    def test_version_embedded_in_archive_name(self):
        info = build_release(ROOT, self.tmp() / 'out', '1.2.3')
        self.assertEqual(info.archive.name, 'bose-lifestyle-1.2.3.zip')
        self.assertEqual(info.tree.name, 'bose-lifestyle-1.2.3')
        with self.assertRaises(ValueError):
            build_release(ROOT, self.tmp() / 'bad', '../escape')

    def test_uncommitted_edits_do_not_leak(self):
        repo = self.make_repo({'a.txt': 'committed\n'})
        (repo / 'a.txt').write_text('LEAK\n', encoding='utf-8')
        (repo / 'new.txt').write_text('untracked\n', encoding='utf-8')
        info = build_release(repo, self.tmp() / 'out', '1.0.0')
        with zipfile.ZipFile(info.archive) as zf:
            self.assertEqual(zf.read('a.txt'), b'committed\n')
        self.assertEqual(info.members, ['a.txt'])

    def test_modes_are_0644_or_0755_and_timestamps_fixed(self):
        repo = self.make_repo({'run.sh': '#!/bin/sh\n', 'a.txt': 'a\n'},
                              modes={'run.sh': 0o755, 'a.txt': 0o600})
        info = build_release(repo, self.tmp() / 'out', '1.0.0')
        with zipfile.ZipFile(info.archive) as zf:
            modes = {i.filename: stat.S_IMODE(i.external_attr >> 16) for i in zf.infolist()}
            dates = {i.date_time for i in zf.infolist()}
        self.assertEqual(modes, {'run.sh': 0o755, 'a.txt': 0o644})
        self.assertEqual(dates, {(1980, 1, 1, 0, 0, 0)})
        self.assertEqual(scan_archive(info.archive), [])


if __name__ == '__main__':
    unittest.main()
