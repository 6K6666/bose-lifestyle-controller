"""Small offline check; serial hardware must never open in dry-run."""
import _bootstrap
import os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
with patch.dict(sys.modules, {'serial': None, 'evdev': None}):
    from bose_bridge import Bridge, SerialFault, validate_response, serve
from bose_request import request
from bose_serial import VOLUME_DOWN
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from helpers import write_config
assert VOLUME_DOWN == b'MM 44,42,00\n'
now=[0.0]; writes=[]
b=Bridge(lambda action,data:writes.append(data),lambda:now[0],allowed=lambda action:True,fast_actions=lambda:frozenset({'volume-down','room-a-volume-up'}))
assert not b.submit('other') and not b.submit('volume-down',key_value=2)
assert not b.submit('volume-down',key_value=0)
assert b.submit('volume-down') and not b.submit('volume-down')
b.handle_one();assert writes==[VOLUME_DOWN]
now[0]=.249;assert not b.submit('volume-down')
now[0]=.25;assert b.submit('volume-down')
now[0]=2.1;b.handle_one();assert len(writes)==1
assert validate_response(b'\r\r\r\r>.C44200000000\r')
assert not validate_response(b'OK')
calls=[]
def fail(action,data):calls.append((action,data));raise SerialFault('failed')
b=Bridge(fail,allowed=lambda action:True,fast_actions=lambda:frozenset({'volume-down','room-a-volume-up'}));assert b.submit('volume-down')
try:b.handle_one()
except SerialFault:pass
else:raise AssertionError('failure did not stop')
b.handle_one();assert calls==[('volume-down',VOLUME_DOWN)]
# The command socket is never more permissive than 0600: it is bound under umask 0177, chmod is checked
# at the moment it runs, and the umask is restored right after.
import stat as _stat
from unittest import mock as _mock
import bose_bridge as _bridge
with tempfile.TemporaryDirectory() as tmp:
 path=tmp+'/private.sock'
 seen=[]
 real_chmod=os.chmod
 def spy(target,mode,*args,**kwargs):
  seen.append(_stat.S_IMODE(os.stat(target).st_mode))
  return real_chmod(target,mode,*args,**kwargs)
 orig=os.umask(0o000)                  # permissive on purpose: without the fix the socket would be 0777
 try:
  with _mock.patch.object(_bridge.os,'chmod',spy), socket.socket(socket.AF_UNIX) as server:
   _bridge.bind_command_socket(server,path)
   assert server.fileno()>=0
 finally:
  restored_to=os.umask(orig)
 assert restored_to==0o000 and os.umask(orig)==orig, (restored_to, orig)
 assert seen==[0o600], seen
 assert _stat.S_IMODE(os.stat(path).st_mode)==0o600
print('Command socket is bound private (0600) with the umask restored')

with tempfile.TemporaryDirectory() as tmp:
 path=tmp+'/command.sock'
 # Import guard makes any serial import fail, even in the child process.
 write_config(Path(tmp, 'config.json'))
 code="import sys;sys.modules['serial']=None;sys.modules['evdev']=None;from bose_bridge import serve;serve(sys.argv[1],True,ir_state=sys.argv[1]+'.state')"
 p=subprocess.Popen([sys.executable,'-u','-c',code,path],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=Path(__file__).resolve().parents[1])
 try:
  # Ready means the socket has its final mode AND accepts a request; no race on the file's existence.
  first=None
  for _ in range(300):
   if p.poll() is not None:raise AssertionError(p.stderr.read())
   if os.path.exists(path) and _stat.S_IMODE(os.stat(path).st_mode)==0o600:
    try:
     first=request(path,b'bad\n'); break
    except (OSError,SystemExit):pass
   time.sleep(.02)
  assert first=='rejected', first
  assert _stat.S_IMODE(os.stat(path).st_mode)==0o600
  assert 'DRY RUN' in request(path,b'volume-down\n')
  assert 'DRY RUN' in request(path,b'volume-down\n')
 finally:p.terminate();out,err=p.communicate(timeout=3)
 assert out.count(VOLUME_DOWN.hex(' '))==2,(out,err)
