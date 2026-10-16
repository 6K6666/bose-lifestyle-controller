"""Pure room settings and card layout logic (no Home Assistant imports)."""
import _bootstrap
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
m = run_path(str(ROOT / 'custom_components/bose_lifestyle/rooms.py'))
KEYS = ('power-on', 'power-off', 'volume-up', 'volume-down', 'mute', 'unmute', 'tv', 'music')

assert m['ROOM_IDS'] == ('a', 'b', 'c', 'd')
names = {r: v['name'] for r, v in m['normalize_rooms'](None).items()}
assert names == {'a': 'Room A', 'b': 'Room B', 'c': 'Room C', 'd': 'Room D'}
assert m['normalize_rooms'](None) == {r: {'name': 'Room ' + r.upper()} for r in 'abcd'}   # names only: zone state is the bridge config's
for garbage in (None, 5, [], 'x', {'a': 5}, {'zz': {'name': 'x'}}, {'a': {'name': 'x' * 41}}, {'a': {'name': ''}}, {'a': {'name': 'a\x00b'}}, {'b': {'enabled': 'yes'}}):
    got = m['normalize_rooms'](garbage)
    assert {r: v['name'] for r, v in got.items()} == names and all(set(v) == {'name'} for v in got.values()), garbage
kept = m['normalize_rooms']({'a': {'name': ' Lounge ', 'enabled': False}})
assert kept['a'] == {'name': 'Lounge'} and kept['b'] == {'name': 'Room B'}

assert m['validate_room_change']({'room': 'b', 'name': '  Garden  '}) == {'room': 'b', 'name': 'Garden'}
assert m['validate_room_change']({'room': 'd', 'enabled': True}) == {'room': 'd', 'enabled': True}
for bad in ({'room': 'e', 'enabled': True}, {'room': 'a'}, {'room': 'a', 'name': ''}, {'room': 'a', 'name': 'x' * 41},
            {'room': 'a', 'name': 'a\nb'}, {'room': 'a', 'enabled': 1}, {'room': 'a', 'enabled': True, 'x': 1}, {'enabled': True}, 'a'):
    try: m['validate_room_change'](bad)
    except ValueError: pass
    else: raise AssertionError(bad)

assert m['room_action']('a', 'volume-down') == 'volume-down' and m['room_action']('a', 'volume-up') == 'room-a-volume-up'
assert [m['room_action']('b', k) for k in KEYS] == ['room-b-power-on', 'room-b-power-off', 'room-b-volume-up', 'room-b-volume-down',
                                                    'room-b-mute', 'room-b-unmute', 'room-b-source-video-1', 'room-b-source-aux']
assert m['room_action']('d', 'music') == 'room-d-source-aux'

print('Room names normalize safely; zone state is never read from HA storage')

# ---- Task 10b: schema 4 settings, layout and IR target (stdlib; no HA imports) ----
import asyncio
import json
from copy import deepcopy
V4 = json.loads((ROOT / 'tests/fixtures/status_v4.json').read_text())
NAMES = m['normalize_rooms'](None)
ENTITY = lambda action: 'button.' + action.replace('-', '_')

def live4(mutate=None):
    data = deepcopy(V4)
    data['bridge'].update(mode='live', serial_open=True, fault=None)
    if mutate: mutate(data)
    return data

def v4layout(mutate=None):
    return m['build_layout_v4'](NAMES, live4(mutate), ENTITY, 'sensor.health', 'sensor.outcome')

