"""A stalled serial output drain must not hang the bridge: the command ends as uncertain, the fault latches, and
read-only status keeps answering. Normal drains and drain errors behave as before. Fake serial only."""
import _bootstrap
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
from bose_commands import COMMANDS, LIVE_MESSAGES  # noqa: E402
from bose_request import request  # noqa: E402

FAKE = r'''
import sys,types,time,termios
from pathlib import Path
from bose_commands import COMMANDS,RESPONSES
sys.path.insert(0,'tests')
from helpers import QUERY_REPLIES
mode,path,log=sys.argv[1:4]
class Port:
 timeout=None   # pyserial has this attribute; the telemetry poll saves and restores it
 def __init__(self,**kwargs): self.pending=b''
 def open(self): pass
 def close(self): pass
 @property
 def in_waiting(self): return len(self.pending)
 def read(self,n):
  out,self.pending=self.pending[:n],self.pending[n:]
  return out
 def write(self,data):
  if data in QUERY_REPLIES:   # a telemetry status query: answered, logged apart from commands
   with open(log,'a') as f: f.write('QX '+data.hex()+'\n')
   self.pending=QUERY_REPLIES[data]; return len(data)
  with open(log,'a') as f: f.write('TX '+data.hex()+'\n')
  self.pending=RESPONSES[next(k for k in COMMANDS if COMMANDS[k]==data)]
  return len(data)
 def flush(self):
  if mode=='stall':
   while True: time.sleep(.05)
  if mode=='error': raise OSError('simulated drain error')
  if mode=='termios': raise termios.error(5,'simulated tcdrain failure')
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
import bose_bridge
bose_bridge.DRAIN_TIMEOUT=.4
bose_bridge.serve(path,False,None,str(Path(path).with_name('ir-state.json')),serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


def wait_for(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return True
        time.sleep(.02)
    return False


@contextmanager
def bridge(tmp, mode):
    path, log = tmp + '/command.sock', Path(tmp, 'serial.log')
    write_config(Path(tmp, 'config.json'), {'schema': 1, 'zones': {z: {'enabled': z == 'a'} for z in 'abcd'},
                 'sources': {s: True for s in ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')},
                 'unverified': {z: False for z in 'abcd'}, 'ir': {'zone': 'a'}})
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, str(log)], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_for(lambda: os.path.exists(path) or process.poll() is not None, 6)
        assert os.path.exists(path), process.communicate()
        yield path, log, process
    finally:
        process.kill()
        process.communicate(timeout=4)


def health(path):
    return json.loads(request(path, b'status\n'))['bridge']


def sent(log):
    return [] if not log.exists() else [line for line in log.read_text().splitlines() if line.startswith('TX ')]


def test_stalled_drain_is_bounded_and_status_keeps_answering():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, 'stall') as (path, log, process):
        time.sleep(1.05)
        started = time.monotonic()
        reply = request(path, b'room-a-volume-up\n')
        assert time.monotonic() - started < 3, 'the command must finish inside the bound'
        assert reply == 'uncertain; no retry', reply
        state = health(path)                       # read-only status still answers
        assert state['fault'] == 'write-drain-timeout', state
        assert state['last_outcome']['outcome'] == 'uncertain'
        assert state['serial_open'] is False
        assert len(sent(log)) == 1                  # exactly one write, never repeated
        # Latched: a second command is refused with no write.
        time.sleep(1.05)
        assert request(path, b'room-a-volume-up\n') in ('rejected', 'rejected: not-allowed'), 'latched fault blocks commands'
        assert len(sent(log)) == 1
        assert process.poll() is None
    print('A stalled drain ends as uncertain within the bound, latches the fault and leaves status answering')


def test_normal_drain_is_unchanged():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, 'ok') as (path, log, process):
        time.sleep(1.05)
        assert request(path, b'room-a-volume-up\n') == LIVE_MESSAGES['room-a-volume-up']
        state = health(path)
        assert state['fault'] is None and state['serial_open'] is True
        assert state['last_outcome']['outcome'] == 'transmitted-with-matching-response'
        assert sent(log) == ['TX ' + COMMANDS['room-a-volume-up'].hex()]
    print('A normal drain behaves exactly as before')


def test_drain_error_still_latches_as_serial_io():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, 'error') as (path, log, process):
        time.sleep(1.05)
        assert request(path, b'room-a-volume-up\n') == 'uncertain; no retry'
        assert health(path)['fault'] == 'serial-io-during-transmission'
        assert len(sent(log)) == 1
    print('A drain error keeps its original fault name and single write')


def test_termios_error_latches_as_serial_io():
    # termios.error is not an OSError; it must still latch the fault, not escape the writer.
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp, 'termios') as (path, log, process):
        time.sleep(1.05)
        assert request(path, b'room-a-volume-up\n') == 'uncertain; no retry'
        state = health(path)
        assert state['fault'] == 'serial-io-during-transmission' and state['serial_open'] is False, state
        assert len(sent(log)) == 1
        time.sleep(1.05)
        assert request(path, b'room-a-volume-up\n') in ('rejected', 'rejected: not-allowed')
        assert len(sent(log)) == 1 and process.poll() is None
    print('A termios error during drain latches the fault with a single write')


if __name__ == '__main__':
    test_termios_error_latches_as_serial_io()
    test_normal_drain_is_unchanged()
    test_drain_error_still_latches_as_serial_io()
    test_stalled_drain_is_bounded_and_status_keeps_answering()
