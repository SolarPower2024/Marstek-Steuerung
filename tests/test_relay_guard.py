"""Tests for ``RelayGuard`` — pure anti-chatter logic for the battery relay.

No Home Assistant, no fixtures: time is passed in as a float (seconds).
Sign convention: power + = charge, - = discharge; error + = grid draw above
target (discharge demand), error - = surplus (charge demand).

Defaults unless stated otherwise: min_off_s=60, direction_lock_s=90,
engage_margin_w=0, bypass_error_w=200, budget_per_hour=0 (no budget),
min charge 50 W, min discharge 30 W.
"""
from __future__ import annotations

from custom_components.omnibattery.control.relay_guard import (
    GuardDecision,
    RelayGuard,
)


def _guard(**over):
    cfg = dict(
        enabled=True,
        min_off_s=60,
        direction_lock_s=90,
        engage_margin_w=0,
        bypass_error_w=200,
        budget_per_hour=0,
    )
    cfg.update(over)
    g = RelayGuard()
    g.configure(**cfg)
    return g


def _filt(g, requested, error, now, *, previous=0.0, min_charge=50.0,
          min_discharge=30.0, peak=False, stale=False):
    return g.filter(
        requested,
        error,
        previous_power=previous,
        min_charge_w=min_charge,
        min_discharge_w=min_discharge,
        peak_bypass=peak,
        stale_recalc=stale,
        now=now,
    )


def _shutoff_at_zero(g, direction=50.0):
    """Battery runs in ``direction`` until t=0, idle from t=0."""
    g.observe(direction, -100.0)
    g.observe(0.0, 0.0)


def test_g1_disabled_passes_everything():
    g = _guard(enabled=False)
    _shutoff_at_zero(g)
    d = _filt(g, 50, -30, 5)
    assert isinstance(d, GuardDecision)
    assert (d.power, d.reason, d.remaining_s) == (50, "disabled", 0)


def test_g2_engage_threshold_blocks_small_surplus():
    d = _filt(_guard(), 50, -30, 0)
    assert (d.power, d.reason) == (0, "engage_threshold")


def test_g3_engage_threshold_passes_at_min_power():
    d = _filt(_guard(), 50, -50, 0)
    assert (d.power, d.reason) == (50, "pass")


def test_g4_discharge_threshold_edge():
    assert _filt(_guard(), -30, 29, 0).power == 0
    assert _filt(_guard(), -30, 30, 0).power == -30


def test_g5_engage_margin():
    g = _guard(engage_margin_w=20)
    assert _filt(g, 50, -60, 0).power == 0
    assert _filt(g, 50, -70, 0).power == 50


def test_g6_min_power_zero_disables_hysteresis():
    d = _filt(_guard(), 50, -5, 0, min_charge=0)
    assert (d.power, d.reason) == (50, "pass")
    d = _filt(_guard(), -30, 1, 0, min_discharge=0)
    assert d.power == -30


def test_g7_min_off_blocks_early_reengage():
    g = _guard()
    _shutoff_at_zero(g)
    d = _filt(g, 50, -100, 30)
    assert (d.power, d.reason) == (0, "min_off")
    assert abs(d.remaining_s - 30) < 0.01


def test_g8_min_off_expired():
    g = _guard()
    _shutoff_at_zero(g)
    d = _filt(g, 50, -100, 61)
    assert (d.power, d.reason) == (50, "pass")


def test_g9_large_error_bypasses_min_off():
    g = _guard()
    _shutoff_at_zero(g)
    d = _filt(g, 50, -250, 30)
    assert (d.power, d.reason) == (50, "bypass_error")


def test_g10_peak_bypass():
    g = _guard()
    _shutoff_at_zero(g, direction=50.0)
    d = _filt(g, -30, 50, 30, peak=True)
    assert (d.power, d.reason) == (-30, "bypass_peak")


def test_g11_direction_lock_after_charge():
    g = _guard()
    _shutoff_at_zero(g, direction=50.0)
    d = _filt(g, -30, 100, 70)
    assert (d.power, d.reason) == (0, "direction_lock")
    assert abs(d.remaining_s - 20) < 0.01


def test_g12_direction_lock_expired():
    g = _guard()
    _shutoff_at_zero(g, direction=50.0)
    d = _filt(g, -30, 100, 91)
    assert (d.power, d.reason) == (-30, "pass")


def test_g13_same_direction_has_no_direction_lock():
    g = _guard()
    _shutoff_at_zero(g, direction=50.0)
    d = _filt(g, 50, -100, 70)
    assert (d.power, d.reason) == (50, "pass")


def test_g14_direct_flip_blocked_when_direction_young():
    g = _guard()
    g.observe(0.0, 0.0)
    g.observe(50.0, 10.0)  # charging since t=10
    d = _filt(g, -30, 100, 30, previous=50.0)
    assert (d.power, d.reason) == (0, "direction_lock")
    assert abs(d.remaining_s - 70) < 0.01