# Daily layout omits disabled zones; administrative settings keep all zones.
lay = v4layout()
assert [r['id'] for r in lay['rooms']] == ['a', 'b', 'c'] and lay['manage_url'] == '/config/integrations/integration/bose_lifestyle'
room = {r['id']: r for r in lay['rooms']}
assert 'd' not in room and room['a']['enabled'] is True
assert [r['id'] for r in m['build_settings'](NAMES, live4())] == ['a','b','c','d']
on_d = v4layout(lambda d: d['config']['zones']['d'].update(enabled=True))['rooms'][-1]
assert on_d['id'] == 'd' and on_d['name'] == 'Room D'
assert set(room['a']['buttons']) == {'power-on', 'power-off', 'volume-up', 'volume-down', 'mute', 'unmute', 'tv', 'music', 'source-am', 'source-video-2'}
assert room['a']['buttons']['tv'] == 'button.room_a_source_video_1' and room['a']['buttons']['volume-down'] == 'button.volume_down'
assert not any(k in room['a']['buttons'] for k in ('source-cd', 'source-fm', 'source-tape')), 'disconnected sources are not shown'
assert room['a']['unavailable'] == {}
assert room['b']['unavailable'] == {} and room['c']['unavailable'] == {}   # B/C Video 2 was promoted to verified 2026-10-11
assert 'source-video-2' in room['b']['buttons'] and 'source-video-2' in room['c']['buttons']
assert set(on_d['buttons']) == set(on_d['unavailable']) and set(on_d['unavailable'].values()) == {'experimental-off'}
assert len(on_d['buttons']) == 10
assert set(v4layout(lambda d: d['bridge'].update(fault='partial-write'))['rooms'][2]['unavailable'].values()) == {'fault'}   # fault outranks zone-disabled
# The bridge no longer emits allowed true with a fault or in dry-run; these cases check that HA's own check still holds.
assert set(v4layout(lambda d: d['bridge'].update(serial_open=False))['rooms'][0]['unavailable'].values()) == {'not-live'}
assert set(v4layout(lambda d: d['bridge'].update(mode='dry-run'))['rooms'][0]['unavailable'].values()) == {'not-live'}
assert 'ir_target' in lay and set(lay) == {'health', 'outcome', 'manage_url', 'rooms', 'ir_target', 'bridge', 'reason'}
R = m['unavailable_reason']
assert R(live4(), 'a', {'function': 'source', 'source': 'cd', 'allowed': False}) == 'source-not-connected'
assert R(live4(), 'a', {'function': 'mute', 'source': None, 'allowed': False}) == 'experimental-off'
assert R(live4(), 'a', {'function': 'mute', 'source': None, 'allowed': True}) is None
assert R(live4(lambda d: d['bridge'].update(fault='x')), 'a', {'function': 'mute', 'source': None, 'allowed': True}) == 'fault'
assert R(live4(), 'd', {'function': 'mute', 'source': None, 'allowed': False}) == 'zone-disabled'
# Schema 3 layout and settings are removed; only schema 4 layout is built.

# test_ir_target_state_exposed (pure part): every reason value, and the allowed flag.
T = m['ir_target']
assert T(live4(), NAMES) == {'zone': 'a', 'zone_name': 'Room A', 'allowed': True, 'reason': None}
def ir_zone(zone, extra=None):
    def mutate(d):
        d['config']['ir']['zone'] = zone
        if extra: extra(d)
    return live4(mutate)
assert T(ir_zone('b'), NAMES)['allowed'] is True
assert T(ir_zone('d'), NAMES) == {'zone': 'd', 'zone_name': 'Room D', 'allowed': False, 'reason': 'zone-disabled'}
assert T(ir_zone('d', lambda d: d['config']['zones'].update(d={'enabled': True})), NAMES) == {'zone': 'd', 'zone_name': 'Room D', 'allowed': False, 'reason': 'experimental-off'}
def no_config(d):
    d.update(config=None, actions={}); d['bridge']['config'] = 'config-invalid'
assert T(live4(no_config), NAMES) == {'zone': None, 'zone_name': None, 'allowed': False, 'reason': 'config-invalid'}
assert T(live4(lambda d: d['actions'].pop('room-a-volume-up')), NAMES)['reason'] == 'config-invalid'
reasons = {T(ir_zone(z, x), NAMES)['reason'] for z, x in (('a', None), ('d', None), ('d', lambda d: d['config']['zones'].update(d={'enabled': True})), ('a', lambda d: None))}
assert reasons == {None, 'zone-disabled', 'experimental-off'}

