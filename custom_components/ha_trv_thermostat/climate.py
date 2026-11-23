"""Climate entity - one per room."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import PRESET_SCHEDULE
from .controller import TEMP_MAX, TEMP_MIN, RoomController
from .entity import TrvThermostatEntity
from .logic import Status


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the climate entity."""
    async_add_entities([TrvThermostatClimate(entry.runtime_data)])


class TrvThermostatClimate(TrvThermostatEntity, ClimateEntity):
    """Room thermostat driving TRVs via calibration offset."""

    _attr_name = None
    _attr_translation_key = "room"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_SCHEDULE, PRESET_COMFORT, PRESET_ECO, PRESET_AWAY]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = TEMP_MIN
    _attr_max_temp = TEMP_MAX
    _attr_target_temperature_step = 0.1
    _unrecorded_attributes = frozenset({"reason", "trend", "deficit"})

    def __init__(self, ctrl: RoomController) -> None:
        """Init."""
        super().__init__(ctrl, "climate")

    @property
    def current_temperature(self) -> float | None:
        """Room temperature (external sensor)."""
        return self._ctrl.room_temp

    @property
    def target_temperature(self) -> float | None:
        """Active target."""
        return self._ctrl.target

    @property
    def hvac_mode(self) -> HVACMode:
        """HEAT or OFF."""
        return self._ctrl.hvac_mode

    @property
    def hvac_action(self) -> HVACAction | None:
        """Heating when level > 0."""
        d = self._ctrl.decision
        if d is not None and d.status == Status.BOOST:
            return HVACAction.HEATING
        if self._ctrl.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        if d is None:
            return None
        if d.status == Status.HEATING:
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str:
        """Preset."""
        return PRESET_AWAY if self._ctrl.away else self._ctrl.preset

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostics."""
        c = self._ctrl
        d = c.decision
        return {
            "status": d.status.value if d else None,
            "level": d.level if d else None,
            "offsets": c.commanded_offsets,
            "target_source": c.target_source,
            "override_until": c.override_until.isoformat() if c.override_until else None,
            "window_open": c.window_open,
            "away": c.away,
            "boost_until": c.boost_until.isoformat() if c.boost_until else None,
            "covers_opened": sorted(c.covers_owned),
            "outdoor_temperature": c.outdoor_temp,
            "outdoor_warm": c.outdoor_warm,
            "last_level_change": c.last_level_change.isoformat() if c.last_level_change else None,
            "deficit": round(d.deficit, 2) if d and d.deficit is not None else None,
            "trend": round(c.slope, 2) if c.slope is not None else None,
            "reason": d.reason if d else None,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HEAT/OFF."""
        self._ctrl.async_set_hvac_mode(hvac_mode)

    async def async_turn_on(self) -> None:
        """Turn on."""
        self._ctrl.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn off."""
        self._ctrl.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset."""
        self._ctrl.async_set_preset(preset_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target (override in schedule preset)."""
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            self._ctrl.async_set_hvac_mode(HVACMode(mode))
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._ctrl.async_set_target(float(temp))
