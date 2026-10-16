"""Room D stays visible as uncommissioned HA catalog, never reaches serial."""
import _bootstrap
import copy
import json
import os
from pathlib import Path
from runpy import run_path
import subprocess
import sys
import tempfile
import time

from bose_commands import COMMANDS, LIVE_ACTIONS
from bose_request import request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import BASELINE_CONFIG, write_config  # noqa: E402
EXPECTED = {
    'room-d-power-on': b'MM 4a,41,08\n',
    'room-d-power-off': b'MM 4a,43,08\n',
    'room-d-volume-up': b'MM 44,41,03\n',
    'room-d-volume-down': b'MM 44,42,03\n',
    'room-d-mute': b'MM 43,41,08\n',
    'room-d-unmute': b'MM 43,40,08\n',
    'room-d-source-am': b'MM 42,40,08,00\n',
    'room-d-source-fm': b'MM 42,40,08,01\n',
    'room-d-source-cd': b'MM 42,40,08,02\n',
    'room-d-source-aux': b'MM 42,40,08,03\n',
    'room-d-source-video-1': b'MM 42,40,08,04\n',
    'room-d-source-video-2': b'MM 42,40,08,05\n',
    'room-d-source-tape': b'MM 42,40,08,06\n',
}
assert {name: COMMANDS.get(name) for name in EXPECTED} == EXPECTED
assert COMMANDS['room-a-power-on'] == COMMANDS['system-power-on']
assert COMMANDS['room-a-power-off'] == COMMANDS['system-power-off']
assert {'room-a-power-on','room-a-power-off'} <= LIVE_ACTIONS
assert len(COMMANDS) == 55
assert run_path(str(ROOT / 'custom_components/bose_lifestyle/commands.py'))['COMMANDS'] == COMMANDS
assert not (set(EXPECTED) & LIVE_ACTIONS)

with tempfile.TemporaryDirectory() as tmp:
    path, log = tmp + '/command.sock', tmp + '/writes.log'
    write_config(Path(tmp, 'config.json'))
    code = r'''
import sys,types
class Port:
 def __init__(self,**kwargs): pass
 def open(self): pass
 def close(self): pass
 @property
 def in_waiting(self): return 0
 def read(self,n): return b''
 def write(self,data):
  with open(sys.argv[2],'a') as f: f.write(data.hex()+'\n')
  return len(data)
 def flush(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
from bose_bridge import serve
serve(sys.argv[1],False,ir_state=sys.argv[2]+'.state',serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''
    process = subprocess.Popen([sys.executable, '-u', '-c', code, path, log],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(150):
            if os.path.exists(path): break
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(.02)
        assert request(path, b'room-d-power-off\n') == 'rejected: not-allowed'   # zone D is off in the baseline config
        snapshot = json.loads(request(path, b'status\n'))
        assert set(snapshot['rooms']) == {'a', 'b', 'c', 'd'}
        assert all(field == {'value': None, 'observed_at': None, 'age': None, 'supported': False}
                   for field in snapshot['rooms']['d'].values())
        assert not Path(log).exists()
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=4)
        assert not stderr, (stdout, stderr)

print('Room D catalog and unknown status remain guarded from serial transmission')

# Room D permission is config data: zones.d.enabled AND unverified.d, read once at startup. Fake serial only.
from contextlib import contextmanager
from bose_commands import LIVE_MESSAGES, REFUSED_MESSAGE, expected_reply

FAKE = r'''
import sys,types
mode,path,log,state=sys.argv[1:5]
from bose_commands import COMMANDS,RESPONSES,expected_reply
class Port:
 def __init__(self,**kwargs): self.sent=None
 def open(self): pass
 def close(self): pass
 in_waiting=0
 def read(self,n):
  if not n or self.sent is None: return b''
  sent,self.sent=self.sent,None
  name=next(k for k in COMMANDS if COMMANDS[k]==sent)
  if name.startswith('room-d-'):
   frame=expected_reply(sent)
   if mode=='refuse': return frame[:-3]+b'F2\r'
   if mode=='wrong': return b'\r\r\r\r>.C00000000000\r'
   return frame
  return RESPONSES[name]
 def write(self,data):
  with open(log,'a') as f: f.write('TX '+data.hex()+'\n')
  self.sent=data
  return len(data)
 def flush(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
from bose_bridge import serve
serve(path,mode=='dry',ir_state=state,serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''

def room_d_config(live):
    config = copy.deepcopy(BASELINE_CONFIG)
    config['zones']['d']['enabled'] = live
    config['unverified']['d'] = live
    config['sources']['video-2'] = False          # FM, CD, Tape and Video 2 stay disconnected: never transmitted here
    return config

ON_CONFIG, OFF_CONFIG = room_d_config(True), room_d_config(False)

@contextmanager
def d_bridge(mode, tmp, config=None):
    """config=None writes nothing, so a test can plant its own (possibly invalid) config.json first."""
    path, log, state = tmp + '/command.sock', tmp + '/serial.log', tmp + '/ir-state.json'
    if config is not None: write_config(Path(tmp, 'config.json'), config)
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, log, state],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(150):
            if os.path.exists(path): break
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(.02)
        assert os.path.exists(path)
        yield path, Path(log), process
    finally:
        process.terminate()
        out, err = process.communicate(timeout=4)
        assert not err, (out, err)

def tx_count(log): return log.read_text().count('TX ') if log.exists() else 0
def manage(path, payload): return json.loads(request(path, b'manage\n' + json.dumps(payload).encode()))
def bridge_status(path): return json.loads(request(path, b'status\n'))
def room_d_live(snap):
    # Schema 4: Room D is permitted exactly when config zone D is enabled and unverified.d is set.
    config = snap['config']
    return bool(config is not None and config['zones']['d']['enabled'] and config['unverified']['d'])
ON = {'op': 'set_room_live', 'room': 'd', 'live': True}

with tempfile.TemporaryDirectory() as tmp:
    with d_bridge('ok', tmp, BASELINE_CONFIG) as (path, log, process):
        snap = bridge_status(path)
        assert snap['schema'] == 4 and room_d_live(snap) is False and snap['actions']['room-d-mute']['allowed'] is False
        assert request(path, b'room-d-mute\n') == 'rejected: not-allowed'
        assert tx_count(log) == 0
        assert manage(path, ON) == {'ok': False, 'error': 'invalid_request'}   # removed op: no file is touched
        assert room_d_live(bridge_status(path)) is False and not Path(tmp, 'rooms.json').exists()
        write_config(Path(tmp, 'config.json'), ON_CONFIG)                  # a running bridge ignores the edit
        assert room_d_live(bridge_status(path)) is False
        assert request(path, b'room-d-mute\n') == 'rejected: not-allowed'
print('Room D is denied by the default config, and the removed set_room_live op is an invalid request')

with tempfile.TemporaryDirectory() as tmp:
    with d_bridge('ok', tmp, ON_CONFIG) as (path, log, process):           # restart reads the config
        assert room_d_live(bridge_status(path)) is True
        assert tx_count(log) == 0, 'startup transmitted'
        assert request(path, b'room-d-mute\n') == LIVE_MESSAGES['room-d-mute']
        assert request(path, b'room-d-unmute\n') == 'rate-limited'          # rapid taps are dropped, not rejected
        assert log.read_text().splitlines() == ['TX ' + COMMANDS['room-d-mute'].hex()]
        time.sleep(1.05)
        for name in ('room-d-source-fm', 'room-d-source-cd', 'room-d-source-tape', 'room-d-source-video-2'):
            assert request(path, (name + '\n').encode()) == 'rejected: not-allowed', name   # sources not connected
        assert tx_count(log) == 1
        # Only status schema 4 is accepted: older or unknown shapes are rejected by the strict validator.
        from bose_request import validate_status
        snap_text = request(path, b'status\n')
        schema4 = json.loads(snap_text)
        assert validate_status(snap_text)['schema'] == 4
        for bad_shape in ({**schema4, 'schema': 3}, {**schema4, 'schema': 2}, {**schema4, 'schema': 5}):
            try: validate_status(json.dumps(bad_shape))
            except OSError: pass
            else: raise AssertionError('non-schema-4 status accepted')
        time.sleep(.05)
        snap = bridge_status(path)['bridge']
        assert snap['last_transmission']['outcome'] == 'transmitted-with-matching-response' and snap['fault'] is None
    with d_bridge('ok', tmp, OFF_CONFIG) as (path, log, process):          # restart with the permission removed
        assert room_d_live(bridge_status(path)) is False
        assert request(path, b'room-d-mute\n') == 'rejected: not-allowed'
        assert tx_count(log) == 1, 'only the earlier permitted transmission is in the log'
print('Room D is live only while the config enables zone D and unverified.d, and only after a restart')

with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, 'config.json').write_text('{"schema": 1, "schema": 1}')     # duplicate key: config-invalid
    with d_bridge('ok', tmp) as (path, log, process):
        assert room_d_live(bridge_status(path)) is False
        assert request(path, b'room-d-mute\n') == 'rejected: not-allowed'
        assert tx_count(log) == 0