# test_ha_reason_for_unavailable_when_fault_or_dry_run_prefers_fault_or_not_live_over_experimental_off:
# with fault or dry-run the bridge publishes allowed false everywhere, so the reason must name the bridge state.
def offline(mutate):
    def edit(d):
        for entry in d['actions'].values(): entry['allowed'] = False
        mutate(d)
    return edit
FAULTED = lambda d: d['bridge'].update(fault='partial-write')
DRY_RUN = lambda d: d['bridge'].update(mode='dry-run')
for label, mutate, expected in (('fault', FAULTED, 'fault'), ('dry-run', DRY_RUN, 'not-live')):
    assert set(v4layout(offline(mutate))['rooms'][0]['unavailable'].values()) == {expected}, label
    assert set(v4layout(offline(mutate))['rooms'][2]['unavailable'].values()) == {expected}, label   # zone-disabled is outranked too
    assert T(live4(offline(mutate)), NAMES) == {'zone': 'a', 'zone_name': 'Room A', 'allowed': False, 'reason': expected}, label
    assert T(live4(offline(lambda d, m=mutate: (m(d), d['config']['ir'].update(zone='d')))), NAMES)['reason'] == expected, label
print('HA reasons with fault or dry-run name the bridge state, never experimental-off or zone-disabled')

# test_set_room_uses_set_zone_and_set_unverified_on_schema4
assert m['room_ops']({'room': 'a', 'enabled': False}) == [('set_zone', {'zone': 'a', 'enabled': False})]
assert m['room_ops']({'room': 'd', 'enabled': True}) == [('set_zone', {'zone': 'd', 'enabled': True}), ('set_unverified', {'zone': 'd', 'allow': True})]
assert m['room_ops']({'room': 'd', 'enabled': False}) == [('set_zone', {'zone': 'd', 'enabled': False}), ('set_unverified', {'zone': 'd', 'allow': False})]
assert m['room_ops']({'room': 'a', 'name': 'Lounge'}) == []
assert m['room_ops'].__code__.co_varnames[:1] == ('change',)   # one signature: no schema argument
legacy = {'a': {'enabled': True, 'name': 'Room A'}, 'b': {'enabled': True, 'name': 'Room B'}}
assert m['stored_rooms_after'](legacy, {'room': 'a', 'enabled': False}) == legacy   # enabled is never written to HA; legacy kept
renamed = m['stored_rooms_after'](legacy, {'room': 'a', 'name': 'Lounge'})
assert renamed['a'] == {'enabled': True, 'name': 'Lounge'} and renamed['b'] == legacy['b'] and legacy['a']['name'] == 'Room A'
settings4 = {r['id']: r for r in m['build_settings'](NAMES, live4())}
assert (settings4['a']['enabled'], settings4['d']['enabled'], settings4['d']['live']) == (True, False, False)
assert m['build_settings'](NAMES, live4(lambda d: d['config']['unverified'].update(d=True)))[3]['live'] is False   # zone still off
assert m['build_settings'](NAMES, live4(lambda d: (d['config']['zones'].update(d={'enabled': True}), d['config']['unverified'].update(d=True))))[3]['live'] is True

calls = []
def fake_manage(fail_on=None, code='not_saved'):
    calls.clear()
    async def manage(op, **fields):
        calls.append((op, fields))
        if op == fail_on: raise RuntimeError(code)
    return manage
async def scenario():
    await m['run_room_ops'](fake_manage(), m['room_ops']({'room': 'd', 'enabled': True}))
    assert [c[0] for c in calls] == ['set_zone', 'set_unverified']
    for fail, code, partial, expect in (('set_zone', 'not_saved', False, ['set_zone']),
                                        ('set_unverified', 'uncertain', True, ['set_zone', 'set_unverified']),
                                        ('set_zone', 'dry_run', False, ['set_zone'])):
        try: await m['run_room_ops'](fake_manage(fail, code), m['room_ops']({'room': 'd', 'enabled': False}))
        except m['RoomChangeError'] as error: assert (error.code, error.partial) == (code, partial), (error.code, error.partial)
        else: raise AssertionError('room change error was swallowed')
        assert [c[0] for c in calls] == expect, calls   # no retry, the second op only after the first succeeded
