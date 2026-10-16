"""HA SSH wrapper: no shell, bounded replies, no retry, finite operations."""
import _bootstrap
import asyncio
from unittest.mock import patch
from custom_components.bose_lifestyle.client import Client,BridgeError
from custom_components.bose_lifestyle.transport import FakeTransport,SshTransport
from bose_commands import COMMANDS
from pathlib import Path

class Stdin:
    def __init__(self):self.body=b''
    def write(self,body):self.body+=body
    async def drain(self):pass
    def close(self):pass
class Process:
    def __init__(self,reply=b'',returncode=0):
        self.stdin=Stdin();self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader()
        self.stdout.feed_data(reply);self.stdout.feed_eof();self.stderr.feed_eof();self.returncode=returncode
    async def wait(self):return self.returncode
    def kill(self):self.returncode=-9

async def run():
    calls=[]; next_process=[Process(b'{"ok":true,"result":{}}')]
    async def spawn(*args,**kwargs):
        calls.append((args,kwargs));return next_process[0]
    client=schema4_client({'host':'bridge.example.invalid','user':'bose','port':22})
    with patch('asyncio.create_subprocess_exec',spawn):
        await client.manage('resync',muted=None)
        args,kwargs=calls[0]
        assert args[-1]=='manage' and 'UserKnownHostsFile=/config/.ssh/bose_known_hosts' in args and 'StrictHostKeyChecking=yes' in args
        assert 'shell' not in kwargs and next_process[0].stdin.body==b'{"op": "resync", "muted": null}'
        for op,data in [('shell',{'command':'id'}),('resync',{'muted':1}),('begin',{'name':' ','model':'ir-remote'})]:
            before=len(calls)
            try:await client.manage(op,**data)
            except BridgeError:pass
            else:raise AssertionError('invalid input accepted')
            assert len(calls)==before
        for process in (Process(b'x',1),Process(b'x'*65537),Process(b'{"ok":true,"result":{"command":"id"}}'),Process(b'uncertain; no retry')):
            next_process[0]=process;before=len(calls)
            try:await client.manage('resync',muted=None)
            except BridgeError:pass
            else:raise AssertionError('failure accepted')
            assert len(calls)==before+1
        next_process[0]=Process(b'rate-limited')
        before=len(calls)
        try: await client.call('volume-down')
        except BridgeError as error: assert str(error)=='not_sent',str(error)
        else: raise AssertionError('rate-limited returned as success')
        assert len(calls)==before+1
        async def lost(*args,**kwargs):calls.append((args,kwargs));raise TimeoutError()
        with patch('asyncio.create_subprocess_exec',lost):
            before=len(calls)
            try:await client.call('room-a-mute')
            except BridgeError:pass
            else:raise AssertionError('timeout accepted')
            assert len(calls)==before+1
    print('SSH argv isolation, pinned key, bounded JSON, malformed responses, failures and no retries passed')

import json,time
import bose_request
from bose_commands import LIVE_MESSAGES,REFUSED_MESSAGE
from custom_components.bose_lifestyle import client as ha_client
from custom_components.bose_lifestyle.commands import COMMANDS as HA_COMMANDS

def schema4_client(data, **kwargs):
    # The names a schema-4 status would list; the press gate needs them, the bridge host still decides each press.
    client=Client(data, **kwargs)
    client.dynamic=frozenset(HA_COMMANDS)
    return client

V4_TEXT=(Path(__file__).resolve().parents[1]/'tests/fixtures/status_v4.json').read_text()

