"""serve() gate: one ConfigStore decides every transmission; denied actions never reach the port. Fake serial only."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
from bose_commands import (CATALOG, COMMANDS, LIVE_ACTIONS, LIVE_MESSAGES, REFUSED_MESSAGE, RESPONSES, expected_reply,
                           classify_reply, zone_of)  # noqa: E402
from bose_config import SOURCES, allowed_actions  # noqa: E402
from helpers import BASELINE_CONFIG  # noqa: E402
from bose_request import request  # noqa: E402

FAKE = r'''
import sys,types
mode,path,log,state=sys.argv[1:5]
from bose_commands import COMMANDS,RESPONSES,CATALOG,expected_reply
class Port:
 def __init__(self,**kwargs): self.pending=b''
 def open(self):
  with open(log,'a') as f: f.write('open\n')
 def close(self): pass
 in_waiting=0
 def read(self,n):
  out,self.pending=self.pending[:n],self.pending[n:]
  return out
 def write(self,data):
  with open(log,'a') as f: f.write('TX '+data.hex()+'\n')
  name=next(k for k in COMMANDS if COMMANDS[k]==data)
  if name=='identify': reply=RESPONSES['identify']
  elif mode=='wrong': reply=b'\r\r\r\r>.C99999999999\r'
  elif name in CATALOG and CATALOG[name].verified: reply=RESPONSES[name]
  else:
   reply=expected_reply(data)
   if mode=='f2': reply=reply[:-3]+b'F2\r'
  if mode=='f2-verified' and name!='identify': reply=reply[:-3]+b'F2\r'
  self.pending=reply
  return len(data)
 def flush(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
from bose_bridge import serve
serve(path,mode=='dry',ir_state=state,serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


@contextmanager
def bridge(tmp, mode='ok', config=BASELINE_CONFIG):
    path, log, state = tmp + '/command.sock', tmp + '/serial.log', tmp + '/ir-state.json'
    if config is not None:
        write_config(Path(tmp, 'config.json'), config)
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


def sent(log):
    if not log.exists(): return []
    return [bytes.fromhex(line[3:]) for line in log.read_text().splitlines() if line.startswith('TX ')]


def status(path):
    return json.loads(request(path, b'status\n'))


def changed(**edits):
    config = copy.deepcopy(BASELINE_CONFIG)
    for section, values in edits.items():
        config[section].update(values)
    return config


def ask(path, name):
    return request(path, (name + '\n').encode())


def room_d_live(snap):
    # Schema 4: Room D is permitted exactly when config zone D is enabled and unverified.d is set.
    config = snap['config']
    return bool(config is not None and config['zones']['d']['enabled'] and config['unverified']['d'])


with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as (path, log, process):
        for name in ('room-a-source-fm', 'room-a-source-cd', 'room-d-power-on', 'room-d-volume-up'):
            assert ask(path, name) == 'rejected: not-allowed', name
        last = status(path)['bridge']['last_outcome']
        assert last['action'] == 'room-d-volume-up' and last['outcome'] == 'rejected', last
        assert sent(log) == []
        assert ask(path, 'volume-down') == LIVE_MESSAGES['volume-down']   # denied requests spent no wire budget
        assert sent(log) == [COMMANDS['volume-down']]
print('Denied actions return rejected: not-allowed, write nothing and spend no budget')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, mode='dry', config=None) as (path, log, process):
        for name in ('volume-down', 'volume-down', 'room-a-volume-up', 'room-a-volume-up', 'identify'):
            assert ask(path, name) == 'DRY RUN; no port opened: ' + COMMANDS[name].hex(' '), name
        assert not log.exists(), 'dry-run touched the port'
        assert status(path)['bridge']['serial_open'] is False
print('Dry-run keeps the DRY RUN reply, forces an empty wire budget and never opens the port')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, mode='wrong') as (path, log, process):
        assert ask(path, 'volume-down') == 'uncertain; no retry'
        assert ask(path, 'identify') == 'rejected: not-allowed'      # identify is refused while faulted
        assert sent(log) == [COMMANDS['volume-down']]
        assert process.poll() is None
print('identify is rejected while faulted; dry-run identify keeps its DRY RUN reply')

config = changed(zones={'a': {'enabled': False}})
with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, config=config) as (path, log, process):
        for name in ('volume-down', 'system-power-on', 'system-power-off', 'room-a-power-on',
                     'room-a-power-off', 'room-a-volume-up', 'room-a-mute', 'room-a-source-aux'):
            assert ask(path, name) == 'rejected: not-allowed', name
        assert ask(path, 'room-b-volume-up') == LIVE_MESSAGES['room-b-volume-up']
        frames = sent(log)
        assert frames == [COMMANDS['room-b-volume-up']], frames
        assert all(zone_of(frame) != 'a' for frame in frames)
print('Aliases and room-a names follow zone A state; no emitted frame is zone A')

everything = {'schema': 1, 'zones': {zone: {'enabled': True} for zone in 'abcd'},
              'sources': {source: True for source in SOURCES}, 'unverified': {zone: True for zone in 'abcd'},
              'ir': {'zone': 'a'}}
allowed = allowed_actions(everything)
assert allowed == frozenset(CATALOG), set(CATALOG) ^ allowed
for name in sorted(allowed):
    data = COMMANDS[name]
    assert name in LIVE_MESSAGES, name
    assert classify_reply(data, expected_reply(data)) == 'ok', name
    if CATALOG[name].verified:
        assert classify_reply(data, RESPONSES[name]) == 'ok', name
        assert 'unverified' not in LIVE_MESSAGES[name], name   # a verified message never claims an unverified reply
assert 'identify' in LIVE_MESSAGES
print('Every allowed action has a live message and a reply check')

unverified_fm = changed(sources={'fm': True}, unverified={'a': True})
with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, config=unverified_fm) as (path, log, process):
        assert ask(path, 'room-a-source-fm') == LIVE_MESSAGES['room-a-source-fm']
        snap = status(path)['bridge']
        assert snap['fault'] is None and snap['serial_open'] is True
        assert snap['last_transmission']['outcome'] == 'transmitted-with-matching-response', snap
        assert sent(log) == [COMMANDS['room-a-source-fm']]
        assert process.poll() is None
room_d_on = changed(zones={'d': {'enabled': True}}, unverified={'d': True}, sources={'fm': True})
with tempfile.TemporaryDirectory() as tmp:
    # room-a-source-fm was promoted to verified, where F2 latches; Room D FM is still unverified.
    with bridge(tmp, mode='f2', config=room_d_on) as (path, log, process):
        assert ask(path, 'room-d-source-fm') == REFUSED_MESSAGE
        snap = status(path)['bridge']
        assert snap['fault'] is None and snap['serial_open'] is True
        assert snap['last_transmission']['outcome'] == 'refused-by-m1', snap
        time.sleep(1.05)
        assert ask(path, 'room-b-volume-up') == LIVE_MESSAGES['room-b-volume-up']   # bridge stays live
        assert process.poll() is None
with tempfile.TemporaryDirectory() as tmp:
    # A promoted (verified) source gets no F2 tolerance: the refusal latches like any other verified command.
    with bridge(tmp, mode='f2-verified', config=unverified_fm) as (path, log, process):
        reply = ask(path, 'room-a-source-fm')
        assert reply == 'uncertain; no retry', reply
        snap = status(path)['bridge']
        assert snap['fault'] == 'unexpected-response', snap
        assert sent(log) == [COMMANDS['room-a-source-fm']]
print('An allowed unverified command transmits without a fault; an F2 reply is tolerated')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, config=room_d_on) as (path, log, process):
        assert room_d_live(status(path)) is True
        assert ask(path, 'room-d-source-fm') == LIVE_MESSAGES['room-d-source-fm']
        assert sent(log) == [COMMANDS['room-d-source-fm']]
print('Room D FM transmits when zone D and unverified.d are on and FM is connected')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as (path, log, process):
        assert ask(path, 'identify') == LIVE_MESSAGES['identify']
        assert sent(log) == [COMMANDS['identify']]
print('Manual identify works with a valid configuration')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, config=None) as (path, log, process):
        assert room_d_live(status(path)) is False and sent(log) == []
        assert ask(path, 'volume-down') == 'rejected: not-allowed'
        assert ask(path, 'room-a-mute') == 'rejected: not-allowed'
        assert ask(path, 'identify') == 'rejected: not-allowed'
        assert sent(log) == []
print('A missing config transmits nothing at startup or on a manual identify request')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as (path, log, process):
        before = Path(tmp, 'config.json').read_bytes()
        reply = json.loads(request(path, b'manage\n' + json.dumps({'op': 'set_room_live', 'room': 'd', 'live': True}).encode()))
        assert reply == {'ok': False, 'error': 'invalid_request'}, reply
        assert Path(tmp, 'config.json').read_bytes() == before
        assert not Path(tmp, 'rooms.json').exists()
        assert room_d_live(status(path)) is False and sent(log) == []
print('The removed set_room_live op is an invalid request and touches no file')

with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as (path, log, process):
        admitted = set()
        for name in sorted(COMMANDS):
            reply = ask(path, name)
            if reply == 'rejected: not-allowed':
                continue
            # Admitted by the gate: it transmits (exact live message) or is only budget-limited. Anything else fails.
            assert reply in (LIVE_MESSAGES[name], 'rate-limited'), (name, reply)
            admitted.add(name)
        # The baseline keeps the FM, CD and Tape source switches off, so those promoted names stay gated.
        baseline_live = {n for n in LIVE_ACTIONS if not n.endswith(('-source-fm', '-source-cd', '-source-tape'))}
        assert admitted == baseline_live, admitted ^ baseline_live
        wire = {COMMANDS[name] for name in baseline_live}
        assert sent(log) and all(frame in wire for frame in sent(log)), sent(log)
print('The baseline config lets serve() transmit exactly the live actions whose source switch is on')
