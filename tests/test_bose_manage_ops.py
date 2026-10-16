"""Manage ops: validation, bridge dispatch, commit-error replies, dry-run refusal, IR retarget. Fake serial and fake IR only."""
import _bootstrap
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
from bose_bridge import Bridge  # noqa: E402
from bose_commands import COMMANDS, LIVE_MESSAGES  # noqa: E402
from bose_config import ConfigStore, SOURCES  # noqa: E402
from helpers import BASELINE_CONFIG  # noqa: E402
from bose_ir import RemoteManager, validate_management  # noqa: E402
from bose_request import request  # noqa: E402

IR_CONFIG = {'ir_device': '/dev/input/by-id/test-receiver', 'ir_name': 'test receiver', 'ir_keys': [101, 102, 103]}
PROFILES = {'c' * 32: {'name': 'Test remote', 'model': 'ir-remote', 'active': True,
                       'mappings': {'volume-up': {'code': 101, 'enabled': True},
                                    'volume-down': {'code': 102, 'enabled': True},
                                    'mute': {'code': 103, 'enabled': True}}}}

FAKE = r'''
import os,sys,types,time
mode,path,log,state,flags,ir_config,ir_fd=sys.argv[1:8]
from bose_commands import COMMANDS,RESPONSES,CATALOG,expected_reply
def guarded(real,name):
 def call(*args,**kwargs):
  if os.path.exists(os.path.join(flags,'fail-'+name)): raise OSError(5,'injected '+name)
  return real(*args,**kwargs)
 return call
os.replace=guarded(os.replace,'replace'); os.fsync=guarded(os.fsync,'fsync')
class Port:
 def __init__(self,**kwargs): self.pending=b''
 def open(self): pass
 def close(self): pass
 in_waiting=0
 def read(self,n):
  out,self.pending=self.pending[:n],self.pending[n:]
  return out
 def write(self,data):
  with open(log,'a') as f: f.write('TX '+data.hex()+'\n')
  name=next(k for k in COMMANDS if COMMANDS[k]==data)
  exact=name=='identify' or (name in CATALOG and CATALOG[name].verified)
  self.pending=RESPONSES[name] if exact else expected_reply(data)
  return len(data)
 def flush(self): pass
class Device:
 name='test receiver'
 def __init__(self,path): self.fd=int(ir_fd)
 def read(self):
  stamp=time.time()
  return [types.SimpleNamespace(type=4,code=4,value=int(v),timestamp=lambda:stamp) for v in os.read(self.fd,256).split()]
 def close(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=types.SimpleNamespace(InputDevice=Device)
from bose_bridge import serve
serve(path,mode=='dry',ir_config or None,state,serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


@contextmanager
def bridge(tmp, mode='ok', config=BASELINE_CONFIG, ir=False):
    path, log, state, flags = tmp + '/command.sock', tmp + '/serial.log', tmp + '/ir-state.json', tmp + '/flags'
    os.mkdir(flags)
    if config is not None:
        write_config(Path(tmp, 'config.json'), config)
    ir_config, read_fd, write_fd = '', -1, -1
    if ir:
        ir_config = tmp + '/ir-config.json'
        Path(ir_config).write_text(json.dumps(IR_CONFIG))
        seeded = RemoteManager(state)
        seeded.profiles = copy.deepcopy(PROFILES)
        seeded.persist()
        read_fd, write_fd = os.pipe()
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, log, state, flags, ir_config, str(read_fd)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               pass_fds=(read_fd,) if ir else ())
    try:
        for _ in range(150):
            if os.path.exists(path): break
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(.02)
        assert os.path.exists(path)
        yield path, Path(log), write_fd
    finally:
        process.terminate()
        out, err = process.communicate(timeout=4)
        assert not err, (out, err)
        if ir:
            os.close(read_fd); os.close(write_fd)


def sent(log):
    if not log.exists(): return []
    return [bytes.fromhex(line[3:]) for line in log.read_text().splitlines() if line.startswith('TX ')]


def ask(path, name):
    return request(path, (name + '\n').encode())


def manage(path, data):
    return json.loads(request(path, b'manage\n' + json.dumps(data).encode()))


def status(path):
    return json.loads(request(path, b'status\n'))


def inject(tmp, name, on=True):
    flag = Path(tmp, 'flags', 'fail-' + name)
    if on: flag.touch()
    else: flag.unlink()


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return True
        time.sleep(.02)
    return False


def set_zone(zone, enabled): return {'op': 'set_zone', 'zone': zone, 'enabled': enabled}
def set_source(source, connected): return {'op': 'set_source', 'source': source, 'connected': connected}
def set_unverified(zone, allow): return {'op': 'set_unverified', 'zone': zone, 'allow': allow}
def set_ir_zone(zone): return {'op': 'set_ir_zone', 'zone': zone}


def rejects(data):
    try:
        validate_management(data)
    except ValueError:
        return True
    return False


def test_new_ops_validate():
    for zone in 'abcd':
        for flag in (True, False):
            validate_management(set_zone(zone, flag))
            validate_management(set_unverified(zone, flag))
    for source in SOURCES:
        validate_management(set_source(source, True))
    for zone in 'abcd':
        validate_management(set_ir_zone(zone))
    bad = [
        set_zone('e', True), set_zone('A', True), set_zone(1, True), set_zone(None, True),
        set_zone('a', 1), set_zone('a', 'true'), set_zone('a', None),
        {'op': 'set_zone', 'enabled': True}, {'op': 'set_zone', 'zone': 'a'},
        dict(set_zone('a', True), extra=1), dict(set_zone('a', True), source='fm'),
        set_source('phono', True), set_source('FM', True), set_source(None, True),
        set_source('fm', 0), set_source('fm', None), {'op': 'set_source', 'source': 'fm'},
        dict(set_source('fm', True), zone='a'),
        set_unverified('x', True), set_unverified('a', None), set_unverified('a', 'yes'),
        {'op': 'set_unverified', 'zone': 'a'}, dict(set_unverified('a', True), source='fm'),
        set_ir_zone('z'), set_ir_zone(['a']), set_ir_zone(None), {'op': 'set_ir_zone'},
        dict(set_ir_zone('a'), enabled=True),
    ]
    for data in bad:
        assert rejects(data), data
    assert rejects({'op': 'set_room_live', 'room': 'd', 'live': True})   # removed op: rejected like any unknown op


def test_manage_ops_cannot_transmit():
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp) as (path, log, _):
            assert ask(path, 'room-a-source-fm') == 'rejected: not-allowed'
            assert manage(path, set_source('fm', True)) == {'ok': True, 'result': {}}
            assert sent(log) == []
            assert sent(log) == []    # a promoted (verified) source needs only its switch; Room D below still needs unverified
            assert ask(path, 'room-a-source-fm') == LIVE_MESSAGES['room-a-source-fm']
            assert sent(log) == [COMMANDS['room-a-source-fm']]
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp) as (path, log, _):
            assert ask(path, 'room-a-volume-up') == LIVE_MESSAGES['room-a-volume-up']
            assert manage(path, set_zone('a', False)) == {'ok': True, 'result': {}}
            assert ask(path, 'room-a-volume-up') == 'rejected: not-allowed'
            assert ask(path, 'volume-down') == 'rejected: not-allowed'
            time.sleep(1.05)
            assert ask(path, 'room-b-volume-up') == LIVE_MESSAGES['room-b-volume-up']
            assert sent(log) == [COMMANDS['room-a-volume-up'], COMMANDS['room-b-volume-up']]
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp) as (path, log, _):
            assert ask(path, 'room-d-source-fm') == 'rejected: not-allowed'
            assert manage(path, set_zone('d', True)) == {'ok': True, 'result': {}}
            assert manage(path, set_source('fm', True)) == {'ok': True, 'result': {}}
            assert ask(path, 'room-d-source-fm') == 'rejected: not-allowed'    # unverified.d still off
            assert manage(path, set_unverified('d', True)) == {'ok': True, 'result': {}}
            assert sent(log) == []
            assert ask(path, 'room-d-source-fm') == LIVE_MESSAGES['room-d-source-fm']
            assert sent(log) == [COMMANDS['room-d-source-fm']]
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp) as (path, log, _):
            assert manage(path, {'op': 'set_room_live', 'room': 'd', 'live': True}) == {'ok': False, 'error': 'invalid_request'}
            assert manage(path, set_zone('e', True)) == {'ok': False, 'error': 'invalid_request'}
            for bad in ({'op': []}, {'op': {}}, {'op': None}):
                assert manage(path, bad) == {'ok': False, 'error': 'invalid_request'}, bad
            assert sent(log) == []


def test_set_room_live_is_not_an_op():
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp) as (path, log, _):
            before = Path(tmp, 'config.json').read_bytes()
            assert manage(path, {'op': 'set_room_live', 'room': 'd', 'live': True}) == {'ok': False, 'error': 'invalid_request'}
            assert Path(tmp, 'config.json').read_bytes() == before and sent(log) == []


def test_remote_manager_rejects_new_ops_without_keyerror():
    with tempfile.TemporaryDirectory() as tmp:
        manager = RemoteManager(Path(tmp, 'ir-state.json'))
        for data in [set_zone('a', True), set_source('fm', True), set_unverified('a', True), set_ir_zone('b'),
                     {'op': 'set_room_live', 'room': 'd', 'live': True}]:
            try:
                manager.manage(data)
            except ValueError:
                pass
            else:
                raise AssertionError(data)
        assert not Path(tmp, 'ir-state.json').exists()


def test_commit_error_maps_to_reply():
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp) as (path, log, _):
            before = Path(tmp, 'config.json').read_bytes()
            assert ask(path, 'room-a-volume-up') == LIVE_MESSAGES['room-a-volume-up']
            inject(tmp, 'fsync')
            assert manage(path, set_zone('a', False)) == {'ok': False, 'error': 'not_saved'}
            inject(tmp, 'fsync', on=False)
            assert Path(tmp, 'config.json').read_bytes() == before
            inject(tmp, 'replace')
            assert manage(path, set_zone('a', False)) == {'ok': False, 'error': 'uncertain'}
            inject(tmp, 'replace', on=False)
            assert status(path)['bridge']['mode'] == 'live'                 # status still answers
            assert ask(path, 'room-b-volume-up') == 'rejected: not-allowed'  # uncertain: transmissions are blocked
            assert manage(path, set_zone('b', True)) == {'ok': False, 'error': 'not_saved'}
            time.sleep(1.05)
            assert ask(path, 'identify') == 'rejected: not-allowed'
            assert sent(log) == [COMMANDS['room-a-volume-up']]


def test_set_zone_disable_discards_pending():
    with tempfile.TemporaryDirectory() as tmp:
        write_config(Path(tmp, 'config.json'))
        store = ConfigStore(Path(tmp, 'config.json'))
        calls = []
        bridge_gate = Bridge(lambda action, data: calls.append(action) or 'ok', clock=lambda: 0.0,
                             result=lambda text: None, allowed=lambda action: store.allowed(action),
                             fast_actions=lambda: frozenset())
        assert bridge_gate.submit('room-a-volume-up') is True
        store.set_zone('a', False)
        assert bridge_gate.handle_one() == 'rejected: not-allowed'
        assert calls == [] and bridge_gate.last == float('-inf')


def test_set_ir_zone_resets_mute_state_and_retargets():
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp, ir=True) as (path, log, write_fd):
            assert wait_for(lambda: status(path)['bridge']['ir_attached'])
            os.write(write_fd, b'103\n')                                    # mute on target A
            assert wait_for(lambda: sent(log) == [COMMANDS['room-a-mute']])
            time.sleep(1.05)
            assert manage(path, set_ir_zone('b')) == {'ok': True, 'result': {}}
            assert sent(log) == [COMMANDS['room-a-mute']]
            os.write(write_fd, b'103\n')                                    # mute state reset: mute, not unmute
            assert wait_for(lambda: len(sent(log)) == 2)
            assert sent(log)[1] == COMMANDS['room-b-mute']
            time.sleep(1.05)
            os.write(write_fd, b'101\n')                                    # volume-up follows the target
            assert wait_for(lambda: len(sent(log)) == 3)
            assert sent(log)[2] == COMMANDS['room-b-volume-up']


def test_manage_ops_refused_in_dry_run():
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp, mode='dry') as (path, log, _):
            before = Path(tmp, 'config.json').read_bytes()
            for data in (set_zone('a', False), set_source('fm', True), set_unverified('a', True), set_ir_zone('b')):
                assert manage(path, data) == {'ok': False, 'error': 'dry_run'}, data
            assert manage(path, set_zone('e', True)) == {'ok': False, 'error': 'invalid_request'}
            assert Path(tmp, 'config.json').read_bytes() == before
            assert sent(log) == [] and not log.exists()


def test_learning_sends_no_bose_commands():
    # Learning and the ready checklist are receive-only: an active, enabled code must transmit nothing while a session is open.
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp, ir=True) as (path, log, write_fd):
            assert wait_for(lambda: status(path)['bridge']['ir_attached'])
            def press(code):
                os.write(write_fd, f'{code}\n'.encode())
                time.sleep(.35)                                 # the bridge treats faster repeats of one scan as held-button repeats
            def profiles():
                return json.loads(Path(tmp, 'ir-state.json').read_text())['profiles']
            before = profiles()
            sid = manage(path, {'op': 'begin', 'name': 'Learn', 'model': 'ir-remote', 'keys': ['volume-up']})['result']['session_id']
            assert manage(path, {'op': 'capture', 'session_id': sid})['ok']
            for _ in range(3): press(101)                       # the code an active, enabled volume-up mapping uses
            assert manage(path, {'op': 'session', 'session_id': sid})['result']['phase'] == 'captured'
            assert manage(path, {'op': 'cancel', 'session_id': sid})['ok']
            assert sent(log) == []
            sid = manage(path, {'op': 'begin', 'name': 'Test remote', 'model': 'ir-remote', 'remote_id': 'c' * 32,
                                'keys': ['volume-up', 'volume-down', 'mute']})['result']['session_id']
            for key, code in (('volume-up', 101), ('volume-down', 102), ('mute', 103)):   # the ready checklist
                assert manage(path, {'op': 'capture', 'session_id': sid})['ok']
                press(code)
                state = manage(path, {'op': 'session', 'session_id': sid})['result']
                assert state['key'] == key and state['verified'] == 1 and state['candidate'] == code, state
                assert manage(path, {'op': 'choose', 'session_id': sid, 'choice': 'skip'})['ok']
            assert manage(path, {'op': 'cancel', 'session_id': sid})['ok']
            assert sent(log) == [], 'learning transmitted a Bose command'
            assert profiles() == before, 'checklist changed the saved profile'
            time.sleep(.35)                                     # positive control: the same code transmits once the session is closed
            press(101)
            assert wait_for(lambda: sent(log) == [COMMANDS['room-a-volume-up']]), sent(log)


TESTS = [test_new_ops_validate, test_manage_ops_cannot_transmit, test_set_room_live_is_not_an_op,
         test_remote_manager_rejects_new_ops_without_keyerror, test_commit_error_maps_to_reply,
         test_set_zone_disable_discards_pending, test_set_ir_zone_resets_mute_state_and_retargets,
         test_manage_ops_refused_in_dry_run, test_learning_sends_no_bose_commands]

if __name__ == '__main__':
    for test in TESTS:
        test()
    print(f'{len(TESTS)} manage op tests passed')
