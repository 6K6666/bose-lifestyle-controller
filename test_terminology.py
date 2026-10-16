"""Platform-neutral wording: no tracked text file says 'Pi' as a word, except the two pages that name it.

Run: python3 -B test_terminology.py
Whole-word match: 'Pi' is matched only where it is not part of a letter run, so api, pip,
pipe and spin pass. An underscore counts as a boundary, so pi_valid and bose_controller_pi_x
are caught. The only exceptions are the two pages allowed to name a Raspberry Pi as a
known-working example (PI_ALLOWED_FILES). The example page's file name is removed before
matching, so links to it elsewhere pass. No legacy spelling may remain.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PI_WORD = re.compile(r'(?<![A-Za-z])pi(?![A-Za-z])', re.IGNORECASE)
SKIP_PATHS = {'test_terminology.py', 'test_neutrality.py', 'test_docs.py'}   # these files spell the pattern
PI_ALLOWED_FILES = ('docs/example-raspberry-pi.md', 'docs/hardware.md')
PI_PAGE_NAME = 'example-raspberry-pi.md'
SKIP_PREFIXES = ('tests/fixtures/',)
# Known-working example hardware (brand and model names) appears only on these pages.
KNOWN_EXAMPLE_PAGES = {
    'docs/hardware.md': ('DTECH', 'Poyiccot', 'HP Media Center', 'OVU400103'),
    'docs/receivers.md': ('HP Media Center', 'OVU400103'),
}
KNOWN_EXAMPLE_NAMES = tuple(sorted({lit for names in KNOWN_EXAMPLE_PAGES.values() for lit in names}))
# No legacy spelling is allowed: the legacy title and entity code was removed (Task 10c).
ALLOWED_GLOBAL = ()
ALLOWED_GLOBAL_FILES = ()
ALLOWED_LITERALS = ()


def tracked_text_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True).stdout
    for name in out.decode('utf-8').split('\0'):
        if not name or name in SKIP_PATHS or name.startswith(SKIP_PREFIXES):
            continue
        data = (ROOT / name).read_bytes()
        if b'\0' in data:
            continue
        yield name, data.decode('utf-8', 'replace')


def findings():
    hits = []
    for name, text in tracked_text_files():
        if name in PI_ALLOWED_FILES:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.replace(PI_PAGE_NAME, '')
            if name in ALLOWED_GLOBAL_FILES:
                for literal in ALLOWED_GLOBAL:
                    stripped = stripped.replace(literal, '')
            for rel, literal in ALLOWED_LITERALS:
                if rel == name:
                    stripped = stripped.replace(literal, '')
            if PI_WORD.search(stripped):
                hits.append(f'{name}:{n}')
    return hits


class TerminologyTests(unittest.TestCase):
    def test_no_pi_wording_in_repo(self):
        hits = findings()
        self.assertEqual(hits, [], 'Pi wording left: ' + ', '.join(hits))

    def test_word_matcher_ignores_substrings(self):
        for word in ('api', 'pip', 'pipe', 'spin', 'pikachu'):
            self.assertIsNone(PI_WORD.search(word), word)
        for word in ('Pi', 'pi_valid', 'bose_controller_pi_mute', 'the Pi host'):
            self.assertIsNotNone(PI_WORD.search(word), word)

    def test_known_example_names_only_on_allowed_pages(self):
        # Known-working example hardware is named only on the hardware and receivers pages.
        hits = sorted({name for name, text in tracked_text_files()
                       if name not in KNOWN_EXAMPLE_PAGES
                       and any(lit.lower() in text.lower() for lit in KNOWN_EXAMPLE_NAMES)})
        self.assertEqual(hits, [], 'known-example name outside its pages: ' + ', '.join(hits))
        for rel, names in KNOWN_EXAMPLE_PAGES.items():
            text = (ROOT / rel).read_text(encoding='utf-8')
            for lit in names:
                self.assertIn(lit, text, f'{rel} must name {lit}')

    def test_no_brand_literal_in_strings_or_card_or_code(self):
        # Every tracked text file, fixtures included. The tokens are assembled here so this file never spells them.
        tokens = tuple(''.join(parts).lower() for parts in (('Phil', 'ips'), ('Direc', 'TV'), ('RC', '73'), ('Si', 'ri')))
        hits = []
        for name in git_tracked_files():
            data = (ROOT / name).read_bytes()
            if b'\0' in data:
                continue
            low = data.decode('utf-8', 'replace').lower()
            hits += [f'{name}:{token}' for token in tokens if token in low]
        self.assertEqual(hits, [], 'brand literal left: ' + ', '.join(hits))


def git_tracked_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in out.decode('utf-8').split('\0') if name]


if __name__ == '__main__':
    unittest.main(verbosity=1)
