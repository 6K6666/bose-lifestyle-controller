"""Persistent Room A remote profiles and receive-only, transactional learning."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

KEYS = ('volume-up', 'volume-down', 'mute')
MODELS = ('ir-remote',)   # one neutral model; the free-text name is the only label
ZONES = ('a', 'b', 'c', 'd')   # local copy: this module imports no sibling (bose_config owns the config model)
SOURCES = ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')
# Owned by the bridge dispatch (bose_bridge.serve): they change the config file or are retired, never IR profiles.
BRIDGE_OPS = frozenset({'set_zone', 'set_source', 'set_unverified', 'set_ir_zone', 'set_volume_steps'})
MAX_VOLUME_STEPS = 5   # same number as bose_config.MAX_VOLUME_STEPS (a test pins both)
MAX_REQUEST = 8192
TOP4 = frozenset({'schema', 'bridge_id', 'generated_at', 'bridge', 'config', 'actions', 'ir', 'rooms'})
BRIDGE4 = frozenset({'mode', 'serial_open', 'fault', 'recovery', 'config', 'ir_configured', 'ir_attached', 'last_outcome', 'last_transmission'})
MAX_REPLY = 65536


class IRException(ValueError):
    pass


class IRStateError(IRException):
    """The saved state file exists but cannot be trusted. Loading never modifies it; `reason` names the damage
    (unreadable, not-json, bad-shape, bad-identity, bad-profiles) and never carries file content."""
    def __init__(self, reason):
        super().__init__('invalid_request')
        self.reason = reason


def strict_json(text):
    def pairs(items):
        result = dict(items)
        if len(result) != len(items): raise IRException('invalid_request')
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(IRException('invalid_request')))
    except RecursionError as error:
        raise IRException('invalid_request') from error


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


def empty_mappings():
    return {key: {'code': None, 'enabled': False} for key in KEYS}


def validate_profiles(profiles):
    if not isinstance(profiles, dict) or len(profiles) > 32: raise IRException('invalid_request')
    assignments = {}
    for rid, profile in profiles.items():
        if not identifier(rid) or not isinstance(profile, dict) or set(profile) != {'name','model','active','mappings'}:
            raise IRException('invalid_request')
        if (not isinstance(profile['name'], str) or not 1 <= len(profile['name'].strip()) <= 64
                or any(ord(c) < 32 for c in profile['name']) or profile['model'] not in MODELS
                or type(profile['active']) is not bool or not isinstance(profile['mappings'],dict) or set(profile['mappings']) != set(KEYS)):
            raise IRException('invalid_request')
        for key, mapping in profile['mappings'].items():
            if not isinstance(mapping, dict) or set(mapping) != {'code','enabled'}: raise IRException('invalid_request')
            code = mapping['code']
            if code is not None and (type(code) is not int or not 0 <= code <= 0xffffffff): raise IRException('invalid_request')
            if type(mapping['enabled']) is not bool or (code is None and mapping['enabled']): raise IRException('invalid_request')
            if code is not None:
                if code in assignments and assignments[code] != key: raise IRException('conflict')
                assignments[code] = key


def validate_management(data):
    if not isinstance(data, dict) or not isinstance(data.get('op'), str): raise IRException('invalid_request')
    op = data['op']
    schemas = {
        'begin': ({'op','name','model'}, {'remote_id','keys'}),
        'capture': ({'op','session_id'}, set()),
        'session': ({'op','session_id'}, set()),
        'choose': ({'op','session_id','choice'}, set()),
        'commit': ({'op','session_id'}, set()),
        'cancel': ({'op','session_id'}, set()),
        'update': ({'op','remote_id'}, {'name','active','key','enabled','remove'}),
        'delete': ({'op','remote_id'}, set()),
        'resync': ({'op','muted'}, set()),
        'set_zone': ({'op','zone','enabled'}, set()),
        'set_source': ({'op','source','connected'}, set()),
        'set_unverified': ({'op','zone','allow'}, set()),
        'set_ir_zone': ({'op','zone'}, set()),
        'set_volume_steps': ({'op','steps'}, set()),
    }
    if op not in schemas: raise IRException('invalid_request')
    required, optional = schemas[op]
    if not required <= set(data) or not set(data) <= required | optional: raise IRException('invalid_request')
    for field in ('remote_id','session_id'):
        if field in data and not identifier(data[field]): raise IRException('invalid_request')
    if 'name' in data and (not isinstance(data['name'], str) or not 1 <= len(data['name'].strip()) <= 64
                           or any(ord(c) < 32 for c in data['name'])): raise IRException('invalid_request')
    if 'model' in data and data['model'] not in MODELS: raise IRException('invalid_request')
    if 'keys' in data and (not isinstance(data['keys'],list) or not data['keys']
                          or data['keys'] != [k for k in KEYS if k in data['keys']]): raise IRException('invalid_request')
    if 'choice' in data and data['choice'] not in ('keep','retry','skip'): raise IRException('invalid_request')
    if 'key' in data and data['key'] not in KEYS: raise IRException('invalid_request')
    if 'zone' in data and (type(data['zone']) is not str or data['zone'] not in ZONES): raise IRException('invalid_request')
    if 'source' in data and (type(data['source']) is not str or data['source'] not in SOURCES): raise IRException('invalid_request')
    for field in ('active','enabled','remove','connected','allow'):
        if field in data and type(data[field]) is not bool: raise IRException('invalid_request')
    if 'muted' in data and data['muted'] is not None and type(data['muted']) is not bool: raise IRException('invalid_request')
    if 'steps' in data and (type(data['steps']) is not int or not 1 <= data['steps'] <= MAX_VOLUME_STEPS): raise IRException('invalid_request')
    if op == 'update':
        if len(data) == 2 or ('key' in data) != ('enabled' in data or 'remove' in data): raise IRException('invalid_request')
        if 'enabled' in data and 'remove' in data: raise IRException('invalid_request')
    return data


STATUS_FAULTS = (None, 'unexpected-pre-input', 'partial-write', 'unexpected-response', 'serial-io-during-transmission', 'write-drain-timeout')
STATUS_RECOVERY = (None, 'waiting-for-exact-adapter', 'transport-lost-before-write', 'operator-investigation-required')
STATUS_CONFIG = ('ok', 'config-invalid', 'config-uncertain')
STATUS_FUNCTIONS = ('power-on', 'power-off', 'volume-up', 'volume-down', 'mute', 'unmute', 'source')


def validate_status(reply, degrade_unknown=False, action_ok=None):
    """Shared bridge status check for the bridge host forced command and the HA copy. Accepts schemas 4 and 5.

    action_ok: predicate for last_outcome/last_transmission action names; None accepts any string.
    degrade_unknown: unknown fault, recovery and config values become 'unknown' instead of raising.
    Raises ValueError on anything else, including the TypeError, KeyError, AttributeError and OverflowError a malformed body can cause.
    """
    try:
        return _check_status(reply, degrade_unknown, action_ok)
    except (TypeError, KeyError, AttributeError, OverflowError) as error:
        raise ValueError('Unexpected status schema') from error


def _check_status(reply, degrade_unknown, action_ok):
    def require(condition):
        if not condition: raise ValueError('Unexpected status schema')
    def epoch(value):
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    def known(value, values):
        if value in values: return value
        require(degrade_unknown)
        return 'unknown'
    def action_name(value, nullable):
        if value is None: return nullable
        return isinstance(value, str) and (action_ok is None or action_ok(value))
    def exact_bools(mapping, keys):
        require(isinstance(mapping, dict) and set(mapping) == set(keys) and all(type(mapping[k]) is bool for k in keys))
    data = strict_json(reply)
    require(isinstance(data, dict) and type(data.get('schema')) is int and data['schema'] in (4, 5))
    schema = data['schema']
    require(set(data) == TOP4 and identifier(data['bridge_id']) and epoch(data['generated_at']))
    health = data['bridge']
    require(isinstance(health, dict) and set(health) == BRIDGE4)
    require(health['mode'] in ('dry-run', 'live'))
    require(all(type(health[k]) is bool for k in ('serial_open', 'ir_configured', 'ir_attached')))
    health['fault'] = known(health['fault'], STATUS_FAULTS)
    health['recovery'] = known(health['recovery'], STATUS_RECOVERY)
    config = data['config']
    state = known(health['config'], STATUS_CONFIG)
    health['config'] = state
    require(isinstance(data['actions'], dict))
    if config is None:
        require(data['actions'] == {} and state != 'ok')
    else:
        base = {'zones', 'sources', 'unverified', 'ir'}
        require(isinstance(config, dict) and state in ('ok', 'unknown') and set(config) in (base, base | {'volume_steps'}))
        require('volume_steps' not in config or (type(config['volume_steps']) is int and 1 <= config['volume_steps'] <= MAX_VOLUME_STEPS))
        require(isinstance(config['zones'], dict) and set(config['zones']) == set(ZONES))
        for zone in ZONES: exact_bools(config['zones'][zone], ('enabled',))
        exact_bools(config['sources'], SOURCES)
        exact_bools(config['unverified'], ZONES)
        require(isinstance(config['ir'], dict) and set(config['ir']) == {'zone'} and config['ir']['zone'] in ZONES)
    for name, entry in data['actions'].items():
        require(isinstance(name, str) and re.fullmatch(r'[a-z0-9-]+', name) is not None)
        require(isinstance(entry, dict) and set(entry) == {'zone', 'function', 'source', 'verified', 'alias', 'allowed'})
        require(entry['zone'] in ZONES and entry['function'] in STATUS_FUNCTIONS)
        require(entry['source'] is None or entry['source'] in SOURCES)
        require((entry['function'] == 'source') == (entry['source'] is not None))
        require(all(type(entry[k]) is bool for k in ('verified', 'alias', 'allowed')))
    ir = data['ir']
    require(isinstance(ir, dict) and set(ir) == {'profiles', 'learning', 'mute_state'})
    validate_profiles(ir['profiles'])
    require(ir['mute_state'] is None or type(ir['mute_state']) is bool)
    if ir['learning'] is not None:
        learning = ir['learning']
        require(isinstance(learning, dict) and set(learning) == {'remote_id', 'phase', 'key', 'step', 'steps'})
        require(identifier(learning['remote_id']) and learning['phase'] in ('ready', 'waiting', 'captured', 'conflict', 'inconsistent', 'timeout', 'review'))
        require(learning['key'] in (None, 'volume-up', 'volume-down', 'mute'))
        require(type(learning['step']) is int and type(learning['steps']) is int and 1 <= learning['steps'] <= 3 and 1 <= learning['step'] <= learning['steps'] + 1)
    for key in ('last_outcome', 'last_transmission'):
        record = health[key]
        if record is None: continue
        require(isinstance(record, dict) and set(record) == {'action', 'outcome', 'observed_at'} and epoch(record['observed_at']))
        require(action_name(record['action'], nullable=(key == 'last_outcome')))
        require(record['outcome'] in ('dry-run', 'rejected', 'uncertain', 'transmitted-with-matching-response', 'refused-by-m1'))
        if key == 'last_transmission':
            require(record['outcome'] in ('uncertain', 'transmitted-with-matching-response', 'refused-by-m1'))
    require(isinstance(data['rooms'], dict) and set(data['rooms']) == {'a', 'b', 'c', 'd'})
    for room_id, room in data['rooms'].items():
        require(isinstance(room, dict) and set(room) == {'power', 'source', 'volume', 'mute'})
        for field_name, field in room.items():
            require(isinstance(field, dict) and set(field) == {'value', 'observed_at', 'age', 'supported'}
                    and type(field['supported']) is bool)
            if schema == 4 or room_id == 'd' or field_name in ('power', 'mute') or not field['supported']:
                require(field['supported'] is False
                        and all(field[k] is None for k in ('value', 'observed_at', 'age')))
                continue
            if field_name == 'volume':
                require(type(field['value']) is int and 0 <= field['value'] <= 255)
            else:
                require(field_name == 'source' and isinstance(field['value'], str) and field['value'] in SOURCES)
            require(epoch(field['observed_at']) and field['observed_at'] <= data['generated_at'])
            require(type(field['age']) in (int, float) and math.isfinite(field['age']) and field['age'] >= 0)
    return data


class RemoteManager:
    def __init__(self, path, clock=time.monotonic):
        self.path, self.clock = Path(path), clock
        self.bridge_id, self.profiles, self.mute_state = uuid.uuid4().hex, {}, None
        self.target_zone = None   # in memory only: the file format never records it
        self.session = None
        self.last_scan = None
        self.last_scan_at = float("-inf")
        if os.path.lexists(self.path):   # a dangling link is an existing state entry, never a first install
            try: text = self.path.read_text()
            except (OSError, UnicodeDecodeError) as error: raise IRStateError('unreadable') from error
            try: data = strict_json(text)
            except (ValueError, RecursionError) as error: raise IRStateError('not-json') from error
            if (not isinstance(data, dict) or data.get('schema') != 2
                    or set(data) != {'schema','bridge_id','profiles','mute_state'}):
                raise IRStateError('bad-shape')
            bridge_id, profiles, mute_state = data['bridge_id'], data['profiles'], data['mute_state']
            if not isinstance(bridge_id, str) or not identifier(bridge_id) or (mute_state is not None and type(mute_state) is not bool):
                raise IRStateError('bad-identity')
            try: validate_profiles(profiles)
            except (ValueError, TypeError, KeyError, AttributeError) as error: raise IRStateError('bad-profiles') from error
            self.bridge_id, self.profiles = bridge_id, profiles
            # A restart cannot establish whether a P1 or physical remote changed mute.
            self.mute_state = None

    def _save(self, profiles, mute):
        validate_profiles(profiles)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump({'schema': 2, 'bridge_id': self.bridge_id, 'profiles': profiles, 'mute_state': mute}, out)
                out.flush(); os.fsync(out.fileno())
            os.replace(temp, self.path)
            self.profiles, self.mute_state = profiles, mute
        finally:
            if os.path.exists(temp): os.unlink(temp)

    def persist(self):
        self._save(self.profiles, self.mute_state)

    def expire(self):
        if self.session:
            if self.clock() >= self.session['expires']: self.session = None
            elif self.session['phase'] == 'waiting' and self.clock() >= self.session['capture_expires']:
                self.session['phase'] = 'timeout'

    @property
    def learning(self):
        self.expire()
        return self.session is not None

    def _session(self, sid):
        self.expire()
        if not self.session or self.session['session_id'] != sid: raise IRException('session_expired')
        return self.session

    def session_status(self, session):
        keys, index = session['keys'], session['index']
        return {field: session[field] for field in ('session_id','remote_id','name','model','phase','candidate','shared','verified')} | {
            'key': keys[index] if index < len(keys) else None,
            'step': index + 1, 'steps': len(keys), 'mappings': session['mappings']}

    def manage(self, data):
        validate_management(data)
        op = data['op']; self.expire()
        if op in BRIDGE_OPS: raise IRException('invalid_request')   # owned by the bridge, never by IR profiles
        if op == 'begin':
            if self.session: raise IRException('busy')
            rid = data.get('remote_id', uuid.uuid4().hex)
            if 'remote_id' in data and rid not in self.profiles: raise IRException('unknown_remote')
            if rid not in self.profiles and len(self.profiles) >= 32: raise IRException('remote_limit')
            self.session = {'session_id': uuid.uuid4().hex, 'remote_id': rid, 'name': data['name'].strip(),
                            'model': data['model'], 'keys': data.get('keys', list(KEYS)), 'index': 0,
                            'phase': 'ready', 'candidate': None, 'shared': False, 'verified': 0,
                            'mappings': copy.deepcopy(self.profiles[rid]['mappings']) if rid in self.profiles else empty_mappings(),
                            'expires': self.clock() + 300, 'capture_expires': 0}
            return self.session_status(self.session)
        if op in ('capture','session','choose','commit','cancel'):
            session = self._session(data['session_id'])
            if op == 'capture':
                if session['index'] >= len(session['keys']): raise IRException('not_ready')
                session.update(phase='waiting', candidate=None, shared=False, verified=0, capture_expires=self.clock()+30)
            elif op == 'choose':
                choice = data['choice']
                if session['index'] >= len(session['keys']): raise IRException('not_ready')
                if choice == 'retry': return self.manage({'op':'capture','session_id':data['session_id']})
                key = session['keys'][session['index']]
                if choice == 'keep':
                    if session['phase'] != 'captured': raise IRException('no_capture')
                    candidate = session['candidate']
                    self._check_code(session, key, candidate)
                else: candidate = None
                session['mappings'][key] = {'code': candidate, 'enabled': False}
                session['index'] += 1
                session.update(phase='review' if session['index'] == len(session['keys']) else 'ready', candidate=None, shared=False, verified=0)
            elif op == 'commit':
                if session['phase'] != 'review': raise IRException('not_ready')
                profiles = copy.deepcopy(self.profiles)
                profiles[session['remote_id']] = {
                    'name': session['name'], 'model': session['model'],
                    'active': profiles.get(session['remote_id'],{}).get('active',False), 'mappings': session['mappings']}
                self._save(profiles, self.mute_state)
                rid = session['remote_id']; self.session = None
                return {'remote_id': rid}
            elif op == 'cancel': self.session = None; return {}
            return self.session_status(session)
        if op == 'resync': self._save(self.profiles, data['muted']); return {}
        if self.session: raise IRException('busy')
        rid = data['remote_id']
        if rid not in self.profiles: raise IRException('unknown_remote')
        profiles = copy.deepcopy(self.profiles)
        if op == 'delete': del profiles[rid]
        elif op == 'update':
            profile = profiles[rid]
            if 'name' in data: profile['name'] = data['name'].strip()
            if 'active' in data: profile['active'] = data['active']
            if 'key' in data:
                mapping = profile['mappings'][data['key']]
                if data.get('remove'): mapping.update(code=None,enabled=False)
                elif 'enabled' in data:
                    if data['enabled'] and mapping['code'] is None: raise IRException('no_mapping')
                    mapping['enabled'] = data['enabled']
        self._save(profiles, self.mute_state)
        return {}

    def _check_code(self, session, key, code):
        for rid, profile in self.profiles.items():
            if rid == session['remote_id']: continue
            for other, mapping in profile['mappings'].items():
                if mapping['code'] == code:
                    if other != key: raise IRException('conflict')
                    session['shared'] = True
        for other, mapping in session['mappings'].items():
            if other != key and mapping['code'] == code: raise IRException('conflict')

    def scan_event(self, value):
        """Consume decoded MSC_SCAN even when Linux has no assigned key."""
        self.expire()
        if type(value) is not int or not -0x80000000 <= value <= 0xffffffff:
            return None
        scan, now = value & 0xffffffff, self.clock()
        repeated = scan == self.last_scan and now - self.last_scan_at < .3
        self.last_scan, self.last_scan_at = scan, now
        # ponytail: a 300 ms quiet gap separates presses; revisit for slower repeat protocols.
        key = self._press(scan) if not self.session or not repeated else None
        if repeated and key == "mute": return None
        return key

    def _press(self, scan):
        if self.session:
            if self.session['phase'] == 'waiting':
                if self.session['candidate'] is None: self.session['candidate'] = scan
                if self.session['candidate'] != scan:
                    self.session['phase'] = 'inconsistent'
                    return None
                self.session['verified'] += 1
                if self.session['verified'] == 3: self.session['phase'] = 'captured'
                key = self.session['keys'][self.session['index']]
                try: self._check_code(self.session,key,scan)
                except IRException: self.session['phase'] = 'conflict'
            return None
        for profile in self.profiles.values():
            if not profile['active']: continue
            for key, mapping in profile['mappings'].items():
                if mapping['enabled'] and mapping['code'] == scan: return key
        return None

    def command_succeeded(self, action, zone='a'):
        if action in (f'room-{zone}-mute', f'room-{zone}-unmute'):
            try: self._save(self.profiles, action.endswith('-mute'))
            except OSError:
                self.mute_state = None
                raise

    def set_target_zone(self, zone):
        # The mute state belongs to one target; a change makes it unknown until the next command on that target.
        if zone == self.target_zone: return
        self.mute_state = None
        self.persist()
        self.target_zone = zone

    def mute_unknown(self):
        self.mute_state = None
        self._save(self.profiles, None)

    def status(self):
        self.expire()
        learning = None
        if self.session:
            state = self.session_status(self.session)
            learning = {k: state[k] for k in ('remote_id','phase','key','step','steps')}
        return {'profiles': self.profiles, 'learning': learning, 'mute_state': self.mute_state}