asyncio.run(scenario())
print('Schema 4 room changes use set_zone then set_unverified, errors keep bridge host codes, layout and IR target are status-driven')

# ---- Task 8: setup screen helpers (stdlib; no HA imports) ----
BOSE_IR = run_path(str(ROOT / 'bose_ir.py'))
assert m['SOURCES'] == BOSE_IR['SOURCES'] == ('am', 'fm', 'cd', 'aux', 'video-1', 'video-2', 'tape')   # one source list, the bridge's
assert m['SOURCE_LABELS'] == {'am': 'AM radio', 'fm': 'FM radio', 'cd': 'CD player', 'aux': 'Auxiliary input',
                              'video-1': 'Television (Video 1)', 'video-2': 'Video 2', 'tape': 'Tape deck'}   # test_source_labels_are_the_familiar_names
assert m['normalize_rooms']({'a': {'name': 'x' * 40}})['a']['name'] == 'x' * 40   # a 40-character name survives a reload
others = ['Room A', 'Room B', 'Room C', 'Room D']
assert m['clean_room_name'](' Garden ', others) == 'Garden' and m['clean_room_name']('x' * 40, others) == 'x' * 40
# test_blank_duplicate_or_overlong_name_rejected_never_saved (pure part: the websocket test checks the Store)
for name, code in {'': 'invalid_name', '   ': 'invalid_name', 'x' * 41: 'invalid_name', 'a\x01b': 'invalid_name', 'a\x7fb': 'invalid_name',
                   5: 'invalid_name', 'room b': 'duplicate_name', ' ROOM D ': 'duplicate_name'}.items():
    try: m['clean_room_name'](name, others)
    except ValueError as error: assert str(error) == code, (name, str(error))
    else: raise AssertionError(name)
assert set(m['NAME_ERRORS']) == {'invalid_name', 'duplicate_name'}
assert m['NAME_ERRORS']['invalid_name'] and 'restart' not in m['NAME_ERRORS']['invalid_name']

# test_fresh_install_shows_finish_setup (pure part): config ok with every zone off; null config is not a fresh install
fresh = deepcopy(V4)
for zone in 'abcd': fresh['config']['zones'][zone]['enabled'] = False
assert m['needs_setup'](fresh) is True and m['needs_setup'](V4) is False and m['needs_setup']({**V4, 'config': None}) is False
state = m['setup_state'](V4, m['build_settings'](NAMES, V4))
assert set(state) == {'needs_setup', 'rooms', 'sources', 'mapping'} and state['needs_setup'] is False
assert [r['label'] for r in state['rooms']] == ['Room A', 'Room B', 'Room C', 'Room D']
assert [(s['source'], s['label'], s['connected']) for s in state['sources']] == [
    ('am', 'AM radio', True), ('fm', 'FM radio', False), ('cd', 'CD player', False), ('aux', 'Auxiliary input', True),
    ('video-1', 'Television (Video 1)', True), ('video-2', 'Video 2', True), ('tape', 'Tape deck', False)]
assert state['mapping'] == 'Rooms A to D are the four room outputs on the Bose control unit.'
assert m['setup_state'](fresh, m['build_settings'](NAMES, fresh))['needs_setup'] is True
print('Setup helpers: name rules, source labels, fresh-install detection and setup state')

# ---- Follow-up 8b: per-room experimental opt-in and generic default room names (stdlib) ----
def enable_d(data, unverified):
    data['config']['zones']['d']['enabled'] = True
    data['config']['unverified']['d'] = unverified
    for action in data['actions'].values():
        if action['zone'] == 'd': action['allowed'] = unverified   # the bridge host computes allowed from unverified

def test_experimental_only_buttons_show_visible_reason_until_enabled():
    # Room D is the only room with experimental-only buttons since B/C Video 2 was promoted.
    off = v4layout(lambda d: enable_d(d, False))['rooms'][-1]
    assert off['id'] == 'd' and set(off['unavailable'].values()) == {'experimental-off'} and set(off['buttons']) == set(off['unavailable'])
    on = v4layout(lambda d: enable_d(d, True))['rooms'][-1]
    assert on['unavailable'] == {} and 'source-video-2' in on['buttons']
    assert {r['id']: r['experimental'] for r in m['build_settings'](NAMES, live4(lambda d: enable_d(d, True)))} == {'a': False, 'b': False, 'c': False, 'd': True}

