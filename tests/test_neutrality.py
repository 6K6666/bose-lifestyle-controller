"""Neutrality of the public tree: no maintainer personalization in files that ship.

Run: python3 -B tests/test_neutrality.py
The private literal list lives outside the repo. Pass its path with --patterns PATH or the
BOSE_PRIVATE_LITERALS environment variable. Without it the literal check prints SKIPPED and passes
(the release gate always runs it with the list). Forbidden fragments are assembled at runtime so
this file never contains them.
"""
import _bootstrap
import ast
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT))
from public_manifest import public_files, tracked_files  # noqa: E402
from test_private_scan import load_lines  # noqa: E402

SELF_EXEMPT = {'tests/test_neutrality.py', 'tests/test_terminology.py', 'tools/neutral_shapes.txt'}

# Public examples of a host or a home user that are documented placeholders, never a real person.
GENERIC_HOME_USERS = {'bose', 'svc-bridge', 'example', 'user', 'name'}
# Addresses reserved for documentation or loopback are the only IPv4 forms allowed in public text.
DOC_ADDRESS = re.compile(r'^(?:127\.0\.0\.1|0\.0\.0\.0|192\.0\.2\.\d+|198\.51\.100\.\d+|203\.0\.113\.\d+)$')
IPV4 = re.compile(r'(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])')
MAC = re.compile(r'(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:])')
HOME_USER = re.compile(r'/home/([A-Za-z0-9_.-]+)')
PI_WORD = re.compile(r'(?<![A-Za-z])' + 'P' + 'i' + r'(?![A-Za-z])')
WORKSPACE = ('/' + 'Users/', 'Documents/' + 'bose-control')
SYNTHETIC_HEX = re.compile(r'^(?:0123456789abcdef)+$|^([0-9a-f])\1*$')
SYNTHETIC_BY_ID = re.compile(r'usb-(?:example|PLACEHOLDER)(?:[-_].*)?|usb-')


def private_patterns():
    """Literal list from the environment or --patterns (argv is consumed here, before unittest sees it)."""
    if '--patterns' in sys.argv:
        at = sys.argv.index('--patterns')
        path = sys.argv[at + 1]
        del sys.argv[at:at + 2]
        return load_lines(path)
    env = os.environ.get('BOSE_PRIVATE_LITERALS')
    return load_lines(env) if env else None


PI_ALLOWED_FILES = ('docs/example-raspberry-pi.md', 'docs/hardware.md')   # the only pages that name the platform
PI_PAGE_NAME = 'example-raspberry-pi.md'


def pi_word_hits(files):
    found = []
    for rel in files:
        if rel in SELF_EXEMPT or rel in PI_ALLOWED_FILES:
            continue
        text = text_of(rel)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PI_WORD.search(line.replace(PI_PAGE_NAME, '')):
                found.append(f'{rel}:{n}')
    return found


def text_of(rel):
    data = (ROOT / rel).read_bytes()
    if b'\0' in data:
        return None
    return data.decode('utf-8', 'replace')


def hits(files, pattern, allowed=lambda match: False):
    found = []
    for rel in files:
        if rel in SELF_EXEMPT:
            continue
        text = text_of(rel)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for match in pattern.finditer(line):
                if not allowed(match):
                    found.append(f'{rel}:{n}')
    return found


PRIVATE_LITERALS = None   # set by the __main__ block (or stays None: the literal test then SKIPs)


