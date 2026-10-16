"""bose-lifestyle CLI read commands: live socket front end and offline saved-config front end.

Fake unix-socket bridge in a temp dir and temp state dirs only; no serial, no IR, no commands sent.
Run: python3 -B test_bose_cli.py
"""
import copy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
import bose_cli  # noqa: E402
from bose_config import default_config  # noqa: E402
from helpers import BASELINE_CONFIG  # noqa: E402

BRIDGE_ID = 'a' * 32
SETTINGS = ('zones', 'sources', 'unverified', 'ir')


def fake_status(config=BASELINE_CONFIG, fault=None, config_state='ok', last_transmission=None):
    """A schema 4 status reply the shared validator accepts."""
    health = {'mode': 'live', 'serial_open': False, 'fault': fault, 'recovery': None, 'ir_configured': True,
              'ir_attached': False, 'last_outcome': None, 'last_transmission': last_transmission, 'config': config_state}
    room = {'value': None, 'observed_at': None, 'age': None, 'supported': False}
    rooms = {zone: {'power': dict(room), 'source': dict(room), 'volume': dict(room), 'mute': dict(room)}
             for zone in 'abcd'}
    actions = {'volume-down': {'zone': 'a', 'function': 'volume-down', 'source': None, 'verified': True,
                               'alias': False, 'allowed': fault is None}}
    settings = None if config_state != 'ok' else {k: copy.deepcopy(config[k]) for k in SETTINGS}
    if settings is None:
        actions = {}                                             # the validator: no config means no actions
    return json.dumps({'schema': 4, 'bridge_id': BRIDGE_ID, 'generated_at': 1.0e9, 'bridge': health,
                       'config': settings, 'actions': actions,
                       'ir': {'profiles': {}, 'learning': None, 'mute_state': None}, 'rooms': rooms},
                      separators=(',', ':'))


