from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from .entity import BoseEntity
from . import group

async def async_setup_entry(hass,entry,async_add_entities):
    async_add_entities([Availability(entry.runtime_data,key,name) for key,name in [('serial','Serial available'),('receiver','IR receiver available')]]+[BridgeFault(entry.runtime_data,'fault','Bridge fault')],config_subentry_id=group(entry,'controller').subentry_id)
class Availability(BoseEntity,BinarySensorEntity):
    _attr_device_class=BinarySensorDeviceClass.CONNECTIVITY
    def __init__(self,c,key,name): super().__init__(c,key,name); self.key=key
    @property
    def is_on(self): return self.coordinator.data['bridge']['serial_open' if self.key=='serial' else 'ir_attached']
class BridgeFault(BoseEntity,BinarySensorEntity):
    _attr_device_class=BinarySensorDeviceClass.PROBLEM
    @property
    def is_on(self): return bool(self.coordinator.data['bridge']['fault'])
    @property
    def extra_state_attributes(self): return {'fault':self.coordinator.data['bridge']['fault']}
