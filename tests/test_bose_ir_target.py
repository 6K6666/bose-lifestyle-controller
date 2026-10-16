"""IR target zone: resolution, gate, per-target mute state and fast repeat. Fake serial and fake IR only."""
import _bootstrap
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
from bose_commands import COMMANDS, LIVE_MESSAGES, zone_of  # noqa: E402
from bose_config import SOURCES  # noqa: E402
from bose_ir import RemoteManager  # noqa: E402
from bose_request import request  # noqa: E402

IR_CONFIG = {'ir_device': '/dev/input/by-id/test-receiver', 'ir_name': 'test receiver', 'ir_keys': [101, 102, 103]}
SCAN = {'volume-up': 101, 'volume-down': 102, 'mute': 103}
PROFILES = {'c' * 32: {'name': 'Test remote', 'model': 'ir-remote', 'active': True,
                       'mappings': {'volume-up': {'code': 101, 'enabled': True},
                                    'volume-down': {'code': 102, 'enabled': True},
                                    'mute': {'code': 103, 'enabled': True}}}}
# Literal names per target: volume-up, volume-down, mute, unmute. Expected bytes come from COMMANDS, not from resolve_ir_action.
NAMES = {
    'a': ('room-a-volume-up', 'volume-down', 'room-a-mute', 'room-a-unmute'),
    'b': ('room-b-volume-up', 'room-b-volume-down', 'room-b-mute', 'room-b-unmute'),
    'c': ('room-c-volume-up', 'room-c-volume-down', 'room-c-mute', 'room-c-unmute'),
    'd': ('room-d-volume-up', 'room-d-volume-down', 'room-d-mute', 'room-d-unmute'),
}

