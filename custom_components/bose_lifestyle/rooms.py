"""Pure room settings and card layout logic. No Home Assistant imports, so it is testable with the stdlib."""
import unicodedata

ROOM_IDS = ('a', 'b', 'c', 'd')
SOURCES = ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')   # same order and names as bose_ir.SOURCES
SOURCE_LABELS = {'am': 'AM radio', 'fm': 'FM radio', 'cd': 'CD player', 'aux': 'Auxiliary input',
                 'video-1': 'Television (Video 1)', 'video-2': 'Video 2', 'tape': 'Tape deck'}   # the jack, in familiar words
MAPPING_TEXT = 'Rooms A to D are the four room outputs on the Bose control unit.'
NAME_MAX = 40
NAME_ERRORS = {'invalid_name': 'Enter a name from 1 to 40 characters. Control characters are not allowed.',
               'duplicate_name': 'Another room already has that name.'}
DEFAULT_BRIDGE_NAME = 'Bose Controller'   # the bridge computer's name until an administrator sets a nickname
DEFAULT_ROOMS = {'a': {'name': 'Room A'}, 'b': {'name': 'Room B'}, 'c': {'name': 'Room C'}, 'd': {'name': 'Room D'}}
# Input nicknames and icons (Task 10d): global per source, stored in the HA Store under 'inputs'.
INPUT_ICON_IDS = ('tv', 'music', 'radio', 'disc', 'tape', 'game', 'speaker', 'computer', 'phone', 'streaming', 'input')
DEFAULT_INPUT_ICONS = {'am': 'radio', 'fm': 'radio', 'cd': 'disc', 'aux': 'input', 'video-1': 'tv', 'video-2': 'game', 'tape': 'tape'}
INPUT_NAME_MAX = 30
INPUT_ERRORS = {'invalid_name': 'Enter a nickname from 1 to 30 characters. Control characters are not allowed.',
                'invalid_icon': 'Choose one of the built-in icons.', 'invalid_request': 'Invalid input'}
SUFFIX = {'tv': 'source-video-1', 'music': 'source-aux'}
MANAGE_URL = '/config/integrations/integration/bose_lifestyle'


def _valid_name(value, limit=NAME_MAX):
    return isinstance(value, str) and 1 <= len(value.strip()) <= limit and not any(unicodedata.category(c) == 'Cc' for c in value)


def clean_name(name):
    """Trimmed name, or ValueError('invalid_name'): blank, over 40 characters, not a string or with control characters."""
    if not _valid_name(name): raise ValueError('invalid_name')
    return name.strip()


def clean_room_name(name, others):
    """Trimmed name for a room. ValueError carries the code: 'invalid_name' or 'duplicate_name' (another room has it, ignoring case).
    `others` are the other rooms' current names."""
    text = clean_name(name)
    if text.casefold() in {other.strip().casefold() for other in others}: raise ValueError('duplicate_name')
    return text


def clean_bridge_name(name):
    """The bridge computer's nickname: None clears it (back to the default); otherwise the room-name rules, with no duplicate rule."""
    return None if name is None else clean_name(name)


def stored_bridge_name(stored):
    name = stored.get('bridge_name') if isinstance(stored, dict) else None
    return name.strip() if _valid_name(name) else DEFAULT_BRIDGE_NAME


def clean_ir_label(label):
    """Where the IR receiver is (a label only, HA-side): None clears it; otherwise 1 to 40 characters after trimming, no control characters."""
    return None if label is None else clean_name(label)


def stored_ir_receiver_label(stored):
    label = stored.get('ir_receiver_label') if isinstance(stored, dict) else None
    return label.strip() if _valid_name(label) else None


def stored_after_ir_receiver_label(stored, label):
    data = dict(stored) if isinstance(stored, dict) else {}
    if label is None: data.pop('ir_receiver_label', None)
    else: data['ir_receiver_label'] = label
    return data


def stored_after_bridge_name(stored, name):
    data = dict(stored) if isinstance(stored, dict) else {}
    if name is None: data.pop('bridge_name', None)
    else: data['bridge_name'] = name
    return data


def stored_after_rooms(stored, rooms):
    """The whole HA Store after a room change: the rooms replaced, every other key (the bridge nickname) kept."""
    return {**(stored if isinstance(stored, dict) else {}), 'rooms': rooms}


def needs_setup(status):
    """A fresh install: the bridge host's config is valid and every room is turned off."""
    config = status.get('config') if isinstance(status, dict) else None
    return config is not None and all(config['zones'][room]['enabled'] is False for room in ROOM_IDS)


def setup_state(status, settings):
    """What the admin setup view shows: rooms with their output labels, every source jack, and whether setup is needed."""
    config = status.get('config') or {} if isinstance(status, dict) else {}
    connected = config.get('sources', {})
    return {'needs_setup': needs_setup(status),
            'rooms': [{'id': r['id'], 'name': r['name'], 'label': f"Room {r['id'].upper()}", 'enabled': r['enabled']} for r in settings],
            'sources': [{'source': s, 'label': SOURCE_LABELS[s], 'connected': connected.get(s) is True} for s in SOURCES],
            'mapping': MAPPING_TEXT}


