"""Number entities for runtime settings."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    S_DAY_TEMP,
    S_HYST_OFF,
    S_HYST_ON,
    S_IDLE_OFFSET,
    S_L1_OFFSET,
    S_L2_OFFSET,
    S_L2_THRESHOLD,
    S_L3_OFFSET,
    S_L3_THRESHOLD,
    S_LEVEL_HYST,
    S_LOOKAHEAD,
    S_MIN_HOLD,
    S_NIGHT_TEMP,
)
from .controller import RoomController
from .entity import TrvOffsetEntity

C = UnitOfTemperature.CELSIUS


def _temp(key: str) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        native_min_value=5,
        native_max_value=30,
        native_step=0.1,
        native_unit_of_measurement=C,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.BOX,
    )


def _delta(key: str, vmin: float, vmax: float, step: float = 0.05) -> NumberEntityDescription:
    # Deltas/offsets: no temperature device_class (it would be unit-converted
    # as an absolute temperature).
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        native_min_value=vmin,
        native_max_value=vmax,
        native_step=step,
        native_unit_of_measurement=C,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


def _minutes(key: str, vmax: float) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        native_min_value=0,
        native_max_value=vmax,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    )


NUMBERS: tuple[NumberEntityDescription, ...] = (
    _temp(S_DAY_TEMP),
    _temp(S_NIGHT_TEMP),
    _delta(S_HYST_ON, 0, 3),
    _delta(S_HYST_OFF, -1, 3),
    _delta(S_LEVEL_HYST, 0, 2),
    _delta(S_IDLE_OFFSET, -15, 15, 0.1),
    _delta(S_L1_OFFSET, -15, 15, 0.1),
    _delta(S_L2_OFFSET, -15, 15, 0.1),
    _delta(S_L2_THRESHOLD, 0, 10),
    _delta(S_L3_OFFSET, -15, 15, 0.1),
    _delta(S_L3_THRESHOLD, 0, 10),
    _minutes(S_MIN_HOLD, 60),
    _minutes(S_LOOKAHEAD, 60),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up number entities."""
    ctrl: RoomController = entry.runtime_data
    async_add_entities(TrvOffsetNumber(ctrl, desc) for desc in NUMBERS)


class TrvOffsetNumber(TrvOffsetEntity, NumberEntity):
    """A runtime setting."""

    def __init__(self, ctrl: RoomController, description: NumberEntityDescription) -> None:
        """Init."""
        super().__init__(ctrl, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float:
        """Current value."""
        return float(self._ctrl.settings[self.entity_description.key])

    async def async_set_native_value(self, value: float) -> None:
        """Store new value."""
        self._ctrl.async_set_setting(self.entity_description.key, round(float(value), 3))
