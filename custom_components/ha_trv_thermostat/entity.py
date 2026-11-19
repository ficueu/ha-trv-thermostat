"""Base entity."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from .controller import RoomController


@callback
def async_remove_orphans(
    hass: HomeAssistant, entry: ConfigEntry, domain: str, entities: Iterable[Entity]
) -> None:
    """Drop registry entries of this room/platform that are no longer provided.

    Covers removed TRVs, a removed outdoor sensor and entities replaced in
    newer versions (e.g. room-level offsets -> per-TRV offsets).
    """
    keep = {e.unique_id for e in entities}
    reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(reg, entry.entry_id):
        if reg_entry.domain == domain and reg_entry.unique_id not in keep:
            reg.async_remove(reg_entry.entity_id)


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
