"""bose-lifestyle CLI change commands: live manage frames, offline ConfigStore commits, identify, Zone D gates.

Fake unix-socket bridge in a temp dir and temp state dirs only. Nothing here touches serial, IR or a service.
Run: python3 -B test_bose_cli_change.py
"""
import copy
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
import bose_cli  # noqa: E402
from bose_commands import COMMANDS, LIVE_MESSAGES  # noqa: E402
from helpers import BASELINE_CONFIG  # noqa: E402

OK = '{"ok":true,"result":{}}'


def error_reply(kind):
    return json.dumps({'ok': False, 'error': kind}, separators=(',', ':'))


def default_reply(name, body):
    return LIVE_MESSAGES['identify'] if name == 'identify' else OK


class ScriptedBridge:
    """Records every frame (action name, body) and answers with reply_for(name, body)."""

    def __init__(self, path, reply_for):
        self.reply_for = reply_for
        self.frames = []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(path)
        self.server.listen(8)
        self.server.settimeout(0.1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                with conn:
                    data = b''
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    _, action, body = data.split(b'\n', 2)
                    name = action.decode('ascii')
                    self.frames.append((name, body))
                    conn.sendall(self.reply_for(name, body).encode('ascii'))
            except (OSError, ValueError):
                pass        # the client went away early; the frame, if complete, is already recorded

    def names(self):
        return [name for name, _ in self.frames]

    def close(self):
        self.stop.set()
        try:
            self.thread.join(2)
        finally:
            self.server.close()


class ChangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()           # short system temp path; unix socket paths are length-limited
        self.state = os.path.join(self.tmp, 'state')
        os.mkdir(self.state)
        self.config_path = Path(self.state, 'config.json')
        write_config(self.config_path)
        self.sock = os.path.join(self.tmp, 'command.sock')
        self.bridge = None

    def tearDown(self):
        if self.bridge:
            self.bridge.close()
        for root, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_bridge(self, reply_for=default_reply):
        self.bridge = ScriptedBridge(self.sock, reply_for)

    def stop_bridge(self):
        self.bridge.close()
        self.bridge = None

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(list(argv), socket_path=self.sock, state_dir=self.state, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def frames(self):
        return [] if self.bridge is None else list(self.bridge.frames)

    def config_bytes(self):
        return self.config_path.read_bytes()

    def assert_nothing_sent(self):
        self.assertEqual(self.frames(), [])
        self.assertEqual(self.config_bytes(), self.before)


class ChangeCommandsTest(ChangeTest):
    def setUp(self):
        super().setUp()
        self.before = self.config_bytes()

    def test_zone_source_experimental_irtarget_live_send_exact_manage_payloads(self):
        self.start_bridge()
        cases = [
            (('zone', 'a', 'on'), {'op': 'set_zone', 'zone': 'a', 'enabled': True}),
            (('zone', 'c', 'off'), {'op': 'set_zone', 'zone': 'c', 'enabled': False}),
            (('zone', 'd', 'off'), {'op': 'set_zone', 'zone': 'd', 'enabled': False}),
            (('source', 'video-1', 'on'), {'op': 'set_source', 'source': 'video-1', 'connected': True}),
            (('source', 'tape', 'off'), {'op': 'set_source', 'source': 'tape', 'connected': False}),
            (('experimental', 'b', 'on'), {'op': 'set_unverified', 'zone': 'b', 'allow': True}),
            (('experimental', 'b', 'off'), {'op': 'set_unverified', 'zone': 'b', 'allow': False}),
            (('ir', 'target', 'd'), {'op': 'set_ir_zone', 'zone': 'd'}),
        ]
        for argv, payload in cases:
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 0, (argv, err))
        expected = [('manage', json.dumps(payload, separators=(',', ':')).encode('utf-8')) for _, payload in cases]
        self.assertEqual(self.frames(), expected)
        self.assertEqual(self.config_bytes(), self.before)      # live: the bridge writes, the CLI does not

    def test_volume_steps_sends_set_volume_steps_and_validates_the_range(self):
        self.start_bridge()
        code, out, err = self.run_cli('volume-steps', '3')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.frames(), [('manage', b'{"op":"set_volume_steps","steps":3}')])
        before = list(self.frames())
        for bad in ('0', '6', 'x', '2.5', '-1'):
            code, out, err = self.run_cli('volume-steps', bad)
            self.assertEqual(code, 2, bad)
            self.assertNotIn('Traceback', err)
        self.assertEqual(self.frames(), before, 'out-of-range values are never sent')

    def test_volume_steps_offline_commits_and_back_to_one_removes_the_key(self):
        code, out, err = self.run_cli('volume-steps', '4')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.config_bytes())['volume_steps'], 4)
        code, out, err = self.run_cli('status')
        self.assertIn('Volume steps per press: 4', out)
        code, out, err = self.run_cli('volume-steps', '1')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.config_bytes()), BASELINE_CONFIG, 'the default is not written')

    def test_offline_uses_configstore_commit_path(self):
        real_replace = os.replace
        with mock.patch('os.replace', side_effect=real_replace) as replace:
            code, out, err = self.run_cli('zone', 'c', 'off')
        self.assertEqual(code, 0, err)
        self.assertEqual(replace.call_count, 1)                  # one commit, no retry
        expected = copy.deepcopy(BASELINE_CONFIG)
        expected['zones']['c']['enabled'] = False
        self.assertEqual(json.loads(self.config_bytes()), expected)
        self.assertEqual(out.strip(), bose_cli.MESSAGES['saved_offline'])
        self.assertIn('It applies when the bridge next starts.', out)

    def test_uncertain_commit_reports_uncertain_and_exit_1_without_retry(self):
        real_fsync = os.fsync

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('directory fsync failed')
            return real_fsync(fd)
        real_replace = os.replace
        with mock.patch('os.fsync', side_effect=fail_directory_fsync), \
                mock.patch('os.replace', side_effect=real_replace) as replace:
            code, out, err = self.run_cli('zone', 'c', 'off')
        self.assertEqual(code, 1)
        self.assertEqual(replace.call_count, 1)                  # the rename happened once; never repeated
        self.assertEqual(err.strip(), bose_cli.MESSAGES['reply_uncertain'])
        self.assertFalse(json.loads(self.config_bytes())['zones']['c']['enabled'])
        self.assertEqual(out, '')

    def test_not_saved_reports_nothing_changed(self):
        if os.geteuid() == 0:
            self.skipTest('root ignores directory permissions')
        os.chmod(self.state, 0o500)                              # readable, not writable: no temp file
        try:
            code, out, err = self.run_cli('source', 'tape', 'on')
        finally:
            os.chmod(self.state, 0o700)
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), bose_cli.MESSAGES['reply_not_saved'])
        self.assertEqual(self.config_bytes(), self.before)

    def test_reply_kinds_map_to_plain_messages_exit_1(self):
        self.start_bridge(lambda name, body: error_reply('dry_run'))
        for kind in ('invalid_request', 'dry_run', 'not_saved', 'uncertain', 'unsupported'):
            self.bridge.reply_for = (lambda k: lambda name, body: error_reply(k))(kind)
            code, out, err = self.run_cli('zone', 'b', 'on')
            self.assertEqual(code, 1, kind)
            self.assertEqual(err.strip(), bose_cli.MESSAGES['reply_' + kind], kind)
        self.bridge.reply_for = default_reply
        code, out, err = self.run_cli('zone', 'b', 'on')
        self.assertEqual((code, out.strip(), err), (0, bose_cli.MESSAGES['saved'], ''))

    def test_invalid_argument_exit_2_nothing_sent(self):
        self.start_bridge()
        for argv in (('zone', 'e', 'on'), ('zone', 'a', 'maybe'), ('source', 'phono', 'on'),
                     ('source', 'video-3', 'on'), ('experimental', 'z', 'on'), ('ir', 'target', 'x'),
                     ('ir',)):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertNotIn('Traceback', err)
        self.assert_nothing_sent()

    def test_local_validation_blocks_send(self):
        self.start_bridge()
        from bose_ir import IRException
        with mock.patch.object(bose_cli, 'validate_management', side_effect=IRException('invalid_request')):
            code, out, err = self.run_cli('zone', 'a', 'on')
        self.assertEqual(code, 2)
        self.assertEqual(err.strip(), bose_cli.MESSAGES['reply_invalid_request'])
        self.assert_nothing_sent()

    def test_identify_requires_yes_and_is_only_transmission(self):
        self.start_bridge()
        code, out, err = self.run_cli('identify')
        self.assertEqual(code, 2)
        self.assertIn('--yes', err)
        self.assertEqual(self.frames(), [])
        change_runs = [
            ('zone', 'a', 'on'), ('zone', 'a', 'off'), ('zone', 'd', 'on', '--yes-i-understand'),
            ('source', 'am', 'on'), ('experimental', 'a', 'on'), ('experimental', 'd', 'on', '--yes-i-understand'),
            ('ir', 'target', 'b'),
        ]
        for argv in change_runs:
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 0, (argv, err))
        names = set(self.bridge.names())
        self.assertEqual(names - {'manage'}, set())               # no Bose command name, only manage ops
        self.assertFalse(names & set(COMMANDS))
        code, out, err = self.run_cli('identify', '--yes')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.bridge.names().count('identify'), 1)
        self.assertEqual(self.bridge.names()[-1], 'identify')
        self.assertEqual(out.strip(), LIVE_MESSAGES['identify'])

    def test_identify_offline_refuses(self):
        code, out, err = self.run_cli('identify', '--yes')       # no socket: bridge not running
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), bose_cli.MESSAGES['identify_offline'])
        self.assertFalse(os.path.exists(self.sock))
        self.assertEqual(self.config_bytes(), self.before)

    def test_zone_d_and_experimental_d_need_extra_flag(self):
        self.start_bridge()
        for argv in (('zone', 'd', 'on'), ('experimental', 'd', 'on')):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn('unverified and experimental', err)
        self.assert_nothing_sent()
        self.stop_bridge()
        for argv in (('zone', 'd', 'on'), ('experimental', 'd', 'on')):   # offline gate is the same
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
        self.assertEqual(self.config_bytes(), self.before)
        code, out, err = self.run_cli('zone', 'd', 'on', '--yes-i-understand')
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(self.config_bytes())['zones']['d']['enabled'])
        code, out, err = self.run_cli('zone', 'd', 'off')        # turning D off needs no flag
        self.assertEqual(code, 0, err)

    def test_experimental_a_to_c_on_warns(self):
        code, out, err = self.run_cli('experimental', 'a', 'on')
        self.assertEqual(code, 0, err)
        self.assertIn('may not do what the label says', err)
        self.assertEqual(err.strip().count('\n'), 0)             # one line

    def test_ir_target_change_prints_mute_state_forgotten(self):
        forgotten = bose_cli.MESSAGES['ir_target_forgets_mute']
        code, out, err = self.run_cli('ir', 'target', 'b')       # offline
        self.assertEqual(code, 0, err)
        self.assertIn(forgotten, out)
        self.start_bridge()
        code, out, err = self.run_cli('ir', 'target', 'c')       # live
        self.assertEqual(code, 0, err)
        self.assertIn(forgotten, out)

    def test_cli_never_restarts_service(self):
        self.start_bridge()
        with mock.patch('subprocess.run') as run, mock.patch('subprocess.Popen') as popen, \
                mock.patch('os.system') as system, mock.patch('os.execv') as execv, \
                mock.patch('os.execvp') as execvp:
            for argv in (('zone', 'a', 'off'), ('source', 'tape', 'on'), ('experimental', 'c', 'on'),
                         ('ir', 'target', 'a'), ('identify', '--yes')):
                code, out, err = self.run_cli(*argv)
                self.assertEqual(code, 0, (argv, err))
            self.stop_bridge()
            code, out, err = self.run_cli('zone', 'a', 'on')     # offline commit path too
            self.assertEqual(code, 0, err)
        for fake in (run, popen, system, execv, execvp):
            self.assertEqual(fake.call_count, 0)


if __name__ == '__main__':
    unittest.main()
