#!/usr/bin/env python3
"""One local Bose writer, accessed by restricted SSH and optional IR."""
import argparse
import copy
from contextlib import suppress
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import socket
import termios
import threading
import time
from bose_commands import (DEFAULT_PORT, DEFAULT_BAUD, VOLUME_DOWN, COMMANDS, CATALOG, RESPONSES, LIVE_MESSAGES,
                           REFUSED_MESSAGE, expected_reply, classify_reply)
from bose_config import (ConfigStore, CommitError, resolve_ir_action, fast_volume_actions, VOLUME_GROUPS, VOLUME_STEP_ACTIONS,
                         MAX_VOLUME_STEPS, volume_steps)
from bose_request import read_to_eof
from bose_events import EventLog
from bose_ir import RemoteManager, IRStateError, strict_json, validate_management, MAX_REQUEST, BRIDGE_OPS

SOCKET = '/run/bose-control/command.sock'
IR_STATE = '/var/lib/bose-control/ir-state.json'
MAX_RESPONSE_BYTES = 128
SERIAL_IO_ERRORS = (OSError, termios.error)   # termios.error (tcdrain, line settings) is not an OSError subclass
NOT_ALLOWED = 'rejected: not-allowed'

SERIAL_PREFIX = '/dev/serial/by-id/'
SERIAL_NAME = re.compile(r'[A-Za-z0-9._+:-]+')


def serial_port_arg(value):
    """--serial-port: an absolute path under /dev/serial/by-id/, nothing else."""
    name = value[len(SERIAL_PREFIX):] if value.startswith(SERIAL_PREFIX) else ''
    if not SERIAL_NAME.fullmatch(name) or '..' in name.split('/'):
        raise argparse.ArgumentTypeError('--serial-port must be an absolute path under ' + SERIAL_PREFIX)
    return value


def load_ir_config(path):
    """The exact ir-config.json schema; raises ValueError when the file does not match it."""
    c = json.loads(Path(path).read_text())
    if (set(c) != {'ir_device','ir_name','ir_keys'}
            or not isinstance(c['ir_name'], str)
            or not isinstance(c['ir_device'], str)
            or not c['ir_device'].startswith('/dev/input/by-id/')
            or not isinstance(c['ir_keys'], list)
            or any(type(key) is not int or key < 0 for key in c['ir_keys'])):
        raise ValueError('Invalid exact IR mapping')
    return c


DRAIN_TIMEOUT = 3.0   # a 12-byte frame at 4800 baud drains in about 25 ms; this only bounds a stalled adapter