def test_g15_direct_flip_allowed_when_direction_old():
    g = _guard()
    g.observe(0.0, 0.0)
    g.observe(50.0, 10.0)
    d = _filt(g, -30, 100, 110, previous=50.0)
    assert (d.power, d.reason) == (-30, "pass")
    # Gap decision: after the lock, the engage threshold of the new direction
    # still applies to a direct flip.
    d = _filt(g, -30, 10, 110, previous=50.0)
    assert (d.power, d.reason) == (0, "engage_threshold")


def test_g16_zero_and_same_sign_pass_through():
    g = _guard()
    g.observe(0.0, 0.0)
    g.observe(50.0, 10.0)
    assert _filt(g, 0, -10, 20, previous=50.0).power == 0
    d = _filt(g, 200, -300, 20, previous=50.0)
    assert d.power == 200
    d = _filt(g, 200, -20, 20, previous=50.0)
    assert (d.power, d.reason) == (200, "pass")


def test_g17_stale_recalc_leaves_state_untouched():
    g = _guard()
    _shutoff_at_zero(g)
    before = g.status(30)
    d = _filt(g, 50, -100, 30, stale=True)
    assert d.power == 50
    after = g.status(30)
    assert after == before
    # A stale pass must not mask the real state: next real call still blocks.
    assert _filt(g, 50, -100, 30).reason == "min_off"


def _four_switches(g):
    g.observe(0.0, 0.0)
    for p, t in ((50, 60), (0, 120), (50, 180), (0, 240)):
        g.observe(float(p), float(t))


def test_g18_budget_blocks_engage():
    g = _guard(budget_per_hour=4)
    _four_switches(g)
    # min_off (60 s) is long over; only the budget blocks. Oldest switch t=60.
    d = _filt(g, 50, -100, 400)
    assert (d.power, d.reason) == (0, "budget")
    assert abs(d.remaining_s - (60 + 3600 - 400)) < 0.01
    assert g.status(400)["throttled"] is True
    # Direct flip is blocked too; shut-off and same direction are allowed.
    assert _filt(g, -50, 100, 400, previous=50.0).reason == "budget"
    assert _filt(g, 0, 0, 400, previous=50.0).power == 0
    assert _filt(g, 80, -100, 400, previous=50.0).power == 80
    # Bypasses go first.
    assert _filt(g, 50, -300, 400).reason == "bypass_error"
    assert _filt(g, 50, -100, 400, peak=True).reason == "bypass_peak"
    assert _filt(g, 50, -100, 400, stale=True).power == 50
    # Without budget the same situation passes.
    g2 = _guard(budget_per_hour=0)
    _four_switches(g2)
    assert _filt(g2, 50, -100, 400).power == 50
    assert g2.status(400)["throttled"] is False


def test_g19_budget_window_rolls_off():
    g = _guard(budget_per_hour=4)
    _four_switches(g)
    assert _filt(g, 50, -100, 3659).reason == "budget"  # t=60 still inside
    now = 240 + 3661
    assert g.status(now)["switches_last_hour"] == 0
    assert g.status(now)["throttled"] is False
    assert _filt(g, 50, -100, now).power == 50


def test_g20_observe_counts_state_changes():
    g = _guard()
    for i, p in enumerate((0, 50, 50, 0, -30, 0)):
        g.observe(float(p), float(i * 10))
    st = g.status(60)
    assert st["switches_last_hour"] == 4
    assert st["switches_today"] == 4
    assert st["last_switch"] == 50.0


def test_g20b_direct_flip_counts_as_switch():
    g = _guard()
    for i, p in enumerate((0, 50, -30)):
        g.observe(float(p), float(i * 10))
    assert g.status(20)["switches_last_hour"] == 2


def test_g21_status_has_all_keys():
    st = _guard().status(0)
    for key in ("state", "remaining_s", "switches_last_hour", "switches_today",
                "blocked_today", "throttled", "last_switch"):
        assert key in st
    assert st["last_switch"] is None
    assert st["state"] == "pass"


def test_status_reports_last_reason_and_remaining():
    g = _guard()
    _shutoff_at_zero(g)
    _filt(g, 50, -100, 20)
    st = g.status(30)
    assert st["state"] == "min_off"
    assert abs(st["remaining_s"] - 30) < 0.01


def test_blocked_today_counts_episodes_and_reset_daily():
    g = _guard()
    _shutoff_at_zero(g)
    for t in (10, 20, 30):  # one continuous blocking episode
        _filt(g, 50, -100, t)
    assert g.status(30)["blocked_today"] == 1
    _filt(g, 50, -100, 70)  # pass ends the episode
    _filt(g, 50, -10, 71)  # engage_threshold: new episode
    assert g.status(71)["blocked_today"] == 2
    g.reset_daily()
    st = g.status(71)
    assert st["blocked_today"] == 0 and st["switches_today"] == 0
    assert st["switches_last_hour"] == 1  # rolling window is unaffected


def test_filter_never_increases_power():
    g = _guard()
    _shutoff_at_zero(g)
    for req in (-500, -30, 0, 50, 500):
        for err in (-300, -60, 0, 60, 300):
            for prev in (-100.0, 0.0, 100.0):
                for t in (5, 50, 200):
                    d = _filt(g, req, err, t, previous=prev)
                    assert d.power in (0, req)
