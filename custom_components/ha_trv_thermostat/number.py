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
    S_AWAY_TEMP,
    S_BOOST_DURATION,
    S_COVER_DEFICIT,
    S_DAY_TEMP,
    S_HYST_OFF,
    S_HYST_ON,
    S_L2_THRESHOLD,
    S_L3_THRESHOLD,
    S_LEVEL_HYST,
    S_LOOKAHEAD,
    S_MIN_HOLD,
    S_NIGHT_TEMP,
    S_OUTDOOR_CUTOFF,
)
from .controller import RoomController
from .entity import TrvThermostatEntity, async_remove_orphans

C = UnitOfTemperature.CELSIUS


def _temp(
    key: str, vmin: float = 5, vmax: float = 30, step: float = 0.5
) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        native_min_value=vmin,
        native_max_value=vmax,
        native_step=step,
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
    _temp(S_AWAY_TEMP),
    _delta(S_HYST_ON, 0, 3),
    _delta(S_HYST_OFF, -1, 3),
    _delta(S_LEVEL_HYST, 0, 2),
    _delta(S_L2_THRESHOLD, 0, 10),
    _delta(S_L3_THRESHOLD, 0, 10),
    NumberEntityDescription(
        key=S_BOOST_DURATION,
        translation_key=S_BOOST_DURATION,
        native_min_value=1,
        native_max_value=180,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
    ),
    _minutes(S_MIN_HOLD, 60),
    _minutes(S_LOOKAHEAD, 60),
)

# Only created when an outdoor sensor is configured.
OUTDOOR_CUTOFF = _temp(S_OUTDOOR_CUTOFF, -10, 30, 0.5)
# Only created when curtains are configured.
COVER_DEFICIT = NumberEntityDescription(
    key=S_COVER_DEFICIT,
    translation_key=S_COVER_DEFICIT,
    native_min_value=0.1,
    native_max_value=5,
    native_step=0.1,
    native_unit_of_measurement=C,
    mode=NumberMode.BOX,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up number entities."""
    ctrl: RoomController = entry.runtime_data
    descriptions = list(NUMBERS)
    if ctrl.outdoor_sensor:
        descriptions.append(OUTDOOR_CUTOFF)
    if ctrl.covers:
        descriptions.append(COVER_DEFICIT)
    entities: list[NumberEntity] = [TrvThermostatNumber(ctrl, desc) for desc in descriptions]
    entities.extend(
        TrvLevelOffsetNumber(ctrl, eid, level) for eid in ctrl.offset_entities for level in LEVELS
    )
    async_remove_orphans(hass, entry, "number", entities)
    async_add_entities(entities)


LEVELS = (0, 1, 2, 3)


class TrvLevelOffsetNumber(TrvThermostatEntity, NumberEntity):
    """Offset of one TRV for one heating level (0 = idle).

    Range and step follow the TRV's own calibration entity when it is known.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = C

    def __init__(self, ctrl: RoomController, offset_entity: str, level: int) -> None:
        """Init."""
        super().__init__(ctrl, f"{ctrl.trv_keys[offset_entity]}_offset_l{level}")
        self._offset_entity = offset_entity
        self._level = level
        self._attr_translation_key = f"trv_offset_l{level}"
        self._attr_translation_placeholders = {"trv": ctrl.trv_names[offset_entity]}

    def _trv_attr(self, key: str, default: float) -> float:
        state = self.hass.states.get(self._offset_entity) if self.hass else None
        try:
            return float(state.attributes[key])  # type: ignore[union-attr]
        except (AttributeError, KeyError, TypeError, ValueError):
            return default

    @property
    def native_min_value(self) -> float:
        """TRV calibration minimum."""
        return self._trv_attr("min", -15.0)

    @property
    def native_max_value(self) -> float:
        """TRV calibration maximum."""
        return self._trv_attr("max", 15.0)

    @property
    def native_step(self) -> float:
        """TRV calibration step."""
        return self._trv_attr("step", 0.1)

    @property
    def native_value(self) -> float:
        """Current value."""
        return self._ctrl.offset_for(self._offset_entity, self._level)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Which TRV entity this offset is written to."""
        return {"offset_entity": self._offset_entity}

    async def async_set_native_value(self, value: float) -> None:
        """Store new value."""
        self._ctrl.async_set_trv_offset(self._offset_entity, self._level, round(float(value), 3))


class TrvThermostatNumber(TrvThermostatEntity, NumberEntity):
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
