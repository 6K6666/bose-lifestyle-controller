"""Verification promotions: every verified name beyond the original 30 has matching evidence; evidence is sanitized.

Run: python3 -B test_bose_promotions.py
Synthetic cases use temporary evidence directories and pure arguments, so no real promotion is needed.
The sanitizer tests need the maintainer tree and skip in the public archive.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import bose_commands  # noqa: E402
from bose_commands import COMMANDS, CATALOG, LIVE_ACTIONS, LIVE_MESSAGES, ORIGINAL_VERIFIED, VERIFIED_EVIDENCE  # noqa: E402
from bose_commands import expected_reply  # noqa: E402
from build_release import _lines  # noqa: E402
from evidence_format import ALLOWED_KEYS, promotion_problems, sha256_bytes, canonical  # noqa: E402
import gen_commands_doc  # noqa: E402

EVIDENCE_DIR = ROOT / 'docs' / 'evidence'
SANITIZER = ROOT / 'maintainer' / 'verify' / 'sanitize_evidence.py'
MAINTAINER_PRESENT = SANITIZER.exists()
BANNER = LIVE_MESSAGES['identify']   # the bridge's identify message, stored verbatim by the harness
PROMOTED = 'room-b-source-fm'   # a synthetic promotion: an ordinary command that is not in the original 30
BRIDGE_ID = '0123456789abcdef0123456789abcdef'
BRAND_WORDS = ('Phil' + 'ips', 'Direc' + 'TV', 'RC' + '73', 'Si' + 'ri')


def _load_sanitizer():
    spec = importlib.util.spec_from_file_location('sanitize_evidence', SANITIZER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shapes():
    lines = (ROOT / 'tools' / 'neutral_shapes.txt').read_text(encoding='utf-8').splitlines()
    return [re.compile(line) for line in lines if line.strip() and not line.startswith('#')]


def sanitized_record(name=PROMOTED, outcome='verified', banner=BANNER, date='2026-10-12', rx=None):
    frame = expected_reply(COMMANDS[name])
    frames = [{'kind': 'tx', 'action': name, 'detail': COMMANDS[name].hex(' ')}]
    if rx is not False:
        frames.append({'kind': 'rx', 'action': name, 'detail': (frame if rx is None else rx).hex(' ')})
    return {'name': name, 'bytes_hex': COMMANDS[name].hex(' '), 'expected_reply': frame.decode('ascii'),
            'reply': LIVE_MESSAGES[name], 'classification': 'transmitted-with-matching-response', 'outcome': outcome,
            'timestamp': date, 'banner': banner, 'observation': 'audible change', 'note': 'test setup',
            'status_before': {'health': {'mode': 'live'}, 'fault': None},
            'status_after': {'health': {'mode': 'live'}, 'fault': None},
            'frames': frames}


class Synthetic(unittest.TestCase):
    """Failure modes for a promotion, checked by the pure function with a temporary evidence directory."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='promo-'))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.verified = set(ORIGINAL_VERIFIED) | {PROMOTED}
        self.file = 'room-b-source-fm-2026-10-12.json'
        self.data = canonical(sanitized_record())
        (self.dir / self.file).write_bytes(self.data)
        self.entry = {'evidence': self.file, 'sha256': sha256_bytes(self.data), 'banner': BANNER, 'date': '2026-10-12'}

    def problems(self, table=None, verified=None):
        table = {PROMOTED: self.entry} if table is None else table
        return promotion_problems(self.verified if verified is None else verified, table, ORIGINAL_VERIFIED, self.dir,
                                  COMMANDS, expected_reply)

    def test_valid_promotion_passes(self):
        self.assertEqual(self.problems(), [])

    def test_missing_entry(self):
        self.assertTrue(any('no VERIFIED_EVIDENCE entry' in p for p in self.problems(table={})))

    def test_wrong_hash(self):
        self.entry['sha256'] = '0' * 64
        self.assertTrue(any('sha256' in p for p in self.problems()))

    def test_missing_file(self):
        (self.dir / self.file).unlink()
        self.assertTrue(any('evidence file missing' in p for p in self.problems()))

    def test_empty_banner(self):
        self.entry['banner'] = ''
        self.assertTrue(any('empty field' in p for p in self.problems()))

    def test_name_in_original_verified(self):
        original = next(iter(ORIGINAL_VERIFIED))
        table = {PROMOTED: self.entry, original: dict(self.entry)}
        self.assertTrue(any('is in ORIGINAL_VERIFIED' in p for p in self.problems(table=table)))

    def test_entry_for_name_not_verified(self):
        self.assertTrue(any('not verified' in p for p in self.problems(verified=set(ORIGINAL_VERIFIED))))

    def test_evidence_for_a_different_banner_fails(self):
        self.entry['banner'] = 'ETAP Interface V9.9'
        self.data = canonical(sanitized_record(banner=BANNER))
        self.entry['sha256'] = sha256_bytes(self.data)
        (self.dir / self.file).write_bytes(self.data)
        self.assertTrue(any('banner' in p for p in self.problems()))

    def test_evidence_for_a_different_name_or_reply_only_fails(self):
        self.data = canonical(sanitized_record(name='room-c-source-fm', outcome='reply_only'))
        self.entry['sha256'] = sha256_bytes(self.data)
        (self.dir / self.file).write_bytes(self.data)
        found = ' '.join(self.problems())
        self.assertIn('name', found)
        self.assertIn('outcome', found)

    def test_wrong_command_bytes_fail(self):
        self.data = canonical(dict(sanitized_record(), bytes_hex='4d 4d 00 0a'))
        self.entry['sha256'] = sha256_bytes(self.data)
        (self.dir / self.file).write_bytes(self.data)
        self.assertTrue(any('command bytes' in p for p in self.problems()))

    def test_rx_frame_that_differs_fails(self):
        self.data = canonical(sanitized_record(rx=b'\r\r\r\r>.C34102FF0000\r'))
        self.entry['sha256'] = sha256_bytes(self.data)
        (self.dir / self.file).write_bytes(self.data)
        self.assertTrue(any('no rx frame' in p for p in self.problems()))

    def test_no_rx_frame_fails(self):
        self.data = canonical(sanitized_record(rx=False))
        self.entry['sha256'] = sha256_bytes(self.data)
        (self.dir / self.file).write_bytes(self.data)
        self.assertTrue(any('no rx frame' in p for p in self.problems()))

    def test_banner_must_equal_the_table_banner_exactly(self):
        self.entry['banner'] = 'ETAP Interface V1.5'
        self.assertTrue(any('banner' in p for p in self.problems()))

    def test_bad_date_fails(self):
        self.entry['date'] = '12/10/2026'
        self.assertTrue(any('date' in p for p in self.problems()))

    def test_promotion_check_rejects_evidence_file_with_private_shape(self):
        planted = {
            'ip': '.'.join(['192', '168', '1', '77']),
            'e-mail': '@'.join(['probe', 'example.org']),
            'by-id serial': 'usb-' + 'FTDI_' + 'A1B2C3',
            'workspace path': '/' + 'Users' + '/someone/bridge',
        }
        for label, value in planted.items():
            with self.subTest(shape=label):
                record = sanitized_record()
                record['note'] = 'bridge at ' + value
                self.data = canonical(record)
                self.entry['sha256'] = sha256_bytes(self.data)
                (self.dir / self.file).write_bytes(self.data)
                found = self.problems()
                self.assertTrue(any('private shape' in p and 'note' in p for p in found), found)
                self.assertFalse(any(value in p for p in found))


