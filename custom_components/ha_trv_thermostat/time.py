"""Time entities: start of day / start of night."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import S_DAY_START, S_NIGHT_START, parse_time
from .controller import RoomController
from .entity import TrvThermostatEntity

TIMES = (
    TimeEntityDescription(key=S_DAY_START, translation_key=S_DAY_START),
    TimeEntityDescription(key=S_NIGHT_START, translation_key=S_NIGHT_START),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up time entities."""
    ctrl: RoomController = entry.runtime_data
    async_add_entities(TrvThermostatTime(ctrl, desc) for desc in TIMES)


class TrvThermostatTime(TrvThermostatEntity, TimeEntity):
    """Schedule boundary."""

    def __init__(self, ctrl: RoomController, description: TimeEntityDescription) -> None:
        """Init."""
        super().__init__(ctrl, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> time:
        """Current value."""
        return parse_time(self._ctrl.settings[self.entity_description.key])

    async def async_set_value(self, value: time) -> None:
        """Store new value."""
        self._ctrl.async_set_setting(self.entity_description.key, value.isoformat())