assert request.__name__=='request'
print('Bridge guards and full socket dry-run passed; no port opened')

check=subprocess.run([sys.executable,str(ROOT / 'bose_request.py')],env={**os.environ,'SSH_ORIGINAL_COMMAND':'id'},capture_output=True,text=True)
assert check.returncode!=0 and 'Rejected' in check.stderr
print('Forced-command allowlist passed')
# A late USB receiver feeds the local writer without any HA/MQTT/network client.
with tempfile.TemporaryDirectory() as tmp:
 config=tmp+'/ir.json';path=tmp+'/ir.sock'
 import json
 Path(config).write_text(json.dumps({'ir_device':'/dev/input/by-id/fake-receiver','ir_name':'exact receiver','ir_keys':[114]}))
 write_config(Path(tmp, 'config.json'))
 code='''
import sys,os,types,time,signal,threading
sys.modules['serial']=None
calls=[0]
class Device:
 name='exact receiver'
 def __init__(self,path):
  calls[0]+=1
  if calls[0]==1:raise OSError('USB still starting')
  self.fd,self.w=os.pipe();os.write(self.w,b'x')
 def read(self):
  os.read(self.fd,1)
  threading.Timer(.1,lambda:os.kill(os.getpid(),signal.SIGTERM)).start()
  stamp=time.time()
  return [types.SimpleNamespace(type=1,code=114,value=v,timestamp=lambda:stamp) for v in (2,1,0,2)]
 def close(self):os.close(self.fd);os.close(self.w)
sys.modules['evdev']=types.SimpleNamespace(InputDevice=Device)
from bose_bridge import serve
serve(sys.argv[1],True,sys.argv[2],sys.argv[1]+'.state')
'''
 p=subprocess.run([sys.executable,'-u','-c',code,path,config],capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1],timeout=4)
 assert p.returncode==0,p.stderr
 assert 'Exact IR receiver ready' in p.stdout
 assert p.stdout.count(VOLUME_DOWN.hex(' '))==0,p.stdout
print('Unlearned IR keys produce no serial bytes')
# Learning a decoded scan leaves it staged for review and never reaches the writer.
with tempfile.TemporaryDirectory() as tmp:
 config=tmp+'/ir.json';path=tmp+'/ir.sock';state=tmp+'/state.json'
 Path(config).write_text(json.dumps({'ir_device':'/dev/input/by-id/fake-receiver','ir_name':'exact receiver','ir_keys':[]}))
 write_config(Path(tmp, 'config.json'))
 code=r'''
import sys,types,os,time,threading
sys.modules['serial']=None
class Device:
 name='exact receiver'
 def __init__(self,path):self.fd,self.w=os.pipe();threading.Timer(.8,lambda:os.write(self.w,b'x')).start()
 def read(self):
  os.read(self.fd,1);stamp=time.time()
  return [types.SimpleNamespace(type=4,code=4,value=0x1234,timestamp=lambda:stamp),
          types.SimpleNamespace(type=1,code=115,value=1,timestamp=lambda:stamp)]
 def close(self):os.close(self.fd);os.close(self.w)
sys.modules['evdev']=types.SimpleNamespace(InputDevice=Device)
from bose_bridge import serve
serve(sys.argv[1],True,sys.argv[2],sys.argv[3])
'''
 p=subprocess.Popen([sys.executable,'-u','-c',code,path,config,state],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=Path(__file__).resolve().parents[1])
 try:
  for _ in range(100):
   if os.path.exists(path):break
   time.sleep(.02)
  sid=json.loads(request(path,b'manage\n'+json.dumps({'op':'begin','name':'Test','model':'ir-remote'}).encode()))['result']['session_id']
  request(path,b'manage\n'+json.dumps({'op':'capture','session_id':sid}).encode())
  for _ in range(100):
   snap=json.loads(request(path,b'manage\n'+json.dumps({'op':'session','session_id':sid}).encode()))['result']
   if snap['candidate'] is not None:break
   time.sleep(.02)
  assert snap['candidate']==0x1234,snap
 finally:p.terminate();out,err=p.communicate(timeout=3)
 assert not err,err
 assert 'DRY RUN' not in out and VOLUME_DOWN.hex(' ') not in out,out