class RealTable(unittest.TestCase):
    def test_original_verified_set_matches_catalog_today(self):
        self.assertEqual(len(ORIGINAL_VERIFIED), 30)
        self.assertEqual(ORIGINAL_VERIFIED | frozenset(VERIFIED_EVIDENCE), frozenset(n for n, e in CATALOG.items() if e.verified))
        self.assertEqual(ORIGINAL_VERIFIED | frozenset(VERIFIED_EVIDENCE), frozenset(LIVE_ACTIONS - {'identify'}))

    def test_every_promoted_command_has_evidence_hash_and_banner(self):
        verified = {n for n, e in CATALOG.items() if e.verified}
        literal_file = os.environ.get('BOSE_PRIVATE_LITERALS')
        literals = _lines(Path(literal_file)) if literal_file else []
        self.assertEqual(promotion_problems(verified, VERIFIED_EVIDENCE, ORIGINAL_VERIFIED, EVIDENCE_DIR,
                                            COMMANDS, expected_reply, literals), [])

    def test_evidence_files_are_sanitized(self):
        shapes = _shapes()
        brand = re.compile('|'.join(BRAND_WORDS), re.IGNORECASE)
        for path in sorted(EVIDENCE_DIR.glob('*.json')):
            with self.subTest(file=path.name):
                raw = path.read_bytes()
                data = json.loads(raw.decode('utf-8'))
                self.assertEqual(raw, canonical(data), 'not canonical JSON')
                self.assertLessEqual(set(data), ALLOWED_KEYS)
                self.assertNotIn('bridge_id', data)
                self.assertIn(data['outcome'], ('verified', 'reply_only'))
                self.assertRegex(data['timestamp'], r'^\d{4}-\d{2}-\d{2}$')
                for line in raw.decode('utf-8').splitlines():
                    self.assertFalse(any(s.search(line) for s in shapes), line)
                    self.assertIsNone(brand.search(line), line)