def normalize_rooms(stored):
    """Names for all four rooms; an invalid name falls back to the default. Zone state comes from the bridge config only."""
    stored = stored if isinstance(stored, dict) else {}
    rooms = {}
    for room, default in DEFAULT_ROOMS.items():
        item = stored.get(room) if isinstance(stored.get(room), dict) else {}
        name = item.get('name')
        rooms[room] = {'name': name.strip() if _valid_name(name) else default['name']}
    return rooms


def validate_room_change(change):
    if not isinstance(change, dict) or change.get('room') not in ROOM_IDS or not set(change) <= {'room', 'enabled', 'name'} or len(change) < 2:
        raise ValueError('invalid_request')
    if 'enabled' in change and type(change['enabled']) is not bool: raise ValueError('invalid_request')
    if 'name' in change:
        if not _valid_name(change['name']): raise ValueError('invalid_request')
        change = {**change, 'name': change['name'].strip()}
    return dict(change)


def room_action(room, key):
    if room == 'a' and key == 'volume-down': return 'volume-down'
    return f'room-{room}-{SUFFIX.get(key, key)}'


# Schema 4: the bridge host config is the only source of zone and source state. Other connected sources get 'source-<name>' keys.
SOURCE_KEYS = {'video-1': 'tv', 'aux': 'music'}


def _v4_key(zone, action, info):
    key = SOURCE_KEYS.get(info['source'], f"source-{info['source']}") if info['function'] == 'source' else info['function']
    return key if action == room_action(zone, key) else None   # aliases such as system-power-on never match a room key


def _bridge_reason(bridge):
    """The bridge-wide reason nothing can be sent (fault outranks not-live), or None. Shared by buttons and the IR target."""
    if bridge['fault']: return 'fault'
    if bridge['mode'] != 'live' or bridge['serial_open'] is not True: return 'not-live'
    return None


def unavailable_reason(status, zone, info):
    """Why a schema-4 button is unavailable, from status alone; None when it is available. The first matching reason wins."""
    bridge, config = status['bridge'], status['config']
    bridge_reason = _bridge_reason(bridge)
    if bridge_reason: return bridge_reason
    if config['zones'][zone]['enabled'] is not True: return 'zone-disabled'
    if info['function'] == 'source' and config['sources'].get(info['source']) is not True: return 'source-not-connected'
    if info['allowed'] is not True: return 'experimental-off'
    return None


def ir_target(status, names):
    """The zone the IR remote's volume-up controls, and why it is not allowed, if it is not."""
    config = status['config']
    if config is None:
        reason = 'config-uncertain' if status['bridge']['config'] == 'config-uncertain' else 'config-invalid'
        return {'zone': None, 'zone_name': None, 'allowed': False, 'reason': reason}
    zone = config['ir']['zone']
    info = status['actions'].get(f'room-{zone}-volume-up')
    if info is None: reason = 'config-invalid'
    elif _bridge_reason(status['bridge']): reason = _bridge_reason(status['bridge'])
    elif config['zones'][zone]['enabled'] is not True: reason = 'zone-disabled'
    elif info['allowed'] is not True: reason = 'experimental-off'
    else: reason = None
    return {'zone': zone, 'zone_name': names[zone]['name'], 'allowed': reason is None, 'reason': reason}


def bridge_health(status):
    bridge = status['bridge']
    return {'fault': bridge['fault'], 'mode': bridge['mode'], 'serial_open': bridge['serial_open'], 'config': bridge['config']}


def status_reason(status):
    """Why there is no config to show (config-uncertain or config-invalid), or None when the config is present."""
    return None if status['config'] is not None else status['bridge']['config']


def build_layout_v4(names, status, lookup, health_id, outcome_id):
    """Daily layout from status: disabled zones remain in admin settings and the registry, but are omitted here.
    With no config (uncertain or invalid) the rooms are empty and `reason` says why, so the card can show the right message."""
    config = status['config']
    rooms = []
    for zone in ROOM_IDS:
        mine = [(action, info) for action, info in status['actions'].items() if info['zone'] == zone]
        if not mine or config['zones'][zone]['enabled'] is not True: continue
        buttons, unavailable = {}, {}
        for action, info in mine:
            key = _v4_key(zone, action, info)
            if key is None: continue
            if info['function'] == 'source' and config['sources'].get(info['source']) is not True: continue   # only connected sources
            entity = lookup(action)
            if not entity: continue
            buttons[key] = entity
            reason = unavailable_reason(status, zone, info)
            if reason: unavailable[key] = reason
        rooms.append({'id': zone, 'name': names[zone]['name'], 'enabled': config['zones'][zone]['enabled'] is True,
                      'buttons': buttons, 'unavailable': unavailable})
    return {'health': health_id, 'outcome': outcome_id, 'manage_url': MANAGE_URL, 'rooms': rooms, 'ir_target': ir_target(status, names),
            'bridge': bridge_health(status), 'reason': status_reason(status)}


