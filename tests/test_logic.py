"""Unit tests for the pure control logic."""

import importlib.util
import sys
from datetime import UTC, datetime, time
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "trv_logic",
    Path(__file__).parents[1] / "custom_components" / "ha_trv_thermostat" / "logic.py",
)
L = importlib.util.module_from_spec(_spec)
sys.modules["trv_logic"] = L
_spec.loader.exec_module(L)

S = L.LoopSettings(
    hyst_on=0.3,
    hyst_off=0.1,
    thresholds=L.build_thresholds(0.6, 1.5),
    level_hyst=0.2,
    min_hold_s=300,
)


def run(temp, state, now=10_000, target=21.0, settings=S, **kw):
    d = L.decide(temp=temp, target=target, state=state, settings=settings, now=now, **kw)
    L.apply_decision(state, d, now)
    return d


def test_build_thresholds():
    assert L.build_thresholds(0.6, 1.5) == (0.6, 1.5)
    assert L.build_thresholds(0.6, 0) == (0.6,)
    assert L.build_thresholds(0, 1.5) == ()
    assert L.build_thresholds(0.6, 0.5) == (0.6,)


def test_idle_inside_band():
    st = L.LoopState()
    assert run(20.8, st).level == 0  # deficit 0.2 < hyst_on
    assert run(21.05, st).level == 0


def test_start_level1_and_level2():
    st = L.LoopState()
    d = run(20.7, st)  # deficit 0.3 -> start, <= 0.6 -> level 1
    assert (d.level, d.status) == (1, L.Status.HEATING)

    st = L.LoopState()
    assert run(20.4, st).level == 1  # deficit exactly 0.6 -> still level 1 ("max 0.6")
    st = L.LoopState()
    assert run(20.3, st).level == 2  # deficit 0.7 -> level 2
    st = L.LoopState()
    assert run(19.4, st).level == 3  # deficit 1.6 -> level 3


def test_level_hysteresis_down():
    st = L.LoopState(level=2, level_since=0)
    # deficit 0.5 > 0.6-0.2 -> stay at 2
    assert run(20.5, st).level == 2
    # deficit 0.35 < 0.4 -> drop to 1
    assert run(20.65, st, now=20_000).level == 1
    # going back up needs full 0.6
    assert run(20.5, st, now=30_000).level == 1
    assert run(20.35, st, now=40_000).level == 2


def test_stop_at_target_plus_hyst_off():
    st = L.LoopState(level=1, level_since=0)
    assert run(21.05, st).level == 1  # below 21.1
    d = run(21.1, st)
    assert (d.level, d.status) == (0, L.Status.IDLE)
    # no restart until deficit >= hyst_on again
    assert run(20.8, st, now=20_000).level == 0
    assert run(20.7, st, now=20_100).level == 1


def test_min_hold_blocks_change():
    st = L.LoopState()
    assert run(20.6, st, now=1000).level == 1
    d = run(21.2, st, now=1100)  # target reached after 100 s -> held
    assert d.level == 1 and d.held
    d = run(21.2, st, now=1300)
    assert d.level == 0 and not d.held


def test_special_states_reset_hold():
    st = L.LoopState()
    run(20.0, st, now=1000)
    assert st.level == 2
    d = run(20.0, st, now=1010, window_open=True)
    assert d.status == L.Status.WINDOW and st.level == 0 and st.level_since is None
    # window closed 20 s later -> immediate restart, no hold
    assert run(20.0, st, now=1030).level == 2


def test_fallback_and_off():
    st = L.LoopState(level=1, level_since=0)
    assert run(None, st).status == L.Status.FALLBACK
    assert st.level == 0
    assert run(19.0, st, active=False).status == L.Status.OFF


def test_lookahead_stops_early():
    s = L.LoopSettings(hyst_on=0.3, hyst_off=0.1, thresholds=(0.6,), min_hold_s=0, lookahead_min=15)
    st = L.LoopState(level=1, level_since=0)
    # 20.9, rising 1 degC/h -> +0.25 in 15 min -> 21.15 >= 21.1 -> stop
    d = run(20.9, st, settings=s, slope_per_h=1.0)
    assert d.level == 0 and "trend" in d.reason
    # falling slope never triggers early stop
    st = L.LoopState(level=1, level_since=0)
    assert run(20.9, st, settings=s, slope_per_h=-1.0).level == 1


def test_slope():
    # 0.1 degC every 6 min = 1 degC/h
    samples = [(i * 360.0, 20.0 + 0.1 * i) for i in range(6)]
    slope = L.compute_slope(samples, now=5 * 360.0 + 1)
    assert abs(slope - 1.0) < 0.15
    # single old sample, held -> 0
    assert L.compute_slope([(0.0, 20.0)], now=3000.0) in (None, 0.0)
    assert L.compute_slope([], now=10.0) is None


def test_schedule():
    d, n = time(6, 0), time(22, 0)
    assert L.is_day(time(12), d, n)
    assert not L.is_day(time(23), d, n)
    assert not L.is_day(time(5, 59), d, n)
    # wrap: day from 22 to 6 (weird but allowed)
    assert L.is_day(time(23), n, d)
    tz = UTC
    now = datetime(2026, 11, 9, 21, 30, tzinfo=tz)
    assert L.next_transition(now, d, n) == datetime(2026, 11, 9, 22, 0, tzinfo=tz)
    now = datetime(2026, 11, 9, 23, 0, tzinfo=tz)
    assert L.next_transition(now, d, n) == datetime(2026, 11, 10, 6, 0, tzinfo=tz)


def test_quantize():
    assert L.quantize(-5, 0.1, -12.8, 12.7) == -5.0
    assert L.quantize(-15, 0.1, -12.8, 12.7) == -12.8
    assert L.quantize(3.3, 1, -9, 9) == 3.0
    assert L.quantize(21.3, 0.5, 5, 30) == 21.5
    assert L.quantize(2.0, 0.2, -12.8, 12.7) == 2.0
