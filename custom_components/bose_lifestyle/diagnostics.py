"""Download diagnostics: status snapshot, room settings and recent bridge events. No keys, hosts or IR codes."""
import copy

from homeassistant.components.diagnostics import async_redact_data

from .client import BridgeError
from .websocket import load_rooms
from .rooms import build_settings


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    status = copy.deepcopy(coordinator.data)
    status['ir']['profiles'] = {pid: {k: p[k] for k in ('name', 'model', 'active')} for pid, p in status['ir']['profiles'].items()}
    try: events = await coordinator.client.events(50)
    except BridgeError: events = []
    return {'entry': async_redact_data(dict(entry.data), {'host', 'user', 'port'}), 'status': status,
            'rooms': build_settings(await load_rooms(hass), coordinator.data), 'events': events}