def test_default_room_names_are_generic():
    # The house names are assembled at runtime so this file never contains them.
    forbidden = [' '.join(['Family', 'Room']), ''.join(['Back', 'yard']), ' '.join(['Master', 'Bedroom'])]
    skipped = {'.git', '__pycache__', 'tests'}
    scanned = [p for p in sorted(ROOT.rglob('*'))
               if p.is_file() and not (set(p.relative_to(ROOT).parts) & skipped) and not p.name.startswith('test_')]
    assert len(scanned) > 10, scanned
    for path in scanned:
        text = path.read_bytes().decode('utf-8', 'ignore').casefold()
        for word in forbidden: assert word.casefold() not in text, (path.name, word)
    assert {r: v['name'] for r, v in m['normalize_rooms'](None).items()} == {'a': 'Room A', 'b': 'Room B', 'c': 'Room C', 'd': 'Room D'}

def test_stored_names_override_defaults():
    got = m['normalize_rooms']({'b': {'name': 'Garden'}})
    assert got['b']['name'] == 'Garden' and got['a']['name'] == 'Room A' and got['c']['name'] == 'Room C' and got['d']['name'] == 'Room D'

def test_bridge_name_rules():
    # Task 8c: the bridge computer nickname uses the room-name rules; None clears it; blank is an error, never a clear.
    assert m['clean_bridge_name'](' Den ') == 'Den' and m['clean_bridge_name']('x' * 40) == 'x' * 40
    assert m['clean_bridge_name'](None) is None
    for bad in ('', '   ', 'x' * 41, 'Lo\x01unge', 5):
        try: m['clean_bridge_name'](bad)
        except ValueError as error: assert str(error) == 'invalid_name', bad
        else: raise AssertionError(bad)
    assert m['stored_bridge_name'](None) == 'Bose Controller'
    assert m['stored_bridge_name']({'bridge_name': 'Den'}) == 'Den'
    assert m['stored_bridge_name']({'bridge_name': 'x' * 41}) == 'Bose Controller'
    stored = {'rooms': {'b': {'name': 'Garden'}}, 'bridge_name': 'Den'}
    assert m['stored_after_bridge_name'](stored, 'Hall') == {'rooms': {'b': {'name': 'Garden'}}, 'bridge_name': 'Hall'}
    assert m['stored_after_bridge_name'](stored, None) == {'rooms': {'b': {'name': 'Garden'}}}
    assert m['stored_after_rooms']({'bridge_name': 'Den'}, {'a': {'name': 'Lounge', 'enabled': True}})['bridge_name'] == 'Den'   # room saves keep the nickname

def test_bridge_name_not_required_unique_vs_rooms():
    # A bridge nickname may equal a room name; room names keep their own duplicate rule.
    assert m['clean_bridge_name']('Room A') == 'Room A' and m['stored_bridge_name']({'bridge_name': 'Room B'}) == 'Room B'
    assert m['clean_room_name']('Den', ['Room A']) == 'Den'
    try: m['clean_room_name']('room a', ['Room A'])
    except ValueError as error: assert str(error) == 'duplicate_name'
    else: raise AssertionError('room duplicate allowed')

test_experimental_only_buttons_show_visible_reason_until_enabled()
test_default_room_names_are_generic()
test_stored_names_override_defaults()
test_bridge_name_rules()
test_bridge_name_not_required_unique_vs_rooms()
print('Experimental reasons are visible until enabled; default room names are generic; stored names win')

