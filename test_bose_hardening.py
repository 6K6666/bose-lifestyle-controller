"""Offline stream/health/fault regression checks; fake serial only."""
import json
import ast
import os
from pathlib import Path
from runpy import run_path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from bose_commands import LIVE_MESSAGES
from bose_request import request
sys.path.insert(0, str(Path(__file__).resolve().parent / 'tests'))
from helpers import write_config, BASELINE_CONFIG

ROOT = Path(__file__).resolve().parent

# Removing EOF framing makes a correctly fragmented response fail.
with tempfile.TemporaryDirectory() as tmp:
    path = tmp + '/reply.sock'
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path); server.listen(1)
        def split_reply():
            with server.accept()[0] as client:
                while client.recv(128): pass
                reply = LIVE_MESSAGES['volume-down'].encode()
                client.sendall(reply[:12]); time.sleep(.05)
                client.sendall(reply[12:])
        thread = threading.Thread(target=split_reply)
        thread.start()
        try:
            assert request(path) == LIVE_MESSAGES['volume-down']
        finally:
            thread.join()
print('Fragmented writer reply accepted only after EOF')

from contextlib import contextmanager, suppress
from bose_commands import COMMANDS, RESPONSES, LIVE_ACTIONS

@contextmanager
def bridge_process(mode='dry'):
    with tempfile.TemporaryDirectory() as tmp:
        path = tmp + '/command.sock'
        log = tmp + '/serial.log'
        code = r'''
import sys,types,os
from pathlib import Path
mode,path,log=sys.argv[1:]
class Port:
 def __init__(self,**kwargs): self.sent=None; self.failed=False
 def open(self):
  with open(log,'a') as f: f.write('open\n')
  if mode=='absent' and not Path(log+'.present').exists(): raise OSError('missing adapter')
 def close(self): pass
 @property
 def in_waiting(self):
  with open(log,'a') as f: f.write('waiting\n')
  if mode=='transport' and not Path(log+'.lost').exists():
   Path(log+'.lost').touch(); raise OSError('disconnected before TX')
  return 1 if mode=='pre-input' else 0
 def read(self,n):
  if not n: return b''
  if mode=='uncaptured': return b'\r\r>.C24002030000\r'
  if mode=='pre-input':return b'x'
  from bose_commands import COMMANDS,RESPONSES
  data=next(RESPONSES[k] for k in RESPONSES if COMMANDS[k]==self.sent)
  self.sent=None
  if mode=='short-reply':return data[:5]
  if mode=='extra':return data+b'late'
  return b'wrong' if mode=='wrong' else data
 def write(self,data):
  with open(log,'a') as f: f.write('TX '+data.hex()+'\n')
  self.sent=data
  if mode=='write-error':raise OSError('lost during TX')
  return len(data)-1 if mode=='partial' else len(data)
 def flush(self): pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
sys.modules['evdev']=None
from bose_bridge import serve
serve(path,mode=='dry',ir_state=path+'.state',serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''
        write_config(Path(tmp, 'config.json'), dict(BASELINE_CONFIG, sources={s: True for s in BASELINE_CONFIG['sources']}))   # promoted FM/CD/Tape need their switch
        process = subprocess.Popen([sys.executable, '-u', '-c', code, mode, path, log],
                                   cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(150):
                if os.path.exists(path): break
                if process.poll() is not None:
                    raise AssertionError(process.communicate())
                time.sleep(.02)
            assert os.path.exists(path)
            for _ in range(150):   # the fake port logs 'open' as soon as the bridge first opens it; wait for that, do not race it
                if mode == 'dry' or os.path.exists(log) or process.poll() is not None: break
                time.sleep(.02)
            yield path, Path(log), process
        finally:
            process.terminate()
            out, err = process.communicate(timeout=4)
            assert not err, (out, err)

def raw_request(path, parts, eof=True):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(2); client.connect(path)
        for part in parts:
            client.sendall(part); time.sleep(.01)
        if eof:
            with suppress(OSError): client.shutdown(socket.SHUT_WR)
        reply = bytearray()
        while True:
            try: chunk = client.recv(4097)
            except ConnectionResetError: return 'connection-reset'
            if not chunk: return reply.decode('ascii')
            reply.extend(chunk)

def status(path):
    return json.loads(request(path,b'status\n'))

# Every live catalogued control must round-trip its action and exact reply.
for action in sorted(LIVE_ACTIONS):
    assert action in LIVE_MESSAGES, action
    with bridge_process('live') as (path, log, process):
        assert request(path,(action+'\n').encode())==LIVE_MESSAGES[action], action
        snapshot=status(path)
        assert snapshot['bridge']['last_transmission']['action']==action, snapshot
        assert snapshot['bridge']['last_transmission']['outcome']=='transmitted-with-matching-response', snapshot
print('Every live HA button preserves its action identity and exact bridge host response')

with bridge_process() as (path, log, process):
    assert 'DRY RUN' in raw_request(path,[str(time.time()).encode()+b'\nvol',b'ume-down\n'])
    for frame in (b'volume-down', b'volume-down\nextra\n', b'volume-down\n\n', b'\xff\n', b'x'*129):
        assert raw_request(path,[str(time.time()).encode()+b'\n'+frame]) in ('rejected','connection-reset')
    assert raw_request(path,[str(time.time()).encode()+b'\nvolume-down\n'],eof=False)=='rejected'
    assert raw_request(path,[b'nan\nvolume-down\n'])=='rejected'
    before = log.read_text() if log.exists() else ''
    snapshot=status(path)
    assert snapshot['bridge']['mode']=='dry-run'
    assert not snapshot['bridge']['serial_open']
    assert all(field['value'] is None for room in snapshot['rooms'].values() for field in room.values())
    assert (log.read_text() if log.exists() else '')==before
    for _ in range(3): assert 'DRY RUN' in request(path,b'room-b-mute\n')
print('Bounded exact request framing, cached dry status and no dry-run wire budget')

for mode in ('live','partial','wrong','write-error','short-reply','extra','pre-input','transport','absent'):
    with bridge_process(mode) as (path, log, process):
        before=log.read_text()
        initial=status(path)
        assert log.read_text()==before, 'status touched serial'
        assert initial['bridge']['serial_open']==(mode!='absent')
        assert initial['bridge']['last_transmission'] is None
        reply=request(path)
        after=status(path)
        if mode=='live':
            assert reply==LIVE_MESSAGES['volume-down']
            assert after['bridge']['last_transmission']['outcome']=='transmitted-with-matching-response'
            time.sleep(.26)  # A volume actions use a 250 ms wire interval.
            assert request(path,b'room-a-volume-up\n') == LIVE_MESSAGES['room-a-volume-up']
            assert log.read_text().count('TX ') == 2
        elif mode in ('partial','wrong','write-error','short-reply','extra'):
            assert reply=='uncertain; no retry'
            assert after['bridge']['fault']
            assert after['bridge']['last_transmission']['outcome']=='uncertain'
            time.sleep(1.05)
            assert request(path)=='rejected'
            assert process.poll() is None
        else:
            assert reply=='rejected'
            assert after['bridge']['last_transmission'] is None
            if mode=='pre-input':assert after['bridge']['fault']=='unexpected-pre-input'
        expected_tx = 2 if mode == 'live' else (0 if mode in ('pre-input','transport','absent') else 1)
        assert log.read_text().count('TX ') == expected_tx
        if mode=='transport':
            time.sleep(5.1)
            assert status(path)['bridge']['serial_open']
            assert log.read_text().count('TX ')==0,'transport recovery replayed action'
            assert request(path)==LIVE_MESSAGES['volume-down'], 'fresh action after safe reopen failed'
print('Fake-port live ACK, rate guard, latched uncertainty, absent port and zero replay on reopen')

with bridge_process('uncaptured') as (path, log, process):
    assert request(path,b'room-b-source-aux\n') == 'uncertain; no retry'
    snapshot = json.loads(raw_request(path,[str(time.time()).encode()+b'\nstatus\n']))
    assert snapshot['bridge']['fault'] == 'unexpected-response'
    assert snapshot['bridge']['last_transmission']['outcome'] == 'uncertain'
    assert process.poll() is None
    assert log.read_text().count('TX ') == 1
print('Uncaptured live response is recorded as uncertain and blocks replay')

# Live eligibility follows the catalog; replies remain independently checked.
from bose_commands import LIVE_ACTIONS
from bose_commands import COMMANDS
from bose_commands import RESPONSES
assert LIVE_ACTIONS==frozenset(RESPONSES) and len(COMMANDS)==55 and len(LIVE_ACTIONS)==42   # 30 original + identify + 11 promoted 2026-10-11
assert COMMANDS['room-a-power-on']==COMMANDS['system-power-on'] and RESPONSES['room-a-power-on']==RESPONSES['system-power-on']
assert COMMANDS['room-a-power-off']==COMMANDS['system-power-off'] and RESPONSES['room-a-power-off']==RESPONSES['system-power-off']

from bose_request import read_to_eof, validate_status
from bose_ir import MAX_REPLY
# A slow peer cannot extend the total deadline; oversize/trailing replies fail.
for payload in (b'x'*513, b'partial'):
    a,b=socket.socketpair()
    try:
        a.sendall(payload)
        start=time.monotonic()
        try:read_to_eof(b,512,.05)
        except OSError:pass
        else:raise AssertionError('overlong or incomplete frame accepted')
        assert time.monotonic()-start < .2
    finally:a.close();b.close()
with bridge_process() as (path,log,process):
    text=request(path,b'status\n')
    good=json.loads(text)
    for bad in (json.dumps({**good, 'rooms':['a','b','c']}), '{}', text+' trailing', text.replace('"schema":4','"schema":4,"schema":4'),
                text.replace('"generated_at":', '"generated_at":NaN,"ignored":'),
                text.replace('"supported":false','"supported":0'),
                text.replace('"value":null','"value":56')):
        try:validate_status(bad)
        except OSError:pass
        else:raise AssertionError('invalid status accepted')
    assert len(text.encode('ascii'))<=MAX_REPLY   # schema 4 carries the 54-entry actions map; the bridge host reads up to MAX_REPLY
print('Total stream deadline, cap and strict status schema reject invented state')

# Pre-write rejection must leave the shared actual-wire interval untouched.
from bose_bridge import Bridge, SerialFault
clock=[0.0]
b=Bridge(lambda action,data: 'rejected',clock=lambda:clock[0],allowed=lambda action:True,fast_actions=lambda:frozenset({'volume-down','room-a-volume-up'}))
assert b.submit('volume-down'); b.handle_one()
assert b.submit('room-a-volume-up'), 'pre-write rejection charged the wire interval'

with bridge_process('absent') as (path, log, process):
    assert request(path)=='rejected'
    Path(str(log)+'.present').touch()
    time.sleep(5.1)
    assert status(path)['bridge']['serial_open']
    assert 'TX ' not in log.read_text()
    assert request(path)==LIVE_MESSAGES['volume-down']
print('Late exact USB attach accepts only a fresh request and never queued work')

# Wrong identity, unplug/replug, stale events and repeated keys cannot add writes.
with tempfile.TemporaryDirectory() as tmp:
    path=tmp+'/ir.sock';config=tmp+'/ir.json'
    Path(config).write_text(json.dumps({'ir_device':'/dev/input/by-id/fake-receiver','ir_name':'exact receiver','ir_keys':[114]}))
    write_config(Path(tmp, 'config.json'))
    code=r'''