def drain_bounded(port, timeout=None):
    """port.flush() waits for the output to reach the wire (termios.tcdrain) and has no timeout of its own.
    Run the same call, but give up after `timeout` seconds so a stalled adapter cannot freeze status and every
    other request. A stalled drain means the frame may or may not have been sent: the caller latches a fault."""
    timeout = DRAIN_TIMEOUT if timeout is None else timeout
    failure = []
    def run():
        try: port.flush()
        except BaseException as error: failure.append(error)
    worker = threading.Thread(target=run, name='serial-drain', daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive(): raise SerialFault('write-drain-timeout')
    if failure: raise failure[0]


class SerialFault(Exception):
    """Stop until the operator investigates; never restart a failed action."""
    def __init__(self, reason, attempted=True):
        super().__init__(reason)
        self.attempted = attempted


def validate_response(data, command=VOLUME_DOWN):
    return any(COMMANDS[name]==command and reply==data for name,reply in RESPONSES.items())


TICK_GAP = .25   # between the ticks of one volume press: the same spacing the bridge already allows between volume presses
# A multi-step press must end well inside the 5 s request deadline of the caller. A new tick starts only if its gap plus
# one serial read timeout still fit inside this budget, checked again after the gap; every serial operation of the press
# (write, drain, read) is then limited to the time that remains, so a slow adapter ends the press as a failed exchange.
BURST_BUDGET = 3.5
READ_TIMEOUT = 1.0   # the serial port's read timeout (open_port)
MIN_EXCHANGE = .5    # a tick is not started with less than this left (a healthy exchange takes well under 0.1 s)


class Bridge:
    def __init__(self, writer, clock=time.monotonic, result=print, *, allowed, fast_actions, fast_groups=None,
                 steps=None, sleep=time.sleep, partial=None, cancelled=None, wall=time.time):
        self.writer, self.clock, self.result = writer, clock, result
        self.pending = None
        self.allowed = allowed
        self.fast_actions = fast_actions
        self.fast_groups = fast_groups    # callable -> groups of actions paced together (a room's volume up and down)
        self.steps = steps                # callable -> ticks per volume-up or volume-down press (1 when absent)
        self.sleep = sleep
        self.partial = partial            # callable(action): mark a press that stopped after some ticks as uncertain
        self.cancelled = cancelled        # callable -> True once the bridge is shutting down: no further tick is written
        self.wall = wall
        self.burst_end_wall = 0.0         # wall time the last multi-step press ended: input stamped before it is discarded
        self.deadline = None              # monotonic time a running multi-step press must be finished by (None otherwise)
        self.last = -math.inf
        self.last_action = None
        self.lock = threading.Lock()

    def submit(self, action, retained=False, key_value=1):
        with self.lock:
            now = self.clock()
            if action not in COMMANDS or retained or key_value != 1 or not self.allowed(action):
                return False
            if self.fast_groups is not None:
                fast = any(action in group and self.last_action in group for group in self.fast_groups())
            else:
                fast_set = self.fast_actions()
                fast = action in fast_set and self.last_action in fast_set
            interval = .25 if fast else 1
            if self.pending is not None or now - self.last < interval:
                return False
            self.pending = (now, action)
            return True

    def _tick_ok(self, action, reply):
        return reply == LIVE_MESSAGES.get(action) or (isinstance(reply, str) and reply.startswith('DRY RUN'))

    def _stopped_midway(self, action):
        # Some ticks of a multi-step press were written and a later one was not: the press is uncertain, never retried.
        self.last = self.clock()
        self.burst_end_wall = self.wall()
        with suppress(Exception): self.partial(action) if self.partial else None
        with suppress(Exception): self.result('volume press stopped after some ticks; no retry')
        return 'uncertain; no retry'

    def handle_one(self):
        with self.lock:
            if self.pending is None:
                return None
            stamp, action = self.pending
            age = self.clock() - stamp
            self.pending = None
            if age > 1:
                self.result('discarded stale command')
                return None
            if not self.allowed(action):
                # Disabled between submit and write: no budget, no bytes, the writer is never called.
                return 'rejected: not-allowed'
            ticks = 1
            if self.steps is not None and action in VOLUME_STEP_ACTIONS:
                ticks = max(1, min(MAX_VOLUME_STEPS, self.steps()))
            done = 0   # ticks written so far in this press
            try:
                previous_last, previous_action = self.last, self.last_action
                reply = None
                began = self.clock()
                if ticks > 1: self.deadline = began + BURST_BUDGET
                for tick in range(ticks):
                    if tick:
                        if self.cancelled is not None and self.cancelled():
                            return self._stopped_midway(action)          # shutdown: no more ticks from this press
                        if self.deadline - self.clock() < TICK_GAP + READ_TIMEOUT:
                            return self._stopped_midway(action)          # out of time: the caller has given up by now
                        self.sleep(TICK_GAP)
                        # A late wake (a suspended process, a busy host) is checked again: nothing starts past the budget.
                        if self.cancelled is not None and self.cancelled():
                            return self._stopped_midway(action)
                        if self.deadline - self.clock() < MIN_EXCHANGE:
                            return self._stopped_midway(action)
                    self.last = self.clock()
                    self.last_action = action
                    reply = self.writer(action, COMMANDS[action])
                    if reply in ('rejected', 'rejected: not-allowed'):
                        if tick == 0:
                            self.last, self.last_action = previous_last, previous_action
                            return reply
                        return self._stopped_midway(action)   # earlier ticks were written: never report "not sent"
                    if ticks > 1 and not self._tick_ok(action, reply):
                        return self._stopped_midway(action)
                    done += 1
                if ticks > 1:
                    self.last = self.clock()                   # pacing runs from the end of the press
                    self.burst_end_wall = self.wall()
                return reply
            except SerialFault as error:
                if done:
                    error.attempted = True     # a tick already went out: keep the pacing and report uncertain
                    with suppress(Exception): self.partial(action) if self.partial else None
                    self.burst_end_wall = self.wall()
                elif not error.attempted: self.last, self.last_action = previous_last, previous_action
                with suppress(Exception): self.result('serial failure; stopped without retry')
                raise
            except Exception:
                with suppress(Exception):
                    self.result('serial failure; stopped without retry')
                raise
            finally:
                self.deadline = None




def bind_command_socket(server, path):
    """Bind the command socket so it is never more permissive than 0600, then listen.

    The umask is 0177 only while bind runs and is restored at once; the chmod that follows is kept.
    """
    old = os.umask(0o177)
    try:
        server.bind(path)
    except OSError as error:
        if error.errno == errno.EADDRINUSE:   # never delete an entry that is there: it may be a live bridge's socket
            raise OSError(error.errno, f'the command socket {path} already exists. If no bridge is running, remove that file and start again') from error
        raise
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    server.listen(1)


def serve(path=SOCKET, dry_run=False, ir_config=None, ir_state=IR_STATE, serial_port=None):
    # Keep this file in the persistent state directory and never unlink it: flock must share one inode.
    lock_path = Path(ir_state).with_name('bridge.lock')
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another bridge process already owns this state directory; refusing to start', flush=True)
            raise SystemExit(20)
        return _serve(path, dry_run, ir_config, ir_state, serial_port)
    finally:
        os.close(lock_fd)


def _serve(path=SOCKET, dry_run=False, ir_config=None, ir_state=IR_STATE, serial_port=None):
    # Unconfigured (no --serial-port and the placeholder default) opens no port and transmits nothing.
    port_path = serial_port if serial_port is not None else (None if 'PLACEHOLDER' in DEFAULT_PORT else DEFAULT_PORT)
    port = ir = None
    bound = False   # only a socket this process bound is ever unlinked: a failed second start must not delete the live bridge's
    running = True
    c = None
    next_ir_check = next_port_check = 0.0
    store = ConfigStore(Path(ir_state).with_name('config.json'))   # loaded once: edits apply only after a restart
    log = EventLog(None if dry_run else Path(ir_state).with_name('events.jsonl'))

    health = {'mode': 'dry-run' if dry_run else 'live', 'serial_open': False,
              'fault': None, 'recovery': None, 'config': store.state, 'ir_configured': bool(ir_config),
              'ir_attached': False, 'last_outcome': None, 'last_transmission': None}

    def allowed(action):
        # The one permission source: the Bridge gate, its write-time recheck, the writer, IR and status.
        return store.allowed(action, dry_run=dry_run, faulted=bool(health['fault']))
    try:
        manager = RemoteManager(ir_state)
    except IRStateError as error:
        # The file holds this bridge's pairing identity and learned remotes. Never overwrite or replace it: stop
        # here (exit 20: the unit does not restart on it) and leave recovery to an explicit operator command.
        message = (f'IR state file {ir_state} is damaged ({error.reason}) and was left unchanged. '
                   'The bridge did not start. Run "bose-lifestyle state check" for the recovery options; '
                   'do not delete the file, it holds this bridge\'s pairing identity.')
        print(message, flush=True)
        log.add('fault', None, 'ir-state-damaged: ' + error.reason)
        raise SystemExit(20)
    if not dry_run: manager.persist()

    def target_zone():
        # The configured IR/mute target; None (nothing resolves, nothing transmits) unless config.json is ok.
        return store.config['ir']['zone'] if store.state == 'ok' else None
    if store.state == 'ok' and not dry_run: manager.set_target_zone(target_zone())
    rooms = {room: {field: {'value': None, 'observed_at': None, 'age': None, 'supported': False}
                   for field in ('power', 'source', 'volume', 'mute')}
             for room in ('a', 'b', 'c', 'd')}

    def outcome(action, value):
        health['last_outcome'] = {'action': action, 'outcome': value, 'observed_at': time.time()}
        log.add('outcome', action, value)

    def config_manage(data):
        # Config ops change config.json only: they never reach the writer, the port or the wire budget.
        try:
            validate_management(data)
        except ValueError:
            return {'ok': False, 'error': 'invalid_request'}
        if dry_run:
            return {'ok': False, 'error': 'dry_run'}
        try:
            if data['op'] == 'set_zone':
                store.set_zone(data['zone'], data['enabled'])
                detail = f"zone={data['zone']} enabled={data['enabled']}"
            elif data['op'] == 'set_source':
                store.set_source(data['source'], data['connected'])
                detail = f"source={data['source']} connected={data['connected']}"
            elif data['op'] == 'set_unverified':
                store.set_unverified(data['zone'], data['allow'])
                detail = f"unverified={data['zone']} allow={data['allow']}"
            elif data['op'] == 'set_volume_steps':
                store.set_volume_steps(data['steps'])
                detail = f"volume_steps={data['steps']}"
            else:
                store.set_ir_zone(data['zone'])
                # A target change resets the mute state; a failed state write must not hide the committed config.
                with suppress(OSError): manager.set_target_zone(data['zone'])
                detail = f"ir_zone={data['zone']}"
        except CommitError as error:
            return {'ok': False, 'error': error.kind}
        log.add('manage', None, detail)
        return {'ok': True, 'result': {}}

    def snapshot():
        # Schema 4: config and actions come from the one store; no feedback capability is commissioned, so rooms stay unknown.
        health['config'] = store.state
        config = None if store.state != 'ok' else {key: copy.deepcopy(store.config[key])
                                                   for key in ('zones', 'sources', 'unverified', 'ir')}
        if config is not None and 'volume_steps' in store.config:   # shown only when set, like the file: an older Home Assistant copy
            config['volume_steps'] = store.config['volume_steps']  # still validates the status at the default
        actions = {} if config is None else {
            # The store ignores faults for catalog actions (the writer latches on them), so the fault is ANDed here.
            name: {'zone': entry.zone, 'function': entry.function, 'source': entry.source,
                   'verified': entry.verified, 'alias': entry.alias, 'allowed': allowed(name) and not health['fault']}
            for name, entry in CATALOG.items()}
        return json.dumps({'schema': 4, 'bridge_id': manager.bridge_id, 'generated_at': time.time(), 'bridge': health,
                           'config': config, 'actions': actions, 'ir': manager.status(),
                           'rooms': rooms}, separators=(',', ':'), allow_nan=False)

    def close_port():
        nonlocal port
        if port is not None:
            log.add('serial', None, 'closed')
            with suppress(Exception): port.close()
        port = None
        health['serial_open'] = False

    def open_port():
        nonlocal port, next_port_check
        next_port_check = time.monotonic() + 5
        try:
            import serial
            port = serial.Serial(port=None, baudrate=DEFAULT_BAUD, bytesize=8, parity='N', stopbits=1,
                                 timeout=1, write_timeout=2, xonxoff=False, rtscts=False,
                                 dsrdtr=False, exclusive=True)
            port.dtr = False; port.rts = False; port.port = port_path; port.open()
            health['serial_open'] = True
            health['recovery'] = None
            log.add('serial', None, 'opened')
            print('Exact serial adapter open; no startup/recovery transmission', flush=True)
        except OSError:
            close_port()
            health['recovery'] = 'waiting-for-exact-adapter'

    def stop(*_):
        nonlocal running
        running = False
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, stop)

    tuned = False   # the port's timeouts were shortened for a multi-step press and must be restored

    def writer(action, data):
        nonlocal next_port_check, tuned
        if dry_run:
            outcome(action, 'dry-run')
            message = 'DRY RUN; no port opened: ' + data.hex(' ')
        elif not allowed(action):
            # Last line before any byte: callers recheck, but the writer must never transmit a denied action.
            outcome(action, 'rejected')
            return NOT_ALLOWED
        elif port is None or health['fault']:
            outcome(action, 'rejected')
            return 'rejected'
        else:
            attempted = False
            received = b''
            logged = []
            def log_exchange():
                # After the serial exchange, never inside it: the write/read path keeps its original timing. Once per write.
                if logged: return
                logged.append(True)
                if attempted: log.add('tx', action, data.hex(' '))
                if received: log.add('rx', action, received.hex(' '))
            try:
                before = port.read(port.in_waiting)
                if before:
                    print('Unexpected pre-input RX ' + before.hex(' '), flush=True)
                    raise SerialFault('unexpected-pre-input')
                # The last line before the write: a stop request, or a press budget that ran out while this tick was
                # getting ready, means nothing is written (the press is then reported uncertain by the caller).
                left = None if bridge.deadline is None else bridge.deadline - bridge.clock()
                if not running or (left is not None and left < MIN_EXCHANGE):
                    outcome(action, 'rejected')
                    return 'rejected'
                # Inside a multi-step press every serial operation is limited to what is left of the press budget,
                # recomputed before each blocking operation so the allowances add up to the budget, not to a multiple of it.
                if left is not None:
                    port.write_timeout = max(.05, min(2.0, left)); port.timeout = max(.05, min(READ_TIMEOUT, left)); tuned = True
                elif tuned:
                    port.write_timeout = 2; port.timeout = READ_TIMEOUT; tuned = False   # the values open_port set
                attempted = True
                health['last_transmission'] = {'action': action, 'outcome': 'uncertain', 'observed_at': time.time()}
                print('TX ' + data.hex(' '), flush=True)
                if port.write(data) != len(data): raise SerialFault('partial-write')
                drain_limit = None
                if bridge.deadline is not None:
                    drain_limit = max(.05, min(DRAIN_TIMEOUT, bridge.deadline - bridge.clock()))
                drain_bounded(port, drain_limit)
                if bridge.deadline is not None:
                    port.timeout = max(.05, min(READ_TIMEOUT, bridge.deadline - bridge.clock()))
                # identify and verified commands need their exact captured reply; unverified ones follow the grammar.
                exact = action == 'identify' or CATALOG[action].verified
                expected = RESPONSES.get(action) if exact else expected_reply(data)
                received = port.read(len(expected) if expected is not None else MAX_RESPONSE_BYTES)
                received += port.read(port.in_waiting)
                print('RX ' + received.hex(' ') + ' ' + repr(received), flush=True)
                if exact:
                    verdict = 'ok' if expected is not None and received == expected and validate_response(received, data) else 'unexpected'
                else:
                    verdict = classify_reply(data, received)
                log_exchange()
                if verdict == 'unexpected': raise SerialFault('unexpected-response')
            except (*SERIAL_IO_ERRORS, SerialFault) as error:
                log_exchange()
                close_port()
                if isinstance(error, SERIAL_IO_ERRORS) and not attempted:
                    health['recovery'] = 'transport-lost-before-write'
                    next_port_check = time.monotonic() + 5
                else:
                    health['fault'] = str(error) if isinstance(error, SerialFault) else 'serial-io-during-transmission'
                    health['recovery'] = 'operator-investigation-required'
                if health['fault']: log.add('fault', action, f"{health['fault']}; rx={received.hex(' ')}")
                outcome(action, 'uncertain' if attempted else 'rejected')
                raise SerialFault(health['fault'] or health['recovery'], attempted=attempted) from error
            if verdict == 'refused':
                # The M1 answered with its own error frame: nothing changed, nothing is retried, the bridge stays live.
                health['last_transmission']['outcome'] = 'refused-by-m1'
                outcome(action, 'refused-by-m1')
                log.add('refused', action, received.hex(' '))
                message = REFUSED_MESSAGE
            else:
                health['last_transmission']['outcome'] = 'transmitted-with-matching-response'
                outcome(action, 'transmitted-with-matching-response')
                message = LIVE_MESSAGES.get(action, f'transmitted {action}; P1 confirmation required')
        print(message, flush=True)
        return message

    result = []
    def write(action, data):
        result[:] = [writer(action, data)]
        return result[0]
    def fast_actions():
        zone = target_zone()
        return fast_volume_actions(zone) if zone else frozenset()
    def partial_press(action):
        # Ticks of one multi-step volume press were written and a later one stopped: the outcome is uncertain.
        health['last_transmission'] = {'action': action, 'outcome': 'uncertain', 'observed_at': time.time()}
        outcome(action, 'uncertain')

    bridge = Bridge(write, allowed=allowed, fast_actions=fast_actions, fast_groups=lambda: VOLUME_GROUPS,
                    steps=lambda: volume_steps(store.config) if store.state == 'ok' else 1, partial=partial_press,
                    cancelled=lambda: not running)
    def keep_mute_state(action, update):
        """Remember the last-commanded mute state after a command that has already finished. A state-file error here
        must not rewrite that command's outcome or detach the receiver: the state simply becomes unknown."""
        try: update()
        except OSError:
            manager.mute_state = None
            log.add('manage', action, 'mute state not saved')

    def handle(action):
        try:
            reply = bridge.handle_one()
            if reply == NOT_ALLOWED: outcome(action, 'rejected')   # the write-time recheck refused before the writer ran
            return 'rejected' if reply is None else reply   # None = nothing pending or discarded as stale; never reuse an older reply
        except SerialFault:
            return 'uncertain; no retry' if health['last_outcome']['outcome'] == 'uncertain' else 'rejected'

    try:
        if ir_config:
            c = load_ir_config(ir_config)
            from evdev import InputDevice
        with socket.socket(socket.AF_UNIX) as server:
            bind_command_socket(server, path)
            bound = True
            print('Ready: ' + health['mode'], flush=True)
            log.add('start', None, 'ready ' + health['mode'])
            if port_path is None and not dry_run:
                log.add('serial', None, 'serial port not configured')
                print('Serial port not configured; no port will be opened', flush=True)
                next_port_check = math.inf
            while running:
                if not dry_run and port is None and not health['fault'] and time.monotonic() >= next_port_check:
                    open_port()
                if c is not None and ir is None and time.monotonic() >= next_ir_check:
                    next_ir_check = time.monotonic() + 1
                    try:
                        candidate = InputDevice(c['ir_device'])
                        if candidate.name == c['ir_name']:
                            ir = candidate; health['ir_attached'] = True
                            log.add('ir', None, 'receiver attached')
                            print('Exact IR receiver ready', flush=True)
                        else: candidate.close()
                    except OSError: pass
                ready,_,_ = select.select([server]+([ir.fd] if ir else []),[],[],.1)
                if server in ready:
                    with server.accept()[0] as client:
                        reply = 'rejected'
                        name = None
                        try:
                            data = read_to_eof(client,MAX_REQUEST+128,.25)
                            stamp, action, body = data.split(b'\n', 2)
                            if not stamp: raise ValueError('Invalid frame')
                            age = time.time() - float(stamp)
                            name = action.decode('ascii')
                            # Read-only requests may wait out a multi-step volume press (up to about 1.5 s); presses and
                            # config changes keep the strict quarter-second window so nothing queued behind a burst ever runs.
                            if not 0 <= age < (3.0 if name in ('status', 'events') else .25): raise ValueError('Stale frame')
                            if name in COMMANDS and float(stamp) < bridge.burst_end_wall:
                                raise ValueError('Stale frame')   # sent while a multi-step press was running: never started after it
                            if name != 'manage' and body: raise ValueError('Invalid frame')
                            if name == 'fault-clear':
                                health['fault'] = None
                                reply = 'fault cleared'
                            elif name == 'status':
                                reply = snapshot()
                            elif name == 'events':
                                reply = json.dumps({'events': log.recent()}, separators=(',', ':'))
                            elif name == 'manage':
                                try:
                                    request_data = strict_json(body.decode('utf-8'))
                                    op = request_data.get('op') if isinstance(request_data, dict) else None
                                    if not isinstance(op, str): op = None   # unhashable ops fall through to manage()'s invalid_request
                                    if op in BRIDGE_OPS:
                                        reply = json.dumps(config_manage(request_data), ensure_ascii=True)
                                    else:
                                        reply = json.dumps({'ok': True, 'result': manager.manage(request_data)}, ensure_ascii=True)
                                except (ValueError, TypeError, OSError) as error:
                                    reply = json.dumps({'ok': False, 'error': str(error) if isinstance(error, ValueError) else 'storage_or_request_error'})
                            elif name in COMMANDS and dry_run:
                                reply = write(name, COMMANDS[name])   # dry-run: DRY RUN reply, no port, no wire budget
                            elif name in COMMANDS and not allowed(name):
                                outcome(name, 'rejected')             # denied: nothing written, no budget
                                reply = NOT_ALLOWED
                            elif bridge.submit(name):
                                reply = handle(name)
                                zone = target_zone()
                                mute_names = {f'room-{zone}-mute', f'room-{zone}-unmute'} if zone else set()
                                if reply == LIVE_MESSAGES.get(name) and name in mute_names:
                                    keep_mute_state(name, lambda: manager.command_succeeded(name, zone))
                                elif name in mute_names and health['last_outcome'] and health['last_outcome']['outcome'] == 'uncertain':
                                    keep_mute_state(name, lambda: (setattr(manager, 'mute_state', None), manager.persist()))
                            else:
                                if allowed(name): reply = 'rate-limited'
                                outcome(name if name in COMMANDS else None, 'rejected')
                        except (ValueError, TypeError, OSError):
                            outcome(name if name in COMMANDS else None, 'rejected')
                        with suppress(OSError): client.sendall(reply.encode('ascii'))
                if ir is not None and ir.fd in ready:
                    try:
                        for event in ir.read():
                            if not 0 <= time.time()-event.timestamp() < .25: continue
                            if event.timestamp() < bridge.burst_end_wall: continue   # pressed during a multi-step press: dropped
                            if event.type != 4 or event.code != 4: continue
                            key = manager.scan_event(event.value)
                            if key:
                                zone = target_zone()
                                if zone is None:
                                    outcome(None, 'rejected')         # config not ok: nothing resolves, nothing transmits
                                    continue
                                action = resolve_ir_action(key, zone, manager.mute_state)
                                if action in COMMANDS and dry_run:
                                    write(action, COMMANDS[action])   # dry-run: DRY RUN, no wire budget
                                elif action in COMMANDS and not allowed(action):
                                    outcome(action, 'rejected')       # denied: nothing written, no budget
                                elif bridge.submit(action):
                                    reply = handle(action)
                                    if key == 'mute' and reply == LIVE_MESSAGES.get(action):
                                        keep_mute_state(action, lambda: manager.command_succeeded(action, zone))
                                    elif key == 'mute' and health['last_outcome'] and health['last_outcome']['outcome'] == 'uncertain':
                                        keep_mute_state(action, manager.mute_unknown)
                    except OSError:
                        ir.close(); ir = None; health['ir_attached'] = False
                        log.add('ir', None, 'receiver removed')
                        print('IR removed; waiting for exact receiver', flush=True)
    finally:
        if ir is not None:
            with suppress(Exception): ir.close()
        close_port()
        if bound:
            with suppress(OSError): os.unlink(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--socket',default=SOCKET)
    parser.add_argument('--ir-config')
    parser.add_argument('--serial-port', type=serial_port_arg)
    args=parser.parse_args()
    serve(args.socket,args.dry_run,args.ir_config,serial_port=args.serial_port)

if __name__=='__main__':
    try: main()
    except SerialFault: raise SystemExit(20)