FAKE = r'''
import os,sys,types,time
path,log,state,ir_config,fd=sys.argv[1:6]
from bose_commands import COMMANDS,RESPONSES,CATALOG,expected_reply
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
 def __init__(self,path): self.fd=int(fd)
 def read(self):
  stamp=time.time()
  return [types.SimpleNamespace(type=4,code=4,value=int(line),timestamp=lambda:stamp) for line in os.read(self.fd,256).split()]
 def close(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=types.SimpleNamespace(InputDevice=Device)
from bose_bridge import serve
serve(path,False,ir_config,state,serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


def config(zone='a', **edits):
    data = {'schema': 1, 'zones': {z: {'enabled': True} for z in 'abcd'},
            'sources': {s: True for s in SOURCES}, 'unverified': {z: True for z in 'abcd'}, 'ir': {'zone': zone}}
    for section, values in edits.items():
        data[section].update(values)
    return data


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return True
        time.sleep(.02)
    return False


def status(path):
    return json.loads(request(path, b'status\n'))


def last_stamp(path):
    outcome = status(path)['bridge']['last_outcome']
    return None if outcome is None else outcome['observed_at']


def sent(log):
    if not log.exists(): return []
    return [bytes.fromhex(line[3:]) for line in log.read_text().splitlines() if line.startswith('TX ')]


@contextmanager
def bridge(tmp, config_data=None, raw_config=None):
    path, log, state, ir_path = tmp + '/command.sock', Path(tmp, 'serial.log'), tmp + '/ir-state.json', tmp + '/ir.json'
    Path(ir_path).write_text(json.dumps(IR_CONFIG))
    if not Path(state).exists():
        Path(state).write_text(json.dumps({'schema': 2, 'bridge_id': 'b' * 32, 'profiles': PROFILES, 'mute_state': None}))
    if config_data is not None: write_config(Path(tmp, 'config.json'), config_data)
    if raw_config is not None: Path(tmp, 'config.json').write_text(raw_config)
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, path, str(log), state, ir_path, str(read_fd)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, pass_fds=(read_fd,))
    os.close(read_fd)
    try:
        assert wait_for(lambda: os.path.exists(path) or process.poll() is not None, 6)
        assert os.path.exists(path), process.communicate()
        assert wait_for(lambda: status(path)['bridge']['ir_attached'], 6), 'IR receiver never attached'
        yield SimpleNamespace(path=path, log=log, process=process, pipe=write_fd, state=state)
    finally:
        os.close(write_fd)
        process.terminate()
        out, err = process.communicate(timeout=4)
        assert not err, (out, err)


def press(ctx, key):
    """One IR press; waits until the bridge records an outcome for it (transmitted or rejected)."""
    time.sleep(1.05)   # bridge interval is 1 s outside the target's volume pair
    before = last_stamp(ctx.path)
    os.write(ctx.pipe, f'{SCAN[key]}\n'.encode())
    assert wait_for(lambda: last_stamp(ctx.path) != before), key


KEYS = ('volume-up', 'volume-down', 'mute', 'mute')


def test_ir_target_resolution_by_zone():
    for zone, names in NAMES.items():
        with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config(zone)) as ctx:
            # mute is pressed twice: the first mutes, the second unmutes
            expected = [COMMANDS[name] for name in names]
            for index, key in enumerate(KEYS):
                press(ctx, key)
                assert sent(ctx.log) == expected[:index + 1], (zone, key, sent(ctx.log))
            assert all(zone_of(frame) == zone for frame in sent(ctx.log)), zone
        print(f'zone {zone} resolves volume-up, volume-down, mute and unmute to its own frames')
    print('Every IR key resolves to the exact frame for each target zone (zone A volume-down is the alias)')


def test_ir_disabled_or_unverified_target_transmits_nothing():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config('b', zones={'b': {'enabled': False}})) as ctx:
        for key in KEYS[:3]: press(ctx, key)
        assert sent(ctx.log) == [], sent(ctx.log)
        assert ctx.process.poll() is None
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config('d', unverified={'d': False})) as ctx:
        for key in KEYS[:3]: press(ctx, key)
        assert sent(ctx.log) == [], sent(ctx.log)
        assert status(ctx.path)['bridge']['last_outcome']['outcome'] == 'rejected'
        assert ctx.process.poll() is None
    print('A disabled target zone, or zone D without unverified.d, transmits nothing and does not raise')


def test_ir_press_with_invalid_config_does_not_raise():
    for config_data, raw in ((None, None), (None, '{"schema": 1, "schema": 1}')):
        with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config_data, raw) as ctx:
            for key in KEYS[:3]: press(ctx, key)
            assert sent(ctx.log) == [], sent(ctx.log)
            assert ctx.process.poll() is None
    print('A missing or invalid config.json: IR presses transmit nothing and do not raise')


def test_ir_target_change_resets_mute_state():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, 'ir-state.json')
        manager = RemoteManager(path)
        manager.persist()
        bridge_id = manager.bridge_id
        manager.set_target_zone('a')
        assert manager.mute_state is None
        manager.command_succeeded('room-a-mute', 'a'); assert manager.mute_state is True
        manager.set_target_zone('a'); assert manager.mute_state is True          # same target keeps its state
        manager.command_succeeded('room-a-unmute', 'a'); assert manager.mute_state is False
        manager.command_succeeded('room-b-mute', 'a'); assert manager.mute_state is False   # other zone never touches it
        manager.set_target_zone('b')
        assert manager.mute_state is None
        assert json.loads(path.read_text()) == {'schema': 2, 'bridge_id': bridge_id, 'profiles': {}, 'mute_state': None}
        assert RemoteManager(path).bridge_id == bridge_id
        path.write_text(json.dumps({'schema': 2, 'bridge_id': 'd' * 32, 'profiles': {}, 'mute_state': True}))
        loaded = RemoteManager(path)
        assert loaded.bridge_id == 'd' * 32 and loaded.mute_state is None   # HEAD-format file still loads
    # Serve level: a state file that says muted, started with a different target. A restart already resets mute,
    # so this half passes at HEAD; the in-process half above is the discriminating one.
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, 'ir-state.json').write_text(json.dumps({'schema': 2, 'bridge_id': 'b' * 32, 'profiles': PROFILES, 'mute_state': True}))
        with bridge(tmp, config('b')) as ctx:
            assert status(ctx.path)['ir']['mute_state'] is None
        saved = json.loads(Path(tmp, 'ir-state.json').read_text())
        assert saved['mute_state'] is None and saved['bridge_id'] == 'b' * 32
    print('A target change resets the mute state to unknown and persists it; bridge_id never changes')


def test_zone_helpers_default_to_zone_a():
    with tempfile.TemporaryDirectory() as tmp:
        manager = RemoteManager(Path(tmp, 'ir-state.json'))
        assert manager.toggle_mute() == 'room-a-mute' and manager.toggle_mute('c') == 'room-c-mute'
        manager.mute_state = True
        assert manager.toggle_mute() == 'room-a-unmute' and manager.toggle_mute('d') == 'room-d-unmute'
        manager.command_succeeded('room-a-unmute')
        assert manager.mute_state is False
    print('toggle_mute and command_succeeded default to zone A and only track the named zone')


def test_ssh_mute_uses_target_zone():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config('b')) as ctx:
        def mute_state():
            return status(ctx.path)['ir']['mute_state']
        assert request(ctx.path, b'room-b-mute\n') == LIVE_MESSAGES['room-b-mute']
        assert mute_state() is True
        time.sleep(1.05)
        assert request(ctx.path, b'room-a-unmute\n') == LIVE_MESSAGES['room-a-unmute']
        assert mute_state() is True, 'a non-target zone press changed the target mute state'
        time.sleep(1.05)
        assert request(ctx.path, b'room-b-unmute\n') == LIVE_MESSAGES['room-b-unmute']
        assert mute_state() is False
    print('A socket mute for the target zone updates its mute state; other zones do not touch it')


def test_ir_profiles_unchanged_by_target_change():
    with tempfile.TemporaryDirectory() as tmp:
        with bridge(tmp, config('a')) as ctx:
            pass
        before = (Path(tmp, 'ir.json').read_bytes(), json.loads(Path(tmp, 'ir-state.json').read_text()))
        with bridge(tmp, config('c')) as ctx:
            press(ctx, 'volume-up')
            assert sent(ctx.log) == [COMMANDS['room-c-volume-up']]   # learned codes still map to the new target
        after = (Path(tmp, 'ir.json').read_bytes(), json.loads(Path(tmp, 'ir-state.json').read_text()))
        assert before[0] == after[0], 'ir-config.json changed'
        assert before[1]['profiles'] == after[1]['profiles'] and before[1]['bridge_id'] == after[1]['bridge_id']
    print('Changing the target leaves ir-config.json bytes and learned codes identical')


def test_ir_path_with_zone_a_disabled_emits_nothing():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config('a', zones={'a': {'enabled': False}})) as ctx:
        for key in KEYS[:3]: press(ctx, key)
        frames = sent(ctx.log)
        assert frames == [] and all(zone_of(frame) != 'a' for frame in frames), frames
    print('Target A with zone A disabled: IR emits no frame')


def test_fast_volume_uses_target_actions():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config('b')) as ctx:
        assert request(ctx.path, b'room-b-volume-up\n') == LIVE_MESSAGES['room-b-volume-up']
        time.sleep(.3)
        assert request(ctx.path, b'room-b-volume-down\n') == LIVE_MESSAGES['room-b-volume-down']   # target pair: .25 s
        time.sleep(.3)
        assert request(ctx.path, b'room-c-volume-up\n') == 'rate-limited'                       # other zones: 1 s
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, config('a')) as ctx:
        assert request(ctx.path, b'room-a-volume-up\n') == LIVE_MESSAGES['room-a-volume-up']
        time.sleep(.3)
        assert request(ctx.path, b'volume-down\n') == LIVE_MESSAGES['volume-down']              # zone A pair unchanged
        time.sleep(.3)
        assert request(ctx.path, b'room-b-volume-up\n') == 'rate-limited'
    print('Fast repeat uses the target volume pair (.25 s); other zones keep the 1 s interval')


if __name__ == '__main__':
    test_ir_target_resolution_by_zone()
    test_ir_disabled_or_unverified_target_transmits_nothing()
    test_ir_press_with_invalid_config_does_not_raise()
    test_ir_target_change_resets_mute_state()
    test_zone_helpers_default_to_zone_a()
    test_ssh_mute_uses_target_zone()
    test_ir_profiles_unchanged_by_target_change()
    test_ir_path_with_zone_a_disabled_emits_nothing()
    test_fast_volume_uses_target_actions()
