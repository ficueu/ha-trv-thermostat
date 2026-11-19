"""Away mode switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller import RoomController
from .entity import TrvThermostatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the away switch."""
    async_add_entities([AwaySwitch(entry.runtime_data)])


class AwaySwitch(TrvThermostatEntity, SwitchEntity):
    """Away: target = away temperature, regardless of schedule/preset/override."""

    _attr_translation_key = "away"

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "away")

    @property
    def is_on(self) -> bool:
        """Away active."""
        return self._ctrl.away

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable away."""
        self._ctrl.async_set_away(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable away."""
        self._ctrl.async_set_away(False)
