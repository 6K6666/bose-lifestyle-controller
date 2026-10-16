"""Status schema 4, the shared status validator and the bridge host forced-command exit codes. Fake serial only."""
import _bootstrap
import copy
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import socket
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
from bose_commands import CATALOG, COMMANDS, LIVE_ACTIONS, LIVE_MESSAGES, REFUSED_MESSAGE  # noqa: E402
from bose_config import SOURCES, allowed_actions  # noqa: E402
from helpers import BASELINE_CONFIG  # noqa: E402
from bose_ir import validate_status, MAX_REPLY  # noqa: E402
from bose_request import request  # noqa: E402

TOP4 = {'schema', 'bridge_id', 'generated_at', 'bridge', 'config', 'actions', 'ir', 'rooms'}
BRIDGE4 = {'mode', 'serial_open', 'fault', 'recovery', 'config', 'ir_configured', 'ir_attached', 'last_outcome', 'last_transmission'}
ENTRY = {'zone', 'function', 'source', 'verified', 'alias', 'allowed'}

# A live bridge with no serial adapter: status and manage answer, nothing can transmit.
FAKE = r'''
import sys,types
mode,path,state=sys.argv[1:4]
class Absent:
    def __init__(self,**kwargs): raise OSError('no adapter in test')
sys.modules['serial']=types.SimpleNamespace(Serial=Absent)
sys.modules['evdev']=None
import bose_config
if mode=='uncertain':
    def failed_rename(self, config):
        self._uncertain()
        raise bose_config.CommitError('uncertain')
    bose_config.ConfigStore._commit=failed_rename
from bose_bridge import serve
serve(path, False, ir_state=state, serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


@contextmanager
def bridge(tmp, mode='ok', config=BASELINE_CONFIG, raw=None):
    path, state = tmp + '/command.sock', tmp + '/ir-state.json'
    if raw is not None:
        Path(tmp, 'config.json').write_bytes(raw)
    elif config is not None:
        write_config(Path(tmp, 'config.json'), config)
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, mode, path, state],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(150):
            if os.path.exists(path): break
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(.02)
        assert os.path.exists(path)
        yield path
    finally:
        process.terminate()
        out, err = process.communicate(timeout=4)
        assert not err, (out, err)


def status(path):
    return json.loads(request(path, b'status\n'))


def record(action, outcome):
    return {'action': action, 'outcome': outcome, 'observed_at': 1.0}


def check(payload, **kwargs):
    return validate_status(json.dumps(payload), **kwargs)


with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as path:
        snap = status(path)
        assert set(snap) == TOP4 and snap['schema'] == 4, sorted(snap)
        assert set(snap['bridge']) == BRIDGE4 and snap['bridge']['config'] == 'ok'
        assert snap['config'] is not None and set(snap['config']) == {'zones', 'sources', 'unverified', 'ir'}   # volume_steps appears only when set
        assert set(snap['config']['zones']) == set('abcd') and all(set(v) == {'enabled'} for v in snap['config']['zones'].values())
        assert set(snap['config']['sources']) == set(SOURCES) and set(snap['config']['unverified']) == set('abcd')
        assert set(snap['config']['ir']) == {'zone'}
        assert set(snap['actions']) == set(CATALOG) and 'identify' not in snap['actions']
        assert all(set(entry) == ENTRY for entry in snap['actions'].values())
        assert 'room_d_live' not in snap['bridge'] and len(json.dumps(snap)) <= MAX_REPLY
with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, raw=b'{"schema": 1, "schema": 1}') as path:
        snap = status(path)
        assert snap['bridge']['config'] == 'config-invalid' and snap['config'] is None and snap['actions'] == {}
with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp, mode='uncertain') as path:
        reply = json.loads(request(path, b'manage\n' + json.dumps({'op': 'set_zone', 'zone': 'd', 'enabled': True}).encode()))
        assert reply == {'ok': False, 'error': 'uncertain'}, reply
        snap = status(path)
        assert snap['bridge']['config'] == 'config-uncertain' and snap['config'] is None and snap['actions'] == {}
print('Status schema 4 has exact keys; invalid and uncertain stores publish config null and actions {}')


with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as path:
        snap = status(path)
        allowed = {name for name, entry in snap['actions'].items() if entry['allowed']}
        gated = {n for n in LIVE_ACTIONS if n.endswith(('-source-fm', '-source-cd', '-source-tape'))}   # baseline keeps these switches off
        assert allowed == set(LIVE_ACTIONS) - {'identify'} - gated and len(LIVE_ACTIONS) == 42, allowed ^ set(LIVE_ACTIONS)
        assert allowed == allowed_actions(BASELINE_CONFIG)
        assert all(snap['actions'][n]['zone'] in 'abcd' for n in snap['actions'])
print('Status actions.allowed equals the live actions minus identify and the switched-off sources for the baseline config')


with tempfile.TemporaryDirectory() as tmp:
    with bridge(tmp) as path:
        base = status(path)
def variant(edit):
    payload = copy.deepcopy(base)
    edit(payload)
    return payload
def set_entry(**fields):
    def edit(p):
        p['actions']['room-a-mute'].update(fields)
    return edit
def drop_entry_key(p): del p['actions']['room-a-mute']['allowed']
def extra_entry_key(p): p['actions']['room-a-mute']['extra'] = 1
def bad_name(p): p['actions']['Room-A-Mute'] = p['actions'].pop('room-a-mute')
def unknown_config_bridge(p): p['bridge']['config'] = 'config-future'
def config_extra_key(p): p['config']['schema'] = 1
def zones_extra(p): p['config']['zones']['e'] = {'enabled': True}
def enabled_int(p): p['config']['zones']['a']['enabled'] = 1
def config_with_invalid(p): p['bridge']['config'] = 'config-invalid'
def config_null_ok(p): p['config'] = None
def actions_on_uncertain(p): p['config'] = None; p['bridge']['config'] = 'config-uncertain'
def top_extra(p): p['extra'] = {}
def bridge_room_d(p): p['bridge']['room_d_live'] = False
def bad_zone(p): p['actions']['room-a-mute']['zone'] = 'e'
def bad_function(p): p['actions']['room-a-mute']['function'] = 'bogus'
def source_mismatch(p): p['actions']['room-a-mute']['source'] = 'am'
def bad_source(p): p['actions']['room-a-source-am']['source'] = 'x'
def source_missing(p): p['actions']['room-a-source-am']['source'] = None
BAD_V4 = {
    'schema 5': lambda p: p.update(schema=5),
    'schema bool': lambda p: p.update(schema=True),
    'missing config': lambda p: p.pop('config'),
    'missing actions': lambda p: p.pop('actions'),
    'extra top key': top_extra,
    'room_d_live in bridge': bridge_room_d,
    'bridge.config unknown without degrade': unknown_config_bridge,
    'config with schema key': config_extra_key,
    'extra zone': zones_extra,
    'zone enabled as int': enabled_int,
    'config present while invalid': config_with_invalid,
    'config null while ok': config_null_ok,
    'actions present while uncertain': actions_on_uncertain,
    'entry missing allowed': drop_entry_key,
    'entry extra key': extra_entry_key,
    'entry zone e': bad_zone,
    'entry function bogus': bad_function,
    'source function without source name': source_missing,
    'source name on mute': source_mismatch,
    'source name unknown': bad_source,
    'entry verified as int': set_entry(verified=1),
    'entry alias as string': set_entry(alias='no'),
    'entry allowed as None': set_entry(allowed=None),
    'action name not lowercase': bad_name,
    'config is not an object': lambda p: p.update(config=5),
    'generated_at overflows float': lambda p: p.update(generated_at=10 ** 400),
}
for label, edit in BAD_V4.items():
    try:
        check(variant(edit))
    except ValueError:
        pass
    else:
        raise AssertionError('accepted: ' + label)
check(base)
print('Schema 5 and malformed schema 4 variants are rejected by the shared validator')


def last_tx(p, action, outcome='transmitted-with-matching-response'):
    p['bridge']['last_transmission'] = record(action, outcome)
    return p
tx_names = copy.deepcopy(base)
check(last_tx(tx_names, 'identify'), action_ok=COMMANDS.__contains__)
for bad in ('room-d-bogus', 'system-bogus'):
    try: check(last_tx(copy.deepcopy(base), bad), action_ok=COMMANDS.__contains__)
    except ValueError: pass
    else: raise AssertionError(bad)
try: check(last_tx(copy.deepcopy(base), None), action_ok=COMMANDS.__contains__)
except ValueError: pass
else: raise AssertionError('null last_transmission action accepted')
check(copy.deepcopy(base), action_ok=COMMANDS.__contains__)   # last_outcome null is fine
bad_outcome = copy.deepcopy(base); bad_outcome['bridge']['last_outcome'] = record('identify', 'bogus')
try: check(bad_outcome, action_ok=COMMANDS.__contains__)
except ValueError: pass
else: raise AssertionError('unknown outcome accepted')
try: check(last_tx(copy.deepcopy(base), 'identify'), action_ok=lambda name: name != 'identify')
except ValueError: pass
else: raise AssertionError('predicate ignored')
print('last_transmission is checked against COMMANDS names, including identify, when the predicate is given')


future = copy.deepcopy(base)
future['bridge']['fault'] = 'future-fault'
future['bridge']['recovery'] = 'future-recovery'
future['bridge']['config'] = 'config-future'
future['config'], future['actions'] = None, {}
try: check(future)
except ValueError: pass
else: raise AssertionError('unknown health accepted without the degrade flag')
degraded = check(future, degrade_unknown=True)
assert degraded['bridge']['fault'] == 'unknown' and degraded['bridge']['recovery'] == 'unknown'
assert degraded['bridge']['config'] == 'unknown' and degraded['config'] is None
mode_bad = copy.deepcopy(base); mode_bad['bridge']['mode'] = 'weird'
try: check(mode_bad, degrade_unknown=True)
except ValueError: pass
else: raise AssertionError('degrade relaxed a field it does not cover')
print('degrade_unknown maps unknown fault, recovery and config values to unknown and nothing else')


for legacy_schema in (2, 3):
    legacy = copy.deepcopy(base); legacy['schema'] = legacy_schema
    try: check(legacy, degrade_unknown=True)
    except ValueError: pass
    else: raise AssertionError('schema %d accepted' % legacy_schema)
print('Schema 2 and 3 payloads are rejected, even with degrade_unknown; only schema 4 validates')


@contextmanager
def fake_bridge(reply):
    """Stands in for the bridge socket: reads one framed request to EOF, returns one reply."""
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, 'command.sock')
    server = socket.socket(socket.AF_UNIX)
    server.bind(path); server.listen(1)
    def answer():
        conn, _ = server.accept()
        with conn:
            while conn.recv(4096): pass
            conn.sendall(reply.encode('ascii'))
    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        yield path
    finally:
        thread.join(5)
        server.close()
        os.unlink(path); os.rmdir(directory)


def run_cli(action, reply):
    """Runs bose_request.py as the forced command, with its socket path pointed at the fake bridge (none for a refused name)."""
    source = (ROOT / 'bose_request.py').read_text()
    marker = "SOCKET = '/run/bose-control/command.sock'"
    assert source.count(marker) == 1
    with contextlib.ExitStack() as stack:
        path = stack.enter_context(fake_bridge(reply)) if reply is not None else '/nonexistent'
        source = source.replace(marker, f'SOCKET = {path!r}')
        os.environ['SSH_ORIGINAL_COMMAND'] = action
        out, code = io.StringIO(), 0
        try:
            with contextlib.redirect_stdout(out):
                exec(compile(source, 'bose_request.py', 'exec'), {'__name__': '__main__'})
        except SystemExit as error:
            code = error.code if error.code is not None else 0
        finally:
            os.environ.pop('SSH_ORIGINAL_COMMAND', None)
    return code, out.getvalue()


assert run_cli('room-d-mute', 'rejected: not-allowed') == (0, 'rejected: not-allowed\n')
assert run_cli('room-d-mute', 'rejected')[0] == 1
assert run_cli('room-a-mute', 'uncertain; no retry')[0] == 1
assert run_cli('room-a-mute', 'rate-limited') == (0, 'rate-limited\n')
assert run_cli('room-a-mute', REFUSED_MESSAGE) == (0, REFUSED_MESSAGE + '\n')
assert run_cli('identify', LIVE_MESSAGES['identify']) == (0, LIVE_MESSAGES['identify'] + '\n')
assert run_cli('volume-down', 'DRY RUN; no port opened: ' + COMMANDS['volume-down'].hex(' '))[0] == 0
code, _ = run_cli('room-a-mute', 'garbage')
assert isinstance(code, str) and code.startswith('Bridge unavailable'), code
code, _ = run_cli('room-a-mute', 'rejected: not-allowed extra')
assert isinstance(code, str), code
status_text = json.dumps(base, separators=(',', ':'))
assert run_cli('status', status_text) == (0, status_text + '\n')
code, out = run_cli('not-a-command', None)
assert code == 'Rejected: only catalogued Bose actions are permitted' and out == ''
print('Forced command: not-allowed exits 0, rejected and uncertain exit 1, status passes through unchanged')



# L1: status actions.allowed follows the same fault and dry-run flags as the bridge gate (the gate is unchanged).
PORT_FAKE = r'''
import sys,types
mode,path,state=sys.argv[1:4]
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
  name=next(k for k in COMMANDS if COMMANDS[k]==data)
  if name=='identify': reply=RESPONSES['identify']
  elif mode=='fault' and name=='room-a-mute': reply=b'\r\r\r\r>.C99999999999\r'
  elif name in CATALOG and CATALOG[name].verified: reply=RESPONSES[name]
  else: reply=expected_reply(data)
  self.pending=reply
  return len(data)
 def flush(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
from bose_bridge import serve
serve(path, mode=='dry', ir_state=state, serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


@contextmanager
def port_bridge(tmp, mode):
    path, state = tmp + '/command.sock', tmp + '/ir-state.json'
    write_config(Path(tmp, 'config.json'), BASELINE_CONFIG)
    process = subprocess.Popen([sys.executable, '-u', '-c', PORT_FAKE, mode, path, state],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(150):
            if os.path.exists(path): break
            if process.poll() is not None: raise AssertionError(process.communicate())
            time.sleep(.02)
        assert os.path.exists(path)
        yield path
    finally:
        process.terminate()
        out, err = process.communicate(timeout=4)
        assert not err, (out, err)


def allowed_names(snapshot):
    return {name for name, entry in snapshot['actions'].items() if entry['allowed']}


def test_status_allowed_unchanged_when_healthy_live():
    with tempfile.TemporaryDirectory() as tmp:
        with port_bridge(tmp, 'ok') as path:
            snap = status(path)
            assert snap['bridge']['mode'] == 'live' and snap['bridge']['serial_open'] is True and snap['bridge']['fault'] is None
            assert allowed_names(snap) == allowed_actions(BASELINE_CONFIG) == {n for n in LIVE_ACTIONS if n != 'identify' and not n.endswith(('-source-fm', '-source-cd', '-source-tape'))}


def test_status_allowed_false_when_faulted_or_dry_run():
    with tempfile.TemporaryDirectory() as tmp:
        with port_bridge(tmp, 'dry') as path:
            snap = status(path)
            assert snap['bridge']['mode'] == 'dry-run' and snap['config']['zones']['a']['enabled'] is True
            assert snap['actions'] and not allowed_names(snap), allowed_names(snap)
    with tempfile.TemporaryDirectory() as tmp:
        with port_bridge(tmp, 'fault') as path:
            assert allowed_names(status(path)) == allowed_actions(BASELINE_CONFIG)   # healthy before the fault
            assert request(path, b'room-a-mute\n') == 'uncertain; no retry'
            snap = status(path)
            assert snap['bridge']['fault']
            assert snap['actions'] and not allowed_names(snap), allowed_names(snap)


test_status_allowed_unchanged_when_healthy_live()
test_status_allowed_false_when_faulted_or_dry_run()
print('Status actions.allowed is false for every action when faulted or in dry-run, and true when healthy and live')