print('A staged receiver capture reaches no serial writer action')
# Losing the writer connection must never look like successful control to HA.
import threading
with tempfile.TemporaryDirectory() as tmp:
 path=tmp+'/empty.sock'
 with socket.socket(socket.AF_UNIX) as server:
  server.bind(path);server.listen(1)
  def empty_reply():
   with server.accept()[0] as client: client.recv(128)
  t=threading.Thread(target=empty_reply);t.start()
  try:request(path)
  except OSError:pass
  else:raise AssertionError('Empty writer response was treated as success')
  finally:t.join()
print('Lost writer response fails without retry')

from bose_commands import COMMANDS, LIVE_ACTIONS, RESPONSES as RESPONSES_FOR_LIVE
assert COMMANDS['room-a-power-on']==COMMANDS['system-power-on']
assert COMMANDS['room-a-power-off']==COMMANDS['system-power-off']
assert len(COMMANDS)==55 and LIVE_ACTIONS==frozenset(RESPONSES_FOR_LIVE)
assert COMMANDS['room-b-power-off']==b'MM 4a,43,02\n'
assert COMMANDS['room-c-power-off']==b'MM 4a,43,04\n'
assert COMMANDS['room-b-volume-down']==b'MM 44,42,01\n'
assert COMMANDS['room-c-volume-down']==b'MM 44,42,02\n'
assert COMMANDS['room-c-source-aux']==b'MM 42,40,04,03\n'
assert all(cmd.endswith(b'\n') for cmd in COMMANDS.values())
assert not any(cmd.startswith((b'DE',b'MO',b'RE',b'SETUP')) for cmd in COMMANDS.values())
print('42 commands remain live-eligible; all 13 inferred Room D commands are guarded')
# BO read-only identification has its own recorded exact response, not volume ACK.
from bose_commands import RESPONSES, LIVE_MESSAGES
assert RESPONSES['identify']==b'\r\rETAP Interface V1.5 [abdefrmnt] (C) 1999 Bose Corporation.\rDevice = 0001\r\r>'
assert validate_response(RESPONSES['identify'], COMMANDS['identify'])
assert not validate_response(RESPONSES['volume-down'], COMMANDS['identify'])
assert LIVE_MESSAGES['identify']=='identified ETAP Interface V1.5; device 0001; read-only'
print('Read-only identification exact response check passed')

assert validate_response(b'\r\r\r\r>.C44100000000\r', COMMANDS['room-a-volume-up'])
assert not validate_response(RESPONSES['volume-down'], COMMANDS['room-a-volume-up'])
print('A-up requires its captured response, not A-down acknowledgement')

assert validate_response(b'\r\r\r\r>.C34101000000\r', COMMANDS['room-a-mute'])
assert validate_response(b'\r\r\r\r>.C34001000000\r', COMMANDS['room-a-unmute'])
assert not validate_response(RESPONSES['room-a-mute'], COMMANDS['room-a-unmute'])
print('A mute/unmute captured response checks passed')

assert validate_response(b'\r\r\r\r>.C24001030000\r', COMMANDS['room-a-source-aux'])
assert validate_response(b'\r\r\r\r>.C24001040000\r', COMMANDS['room-a-source-video-1'])
assert validate_response(b'\r\r\r\r>.C24001050000\r', COMMANDS['room-a-source-video-2'])
assert not validate_response(RESPONSES['room-a-source-aux'], COMMANDS['room-a-source-video-1'])
print('Aux/Video 1 exact captured response checks passed')

for room in 'abc':
 assert validate_response(RESPONSES[f'room-{room}-source-am'], COMMANDS[f'room-{room}-source-am'])
