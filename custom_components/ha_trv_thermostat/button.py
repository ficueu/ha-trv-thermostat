"""Boost buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller import RoomController
from .entity import TrvThermostatEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up boost buttons."""
    ctrl: RoomController = entry.runtime_data
    async_add_entities([BoostButton(ctrl), BoostStopButton(ctrl)])


class BoostButton(TrvThermostatEntity, ButtonEntity):
    """Start (or restart) boost."""

    _attr_translation_key = "boost"

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "boost")

    async def async_press(self) -> None:
        """Start boost."""
        self._ctrl.async_start_boost()


class BoostStopButton(TrvThermostatEntity, ButtonEntity):
    """End boost early."""

    _attr_translation_key = "boost_stop"

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "boost_stop")

    async def async_press(self) -> None:
        """Stop boost."""
        self._ctrl.async_stop_boost()
