from homeassistant.components.sensor import SensorEntity
from .entity import BoseEntity
from . import group, remote_profiles

async def async_setup_entry(hass,entry,async_add_entities):
    c=entry.runtime_data
    async_add_entities([Status(c,key,name) for key,name in [('health','Bridge health'),('outcome','Last command outcome'),('mute','Last-commanded mute state')]],config_subentry_id=group(entry,'controller').subentry_id)
    for rid in remote_profiles(entry):
        async_add_entities([Status(c,'mappings','Mapping status',rid)],config_subentry_id=group(entry,'remotes').subentry_id)

class Status(BoseEntity,SensorEntity):
    def __init__(self,c,key,name,remote_id=None):
        super().__init__(c,key,name,remote_id); self.key=key
    @property
    def native_value(self):
        data=self.coordinator.data
        if self.key=='mappings':
            if not self.profile: return 'missing'
            return f"{sum(m['code'] is not None for m in self.profile['mappings'].values())}/3 learned; {sum(m['enabled'] for m in self.profile['mappings'].values())}/3 enabled"
        if self.key=='health': return 'serial-open' if self.command_available else 'unavailable'
        if self.key=='mute': return {None:'unknown',True:'muted',False:'unmuted'}[data['ir']['mute_state']]
        outcome=data['bridge']['last_outcome']
        return outcome['outcome'] if outcome else 'none'
    @property
    def extra_state_attributes(self):
        if self.key=='mappings': return {'mappings':self.profile['mappings'] if self.profile else {},'signal_identity':'Identical IR codes cannot identify the physical remote.'}
        if self.key=='outcome': return self.coordinator.data['bridge']['last_outcome'] or {}
        if self.key=='mute': return {'meaning':'Last successful command only; not live Bose feedback.'}
        return {'fault':self.coordinator.data['bridge']['fault'],'config':self.coordinator.data['bridge'].get('config'),
                'configuration':self.coordinator.data['config'],
                'fault_message':self.coordinator.config_entry.options.get('fault_message','')}
