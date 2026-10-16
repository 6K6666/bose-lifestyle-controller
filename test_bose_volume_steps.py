"""Volume steps per press and same-room pacing: one volume-up or volume-down request writes `volume_steps` ticks, each one
gated, reply-checked and spaced; the first anomaly stops the press, which is then uncertain and never retried.
Fake clocks, fake writers and fake serial only."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import bose_bridge  # noqa: E402
import bose_config  # noqa: E402
import bose_ir  # noqa: E402
from bose_bridge import Bridge, SerialFault, TICK_GAP  # noqa: E402
from bose_commands import COMMANDS, LIVE_MESSAGES  # noqa: E402
from bose_config import ConfigError, ConfigStore, MAX_VOLUME_STEPS, VOLUME_GROUPS, parse_config  # noqa: E402
from bose_request import request  # noqa: E402
from helpers import BASELINE_CONFIG, write_config  # noqa: E402


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds


def make(steps, writer, partial=None, clock=None):
    clock = clock or Clock()
    seen = []
    bridge = Bridge(writer, clock=clock, result=lambda *_: None, allowed=lambda action: True,
                    fast_actions=lambda: frozenset(), fast_groups=lambda: VOLUME_GROUPS,
                    steps=(lambda: steps), sleep=clock.sleep, partial=partial or seen.append)
    return bridge, clock, seen


def press(bridge, action):
    assert bridge.submit(action)
    return bridge.handle_one()


class Burst(unittest.TestCase):
    def test_n_ticks_are_written_in_order_with_the_gap_and_the_normal_reply(self):
        writes = []
        def writer(action, data):
            writes.append((action, data)); return LIVE_MESSAGES[action]
        bridge, clock, _ = make(3, writer)
        start = clock.now
        self.assertEqual(press(bridge, 'room-a-volume-up'), LIVE_MESSAGES['room-a-volume-up'])
        self.assertEqual([a for a, _ in writes], ['room-a-volume-up'] * 3)
        self.assertEqual({d for _, d in writes}, {COMMANDS['room-a-volume-up']})
        self.assertAlmostEqual(clock.now - start, 2 * TICK_GAP)

    def test_volume_down_and_the_alias_are_multiplied_too(self):
        for action in ('volume-down', 'room-b-volume-down', 'room-c-volume-up'):
            writes = []
            bridge, _, _ = make(2, lambda a, d, w=writes: (w.append(a), LIVE_MESSAGES[a])[1])
            press(bridge, action)
            self.assertEqual(writes, [action, action], action)

    def test_one_step_is_the_old_single_write_without_sleeping(self):
        writes = []
        bridge, clock, _ = make(1, lambda a, d: (writes.append(a), LIVE_MESSAGES[a])[1])
        start = clock.now
        press(bridge, 'room-a-volume-up')
        self.assertEqual(len(writes), 1); self.assertEqual(clock.now, start)

    def test_mute_and_room_d_are_never_multiplied(self):
        for action in ('room-a-mute', 'room-b-unmute', 'room-d-volume-up', 'room-a-power-on', 'room-a-source-aux'):
            writes = []
            bridge, _, _ = make(5, lambda a, d, w=writes: (w.append(a), LIVE_MESSAGES.get(a, 'ok'))[1])
            press(bridge, action)
            self.assertEqual(len(writes), 1, action)

    def test_a_fault_on_a_later_tick_stops_the_press_and_is_uncertain(self):
        calls = []
        def writer(action, data):
            calls.append(action)
            if len(calls) == 3: raise SerialFault('unexpected-response', attempted=True)
            return LIVE_MESSAGES[action]
        bridge, _, seen = make(5, writer)
        bridge.submit('room-a-volume-up')
        with self.assertRaises(SerialFault):
            bridge.handle_one()
        self.assertEqual(len(calls), 3, 'no tick after the failing one')
        self.assertEqual(seen, ['room-a-volume-up'])

    def test_a_not_attempted_failure_after_ticks_were_written_is_not_reported_as_not_sent(self):
        calls = []
        def writer(action, data):
            calls.append(action)
            if len(calls) == 3: raise SerialFault('transport-lost-before-write', attempted=False)
            return LIVE_MESSAGES[action]
        bridge, clock, seen = make(5, writer)
        bridge.submit('room-a-volume-up')
        stamp = clock.now
        with self.assertRaises(SerialFault) as caught:
            bridge.handle_one()
        self.assertTrue(caught.exception.attempted, 'ticks went out: the press is not "never written"')
        self.assertEqual(seen, ['room-a-volume-up'])
        self.assertGreater(bridge.last, stamp, 'pacing is kept, not restored')

    def test_a_rejected_reply_on_a_later_tick_is_uncertain(self):
        calls = []
        def writer(action, data):
            calls.append(action)
            return 'rejected' if len(calls) == 2 else LIVE_MESSAGES[action]
        bridge, _, seen = make(4, writer)
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'uncertain; no retry')
        self.assertEqual(len(calls), 2); self.assertEqual(seen, ['room-a-volume-up'])

    def test_pacing_after_a_burst_runs_from_the_last_tick(self):
        bridge, clock, _ = make(3, lambda a, d: LIVE_MESSAGES[a])
        press(bridge, 'room-a-volume-up')
        last_tick = clock.now
        clock.now = last_tick + .1
        self.assertFalse(bridge.submit('room-a-volume-up'), 'inside a quarter second of the last tick')
        clock.now = last_tick + .3
        self.assertTrue(bridge.submit('room-a-volume-up'))

    def test_a_press_that_would_run_out_of_time_stops_and_is_uncertain(self):
        clock = Clock()
        calls = []
        def slow(action, data):
            calls.append(action)
            clock.now += 1.3                                  # a slow but valid exchange
            return LIVE_MESSAGES[action]
        bridge, _, seen = make(5, slow, clock=clock)
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'uncertain; no retry')
        self.assertLess(len(calls), 5, 'the budget stops the press before the fifth tick')
        self.assertEqual(seen, ['room-a-volume-up'])
        self.assertLessEqual(clock.now - 100.0, bose_bridge.BURST_BUDGET + bose_bridge.READ_TIMEOUT, 'inside the caller deadline')

    def test_shutdown_stops_the_remaining_ticks(self):
        calls = []
        stop = []
        def writer(action, data):
            calls.append(action); stop.append(True)           # the stop request arrives during the first tick
            return LIVE_MESSAGES[action]
        clock = Clock()
        seen = []
        bridge = Bridge(writer, clock=clock, result=lambda *_: None, allowed=lambda a: True, fast_actions=lambda: frozenset(),
                        fast_groups=lambda: VOLUME_GROUPS, steps=lambda: 5, sleep=clock.sleep, partial=seen.append,
                        cancelled=lambda: bool(stop))
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'uncertain; no retry')
        self.assertEqual(len(calls), 1); self.assertEqual(seen, ['room-a-volume-up'])

    def test_a_mismatching_return_on_the_first_tick_stops_a_multi_step_press(self):
        calls = []
        def writer(action, data):
            calls.append(action); return 'bad' if len(calls) == 1 else LIVE_MESSAGES[action]
        bridge, _, seen = make(5, writer)
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'uncertain; no retry')
        self.assertEqual(len(calls), 1); self.assertEqual(seen, ['room-a-volume-up'])
        calls.clear()
        bridge, _, _ = make(1, writer)
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'bad', 'one step keeps the old pass-through')

    def test_a_late_wake_after_the_gap_starts_no_tick(self):
        clock = Clock()
        calls = []
        def late_sleep(seconds):
            clock.now += 5.1                                  # the process was suspended: it wakes long after the budget
        def writer(action, data):
            calls.append(action); return LIVE_MESSAGES[action]
        seen = []
        bridge = Bridge(writer, clock=clock, result=lambda *_: None, allowed=lambda a: True, fast_actions=lambda: frozenset(),
                        fast_groups=lambda: VOLUME_GROUPS, steps=lambda: 5, sleep=late_sleep, partial=seen.append)
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'uncertain; no retry')
        self.assertEqual(len(calls), 1); self.assertEqual(seen, ['room-a-volume-up'])

    def test_shutdown_during_the_gap_is_seen_after_the_gap(self):
        clock = Clock()
        flag = []
        calls = []
        def sleep_then_stop(seconds):
            clock.sleep(seconds); flag.append(True)           # the stop request arrives while sleeping
        seen = []
        bridge = Bridge(lambda a, d: (calls.append(a), LIVE_MESSAGES[a])[1], clock=clock, result=lambda *_: None,
                        allowed=lambda a: True, fast_actions=lambda: frozenset(), fast_groups=lambda: VOLUME_GROUPS,
                        steps=lambda: 5, sleep=sleep_then_stop, partial=seen.append, cancelled=lambda: bool(flag))
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'uncertain; no retry')
        self.assertEqual(len(calls), 1, 'no write after the stop request')

    def test_the_end_of_a_burst_is_recorded_for_input_that_arrived_during_it(self):
        walls = iter([500.0, 501.0, 502.0, 503.0])
        bridge, clock, _ = make(3, lambda a, d: LIVE_MESSAGES[a])
        bridge.wall = lambda: next(walls)
        self.assertEqual(bridge.burst_end_wall, 0.0)
        press(bridge, 'room-a-volume-up')
        self.assertEqual(bridge.burst_end_wall, 500.0)
        bridge2, _, _ = make(1, lambda a, d: LIVE_MESSAGES[a])
        press(bridge2, 'room-a-volume-up')
        self.assertEqual(bridge2.burst_end_wall, 0.0, 'a single write does not change the cutoff')

    def test_a_rejected_first_tick_is_still_just_rejected(self):
        bridge, clock, seen = make(4, lambda a, d: 'rejected')
        before = bridge.last
        self.assertEqual(press(bridge, 'room-a-volume-up'), 'rejected')
        self.assertEqual(bridge.last, before); self.assertEqual(seen, [])

    def test_the_budget_keeps_a_burst_inside_the_request_deadline(self):
        # The press stops starting ticks that cannot finish inside the budget; the budget plus one read timeout stays under
        # Home Assistant's 5 s request deadline with room for SSH overhead.
        self.assertLessEqual(bose_bridge.BURST_BUDGET + bose_bridge.READ_TIMEOUT, 4.5)
        self.assertLessEqual((MAX_VOLUME_STEPS - 1) * TICK_GAP + MAX_VOLUME_STEPS * 0.25, 2.5)
        self.assertEqual(MAX_VOLUME_STEPS, bose_ir.MAX_VOLUME_STEPS)
        self.assertEqual(MAX_VOLUME_STEPS, 5)


class Pacing(unittest.TestCase):
    def setUp(self):
        self.bridge, self.clock, _ = make(1, lambda a, d: LIVE_MESSAGES[a])

    def at(self, seconds, action):
        self.clock.now = 100.0 + seconds
        return self.bridge.submit(action)

    def test_every_a_b_c_room_has_the_quarter_second_volume_pacing(self):
        for up, down in (('room-a-volume-up', 'volume-down'), ('room-b-volume-up', 'room-b-volume-down'),
                         ('room-c-volume-up', 'room-c-volume-down')):
            bridge, clock, _ = make(1, lambda a, d: LIVE_MESSAGES[a])
            self.assertTrue(bridge.submit(up)); bridge.handle_one()
            clock.now += .26
            self.assertTrue(bridge.submit(down), up)
            bridge.handle_one()
            clock.now += .1
            self.assertFalse(bridge.submit(up), 'faster than a quarter second is dropped')

    def test_a_different_room_or_a_different_function_stays_at_one_second(self):
        self.assertTrue(self.at(0, 'room-a-volume-up')); self.bridge.handle_one()
        self.assertFalse(self.at(.3, 'room-b-volume-up'), 'other room, same quarter second rule does not apply')
        self.assertTrue(self.at(1.1, 'room-b-volume-up')); self.bridge.handle_one()
        self.assertFalse(self.at(1.4, 'room-b-mute'))

    def test_room_d_stays_at_one_second(self):
        self.assertTrue(self.at(0, 'room-d-volume-up')); self.bridge.handle_one()
        self.assertFalse(self.at(.3, 'room-d-volume-down'))


class Config(unittest.TestCase):
    def data(self, **extra):
        return json.dumps({**BASELINE_CONFIG, **extra}).encode()

    def test_the_key_is_optional_and_validated(self):
        self.assertNotIn('volume_steps', parse_config(self.data()))
        for steps in (1, 2, 5):
            self.assertEqual(parse_config(self.data(volume_steps=steps))['volume_steps'], steps)
        for bad in (0, 6, -1, True, False, '2', 2.0, None, [2]):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                parse_config(self.data(volume_steps=bad))

    def test_the_setter_validates_and_never_writes_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'config.json')
            write_config(path)
            store = ConfigStore(path)
            for bad in (0, 6, True, '3', 2.5, None):
                with self.assertRaises(ValueError, msg=repr(bad)):
                    store.set_volume_steps(bad)
            store.set_volume_steps(3)
            self.assertEqual(json.loads(path.read_text())['volume_steps'], 3)
            self.assertEqual(bose_config.volume_steps(store.config), 3)
            store.set_volume_steps(1)
            self.assertEqual(set(json.loads(path.read_text())), set(BASELINE_CONFIG),
                             'back to one step the file is the old five-key format again')

    def test_manage_op_validation_and_status_validator(self):
        for steps in (1, 3, 5):
            bose_ir.validate_management({'op': 'set_volume_steps', 'steps': steps})
        for bad in (0, 6, True, '2', 1.5, None):
            with self.assertRaises(ValueError):
                bose_ir.validate_management({'op': 'set_volume_steps', 'steps': bad})
        with self.assertRaises(ValueError):
            bose_ir.validate_management({'op': 'set_volume_steps'})
        fixture = json.loads((ROOT / 'tests/fixtures/status_v4.json').read_text())
        for config_extra in ({}, {'volume_steps': 3}):
            payload = json.loads(json.dumps(fixture))
            payload['config'].update(config_extra)
            payload['generated_at'] = time.time()
            bose_ir.validate_status(json.dumps(payload))
        payload = json.loads(json.dumps(fixture))
        payload['config']['volume_steps'] = 9
        with self.assertRaises(ValueError):
            bose_ir.validate_status(json.dumps(payload))
        self.assertEqual((ROOT / 'bose_ir.py').read_bytes(), (ROOT / 'custom_components/bose_lifestyle/protocol.py').read_bytes())


FAKE = r'''
import sys,types,time
from pathlib import Path
from bose_commands import COMMANDS,RESPONSES
mode,path,log,state=sys.argv[1:5]
class Port:
 def __init__(self,**kwargs): self.pending=b''; self.writes=0
 def open(self): pass
 def close(self): pass
 @property
 def in_waiting(self): return len(self.pending)
 def read(self,n):
  if n==0:
   self.zero_reads=getattr(self,'zero_reads',0)+1   # per tick: the pre-input read, then the post-reply read
   if self.zero_reads==3 and mode=='preread': time.sleep(3.6)            # the second tick's pre-input read
   if self.zero_reads==3 and mode=='sigterm_pre':
    import os,signal; os.kill(os.getpid(), signal.SIGTERM)
  out,self.pending=self.pending[:n],self.pending[n:]
  if mode=='slow' and out: time.sleep(0.8)
  return out
 def write(self,data):
  with open(log,'a') as f: f.write('TX '+data.hex()+' TO %s %s\n' % (getattr(self,'timeout',None),getattr(self,'write_timeout',None)))
  self.writes+=1
  if mode=='slowops' and self.writes==2: time.sleep(1.8)
  name=next(k for k in COMMANDS if COMMANDS[k]==data)
  if mode=='wrong2' and self.writes==2: self.pending=b'\r\r\r\r>.C99999999999\r'
  else: self.pending=RESPONSES[name]
  return len(data)
 def flush(self):
  if mode=='slowops' and self.writes==2: time.sleep(2.8)
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
from bose_bridge import serve
serve(path,False,None,state,serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


@contextmanager
def serve(tmp, mode, steps):
    path, log, state = tmp + '/command.sock', Path(tmp, 'serial.log'), tmp + '/ir-state.json'
    config = dict(BASELINE_CONFIG)
    if steps != 1: config['volume_steps'] = steps
    write_config(Path(tmp, 'config.json'), config)
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, str(log), state], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(200):
            if os.path.exists(path): break
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(.02)
        time.sleep(1.05)   # past the startup interval
        yield path, log, process
    finally:
        process.kill(); process.communicate(timeout=5)


def sent(log):
    return [] if not log.exists() else [l[3:].split(' ')[0] for l in log.read_text().splitlines() if l.startswith('TX ')]


def timeouts(log):
    rows = [l.split(' ') for l in log.read_text().splitlines() if l.startswith('TX ')]
    return [(r[3], r[4]) for r in rows]


class Serve(unittest.TestCase):
    def test_one_request_writes_the_configured_ticks_and_replies_once(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'ok', 3) as (path, log, process):
            started = time.monotonic()
            reply = request(path, b'room-b-volume-up\n')
            took = time.monotonic() - started
            self.assertEqual(reply, LIVE_MESSAGES['room-b-volume-up'])
            self.assertEqual(sent(log), [COMMANDS['room-b-volume-up'].hex()] * 3)
            self.assertLess(took, 3.0)
            status = json.loads(request(path, b'status\n'))
            self.assertEqual(status['config']['volume_steps'], 3)
            self.assertIsNone(status['bridge']['fault'])
            self.assertEqual(status['bridge']['last_transmission']['outcome'], 'transmitted-with-matching-response')

    def test_a_bad_reply_on_tick_two_latches_and_stops_with_two_writes(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'wrong2', 5) as (path, log, process):
            self.assertEqual(request(path, b'room-a-volume-up\n'), 'uncertain; no retry')
            self.assertEqual(len(sent(log)), 2, 'nothing after the failing tick')
            status = json.loads(request(path, b'status\n'))
            self.assertEqual(status['bridge']['fault'], 'unexpected-response')
            self.assertEqual(status['bridge']['last_outcome']['outcome'], 'uncertain')
            time.sleep(1.05)
            self.assertIn(request(path, b'room-a-volume-up\n'), ('rejected', 'rejected: not-allowed'))
            self.assertEqual(len(sent(log)), 2)

    def test_status_is_answered_while_a_burst_is_running(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'ok', 5) as (path, log, process):
            box = []
            presser = threading.Thread(target=lambda: box.append(request(path, b'room-a-volume-up\n')))
            presser.start()
            time.sleep(.3)                                   # inside the burst: the loop is busy writing ticks
            status = json.loads(request(path, b'status\n'))   # a poll arriving now is answered, not rejected as stale
            self.assertEqual(status['bridge']['mode'], 'live')
            presser.join(10)
            self.assertEqual(box, [LIVE_MESSAGES['room-a-volume-up']])
            self.assertEqual(len(sent(log)), 5)
            time.sleep(1.05)
            threading.Thread(target=lambda: request(path, b'room-a-volume-up\n')).start()
            time.sleep(.3)
            late = request(path, b'room-a-volume-up\n')     # a second press queued behind a burst is dropped, not run
            self.assertIn(late, ('rejected', 'rate-limited'))
            time.sleep(2.5)
            self.assertEqual(len(sent(log)), 10, 'the queued press added no ticks')

    def test_slow_valid_exchanges_end_the_press_inside_the_budget(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'slow', 5) as (path, log, process):
            started = time.monotonic()
            reply = request(path, b'room-a-volume-up\n')
            took = time.monotonic() - started
            self.assertEqual(reply, 'uncertain; no retry')
            self.assertLess(len(sent(log)), 5)
            self.assertLess(took, 4.2, 'the press ends before the caller\'s 5 s deadline')
            status = json.loads(request(path, b'status\n'))
            self.assertIsNone(status['bridge']['fault'], 'a press that ran out of time is not a serial fault')
            self.assertEqual(status['bridge']['last_outcome']['outcome'], 'uncertain')

    def test_serial_timeouts_shrink_inside_a_press_and_are_restored(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'ok', 3) as (path, log, process):
            request(path, b'room-a-volume-up\n')
            rows = timeouts(log)
            self.assertEqual(len(rows), 3)
            for read_timeout, write_timeout in rows:
                self.assertLessEqual(float(read_timeout), 1.0); self.assertLessEqual(float(write_timeout), 2.0)
            self.assertLess(float(rows[2][0]), 1.01)
            frame = b'manage\n' + json.dumps({'op': 'set_volume_steps', 'steps': 1}).encode()
            request(path, frame)
            time.sleep(1.05)
            request(path, b'room-a-volume-down\n')
            self.assertEqual([float(x) for x in timeouts(log)[-1]], [1.0, 2.0], 'a single press runs with the original timeouts again')

    def test_shutdown_during_the_gap_sends_no_further_tick(self):
        import signal
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'ok', 5) as (path, log, process):
            import threading
            box = []
            presser = threading.Thread(target=lambda: box.append(request(path, b'room-a-volume-up\n')))
            presser.start()
            for _ in range(100):
                if sent(log): break
                time.sleep(.005)
            time.sleep(.1)                                   # inside the first gap
            process.send_signal(signal.SIGTERM)
            presser.join(10)
            self.assertEqual(len(sent(log)), 1, 'the stop request ended the press after the tick in progress')

    def test_a_budget_that_expires_before_the_write_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'preread', 2) as (path, log, process):
            self.assertEqual(request(path, b'room-a-volume-up\n'), 'uncertain; no retry')
            self.assertEqual(len(sent(log)), 1, 'the second tick was not started after the budget ran out')
            status = json.loads(request(path, b'status\n'))
            self.assertIsNone(status['bridge']['fault']); self.assertEqual(status['bridge']['last_outcome']['outcome'], 'uncertain')

    def test_shutdown_during_pre_write_work_sends_nothing_more(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'sigterm_pre', 2) as (path, log, process):
            try: request(path, b'room-a-volume-up\n')
            except OSError: pass
            process.wait(10)
            self.assertEqual(len(sent(log)), 1, 'the stop request arrived before the second write')

    def test_slow_operations_cannot_each_use_the_whole_allowance(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'slowops', 2) as (path, log, process):
            started = time.monotonic()
            self.assertEqual(request(path, b'room-a-volume-up\n'), 'uncertain; no retry')
            took = time.monotonic() - started
            self.assertLess(took, 4.5, 'write, drain and read share one budget')
            status = json.loads(request(path, b'status\n'))
            self.assertEqual(status['bridge']['fault'], 'write-drain-timeout')
            self.assertEqual(len(sent(log)), 2)

    def test_the_default_is_one_write(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'ok', 1) as (path, log, process):
            self.assertEqual(request(path, b'room-c-volume-down\n'), LIVE_MESSAGES['room-c-volume-down'])
            self.assertEqual(len(sent(log)), 1)

    def test_set_volume_steps_through_manage_changes_the_next_press(self):
        with tempfile.TemporaryDirectory() as tmp, serve(tmp, 'ok', 1) as (path, log, process):
            frame = b'manage\n' + json.dumps({'op': 'set_volume_steps', 'steps': 2}).encode()
            self.assertEqual(json.loads(request(path, frame)), {'ok': True, 'result': {}})
            self.assertEqual(json.loads(Path(tmp, 'config.json').read_text())['volume_steps'], 2)
            self.assertEqual(request(path, b'room-a-volume-up\n'), LIVE_MESSAGES['room-a-volume-up'])
            self.assertEqual(len(sent(log)), 2)


if __name__ == '__main__':
    unittest.main()
