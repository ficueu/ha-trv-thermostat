"""End-to-end tests in a real Home Assistant test instance."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

DOMAIN = "ha_trv_thermostat"
ENTRY_DATA = {
    "temperature_sensor": "sensor.salon_temp",
    "trvs": ["climate.trv_1", "climate.trv_2"],
    "offset_entities": ["number.trv_1_calibration", "number.trv_2_calibration"],
    "window_sensors": ["binary_sensor.salon_okno"],
}


async def settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def advance(
    hass: HomeAssistant, freezer, seconds: float, step: float = 30, heartbeat: bool = True
) -> None:
    """Move time forward. ``heartbeat`` re-reports the room sensor's current value
    (no state change, only last_reported) like a real Zigbee sensor does."""
    left = seconds
    while left > 0:
        d = min(step, left)
        freezer.tick(timedelta(seconds=d))
        if heartbeat and (st := hass.states.get("sensor.salon_temp")) and st.state != "unavailable":
            hass.states.async_set(st.entity_id, st.state, st.attributes)
        async_fire_time_changed(hass)
        await settle(hass)
        left -= d


async def set_temp(hass: HomeAssistant, value) -> None:
    hass.states.async_set(
        "sensor.salon_temp",
        str(value),
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    await settle(hass)


async def setup_room(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Salon", data=ENTRY_DATA, options=options, unique_id="salon"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return entry


def our_entity(hass: HomeAssistant, entry, domain: str, key: str) -> str:
    eid = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}")
    assert eid, key
    return eid


def trv_key(hass: HomeAssistant, offset_entity: str) -> str:
    return er.async_get(hass).async_get(offset_entity).id


def trv_entity(hass: HomeAssistant, entry, domain: str, offset_entity: str, key: str) -> str:
    return our_entity(hass, entry, domain, f"{trv_key(hass, offset_entity)}_{key}")


async def test_full_day(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Walk through a realistic sequence and count every Zigbee write."""
    trv1, trv2, off1, off2 = (fake_devices[k] for k in ("trv1", "trv2", "off1", "off2"))
    freezer.move_to("2026-11-09 11:00:00+00:00")  # 12:00 Warsaw -> day, target 21

    entry = await setup_room(hass)
    ctrl = entry.runtime_data

    # 20.5 -> deficit 0.5 >= 0.3 -> level 1 -> offset -2. Setpoint/mode already ok.
    assert off1.calls == [-2.0] and off2.calls == [-2.0]
    assert trv1.calls == [] and trv2.calls == []
    state = hass.states.get("climate.salon")
    assert state.attributes["hvac_action"] == HVACAction.HEATING
    assert state.attributes["level"] == 1
    assert state.attributes["current_temperature"] == 20.5
    assert state.attributes["temperature"] == 21.0
    assert ctrl.writes == 2

    # Noise / slow rise inside the band: no writes at all.
    for t in (20.6, 20.5, 20.7, 20.8, 20.9, 21.0):
        await set_temp(hass, t)
        await advance(hass, freezer, 30)
    assert off1.calls == [-2.0]

    # Target + hyst_off reached at ~3 min -> held until 5 min have passed.
    await set_temp(hass, 21.1)
    assert hass.states.get("climate.salon").attributes["level"] == 1
    await advance(hass, freezer, 150)
    assert off1.calls == [-2.0, 3.0] and off2.calls == [-2.0, 3.0]
    assert hass.states.get("climate.salon").attributes["hvac_action"] == HVACAction.IDLE

    # 1 h of ticks with a stable temperature -> zero writes.
    writes = ctrl.writes
    await advance(hass, freezer, 3600, step=60)
    await set_temp(hass, 21.0)
    await set_temp(hass, 20.8)  # deficit 0.2 < 0.3 -> still idle
    assert ctrl.writes == writes

    # Drops further -> level 1, then deeper -> level 2 (after hold).
    await set_temp(hass, 20.7)
    assert off1.calls[-1] == -2.0
    await set_temp(hass, 20.2)  # deficit 0.8 > 0.6
    assert off1.calls[-1] == -2.0  # held
    await advance(hass, freezer, 300)
    assert off1.calls[-1] == -5.0
    assert hass.states.get("sensor.salon_heating_level").state == "2"

    # Level hysteresis: 0.5 deficit stays at 2, 0.35 drops to 1.
    await set_temp(hass, 20.5)
    await advance(hass, freezer, 300)
    assert off1.calls[-1] == -5.0
    await set_temp(hass, 20.65)
    assert off1.calls[-1] == -2.0

    # TRV 1 stops confirming writes -> 3 quick retries, then back-off.
    await advance(hass, freezer, 300)
    off1.respond = False
    await set_temp(hass, 21.2)
    assert off1.calls[-1] == 3.0 and off2.calls[-1] == 3.0
    n = len(off1.calls)
    await advance(hass, freezer, 60)
    assert len(off1.calls) == n  # waiting for confirmation (90 s)
    await advance(hass, freezer, 240)
    assert len(off1.calls) == n + 2  # attempts 2 and 3
    await advance(hass, freezer, 1200, step=60)
    assert len(off1.calls) == n + 2  # backing off
    off1.respond = True
    await advance(hass, freezer, 600, step=60)
    assert len(off1.calls) == n + 3 and off1.native_value == 3.0
    await advance(hass, freezer, 3600, step=120)
    assert len(off1.calls) == n + 3  # confirmed -> quiet again

    # Window: open 60 s -> TRV1 off, TRV2 (no off mode) -> min_temp.
    hass.states.async_set("binary_sensor.salon_okno", "on")
    await settle(hass)
    await advance(hass, freezer, 30)
    assert trv1.hvac_mode == HVACMode.HEAT
    await advance(hass, freezer, 60)
    assert trv1.hvac_mode == HVACMode.OFF
    assert trv2.target_temperature == 5
    assert hass.states.get("sensor.salon_status").state == "window_open"
    hass.states.async_set("binary_sensor.salon_okno", "off")
    await settle(hass)
    await advance(hass, freezer, 150)
    assert trv1.hvac_mode == HVACMode.HEAT
    assert trv2.target_temperature == 21.0
    assert trv1.target_temperature == 21.0

    # Night: 22:00 Warsaw -> target 19 -> setpoint 19 written to both TRVs.
    freezer.move_to("2026-11-09 21:00:30+00:00")
    async_fire_time_changed(hass)
    await settle(hass)
    await advance(hass, freezer, 60)
    assert trv1.target_temperature == 19.0 and trv2.target_temperature == 19.0
    assert hass.states.get("climate.salon").attributes["target_source"] == "night"

    # Someone turns the knob on TRV 1 -> override 22 until 06:00, TRV 2 follows.
    await advance(hass, freezer, 300)
    trv1.knob(22.0)
    await settle(hass)
    state = hass.states.get("climate.salon")
    assert state.attributes["temperature"] == 22.0
    assert state.attributes["target_source"] == "override"
    assert state.attributes["override_until"].startswith("2026-11-10T06:00")
    assert trv2.target_temperature == 22.0
    assert trv1.calls.count(("temperature", 22.0)) == 0  # not echoed back

    # Short availability blip of the room sensor -> ignored, no writes.
    writes = ctrl.writes
    hass.states.async_set("sensor.salon_temp", "unavailable")
    await settle(hass)
    await advance(hass, freezer, 240)
    hass.states.async_set("sensor.salon_temp", "21.2")
    await settle(hass)
    await advance(hass, freezer, 60)
    assert ctrl.writes == writes
    assert hass.states.get("sensor.salon_status").state != "sensor_lost"

    # Room sensor really lost (10 min) -> fallback offset 0, TRVs regulate themselves.
    hass.states.async_set("sensor.salon_temp", "unavailable")
    await settle(hass)
    await advance(hass, freezer, 540)
    assert hass.states.get("sensor.salon_status").state != "sensor_lost"
    await advance(hass, freezer, 90)
    assert hass.states.get("sensor.salon_status").state == "sensor_lost"
    assert off1.native_value == 0.0 and off2.native_value == 0.0