class Manifest(unittest.TestCase):
    def test_evidence_json_ships_and_other_dated_json_does_not(self):
        from public_manifest import is_excluded
        self.assertFalse(is_excluded('docs/evidence/room-b-source-fm-2026-10-12.json'))
        self.assertTrue(is_excluded('docs/room-b-source-fm-2026-10-12.json'))
        self.assertTrue(is_excluded('docs/evidence/nested/room-b-source-fm-2026-10-12.json'))


class Documentation(unittest.TestCase):
    def test_docs_commands_page_matches_catalog(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / 'gen_commands_doc.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unobservable_commands_stay_experimental_in_docs(self):
        directory = Path(tempfile.mkdtemp(prefix='reply-only-'))
        self.addCleanup(shutil.rmtree, directory, True)
        (directory / 'room-b-source-fm-2026-10-12.json').write_bytes(
            canonical(sanitized_record(name='room-b-source-fm', outcome='reply_only')))
        text = gen_commands_doc.render(CATALOG, {}, ORIGINAL_VERIFIED, directory)
        row = next(line for line in text.splitlines() if '`room-b-source-fm`' in line)
        self.assertIn('Experimental, reply captured only', row)
        self.assertNotIn('| Verified |', row)

    def test_room_d_marked_untested(self):
        text = gen_commands_doc.render(CATALOG, {}, ORIGINAL_VERIFIED, EVIDENCE_DIR)
        self.assertIn('untested hardware', text)
        rows = [line for line in text.splitlines() if '`room-d-' in line]
        self.assertTrue(rows)
        for line in rows:
            self.assertIn('Experimental', line)
            self.assertNotIn('| Verified |', line)

    def test_commands_doc_has_no_private_shapes(self):
        text = (ROOT / 'docs' / 'commands.md').read_text(encoding='utf-8')
        for line in text.splitlines():
            self.assertFalse(any(s.search(line) for s in _shapes()), line)
            self.assertFalse(any(word in line for word in BRAND_WORDS), line)
        self.assertIsNone(re.search(r'(?<![A-Za-z])' + 'P' + 'i' + r'(?![A-Za-z])', text))


@unittest.skipUnless(MAINTAINER_PRESENT, 'maintainer tree not in this archive')
class Sanitizer(unittest.TestCase):
    def setUp(self):
        self.sanitize = _load_sanitizer().sanitize

    def raw(self, **extra):
        record = {'name': PROMOTED, 'bytes_hex': 'aa', 'expected_reply': 'x', 'reply': 'y', 'classification': 'c',
                  'outcome': 'verified', 'timestamp': '2026-10-12T10:11:12Z', 'bridge_id': BRIDGE_ID,
                  'sensitive_fields': ['bridge_id'], 'raw': True, 'banner': BANNER, 'banner_expected': BANNER,
                  'zone': 'b', 'source': 'fm', 'catalog': 'experimental', 'note': 'setup', 'observation': 'audible change',
                  'status_before': {'mode': 'live', 'serial_open': True, 'config': 'ok', 'fault': None, 'recovery': 'x',
                                    'last_outcome': 'y', 'last_transmission': 'z'},
                  'status_after': {'mode': 'live', 'serial_open': True, 'config': 'ok', 'fault': None},
                  'frames': [{'t': 1.5, 'kind': 'tx', 'action': PROMOTED, 'detail': '4d'},
                             {'t': 1.6, 'kind': 'rx', 'action': PROMOTED, 'detail': '0d'}],
                  'path': '/' + 'Users' + '/someone/bridge', 'hostname': 'bridge-host'}
        record.update(extra)
        return record

    def test_sanitize_drops_bridge_id_and_paths_and_times(self):
        out = self.sanitize(self.raw())
        text = json.dumps(out)
        self.assertNotIn(BRIDGE_ID, text)
        self.assertNotIn('/Users', text)
        self.assertNotIn('bridge-host', text)
        self.assertEqual(out['timestamp'], '2026-10-12')
        self.assertNotIn('T10', text)
        for frame in out['frames']:
            self.assertEqual(set(frame), {'kind', 'action', 'detail'})
        self.assertEqual(set(out['status_before']), {'health', 'fault'})
        self.assertEqual(set(out) - ALLOWED_KEYS, set())
        self.assertNotIn('zone', out)
        self.assertNotIn('sensitive_fields', out)

    def test_sanitize_refuses_not_verified_outcome(self):
        for outcome in ('not_verified', 'unknown', 'pending', 'interrupted'):
            with self.subTest(outcome=outcome):
                with self.assertRaises(ValueError):
                    self.sanitize(self.raw(outcome=outcome))

    def test_sanitizer_main_refuses_without_writing_or_echoing(self):
        module = _load_sanitizer()
        planted = '/' + 'Users' + '/someone/bridge'
        with tempfile.TemporaryDirectory(prefix='san-') as directory:
            raw = Path(directory) / 'raw.json'
            out = Path(directory) / 'out.json'
            raw.write_text(json.dumps(self.raw(note='seen at ' + planted)), encoding='utf-8')
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = module.main([str(raw), '--out', str(out)])
            self.assertNotEqual(code, 0)
            self.assertFalse(out.exists())
            self.assertIn('note', buffer.getvalue())
            self.assertNotIn(planted, buffer.getvalue())

    def test_sanitize_keeps_reply_only_outcome(self):
        self.assertEqual(self.sanitize(self.raw(outcome='reply_only'))['outcome'], 'reply_only')

    def _planted(self, field, value):
        module = _load_sanitizer()
        with self.assertRaises(ValueError) as caught:
            module.sanitize(self.raw(**{field: value}))
        return str(caught.exception)

    def test_sanitizer_refuses_free_text_with_private_shapes(self):
        # Fragments only, so this file never holds the literal values.
        planted = {
            'ip': '.'.join(['192', '168', '1', '77']),
            'e-mail': '@'.join(['probe', 'example.org']),
            'by-id serial': 'usb-' + 'FTDI_' + 'A1B2C3',
            'home path': '/' + 'Users' + '/someone/bridge',
        }
        for label, value in planted.items():
            for field in ('note', 'observation'):
                with self.subTest(shape=label, field=field):
                    message = self._planted(field, 'before ' + value + ' after')
                    self.assertIn(field, message)
                    self.assertNotIn(value, message)

    def test_sanitizer_refuses_overlong_or_non_ascii_free_text(self):
        for field in ('note', 'observation', 'banner', 'reply'):
            with self.subTest(field=field, case='overlong'):
                self.assertIn(field, self._planted(field, 'a' * 201))
            with self.subTest(field=field, case='non-ascii'):
                self.assertIn(field, self._planted(field, 'caf' + 'é'))
            with self.subTest(field=field, case='control'):
                self.assertIn(field, self._planted(field, 'tab\there'))
        module = _load_sanitizer()
        self.assertEqual(module.sanitize(self.raw(note='a' * 200))['note'], 'a' * 200)

    def test_sanitizer_refuses_private_literal_when_list_given(self):
        module = _load_sanitizer()
        literal = 'zq' + 'kettle' + 'orchid'
        with tempfile.TemporaryDirectory(prefix='lit-') as directory:
            patterns = Path(directory) / 'literals.txt'
            patterns.write_text(literal + '\n', encoding='utf-8')
            with self.assertRaises(ValueError) as caught:
                module.sanitize(self.raw(note='seen ' + literal.upper() + ' here'), patterns=patterns)
            self.assertIn('note', str(caught.exception))
            self.assertNotIn(literal, str(caught.exception).lower())
            self.assertEqual(module.sanitize(self.raw(), patterns=patterns)['note'], 'setup')


if __name__ == '__main__':
    unittest.main()
