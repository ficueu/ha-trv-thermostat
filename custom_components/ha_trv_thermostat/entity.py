"""Base entity."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from .controller import RoomController


class TrvThermostatEntity(Entity):
    """Entity bound to a RoomController."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, ctrl: RoomController, key: str) -> None:
        """Init."""
        self._ctrl = ctrl
        self._attr_unique_id = f"{ctrl.entry.entry_id}_{key}"
        self._attr_device_info = ctrl.device_info

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates."""
        self.async_on_remove(self._ctrl.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
