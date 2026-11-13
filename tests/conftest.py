"""Fixtures: fake Zigbee TRVs that behave like real entities."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.number import NumberEntity
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import setup_test_component_platform

import custom_components  # noqa: F401  # import first so this repo's package wins over the harness one


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
def no_write_spacing(monkeypatch):
    """No real sleeps between writes in tests."""
    monkeypatch.setattr("custom_components.trv_offset.controller.WRITE_SPACING_S", 0)


class FakeTRV(ClimateEntity):
    """Zigbee TRV stand-in. ``respond=False`` simulates lost writes."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(self, name: str, modes: list[HVACMode] | None = None) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_target_temperature = 21.0
        self._attr_current_temperature = 21.0
        if modes is not None:
            self._attr_hvac_modes = modes
        self.respond = True
        self.calls: list[tuple[str, Any]] = []

    async def async_set_temperature(self, **kwargs: Any) -> None:
        self.calls.append(("temperature", kwargs[ATTR_TEMPERATURE]))
        if self.respond:
            self._attr_target_temperature = kwargs[ATTR_TEMPERATURE]
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self.calls.append(("mode", hvac_mode))
        if self.respond:
            self._attr_hvac_mode = hvac_mode
            self.async_write_ha_state()

    def knob(self, temp: float) -> None:
        """Somebody turns the knob on the TRV."""
        self._attr_target_temperature = temp
        self.async_write_ha_state()


class FakeOffset(NumberEntity):
    """local_temperature_calibration stand-in."""

    _attr_should_poll = False
    _attr_native_min_value = -12.8
    _attr_native_max_value = 12.7
    _attr_native_step = 0.1

    def __init__(self, name: str) -> None:
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_native_value = 0.0
        self.respond = True
        self.calls: list[float] = []

    async def async_set_native_value(self, value: float) -> None:
        self.calls.append(value)
        if self.respond:
            self._attr_native_value = value
            self.async_write_ha_state()


@pytest.fixture
async def fake_devices(hass: HomeAssistant):
    """Two TRVs (one without 'off' mode) + their offset numbers."""
    await hass.config.async_set_time_zone("Europe/Warsaw")
    trv1 = FakeTRV("TRV 1")
    trv2 = FakeTRV("TRV 2", modes=[HVACMode.HEAT])  # no OFF -> min_temp emulation
    off1 = FakeOffset("TRV 1 calibration")
    off2 = FakeOffset("TRV 2 calibration")
    setup_test_component_platform(hass, "climate", [trv1, trv2])
    setup_test_component_platform(hass, "number", [off1, off2])
    assert await async_setup_component(hass, "climate", {"climate": {"platform": "test"}})
    assert await async_setup_component(hass, "number", {"number": {"platform": "test"}})
    hass.states.async_set(
        "sensor.salon_temp", "20.5", {"device_class": "temperature", "unit_of_measurement": "°C"}
    )
    hass.states.async_set("binary_sensor.salon_okno", "off", {"device_class": "window"})
    await hass.async_block_till_done()
    return {"trv1": trv1, "trv2": trv2, "off1": off1, "off2": off2}
