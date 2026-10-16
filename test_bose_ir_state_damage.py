"""A damaged ir-state.json must stop the bridge cleanly: exit 20 (the unit does not restart on it), no socket,
the file untouched, and no replacement identity. A missing file is still a normal first start."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
from bose_ir import IRException, IRStateError, RemoteManager  # noqa: E402

GOOD = {'schema': 2, 'bridge_id': 'b' * 32, 'mute_state': None,
        'profiles': {'c' * 32: {'name': 'Test remote', 'model': 'ir-remote', 'active': True,
                                'mappings': {'volume-up': {'code': 101, 'enabled': True}}}}}
DAMAGE = {
    'empty': ('', 'not-json'),
    'truncated': ('{"schema": 2, "bridge_id": "' + 'b' * 32, 'not-json'),
    'binary': ('\x00\x01\x02', 'not-json'),
    'wrong-schema': (json.dumps({**GOOD, 'schema': 9}), 'bad-shape'),
    'extra-key': (json.dumps({**GOOD, 'extra': 1}), 'bad-shape'),
    'bad-identity': (json.dumps({**GOOD, 'bridge_id': 'z'}), 'bad-identity'),
    'bad-profiles': (json.dumps({**GOOD, 'profiles': {'c' * 32: {'name': 'x'}}}), 'bad-profiles'),
}
FAKE = r'''
import sys,types
sys.modules['serial']=types.SimpleNamespace(Serial=lambda **k: None)
sys.modules['evdev']=None
from bose_bridge import serve
serve(sys.argv[1],False,None,sys.argv[2],serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


def run_bridge(tmp):
    return subprocess.run([sys.executable, '-u', '-c', FAKE, tmp + '/command.sock', tmp + '/ir-state.json'],
                          cwd=ROOT, capture_output=True, text=True, timeout=10)


def test_manager_names_the_damage_and_never_writes():
    for name, (text, reason) in DAMAGE.items():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'ir-state.json')
            path.write_bytes(text.encode('utf-8'))
            try: RemoteManager(path)
            except IRStateError as error:
                assert error.reason == reason, (name, error.reason)
                assert isinstance(error, IRException)            # existing callers keep working
            else: raise AssertionError(name)
            assert path.read_bytes() == text.encode('utf-8'), name
    print('Every kind of damage is named and the file is never touched by loading it')


def test_bridge_exits_20_without_a_socket_and_leaves_the_file():
    for name, (text, reason) in DAMAGE.items():
        with tempfile.TemporaryDirectory() as tmp:
            write_config(Path(tmp, 'config.json'), {'schema': 1, 'zones': {z: {'enabled': False} for z in 'abcd'},
                         'sources': {s: False for s in ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')},
                         'unverified': {z: False for z in 'abcd'}, 'ir': {'zone': 'a'}})
            path = Path(tmp, 'ir-state.json')
            path.write_bytes(text.encode('utf-8'))
            done = run_bridge(tmp)
            assert done.returncode == 20, (name, done.returncode, done.stderr[-400:])
            assert not os.path.exists(tmp + '/command.sock'), name
            assert path.read_bytes() == text.encode('utf-8'), name       # preserved byte for byte
            assert sorted(p.name for p in Path(tmp).iterdir() if 'ir-state' in p.name) == ['ir-state.json'], name
            output = done.stdout + done.stderr
            assert reason in output and 'ir-state.json' in output and 'bose-lifestyle state' in output, (name, output)
            assert 'b' * 32 not in output and 'Traceback' not in output, name   # no identity or stack trace in the log
    print('A damaged state file ends in exit 20 with no socket, the file preserved and a recovery hint')


def test_missing_file_is_still_a_first_start():
    with tempfile.TemporaryDirectory() as tmp:
        manager = RemoteManager(Path(tmp, 'ir-state.json'))
        assert len(manager.bridge_id) == 32 and manager.profiles == {}
    print('A missing state file is a normal first start')


if __name__ == '__main__':
    test_manager_names_the_damage_and_never_writes()
    test_bridge_exits_20_without_a_socket_and_leaves_the_file()
    test_missing_file_is_still_a_first_start()