class FakeBridge:
    """Answers one frame per connection with the reply for its action name; records what it received."""

    def __init__(self, path, replies):
        self.replies = replies
        self.received = []
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
                    self.received.append(data)
                    name = data.split(b'\n', 2)[1].decode('ascii')
                    conn.sendall(self.replies[name].encode('ascii'))
            except (OSError, ValueError, IndexError):
                pass        # the client went away early; the frame, if complete, is already recorded

    def close(self):
        self.stop.set()
        try:
            self.thread.join(2)
        finally:
            self.server.close()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()           # short system temp path; unix socket paths are length-limited
        self.state = os.path.join(self.tmp, 'state')
        os.mkdir(self.state)
        write_config(Path(self.state, 'config.json'))
        self.sock = os.path.join(self.tmp, 'command.sock')
        self.bridge = None

    def tearDown(self):
        if self.bridge:
            self.bridge.close()
        for root, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_bridge(self, replies):
        self.bridge = FakeBridge(self.sock, replies)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = bose_cli.main(list(argv), socket_path=self.sock, state_dir=self.state, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_status_prints_configuration_and_last_command_without_claiming_room_state(self):
        transmission = {'action': 'room-b-volume-up', 'outcome': 'transmitted-with-matching-response', 'observed_at': 1.0e9}
        self.start_bridge({'status': fake_status(last_transmission=transmission)})
        code, out, err = self.run_cli('status')
        self.assertEqual(code, 0, err)
        self.assertIn('Bridge: running (live mode)', out)
        self.assertNotIn('Zones:', out)
        self.assertNotIn('Sources:', out)
        self.assertIn('Last command sent: room-b-volume-up (transmitted-with-matching-response)', out)
        self.assertIn('IR target: zone A', out)
        self.assertEqual(self.bridge.received[0].split(b'\n', 2)[1], b'status')

    def test_config_show_matches_config_json(self):
        saved = json.loads(Path(self.state, 'config.json').read_text())
        code, out, err = self.run_cli('config', 'show')                  # bridge not running: offline
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {k: saved[k] for k in SETTINGS})
        self.start_bridge({'status': fake_status()})                      # live: same settings
        code, out, err = self.run_cli('config', 'show')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {k: saved[k] for k in SETTINGS})

    def test_events_prints_recent_lines_bounded(self):
        lines = [json.dumps({'t': 1.0e9 + i, 'kind': 'start', 'action': None, 'detail': 'event %d' % i},
                            separators=(',', ':')) for i in range(300)]
        Path(self.state, 'events.jsonl').write_text('\n'.join(lines) + '\n')
        code, out, err = self.run_cli('events', '--count', '5')
        self.assertEqual(code, 0, err)
        shown = [ln for ln in out.splitlines() if 'event ' in ln]
        self.assertEqual(len(shown), 5)
        self.assertTrue(shown[-1].endswith('event 299'))
        code, out, err = self.run_cli('events')                           # default 20
        self.assertEqual(len([ln for ln in out.splitlines() if 'event ' in ln]), 20)
        for bad in ('0', '201', 'many'):
            code, out, err = self.run_cli('events', '--count', bad)
            self.assertEqual(code, 2)
            self.assertNotIn('Traceback', err)

    def test_fault_clear_uses_bridge_and_fails_without_offline_fallback(self):
        self.start_bridge({'fault-clear': 'fault cleared'})
        code, out, err = self.run_cli('fault', 'clear')
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.bridge.received), 1)
        self.assertEqual(self.bridge.received[0].split(b'\n', 2)[1], b'fault-clear')
        self.assertIn('M1 communication was not tested', out)
        self.assertEqual(err, '')

    def test_fault_clear_does_not_fall_back_when_bridge_is_missing(self):
        code, out, err = self.run_cli('fault', 'clear')
        self.assertEqual(code, 1)
        self.assertEqual(out, '')
        self.assertEqual(err.strip(), bose_cli.MESSAGES['cant_reach'])

    def test_socket_missing_falls_back_offline_and_says_so(self):
        code, out, err = self.run_cli('status')                          # no socket file at all
        self.assertEqual(code, 0, err)
        self.assertEqual(err.strip(), bose_cli.MESSAGES['not_running'])
        self.assertNotIn('Zones:', out)
        self.assertNotIn('Sources:', out)
        self.assertIn('Last command sent: unavailable (bridge stopped)', out)

    def test_permission_denied_is_one_plain_line_no_traceback_nonzero_exit(self):
        if os.geteuid() == 0:
            self.skipTest('root ignores directory permissions')
        locked = os.path.join(self.tmp, 'locked')
        os.mkdir(locked)
        real = os.path.join(locked, 'command.sock')
        os.chmod(locked, 0o000)
        self.sock = real
        code, out, err = self.run_cli('status')
        self.assertEqual(code, 1)
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(lines, ["Can't reach the bridge: run this as the bridge's user or ask the installer"])
        self.assertNotIn('Traceback', err)
        self.assertEqual(out, '')

    def test_unreadable_saved_state_with_no_socket_is_cant_reach_not_missing_config(self):
        if os.geteuid() == 0:
            self.skipTest('root ignores directory permissions')
        os.chmod(self.state, 0o000)
        code, out, err = self.run_cli('status')
        self.assertEqual(code, 1)
        self.assertEqual([ln for ln in err.splitlines() if ln.strip()], [bose_cli.MESSAGES['cant_reach']])
        self.assertEqual(out, '')

    def test_offline_identify_refuses_with_plain_message_and_sends_nothing(self):
        backend = bose_cli.OfflineBackend(self.state)
        with self.assertRaises(bose_cli.NotRunning) as caught:
            backend.identify()
        self.assertEqual(str(caught.exception), bose_cli.MESSAGES['identify_offline'])
        self.assertFalse(os.path.exists(self.sock))

    def test_no_args_prints_usage_exit_2(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn('Usage', err)
        self.assertNotIn('Traceback', err)

    def test_unexpected_error_is_one_plain_line_exit_1(self):
        original = bose_cli.OfflineBackend.status

        def broken(self):
            raise RuntimeError('boom')
        bose_cli.OfflineBackend.status = broken
        try:
            code, out, err = self.run_cli('status')
        finally:
            bose_cli.OfflineBackend.status = original
        self.assertEqual(code, 1)
        self.assertEqual([ln for ln in err.splitlines() if ln.strip()],
                         [bose_cli.MESSAGES['not_running'], bose_cli.MESSAGES['failed']])
        self.assertNotIn('Traceback', err)

    def test_cli_never_writes_in_read_commands(self):
        Path(self.state, 'events.jsonl').write_text('{"t":1.0,"kind":"start","action":null,"detail":"x"}\n')

        def snapshot():
            state = {}
            for name in sorted(os.listdir(self.state)):
                path = os.path.join(self.state, name)
                st = os.stat(path)
                state[name] = (Path(path).read_bytes(), st.st_mtime_ns)
            return state
        before = snapshot()
        self.run_cli('status')
        self.run_cli('config', 'show')
        self.run_cli('events')
        self.start_bridge({'status': fake_status(), 'events': json.dumps({'events': []})})
        self.run_cli('status')
        self.run_cli('events')
        self.assertEqual(snapshot(), before)
        self.assertEqual(sorted(os.listdir(self.state)), ['config.json', 'events.jsonl'])

    def test_launcher_runs_with_help(self):
        launcher = ROOT / 'bin' / 'bose-lifestyle'
        self.assertTrue(os.access(launcher, os.X_OK))
        proc = subprocess.run([sys.executable, '-B', str(launcher), '--help'], cwd=self.tmp,
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(re.search(r'\bPi\b', proc.stdout))
        self.assertIn('status', proc.stdout)


class InitTests(unittest.TestCase):
    """Missing-config commands. Borrows CliTest's temp dirs, fake bridge and run_cli without inheriting its tests."""

    def setUp(self):
        CliTest.setUp(self)
        os.unlink(os.path.join(self.state, 'config.json'))
        self.config = Path(self.state, 'config.json')

    tearDown = CliTest.tearDown
    start_bridge = CliTest.start_bridge
    run_cli = CliTest.run_cli

    def test_cli_init_creates_then_refuses_second_time(self):
        code, out, err = self.run_cli('init')
        self.assertEqual(code, 0, err)
        self.assertIn('Turn on a room: bose-lifestyle zone a on', out)
        self.assertIn('Say which inputs are connected: bose-lifestyle source aux on', out)
        self.assertEqual(json.loads(self.config.read_bytes()), default_config())
        before = self.config.read_bytes()
        code, out, err = self.run_cli('init')
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'A configuration already exists. Nothing was changed.')
        self.assertEqual(self.config.read_bytes(), before)

    def test_cli_init_when_bridge_running_changes_nothing(self):
        self.start_bridge({'status': fake_status()})               # a running bridge with a valid config
        code, out, err = self.run_cli('init')
        self.assertEqual(code, 0, err)
        self.assertIn('Nothing was changed', out)
        self.assertFalse(self.config.exists())
        self.assertEqual(len(self.bridge.received), 1)
        self.assertIn(b'status', self.bridge.received[0])

    def test_init_with_running_bridge_that_has_no_config_creates_and_says_restart_first(self):
        self.start_bridge({'status': fake_status(config_state='config-invalid')})
        code, out, err = self.run_cli('init')
        self.assertEqual(code, 0, err)
        self.assertTrue(self.config.exists())
        self.assertEqual(json.loads(self.config.read_bytes()), default_config())
        self.assertIn('It applies when the bridge next starts. After that:', out)
        self.assertLess(out.index('It applies when'), out.index('Turn on a room'))
        self.assertEqual(len(self.bridge.received), 1)
        self.assertIn(b'status', self.bridge.received[0])

    def test_status_and_config_show_hint_when_missing(self):
        code, out, err = self.run_cli('status')
        self.assertEqual(code, 0, err)
        self.assertIn('No configuration yet. Run: bose-lifestyle init', out)
        code, out, err = self.run_cli('config', 'show')
        self.assertEqual(code, 1)
        self.assertIn('No configuration yet. Run: bose-lifestyle init', out)
        self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()