# ---- Task 10d: input nicknames and icons (stdlib; no HA imports) ----
assert m['INPUT_ICON_IDS'] == ('tv', 'music', 'radio', 'disc', 'tape', 'game', 'speaker', 'computer', 'phone', 'streaming', 'input')
DEFAULT_INPUTS = {'am': {'name': 'AM radio', 'icon': 'radio'}, 'fm': {'name': 'FM radio', 'icon': 'radio'}, 'cd': {'name': 'CD player', 'icon': 'disc'},
                  'aux': {'name': 'Auxiliary input', 'icon': 'input'}, 'video-1': {'name': 'Television (Video 1)', 'icon': 'tv'},
                  'video-2': {'name': 'Video 2', 'icon': 'game'}, 'tape': {'name': 'Tape deck', 'icon': 'tape'}}
assert m['normalize_inputs'](None) == DEFAULT_INPUTS and m['normalize_inputs'](V4) == DEFAULT_INPUTS   # defaults resolved, nothing stored

def test_set_input_rules():
    assert m['clean_input_change']('am', {'name': '  Den  '}) == {'name': 'Den'}
    assert m['clean_input_change']('cd', {'icon': 'tv'}) == {'icon': 'tv'}
    assert m['clean_input_change']('cd', {'name': None, 'icon': None}) == {'name': None, 'icon': None}
    assert m['clean_input_change']('tape', {'name': 'x' * 30}) == {'name': 'x' * 30}
    for source, fields, code in (('am', {'name': ''}, 'invalid_name'), ('am', {'name': '   '}, 'invalid_name'),
                                 ('am', {'name': 'x' * 31}, 'invalid_name'), ('am', {'name': 'a\x01b'}, 'invalid_name'),
                                 ('am', {'name': 5}, 'invalid_name'), ('am', {'icon': 'bogus'}, 'invalid_icon'),
                                 ('am', {'icon': 'TV'}, 'invalid_icon'), ('am', {'icon': None, 'name': 'x', 'extra': 1}, None),
                                 ('e', {'name': 'x'}, 'invalid_request'), (None, {'name': 'x'}, 'invalid_request')):
        try: m['clean_input_change'](source, fields)
        except ValueError as error:
            assert code is not None and str(error) == code, (source, fields, str(error))
        else: assert code is None, (source, fields)   # the extra key is ignored, not an error
    assert m['INPUT_ERRORS']['invalid_name'].startswith('Enter a nickname from 1 to 30') and 'restart' not in str(m['INPUT_ERRORS'])
    assert set(m['INPUT_ERRORS']) >= {'invalid_name', 'invalid_icon', 'invalid_request'}

def test_unknown_stored_icon_falls_back():
    got = m['normalize_inputs']({'inputs': {'am': {'name': 'Den', 'icon': 'spaceship'}, 'cd': {'name': 'x' * 31, 'icon': 'tv'},
                                            'video-2': {'name': None}, 'bogus-source': {'name': 'Z', 'icon': 'tv'}}})
    assert got['am'] == {'name': 'Den', 'icon': 'radio'} and got['cd'] == {'name': 'CD player', 'icon': 'tv'}
    assert got['video-2'] == {'name': 'Video 2', 'icon': 'game'} and set(got) == set(m['SOURCES'])
    assert m['normalize_inputs']({'inputs': 'garbage'}) == DEFAULT_INPUTS

def test_set_input_stores_per_source_and_keeps_other_keys():
    stored = {'rooms': {'b': {'name': 'Garden'}}, 'bridge_name': 'Den', 'inputs': {'cd': {'name': 'Disc', 'icon': 'disc'}}}
    icon_only = m['stored_after_input'](stored, 'cd', {'icon': 'speaker'})
    assert icon_only == {'rooms': {'b': {'name': 'Garden'}}, 'bridge_name': 'Den', 'inputs': {'cd': {'name': 'Disc', 'icon': 'speaker'}}}   # a custom nickname survives an icon pick
    name_null = m['stored_after_input'](stored, 'cd', {'name': None})
    assert name_null['inputs'] == {'cd': {'name': None, 'icon': 'disc'}}   # Use default keeps the custom icon
    assert m['normalize_inputs'](name_null)['cd'] == {'name': 'CD player', 'icon': 'disc'}
    reset = m['stored_after_input'](stored, 'cd', {'name': None, 'icon': None})
    assert reset == {'rooms': {'b': {'name': 'Garden'}}, 'bridge_name': 'Den', 'inputs': {}} and stored['inputs']['cd']['name'] == 'Disc'   # input not mutated
    assert m['stored_after_rooms'](stored, {'a': {'name': 'x', 'enabled': True}})['inputs'] == stored['inputs']   # room saves keep inputs

