"""Explicit recovery for a damaged ir-state.json: check, restore and reset. Offline only; never deletes."""
import _bootstrap
import io
import json
import os
import sys
import tempfile
import threading
import socket
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bose_cli  # noqa: E402
import bose_state  # noqa: E402

IDENTITY = 'b' * 32
GOOD = {'schema': 2, 'bridge_id': IDENTITY, 'mute_state': None,
        'profiles': {'c' * 32: {'name': 'Test remote', 'model': 'ir-remote', 'active': True,
                                'mappings': {'volume-up': {'code': 101, 'enabled': True},
                                             'volume-down': {'code': 102, 'enabled': True},
                                             'mute': {'code': 103, 'enabled': True}}}}}
DAMAGED = ('{"schema": 2, "bridge_id": "' + IDENTITY + '", "profiles": {').encode()


def run(args, state_dir, socket_path=None):
    out, err = io.StringIO(), io.StringIO()
    code = bose_cli.main(args, state_dir=str(state_dir), socket_path=socket_path or str(state_dir) + '/none.sock', out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class State(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / 'ir-state.json'

    def tearDown(self):
        self.tmp.cleanup()

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_check_reports_each_state_and_changes_nothing(self):
        code, out, _ = run(['state', 'check'], self.dir)
        self.assertEqual(code, 0); self.assertIn('no saved remote state', out)
        self.path.write_text(json.dumps(GOOD))
        before = self.path.read_bytes()
        code, out, _ = run(['state', 'check'], self.dir)
        self.assertEqual(code, 0); self.assertIn('Remotes saved: 1', out)
        self.path.write_bytes(DAMAGED)
        code, out, err = run(['state', 'check'], self.dir)
        self.assertEqual(code, 1); self.assertIn('not-json', err); self.assertIn('can still be read', err)
        self.assertNotIn(IDENTITY, out + err)                       # the identity itself is never printed
        self.assertEqual(self.path.read_bytes(), DAMAGED)
        self.assertEqual(self.names(), ['ir-state.json'])
        self.assertEqual(before, json.dumps(GOOD).encode())

    def test_reset_keep_identity_preserves_damaged_bytes_and_identity(self):
        self.path.write_bytes(DAMAGED)
        code, _, err = run(['state', 'reset', '--keep-identity'], self.dir)
        self.assertEqual(code, 2); self.assertIn('--yes', err)       # nothing without --yes
        self.assertEqual(self.path.read_bytes(), DAMAGED)
        code, out, err = run(['state', 'reset', '--keep-identity', '--yes'], self.dir)
        self.assertEqual(code, 0, err)
        data = json.loads(self.path.read_text())
        self.assertEqual(data, {'schema': 2, 'bridge_id': IDENTITY, 'profiles': {}, 'mute_state': None})
        copies = [p for p in self.dir.iterdir() if '.damaged-' in p.name]
        self.assertEqual(len(copies), 1); self.assertEqual(copies[0].read_bytes(), DAMAGED)
        self.assertEqual(oct(copies[0].stat().st_mode & 0o777), '0o600')

    def test_reset_keep_identity_refused_when_identity_unreadable(self):
        self.path.write_bytes(b'\x00\x01 not json at all')
        code, _, err = run(['state', 'reset', '--keep-identity', '--yes'], self.dir)
        self.assertEqual(code, 1); self.assertIn('identity-not-recoverable', err)
        self.assertEqual(self.path.read_bytes(), b'\x00\x01 not json at all')
        self.assertEqual(self.names(), ['ir-state.json'])

    def test_damaged_byte_inside_the_identity_is_not_skipped(self):
        # A corrupt byte inside the id must not be dropped to forge a valid-looking identity.
        self.path.write_bytes(b'{"schema": 2, "bridge_id": "' + b'a' * 16 + b'\xff' + b'b' * 16 + b'", "profiles": {')
        self.assertFalse(bose_state.inspect(self.path)['identity_recoverable'])
        code, _, err = run(['state', 'reset', '--keep-identity', '--yes'], self.dir)
        self.assertEqual(code, 1); self.assertIn('identity-not-recoverable', err)
        self.assertEqual(self.names(), ['ir-state.json'])

    def test_failed_install_after_preserving_a_link_leaves_the_state_in_place(self):
        target = self.dir / 'damaged.json'
        target.write_text('{"bridge_id":"' + IDENTITY + '","profiles":{')
        os.symlink('damaged.json', self.path)
        from unittest import mock
        with mock.patch.object(bose_state, '_install', side_effect=OSError(28, 'disk full')):
            with self.assertRaises(OSError):
                bose_state.reset(self.path, keep_identity=True)
        self.assertTrue(self.path.is_symlink(), 'the original entry must stay until the install succeeds')
        from bose_ir import RemoteManager, IRStateError
        with self.assertRaises(IRStateError):
            RemoteManager(self.path)                                    # startup still stops; no new identity

    def test_dangling_state_link_is_damaged_not_a_first_install(self):
        os.symlink(self.dir / 'moved-away.json', self.path)
        state = bose_state.inspect(self.path)
        self.assertTrue(state['exists']); self.assertFalse(state['ok'])
        from bose_ir import RemoteManager, IRStateError
        with self.assertRaises(IRStateError):
            RemoteManager(self.path)
        self.assertTrue(self.path.is_symlink())                       # never replaced by a fresh identity
        code, _, err = run(['state', 'reset', '--new-identity', '--yes'], self.dir)   # explicit recovery keeps the link aside
        self.assertEqual(code, 0, err)
        self.assertFalse(self.path.is_symlink()); self.assertTrue(self.path.is_file())
        moved = [p for p in self.dir.iterdir() if '.damaged-' in p.name]
        self.assertEqual(len(moved), 1); self.assertTrue(moved[0].is_symlink())

    def test_two_different_identities_are_not_trusted(self):
        text = ('{"bridge_id": "' + 'a' * 32 + '", "bridge_id": "' + 'b' * 32 + '"').encode()
        self.path.write_bytes(text)
        code, _, err = run(['state', 'reset', '--keep-identity', '--yes'], self.dir)
        self.assertEqual(code, 1); self.assertIn('identity-not-recoverable', err)

    def test_nested_identity_is_not_trusted(self):
        self.path.write_bytes(b'{"profiles":{"bridge_id":"' + b'a' * 32 + b'"}}')
        self.assertIsNone(bose_state._raw_identity(self.path))

    def test_nested_conflicting_identity_does_not_override_top_level(self):
        self.path.write_bytes(b'{"bridge_id":"' + b'b' * 32 + b'","profiles":{"bridge_id":"' + b'a' * 32 + b'"')
        self.assertEqual(bose_state._raw_identity(self.path), IDENTITY)

    def test_malformed_intervening_value_does_not_hide_conflicting_top_level_identity(self):
        self.path.write_bytes(b'{"bridge_id":"' + IDENTITY.encode() + b'","profiles":,"bridge_id":"' + b'a' * 32 + b'"}')
        self.assertIsNone(bose_state._raw_identity(self.path))

    def test_duplicate_top_level_identity_keys_are_not_trusted_even_if_equal(self):
        self.path.write_bytes(b'{"bridge_id":"' + b'b' * 32 + b'","bridge_id":"' + b'b' * 32 + b'"')
        self.assertIsNone(bose_state._raw_identity(self.path))

    def test_top_level_identity_survives_truncated_trailing_json(self):
        self.path.write_bytes(b'{"bridge_id":"' + IDENTITY.encode() + b'","profiles":{"remote":')
        self.assertEqual(bose_state._raw_identity(self.path), IDENTITY)

    def test_top_level_identity_survives_incomplete_json_literal_prefix(self):
        for prefix in (b'n', b'nu', b'nul', b't', b'tr', b'tru', b'f', b'fa', b'fal', b'fals'):
            with self.subTest(prefix=prefix):
                self.path.write_bytes(b'{"bridge_id":"' + IDENTITY.encode() + b'","mute_state":' + prefix)
                self.assertEqual(bose_state._raw_identity(self.path), IDENTITY)

    def test_deep_json_is_unrecoverable_without_crashing_inspect_or_reset(self):
        nested = b'[' * 1200 + b']' * 1200
        self.path.write_bytes(b'{"bridge_id":"' + IDENTITY.encode() + b'","profiles":' + nested + b'}')
        state = bose_state.inspect(self.path)
        self.assertFalse(state['ok'])
        self.assertFalse(state['identity_recoverable'])
        code, _, err = run(['state', 'reset', '--keep-identity', '--yes'], self.dir)
        self.assertEqual(code, 1)
        self.assertIn('identity-not-recoverable', err)
        self.assertEqual(self.path.read_bytes(), b'{"bridge_id":"' + IDENTITY.encode() + b'","profiles":' + nested + b'}')

    def test_reset_new_identity_is_explicit_and_different(self):
        self.path.write_bytes(DAMAGED)
        code, _, err = run(['state', 'reset', '--new-identity', '--yes'], self.dir)
        self.assertEqual(code, 0, err)
        data = json.loads(self.path.read_text())
        self.assertNotEqual(data['bridge_id'], IDENTITY); self.assertEqual(len(data['bridge_id']), 32)
        self.assertEqual(data['profiles'], {})
        self.assertEqual(len([p for p in self.dir.iterdir() if '.damaged-' in p.name]), 1)

    def test_restore_from_backup_validates_and_preserves(self):
        backup = self.dir / 'backup.json'
        backup.write_text(json.dumps(GOOD))
        self.path.write_bytes(DAMAGED)
        code, _, err = run(['state', 'restore', '--from', str(backup), '--yes'], self.dir)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.path.read_text()), GOOD)
        self.assertEqual([p.read_bytes() for p in self.dir.iterdir() if '.damaged-' in p.name], [DAMAGED])

    def test_restore_refuses_a_bad_backup_and_changes_nothing(self):
        backup = self.dir / 'backup.json'
        backup.write_bytes(b'{')
        self.path.write_bytes(DAMAGED)
        code, _, err = run(['state', 'restore', '--from', str(backup), '--yes'], self.dir)
        self.assertEqual(code, 1); self.assertIn('backup-not-json', err)
        self.assertEqual(self.path.read_bytes(), DAMAGED)
        self.assertEqual(self.names(), ['backup.json', 'ir-state.json'])

    def test_refuses_when_state_is_fine_or_missing(self):
        self.path.write_text(json.dumps(GOOD))
        code, _, err = run(['state', 'reset', '--new-identity', '--yes'], self.dir)
        self.assertEqual(code, 1); self.assertIn('not damaged', err)
        self.assertEqual(json.loads(self.path.read_text()), GOOD)
        self.path.unlink()
        code, _, err = run(['state', 'reset', '--new-identity', '--yes'], self.dir)
        self.assertEqual(code, 1); self.assertIn('no-state-file', err)

    def test_refuses_while_a_bridge_answers(self):
        self.path.write_bytes(DAMAGED)
        sock = str(self.dir) + '/live.sock'
        server = socket.socket(socket.AF_UNIX); server.bind(sock); server.listen(1)
        def answer():
            conn, _ = server.accept()
            conn.recv(256)
            conn.close()
        thread = threading.Thread(target=answer, daemon=True); thread.start()
        try:
            code, _, err = run(['state', 'reset', '--keep-identity', '--yes'], self.dir, sock)
        finally:
            server.close()
        self.assertEqual(code, 1)
        self.assertEqual(self.path.read_bytes(), DAMAGED)

    def test_state_without_subcommand_is_usage(self):
        code, _, err = run(['state'], self.dir)
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
