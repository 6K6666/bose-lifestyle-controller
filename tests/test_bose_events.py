"""Bounded event log: never raises, rotates, survives a fault exit; `events` is read-only and cannot affect serial control."""
import _bootstrap
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from bose_events import EventLog
from bose_request import request, validate_events
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from helpers import write_config

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as tmp:
    log = EventLog(tmp + '/events.jsonl')
    assert log.recent() == []
    log.add('tx', 'volume-down', '4d 4d')
    log.add('bogus', None, 'ignored kind')
    log.add('outcome', None, 'x' * 500)
    events = log.recent()
    assert [e['kind'] for e in events] == ['tx', 'outcome']
    assert set(events[0]) == {'t', 'kind', 'action', 'detail'} and isinstance(events[0]['t'], float)
    assert len(events[1]['detail']) == 200 and events[1]['action'] is None

    small = EventLog(tmp + '/small.jsonl', cap=1000)
    for i in range(40): small.add('rx', 'volume-down', f'{i:02d}' + 'y' * 60)
    assert Path(tmp, 'small.jsonl.1').exists() and Path(tmp, 'small.jsonl').stat().st_size <= 1000
    kept = small.recent(100)
    assert kept[-1]['detail'].startswith('39') and [e['detail'][:2] for e in kept] == sorted(e['detail'][:2] for e in kept)
    assert len(kept) > 10

    huge = Path(tmp, 'huge.jsonl')
    huge.write_text(''.join(json.dumps({'t': float(i), 'kind': 'rx', 'action': None, 'detail': 'q' * 90}) + '\n' for i in range(200000)))
    started = time.monotonic()
    tail = EventLog(huge, cap=10**9).recent()
    assert time.monotonic() - started < .1 and [e['t'] for e in tail][-1] == 199999.0 and len(tail) == 50   # reads the tail only

    big = EventLog(tmp + '/big.jsonl')
    for i in range(80): big.add('rx', None, 'z' * 200)
    fitted = big.recent(50, 8192)
    assert fitted and len(json.dumps({'events': fitted}, separators=(',', ':'))) <= 8192

    Path(tmp, 'mixed.jsonl').write_text('not json\n{"t":1}\n' + json.dumps({'t': 1.0, 'kind': 'tx', 'action': None, 'detail': 'ok'}) + '\n\xff\n')
    assert [e['detail'] for e in EventLog(tmp + '/mixed.jsonl').recent()] == ['ok']

    Path(tmp, 'blocker').write_text('x')
    broken, out = EventLog(tmp + '/blocker/events.jsonl'), io.StringIO()
    with contextlib.redirect_stdout(out):
        for _ in range(3): broken.add('tx', None, 'dropped')
    assert out.getvalue().count('Event log unavailable') == 1 and broken.recent() == []
    assert EventLog(None).recent() == [] and EventLog(None).add('tx') is None
    closed = io.StringIO(); closed.close()
    with contextlib.redirect_stdout(closed):
        EventLog(tmp + '/blocker/events.jsonl').add('tx', None, 'stdout is gone')   # the one-time warning must not raise either

good = json.dumps({'events': [{'t': 1.5, 'kind': 'tx', 'action': 'volume-down', 'detail': 'a'}]})
assert len(validate_events(good)) == 1 and validate_events('{"events":[]}') == []
for bad in ('{}', '[]', '{"events":{}}', '{"events":[],"x":1}', good.replace('"tx"', '"zap"'), good.replace('1.5', 'NaN'),
            good.replace('"a"', '1'), good.replace('"volume-down"', '5'),
            json.dumps({'events': [{'t': 1.0, 'kind': 'tx', 'action': None, 'detail': ''}] * 51}),
            good.replace('"detail"', '"detail":"d","detail"'), good + ' trailing'):
    try: validate_events(bad)
    except OSError: pass
    else: raise AssertionError(bad)

FAKE = r'''
import sys,types
mode,path,log,state=sys.argv[1:5]
from bose_commands import COMMANDS,RESPONSES
class Port:
 def __init__(self,**kwargs): self.sent=None
 def open(self): pass
 def close(self): pass
 in_waiting=0
 def read(self,n):
  if not n or self.sent is None: return b''
  sent,self.sent=self.sent,None
  if mode=='wrong': return b'\r\r\r\r>.C99999999999\r'
  return RESPONSES[next(k for k in COMMANDS if COMMANDS[k]==sent)]
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

def bridge(mode, tmp):
    path, log, state = tmp + '/command.sock', tmp + '/serial.log', tmp + '/ir-state.json'
    write_config(Path(tmp, 'config.json'))
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, log, state],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(150):
        if os.path.exists(path): break
        if process.poll() is not None: raise AssertionError(process.communicate())
        time.sleep(.02)
    return path, Path(log), process

def stop(process):
    process.terminate()
    out, err = process.communicate(timeout=4)
    assert not err, (out, err)
    return out

def recent(path): return json.loads(request(path, b'events\n'))['events']

with tempfile.TemporaryDirectory() as tmp:
    path, log, process = bridge('ok', tmp)
    try:
        before = recent(path)
        assert [e['kind'] for e in before] == ['start', 'serial']
        assert not log.exists(), 'events request touched the serial port'
        assert request(path, b'volume-down\n').startswith('transmitted room A volume-down')   # events consumed no wire interval
        kinds = [e['kind'] for e in recent(path)]
        assert kinds == ['start', 'serial', 'tx', 'rx', 'outcome'], kinds
        assert recent(path)[2]['detail'] == '4d 4d 20 34 34 2c 34 32 2c 30 30 0a' and recent(path)[2]['action'] == 'volume-down'
    finally:
        stop(process)

with tempfile.TemporaryDirectory() as tmp:
    path, log, process = bridge('wrong', tmp)
    try:
        assert request(path, b'volume-down\n') == 'uncertain; no retry'
        faults = [e for e in recent(path) if e['kind'] == 'fault']
        assert len(faults) == 1 and 'unexpected-response' in faults[0]['detail'] and '39 39 39' in faults[0]['detail']
        kinds = [e['kind'] for e in recent(path)]
        assert kinds.count('tx') == 1 and kinds.count('rx') == 1, kinds   # one write is one tx and one rx, never a repeat
        assert process.poll() is None   # a latched fault does not end the bridge; the record is already on disk
        assert 'unexpected-response' in Path(tmp, 'events.jsonl').read_text()
    finally:
        stop(process)
    assert 'unexpected-response' in Path(tmp, 'events.jsonl').read_text()   # file outlives the process

with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, 'events.jsonl').mkdir()                                       # logging cannot work here
    path, log, process = bridge('ok', tmp)
    try:
        assert request(path, b'volume-down\n').startswith('transmitted room A volume-down')
        assert log.read_text().count('TX ') == 1 and recent(path) == []
        time.sleep(.3)
        assert request(path, b'room-a-volume-up\n').startswith('transmitted room A volume-up')
    finally:
        out = stop(process)
    assert out.count('Event log unavailable') == 1

with tempfile.TemporaryDirectory() as tmp:
    path, log, process = bridge('dry', tmp)
    try:
        assert request(path, b'volume-down\n').startswith('DRY RUN')
    finally:
        stop(process)
    assert not Path(tmp, 'events.jsonl').exists()                           # a dry-run bridge writes no event file
print('Event log is bounded, never raises, records tx/rx/outcome/fault, survives a fault, and cannot change serial behavior')