for room in 'bc':
 assert validate_response(RESPONSES[f'room-{room}-power-on'], COMMANDS[f'room-{room}-power-on'])
 assert validate_response(RESPONSES[f'room-{room}-power-off'], COMMANDS[f'room-{room}-power-off'])
print('AM and independent B/C power exact response checks passed')

assert COMMANDS['room-d-power-off']==b'MM 4a,43,08\n'
assert COMMANDS['room-d-volume-down']==b'MM 44,42,03\n'
assert COMMANDS['room-d-source-am']==b'MM 42,40,08,00\n'
assert all(name not in LIVE_ACTIONS for name in COMMANDS if name.startswith('room-d-'))
print("Room D catalogued and held out of live actions")

# Consecutive A volume steps may be fast; other command pairs retain one second.
now=[0.0];writes=[]
b=Bridge(lambda action,data:writes.append(data),lambda:now[0],allowed=lambda action:True,fast_actions=lambda:frozenset({'volume-down','room-a-volume-up'}))
assert b.submit('volume-down');b.handle_one()
now[0]=.249;assert not b.submit('room-a-volume-up')
now[0]=.25;assert b.submit('room-a-volume-up');b.handle_one()
now[0]=.5;assert not b.submit('room-a-mute')
now[0]=1.25;assert b.submit('room-a-mute');b.handle_one()
now[0]=1.5;assert not b.submit('volume-down')
now[0]=2.25;assert b.submit('volume-down');b.handle_one()
assert len(writes)==4 and b.pending is None
print('Quarter-second volume pairs by room (fast_actions path); one-second other pairs; no rejected-tap backlog')
# Two enabled profiles sharing one decoded code still reach the single writer once.
with tempfile.TemporaryDirectory() as tmp:
 from bose_ir import RemoteManager
 config=tmp+'/ir.json';path=tmp+'/accepted.sock';state=tmp+'/state.json'
 Path(config).write_text(json.dumps({'ir_device':'/dev/input/by-id/fake-receiver','ir_name':'exact receiver','ir_keys':[]}))
 write_config(Path(tmp, 'config.json'))
 clock=[0.0]
 manager=RemoteManager(state,lambda:clock[0])
 for name in ('Remote One','Remote Two'):
  sid=manager.manage({'op':'begin','name':name,'model':'ir-remote'})['session_id']
  manager.manage({'op':'capture','session_id':sid})
  for _ in range(3):
   clock[0]+=.31;manager.scan_event(-1)
  manager.manage({'op':'choose','session_id':sid,'choice':'keep'})
  manager.manage({'op':'choose','session_id':sid,'choice':'skip'})
  manager.manage({'op':'choose','session_id':sid,'choice':'skip'})
  rid=manager.manage({'op':'commit','session_id':sid})['remote_id']
  manager.manage({'op':'update','remote_id':rid,'active':True,'key':'volume-up','enabled':True})
 code=r'''
import sys,types,os,time,threading,signal
from bose_commands import RESPONSES
class Port:
 def __init__(self,**kwargs):pass
 def open(self):pass
 def close(self):pass
 @property
 def in_waiting(self):return 0
 def read(self,n):return RESPONSES['room-a-volume-up'] if n else b''
 def write(self,data):return len(data)
 def flush(self):pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
class Device:
 name='exact receiver'
 def __init__(self,path):
  self.fd,self.w=os.pipe()
  def management_before_press():
   import socket,json
   with socket.socket(socket.AF_UNIX) as client:
    client.connect(sys.argv[1])
    client.sendall((str(time.time())+'\nmanage\n'+json.dumps({'op':'resync','muted':None})).encode())
    client.shutdown(socket.SHUT_WR)
    assert json.loads(client.recv(65536))['ok']
   os.write(self.w,b'x')
  threading.Thread(target=management_before_press).start()
 def close(self):os.close(self.fd);os.close(self.w)
 def read(self):
  os.read(self.fd,1);now=time.time()
  threading.Timer(.2,lambda:os.kill(os.getpid(),signal.SIGTERM)).start()
  events=[(4,4,-1),(1,115,1),(4,4,-1),(1,115,2),(4,4,-1),(1,115,1),(1,115,0),(4,4,999),(1,115,1),(1,115,0)]
  return [types.SimpleNamespace(type=t,code=c,value=v,timestamp=lambda:now) for t,c,v in events]
sys.modules['evdev']=types.SimpleNamespace(InputDevice=Device)
from bose_bridge import serve
serve(sys.argv[1],False,sys.argv[2],sys.argv[3],serial_port='/dev/serial/by-id/usb-example-if00-port0')
'''
 check=subprocess.run([sys.executable,'-u','-c',code,path,config,state],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=4)
 assert check.returncode==0,check.stderr
 assert check.stdout.count('TX ')==1,check.stdout
 assert 'TX '+COMMANDS['room-a-volume-up'].hex(' ') in check.stdout,check.stdout
 assert RemoteManager(state).mute_state is None
