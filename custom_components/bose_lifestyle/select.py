from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory
from .entity import BoseEntity
from . import group

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([Resync(entry.runtime_data,'resync','Resync last-commanded mute state')],config_subentry_id=group(entry,'controller').subentry_id)
class Resync(BoseEntity,SelectEntity):
    _attr_options=['unknown','muted','unmuted']
    _attr_entity_category=EntityCategory.CONFIG
    @property
    def current_option(self): return {None:'unknown',True:'muted',False:'unmuted'}[self.coordinator.data['ir']['mute_state']]
    async def async_select_option(self,option):
        await self.coordinator.client.manage('resync',muted={'unknown':None,'muted':True,'unmuted':False}[option])
        await self.coordinator.async_request_refresh()