class NeutralityTests(unittest.TestCase):
    def setUp(self):
        self.public = public_files(ROOT)

    def test_no_private_literals_in_tracked_files(self):
        literals = PRIVATE_LITERALS
        if literals is None:
            print('\nSKIPPED: no private literal list (pass --patterns PATH or set BOSE_PRIVATE_LITERALS)')
            return
        found = []
        for rel in tracked_files(ROOT):
            text = text_of(rel)
            if text is None:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                low = line.lower()
                if any(lit.lower() in low for lit in literals if lit):
                    found.append(f'{rel}:{n}')
        print(f'\nprivate literal hits: {len(found)}')
        for item in found:
            print('  ' + item)
        self.assertEqual(found, [], 'private literal found in tracked files (file:line only)')

    def test_generic_shapes_absent(self):
        ip_hits = hits(self.public, IPV4, allowed=lambda m: bool(DOC_ADDRESS.match(m.group(0))))
        mac_hits = hits(self.public, MAC)
        mail_hits = hits(self.public, re.compile(r'[A-Za-z0-9._%+-]+@(?!2x\.png\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+'))
        home_hits = hits(self.public, HOME_USER, allowed=lambda m: m.group(1) in GENERIC_HOME_USERS)
        pi_hits = pi_word_hits(self.public)
        by_id = []
        for rel in self.public:
            text = text_of(rel)
            if text is None or rel in SELF_EXEMPT:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for m in re.finditer(r'usb-[A-Za-z0-9_.-]+', line):
                    if not SYNTHETIC_BY_ID.match(m.group(0)) and '_' in m.group(0):
                        by_id.append(f'{rel}:{n}')
        self.assertEqual(ip_hits, [], 'IPv4 address shape in public files')
        self.assertEqual(mac_hits, [], 'MAC address shape in public files')
        self.assertEqual(mail_hits, [], 'e-mail shape in public files')
        self.assertEqual(home_hits, [], '/home/<name> outside the documented placeholders')
        self.assertEqual(pi_hits, [], 'the word Pi in public files')
        self.assertEqual(by_id, [], 'usb- by-id path with a real-looking vendor and serial')

    def test_known_example_names_only_on_allowed_pages(self):
        # The same allowlist as test_terminology.py: example hardware is named only on these two pages.
        allowed = {'docs/hardware.md': ('DTECH', 'Poyiccot', 'HP Media Center', 'OVU400103'),
                   'docs/receivers.md': ('HP Media Center', 'OVU400103')}
        names = {name for group in allowed.values() for name in group}
        found = []
        for rel in self.public:
            text = text_of(rel)
            if text is None or rel in SELF_EXEMPT:
                continue
            for name in names:
                if name in text and name not in allowed.get(rel, ()):
                    found.append(rel)
        self.assertEqual(sorted(set(found)), [], 'known-example name outside its pages')

    def test_no_house_room_names_or_receiver_or_remote_brands(self):
        brands = [''.join(parts) for parts in (('Phil', 'ips'), ('Direc', 'TV'), ('RC', '73'), ('Si', 'ri'))]
        pattern = re.compile('|'.join(re.escape(b) for b in brands), re.IGNORECASE)
        self.assertEqual(hits(self.public, pattern), [], 'receiver or remote brand in a public file')

    def test_no_legacy_migration_code_in_public_tree(self):
        tokens = [''.join(parts) for parts in (('migrat',), ('MIGRATION', '_CONFIG'), ('histor', 'ical'))]
        pattern = re.compile('|'.join(re.escape(t) for t in tokens), re.IGNORECASE)
        self.assertEqual(hits(self.public, pattern), [], 'migration code or fixture in the public tree')
        init = (ROOT / 'custom_components/bose_lifestyle/__init__.py').read_text(encoding='utf-8')
        self.assertNotIn('LEGACY', init)

    def test_fixtures_are_synthetic(self):
        bad = []
        for rel in self.public:
            if not (rel.startswith('tests/') or rel.startswith('test_')):
                continue
            text = text_of(rel)
            if text is None or rel in SELF_EXEMPT:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for m in re.finditer(r'(?:bridge_id|remote_id)["\']?\s*[:=]\s*["\']([^"\']+)["\']', line):
                    value = m.group(1)
                    if not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
                        continue                                    # deliberately invalid input, not an id
                    if not (SYNTHETIC_HEX.match(value) or re.fullmatch(r'[a-z]', value)
                            or 'example' in value or 'PLACEHOLDER' in value or value.startswith('bridge.example')):
                        bad.append(f'{rel}:{n}')
        self.assertEqual(bad, [], 'bridge or remote id that is not a synthetic shape')
        for rel in self.public:
            if rel.startswith(('tests/', 'test_')):
                text = text_of(rel) or ''
                for m in re.finditer(r'usb-[A-Za-z0-9_.-]*', text):
                    self.assertTrue(SYNTHETIC_BY_ID.fullmatch(m.group(0)), (rel, m.group(0)))

    def test_no_workspace_paths_in_public_files(self):
        pattern = re.compile('|'.join(re.escape(w) for w in WORKSPACE))
        self.assertEqual(hits(self.public, pattern), [], 'workspace path in a public file')

    def test_defaults_are_neutral(self):
        tree = ast.parse((ROOT / 'custom_components/bose_lifestyle/rooms.py').read_text(encoding='utf-8'))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ('DEFAULT_ROOMS', 'DEFAULT_BRIDGE_NAME'):
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(values['DEFAULT_BRIDGE_NAME'], 'Bose Controller')
        self.assertEqual(values['DEFAULT_ROOMS'], {'a': {'name': 'Room A'}, 'b': {'name': 'Room B'},
                                                    'c': {'name': 'Room C'}, 'd': {'name': 'Room D'}})
        flow = (ROOT / 'custom_components/bose_lifestyle/config_flow.py').read_text(encoding='utf-8')
        self.assertIn("vol.Required('host'):str", flow)            # the host has no default
        installer = (ROOT / 'deploy/install-bridge.sh').read_text(encoding='utf-8')
        self.assertIn('USER_NAME=bose;', installer)

    def test_maintainer_dir_excluded_from_public_manifest(self):
        self.assertTrue(self.public, 'manifest is empty')
        owned = [rel for rel in tracked_files(ROOT) if rel.startswith('maintainer/')]
        if (ROOT / 'maintainer').exists():
            self.assertTrue(owned, 'maintainer/ should hold tracked owner tooling')
        for rel in owned:
            self.assertNotIn(rel, self.public, rel)
        self.assertTrue(all(not rel.startswith('maintainer/') for rel in self.public))


if __name__ == '__main__':
    PRIVATE_LITERALS = private_patterns()     # consumes --patterns before unittest parses argv
    unittest.main()