def room_ops(change):
    """Bridge host config ops for one room change, in order. A name change is stored in HA and needs no op."""
    room = change['room']
    if 'enabled' not in change: return []
    ops = [('set_zone', {'zone': room, 'enabled': change['enabled']})]
    if room == 'd': ops.append(('set_unverified', {'zone': 'd', 'allow': change['enabled']}))   # Room D live = zone on and unverified allowed
    return ops


class RoomChangeError(Exception):
    """A bridge host op failed. partial is True when an earlier op in the same change had already been applied."""
    def __init__(self, code, partial=False):
        super().__init__(code)
        self.code, self.partial = code, partial


async def run_room_ops(manage, ops):
    """Run the ops in order, once each. The first failure stops the change; nothing is retried."""
    for index, (op, fields) in enumerate(ops):
        try:
            await manage(op, **fields)
        except Exception as error:
            raise RoomChangeError(str(error), partial=index > 0) from error


def stored_rooms_after(stored, change):
    """HA Store after one room change: only the name is stored in HA; zone state is the bridge config's."""
    room = change['room']
    rooms = dict(stored) if isinstance(stored, dict) else {}
    if 'name' in change:
        item = rooms.get(room) if isinstance(rooms.get(room), dict) else {}
        rooms[room] = {**item, 'name': change['name']}
    return rooms


def _v4_switch(status, room):
    """(enabled, live) for schema 4. Room D is live when its zone is on and unverified commands are allowed."""
    config = status['config']
    if config is None: return False, False
    enabled = config['zones'][room]['enabled'] is True
    if room != 'd': return enabled, True
    return enabled, enabled and config['unverified']['d'] is True


def _experimental(status, room):
    config = status['config']
    return config is not None and config['unverified'][room] is True


def build_settings(rooms, status):
    states = {room: _v4_switch(status, room) for room in ROOM_IDS}
    return [{'id': room, 'name': rooms[room]['name'], 'enabled': states[room][0], 'live': states[room][1],
             'commissioning': 'No zone installed, commands inferred' if room == 'd' else 'Commissioned',
             'experimental': _experimental(status, room)} for room in ROOM_IDS]


def clean_input_change(source, fields):
    """Validated change for one source. `fields` may hold 'name' and 'icon'; a key that is absent is left unchanged by the caller.
    A value of None means the default for that field, and a name equal to the source's default is stored as None.
    ValueError carries 'invalid_request', 'invalid_name' or 'invalid_icon'."""
    if source not in SOURCES: raise ValueError('invalid_request')
    change = {}
    if 'name' in fields:
        name = fields['name']
        if name is not None and not _valid_name(name, INPUT_NAME_MAX): raise ValueError('invalid_name')
        # The default name is stored as null, so "Use default" and the stored state agree (also when only the icon changes).
        change['name'] = None if name is None or name.strip() == SOURCE_LABELS[source] else name.strip()
    if 'icon' in fields:
        icon = fields['icon']
        if icon is not None and icon not in INPUT_ICON_IDS: raise ValueError('invalid_icon')
        change['icon'] = icon
    return change


def stored_inputs(stored):
    raw = stored.get('inputs') if isinstance(stored, dict) else None
    return raw if isinstance(raw, dict) else {}


def normalize_inputs(stored):
    """Name and icon for every source, defaults resolved; a stale icon or an invalid name falls back to that source's default."""
    raw = stored_inputs(stored)
    out = {}
    for source in SOURCES:
        item = raw.get(source) if isinstance(raw.get(source), dict) else {}
        name, icon = item.get('name'), item.get('icon')
        out[source] = {'name': name.strip() if _valid_name(name, INPUT_NAME_MAX) else SOURCE_LABELS[source],
                       'icon': icon if icon in INPUT_ICON_IDS else DEFAULT_INPUT_ICONS[source]}
    return out


def stored_after_input(stored, source, change):
    """The whole HA Store after one input change. Fields missing from `change` keep their stored value; an entry with
    neither a name nor an icon is removed. Every other key (rooms, bridge nickname) is kept."""
    data = dict(stored) if isinstance(stored, dict) else {}
    inputs = {key: value for key, value in stored_inputs(data).items()}
    entry = dict(inputs[source]) if isinstance(inputs.get(source), dict) else {}
    entry.update(change)
    if entry.get('name') is None and entry.get('icon') is None: inputs.pop(source, None)
    else: inputs[source] = {'name': entry.get('name'), 'icon': entry.get('icon')}
    data['inputs'] = inputs
    return data