print('Shared profiles, signed Linux scan codes, repeats/releases and unknown codes reach one fake serial writer at most once')

# Clearing the bridge latch is an in-memory operation. It sends no second serial write and lets the existing
# reopen loop acquire the fake port again. The deliberately wrong first reply creates the latched fault.
with tempfile.TemporaryDirectory() as tmp:
 config=tmp+'/ir.json';path=tmp+'/fault-clear.sock';state=tmp+'/state.json';counts=tmp+'/serial.log'
 write_config(Path(tmp, 'config.json'))
 code=r'''
import sys,types,os,time
real_monotonic=time.monotonic
time.monotonic=lambda:real_monotonic()*100
counter=sys.argv[3]
class Port:
 def __init__(self,**kwargs):pass
 def open(self):
  with open(counter,'a') as f:f.write('open\n');f.flush()
 def close(self):pass
 @property
 def in_waiting(self):return 0
 def read(self,n):return b'\xff'*n
 def write(self,data):
  with open(counter,'a') as f:f.write('write '+data.hex()+'\n');f.flush()
  return len(data)
 def flush(self):pass
sys.modules['serial']=types.SimpleNamespace(Serial=Port)
from bose_bridge import serve
serve(sys.argv[1],False,None,sys.argv[2],serial_port='/dev/fake-serial')
'''
 p=subprocess.Popen([sys.executable,'-u','-c',code,path,state,counts],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=Path(__file__).resolve().parents[1])
 def raw_action(action):
  with socket.socket(socket.AF_UNIX) as client:
   client.settimeout(2);client.connect(path)
   client.sendall(str(time.time()).encode()+b'\n'+action+b'\n');client.shutdown(socket.SHUT_WR)
   return client.recv(65536).decode('ascii')
 try:
  for _ in range(300):
   if p.poll() is not None:raise AssertionError(p.stderr.read())
   if os.path.exists(path):
    try:
     if json.loads(request(path,b'status\n'))['bridge']['serial_open']:break
    except (OSError,ValueError):pass
   time.sleep(.02)
  else:raise AssertionError('fake serial port did not open')
  reply=raw_action(b'volume-down')
  assert reply=='uncertain; no retry',reply
  status=json.loads(request(path,b'status\n'))['bridge']
  assert status['fault'] and not status['serial_open'],status
  before=Path(counts).read_text().splitlines()
  assert len([line for line in before if line.startswith('write ')])==1,before
  assert raw_action(b'fault-clear')=='fault cleared'
  for _ in range(300):
   lines=Path(counts).read_text().splitlines()
   if len([line for line in lines if line=='open'])>=2:break
   time.sleep(.02)
  else:raise AssertionError('bridge loop did not retry opening serial after clear')
  final=Path(counts).read_text().splitlines()
  assert len([line for line in final if line.startswith('write ')])==1,final
 finally:p.terminate();out,err=p.communicate(timeout=3)
 assert p.returncode==0,(out,err)
print('Fault clear produced no serial write and the bridge loop reopened the fake port')
