"""Problem sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller import RoomController
from .entity import TrvThermostatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the problem sensor."""
    async_add_entities([ProblemSensor(entry.runtime_data)])


class ProblemSensor(TrvThermostatEntity, BinarySensorEntity):
    """On when something prevents the room from being controlled properly.

    TRV not confirming writes, TRV/offset entity unavailable > 15 min, TRV battery
    low, room sensor lost. Details in the ``problems`` attribute.
    """

    _attr_translation_key = "problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "problem")

    @property
    def is_on(self) -> bool:
        """Any problem."""
        return bool(self._ctrl.problems)

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Problem list."""
        return {"problems": list(self._ctrl.problems)}
