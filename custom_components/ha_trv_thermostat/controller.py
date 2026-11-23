"""Room controller - glue between Home Assistant and the pure logic.

Zigbee traffic policy
---------------------
* The evaluation loop runs on every room-sensor update and every 60 s, but it
  only *decides*. Nothing is sent unless the desired value of an actuator
  (TRV mode, TRV setpoint, offset number) changes.
* Before every write the current entity state is compared with the desired
  value; if the TRV already has it, nothing is sent.
* After a write we wait ``VERIFY_DELAY_S`` for the TRV to report back. If it
  doesn't, we retry up to ``MAX_ATTEMPTS`` times, then at most every
  ``BACKOFF_S``. This also repairs TRVs that rebooted or were changed by hand.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MIN_TEMP,
    ATTR_MAX_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.number import (
    ATTR_MAX,
    ATTR_MIN,
    ATTR_STEP,
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.cover import ATTR_CURRENT_POSITION, DOMAIN as COVER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    ATTR_TEMPERATURE,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ADOPT_GUARD_S,
    BACKOFF_S,
    COVER_GUARD_S,
    COVER_MANUAL_POSITION,
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
    DEFAULT_SETTINGS,
    DEFAULT_WINDOW_CLOSE_DELAY,
    DEFAULT_WINDOW_OPEN_DELAY,
    DOMAIN,
    MAX_ATTEMPTS,
    PRESET_SCHEDULE,
    S_AWAY_TEMP,
    S_BOOST_DURATION,
    S_COVER_DEFICIT,
    S_DAY_START,
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
    S_NIGHT_START,
    S_NIGHT_TEMP,
    S_OUTDOOR_CUTOFF,
    SENSOR_BAD_GRACE_S,
    SETPOINT_MIN_TOLERANCE,
    STARTUP_GRACE_S,
    STORAGE_VERSION,
    TICK_INTERVAL_S,
    TREND_HISTORY_S,
    TREND_WINDOW_S,
    VERIFY_DELAY_S,
    WRITE_SPACING_S,
    parse_time,
)
from .logic import (
    Decision,
    LoopSettings,
    LoopState,
    Status,
    apply_decision,
    build_thresholds,
    compute_slope,
    decide,
    is_day,
    next_transition,
    outdoor_cutoff,
    quantize,
)

_LOGGER = logging.getLogger(__name__)

BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, None)
KIND_MODE = "mode"
KIND_SETPOINT = "setpoint"
KIND_OFFSET = "offset"
SETPOINT_MIN = "min"  # sentinel: emulate "off" with the TRV's min_temp
SETPOINT_MAX = "max"  # sentinel: boost -> TRV's max_temp, valve fully open
COVER_OPEN, COVER_OPENING, COVER_CLOSING, COVER_CLOSED = "open", "opening", "closing", "closed"
TEMP_MIN = 5.0
TEMP_MAX = 30.0


@dataclass
class Actuator:
    """One writable thing on a TRV (mode, setpoint or offset)."""

    entity_id: str
    kind: str
    desired: Any = None  # abstract desired value (resolved per-TRV on apply)
    written: Any = None  # last concrete value we sent / saw confirmed
    last_write: float = 0.0
    attempts: int = 0
    warned: bool = False


def _now_ts() -> float:
    return dt_util.utcnow().timestamp()


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RoomController:
    """Controls all TRVs of one room."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise from a config entry."""
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.temp_sensor: str = cfg[CONF_TEMP_SENSOR]
        self.trvs: list[str] = list(cfg.get(CONF_TRVS) or [])
        self.offset_entities: list[str] = list(cfg.get(CONF_OFFSETS) or [])
        self.windows: list[str] = list(cfg.get(CONF_WINDOWS) or [])
        self.window_open_delay = float(cfg.get(CONF_WINDOW_OPEN_DELAY, DEFAULT_WINDOW_OPEN_DELAY))
        self.window_close_delay = float(
            cfg.get(CONF_WINDOW_CLOSE_DELAY, DEFAULT_WINDOW_CLOSE_DELAY)
        )
        self.sensor_timeout_s = float(cfg.get(CONF_SENSOR_TIMEOUT, DEFAULT_SENSOR_TIMEOUT)) * 60
        self.adopt_trv_setpoint = bool(cfg.get(CONF_ADOPT_TRV_SETPOINT, DEFAULT_ADOPT_TRV_SETPOINT))
        self.fallback_offset = float(cfg.get(CONF_FALLBACK_OFFSET, DEFAULT_FALLBACK_OFFSET))
        self.outdoor_sensor: str | None = cfg.get(CONF_OUTDOOR_SENSOR) or None
        self.outdoor_hyst = float(cfg.get(CONF_OUTDOOR_HYST, DEFAULT_OUTDOOR_HYST))
        self.covers: list[str] = list(cfg.get(CONF_COVERS) or [])
        self.cover_daylight_only = bool(
            cfg.get(CONF_COVER_DAYLIGHT_ONLY, DEFAULT_COVER_DAYLIGHT_ONLY)
        )

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.hvac_mode: HVACMode = HVACMode.HEAT
        self.preset: str = PRESET_SCHEDULE
        self.override_temp: float | None = None
        self.override_until: datetime | None = None

        self._loop = LoopState()
        self._samples: deque[tuple[float, float]] = deque()
        self._window_raw = False
        self._window_raw_since = 0.0
        self.window_open = False

        self.decision: Decision | None = None
        self.target: float | None = None
        self.target_source: str | None = None
        self.room_temp: float | None = None
        self.slope: float | None = None
        self.commanded_offsets: dict[str, float | None] = {}
        self.last_level_change: datetime | None = None
        self.writes = 0
        self.away = False
        self.boost_until: datetime | None = None
        self._boost_timer: CALLBACK_TYPE | None = None
        self.covers_enabled = True
        # covers we opened (and will close again) / covers already dealt with
        # in the current heating episode (never reopened after a manual close)
        self.covers_owned: set[str] = set()
        self._covers_handled: set[str] = set()
        self._cover_cmd_ts: dict[str, float] = {}
        self.outdoor_temp: float | None = None
        self.outdoor_warm = False

        self._modes = {e: Actuator(e, KIND_MODE) for e in self.trvs}
        self._setpoints = {e: Actuator(e, KIND_SETPOINT) for e in self.trvs}
        self._offsets = {e: Actuator(e, KIND_OFFSET) for e in self.offset_entities}
        # Per-TRV offsets: keyed by the offset entity's registry id (survives
        # entity_id renames), values per level 0..3.
        self.trv_keys: dict[str, str] = {}
        self.trv_names: dict[str, str] = {}
        self._identify_trvs()
        self.trv_offsets: dict[str, dict[str, float]] = {}

        self._started = False
        self._started_at = 0.0
        self._last_temp: float | None = None
        self._last_temp_ts = 0.0
        self._temp_bad_since: float | None = None
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._window_timer: CALLBACK_TYPE | None = None
        self._apply_task: asyncio.Task | None = None
        self._apply_again = False
        self._snapshot: tuple | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def device_info(self) -> DeviceInfo:
        """Device for all entities of this room."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="HA TRV Thermostat",
            model="Room controller",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_load(self) -> None:
        """Restore persisted settings and runtime state."""
        data = await self._store.async_load() or {}
        self.settings.update(data.get("settings", {}))
        try:
            self.hvac_mode = HVACMode(data.get("hvac_mode", HVACMode.HEAT))
        except ValueError:
            self.hvac_mode = HVACMode.HEAT
        if data.get("preset") in (PRESET_SCHEDULE, PRESET_COMFORT, PRESET_ECO):
            self.preset = data["preset"]
        self.override_temp = _float(data.get("override_temp"))
        until = data.get("override_until")
        self.override_until = dt_util.parse_datetime(until) if until else None
        stored = data.get("trv_offsets", {})
        for key in self.trv_keys.values():
            values = dict(self._template_offsets())
            values.update({k: float(v) for k, v in stored.get(key, {}).items()})
            self.trv_offsets[key] = values
        self.away = bool(data.get("away", False))
        boost = data.get("boost_until")
        self.boost_until = dt_util.parse_datetime(boost) if boost else None
        self.covers_enabled = bool(data.get("covers_enabled", True))
        self.covers_owned = {c for c in data.get("covers_owned", []) if c in self.covers}
        self._covers_handled = {c for c in data.get("covers_handled", []) if c in self.covers}
        self.outdoor_warm = bool(data.get("outdoor_warm", False)) and bool(self.outdoor_sensor)
        self._loop.level = int(data.get("level", 0) or 0)
        self._loop.level_since = _float(data.get("level_since"))

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "settings": self.settings,
            "hvac_mode": str(self.hvac_mode),
            "preset": self.preset,
            "override_temp": self.override_temp,
            "override_until": self.override_until.isoformat() if self.override_until else None,
            "trv_offsets": self.trv_offsets,
            "away": self.away,
            "boost_until": self.boost_until.isoformat() if self.boost_until else None,
            "covers_enabled": self.covers_enabled,
            "covers_owned": sorted(self.covers_owned),
            "covers_handled": sorted(self._covers_handled),
            "outdoor_warm": self.outdoor_warm,
            "level": self._loop.level,
            "level_since": self._loop.level_since,
        }

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 5)

    @callback
    def async_start(self) -> None:
        """Subscribe to everything and start evaluating once HA is up."""
        hass = self.hass
        self._unsubs.append(async_track_state_change_event(hass, [self.temp_sensor], self._on_temp))
        if self.windows:
            self._unsubs.append(async_track_state_change_event(hass, self.windows, self._on_window))
        if self.outdoor_sensor:
            self._unsubs.append(
                async_track_state_change_event(hass, [self.outdoor_sensor], self._on_outdoor)
            )
        if self.covers:
            self._unsubs.append(async_track_state_change_event(hass, self.covers, self._on_cover))
            if self.cover_daylight_only:
                self._unsubs.append(async_track_state_change_event(hass, ["sun.sun"], self._on_sun))
        if self.trvs:
            self._unsubs.append(async_track_state_change_event(hass, self.trvs, self._on_trv))
        if self.offset_entities:
            self._unsubs.append(
                async_track_state_change_event(hass, self.offset_entities, self._on_actuator)
            )
        self._unsubs.append(
            async_track_time_interval(hass, self._on_tick, timedelta(seconds=TICK_INTERVAL_S))
        )
        self._unsubs.append(async_at_started(hass, self._on_started))

    async def async_stop(self) -> None:
        """Unsubscribe and flush storage."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._window_timer:
            self._window_timer()
            self._window_timer = None
        if self._boost_timer:
            self._boost_timer()
            self._boost_timer = None
        if self._apply_task and not self._apply_task.done():
            self._apply_task.cancel()
        await self._store.async_save(self._data_to_save())

    async def async_remove_store(self) -> None:
        """Delete persisted data (entry removed)."""
        await self._store.async_remove()

    @callback
    def async_add_listener(self, update_cb: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Register an entity update callback."""
        self._listeners.append(update_cb)

        @callback
        def _remove() -> None:
            self._listeners.remove(update_cb)

        return _remove

    @callback
    def _notify(self, force: bool = False) -> None:
        d = self.decision
        snap = (
            d.status if d else None,
            d.level if d else None,
            d.reason if d else None,
            self.target,
            self.target_source,
            self.room_temp,
            self.hvac_mode,
            self.preset,
            self.override_temp,
            self.override_until,
            self.window_open,
            tuple(sorted(self.commanded_offsets.items())),
            self.away,
            self.boost_until,
            tuple(sorted(self.covers_owned)),
            self.covers_enabled,
            self.outdoor_temp,
            self.outdoor_warm,
        )
        if not force and snap == self._snapshot:
            return
        self._snapshot = snap
        for update_cb in list(self._listeners):
            update_cb()

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    async def _on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        now = _now_ts()
        self._started_at = now
        state = self.hass.states.get(self.temp_sensor)
        if state is not None and (val := _float(state.state)) is not None:
            self._samples.append((state.last_updated.timestamp(), val))
        self._window_raw = self._window_any_open()
        self._window_raw_since = now - 86400  # already settled
        self.window_open = self._window_raw
        self._schedule_boost_end()
        self.async_evaluate("startup")

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self.async_evaluate("tick")

    @callback
    def _on_outdoor(self, _event: Event[EventStateChangedData]) -> None:
        self.async_evaluate("outdoor temperature")

    @callback
    def _on_sun(self, _event: Event[EventStateChangedData]) -> None:
        self.async_evaluate("sun")

    @callback
    def _on_cover(self, event: Event[EventStateChangedData]) -> None:
        """Someone else moved a cover we opened -> hands off until the episode ends."""
        new = event.data["new_state"]
        if new is None or new.entity_id not in self.covers_owned:
            return
        if _now_ts() - self._cover_cmd_ts.get(new.entity_id, 0.0) < COVER_GUARD_S:
            return
        pos = _float(new.attributes.get(ATTR_CURRENT_POSITION))
        if new.state in (COVER_CLOSING, COVER_CLOSED) or (
            pos is not None and pos < COVER_MANUAL_POSITION
        ):
            _LOGGER.info(
                "%s: %s moved by hand, no longer controlled this heating cycle",
                self.entry.title,
                new.entity_id,
            )
            self.covers_owned.discard(new.entity_id)
            self._save()
            self._notify()

    @callback
    def _on_temp(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is not None and (val := _float(new.state)) is not None:
            self._samples.append((_now_ts(), val))
        self.async_evaluate("temperature")

    def _window_any_open(self) -> bool:
        for eid in self.windows:
            state = self.hass.states.get(eid)
            if state is not None and state.state == STATE_ON:
                return True
        return False

    @callback
    def _on_window(self, _event: Event[EventStateChangedData]) -> None:
        raw = self._window_any_open()
        if raw == self._window_raw:
            return
        self._window_raw = raw
        self._window_raw_since = _now_ts()
        delay = self.window_open_delay if raw else self.window_close_delay
        if self._window_timer:
            self._window_timer()

        @callback
        def _fire(_now: datetime) -> None:
            self._window_timer = None
            self.async_evaluate("window")

        self._window_timer = async_call_later(self.hass, delay + 0.5, _fire)
        self.async_evaluate("window raw")

    @callback
    def _on_actuator(self, event: Event[EventStateChangedData]) -> None:
        """Offset entity came back online -> push pending values."""
        old, new = event.data["old_state"], event.data["new_state"]
        if (
            (old is None or old.state in BAD_STATES)
            and new is not None
            and new.state not in BAD_STATES
        ):
            self._schedule_apply()

    @callback
    def _on_trv(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state in BAD_STATES:
            return
        if old is None or old.state in BAD_STATES:
            self._schedule_apply()
            return
        if not (self.adopt_trv_setpoint and self._started and self.decision):
            return
        if self.decision.status not in (Status.IDLE, Status.HEATING, Status.FALLBACK):
            return
        if self.away:
            return  # away mode wins; the knob value gets reverted
        if old.state != new.state or new.state == HVACMode.OFF:
            return
        old_t = _float(old.attributes.get(ATTR_TEMPERATURE))
        new_t = _float(new.attributes.get(ATTR_TEMPERATURE))
        if old_t is None or new_t is None or abs(old_t - new_t) < 0.01:
            return
        act = self._setpoints.get(new.entity_id)
        if act is None:
            return
        if _now_ts() - act.last_write < ADOPT_GUARD_S:
            return  # most likely our own write being confirmed (or a stale report)
        if act.written is not None and abs(new_t - float(act.written)) < 0.01:
            return
        wanted = self._resolve_value(act, new)
        if wanted is not None and self._matches(act, new, wanted):
            return
        _LOGGER.info(
            "%s: setpoint changed on %s (%s -> %s), taking it as manual override",
            self.entry.title,
            new.entity_id,
            old_t,
            new_t,
        )
        self._set_override(new_t)
        self.async_evaluate("trv setpoint")

    # ------------------------------------------------------------------ #
    # User actions (from entities)
    # ------------------------------------------------------------------ #

    @callback
    def async_set_hvac_mode(self, mode: HVACMode) -> None:
        """Turn the room on (HEAT) or off."""
        self.hvac_mode = mode
        self._save()
        self.async_evaluate("hvac mode")

    @callback
    def async_set_away(self, away: bool) -> None:
        """Away mode on/off. Clears any temporary override."""
        if away == self.away:
            return
        self.away = away
        self.override_temp = self.override_until = None
        self._save()
        self.async_evaluate("away on" if away else "away off")

    @callback
    def async_start_boost(self) -> None:
        """Full heat for boost_duration minutes (pressing again restarts it)."""
        minutes = float(self.settings[S_BOOST_DURATION])
        self.boost_until = dt_util.utcnow() + timedelta(minutes=minutes)
        self._save()
        self._schedule_boost_end()
        self.async_evaluate("boost start")

    @callback
    def async_set_covers_enabled(self, enabled: bool) -> None:
        """Curtain control on/off (off also closes what we opened)."""
        self.covers_enabled = enabled
        self._save()
        self.async_evaluate("covers on" if enabled else "covers off")

    @callback
    def async_stop_boost(self) -> None:
        """End boost now."""
        if self.boost_until is None:
            return
        self.boost_until = None
        self._save()
        self._schedule_boost_end()
        self.async_evaluate("boost stop")

    @callback
    def _schedule_boost_end(self) -> None:
        if self._boost_timer:
            self._boost_timer()
            self._boost_timer = None
        if self.boost_until is None:
            return

        @callback
        def _end(_now: datetime) -> None:
            self._boost_timer = None
            self.async_evaluate("boost end")

        self._boost_timer = async_track_point_in_utc_time(
            self.hass, _end, self.boost_until + timedelta(seconds=1)
        )

    @callback
    def async_set_preset(self, preset: str) -> None:
        """Change preset; clears any override.

        "away" is the away mode (same as the switch); the schedule/day/night
        preset underneath is kept and comes back when away is turned off.
        """
        if preset == PRESET_AWAY:
            self.async_set_away(True)
            return
        self.away = False
        self.preset = preset
        self.override_temp = None
        self.override_until = None
        self._save()
        self.async_evaluate("preset")

    @callback
    def async_set_target(self, temp: float) -> None:
        """Target set from the climate card."""
        temp = max(TEMP_MIN, min(TEMP_MAX, float(temp)))
        if self.away:
            self.settings[S_AWAY_TEMP] = temp
        elif self.preset == PRESET_COMFORT:
            self.settings[S_DAY_TEMP] = temp
            self.override_temp = self.override_until = None
        elif self.preset == PRESET_ECO:
            self.settings[S_NIGHT_TEMP] = temp
            self.override_temp = self.override_until = None
        else:
            scheduled = self._scheduled_target(dt_util.now())
            if abs(temp - scheduled) < 0.05:
                self.override_temp = self.override_until = None
            else:
                self._set_override(temp)
        self._save()
        self.async_evaluate("set target")

    @callback
    def async_set_setting(self, key: str, value: Any) -> None:
        """Change one runtime setting (number/time entities)."""
        self.settings[key] = value
        self._save()
        self.async_evaluate(f"setting {key}")

    @callback
    def _set_override(self, temp: float) -> None:
        self.override_temp = max(TEMP_MIN, min(TEMP_MAX, float(temp)))
        if self.preset == PRESET_SCHEDULE:
            self.override_until = next_transition(
                dt_util.now(),
                parse_time(self.settings[S_DAY_START]),
                parse_time(self.settings[S_NIGHT_START]),
            )
        else:
            self.override_until = None
        self._save()

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    def _scheduled_target(self, now_dt: datetime) -> float:
        day = is_day(
            now_dt.time(),
            parse_time(self.settings[S_DAY_START]),
            parse_time(self.settings[S_NIGHT_START]),
        )
        return float(self.settings[S_DAY_TEMP] if day else self.settings[S_NIGHT_TEMP])

    def _resolve_target(self, now_dt: datetime) -> tuple[float, str]:
        if self.away:
            return float(self.settings[S_AWAY_TEMP]), "away"
        if self.override_temp is not None:
            return self.override_temp, "override"
        if self.preset == PRESET_COMFORT:
            return float(self.settings[S_DAY_TEMP]), "comfort"
        if self.preset == PRESET_ECO:
            return float(self.settings[S_NIGHT_TEMP]), "eco"
        day = is_day(
            now_dt.time(),
            parse_time(self.settings[S_DAY_START]),
            parse_time(self.settings[S_NIGHT_START]),
        )
        if day:
            return float(self.settings[S_DAY_TEMP]), "day"
        return float(self.settings[S_NIGHT_TEMP]), "night"

    def _read_room_temp(self, now: float) -> float | None:
        """Room temperature, or None if the sensor is considered lost.

        * unavailable/unknown for less than SENSOR_BAD_GRACE_S -> keep last value
          (Z2M availability blips must not cause two rounds of writes)
        * no report (not even an unchanged one) for sensor_timeout -> lost
        """
        state = self.hass.states.get(self.temp_sensor)
        val = None if state is None or state.state in BAD_STATES else _float(state.state)
        if val is not None:
            last = getattr(state, "last_reported", None) or state.last_updated
            self._last_temp = val
            self._last_temp_ts = last.timestamp()
            self._temp_bad_since = None
        elif self._temp_bad_since is None:
            self._temp_bad_since = now
        if self._last_temp is None:
            return None
        if self._temp_bad_since is not None and now - self._temp_bad_since >= SENSOR_BAD_GRACE_S:
            return None
        if now - self._last_temp_ts > self.sensor_timeout_s:
            return None
        return self._last_temp

    def _update_outdoor(self) -> None:
        if not self.outdoor_sensor:
            self.outdoor_temp, self.outdoor_warm = None, False
            return
        state = self.hass.states.get(self.outdoor_sensor)
        if state is None or state.state in BAD_STATES:
            self.outdoor_temp = None
        elif state.domain == "weather":
            self.outdoor_temp = _float(state.attributes.get(ATTR_TEMPERATURE))
        else:
            self.outdoor_temp = _float(state.state)
        warm = outdoor_cutoff(
            self.outdoor_temp,
            float(self.settings[S_OUTDOOR_CUTOFF]),
            self.outdoor_hyst,
            self.outdoor_warm,
        )
        if warm != self.outdoor_warm:
            self.outdoor_warm = warm
            self._save()

    def _update_window(self, now: float) -> None:
        if not self.windows:
            self.window_open = False
            return
        elapsed = now - self._window_raw_since
        if self._window_raw and not self.window_open and elapsed >= self.window_open_delay:
            self.window_open = True
        elif not self._window_raw and self.window_open and elapsed >= self.window_close_delay:
            self.window_open = False

    def loop_settings(self) -> LoopSettings:
        """Build LoopSettings from current runtime settings."""
        s = self.settings
        return LoopSettings(
            hyst_on=float(s[S_HYST_ON]),
            hyst_off=float(s[S_HYST_OFF]),
            thresholds=build_thresholds(float(s[S_L2_THRESHOLD]), float(s[S_L3_THRESHOLD])),
            level_hyst=float(s[S_LEVEL_HYST]),
            min_hold_s=float(s[S_MIN_HOLD]) * 60.0,
            lookahead_min=float(s[S_LOOKAHEAD]),
        )

    def _template_offsets(self) -> dict[str, float]:
        """Room-level offsets: defaults for TRVs that have no own values yet."""
        return {
            str(level): float(self.settings[key])
            for level, key in (
                (0, S_IDLE_OFFSET),
                (1, S_L1_OFFSET),
                (2, S_L2_OFFSET),
                (3, S_L3_OFFSET),
            )
        }

    def _identify_trvs(self) -> None:
        """Stable key and a readable name for every offset entity."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        for eid in self.offset_entities:
            entry = ent_reg.async_get(eid)
            self.trv_keys[eid] = entry.id if entry else eid
            name = None
            if entry and entry.device_id and (dev := dev_reg.async_get(entry.device_id)):
                name = dev.name_by_user or dev.name
            if not name and entry:
                name = entry.name or entry.original_name
            if not name and (state := self.hass.states.get(eid)):
                name = state.name
            self.trv_names[eid] = name or eid

    def offset_for(self, entity_id: str, level: int) -> float:
        """Calibration offset of one TRV for a heating level."""
        return float(self.trv_offsets[self.trv_keys[entity_id]][str(level)])

    @callback
    def async_set_trv_offset(self, entity_id: str, level: int, value: float) -> None:
        """Change one TRV's offset for one level."""
        self.trv_offsets[self.trv_keys[entity_id]][str(level)] = float(value)
        self._save()
        self.async_evaluate(f"offset {entity_id} L{level}")

    @callback
    def async_evaluate(self, reason: str = "") -> None:
        """Run one control step. Cheap - no I/O unless an output changes."""
        if not self._started:
            return
        now_dt = dt_util.now()
        now = now_dt.timestamp()

        if self.boost_until is not None and now_dt >= self.boost_until:
            _LOGGER.debug("%s: boost finished", self.entry.title)
            self.boost_until = None
            self._save()

        if self.override_until is not None and now_dt >= self.override_until:
            _LOGGER.debug("%s: override expired", self.entry.title)
            self.override_temp = self.override_until = None
            self._save()

        target, source = self._resolve_target(now_dt)
        temp = self._read_room_temp(now)
        self._update_window(now)
        self._update_outdoor()
        while self._samples and self._samples[0][0] < now - TREND_HISTORY_S:
            self._samples.popleft()
        slope = compute_slope(list(self._samples), now, TREND_WINDOW_S)

        decision = decide(
            temp=temp,
            target=target,
            state=self._loop,
            settings=self.loop_settings(),
            now=now,
            active=self.hvac_mode != HVACMode.OFF,
            window_open=self.window_open,
            outdoor_warm=self.outdoor_warm,
            boost=self.boost_until is not None,
            slope_per_h=slope,
        )
        prev = self.decision
        if apply_decision(self._loop, decision, now):
            self.last_level_change = now_dt
            self._save()
        if prev is None or prev.status != decision.status or prev.level != decision.level:
            _LOGGER.info(
                "%s: %s level %s (T=%s target=%s/%s) - %s [%s]",
                self.entry.title,
                decision.status,
                decision.level,
                temp,
                target,
                source,
                decision.reason,
                reason,
            )

        self.decision = decision
        self.target, self.target_source = target, source
        self.room_temp, self.slope = temp, slope
        self._update_desired(decision, target)
        self._update_covers(decision)
        self._schedule_apply()
        self._notify()

    def _update_desired(self, decision: Decision, target: float) -> None:
        if decision.status == Status.FALLBACK and _now_ts() - self._started_at < STARTUP_GRACE_S:
            # Right after a restart the room sensor may simply not have reported
            # yet - leave the TRVs exactly as they are instead of writing twice.
            for act in (*self._modes.values(), *self._setpoints.values(), *self._offsets.values()):
                act.desired = None
            self.commanded_offsets = {}
            return
        if decision.status == Status.BOOST:
            # Max setpoint opens the valve fully whatever the offset is, so the
            # offsets are left alone: 2 writes per TRV for the whole boost.
            for act in self._modes.values():
                act.desired = HVACMode.HEAT
            for act in self._setpoints.values():
                act.desired = SETPOINT_MAX
            for act in self._offsets.values():
                act.desired = None
            self.commanded_offsets = {}
            return
        if decision.status in (Status.OFF, Status.WINDOW, Status.OUTDOOR):
            for act in self._modes.values():
                act.desired = HVACMode.OFF
            for act in self._setpoints.values():
                act.desired = SETPOINT_MIN  # only used if the TRV has no "off"
            for act in self._offsets.values():
                act.desired = None  # leave untouched, saves traffic
            self.commanded_offsets = {}
            return
        for act in self._modes.values():
            act.desired = HVACMode.HEAT
        for act in self._setpoints.values():
            act.desired = target
        offsets: dict[str, float | None] = {}
        for eid, act in self._offsets.items():
            act.desired = (
                self.fallback_offset
                if decision.status == Status.FALLBACK
                else self.offset_for(eid, decision.level)
            )
            offsets[eid] = act.desired
        self.commanded_offsets = offsets

    # ------------------------------------------------------------------ #
    # Curtains
    # ------------------------------------------------------------------ #

    def _daylight(self) -> bool:
        if not self.cover_daylight_only:
            return True
        sun = self.hass.states.get("sun.sun")
        if sun is not None and sun.state in ("above_horizon", "below_horizon"):
            return sun.state == "above_horizon"
        return is_day(  # no sun integration -> day period of the schedule
            dt_util.now().time(),
            parse_time(self.settings[S_DAY_START]),
            parse_time(self.settings[S_NIGHT_START]),
        )

    @callback
    def _update_covers(self, decision: Decision) -> None:
        """Open closed curtains on a big deficit, close the ones we opened afterwards.

        Episode = one continuous heating period. Each cover is considered at most
        once per episode, so a curtain closed by hand is never reopened.
        """
        if not self.covers:
            return
        allowed = self.covers_enabled and self._daylight()
        status = decision.status
        if allowed and status in (Status.WINDOW, Status.FALLBACK, Status.BOOST):
            return  # leave curtains as they are
        if not allowed or status != Status.HEATING:
            for eid in list(self.covers_owned):
                state = self.hass.states.get(eid)
                if state is not None and state.state in (COVER_OPEN, COVER_OPENING):
                    self._cover_command(eid, SERVICE_CLOSE_COVER)
                self.covers_owned.discard(eid)
            if self._covers_handled:
                self._covers_handled.clear()
                self._save()
            return
        deficit = decision.deficit
        if deficit is None or deficit < float(self.settings[S_COVER_DEFICIT]) - 1e-6:
            return
        for eid in self.covers:
            if eid in self._covers_handled:
                continue
            self._covers_handled.add(eid)
            state = self.hass.states.get(eid)
            if state is not None and state.state == COVER_CLOSED:
                self.covers_owned.add(eid)
                self._cover_command(eid, SERVICE_OPEN_COVER)
            self._save()

    @callback
    def _cover_command(self, entity_id: str, service: str) -> None:
        _LOGGER.info("%s: %s %s", self.entry.title, service, entity_id)
        self._cover_cmd_ts[entity_id] = _now_ts()
        self._save()
        self.hass.async_create_task(
            self.hass.services.async_call(
                COVER_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=False
            )
        )

    # ------------------------------------------------------------------ #
    # Actuation
    # ------------------------------------------------------------------ #

    @callback
    def _schedule_apply(self) -> None:
        if self._apply_task is not None and not self._apply_task.done():
            self._apply_again = True
            return
        self._apply_task = self.entry.async_create_background_task(
            self.hass, self._async_apply(), f"{DOMAIN}_apply_{self.entry.entry_id}"
        )

    async def _async_apply(self) -> None:
        while True:
            self._apply_again = False
            # Order matters: mode first, then setpoint, then offset.
            for act in (*self._modes.values(), *self._setpoints.values(), *self._offsets.values()):
                await self._apply_one(act)
            if not self._apply_again:
                return

    def _resolve_value(self, act: Actuator, state: State) -> Any:
        """Turn the abstract desired value into a concrete one for this entity."""
        if act.desired is None:
            return None
        attrs = state.attributes
        if act.kind == KIND_MODE:
            modes = attrs.get(ATTR_HVAC_MODES) or []
            return act.desired if act.desired in modes else None
        if act.kind == KIND_SETPOINT:
            modes = attrs.get(ATTR_HVAC_MODES) or []
            if act.desired == SETPOINT_MIN:
                if HVACMode.OFF in modes:
                    return None
                return _float(attrs.get(ATTR_MIN_TEMP)) or TEMP_MIN
            if act.desired == SETPOINT_MAX:
                return _float(attrs.get(ATTR_MAX_TEMP)) or TEMP_MAX
            return quantize(
                float(act.desired),
                _float(attrs.get(ATTR_TARGET_TEMP_STEP)) or 0.5,
                _float(attrs.get(ATTR_MIN_TEMP)),
                _float(attrs.get(ATTR_MAX_TEMP)),
            )
        # offset number
        value = quantize(
            float(act.desired),
            _float(attrs.get(ATTR_STEP)),
            _float(attrs.get(ATTR_MIN)),
            _float(attrs.get(ATTR_MAX)),
        )
        if abs(value - float(act.desired)) > 0.01 and not act.warned:
            act.warned = True
            _LOGGER.warning(
                "%s: offset %s clamped to %s by %s range (%s..%s)",
                self.entry.title,
                act.desired,
                value,
                act.entity_id,
                attrs.get(ATTR_MIN),
                attrs.get(ATTR_MAX),
            )
        return value

    @staticmethod
    def _matches(act: Actuator, state: State, value: Any) -> bool:
        attrs = state.attributes
        if act.kind == KIND_MODE:
            return state.state == value
        if act.kind == KIND_SETPOINT:
            actual = _float(attrs.get(ATTR_TEMPERATURE))
            if actual is None:
                return False
            step = _float(attrs.get(ATTR_TARGET_TEMP_STEP)) or 0.5
            return abs(actual - float(value)) < max(SETPOINT_MIN_TOLERANCE, step / 2 + 0.001)
        actual = _float(state.state)
        if actual is None:
            return False
        step = _float(attrs.get(ATTR_STEP)) or 0.1
        return abs(actual - float(value)) < max(0.05, step / 2 + 0.001)

    async def _apply_one(self, act: Actuator) -> None:
        state = self.hass.states.get(act.entity_id)
        if state is None or state.state in BAD_STATES:
            return
        value = self._resolve_value(act, state)
        if value is None:
            act.attempts = 0
            return
        if self._matches(act, state, value):
            act.written = value
            act.attempts = 0
            return
        now = _now_ts()
        if act.written == value:
            if now - act.last_write < VERIFY_DELAY_S:
                return  # give the TRV time to confirm
            if act.attempts >= MAX_ATTEMPTS and now - act.last_write < BACKOFF_S:
                return
        else:
            act.attempts = 0
        if act.attempts == MAX_ATTEMPTS:
            _LOGGER.warning(
                "%s: %s does not confirm %s=%s after %d attempts, backing off to every %d min",
                self.entry.title,
                act.entity_id,
                act.kind,
                value,
                MAX_ATTEMPTS,
                BACKOFF_S // 60,
            )
        await self._write(act, value)
        act.written = value
        act.last_write = _now_ts()
        act.attempts += 1
        await asyncio.sleep(WRITE_SPACING_S)

    async def _write(self, act: Actuator, value: Any) -> None:
        if act.kind == KIND_MODE:
            domain, service, data = CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, {ATTR_HVAC_MODE: value}
        elif act.kind == KIND_SETPOINT:
            domain, service, data = (
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_TEMPERATURE: value},
            )
        else:
            domain, service, data = NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_VALUE: value}
        _LOGGER.debug("%s: write %s %s=%s", self.entry.title, act.entity_id, act.kind, value)
        self.writes += 1
        try:
            async with asyncio.timeout(30):
                await self.hass.services.async_call(
                    domain, service, {ATTR_ENTITY_ID: act.entity_id, **data}, blocking=True
                )
        except Exception as err:  # noqa: BLE001 - any failure is retried later
            _LOGGER.warning(
                "%s: writing %s=%s to %s failed: %s",
                self.entry.title,
                act.kind,
                value,
                act.entity_id,
                err,
            )
        self._notify(force=True)

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #

    @property
    def pending_writes(self) -> list[str]:
        """Actuators whose state does not (yet) match the desired value."""
        out = []
        for act in (*self._modes.values(), *self._setpoints.values(), *self._offsets.values()):
            state = self.hass.states.get(act.entity_id)
            if state is None or state.state in BAD_STATES:
                if act.desired is not None:
                    out.append(f"{act.entity_id} (unavailable)")
                continue
            value = self._resolve_value(act, state)
            if value is not None and not self._matches(act, state, value):
                out.append(f"{act.entity_id} {act.kind}={value}")
        return out