async def test_sensor_timeout(hass: HomeAssistant, fake_devices, freezer) -> None:
    """No report from the room sensor for > timeout -> fallback."""
    freezer.move_to("2026-11-09 11:00:00+00:00")
    await set_temp(hass, 20.5)  # report at frozen "now"
    await setup_room(hass, sensor_timeout=30)
    assert hass.states.get("sensor.salon_status").state == "heating"
    await advance(hass, freezer, 25 * 60, step=60, heartbeat=False)
    assert hass.states.get("sensor.salon_status").state == "heating"
    await advance(hass, freezer, 6 * 60, step=60, heartbeat=False)
    assert hass.states.get("sensor.salon_status").state == "sensor_lost"
    assert fake_devices["off1"].native_value == 0.0
    await set_temp(hass, 20.4)
    assert hass.states.get("sensor.salon_status").state == "heating"


async def test_settings_entities_and_off(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Number/time entities change behaviour; HVAC off turns TRVs off."""
    freezer.move_to("2026-11-09 11:00:00+00:00")
    entry = await setup_room(hass)
    off1, trv1 = fake_devices["off1"], fake_devices["trv1"]
    assert off1.calls == [-2.0]

    # level 1 offset -2 -> -3 is pushed immediately (same level, new value)
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": trv_entity(hass, entry, "number", "number.trv_1_calibration", "offset_l1"),
            "value": -3,
        },
        blocking=True,
    )
    await settle(hass)
    assert off1.calls[-1] == -3.0

    # day temp 20 -> 20.5 is above target -> idle offset
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": our_entity(hass, entry, "number", "day_temp"), "value": 20.0},
        blocking=True,
    )
    await settle(hass)
    await advance(hass, freezer, 300)
    assert off1.calls[-1] == 3.0
    assert trv1.target_temperature == 20.0

    # night starts now (12:00) -> night temp 19
    await hass.services.async_call(
        "time",
        "set_value",
        {"entity_id": our_entity(hass, entry, "time", "night_start"), "time": "11:59:00"},
        blocking=True,
    )
    await settle(hass)
    assert trv1.target_temperature == 19.0

    # preset comfort forces day temp regardless of schedule
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": "climate.salon", "preset_mode": "comfort"},
        blocking=True,
    )
    await settle(hass)
    assert trv1.target_temperature == 20.0

    # card +/- in comfort preset edits the day temperature
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.salon", "temperature": 22.0},
        blocking=True,
    )
    await settle(hass)
    assert entry.runtime_data.settings["day_temp"] == 22.0

    # off -> TRV off, offsets untouched
    n = len(off1.calls)
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "off"},
        blocking=True,
    )
    await settle(hass)
    assert trv1.hvac_mode == HVACMode.OFF
    assert len(off1.calls) == n
    assert hass.states.get("climate.salon").attributes["hvac_action"] == HVACAction.OFF

    # survives a reload with settings intact
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    ctrl = entry.runtime_data
    assert ctrl.settings["day_temp"] == 22.0
    assert ctrl.offset_for("number.trv_1_calibration", 1) == -3.0
    assert ctrl.offset_for("number.trv_2_calibration", 1) == -2.0
    assert ctrl.hvac_mode == HVACMode.OFF
    assert ctrl.preset == "comfort"


async def test_config_flow_autodetect(hass: HomeAssistant) -> None:
    """Offset entity is found on the TRV's device (Z2M naming)."""
    src = MockConfigEntry(domain="mqtt")
    src.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=src.entry_id, identifiers={("mqtt", "0xabc")}, name="Glowica salon"
    )
    reg = er.async_get(hass)
    for domain, uid, obj in (
        ("climate", "0xabc_climate", "glowica_salon"),
        ("number", "0xabc_valve_opening_degree", "glowica_salon_valve_opening_degree"),
        ("number", "0xabc_external_temperature_input", "glowica_salon_external_temperature_input"),
        (
            "number",
            "0xabc_local_temperature_calibration",
            "glowica_salon_local_temperature_calibration",
        ),
    ):
        reg.async_get_or_create(
            domain, "mqtt", uid, device_id=dev.id, suggested_object_id=obj, config_entry=src
        )
    hass.states.async_set("sensor.salon_t", "20.4", {"device_class": "temperature"})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Salon",
            "temperature_sensor": "sensor.salon_t",
            "trvs": ["climate.glowica_salon"],
        },
    )
    assert result["step_id"] == "offsets"
    assert result["description_placeholders"]["detected"] == (
        "number.glowica_salon_local_temperature_calibration"
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"offset_entities": ["number.glowica_salon_local_temperature_calibration"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Salon"
    await settle(hass)

    # Same TRV in a second room -> abort
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Inne", "temperature_sensor": "sensor.salon_t", "trvs": ["climate.glowica_salon"]},
    )
    assert result["type"] is FlowResultType.ABORT

    # Options flow
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "temperature_sensor": "sensor.salon_t",
            "trvs": ["climate.glowica_salon"],
            "offset_entities": ["number.glowica_salon_local_temperature_calibration"],
            "window_sensors": [],
            "window_open_delay": 30,
            "window_close_delay": 60,
            "sensor_timeout": 60,
            "fallback_offset": 0,
            "adopt_trv_setpoint": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)
    assert entry.runtime_data.adopt_trv_setpoint is False
    assert entry.options["outdoor_sensor"] is None
    assert entry.runtime_data.outdoor_sensor is None
    assert entry.runtime_data.sensor_timeout_s == 3600