async def run_schema4_only():
    # Only status schema 4 validates, in both validators; schema 2 and 3 are rejected, and the removed op never reaches ssh.
    v4=json.loads(V4_TEXT)
    for schema in (2,3):
        text=json.dumps({**v4,'schema':schema})
        for name,check in (('bridge',bose_request.validate_status),('ha',ha_client.validate_status)):
            try: check(text)
            except OSError: pass
            else: raise AssertionError((name,schema))
    for check in (bose_request.validate_status,ha_client.validate_status): check(V4_TEXT)
    import bose_events
    assert ha_client.EVENT_KINDS==bose_events.KINDS

    calls=[]; next_process=[Process(b'')]
    async def spawn(*args,**kwargs):
        calls.append((args,kwargs));return next_process[0]
    client=schema4_client({'host':'bridge.example.invalid','user':'bose','port':22})
    with patch('asyncio.create_subprocess_exec',spawn):
        next_process[0]=Process(V4_TEXT.encode())
        await client.call('status')
        before=len(calls)
        try: await client.manage('set_room_live',room='d',live=True)
        except BridgeError as error: assert str(error)=='invalid_request',error
        else: raise AssertionError('removed set_room_live op accepted')
        assert len(calls)==before
        next_process[0]=Process(LIVE_MESSAGES['room-d-mute'].encode())
        assert await client.call('room-d-mute')==LIVE_MESSAGES['room-d-mute']
        next_process[0]=Process(REFUSED_MESSAGE.encode())          # the bridge host exits 0 on an M1 refusal
        before=len(calls)
        try: await client.call('room-d-mute')
        except BridgeError as error: assert str(error)=='refused_by_unit'
        else: raise AssertionError('refusal returned as success')
        assert len(calls)==before+1
        event={'t':1.5,'kind':'tx','action':'volume-down','detail':'4d'}
        next_process[0]=Process(json.dumps({'events':[event]}).encode())
        assert await client.events(10)==[event]
        assert calls[-1][0][-1]=='events' and next_process[0].stdin.body==b''
        for bad in (b'{"events":{}}',b'{"events":[],"x":1}',b'x',b'{"events":[{"t":1,"kind":"zap","action":null,"detail":""}]}'):
            next_process[0]=Process(bad); before=len(calls)
            try: await client.events(10)
            except BridgeError: pass
            else: raise AssertionError('invalid events accepted')
            assert len(calls)==before+1
    print('Only status schema 4 validates in both validators; the removed set_room_live op never reaches ssh; refusal and events are bounded and unretried')

async def reply_to(action,stdout,returncode=0,client=None):
    client=client or schema4_client({'host':'bridge.example.invalid','user':'bose','port':22})
    spawned=[]
    async def spawn(*args,**kwargs):
        spawned.append(args);return Process(stdout,returncode)
    with patch('asyncio.create_subprocess_exec',spawn):
        return await client.call(action),spawned

async def expect_code(action,stdout,returncode,code):
    client=schema4_client({'host':'bridge.example.invalid','user':'bose','port':22})
    spawned=[]
    async def spawn(*args,**kwargs):
        spawned.append(args);return Process(stdout,returncode)
    with patch('asyncio.create_subprocess_exec',spawn):
        try: await client.call(action)
        except BridgeError as error: assert str(error)==code,(stdout,returncode,str(error))
        else: raise AssertionError(('accepted',stdout,returncode))
    assert len(spawned)==1

async def test_rejected_not_allowed_maps_to_not_allowed():
    for returncode in (0,1):   # the stdout token wins over the exit code
        await expect_code('room-a-mute',b'rejected: not-allowed',returncode,'not_allowed')
    await expect_code('room-d-mute',b'rejected: not-allowed',0,'not_allowed')

async def test_uncertain_maps_to_uncertain_not_cannot_connect():
    for returncode in (0,1):
        await expect_code('room-a-mute',b'uncertain; no retry',returncode,'uncertain')
    await expect_code('room-a-mute',b'garbage',1,'uncertain')           # output after the connection: the bridge may have answered
    await expect_code('room-a-mute',b'',1,'uncertain')                  # exit 1 is a remote exit: the connection was made
    await expect_code('room-a-mute',b'',255,'uncertain')          # SSH can fail after delivery but before the reply arrives

async def test_rejected_maps_to_not_sent_and_rate_limited_maps_to_not_sent():
    for returncode in (0,1):
        await expect_code('room-a-mute',b'rejected',returncode,'not_sent')
        await expect_code('room-a-mute',b'rate-limited',returncode,'not_sent')

