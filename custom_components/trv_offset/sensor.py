"""Diagnostic sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .controller import RoomController
from .entity import TrvOffsetEntity
from .logic import Status


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors."""
    ctrl: RoomController = entry.runtime_data
    async_add_entities(
        [LevelSensor(ctrl), StatusSensor(ctrl), OffsetSensor(ctrl), WritesSensor(ctrl)]
    )


class LevelSensor(TrvOffsetEntity, SensorEntity):
    """Current heating level (0 = not heating)."""

    _attr_translation_key = "level"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "level")

    @property
    def native_value(self) -> int | None:
        """Level."""
        return self._ctrl.decision.level if self._ctrl.decision else None


class StatusSensor(TrvOffsetEntity, SensorEntity):
    """Controller status."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in Status]

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "status")

    @property
    def native_value(self) -> str | None:
        """Status."""
        return self._ctrl.decision.status.value if self._ctrl.decision else None


class OffsetSensor(TrvOffsetEntity, SensorEntity):
    """Offset currently commanded to the TRVs."""

    _attr_translation_key = "offset"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "offset")

    @property
    def native_value(self) -> float | None:
        """Offset."""
        return self._ctrl.commanded_offset


class WritesSensor(TrvOffsetEntity, SensorEntity):
    """Number of Zigbee writes since HA start - to prove the network stays quiet."""

    _attr_translation_key = "writes"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "writes")

    @property
    def native_value(self) -> int:
        """Writes."""
        return self._ctrl.writes
