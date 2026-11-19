"""Constants for HA TRV Thermostat."""

from __future__ import annotations

from datetime import time
from typing import Final

DOMAIN: Final = "ha_trv_thermostat"
STORAGE_VERSION: Final = 1

# --- static config (config entry data / options) ---------------------------
CONF_TEMP_SENSOR: Final = "temperature_sensor"
CONF_TRVS: Final = "trvs"
CONF_OFFSETS: Final = "offset_entities"
CONF_WINDOWS: Final = "window_sensors"
CONF_WINDOW_OPEN_DELAY: Final = "window_open_delay"
CONF_WINDOW_CLOSE_DELAY: Final = "window_close_delay"
CONF_SENSOR_TIMEOUT: Final = "sensor_timeout"
CONF_ADOPT_TRV_SETPOINT: Final = "adopt_trv_setpoint"
CONF_FALLBACK_OFFSET: Final = "fallback_offset"
CONF_OUTDOOR_SENSOR: Final = "outdoor_sensor"
CONF_OUTDOOR_HYST: Final = "outdoor_hysteresis"

DEFAULT_WINDOW_OPEN_DELAY: Final = 60  # s
DEFAULT_WINDOW_CLOSE_DELAY: Final = 120  # s
DEFAULT_SENSOR_TIMEOUT: Final = 120  # min
DEFAULT_ADOPT_TRV_SETPOINT: Final = True
DEFAULT_FALLBACK_OFFSET: Final = 0.0
DEFAULT_OUTDOOR_HYST: Final = 1.0

# --- runtime settings (persisted in Store, exposed as number/time entities) --
S_DAY_TEMP: Final = "day_temp"
S_NIGHT_TEMP: Final = "night_temp"
S_HYST_ON: Final = "hyst_on"
S_HYST_OFF: Final = "hyst_off"
S_LEVEL_HYST: Final = "level_hyst"
S_IDLE_OFFSET: Final = "idle_offset"
S_L1_OFFSET: Final = "level1_offset"
S_L2_OFFSET: Final = "level2_offset"
S_L2_THRESHOLD: Final = "level2_threshold"
S_L3_OFFSET: Final = "level3_offset"
S_L3_THRESHOLD: Final = "level3_threshold"
S_MIN_HOLD: Final = "min_hold"  # minutes
S_LOOKAHEAD: Final = "lookahead"  # minutes
S_AWAY_TEMP: Final = "away_temp"
S_OUTDOOR_CUTOFF: Final = "outdoor_cutoff"
S_DAY_START: Final = "day_start"
S_NIGHT_START: Final = "night_start"

DEFAULT_SETTINGS: Final[dict] = {
    S_DAY_TEMP: 21.0,
    S_NIGHT_TEMP: 19.0,
    S_AWAY_TEMP: 20.0,
    S_OUTDOOR_CUTOFF: 15.0,
    S_HYST_ON: 0.3,
    S_HYST_OFF: 0.1,
    S_LEVEL_HYST: 0.2,
    S_IDLE_OFFSET: 3.0,
    S_L1_OFFSET: -2.0,
    S_L2_OFFSET: -5.0,
    S_L2_THRESHOLD: 0.6,
    S_L3_OFFSET: -8.0,
    S_L3_THRESHOLD: 0.0,  # 0 = level 3 disabled
    S_MIN_HOLD: 5.0,
    S_LOOKAHEAD: 0.0,
    S_DAY_START: "06:00:00",
    S_NIGHT_START: "22:00:00",
}

TIME_SETTINGS: Final = (S_DAY_START, S_NIGHT_START)

# --- presets ---------------------------------------------------------------
PRESET_SCHEDULE: Final = "schedule"
# comfort / eco come from homeassistant.components.climate

# --- actuator behaviour ----------------------------------------------------
TICK_INTERVAL_S: Final = 60
VERIFY_DELAY_S: Final = 90  # wait this long for the TRV to confirm a write
MAX_ATTEMPTS: Final = 3  # quick retries before backing off
BACKOFF_S: Final = 1800  # then retry at most every 30 min
ADOPT_GUARD_S: Final = 180  # ignore TRV setpoint reports this long after our write
WRITE_SPACING_S: Final = 0.3  # gap between consecutive Zigbee writes
SETPOINT_MIN_TOLERANCE: Final = 0.26  # TRV setpoint within +-0.25 is "good enough"
TREND_WINDOW_S: Final = 1200
SENSOR_BAD_GRACE_S: Final = 600  # unavailable shorter than this = keep last value
STARTUP_GRACE_S: Final = 600  # no fallback writes right after HA start
TREND_HISTORY_S: Final = 3600


def parse_time(value: str | time) -> time:
    """Parse HH:MM[:SS] to time."""
    if isinstance(value, time):
        return value
    parts = [int(p) for p in str(value).split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])