async def test_timeout_on_press_is_uncertain():
    async def lost(*args,**kwargs): raise TimeoutError()
    client=schema4_client(DATA)
    with patch('asyncio.create_subprocess_exec',lost):
        try: await client.call('room-a-mute')
        except BridgeError as error: assert str(error)=='uncertain',str(error)
        else: raise AssertionError('timeout on a press accepted')

async def test_unrecognised_reply_exit_0_is_uncertain():
    await expect_code('room-a-mute',b'garbage',0,'uncertain')

async def test_ssh_exit_255_empty_stdout_is_uncertain():
    await expect_code('room-a-mute',b'',255,'uncertain')
    async def refused(*args,**kwargs): raise OSError('no ssh')
    with patch('asyncio.create_subprocess_exec',refused):
        try: await schema4_client(DATA).call('room-a-mute')
        except BridgeError as error: assert str(error)=='connection_lost',str(error)
        else: raise AssertionError('spawn failure accepted')

async def test_status_validation_failure_maps_to_invalid_response():
    await expect_code('status',b'{"schema":3}',0,'invalid_response')    # exit 0, passes SSH, fails validation
    await expect_code('status',b'',1,'connection_lost')                 # no readable reply at all

FORBIDDEN=('restart','reboot','unplug','power cycle','try again')
FAILURE_CODES=('not_sent','not_allowed','uncertain','connection_lost','fault_paused','config_uncertain','refused_by_unit')

async def test_refused_by_m1_reply_maps_to_plain_error():
    for returncode in (0,1):   # the bridge's refusal reply is never shown raw
        await expect_code('room-d-mute',REFUSED_MESSAGE.encode(),returncode,'refused_by_unit')

def test_refused_message_text_exact():
    exc=json.loads((ROOT/'custom_components/bose_lifestyle/strings.json').read_text())['exceptions']
    assert exc['refused_by_unit']['message']=="The Bose control unit declined that command. Nothing was changed."
    assert REFUSED_MESSAGE=='refused by M1; no retry'   # the bridge reply string is unchanged
    assert "REFUSED_MESSAGE = 'refused by M1; no retry'" in (ROOT/'custom_components/bose_lifestyle/commands.py').read_text()
    for path in (ROOT/'custom_components/bose_lifestyle/strings.json',ROOT/'custom_components/bose_lifestyle/translations/en.json',ROOT/'custom_components/bose_lifestyle/frontend/bose-control-card.js'):
        assert REFUSED_MESSAGE not in path.read_text(),path.name   # the raw reply appears in no user-facing file
    card=(ROOT/'custom_components/bose_lifestyle/frontend/bose-control-card.js').read_text()
    assert exc['refused_by_unit']['message'] in card

def test_each_failure_class_has_distinct_message():
    root=ROOT/'custom_components/bose_lifestyle'
    exc=json.loads((root/'strings.json').read_text())['exceptions']
    messages=[exc[code]['message'] for code in FAILURE_CODES]
    assert len(set(messages))==len(FAILURE_CODES),messages
    card=(root/'frontend/bose-control-card.js').read_text()
    for message in messages: assert message in card,message

def test_fault_message_exact_text():
    exc=json.loads((ROOT/'custom_components/bose_lifestyle/strings.json').read_text())['exceptions']
    assert exc['fault_paused']['message']=='FAULTED — controls paused; use the P1 remote'
    assert exc['uncertain']['message']=="We couldn't confirm that command. Check the room before pressing again."
    assert exc['not_sent']['message']=="That wasn't sent. Nothing was changed."
    assert exc['connection_lost']['message']=="Can't reach the bridge computer right now."
    assert exc['config_uncertain']['message']=="The bridge's settings couldn't be confirmed. Ask the person who installed this system for help."

