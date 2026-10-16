"""Websocket commands for the Bose card. Layout is open to every user; settings, room changes and events are admin only."""
import logging

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import DOMAIN
from .client import BridgeError
from .protocol import MAX_VOLUME_STEPS
from .rooms import (ROOM_IDS, SOURCES, NAME_ERRORS, normalize_rooms, validate_room_change, build_layout_v4, build_settings,
                    room_ops, run_room_ops, stored_rooms_after, RoomChangeError, clean_room_name, needs_setup, setup_state,
                    clean_bridge_name, stored_bridge_name, stored_after_bridge_name, stored_after_rooms,
                    clean_input_change, normalize_inputs, stored_after_input, INPUT_ERRORS,
                    clean_ir_label, stored_ir_receiver_label, stored_after_ir_receiver_label, ir_target, bridge_health, status_reason)

_LOGGER = logging.getLogger(__name__)
BRIDGE_CODES = frozenset({'not_saved', 'uncertain', 'invalid_request', 'dry_run'})   # reported with their own code


def loaded_entry(hass):
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED: return entry
    return None


def stored_rooms(data):
    return (data or {}).get('rooms') if isinstance(data, dict) else None


async def load_rooms(hass):
    return normalize_rooms(stored_rooms(await hass.data[DOMAIN]['store'].async_load()))


async def load_bridge_name(hass):
    return stored_bridge_name(await hass.data[DOMAIN]['store'].async_load())


async def load_inputs(hass):
    return normalize_inputs(await hass.data[DOMAIN]['store'].async_load())


async def load_ir_label(hass):
    return stored_ir_receiver_label(await hass.data[DOMAIN]['store'].async_load())


def apply_bridge_name(hass, entry, name):
    """Set (or clear, with None) the nickname on the bridge device's name_by_user, so it survives setup and reloads."""
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, entry.unique_id), entry.entry_id)
    if device is not None: devices.async_update_device(device.id, name_by_user=name)


async def layout(hass, entry):
    registry, bid, data = er.async_get(hass), entry.unique_id, entry.runtime_data.data
    rooms = await load_rooms(hass)
    lookup = lambda action: registry.async_get_entity_id('button', DOMAIN, f'{bid}_{action}')
    health = registry.async_get_entity_id('sensor', DOMAIN, f'{bid}_health')
    outcome = registry.async_get_entity_id('sensor', DOMAIN, f'{bid}_outcome')
    out = build_layout_v4(rooms, data, lookup, health, outcome)
    out['needs_setup'] = needs_setup(data)   # fresh install: the card shows Finish setup to admins
    out['bridge_name'] = await load_bridge_name(hass)
    out['inputs'] = await load_inputs(hass)
    out['ir_receiver_label'] = await load_ir_label(hass)
    config = entry.runtime_data.data.get('config')
    out['volume_steps'] = config.get('volume_steps', 1) if isinstance(config, dict) else 1
    return out


async def settings_payload(hass, entry):
    names = await load_rooms(hass)
    settings = build_settings(names, entry.runtime_data.data)
    return {'rooms': settings, 'setup': setup_state(entry.runtime_data.data, settings), 'bridge_name': await load_bridge_name(hass),
            'inputs': await load_inputs(hass), 'ir_receiver_label': await load_ir_label(hass),
            'ir_target': ir_target(entry.runtime_data.data, names), 'bridge': bridge_health(entry.runtime_data.data),
            'reason': status_reason(entry.runtime_data.data)}


def _bridge_error(error):
    """(websocket code, message). The bridge host's own codes are kept; anything else is bridge_error."""
    if str(error) in BRIDGE_CODES: return str(error), f'The bridge reported {error}; nothing was retried.'
    return 'bridge_error', str(error)


def _room_error(error):
    if error.partial:
        return 'partial_failure', (f'Room D changed partly: the zone was set but live permission was not ({error.code}). '
                                   'Nothing was retried; check Room D before using it.')
    return _bridge_error(error.code)


