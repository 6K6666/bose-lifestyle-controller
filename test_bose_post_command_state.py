"""A transmitted command is never reported as rejected, and an IR receiver is never marked removed,
because the mute state could not be saved afterwards. Fake serial and fake IR only."""
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from helpers import write_config  # noqa: E402
from bose_commands import COMMANDS, LIVE_MESSAGES  # noqa: E402
from bose_request import request  # noqa: E402

IR_CONFIG = {'ir_device': '/dev/input/by-id/test-receiver', 'ir_name': 'test receiver', 'ir_keys': [101, 102, 103]}
PROFILES = {'c' * 32: {'name': 'Test remote', 'model': 'ir-remote', 'active': True,
                       'mappings': {'volume-up': {'code': 101, 'enabled': True},
                                    'volume-down': {'code': 102, 'enabled': True},
                                    'mute': {'code': 103, 'enabled': True}}}}
MATCHED = 'transmitted-with-matching-response'

FAKE = r'''
import os,sys,types,time
path,log,state,ir_config,fd,flag=sys.argv[1:7]
from bose_commands import COMMANDS,RESPONSES
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
  self.pending=RESPONSES[next(k for k in COMMANDS if COMMANDS[k]==data)]
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
from bose_ir import RemoteManager
original=RemoteManager._save
def failing(self,profiles,mute):
 if os.path.exists(flag): raise OSError('simulated full disk')
 return original(self,profiles,mute)
RemoteManager._save=failing
from bose_bridge import serve
serve(path,False,ir_config,state,serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate(): return True
        time.sleep(.02)
    return False


def status(path):
    return json.loads(request(path, b'status\n'))


def sent(log):
    if not log.exists(): return []
    return [bytes.fromhex(line[3:]) for line in log.read_text().splitlines() if line.startswith('TX ')]


@contextmanager
def bridge(tmp):
    path, log, state, ir_path = tmp + '/command.sock', Path(tmp, 'serial.log'), tmp + '/ir-state.json', tmp + '/ir.json'
    flag = Path(tmp, 'disk-full')
    Path(ir_path).write_text(json.dumps(IR_CONFIG))
    Path(state).write_text(json.dumps({'schema': 2, 'bridge_id': 'b' * 32, 'profiles': PROFILES, 'mute_state': None}))
    data = {'schema': 1, 'zones': {z: {'enabled': z == 'a'} for z in 'abcd'},
            'sources': {s: True for s in ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')},
            'unverified': {z: False for z in 'abcd'}, 'ir': {'zone': 'a'}}
    write_config(Path(tmp, 'config.json'), data)
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen([sys.executable, '-u', '-c', FAKE, path, str(log), state, ir_path, str(read_fd), str(flag)],
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, pass_fds=(read_fd,))
    os.close(read_fd)
    try:
        assert wait_for(lambda: os.path.exists(path) or process.poll() is not None, 6)
        assert os.path.exists(path), process.communicate()
        assert wait_for(lambda: status(path)['bridge']['ir_attached'], 6), 'IR receiver never attached'
        yield SimpleNamespace(path=path, log=log, flag=flag, process=process, pipe=write_fd)
    finally:
        os.close(write_fd)
        process.terminate()
        process.communicate(timeout=4)


def events(path):
    return json.loads(request(path, b'events\n'))['events']


def test_socket_mute_with_unsaved_state_is_still_transmitted():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp) as ctx:
        ctx.flag.write_text('x')
        time.sleep(1.05)
        reply = request(ctx.path, b'room-a-mute\n')
        assert reply == LIVE_MESSAGES['room-a-mute'], reply
        bridge_state = status(ctx.path)
        health = bridge_state['bridge']
        assert health['last_transmission']['outcome'] == MATCHED, health
        assert health['last_outcome']['outcome'] == MATCHED, health['last_outcome']   # not overwritten with "rejected"
        assert health['fault'] is None and health['serial_open'] is True
        assert bridge_state['ir']['mute_state'] is None                            # unknown, never guessed
        assert sent(ctx.log) == [COMMANDS['room-a-mute']]
        assert any(e['kind'] == 'manage' and 'mute' in e['detail'] for e in events(ctx.path)), events(ctx.path)
        assert ctx.process.poll() is None
    print('A saved-state failure after a matching mute reply keeps the transmitted outcome and an unknown mute state')


def test_ir_mute_with_unsaved_state_keeps_the_receiver_attached():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp) as ctx:
        ctx.flag.write_text('x')
        time.sleep(1.05)
        os.write(ctx.pipe, b'103\n')
        assert wait_for(lambda: len(sent(ctx.log)) == 1), sent(ctx.log)
        assert wait_for(lambda: status(ctx.path)['bridge']['last_outcome']['outcome'] == MATCHED)
        health = status(ctx.path)['bridge']
        assert health['ir_attached'] is True, health
        assert not any(e['kind'] == 'ir' and 'removed' in e['detail'] for e in events(ctx.path)), events(ctx.path)
        # The next press still works through the same receiver: state was unknown, so it sends mute again.
        time.sleep(1.05)
        os.write(ctx.pipe, b'103\n')
        assert wait_for(lambda: len(sent(ctx.log)) == 2), sent(ctx.log)
        assert sent(ctx.log) == [COMMANDS['room-a-mute'], COMMANDS['room-a-mute']]
    print('An unsaved mute state never detaches the IR receiver, and the next press resolves from an unknown state')


def test_unmute_failure_path_is_also_contained():
    with tempfile.TemporaryDirectory() as tmp, bridge(tmp) as ctx:
        time.sleep(1.05)
        assert request(ctx.path, b'room-a-mute\n') == LIVE_MESSAGES['room-a-mute']     # saved normally
        assert status(ctx.path)['ir']['mute_state'] is True
        ctx.flag.write_text('x')
        time.sleep(1.05)
        assert request(ctx.path, b'room-a-unmute\n') == LIVE_MESSAGES['room-a-unmute']
        health = status(ctx.path)['bridge']
        assert health['last_outcome']['outcome'] == MATCHED, health['last_outcome']
        assert status(ctx.path)['ir']['mute_state'] is None
    print('An unsaved unmute is contained the same way')


if __name__ == '__main__':
    test_socket_mute_with_unsaved_state_is_still_transmitted()
    test_ir_mute_with_unsaved_state_keeps_the_receiver_attached()
    test_unmute_failure_path_is_also_contained()