def test_no_forbidden_words_in_any_string():
    root=ROOT/'custom_components/bose_lifestyle'
    for path in (root/'strings.json',root/'translations/en.json',root/'frontend/bose-control-card.js'):
        text=path.read_text().lower()
        for word in FORBIDDEN: assert word not in text,(path.name,word)
    # uncertain must never say try again or restart, in either file
    exc=json.loads((root/'strings.json').read_text())['exceptions']
    assert not any(word in exc['uncertain']['message'].lower() for word in FORBIDDEN)

async def test_transmitted_replies_are_accepted():
    # A transmitted reply is accepted for any catalog name, even with a nonzero exit; HA no longer compares it with LIVE_MESSAGES.
    reply,_=await reply_to('room-a-mute',b'transmitted room-a-mute; wording the bridge host chose')
    assert reply=='transmitted room-a-mute; wording the bridge host chose'
    reply,_=await reply_to('room-a-mute',b'transmitted room-a-mute; x',1)
    assert reply=='transmitted room-a-mute; x'
    dry=b'DRY RUN; no port opened: '+COMMANDS['room-a-source-fm'].hex(' ').encode()
    reply,_=await reply_to('room-a-mute',dry)
    assert reply==dry.decode()

async def test_new_names_pass_invalid_action_gate():
    client=Client({'host':'bridge.example.invalid','user':'bose','port':22})
    v4=Path('tests/fixtures/status_v4.json').read_text().encode()
    # Before a schema-4 status names the action, it is not a valid name.
    try: await reply_to('room-a-source-fm',b'',0,client)
    except BridgeError as error: assert str(error)=='invalid_action'
    else: raise AssertionError('unknown dynamic name accepted before status')
    await reply_to('status',v4,0,client)
    dry=b'DRY RUN; no port opened: '+COMMANDS['room-a-source-fm'].hex(' ').encode()
    reply,spawned=await reply_to('room-a-source-fm',dry,0,client)
    assert reply==dry.decode() and spawned[-1][-1]=='room-a-source-fm'
    for bad in ('Room-A-Source-FM','room a source fm','room-a-source-xyz',"room-a-mute;id"):
        try: await reply_to(bad,b'',0,client)
        except BridgeError as error: assert str(error)=='invalid_action',bad
        else: raise AssertionError(('name reached the gate',bad))

ROOT=Path(__file__).resolve().parents[1]
V3=json.dumps({**json.loads(V4_TEXT),'schema':3}).encode()   # a schema-3 bridge: rejected
V4=(ROOT/'tests/fixtures/status_v4.json').read_bytes()
DATA={'host':'bridge.example.invalid','user':'bose','port':22}
TARGET=DATA['user']+'@'+DATA['host']
PRE_CHANGE_ARGV=['ssh','-T','-i','/config/.ssh/bose_ed25519','-o','IdentitiesOnly=yes','-o','BatchMode=yes',
                 '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/config/.ssh/bose_known_hosts',
                 '-o','ConnectTimeout=3','-o','ConnectionAttempts=1','-o','ControlMaster=auto','-o','ControlPersist=60',
                 '-o','ControlPath=/config/.ssh/bose-%C','-p','22',TARGET,'volume-down']

async def test_transport_interface_fake():
    client3=Client(DATA,transport=FakeTransport({'status':(0,V3)}))
    try: await client3.call('status')
    except BridgeError as error: assert str(error)=='invalid_response'
    else: raise AssertionError('schema 3 status accepted')
    client4=Client(DATA,transport=FakeTransport({'status':(0,V4)}))
    assert (await client4.call('status'))['schema']==4
    fake=FakeTransport({'room-a-mute':(0,b'transmitted room-a-mute; x'),'volume-down':(0,b'rejected: not-allowed'),
                        'room-a-unmute':(1,b'uncertain; no retry'),'room-a-volume-up':(1,b'rejected')})
    client=Client(DATA,transport=fake)
    client.dynamic=frozenset({'room-a-mute','volume-down','room-a-unmute','room-a-volume-up'})   # as a schema-4 status would name them
    assert await client.call('room-a-mute')=='transmitted room-a-mute; x'
    for action,code in (('volume-down','not_allowed'),('room-a-unmute','uncertain'),('room-a-volume-up','not_sent')):
        try: await client.call(action)
        except BridgeError as error: assert str(error)==code,(action,str(error))
        else: raise AssertionError(('accepted',action))
    assert [action for action,_ in fake.calls]==['room-a-mute','volume-down','room-a-unmute','room-a-volume-up']
    before=len(fake.calls)
    try: await client.call('shell')
    except BridgeError as error: assert str(error)=='invalid_action'
    assert len(fake.calls)==before

