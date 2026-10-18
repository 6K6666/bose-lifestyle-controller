"""Private-value scan and port/substitution tests.

Run the unit tests:    python3 -B tests/test_private_scan.py
Run the scan of a tree with external literals (literal values never live in this repo):
    python3 -B tests/test_private_scan.py --patterns PATH [--root DIR]
"""
import _bootstrap
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from subst import subst  # noqa: E402

SHAPES_FILE = ROOT / 'tools' / 'neutral_shapes.txt'
SKIP_DIRS = {'.git', '__pycache__', '.venv', 'venv'}
SKIP_FILES = {'tools/neutral_shapes.txt'}


def load_lines(path):
    return [ln for ln in Path(path).read_text(encoding='utf-8').splitlines()
            if ln.strip() and not ln.startswith('#')]


def scan(root, shapes, literals):
    """Return findings as 'relpath:line: kind' strings. Never echoes matched text."""
    root = Path(root)
    findings = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if SKIP_DIRS & set(path.relative_to(root).parts) or rel.startswith('build/') or rel in SKIP_FILES:
            continue
        for lit in literals:
            if lit and lit in rel:
                findings.append(f'{rel}: path contains a literal')
        text = path.read_bytes().decode('utf-8', 'replace')
        for n, line in enumerate(text.splitlines(), 1):
            for i, shape in enumerate(shapes):
                if re.search(shape, line):
                    findings.append(f'{rel}:{n}: shape {i}')
            for lit in literals:
                if lit and lit in line:
                    findings.append(f'{rel}:{n}: literal')
    return findings


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.shapes = load_lines(SHAPES_FILE)

    def test_scan_flags_planted_ip_serial_email(self):
        planted = {
            'ip.txt': 'host = "' + '10' + '.0.0.' + '242"\n',
            'serial.txt': 'path = "/dev/serial/by-id/' + 'usb-' + 'FTDI_' + 'X-if00-port0"\n',
            'mail.txt': 'to = "owner' + '@' + 'example' + '.com"\n',
        }
        with tempfile.TemporaryDirectory() as d:
            for name, body in planted.items():
                Path(d, name).write_text(body, encoding='utf-8')
            found = scan(d, self.shapes, [])
        self.assertEqual(len(found), 3, found)

    def test_generated_outputs_are_ignored_but_source_and_config_are_scanned(self):
        literal = 'synthetic-' + 'private-value'
        with tempfile.TemporaryDirectory() as d:
            for name in ('.venv/dependency.txt', 'venv/dependency.txt', 'build/run.log',
                         'source.txt', 'src/build/source.txt', 'config.json'):
                path = Path(d, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(literal, encoding='utf-8')
            self.assertEqual(scan(d, [], [literal]),
                             ['config.json:1: literal', 'source.txt:1: literal',
                              'src/build/source.txt:1: literal'])

    def test_clean_tree_passes(self):
        self.assertEqual(scan(ROOT, self.shapes, []), [])

    def test_every_json_file_parses(self):
        for p in ROOT.rglob('*.json'):
            if SKIP_DIRS & set(p.relative_to(ROOT).parts) or p.relative_to(ROOT).parts[0] == 'build':
                continue
            json.loads(p.read_text(encoding='utf-8'))


class SubstTests(unittest.TestCase):
    def test_subst_keeps_hyphen_forms(self):
        text = ('/run/bose-control /var/lib/bose-control bose-control-card.js '
                'll-strategy-x-bose-control')
        self.assertEqual(subst(text, 'bose_lifestyle', token='example_domain'), text)

    def test_subst_rewrites_underscore_token_only(self):
        text = "domain='example_domain' url='/example_domain_static' key example_domain.rooms"
        self.assertEqual(
            subst(text, 'bose_lifestyle', token='example_domain'),
            "domain='bose_lifestyle' url='/bose_lifestyle_static' key bose_lifestyle.rooms")


if __name__ == '__main__':
    if '--patterns' in sys.argv:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument('--patterns', required=True)
        ap.add_argument('--root', default=str(ROOT))
        args = ap.parse_args()
        found = scan(args.root, load_lines(SHAPES_FILE), load_lines(args.patterns))
        for item in found:
            print(item)
        print(f'{len(found)} finding(s)')
        sys.exit(1 if found else 0)
    unittest.main()
