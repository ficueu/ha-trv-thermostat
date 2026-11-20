"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_ADOPT_TRV_SETPOINT,
    CONF_COVER_DAYLIGHT_ONLY,
    CONF_COVERS,
    CONF_FALLBACK_OFFSET,
    CONF_OUTDOOR_HYST,
    CONF_OUTDOOR_SENSOR,
    CONF_OFFSETS,
    CONF_SENSOR_TIMEOUT,
    CONF_TEMP_SENSOR,
    CONF_TRVS,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOWS,
    DEFAULT_ADOPT_TRV_SETPOINT,
    DEFAULT_COVER_DAYLIGHT_ONLY,
    DEFAULT_FALLBACK_OFFSET,
    DEFAULT_OUTDOOR_HYST,
    DEFAULT_SENSOR_TIMEOUT,
    DEFAULT_WINDOW_CLOSE_DELAY,
    DEFAULT_WINDOW_OPEN_DELAY,
    DOMAIN,
)

# Specific tokens only - room/device names end up in entity_ids too.
_EXCLUDE = ("external_temperature", "valve_", "humidity", "boost_", "window_")


def find_offset_entities(hass: HomeAssistant, trvs: list[str]) -> list[str]:
    """Find the calibration/offset number entity on each TRV's device.

    Z2M:  number.<trv>_local_temperature_calibration
    ZHA:  number.<trv>_local_temperature_offset (translation_key ...calibration)
    """
    reg = er.async_get(hass)
    found: list[str] = []
    for trv in trvs:
        ent = reg.async_get(trv)
        if ent is None or ent.device_id is None:
            continue
        best, best_score = None, 0
        for cand in er.async_entries_for_device(reg, ent.device_id):
            if cand.domain != "number":
                continue
            hay = (
                " ".join(
                    str(x)
                    for x in (
                        cand.entity_id,
                        cand.translation_key,
                        cand.original_name,
                        cand.unique_id,
                    )
                    if x
                )
                .lower()
                .replace(" ", "_")
            )
            if any(x in hay for x in _EXCLUDE):
                continue
            if "local_temperature_calibration" in hay:
                score = 4
            elif "temperature_calibration" in hay or "local_temperature_offset" in hay:
                score = 3
            elif "calibration" in hay:
                score = 2
            elif "offset" in hay and "temp" in hay:
                score = 1
            else:
                score = 0
            if score > best_score:
                best, best_score = cand.entity_id, score
        if best and best not in found:
            found.append(best)
    return found


def _sensor_selector() -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=SensorDeviceClass.TEMPERATURE)
    )


def _outdoor_selector() -> EntitySelector:
    # A temperature sensor or a weather entity (its "temperature" attribute).
    return EntitySelector(
        EntitySelectorConfig(
            filter=[
                {"domain": "sensor", "device_class": SensorDeviceClass.TEMPERATURE},
                {"domain": "weather"},
            ]
        )
    )


def _multi(domain: str) -> EntitySelector:
    return EntitySelector(EntitySelectorConfig(domain=domain, multiple=True))


def _num(vmin: float, vmax: float, step: float, unit: str | None = None) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=vmin, max=vmax, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


class TrvThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a room."""

    VERSION = 1

    def __init__(self) -> None:
        """Init."""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Room name, sensor, TRVs."""
        if user_input is not None:
            trvs = sorted(user_input[CONF_TRVS])
            await self.async_set_unique_id("|".join(trvs))
            self._abort_if_unique_id_configured()
            self._data = dict(user_input)
            return await self.async_step_offsets()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_TEMP_SENSOR): _sensor_selector(),
                vol.Required(CONF_TRVS): _multi("climate"),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_offsets(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offset entities (auto-detected) and optional window sensors."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_OFFSETS):
                errors[CONF_OFFSETS] = "no_offsets"
            else:
                data = {
                    CONF_TEMP_SENSOR: self._data[CONF_TEMP_SENSOR],
                    CONF_TRVS: self._data[CONF_TRVS],
                    CONF_OFFSETS: user_input[CONF_OFFSETS],
                    CONF_WINDOWS: user_input.get(CONF_WINDOWS, []),
                    CONF_OUTDOOR_SENSOR: user_input.get(CONF_OUTDOOR_SENSOR),
                    CONF_COVERS: user_input.get(CONF_COVERS, []),
                }
                return self.async_create_entry(title=self._data[CONF_NAME], data=data)

        detected = find_offset_entities(self.hass, self._data[CONF_TRVS])
        schema = vol.Schema(
            {
                vol.Required(CONF_OFFSETS, default=detected): _multi("number"),
                vol.Optional(CONF_WINDOWS, default=[]): _multi("binary_sensor"),
                vol.Optional(CONF_OUTDOOR_SENSOR): _outdoor_selector(),
                vol.Optional(CONF_COVERS, default=[]): _multi("cover"),
            }
        )
        return self.async_show_form(
            step_id="offsets",
            data_schema=schema,
            errors=errors,
            description_placeholders={"detected": ", ".join(detected) or "-"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options."""
        return TrvThermostatOptionsFlow()


class TrvThermostatOptionsFlow(OptionsFlow):
    """Change entities and advanced options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Single options page."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_OFFSETS):
                errors[CONF_OFFSETS] = "no_offsets"
            else:
                # A cleared optional selector is simply missing - store None so it
                # overrides a value from the initial config entry data.
                user_input.setdefault(CONF_OUTDOOR_SENSOR, None)
                return self.async_create_entry(data=user_input)

        cfg = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_TEMP_SENSOR, default=cfg.get(CONF_TEMP_SENSOR)
                ): _sensor_selector(),
                vol.Required(CONF_TRVS, default=cfg.get(CONF_TRVS, [])): _multi("climate"),
                vol.Required(CONF_OFFSETS, default=cfg.get(CONF_OFFSETS, [])): _multi("number"),
                vol.Optional(CONF_WINDOWS, default=cfg.get(CONF_WINDOWS, [])): _multi(
                    "binary_sensor"
                ),
                vol.Optional(
                    CONF_OUTDOOR_SENSOR,
                    description={"suggested_value": cfg.get(CONF_OUTDOOR_SENSOR)},
                ): _outdoor_selector(),
                vol.Required(
                    CONF_OUTDOOR_HYST,
                    default=cfg.get(CONF_OUTDOOR_HYST, DEFAULT_OUTDOOR_HYST),
                ): _num(0, 10, 0.1, "°C"),
                vol.Optional(CONF_COVERS, default=cfg.get(CONF_COVERS, [])): _multi("cover"),
                vol.Required(
                    CONF_COVER_DAYLIGHT_ONLY,
                    default=cfg.get(CONF_COVER_DAYLIGHT_ONLY, DEFAULT_COVER_DAYLIGHT_ONLY),
                ): BooleanSelector(),
                vol.Required(
                    CONF_WINDOW_OPEN_DELAY,
                    default=cfg.get(CONF_WINDOW_OPEN_DELAY, DEFAULT_WINDOW_OPEN_DELAY),
                ): _num(0, 3600, 1, "s"),
                vol.Required(
                    CONF_WINDOW_CLOSE_DELAY,
                    default=cfg.get(CONF_WINDOW_CLOSE_DELAY, DEFAULT_WINDOW_CLOSE_DELAY),
                ): _num(0, 3600, 1, "s"),
                vol.Required(
                    CONF_SENSOR_TIMEOUT,
                    default=cfg.get(CONF_SENSOR_TIMEOUT, DEFAULT_SENSOR_TIMEOUT),
                ): _num(5, 1440, 1, "min"),
                vol.Required(
                    CONF_FALLBACK_OFFSET,
                    default=cfg.get(CONF_FALLBACK_OFFSET, DEFAULT_FALLBACK_OFFSET),
                ): _num(-15, 15, 0.1, "°C"),
                vol.Required(
                    CONF_ADOPT_TRV_SETPOINT,
                    default=cfg.get(CONF_ADOPT_TRV_SETPOINT, DEFAULT_ADOPT_TRV_SETPOINT),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
