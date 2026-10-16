from homeassistant.components.switch import SwitchEntity
from .entity import BoseEntity
from . import group, remote_profiles

async def async_setup_entry(hass,entry,async_add_entities):
    for rid in remote_profiles(entry):
        async_add_entities([Enabled(entry.runtime_data,'enabled','Enabled',rid)],config_subentry_id=group(entry,'remotes').subentry_id)
class Enabled(BoseEntity,SwitchEntity):
    @property
    def is_on(self): return bool(self.profile and self.profile['active'])
    async def async_turn_on(self,**kwargs): await self.set_enabled(True)
    async def async_turn_off(self,**kwargs): await self.set_enabled(False)
    async def set_enabled(self,value):
        await self.coordinator.client.manage('update',remote_id=self.remote_id,active=value)
        await self.coordinator.async_request_refresh()