async def test_startup_without_sensor_touches_nothing(
    hass: HomeAssistant, fake_devices, freezer
) -> None:
    """After a restart the room sensor may be 'unknown' for a while: no writes."""
    freezer.move_to("2026-11-09 11:00:00+00:00")
    hass.states.async_set("sensor.salon_temp", "unknown")
    entry = await setup_room(hass)
    await advance(hass, freezer, 300, heartbeat=False)
    assert entry.runtime_data.writes == 0
    await set_temp(hass, 20.5)
    assert fake_devices["off1"].calls == [-2.0]
    # never reports at all -> after the grace period the TRVs take over (offset 0)
    hass.states.async_set("sensor.salon_temp", "unknown")
    await advance(hass, freezer, 900, heartbeat=False)
    assert fake_devices["off1"].native_value == 0.0


async def test_away_mode(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Away switch: target = away temp (default 20), knob is reverted, off -> schedule."""
    trv1, trv2, off1 = fake_devices["trv1"], fake_devices["trv2"], fake_devices["off1"]
    freezer.move_to("2026-11-09 11:00:00+00:00")  # day, target 21
    await set_temp(hass, 20.5)
    entry = await setup_room(hass)
    away = our_entity(hass, entry, "switch", "away")
    assert hass.states.get(away).state == "off"
    assert off1.calls == [-2.0]

    await hass.services.async_call("switch", "turn_on", {"entity_id": away}, blocking=True)
    await settle(hass)
    state = hass.states.get("climate.salon")
    assert state.attributes["temperature"] == 20.0
    assert state.attributes["target_source"] == "away"
    assert trv1.target_temperature == 20.0 and trv2.target_temperature == 20.0
    # 20.5 vs 20 -> no heating; level change waits for the 5 min hold
    await advance(hass, freezer, 300)
    assert off1.calls[-1] == 3.0

    # knob while away -> reverted, target stays 20
    await advance(hass, freezer, 300)
    trv1.knob(23.0)
    await settle(hass)
    assert hass.states.get("climate.salon").attributes["temperature"] == 20.0
    await advance(hass, freezer, 120)
    assert trv1.target_temperature == 20.0

    # card +/- while away edits the away temperature
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.salon", "temperature": 18.0},
        blocking=True,
    )
    await settle(hass)
    assert entry.runtime_data.settings["away_temp"] == 18.0
    away_temp = our_entity(hass, entry, "number", "away_temp")
    assert float(hass.states.get(away_temp).state) == 18.0

    # off -> back to schedule (21), survives reload
    await hass.services.async_call("switch", "turn_off", {"entity_id": away}, blocking=True)
    await settle(hass)
    assert trv1.target_temperature == 21.0
    await hass.services.async_call("switch", "turn_on", {"entity_id": away}, blocking=True)
    await settle(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert hass.states.get(away).state == "on"
    assert hass.states.get("climate.salon").attributes["temperature"] == 18.0


async def test_outdoor_cutoff(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Outdoor >= 15 -> TRVs off; back only at <= 14 (hysteresis 1)."""
    trv1, trv2, off1 = fake_devices["trv1"], fake_devices["trv2"], fake_devices["off1"]
    freezer.move_to("2026-11-09 11:00:00+00:00")
    await set_temp(hass, 20.5)
    hass.states.async_set("sensor.outside", "10.0", {"device_class": "temperature"})
    entry = await setup_room(hass, outdoor_sensor="sensor.outside")
    cutoff = our_entity(hass, entry, "number", "outdoor_cutoff")
    assert float(hass.states.get(cutoff).state) == 15.0
    assert hass.states.get("sensor.salon_status").state == "heating"

    hass.states.async_set("sensor.outside", "15.2", {"device_class": "temperature"})
    await settle(hass)
    assert hass.states.get("sensor.salon_status").state == "outdoor_warm"
    assert trv1.hvac_mode == HVACMode.OFF
    assert trv2.target_temperature == 5  # no OFF mode -> min_temp
    assert hass.states.get("climate.salon").attributes["outdoor_warm"] is True

    writes = entry.runtime_data.writes
    for t in ("14.6", "unavailable", "14.3", "15.5", "14.1"):
        hass.states.async_set("sensor.outside", t, {"device_class": "temperature"})
        await settle(hass)
        await advance(hass, freezer, 60)
    assert hass.states.get("sensor.salon_status").state == "outdoor_warm"
    assert entry.runtime_data.writes == writes  # nothing sent while it flaps

    hass.states.async_set("sensor.outside", "14.0", {"device_class": "temperature"})
    await settle(hass)
    assert hass.states.get("sensor.salon_status").state == "heating"
    assert trv1.hvac_mode == HVACMode.HEAT and trv2.target_temperature == 21.0
    assert off1.native_value == -2.0

    # threshold is a live setting
    await hass.services.async_call(
        "number", "set_value", {"entity_id": cutoff, "value": 12.0}, blocking=True
    )
    await settle(hass)
    assert hass.states.get("sensor.salon_status").state == "outdoor_warm"


async def test_outdoor_from_weather_entity(hass: HomeAssistant, fake_devices, freezer) -> None:
    """A weather entity works as outdoor source (temperature attribute)."""
    freezer.move_to("2026-11-09 11:00:00+00:00")
    await set_temp(hass, 20.5)
    hass.states.async_set("weather.home", "sunny", {"temperature": 17.0})
    await setup_room(hass, outdoor_sensor="weather.home")
    assert hass.states.get("sensor.salon_status").state == "outdoor_warm"


async def test_no_outdoor_sensor_no_cutoff_entity(hass: HomeAssistant, fake_devices) -> None:
    """Without an outdoor sensor the cutoff number is not created."""
    entry = await setup_room(hass)
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_outdoor_cutoff") is None
    assert reg.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_away_temp")


async def test_per_trv_offsets(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Each TRV has its own offsets per level; range follows the TRV entity."""
    off1, off2 = fake_devices["off1"], fake_devices["off2"]
    freezer.move_to("2026-11-09 11:00:00+00:00")
    await set_temp(hass, 20.5)
    entry = await setup_room(hass)
    assert off1.calls == [-2.0] and off2.calls == [-2.0]

    l1_trv2 = trv_entity(hass, entry, "number", "number.trv_2_calibration", "offset_l1")
    state = hass.states.get(l1_trv2)
    assert state.attributes["min"] == -12.8 and state.attributes["max"] == 12.7
    assert state.attributes["offset_entity"] == "number.trv_2_calibration"
    assert "TRV 2 calibration" in state.attributes["friendly_name"]

    await hass.services.async_call(
        "number", "set_value", {"entity_id": l1_trv2, "value": -4.0}, blocking=True
    )
    await settle(hass)
    assert off2.calls[-1] == -4.0
    assert off1.calls == [-2.0]  # untouched
    sens2 = trv_entity(hass, entry, "sensor", "number.trv_2_calibration", "offset")
    assert float(hass.states.get(sens2).state) == -4.0
    assert hass.states.get("climate.salon").attributes["offsets"] == {
        "number.trv_1_calibration": -2.0,
        "number.trv_2_calibration": -4.0,
    }

    # idle offsets per TRV too
    idle1 = trv_entity(hass, entry, "number", "number.trv_1_calibration", "offset_l0")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": idle1, "value": 5.0}, blocking=True
    )
    await set_temp(hass, 21.2)
    await advance(hass, freezer, 300)
    assert off1.native_value == 5.0 and off2.native_value == 3.0

    # values survive a reload
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert float(hass.states.get(l1_trv2).state) == -4.0
    assert float(hass.states.get(idle1).state) == 5.0

    # removing TRV 2 from the room removes its entities
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "temperature_sensor": "sensor.salon_temp",
            "trvs": ["climate.trv_1"],
            "offset_entities": ["number.trv_1_calibration"],
        },
    )
    await settle(hass)
    assert hass.states.get(l1_trv2) is None
    assert er.async_get(hass).async_get(l1_trv2) is None
    assert er.async_get(hass).async_get(idle1) is not None


async def test_migration_from_room_offsets(
    hass: HomeAssistant, fake_devices, freezer, hass_storage
) -> None:
    """1.1 stored room-level offsets: they become every TRV's values, old entities go."""
    freezer.move_to("2026-11-09 11:00:00+00:00")
    await set_temp(hass, 20.5)
    entry = MockConfigEntry(domain=DOMAIN, title="Salon", data=ENTRY_DATA, unique_id="salon")
    entry.add_to_hass(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"settings": {"level1_offset": -3.5, "idle_offset": 4.0}},
    }
    reg = er.async_get(hass)
    old = reg.async_get_or_create(
        "number", DOMAIN, f"{entry.entry_id}_level1_offset", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    assert fake_devices["off1"].calls == [-3.5] and fake_devices["off2"].calls == [-3.5]
    assert reg.async_get(old.entity_id) is None
    ctrl = entry.runtime_data
    assert ctrl.offset_for("number.trv_2_calibration", 0) == 4.0
    assert ctrl.offset_for("number.trv_2_calibration", 2) == -5.0


async def test_boost(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Boost = TRV max setpoint for N minutes, ignores off/away/target; window wins."""
    trv1, trv2, off1 = fake_devices["trv1"], fake_devices["trv2"], fake_devices["off1"]
    freezer.move_to("2026-11-09 11:00:00+00:00")
    await set_temp(hass, 21.5)  # above target -> idle
    entry = await setup_room(hass)
    ctrl = entry.runtime_data
    boost = our_entity(hass, entry, "button", "boost")
    stop = our_entity(hass, entry, "button", "boost_stop")
    duration = our_entity(hass, entry, "number", "boost_duration")
    assert float(hass.states.get(duration).state) == 15.0
    offsets_before = list(off1.calls)

    # room switched off, still boosts
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "off"},
        blocking=True,
    )
    await settle(hass)
    assert trv1.hvac_mode == HVACMode.OFF
    writes = ctrl.writes
    await hass.services.async_call("button", "press", {"entity_id": boost}, blocking=True)
    await settle(hass)
    assert hass.states.get("sensor.salon_status").state == "boost"
    assert hass.states.get("climate.salon").attributes["hvac_action"] == HVACAction.HEATING
    assert trv1.hvac_mode == HVACMode.HEAT
    assert trv1.target_temperature == 30 and trv2.target_temperature == 30  # max_temp
    assert off1.calls == offsets_before  # offsets untouched
    assert ctrl.writes == writes + 3  # trv1 mode + 2 setpoints

    # still boosting after 14 min, over after 15
    await advance(hass, freezer, 14 * 60, step=60)
    assert hass.states.get("sensor.salon_status").state == "boost"
    await advance(hass, freezer, 70, step=10)
    assert hass.states.get("sensor.salon_status").state == "off"
    assert trv1.hvac_mode == HVACMode.OFF
    assert hass.states.get("climate.salon").attributes["boost_until"] is None

    # back on, boost again; window opening wins over boost
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.salon", "hvac_mode": "heat"},
        blocking=True,
    )
    await hass.services.async_call("button", "press", {"entity_id": boost}, blocking=True)
    await settle(hass)
    assert trv1.target_temperature == 30
    hass.states.async_set("binary_sensor.salon_okno", "on")
    await settle(hass)
    await advance(hass, freezer, 90)
    assert hass.states.get("sensor.salon_status").state == "window_open"
    assert trv1.hvac_mode == HVACMode.OFF
    hass.states.async_set("binary_sensor.salon_okno", "off")
    await settle(hass)
    await advance(hass, freezer, 150)
    assert hass.states.get("sensor.salon_status").state == "boost"
    assert trv1.target_temperature == 30

    # survives a reload, stop button ends it, setpoint back to target
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert hass.states.get("sensor.salon_status").state == "boost"
    await hass.services.async_call("button", "press", {"entity_id": stop}, blocking=True)
    await settle(hass)
    assert hass.states.get("sensor.salon_status").state == "idle"
    assert trv1.target_temperature == 21.0 and trv2.target_temperature == 21.0

    # custom duration
    await hass.services.async_call(
        "number", "set_value", {"entity_id": duration, "value": 5}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": boost}, blocking=True)
    await settle(hass)
    await advance(hass, freezer, 5 * 60 + 30, step=30)
    assert hass.states.get("sensor.salon_status").state == "idle"


COVER = "cover.zaslona_salon"


async def test_curtains(hass: HomeAssistant, fake_devices, freezer) -> None:
    """Open on big deficit while heating, close when done; manual moves win."""
    cover = fake_devices["cover"]
    freezer.move_to("2026-11-09 11:00:00+00:00")  # day, target 21
    await set_temp(hass, 20.5)  # deficit 0.5 -> heating, but below 1.0
    entry = await setup_room(hass, covers=[COVER])
    ctrl = entry.runtime_data
    assert hass.states.get("sensor.salon_status").state == "heating"
    assert cover.calls == []
    switch = our_entity(hass, entry, "switch", "covers")
    deficit = our_entity(hass, entry, "number", "cover_deficit")
    assert float(hass.states.get(deficit).state) == 1.0

    # deficit grows during the same heating -> open
    await set_temp(hass, 19.9)
    await settle(hass)
    assert cover.calls == ["open"] and cover.current_cover_position == 100
    assert hass.states.get("climate.salon").attributes["covers_opened"] == [COVER]
    await advance(hass, freezer, 600)
    assert cover.calls == ["open"]  # no repeats

    # deficit shrinks but still heating -> stays open; heating ends -> close
    await set_temp(hass, 20.6)
    assert cover.calls == ["open"]
    await set_temp(hass, 21.1)
    await advance(hass, freezer, 300)
    assert hass.states.get("sensor.salon_status").state == "idle"
    assert cover.calls == ["open", "close"]
    assert ctrl.covers_owned == set()

    # next cycle: opened, then closed by hand -> not reopened, not touched at the end
    await set_temp(hass, 19.8)
    await advance(hass, freezer, 300)
    assert cover.calls[-1] == "open"
    await advance(hass, freezer, 300)
    cover.move(0)
    await settle(hass)
    await advance(hass, freezer, 600)
    await set_temp(hass, 19.5)
    assert cover.calls == ["open", "close", "open"]
    await set_temp(hass, 21.2)
    await advance(hass, freezer, 300)
    assert hass.states.get("sensor.salon_status").state == "idle"
    assert cover.calls == ["open", "close", "open"]

    # curtain already open before heating -> never closed by us
    cover.move(100)
    await set_temp(hass, 19.6)
    await advance(hass, freezer, 300)
    await set_temp(hass, 21.2)
    await advance(hass, freezer, 300)
    assert cover.calls == ["open", "close", "open"] and cover.current_cover_position == 100

    # window open -> curtains left alone
    cover.move(0)
    await set_temp(hass, 19.6)
    await advance(hass, freezer, 300)
    assert cover.calls[-1] == "open"
    n = len(cover.calls)
    hass.states.async_set("binary_sensor.salon_okno", "on")
    await settle(hass)
    await advance(hass, freezer, 120)
    assert hass.states.get("sensor.salon_status").state == "window_open"
    assert len(cover.calls) == n
    hass.states.async_set("binary_sensor.salon_okno", "off")
    await settle(hass)
    await advance(hass, freezer, 180)

    # sunset while heating -> close what we opened, nothing opens at night
    hass.states.async_set("sun.sun", "below_horizon")
    await settle(hass)
    assert cover.calls[-1] == "close" and len(cover.calls) == n + 1
    await set_temp(hass, 19.0)
    await advance(hass, freezer, 600)
    assert len(cover.calls) == n + 1

    # sunrise during heating -> new episode, open again; switch off -> close
    hass.states.async_set("sun.sun", "above_horizon")
    await settle(hass)
    assert cover.calls[-1] == "open"
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await settle(hass)
    assert cover.calls[-1] == "close"
    await advance(hass, freezer, 600)
    assert cover.calls[-1] == "close"

    # ownership survives a reload
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await settle(hass)
    assert cover.calls[-1] == "open"
    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert entry.runtime_data.covers_owned == {COVER}
    await set_temp(hass, 21.3)
    await advance(hass, freezer, 300)
    assert cover.calls[-1] == "close"


async def test_curtain_entities_only_with_covers(hass: HomeAssistant, fake_devices) -> None:
    """No covers configured -> no curtain switch/number; removing covers cleans up."""
    entry = await setup_room(hass, covers=[COVER])
    reg = er.async_get(hass)
    sw = reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_covers")
    num = reg.async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_cover_deficit")
    assert sw and num
    hass.config_entries.async_update_entry(entry, options={"covers": []})
    await settle(hass)
    assert reg.async_get(sw) is None and reg.async_get(num) is None
    assert reg.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_away")