test_set_input_rules()
test_unknown_stored_icon_falls_back()
test_set_input_stores_per_source_and_keeps_other_keys()
def test_default_name_with_icon_change_stores_name_null():
    # A nickname equal to the default plus an icon change stores only the icon; "Use default" and the stored state agree.
    change = m['clean_input_change']('cd', {'name': 'CD player', 'icon': 'tv'})
    assert change == {'name': None, 'icon': 'tv'}
    assert m['clean_input_change']('cd', {'name': '  CD player  '}) == {'name': None}
    stored = m['stored_after_input']({}, 'cd', change)
    assert stored == {'inputs': {'cd': {'name': None, 'icon': 'tv'}}}
    assert m['normalize_inputs'](stored)['cd'] == {'name': 'CD player', 'icon': 'tv'}
    assert m['stored_after_input']({}, 'cd', m['clean_input_change']('cd', {'name': 'CD player'})) == {'inputs': {}}   # default name alone is no entry
    assert m['clean_input_change']('cd', {'name': 'Den CD', 'icon': 'tv'}) == {'name': 'Den CD', 'icon': 'tv'}

test_default_name_with_icon_change_stores_name_null()

print('Input nicknames and icons: rules, defaults, unknown icons fall back, stored per source')

# ---- Task 10g: IR receiver label and the config-uncertain layout ----
def test_ir_receiver_label_validation_and_clear():
    assert m['clean_ir_label'](None) is None
    assert m['clean_ir_label']('  Kitchen  ') == 'Kitchen' and m['clean_ir_label']('x' * 40) == 'x' * 40
    for bad in ('', '   ', 'x' * 41, 'Kit\nchen', 'Kit\tchen', 5):
        try: m['clean_ir_label'](bad)
        except ValueError as error: assert str(error) == 'invalid_name', bad
        else: raise AssertionError(bad)
    stored = {'rooms': {'a': {'name': 'Den'}}, 'bridge_name': 'Bridge'}
    labelled = m['stored_after_ir_receiver_label'](stored, 'Kitchen')
    assert labelled == {'rooms': {'a': {'name': 'Den'}}, 'bridge_name': 'Bridge', 'ir_receiver_label': 'Kitchen'}
    assert m['stored_ir_receiver_label'](labelled) == 'Kitchen'
    cleared = m['stored_after_ir_receiver_label'](labelled, None)
    assert cleared == stored and m['stored_ir_receiver_label'](cleared) is None
    assert m['stored_ir_receiver_label']({'ir_receiver_label': 'a\x00'}) is None and m['stored_ir_receiver_label'](None) is None

def test_config_uncertain_layout_shows_config_message_not_isnt_ready():
    def uncertain(data):
        data['config'] = None
        data['actions'] = {}
        data['bridge']['config'] = 'config-uncertain'
    lay = v4layout(uncertain)
    assert lay['rooms'] == [] and lay['reason'] == 'config-uncertain'
    assert lay['bridge'] == {'fault': None, 'mode': 'live', 'serial_open': True, 'config': 'config-uncertain'}
    assert lay['ir_target'] == {'zone': None, 'zone_name': None, 'allowed': False, 'reason': 'config-uncertain'}
    def invalid(data):
        data['config'] = None
        data['actions'] = {}
        data['bridge']['config'] = 'config-invalid'
    lay = v4layout(invalid)
    assert lay['rooms'] == [] and lay['reason'] == 'config-invalid' and lay['ir_target']['reason'] == 'config-invalid'
    assert v4layout()['reason'] is None

test_ir_receiver_label_validation_and_clear()
test_config_uncertain_layout_shows_config_message_not_isnt_ready()
print('IR receiver label rules and the config-uncertain layout passed')
