"""HA client: validators, action gate and outcome mapping. Process work is in transport.py."""
import json
import math
import re
from pathlib import Path
from .commands import COMMANDS, REFUSED_MESSAGE
from .protocol import strict_json, validate_management, validate_profiles, identifier, MAX_REQUEST, MAX_REPLY
from .protocol import validate_status as validate_shared_status
from .transport import BridgeError, HOST_PATTERN, USER_PATTERN, SshTransport

EVENT_KINDS = frozenset({'start','tx','rx','outcome','fault','refused','ir','serial','manage'})   # same set as the bridge host's bose_events.KINDS
ACTION_NAME = re.compile(r'[a-z0-9-]{1,40}')
NOT_ALLOWED = 'rejected: not-allowed'
UNCERTAIN = 'uncertain; no retry'
ACCEPTED_PREFIXES = ('transmitted ', 'DRY RUN; ', 'identified ')   # any catalog name; the bridge host already verified the exact reply

class Client:
    def __init__(self, data, transport=None, config_dir=Path('/config')):
        self.data = data
        self.dynamic = frozenset()   # schema-4 action names from the last status; the bridge host's allowed flag decides each one
        if not HOST_PATTERN.fullmatch(data['host']) or not USER_PATTERN.fullmatch(data['user']) or type(data['port']) is not int or not 1 <= data['port'] <= 65535:
            raise BridgeError('invalid_connection')
        self.transport = SshTransport(config_dir,data['host'],data['user'],data['port']) if transport is None else transport

    def _gate(self, action):
        if action in self.dynamic and ACTION_NAME.fullmatch(action): return
        if action not in ('status','manage','events','fault-clear'): raise BridgeError('invalid_action')

    async def call(self, action, payload=None):
        self._gate(action)
        body = b''
        if action == 'manage':
            try: validate_management(payload)
            except (ValueError,TypeError) as error: raise BridgeError('invalid_request') from error
            body = json.dumps(payload,ensure_ascii=True).encode()
            if len(body)>MAX_REQUEST: raise BridgeError('invalid_request')
        press = action not in ('status','manage','events','fault-clear')
        try:
            transported = await self.transport.call(action, body)
        except OSError as error:   # the ssh process never started: nothing can have reached the bridge
            raise BridgeError('connection_lost') from error
        if transported.unclear: raise BridgeError('uncertain' if press else 'connection_lost')   # a press may already have been sent
        try:
            reply = transported.stdout.decode('ascii','replace').strip()
            if action == 'fault-clear':
                if transported.returncode or reply != 'fault cleared': raise BridgeError('connection_lost')
                return reply
            if press:   # stdout tokens are read before the exit code
                if reply == NOT_ALLOWED: raise BridgeError('not_allowed')
                if reply == UNCERTAIN: raise BridgeError('uncertain')
                if reply in ('rate-limited','rejected'): raise BridgeError('not_sent')   # the bridge dropped it: definitely nothing transmitted
                if reply.startswith(ACCEPTED_PREFIXES): return reply
                if reply == REFUSED_MESSAGE: raise BridgeError('refused_by_unit')
                raise BridgeError('uncertain')
            if transported.returncode: raise BridgeError('connection_lost')
            if action == 'status':
                status = validate_status(reply)
                self.dynamic = frozenset(status['actions'])
                return status
            if action == 'events': return validate_events(reply)
            if action == 'manage':
                response = strict_json(reply)
                if not isinstance(response,dict) or type(response.get('ok')) is not bool or set(response)!=({'ok','result'} if response['ok'] else {'ok','error'}): raise BridgeError('invalid_response')
                if not response['ok']: raise BridgeError(response['error'])
                result=response['result']
                if not isinstance(result,dict): raise BridgeError('invalid_response')
                op=payload['op']
                if op in ('begin','capture','session','choose'):
                    fields={'session_id','remote_id','name','model','phase','candidate','shared','verified','key','step','steps','mappings'}
                    if set(result)!=fields or not identifier(result['session_id']) or not identifier(result['remote_id']): raise BridgeError('invalid_response')
                    validate_profiles({result['remote_id']:{'name':result['name'],'model':result['model'],'active':False,'mappings':result['mappings']}})
                    if result['phase'] not in ('ready','waiting','captured','conflict','inconsistent','timeout','review') or type(result['shared']) is not bool or type(result['verified']) is not int or not 0<=result['verified']<=3: raise BridgeError('invalid_response')
                    if result['candidate'] is not None and (type(result['candidate']) is not int or not 0<=result['candidate']<=0xffffffff): raise BridgeError('invalid_response')
                    if result['key'] not in (None,'volume-up','volume-down','mute') or type(result['step']) is not int or type(result['steps']) is not int or not 1<=result['steps']<=3 or not 1<=result['step']<=result['steps']+1: raise BridgeError('invalid_response')
                elif op=='commit':
                    if set(result)!={'remote_id'} or not identifier(result['remote_id']): raise BridgeError('invalid_response')
                elif result: raise BridgeError('invalid_response')
                return result
        except (OSError,ValueError,TypeError) as error:   # a reply that passed ssh but did not parse or validate
            raise BridgeError('invalid_response') from error

    async def manage(self, op, **fields):
        return await self.call('manage',{'op':op,**fields})

    async def events(self, limit=50):
        return (await self.call('events'))[-limit:]

def validate_events(reply):
    """Bounded read-only event list from the bridge host; exact keys, known kinds, finite times."""
    try:
        data=strict_json(reply)
        events=data['events']
        if not isinstance(data,dict) or set(data)!={'events'} or not isinstance(events,list) or len(events)>50: raise ValueError
        for e in events:
            if (not isinstance(e,dict) or set(e)!={'t','kind','action','detail'} or e['kind'] not in EVENT_KINDS
                    or type(e['t']) not in (int,float) or not math.isfinite(e['t'])
                    or not (e['action'] is None or isinstance(e['action'],str)) or not isinstance(e['detail'],str) or len(e['detail'])>200): raise ValueError
        return events
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        raise BridgeError('invalid_response') from error

def validate_status(reply):
    """Shared status validator (protocol.py) with HA's last-action rules. Schema 4 only; unknown health values degrade to 'unknown'."""
    try:
        data=validate_shared_status(reply,degrade_unknown=True,action_ok=lambda name: ACTION_NAME.fullmatch(name) is not None)
        catalog=data['actions']
        for key in ('last_outcome','last_transmission'):
            record=data['bridge'][key]
            if record is None or record['action'] is None: continue
            name=record['action']
            known=name=='identify' or name in catalog or (not catalog and name in COMMANDS)   # empty actions: uncertain or invalid commit
            if not known: raise ValueError('Unexpected last action')
        return data
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError) as error:
        raise OSError('Invalid cached status; no retry') from error
