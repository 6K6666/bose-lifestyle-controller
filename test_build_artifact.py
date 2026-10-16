"""Tests for tools/build_artifact.py: the domain-parameterised artifact build.

Stdlib tests run anywhere. test_built_tree_passes_ha_tests needs Home Assistant
(run_tests.sh runs this file with the venv python); it is skipped without it.
Run: python3 -B test_build_artifact.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / 'tools'))
sys.path.insert(0, str(REPO))
from build_artifact import build, HA_SUITES  # noqa: E402
from subst import subst  # noqa: E402
from test_private_scan import scan, load_lines, SHAPES_FILE  # noqa: E402

COMP = 'custom_components/bose_lifestyle/'
MUST_HAVE = [
    COMP + 'manifest.json', COMP + 'card.py', COMP + 'frontend/bose-control-card.js',
    'test_bose_integration.py', 'test_bose_transport.py', 'test_bose_compat.py',
    'bose_commands.py', 'bose_events.py', 'bose_ir.py', 'bose_request.py',
    'tests/fixtures/status_v4.json',
]


def tree_bytes(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob('*')) if p.is_file()}


def copy_repo(dst):
    shutil.copytree(REPO, dst, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    return dst


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def out(self, name):
        return self.tmp / name

    def test_build_example_domain_keeps_hyphen_forms(self):
        dst = self.out('control')
        build(REPO, dst, 'example_domain')
        files = tree_bytes(dst)
        for rel in MUST_HAVE:
            self.assertIn(rel.replace('bose_lifestyle', 'example_domain'), files)
        comp = 'custom_components/example_domain/'
        self.assertIn(b'"domain":"example_domain"', files[comp + 'manifest.json'])
        card = files[comp + 'frontend/bose-control-card.js'].decode()
        self.assertIn("'example_domain.open_room'", card)
        self.assertIn('bose-control-card', card)
        self.assertIn('ll-strategy-view-bose-control', card)
        self.assertIn('ll-strategy-dashboard-bose-control', card)
        self.assertIn(b"'/example_domain_static'", files[comp + 'card.py'])
        self.assertIn(b'/run/bose-control/command.sock', files['bose_request.py'])
        # the underscore-only rule: an unrelated controller name is untouched
        self.assertIn(b'button.bose_controller_example_mute', files['test_bose_integration.py'])
        for rel, data in files.items():
            self.assertNotIn(b'bose_lifestyle', data, rel)
            self.assertNotIn('bose_lifestyle', rel)

    def test_build_public_domain_is_identity(self):
        dst = self.out('pub')
        build(REPO, dst, 'bose_lifestyle')
        built = tree_bytes(dst)
        for rel in MUST_HAVE:
            self.assertIn(rel, built)
        for rel, data in built.items():
            self.assertEqual(data, (REPO / rel).read_bytes(), rel)

    def test_roundtrip(self):
        # The repo tree is already scrubbed (no example_domain token), so the
        # plan's one-way scrub list does not apply: build out and back must be exact.
        one = self.out('one')
        build(REPO, one, 'example_domain')
        self.assertIn('custom_components/example_domain/manifest.json', tree_bytes(one))
        for rel, data in tree_bytes(one).items():
            self.assertNotIn(b'bose_lifestyle', data, rel)
        back = self.out('back')
        build(one, back, 'bose_lifestyle')
        pub = self.out('pub')
        build(REPO, pub, 'bose_lifestyle')
        self.assertEqual(tree_bytes(back), tree_bytes(pub))

    def test_json_files_parse(self):
        for domain in ('bose_lifestyle', 'example_domain'):
            dst = self.out(domain)
            build(REPO, dst, domain)
            jsons = [p for p in dst.rglob('*.json')]
            self.assertTrue(jsons)
            for p in jsons:
                json.loads(p.read_text(encoding='utf-8'))

    def test_invalid_json_source_is_refused(self):
        src = copy_repo(self.out('src'))
        (src / COMP / 'manifest.json').write_text('{"domain": "bose_lifestyle",}', encoding='utf-8')
        with self.assertRaises(ValueError):
            build(src, self.out('bad'), 'example_domain')

    def test_no_private_shapes_in_built_tree(self):
        shapes = load_lines(SHAPES_FILE)
        for domain in ('bose_lifestyle', 'example_domain'):
            dst = self.out(domain)
            build(REPO, dst, domain)
            self.assertEqual(scan(dst, shapes, []), [])

    def test_no_pycache_copied(self):
        src = copy_repo(self.out('src'))
        (src / COMP / '__pycache__').mkdir()
        (src / COMP / '__pycache__' / 'manifest.cpython-314.pyc').write_bytes(b'\x00')
        dst = self.out('dst')
        build(src, dst, 'example_domain')
        self.assertFalse(any('__pycache__' in rel for rel in tree_bytes(dst)))

    def test_refuses_symlink_and_leaves_dst_untouched(self):
        src = copy_repo(self.out('src'))
        target = self.out('outside.txt')
        target.write_text('x', encoding='utf-8')
        os.symlink(target, src / COMP / 'evil.py')
        dst = self.out('dst')
        with self.assertRaises(ValueError):
            build(src, dst, 'example_domain')
        self.assertFalse(dst.exists())

    def test_refuses_nonempty_or_inner_dst(self):
        dst = self.out('dst')
        dst.mkdir()
        (dst / 'stale.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError):
            build(REPO, dst, 'example_domain')
        with self.assertRaises(ValueError):
            build(REPO, REPO / 'custom_components' / 'inside', 'example_domain')

    def test_refuses_domain_that_is_not_identifier(self):
        for bad in ('bose-control', 'Bose', 'bose control', ''):
            with self.assertRaises(ValueError):
                build(REPO, self.out('x' + str(len(bad))), bad)

    def test_collision_guard_refuses_existing_target_token(self):
        src = copy_repo(self.out('src'))
        with open(src / 'bose_events.py', 'a', encoding='utf-8') as f:
            f.write('\n# example_domain\n')
        with self.assertRaises(ValueError):
            build(src, self.out('dst'), 'example_domain')

    def test_subst_token_parameter(self):
        text = 'bose_lifestyle /run/bose-control bose_controller_hub example_domain'
        self.assertEqual(subst(text, 'example_domain', token='bose_lifestyle'),
                         'example_domain /run/bose-control bose_controller_hub example_domain')


@unittest.skipUnless(importlib.util.find_spec('homeassistant'), 'needs the HA venv')
class BuiltTreeHATests(unittest.TestCase):
    def test_built_tree_passes_ha_tests(self):
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        for domain in ('bose_lifestyle', 'example_domain'):
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp, True)
            dst = tmp / domain
            build(REPO, dst, domain)
            for suite in HA_SUITES:
                with self.subTest(domain=domain, suite=suite):
                    r = subprocess.run([sys.executable, '-B', str(dst / suite)], cwd=dst, env=env,
                                       capture_output=True, text=True, timeout=900)
                    self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])


if __name__ == '__main__':
    unittest.main()
