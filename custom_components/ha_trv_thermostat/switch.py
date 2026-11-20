"""Away mode switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller import RoomController
from .entity import TrvThermostatEntity, async_remove_orphans


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switches (curtain control only when covers are configured)."""
    ctrl: RoomController = entry.runtime_data
    entities: list[SwitchEntity] = [AwaySwitch(ctrl)]
    if ctrl.covers:
        entities.append(CoversSwitch(ctrl))
    async_remove_orphans(hass, entry, "switch", entities)
    async_add_entities(entities)


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


class CoversSwitch(TrvThermostatEntity, SwitchEntity):
    """Curtain control on/off. Turning it off closes curtains it opened."""

    _attr_translation_key = "covers"

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "covers")

    @property
    def is_on(self) -> bool:
        """Curtain control active."""
        return self._ctrl.covers_enabled

    @property
    def extra_state_attributes(self) -> dict[str, list[str]]:
        """Curtains currently opened by the integration."""
        return {"opened_by_integration": sorted(self._ctrl.covers_owned)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable."""
        self._ctrl.async_set_covers_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable."""
        self._ctrl.async_set_covers_enabled(False)
