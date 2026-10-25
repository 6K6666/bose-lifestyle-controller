"""Run the real bridge/socket loop against faulty serial exchanges; no hardware."""
import _bootstrap
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from bose_commands import LIVE_MESSAGES
from bose_request import request
from helpers import write_config

ROOT = Path(__file__).resolve().parents[1]

FAKE = r'''
import sys, types, time
from pathlib import Path
from bose_commands import COMMANDS, RESPONSES
sys.path.insert(0, 'tests')
from helpers import QUERY_REPLIES
mode, path, clock_file, wire = sys.argv[1:]
queries = 0
pending = b''
injected = False
class Port:
 def __init__(self, **kwargs): self.timeout = kwargs['timeout']; self.query = False
 def open(self):
  global pending
  pending = b''  # pyserial's open flushes old RX, including a late query reply
 def close(self):
  global pending
  pending = b'\r\r\r\r>.0A0024222337\r'  # late reply must not reach the next command
 @property
 def in_waiting(self):
  global pending, injected
  if mode == 'pre-input' and not injected and queries == 2 and float(Path(clock_file).read_text()) > 0 and not pending:
   pending = b'late reply'
   injected = True
  return len(pending)
 def read(self, n):
  global pending
  out, pending = pending[:n], pending[n:]
  return out
 def write(self, data):
  global pending, queries
  with open(wire, 'a') as f: f.write(data.hex() + '\n')
  self.query = data in QUERY_REPLIES
  if self.query:
   queries += 1
   pending = QUERY_REPLIES[data]
   if queries == 3 or (mode == 'repeated' and queries > 2):
    if mode in ('empty', 'repeated'): pending = b''
    elif mode == 'partial': pending = b'\r\r'
    elif mode == 'malformed': pending = b'X' * 19
    elif mode == 'wrong-query': pending = QUERY_REPLIES[b'MM 2B,00\r']
    elif mode == 'trailing': pending += b'X'
    elif mode == 'io-error': raise OSError('simulated query IO error')
    elif mode == 'partial-write': return len(data) - 1
   return len(data)
  pending = RESPONSES[next(name for name, command in COMMANDS.items() if command == data)]
  if mode == 'command-fault': pending = b''
  return len(data)
 def flush(self):
  if mode == 'drain-stall' and self.query and queries == 3:
   while True: time.sleep(.05)
sys.modules['serial'] = types.SimpleNamespace(Serial=Port)
sys.modules['evdev'] = None
import bose_bridge
# Advance only the bridge loop's maintenance clock, not request framing or command pacing.
bose_bridge.time = types.SimpleNamespace(
 monotonic=lambda: time.monotonic() + float(Path(clock_file).read_text()),
 time=time.time, sleep=time.sleep)
bose_bridge.DRAIN_TIMEOUT = .1
bose_bridge.serve(path, False, ir_state=str(Path(path).with_name('ir-state.json')),
 serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


@contextmanager
def bridge(mode):
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp, 'command.sock'))
        clock, wire = Path(tmp, 'clock'), Path(tmp, 'wire')
        clock.write_text('0')
        write_config(Path(tmp, 'config.json'))
        process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, str(clock), str(wire)],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not os.path.exists(path) and time.monotonic() < deadline and process.poll() is None:
                time.sleep(.01)
            assert os.path.exists(path), 'bridge failed to start'
            yield path, clock, wire
        finally:
            process.terminate()
            out, err = process.communicate(timeout=4)
            assert process.returncode == 0 and not err, (out, err)


def status(path):
    return json.loads(request(path, b'status\n'))


def advance(clock, offset):
    # Atomic replacement keeps the subprocess from seeing an empty clock file.
    update = clock.with_suffix('.next')
    update.write_text(str(offset))
    update.replace(clock)
    time.sleep(.15)  # the production adapter loop wakes at most every 100 ms


class RecoveryTests(unittest.TestCase):
    def test_bad_status_exchanges_recover_without_replaying_controls(self):
        for mode in ('empty', 'partial', 'malformed', 'wrong-query', 'trailing',
                     'pre-input', 'io-error', 'partial-write'):
            with self.subTest(mode=mode), bridge(mode) as (path, clock, wire):
                initial = status(path)
                self.assertEqual(initial['rooms']['a']['volume']['value'], 36)
                advance(clock, 6)
                failed = status(path)
                self.assertIsNone(failed['bridge']['fault'], failed['bridge'])
                self.assertFalse(failed['bridge']['serial_open'])
                self.assertEqual(failed['rooms']['a']['volume']['observed_at'],
                                 initial['rooms']['a']['volume']['observed_at'])
                writes = wire.read_text()
                self.assertFalse(status(path)['bridge']['serial_open'])
                self.assertEqual(wire.read_text(), writes, 'no immediate retry')
                advance(clock, 10)
                self.assertFalse(status(path)['bridge']['serial_open'], 'cooldown is five seconds')
                self.assertEqual(wire.read_text(), writes)
                advance(clock, 12)
                recovered = status(path)
                self.assertTrue(recovered['bridge']['serial_open'], recovered['bridge'])
                self.assertIsNone(recovered['bridge']['fault'])
                self.assertGreater(recovered['rooms']['a']['volume']['observed_at'],
                                   initial['rooms']['a']['volume']['observed_at'])
                self.assertIsNone(recovered['bridge']['last_transmission'])
                self.assertIsNone(recovered['bridge']['last_outcome'])
                # A late query reply is discarded on reopen; a real control exchange can now succeed.
                self.assertEqual(request(path, b'room-a-volume-up\n'), LIVE_MESSAGES['room-a-volume-up'])
                events = json.loads(request(path, b'events\n'))['events']
                self.assertFalse(any(e['kind'] == 'fault' for e in events), events)

    def test_repeated_status_failures_stay_recoverable_and_rate_limited(self):
        with bridge('repeated') as (path, clock, wire):
            status(path)
            for offset in (6, 12, 18):
                advance(clock, offset)
                failed = status(path)
                self.assertIsNone(failed['bridge']['fault'])
                self.assertFalse(failed['bridge']['serial_open'])
                self.assertIsNone(failed['bridge']['last_transmission'])
                writes = wire.read_text()
                for _ in range(3):
                    status(path)
                self.assertEqual(wire.read_text(), writes)

    def test_dirty_volume_refresh_uses_recoverable_path(self):
        with bridge('empty') as (path, clock, wire):
            status(path)
            self.assertEqual(request(path, b'room-a-volume-up\n'), LIVE_MESSAGES['room-a-volume-up'])
            advance(clock, 1)
            failed = status(path)
            self.assertIsNone(failed['bridge']['fault'])
            self.assertFalse(failed['bridge']['serial_open'])
            advance(clock, 7)
            recovered = status(path)
            self.assertTrue(recovered['bridge']['serial_open'])
            self.assertIsNone(recovered['bridge']['fault'])
            # Only the owner's original command was sent, never replayed during recovery.
            self.assertEqual(sum(line == '4d4d2034342c34312c30300a' for line in wire.read_text().splitlines()), 1)

    def test_control_fault_remains_latched(self):
        with bridge('command-fault') as (path, clock, wire):
            status(path)
            self.assertEqual(request(path, b'room-a-volume-up\n'), 'uncertain; no retry')
            advance(clock, 30)
            faulted = status(path)
            self.assertEqual(faulted['bridge']['fault'], 'unexpected-response')
            writes = wire.read_text()
            self.assertIn(request(path, b'room-a-volume-up\n'), ('rejected', 'rejected: not-allowed', 'rate-limited'))
            self.assertEqual(wire.read_text(), writes)

    def test_stalled_query_drain_still_latches(self):
        with bridge('drain-stall') as (path, clock, wire):
            status(path)
            advance(clock, 6)
            self.assertEqual(status(path)['bridge']['fault'], 'write-drain-timeout')
            writes = wire.read_text()
            advance(clock, 30)
            self.assertEqual(status(path)['bridge']['fault'], 'write-drain-timeout')
            self.assertEqual(wire.read_text(), writes)


if __name__ == '__main__':
    unittest.main()