async def test_fault_clear_is_a_single_bridge_control():
    fake=FakeTransport({'fault-clear':(0,b'fault cleared')})
    client=Client(DATA,transport=fake)
    assert await client.call('fault-clear')=='fault cleared'
    assert [action for action,_ in fake.calls]==['fault-clear']
    for response in (b'rejected',b'uncertain; no retry',b'unknown'):
        fake=FakeTransport({'fault-clear':(1,response)})
        client=Client(DATA,transport=fake)
        try: await client.call('fault-clear')
        except BridgeError as error: assert str(error)=='connection_lost'
        else: raise AssertionError(('unexpected clear result',response))
        assert [action for action,_ in fake.calls]==['fault-clear']

async def test_ssh_transport_argv_unchanged():
    spawned=[]
    async def spawn(*args,**kwargs):
        spawned.append((args,kwargs));return Process(b'rate-limited')
    with patch('asyncio.create_subprocess_exec',spawn):
        known=Client(DATA); known.dynamic=frozenset({'volume-down'})   # named by a schema-4 status
        try: await known.call('volume-down')
        except BridgeError as error: assert str(error)=='not_sent'
        else: raise AssertionError('rate-limited returned as success')
        assert len(spawned)==1
        args,kwargs=spawned[0]
        assert list(args)==PRE_CHANGE_ARGV and 'shell' not in kwargs
        assert args[-1]=='volume-down'
        # Paths come from config_dir; the default /config reproduces the pre-change list exactly.
        spawned.clear()
        elsewhere=Client(DATA,config_dir=Path('/elsewhere')); elsewhere.dynamic=frozenset({'volume-down'})
        try: await elsewhere.call('volume-down')
        except BridgeError: pass   # the reply is not under test here
        assert '/elsewhere/.ssh/bose_ed25519' in spawned[0][0]
        assert 'UserKnownHostsFile=/elsewhere/.ssh/bose_known_hosts' in spawned[0][0]
        assert 'ControlPath=/elsewhere/.ssh/bose-%C' in spawned[0][0]
    assert SshTransport(Path('/config'),'bridge.example.invalid','bose',22).argv('volume-down')==PRE_CHANGE_ARGV

if __name__=='__main__':
    asyncio.run(test_fault_clear_is_a_single_bridge_control())
    asyncio.run(test_transport_interface_fake())
    asyncio.run(test_ssh_transport_argv_unchanged())
    asyncio.run(run())
    asyncio.run(run_schema4_only())
    asyncio.run(test_rejected_not_allowed_maps_to_not_allowed())
    asyncio.run(test_uncertain_maps_to_uncertain_not_cannot_connect())
    asyncio.run(test_transmitted_replies_are_accepted())
    asyncio.run(test_rejected_maps_to_not_sent_and_rate_limited_maps_to_not_sent())
    asyncio.run(test_timeout_on_press_is_uncertain())
    asyncio.run(test_unrecognised_reply_exit_0_is_uncertain())
    asyncio.run(test_ssh_exit_255_empty_stdout_is_uncertain())
    asyncio.run(test_refused_by_m1_reply_maps_to_plain_error())
    test_refused_message_text_exact()
    asyncio.run(test_status_validation_failure_maps_to_invalid_response())
    test_each_failure_class_has_distinct_message()
    test_fault_message_exact_text()
    test_no_forbidden_words_in_any_string()
    asyncio.run(test_new_names_pass_invalid_action_gate())
    print('Outcome tokens, dynamic action gate and the bridge/HA validator split passed')
