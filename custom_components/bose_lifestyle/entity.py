from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo, async_get
from . import DOMAIN

class BoseEntity(CoordinatorEntity):
    _attr_has_entity_name=True
    def __init__(self,coordinator,key,name,remote_id=None):
        super().__init__(coordinator)
        self.remote_id=remote_id
        self._attr_name=name
        self._attr_unique_id=f'{remote_id or coordinator.config_entry.unique_id}_{key}'
    @property
    def profile(self):
        return self.coordinator.data['ir']['profiles'].get(self.remote_id)
    @property
    def available(self):
        return self.coordinator.fresh and (self.remote_id is None or self.profile is not None)
    @property
    def device_info(self):
        bid=self.coordinator.config_entry.unique_id
        for room,name in (('a','Zone A'),('b','Zone B'),('c','Zone C'),('d','Zone D')):
            if self._attr_unique_id.startswith(f'{bid}_room-{room}-') or (room=='a' and self._attr_unique_id==f'{bid}_volume-down'):
                bridge=async_get(self.hass).async_get_device_by_identifier((DOMAIN,bid),self.coordinator.config_entry.entry_id)
                return DeviceInfo(identifiers={(DOMAIN,f'{bid}_room_{room}')},name=name,
                                  manufacturer='Bose Lifestyle Controller',model='Zone',
                                  via_device_id=bridge.id)
        if self.remote_id:
            profile=self.profile or {}
            return DeviceInfo(identifiers={(DOMAIN,self.remote_id)},name=profile.get('name','Remote'),manufacturer='Bose Lifestyle Controller',model='Remote',via_device_id=async_get(self.hass).async_get_device_by_identifier((DOMAIN,bid),self.coordinator.config_entry.entry_id).id)
        return DeviceInfo(identifiers={(DOMAIN,bid)},name='Bose Controller',manufacturer='Bose Lifestyle Controller',model='Controller')
    @property
    def command_available(self):
        health=self.coordinator.data['bridge']
        return BoseEntity.available.fget(self) and health['serial_open'] and not health['fault'] and health['mode']=='live'
