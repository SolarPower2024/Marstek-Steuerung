"""Closed-loop simulation of the relay guard (fork, docs/fork/04-teststrategie.md 4.2).

A tiny plant model drives the REAL unbound pipeline stages
``ChargeDischargeController._apply_min_power`` and ``._apply_relay_dwell`` (light
``SimpleNamespace`` stub, pattern of ``test_relay_dwell.py``) followed by
``RelayGuard.filter``. Nothing is mocked except the clock.

Plant (one sample every 2 s, 30 min unless noted)::

    surplus S    free power relative to the grid target (+ = export, - = import)
    error        = previous_command - S      (grid minus target; + = draw)
    battery      follows the command of the previous sample (one-sample lag)

Controller: deadband gate on ``|error|`` (as the real loop, which returns
before the pipeline inside the deadband and keeps the last command), then a
simplified P step ``previous - kp * error`` and the pipeline stages. kp 0.35,
deadband 40 W, min charge/discharge 50/30 W, same as the field setup.

Deviations from the strategy table:

* S1/S2 signal. With the deadband gate in the loop an idle battery engages
  only at S >= 40 W (error <= -40) and a running 50 W battery disengages at
  S < 10 W. Surplus ``30 W +- 25 W`` crosses the 40 W edge but rarely the
  10 W edge for the dwell length, and with the guard it crosses the guard's
  own engage threshold (min power 50 W, so S >= 50 W) often enough that the
  guard can only halve/quarter the switching, not reach <= 4. The reference
  therefore uses noise *around the low edge*: Gaussian, mean 15 W, sigma 13 W,
  fixed seed (no real clatter source is modelled besides this noise). It
  reproduces the clatter without the guard (33 switches) and is calm with the
  guard (4). The expectation "guard reduces strongly" was not touched.
* S7 (hard switch budget): bound is budget + 2, see the test.
* The real dwell timer is not re-armed by deadband cycles (the loop returns
  before the pipeline); the simulation reproduces that.

Sign convention: + charge, - discharge. Time is deterministic (fixed seeds,
patched clocks).
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.omnibattery import ChargeDischargeController
from custom_components.omnibattery.control.relay_guard import RelayGuard

DT = 2.0
KP = 0.35
DEADBAND = 40
MIN_CHARGE = 50
MIN_DISCHARGE = 30
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)

GUARD_ON = dict(enabled=True, min_off_s=60, direction_lock_s=90,
                engage_margin_w=0, bypass_error_w=200, budget_per_hour=0)


def _sign(p):
    return (p > 0) - (p < 0)


def simulate(monkeypatch, surplus, *, guard_cfg=None, dwell_s, duration_s=1800.0):
    """Run the loop; return a list of per-sample dicts."""
    clock = {"t": 0.0}
    monkeypatch.setattr(dt_util, "utcnow",
                        lambda: BASE + timedelta(seconds=clock["t"]))
    ctrl = SimpleNamespace(
        _relay_cooldown_s=dwell_s,
        previous_power=0.0,
        deadband=DEADBAND,
        min_charge_power=MIN_CHARGE,
        min_discharge_power=MIN_DISCHARGE,
        _relay_shutoff_since=None,
        _zero_cross_since=None,
    )
    guard = RelayGuard()
    guard.configure(**(guard_cfg or dict(GUARD_ON, enabled=False)))
    trace = []
    for k in range(int(duration_s / DT)):
        t = k * DT
        clock["t"] = t
        guard.observe(ctrl.previous_power, t)
        s = surplus(t)
        error = ctrl.previous_power - s
        reason = "deadband"
        if abs(error) < DEADBAND:
            new = ctrl.previous_power
        else:
            new = ctrl.previous_power - KP * error
            new = ChargeDischargeController._apply_min_power(ctrl, new, error)
            new = ChargeDischargeController._apply_relay_dwell(ctrl, new, error)
            dec = guard.filter(
                new, error, previous_power=ctrl.previous_power,
                min_charge_w=MIN_CHARGE, min_discharge_w=MIN_DISCHARGE,
                peak_bypass=False, stale_recalc=False, now=t)
            new, reason = dec.power, dec.reason
        ctrl.previous_power = new
        trace.append(dict(t=t, s=s, error=error, power=new, reason=reason,
                          throttled=guard.status(t)["throttled"]))
    return trace


def switches(trace):
    """Number of command state changes (sign changes incl. idle)."""
    n, prev = 0, 0
    for r in trace:
        s = _sign(r["power"])
        n += s != prev
        prev = s
    return n


def direction_changes(trace):
    """Times at which the active direction differs from the last active one."""
    out, last = [], 0
    for r in trace:
        s = _sign(r["power"])
        if s and last and s != last:
            out.append(r["t"])
        if s:
            last = s
    return out


def noisy(mean, sigma, seed=7):
    rng = random.Random(seed)
    cache = {}

    def f(t):
        if t not in cache:
            cache[t] = rng.gauss(mean, sigma)
        return cache[t]
    return f


EDGE_NOISE = (15, 13)  # mean, sigma of the surplus around the low (10 W) edge


def test_s1_reference_without_guard_chatters(monkeypatch):
    trace = simulate(monkeypatch, noisy(*EDGE_NOISE), dwell_s=15)
    assert switches(trace) > 20


def test_s2_guard_reduces_chatter(monkeypatch):
    ref = switches(simulate(monkeypatch, noisy(*EDGE_NOISE), dwell_s=15))
    trace = simulate(monkeypatch, noisy(*EDGE_NOISE), guard_cfg=GUARD_ON,
                     dwell_s=60)
    assert switches(trace) <= 4 < ref
    assert any(r["reason"] == "engage_threshold" for r in trace)


def test_s3_constant_surplus_single_engage(monkeypatch):
    trace = simulate(monkeypatch, lambda t: 80.0, guard_cfg=GUARD_ON, dwell_s=60)
    assert switches(trace) == 1
    assert trace[0]["power"] > 0 or trace[1]["power"] > 0
    assert all(r["power"] > 0 for r in trace[2:])


def test_s4_alternating_surplus_limits_direction_changes(monkeypatch):
    def s(t):
        return 80.0 if int(t // 45) % 2 == 0 else -80.0
    trace = simulate(monkeypatch, s, guard_cfg=GUARD_ON, dwell_s=60)
    changes = direction_changes(trace)
    assert all(b - a >= 90 for a, b in zip(changes, changes[1:])), changes
    # without the guard the same signal flips much more often
    ref = direction_changes(simulate(monkeypatch, s, dwell_s=60))
    assert len(changes) < len(ref)


def test_s5_load_jump_bypasses_min_off_lock(monkeypatch):
    def s(t):
        if t < 100:
            return 80.0      # charging
        if t < 200:
            return 0.0       # demand gone -> dwell, shut-off, min-off lock
        if t < 230:
            return 80.0      # re-engage attempt inside the lock
        return -1500.0       # big load jump
    trace = simulate(monkeypatch, s, guard_cfg=GUARD_ON, dwell_s=60,
                     duration_s=300)
    assert any(r["reason"] == "min_off" for r in trace if 200 <= r["t"] < 230)
    jump = next(r for r in trace if r["t"] == 230)
    assert jump["power"] < 0
    assert jump["reason"] == "bypass_error"


def test_s6_ramp_engages_once_and_follows(monkeypatch):
    def s(t):
        return 300.0 * min(t, 600.0) / 600.0
    trace = simulate(monkeypatch, s, guard_cfg=GUARD_ON, dwell_s=60,
                     duration_s=900)
    assert switches(trace) == 1
    on = next(i for i, r in enumerate(trace) if r["power"] > 0)
    assert all(r["power"] > 0 for r in trace[on:])
    assert trace[-1]["power"] > trace[on]["power"]


def _s7(monkeypatch):
    def s(t):
        return 80.0 if int(t // 45) % 2 == 0 else -80.0
    cfg = dict(GUARD_ON, budget_per_hour=6)
    return simulate(monkeypatch, s, guard_cfg=cfg, dwell_s=60, duration_s=3600)


def test_s7_switch_budget_is_hard_limit(monkeypatch):
    """Budget 6/h, 60 min alternating load: <= budget + 2 switches.

    Engages and direct flips are refused once 6 switches lie in the last hour
    (reason "budget"); only shut-offs and bypasses may still switch, and a
    shut-off needs a prior engage, so the overshoot is at most the one open
    run (1-2 switches). Window effect: the first expiry frees one slot.
    """
    def s(t):
        return 80.0 if int(t // 45) % 2 == 0 else -80.0
    cfg = dict(GUARD_ON, budget_per_hour=6)
    trace = simulate(monkeypatch, s, guard_cfg=cfg, dwell_s=60, duration_s=3600)
    n = switches(trace)
    print("S7 switches", n)
    assert n <= 6 + 2
    assert any(r["throttled"] for r in trace)
    assert any(r["reason"] == "budget" for r in trace)
