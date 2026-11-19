"""Pure control logic for HA TRV Thermostat.

No Home Assistant imports here on purpose - everything in this module is
deterministic and unit-testable with plain pytest.

Control model
-------------
* The room sensor is the only source of truth for temperature.
* The TRV always gets the real target as its setpoint. Heating is forced or
  blocked purely by the TRV's local temperature calibration (offset):
    - level 0  -> idle offset (positive, TRV thinks it's warm -> valve closed)
    - level 1+ -> negative offsets (TRV thinks it's cold -> valve opens)
* Heating starts when   T <= target - hyst_on
  Heating stops when    T >= target + hyst_off   (or predicted T, see lookahead)
* While heating, the level is picked from the deficit (target - T) with its own
  hysteresis so the level does not flap around a threshold.
* Every level change is held for at least ``min_hold_s`` - this caps how often
  anything is written to Zigbee, regardless of sensor noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum

EPS = 1e-6


class Status(StrEnum):
    """Controller status."""

    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"
    WINDOW = "window_open"
    OUTDOOR = "outdoor_warm"
    FALLBACK = "sensor_lost"


SPECIAL_STATUSES = (Status.OFF, Status.WINDOW, Status.OUTDOOR, Status.FALLBACK)


@dataclass(frozen=True)
class LoopSettings:
    """Settings used by the decision function."""

    hyst_on: float = 0.3
    hyst_off: float = 0.1
    # Deficit thresholds for level 2, 3, ... (level 1 is implicit at 0).
    # Must be strictly increasing; use ``build_thresholds`` to sanitise.
    thresholds: tuple[float, ...] = (0.6,)
    level_hyst: float = 0.2
    min_hold_s: float = 300.0
    lookahead_min: float = 0.0

    @property
    def max_level(self) -> int:
        """Highest available heating level."""
        return 1 + len(self.thresholds)


@dataclass
class LoopState:
    """Mutable state carried between decisions."""

    level: int = 0
    level_since: float | None = None  # epoch seconds, None = no hold active


@dataclass(frozen=True)
class Decision:
    """Result of one evaluation."""

    level: int
    status: Status
    deficit: float | None
    predicted: float | None
    held: bool
    reason: str


def build_thresholds(*thresholds: float) -> tuple[float, ...]:
    """Return enabled thresholds (>0), cut at the first non-increasing one.

    ``build_thresholds(0.6, 1.5)`` -> (0.6, 1.5)  -> levels 1..3
    ``build_thresholds(0.6, 0)``   -> (0.6,)      -> levels 1..2
    ``build_thresholds(0, 1.5)``   -> ()          -> level 1 only
    """
    out: list[float] = []
    for thr in thresholds:
        if thr is None or thr <= 0:
            break
        if out and thr <= out[-1] + EPS:
            break
        out.append(float(thr))
    return tuple(out)


def pick_level(deficit: float, current: int, settings: LoopSettings) -> int:
    """Pick heating level (>=1) for a given deficit, with level hysteresis."""
    level = 1
    for idx, thr in enumerate(settings.thresholds):
        lvl = idx + 2
        # Going up needs the full threshold, staying needs threshold - hyst.
        effective = thr - settings.level_hyst if current >= lvl else thr
        if deficit > effective + EPS:
            level = lvl
        else:
            break
    return level


def decide(
    *,
    temp: float | None,
    target: float,
    state: LoopState,
    settings: LoopSettings,
    now: float,
    active: bool = True,
    window_open: bool = False,
    outdoor_warm: bool = False,
    slope_per_h: float | None = None,
) -> Decision:
    """Decide the heating level. Pure function."""
    if not active:
        return Decision(0, Status.OFF, None, None, False, "off")
    if window_open:
        return Decision(0, Status.WINDOW, None, None, False, "window open")
    if outdoor_warm:
        return Decision(0, Status.OUTDOOR, None, None, False, "warm outside")
    if temp is None:
        return Decision(0, Status.FALLBACK, None, None, False, "no room temperature")

    deficit = target - temp
    current = max(0, min(state.level, settings.max_level))

    predicted = temp
    if settings.lookahead_min > 0 and slope_per_h is not None and slope_per_h > 0:
        predicted = temp + slope_per_h * settings.lookahead_min / 60.0

    if current == 0:
        if deficit >= settings.hyst_on - EPS:
            wanted = pick_level(deficit, 0, settings)
            reason = f"start: deficit {deficit:.2f} >= {settings.hyst_on:.2f}"
        else:
            wanted = 0
            reason = "idle"
    else:
        stop_at = target + settings.hyst_off
        if temp >= stop_at - EPS:
            wanted = 0
            reason = f"stop: {temp:.2f} >= {stop_at:.2f}"
        elif predicted >= stop_at - EPS:
            wanted = 0
            reason = f"stop (trend): predicted {predicted:.2f} >= {stop_at:.2f}"
        else:
            wanted = pick_level(deficit, current, settings)
            reason = f"heating: deficit {deficit:.2f}"

    held = False
    if (
        wanted != current
        and state.level_since is not None
        and now - state.level_since < settings.min_hold_s
    ):
        held = True
        # No countdown in the text: the reason is a state attribute and must not
        # change every tick (recorder churn).
        reason = f"hold (min {settings.min_hold_s / 60:g} min) -> {wanted}: {reason}"
        wanted = current

    status = Status.HEATING if wanted > 0 else Status.IDLE
    return Decision(wanted, status, deficit, predicted, held, reason)


def apply_decision(state: LoopState, decision: Decision, now: float) -> bool:
    """Update loop state with a decision. Returns True if the level changed."""
    if decision.status in SPECIAL_STATUSES:
        changed = state.level != 0
        state.level = 0
        state.level_since = None  # no hold after leaving a special state
        return changed
    if decision.level != state.level:
        state.level = decision.level
        state.level_since = now
        return True
    return False


def outdoor_cutoff(outdoor: float | None, threshold: float, hyst: float, active: bool) -> bool:
    """Heating season switch with hysteresis.

    Becomes active (no heating) at ``outdoor >= threshold`` and releases only at
    ``outdoor <= threshold - hyst``. Unknown outdoor temperature keeps the
    current state, so sensor blips never toggle the TRVs.
    """
    if outdoor is None:
        return active
    if active:
        return outdoor > threshold - hyst + EPS
    return outdoor >= threshold - EPS


# --------------------------------------------------------------------------- #
# Trend
# --------------------------------------------------------------------------- #


def compute_slope(
    samples: list[tuple[float, float]],
    now: float,
    window_s: float = 1200.0,
    min_span_s: float = 300.0,
) -> float | None:
    """Least-squares slope in degC/h over the last ``window_s`` seconds.

    ``samples`` are (epoch_s, temp) sorted by time. Sensors report on change,
    so the signal is treated as a step function: the value valid at the window
    start is used as an anchor and the last value is held until ``now``.
    """
    if not samples:
        return None
    start = now - window_s
    anchor: tuple[float, float] | None = None
    points: list[tuple[float, float]] = []
    for ts, val in samples:
        if ts < start:
            anchor = (start, val)
        elif ts <= now:
            points.append((ts, val))
    if anchor is not None:
        points.insert(0, anchor)
    if not points:
        return None
    if now - points[-1][0] > 1:
        points.append((now, points[-1][1]))
    if len(points) < 3 or points[-1][0] - points[0][0] < min_span_s:
        return None
    n = len(points)
    mean_t = sum(p[0] for p in points) / n
    mean_v = sum(p[1] for p in points) / n
    num = sum((t - mean_t) * (v - mean_v) for t, v in points)
    den = sum((t - mean_t) ** 2 for t, _ in points)
    if den <= 0:
        return None
    return num / den * 3600.0


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


def is_day(now_t: time, day_start: time, night_start: time) -> bool:
    """True if ``now_t`` falls into the day period. Handles wrap past midnight."""
    if day_start == night_start:
        return True
    if day_start < night_start:
        return day_start <= now_t < night_start
    return now_t >= day_start or now_t < night_start


def next_transition(now: datetime, day_start: time, night_start: time) -> datetime | None:
    """Return the next day/night switch moment (tz-aware like ``now``)."""
    if day_start == night_start:
        return None
    candidates = []
    for add_days in (0, 1):
        base = (now + timedelta(days=add_days)).date()
        for t in (day_start, night_start):
            cand = datetime.combine(base, t, tzinfo=now.tzinfo)
            if cand > now:
                candidates.append(cand)
    return min(candidates) if candidates else None


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #


def quantize(value: float, step: float | None, vmin: float | None, vmax: float | None) -> float:
    """Clamp to [vmin, vmax] and round to ``step``."""
    if vmin is not None:
        value = max(vmin, value)
    if vmax is not None:
        value = min(vmax, value)
    if step and step > 0:
        base = vmin if vmin is not None else 0.0
        value = base + round((value - base) / step) * step
        if vmax is not None and value > vmax + EPS:
            value -= step
        if vmin is not None and value < vmin - EPS:
            value += step
    return round(value, 3)