import sys,types,time,os,threading,signal
sys.modules['serial']=None
calls=[0]
class Device:
 def __init__(self,path):
  calls[0]+=1
  if calls[0]==1:raise OSError('late USB')
  self.name='wrong receiver' if calls[0]==2 else 'exact receiver'
  self.fd,self.w=os.pipe();os.write(self.w,b'x')
 def read(self):
  os.read(self.fd,1)
  if calls[0]==3:raise OSError('unplugged')
  now=time.time()
  threading.Timer(.1,lambda:os.kill(os.getpid(),signal.SIGTERM)).start()
  return [types.SimpleNamespace(type=1,code=114,value=v,timestamp=lambda stamp=stamp:stamp)
          for v,stamp in ((1,now-2),(2,now),(0,now),(1,now))]
 def close(self):os.close(self.fd);os.close(self.w)
sys.modules['evdev']=types.SimpleNamespace(InputDevice=Device)
from bose_bridge import serve
serve(sys.argv[1],True,sys.argv[2],sys.argv[1]+'.state')
'''
    check=subprocess.run([sys.executable,'-u','-c',code,path,config],cwd=ROOT,capture_output=True,text=True,timeout=6)
    assert check.returncode==0,check.stderr
    assert check.stdout.count('Exact IR receiver ready')==2,check.stdout
    assert 'IR removed' in check.stdout
    assert check.stdout.count(COMMANDS['volume-down'].hex(' '))==0,check.stdout
print('Exact IR identity, unplug/replug and unlearned-key no-transmit guard passed')