async def set_room(hass, entry, change, user='unknown'):
    change = validate_room_change(change)
    coordinator, room = entry.runtime_data, change['room']
    ops = room_ops(change)
    try:
        await run_room_ops(coordinator.client.manage, ops)
    except RoomChangeError:
        await coordinator.async_refresh()   # the bridge host's state after a failed change is what the card shows
        raise
    if ops: await coordinator.async_refresh()
    if room == 'd' and 'enabled' in change:
        _LOGGER.info('Room D live permission %s by %s', 'enabled' if change['enabled'] else 'disabled', user)
    store = hass.data[DOMAIN]['store']
    data = await store.async_load()
    rooms = stored_rooms_after(stored_rooms(data), change)
    await store.async_save(stored_after_rooms(data, rooms))
    return {'rooms': build_settings(normalize_rooms(rooms), coordinator.data)}


@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/layout'})
@websocket_api.async_response
async def ws_layout(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    connection.send_result(msg['id'], await layout(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/settings'})
@websocket_api.async_response
async def ws_settings(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_room', vol.Required('room'): str,
                                  vol.Optional('enabled'): bool, vol.Optional('name'): str})
@websocket_api.async_response
async def ws_set_room(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    try:
        result = await set_room(hass, entry, {k: v for k, v in msg.items() if k not in ('id', 'type')}, connection.user.name)
    except ValueError:
        return connection.send_error(msg['id'], 'invalid_request', 'Invalid room change')
    except RoomChangeError as error:
        return connection.send_error(msg['id'], *_room_error(error))
    connection.send_result(msg['id'], result)


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_ir_zone', vol.Required('zone'): str})
@websocket_api.async_response
async def ws_set_ir_zone(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    coordinator = entry.runtime_data
    if msg['zone'] not in ROOM_IDS: return connection.send_error(msg['id'], 'invalid_request', 'Invalid IR target')
    try:
        await coordinator.client.manage('set_ir_zone', zone=msg['zone'])
    except BridgeError as error:
        await coordinator.async_refresh()
        return connection.send_error(msg['id'], *_bridge_error(error))
    await coordinator.async_refresh()
    connection.send_result(msg['id'], {'ir_target': (await layout(hass, entry))['ir_target'],
                                       'message': 'The last-commanded mute state is forgotten for the new target room.'})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_volume_steps', vol.Required('steps'): int})
@websocket_api.async_response
async def ws_set_volume_steps(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    coordinator = entry.runtime_data
    steps = msg['steps']
    if type(steps) is not int or not 1 <= steps <= MAX_VOLUME_STEPS:
        return connection.send_error(msg['id'], 'invalid_request', 'Volume steps must be 1 to %d' % MAX_VOLUME_STEPS)
    try:
        await coordinator.client.manage('set_volume_steps', steps=steps)
    except BridgeError as error:
        await coordinator.async_refresh()
        return connection.send_error(msg['id'], *_bridge_error(error))
    await coordinator.async_refresh()
    connection.send_result(msg['id'], {'volume_steps': (await layout(hass, entry))['volume_steps']})


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_source', vol.Required('source'): str, vol.Required('connected'): bool})
@websocket_api.async_response
async def ws_set_source(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    coordinator = entry.runtime_data
    if msg['source'] not in SOURCES: return connection.send_error(msg['id'], 'invalid_request', 'Invalid input')
    try:
        await coordinator.client.manage('set_source', source=msg['source'], connected=msg['connected'])
    except BridgeError as error:
        await coordinator.async_refresh()
        return connection.send_error(msg['id'], *_bridge_error(error))
    await coordinator.async_refresh()
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_experimental', vol.Required('room'): str, vol.Required('allow'): bool})
@websocket_api.async_response
async def ws_set_experimental(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    coordinator = entry.runtime_data
    if msg['room'] not in ROOM_IDS: return connection.send_error(msg['id'], 'invalid_request', 'Invalid room')
    try:
        await coordinator.client.manage('set_unverified', zone=msg['room'], allow=msg['allow'])
    except BridgeError as error:
        await coordinator.async_refresh()
        return connection.send_error(msg['id'], *_bridge_error(error))
    await coordinator.async_refresh()
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_room_name', vol.Required('room'): str, vol.Required('name'): str})
@websocket_api.async_response
async def ws_set_room_name(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    room = msg['room']
    if room not in ROOM_IDS: return connection.send_error(msg['id'], 'invalid_request', 'Invalid room')
    rooms = await load_rooms(hass)
    try:
        name = clean_room_name(msg['name'], [rooms[other]['name'] for other in ROOM_IDS if other != room])
    except ValueError as error:
        return connection.send_error(msg['id'], str(error), NAME_ERRORS[str(error)])   # nothing is saved
    store = hass.data[DOMAIN]['store']
    data = await store.async_load()
    stored = stored_rooms_after(stored_rooms(data), {'room': room, 'name': name})
    await store.async_save(stored_after_rooms(data, stored))
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_bridge_name', vol.Optional('name'): vol.Any(None, str)})
@websocket_api.async_response
async def ws_set_bridge_name(hass, connection, msg):
    """Nickname for the bridge computer. A missing or null name clears it; a blank one is refused, never saved."""
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    try:
        name = clean_bridge_name(msg.get('name'))
    except ValueError as error:
        return connection.send_error(msg['id'], str(error), NAME_ERRORS[str(error)])   # nothing is saved
    store = hass.data[DOMAIN]['store']
    await store.async_save(stored_after_bridge_name(await store.async_load(), name))
    apply_bridge_name(hass, entry, name)
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_input', vol.Required('source'): str,
                                  vol.Optional('name'): vol.Any(None, str), vol.Optional('icon'): vol.Any(None, str)})
@websocket_api.async_response
async def ws_set_input(hass, connection, msg):
    """Nickname and icon for one input. A field left out keeps its stored value; null means the default for that field."""
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    try:
        change = clean_input_change(msg['source'], {key: msg[key] for key in ('name', 'icon') if key in msg})
    except ValueError as error:
        code = str(error)
        return connection.send_error(msg['id'], code, INPUT_ERRORS[code])   # nothing is saved
    store = hass.data[DOMAIN]['store']
    await store.async_save(stored_after_input(await store.async_load(), msg['source'], change))
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/set_ir_receiver_label', vol.Optional('label'): vol.Any(None, str)})
@websocket_api.async_response
async def ws_set_ir_receiver_label(hass, connection, msg):
    """Where the IR receiver is: a label kept in the HA Store only. A missing or null label clears it; the bridge is never asked."""
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    try:
        label = clean_ir_label(msg.get('label'))
    except ValueError as error:
        return connection.send_error(msg['id'], str(error), NAME_ERRORS[str(error)])   # nothing is saved
    store = hass.data[DOMAIN]['store']
    await store.async_save(stored_after_ir_receiver_label(await store.async_load(), label))
    connection.send_result(msg['id'], await settings_payload(hass, entry))


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required('type'): 'bose_lifestyle/events', vol.Optional('limit', default=50): vol.All(int, vol.Range(min=1, max=50))})
@websocket_api.async_response
async def ws_events(hass, connection, msg):
    entry = loaded_entry(hass)
    if entry is None: return connection.send_error(msg['id'], 'not_ready', 'Bose Lifestyle Controller is not ready')
    try:
        events = await entry.runtime_data.client.events(msg.get('limit', 50))
    except BridgeError as error:
        return connection.send_error(msg['id'], 'bridge_error', str(error))
    connection.send_result(msg['id'], events)


def async_register(hass):
    for command in (ws_layout, ws_settings, ws_set_room, ws_set_room_name, ws_set_bridge_name, ws_set_source, ws_set_ir_zone, ws_set_volume_steps, ws_set_ir_receiver_label, ws_set_experimental, ws_set_input, ws_events):
        websocket_api.async_register_command(hass, command)
