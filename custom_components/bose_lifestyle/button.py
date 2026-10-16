from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from . import DOMAIN, group
from .entity import BoseEntity
from .client import BridgeError
from .commands import SCHEMA4_EXCLUDED, FUNCTION_NAMES, SOURCE_NAMES

def button_name(key, info):
    """Built from the status function and source."""
    if info.get('function') == 'source': return SOURCE_NAMES.get(info.get('source'), key)
    return FUNCTION_NAMES.get(info.get('function'), key)

def wanted_buttons(data):
    """key -> display name for every button this status calls for."""
    return {key: button_name(key, info) for key, info in data['actions'].items() if key not in SCHEMA4_EXCLUDED}

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    # Registry names keep buttons alive while status lists none (config null, actions {}); entities are never removed.
    registry_names = {item.unique_id[len(entry.unique_id) + 1:]: item.original_name
                      for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                      if item.platform == DOMAIN and item.domain == 'button' and item.unique_id.startswith(f'{entry.unique_id}_')
                      and item.unique_id != f'{entry.unique_id}_fault-clear'}
    known = set()
    fault_clear_added = False
    @callback
    def sync():
        nonlocal fault_clear_added
        if coordinator.data is None: return
        wanted = {**registry_names, **wanted_buttons(coordinator.data)}
        new = [Control(coordinator, key, name or key) for key, name in wanted.items() if key not in known]
        known.update(wanted)
        if new: async_add_entities(new,config_subentry_id=group(entry,'zones').subentry_id)
        if not fault_clear_added:
            async_add_entities([FaultClear(coordinator)],config_subentry_id=group(entry,'controller').subentry_id)
            fault_clear_added = True
    sync()
    entry.async_on_unload(coordinator.async_add_listener(sync))

class Control(BoseEntity, ButtonEntity):
    def __init__(self, coordinator, action, name):
        super().__init__(coordinator, action, name)
        self.action = action
    _attr_icon = 'mdi:speaker'
    @property
    def available(self):
        data = self.coordinator.data
        if not data or not BoseEntity.available.fget(self): return False
        bridge = data['bridge']
        info = data['actions'].get(self.action) or {}
        return info.get('allowed') is True and bridge['mode'] == 'live' and bridge['serial_open'] is True and not bridge['fault'] and bridge['config'] == 'ok'
    async def async_press(self):
        bridge = (self.coordinator.data or {}).get('bridge') or {}
        if bridge.get('fault'): raise BridgeError('fault_paused')   # nothing is sent while the bridge is latched
        if bridge.get('config') == 'config-uncertain': raise BridgeError('config_uncertain')
        if not self.available: raise BridgeError('bridge_unavailable')
        try: await self.coordinator.client.call(self.action)
        finally: await self.coordinator.async_request_refresh()

class FaultClear(BoseEntity, ButtonEntity):
    _attr_icon = 'mdi:alert-remove'

    def __init__(self, coordinator):
        super().__init__(coordinator, 'fault-clear', 'Clear fault latch')

    @property
    def available(self):
        data = self.coordinator.data
        if not data or not BoseEntity.available.fget(self): return False
        bridge = data['bridge']
        return bridge['mode'] == 'live' and bool(bridge['fault'])

    async def async_press(self):
        if not self.available: raise BridgeError('bridge_unavailable')
        await self.coordinator.client.call('fault-clear')
        await self.coordinator.async_request_refresh()
