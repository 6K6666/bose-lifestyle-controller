"""Bose Lifestyle Assistant: bridge-authoritative mappings and one serial writer."""
from datetime import timedelta
import json
import logging
import time
from pathlib import Path
from homeassistant.const import Platform
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import device_registry as dr
from .client import Client, BridgeError
from .rooms import DEFAULT_BRIDGE_NAME, stored_bridge_name

DOMAIN = 'bose_lifestyle'
_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON,Platform.SENSOR,Platform.BINARY_SENSOR,Platform.SWITCH,Platform.SELECT]
GROUPS = ('controller', 'zones', 'remotes')

def group(entry, kind):
    return next(sub for sub in entry.subentries.values() if sub.subentry_type == kind)

def remote_profiles(entry):
    return group(entry, 'remotes').data['profiles']

def initial_groups(profiles):
    return [{'subentry_type': kind, 'title': kind.title(), 'unique_id': kind,
             'data': {'profiles': profiles} if kind == 'remotes' else {}} for kind in GROUPS]

async def async_setup(hass,config):
    """Once per start: card file, websocket commands and the settings store, even while the bridge host is down."""
    from homeassistant.helpers.storage import Store
    from .card import async_register_card
    from .websocket import async_register
    hass.data[DOMAIN]={'store':Store(hass,1,f'{DOMAIN}.rooms')}
    try:
        async_register(hass)
        await async_register_card(hass)
    except Exception as error:   # the card is optional; it must never stop Bose control from loading
        _LOGGER.warning('Bose card registration failed: %s',error)
    return True

class Coordinator(DataUpdateCoordinator):
    def __init__(self,hass,entry):
        refresh_debouncer=Debouncer(hass,_LOGGER,cooldown=1.0,immediate=True)
        super().__init__(hass,_LOGGER,name='Bose Lifestyle Assistant',config_entry=entry,
                         update_interval=timedelta(seconds=5),request_refresh_debouncer=refresh_debouncer)
        self.client=Client(entry.data,config_dir=Path(hass.config.config_dir))   # the same directory the pairing flow keys and pins under
        self.read_at=0
        self._fault=None
        self._outcome=...       # unset until the first poll, so an old refusal is not logged at startup
        self._error=None
        self._logged={}         # error text -> monotonic time it was last reported

    async def _log_transitions(self,bridge):
        # Transitions only, never per poll. HA's coordinator itself logs the first failure and the recovery.
        if bridge['fault']!=self._fault:
            if bridge['fault']:
                _LOGGER.error('Bose bridge fault: %s. Control is stopped until it is investigated.',bridge['fault'])
                try: _LOGGER.error('Recent Bose events: %s',json.dumps(await self.client.events(10)))
                except BridgeError as error: _LOGGER.warning('Could not read the bridge events: %s',error)
            else: _LOGGER.info('Bose bridge fault cleared')
            self._fault=bridge['fault']
        outcome=bridge['last_outcome']
        key=(outcome['action'],outcome['observed_at']) if outcome else None
        if self._outcome is not ... and key!=self._outcome and outcome and outcome['outcome']=='refused-by-m1':
            _LOGGER.warning('The Bose control unit refused %s; nothing was retried',outcome['action'])
        self._outcome=key

    async def _async_update_data(self):
        try:
            data=await self.client.call('status')
            if data['bridge_id']!=self.config_entry.unique_id or not -2 <= time.time()-data['generated_at'] <= 15: raise BridgeError('stale_status')
            self.read_at=time.monotonic()
            self._error=None
            await self._log_transitions(data['bridge'])
            return data
        except BridgeError as error:
            now,text,first=time.monotonic(),str(error),self._error is None
            if first or now-self._logged.get(text,-1e9)>=600:   # each distinct error once per 10 minutes; HA itself logs the first failure
                if not first: _LOGGER.warning('Bose bridge error changed: %s',error)
                self._logged[text]=now
            self._error=text
            raise UpdateFailed(str(error)) from error

    @property
    def fresh(self):
        return self.last_update_success and time.monotonic()-self.read_at<15

async def async_setup_entry(hass,entry):
    coordinator=Coordinator(hass,entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data=coordinator
    devices=dr.async_get(hass)
    bridge=devices.async_get_or_create(config_entry_id=entry.entry_id,config_subentry_id=group(entry,'controller').subentry_id,identifiers={(DOMAIN,entry.unique_id)},name='Bose Controller',manufacturer='Bose',model='Controller')
    bridge_name=stored_bridge_name(await hass.data[DOMAIN]['store'].async_load())
    if bridge_name!=DEFAULT_BRIDGE_NAME: devices.async_update_device(bridge.id,name_by_user=bridge_name)   # a stored nickname is put back after the device is recreated
    for room,name in (('a','Zone A'),('b','Zone B'),('c','Zone C'),('d','Zone D')):
        devices.async_get_or_create(config_entry_id=entry.entry_id,config_subentry_id=group(entry,'zones').subentry_id,identifiers={(DOMAIN,f'{entry.unique_id}_room_{room}')},
                                    name=name,manufacturer='Bose',model='Zone',
                                    via_device_id=bridge.id)
    entry.async_on_unload(entry.add_update_listener(async_reload))
    await hass.config_entries.async_forward_entry_setups(entry,PLATFORMS)
    return True

async def async_reload(hass,entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass,entry):
    return await hass.config_entries.async_unload_platforms(entry,PLATFORMS)