print('An invalid config fails closed for Room D and transmits nothing at startup')

with tempfile.TemporaryDirectory() as tmp:
    with d_bridge('refuse', tmp, ON_CONFIG) as (path, log, process):
        assert request(path, b'room-d-power-on\n') == REFUSED_MESSAGE
        snap = bridge_status(path)['bridge']
        assert snap['fault'] is None and snap['serial_open'] is True and snap['recovery'] is None
        assert snap['last_outcome']['outcome'] == 'refused-by-m1'
        assert snap['last_transmission']['outcome'] == 'refused-by-m1'
        assert process.poll() is None and tx_count(log) == 1
        time.sleep(1.05)
        assert request(path, b'volume-down\n') == LIVE_MESSAGES['volume-down']   # bridge stays live; refusal never retried
        assert tx_count(log) == 2
print('An M1 refusal of a Room D command is recorded without latching the bridge')

with tempfile.TemporaryDirectory() as tmp:
    with d_bridge('wrong', tmp, ON_CONFIG) as (path, log, process):
        assert request(path, b'room-d-volume-up\n') == 'uncertain; no retry'
        assert bridge_status(path)['bridge']['fault'] == 'unexpected-response'
        time.sleep(1.05)
        assert request(path, b'room-d-volume-up\n') == 'rejected' and tx_count(log) == 1
print('Any other Room D reply still latches the bridge and is never replayed')

with tempfile.TemporaryDirectory() as tmp:
    with d_bridge('dry', tmp, ON_CONFIG) as (path, log, process):
        assert manage(path, ON) == {'ok': False, 'error': 'invalid_request'}
        assert request(path, b'room-d-mute\n').startswith('DRY RUN; no port opened: ')
        assert tx_count(log) == 0 and not Path(tmp, 'rooms.json').exists()
print('A dry-run bridge never transmits Room D commands')
