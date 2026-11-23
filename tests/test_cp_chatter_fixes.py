"""FORK: capacity-protection chatter fixes (log-analyse-2026-11-18.md).

I - a fresh charge command whose AC telemetry still reads 0 W must not look
    like house import and be force-idled.
K - below the SOC threshold and outside shaving, the PD must not discharge
    (raw meter says surplus while the EMA-filtered control value still shows
    import).
Values are taken from the trace of 18.11.2026.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.omnibattery import ChargeDischargeController

from .test_control_sample_scheduling import _main_controller, _state


def _cp_controller(*, previous_power: float, ac_power: float | None):
    ctrl = object.__new__(ChargeDischargeController)
    ctrl.capacity_protection_enabled = True
    ctrl.capacity_protection_excluded_devices = False
    ctrl.capacity_protection_soc_threshold = 40
    ctrl.capacity_protection_limit = 7000
    ctrl.capacity_protection_soc_hysteresis = 0
    ctrl._capacity_protection_soc_latched = False
    data = {"battery_soc": 39}
    if ac_power is not None:
        data["ac_power"] = ac_power  # Marstek: + discharge / - charge
    ctrl.coordinators = [SimpleNamespace(data=data)]
    ctrl._non_responsive = SimpleNamespace(is_excluded=lambda _c: False)
    ctrl.previous_power = previous_power
    ctrl._excluded_included_adjustment = 0.0
    ctrl._setpoint_offsets = {"user_target": 20.0}
    ctrl._setpoint_overrides = {}
    ctrl._capacity_protection_active = False
    ctrl._capacity_protection_force_idle = False
    ctrl._capacity_protection_block_discharge = False
    ctrl._capacity_protection_status = {}
    return ctrl


# --- I ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "previous_power, grid",
    [(374.2, 32.0), (123.4, 30.0), (262.0, 25.6), (191.8, 42.0)],
)
def test_lagging_telemetry_does_not_force_idle_a_surplus_charge(previous_power, grid):
    """cyc 102429/102434/102471/102477: charge commanded, ac0, grid +26..+42 W."""
    ctrl = _cp_controller(previous_power=previous_power, ac_power=0)

    target, _sensor = ctrl._apply_capacity_protection(grid, active_target=20.0)

    assert ctrl._capacity_protection_force_idle is False
    assert ctrl._capacity_protection_status["action"] == "charging"
    assert target == pytest.approx(20.0)


def test_real_import_while_charging_still_force_idles():
    """17.11. 10:45:33: 30 W charge confirmed by telemetry, grid +59 W."""
    ctrl = _cp_controller(previous_power=30.0, ac_power=-30)

    ctrl._apply_capacity_protection(59.0, active_target=20.0)

    assert ctrl._capacity_protection_force_idle is True
    assert ctrl._capacity_protection_status["action"] == "conserving"


def test_measured_charge_above_command_is_kept():
    ctrl = _cp_controller(previous_power=100.0, ac_power=-300)

    ctrl._apply_capacity_protection(150.0, active_target=20.0)

    # house = 150 - 300 = -150 -> surplus
    assert ctrl._capacity_protection_status["action"] == "charging"


def test_discharge_command_keeps_measured_value():
    """Discharge side unchanged: a running discharge with import is stopped."""
    ctrl = _cp_controller(previous_power=-30.0, ac_power=0)

    ctrl._apply_capacity_protection(44.0, active_target=20.0)

    assert ctrl._capacity_protection_force_idle is True


def test_measured_discharge_is_not_raised_to_zero():
    """grid -100 W while discharging 400 W -> house 300 W -> conserving."""
    ctrl = _cp_controller(previous_power=-400.0, ac_power=400)

    ctrl._apply_capacity_protection(-100.0, active_target=20.0)

    assert ctrl._capacity_protection_status["action"] == "conserving"
    assert ctrl._capacity_protection_status["estimated_house_load"] == 300


# --- K: flag ----------------------------------------------------------------

def test_surplus_branch_blocks_discharge():
    """13:41:39: raw 10 W < target 20 W -> surplus branch."""
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)

    ctrl._apply_capacity_protection(
        53.9, active_target=20.0, physical_actual=9.9,
    )

    assert ctrl._capacity_protection_status["action"] == "charging"
    assert ctrl._capacity_protection_block_discharge is True


def test_conserving_blocks_discharge():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)

    ctrl._apply_capacity_protection(120.0, active_target=20.0)

    assert ctrl._capacity_protection_status["action"] == "conserving"
    assert ctrl._capacity_protection_block_discharge is True


def test_peak_exit_hold_keeps_its_discharge(monkeypatch):
    ctrl = _cp_controller(previous_power=-400.0, ac_power=400)
    monkeypatch.setattr(
        ChargeDischargeController, "_peak_exit_hold_active", lambda self: True,
    )

    ctrl._apply_capacity_protection(1300.0, active_target=20.0)

    assert ctrl._capacity_protection_status["action"] == "conserving"
    assert ctrl._capacity_protection_force_idle is False
    assert ctrl._capacity_protection_block_discharge is False


def test_shaving_allows_discharge():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)

    ctrl._apply_capacity_protection(7563.0, active_target=20.0)

    assert ctrl._capacity_protection_status["action"] == "shaving"
    assert ctrl._capacity_protection_block_discharge is False


def test_above_threshold_allows_discharge():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl.coordinators[0].data["battery_soc"] = 40

    ctrl._apply_capacity_protection(120.0, active_target=20.0)

    assert ctrl._capacity_protection_block_discharge is False


def test_flag_is_cleared_when_protection_is_disabled():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl._capacity_protection_block_discharge = True
    ctrl.capacity_protection_enabled = False

    ctrl._apply_capacity_protection(120.0, active_target=20.0)

    assert ctrl._capacity_protection_block_discharge is False


# --- K: clamp in the control cycle -------------------------------------------

def _cycle(*, block: bool, status: dict | None = None, pd_out: float = -500.0):
    reported_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    controller = _main_controller({"state": _state(100, reported_at)}, [])
    controller.previous_power = 0.0
    controller.last_output_sign = 0
    controller._compute_pd_new_power = lambda *_a: pd_out
    controller._capacity_protection_block_discharge = block
    controller._capacity_protection_active = status is not None
    controller._capacity_protection_status = status or {}
    asyncio.run(controller._run_control_cycle(now=reported_at))
    return controller


def test_blocked_discharge_is_clamped_to_zero():
    controller = _cycle(block=True, status={"action": "charging"})

    assert controller.previous_power == 0


def test_charge_passes_while_discharge_is_blocked():
    controller = _cycle(block=True, status={"action": "charging"}, pd_out=250.0)

    assert controller.previous_power == pytest.approx(250.0)


def test_discharge_passes_without_block():
    controller = _cycle(block=False)

    assert controller.previous_power == pytest.approx(-500.0)


def test_shaving_bypass_wins_over_stale_block():
    controller = _cycle(block=True, status={"action": "shaving"})

    assert controller.previous_power == pytest.approx(-500.0)


def test_dwell_cannot_lift_a_blocked_discharge():
    """M - 21.11.2026 11:14:58: p_cp=0, then the dwell held -30 W."""
    reported_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    controller = _main_controller({"state": _state(100, reported_at)}, [])
    controller.previous_power = -266.8
    controller.last_output_sign = -1
    controller._compute_pd_new_power = lambda *_a: -225.7
    controller._apply_relay_dwell = lambda p, e, stale_recalc=False: -30.0
    controller._capacity_protection_block_discharge = True
    controller._capacity_protection_active = True
    controller._capacity_protection_status = {"action": "conserving"}
    asyncio.run(controller._run_control_cycle(now=reported_at))

    assert controller.previous_power == 0


# --- L: SOC release hysteresis -------------------------------------------------

def _set_soc(ctrl, soc):
    ctrl.coordinators[0].data["battery_soc"] = soc


def test_hysteresis_holds_protection_until_threshold_plus_hysteresis():
    """21.11.2026: 39 % -> charge -> 40 % -> discharge -> 39 % ... (6x in 3 h)."""
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl.capacity_protection_soc_hysteresis = 2

    for soc, protected in [(39, True), (40, True), (41, True), (42, False), (41, False)]:
        _set_soc(ctrl, soc)
        ctrl._apply_capacity_protection(120.0, active_target=20.0)
        assert ctrl._capacity_protection_status["active"] is protected, soc
        assert ctrl._capacity_protection_block_discharge is protected, soc


def test_hysteresis_is_on_top_of_the_configured_threshold():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl.capacity_protection_soc_threshold = 30
    ctrl.capacity_protection_soc_hysteresis = 2

    for soc, protected in [(29, True), (31, True), (32, False)]:
        _set_soc(ctrl, soc)
        ctrl._apply_capacity_protection(120.0, active_target=20.0)
        assert ctrl._capacity_protection_status["active"] is protected, soc
    assert ctrl._capacity_protection_status["soc_release"] == 32


def test_hysteresis_does_not_start_protection_above_threshold():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl.capacity_protection_soc_hysteresis = 2
    _set_soc(ctrl, 41)

    ctrl._apply_capacity_protection(120.0, active_target=20.0)

    assert ctrl._capacity_protection_status["active"] is False


def test_zero_hysteresis_keeps_the_old_edge():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl._apply_capacity_protection(120.0, active_target=20.0)
    _set_soc(ctrl, 40)

    ctrl._apply_capacity_protection(120.0, active_target=20.0)

    assert ctrl._capacity_protection_status["active"] is False


def test_soc_limited_query_follows_the_latch():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl.capacity_protection_soc_hysteresis = 2
    ctrl._capacity_protection_coordinators = lambda: ctrl.coordinators
    ctrl._apply_capacity_protection(120.0, active_target=20.0)
    _set_soc(ctrl, 41)

    assert ctrl._is_capacity_protection_soc_limited() is True


def test_disabling_protection_clears_the_latch():
    ctrl = _cp_controller(previous_power=0.0, ac_power=0)
    ctrl.capacity_protection_soc_hysteresis = 2
    ctrl._apply_capacity_protection(120.0, active_target=20.0)
    ctrl.capacity_protection_enabled = False

    ctrl._apply_capacity_protection(120.0, active_target=20.0)

    assert ctrl._capacity_protection_soc_latched is False


@pytest.mark.parametrize(
    "data, expected",
    [({}, 2.0), ({"capacity_protection_soc_hysteresis": None}, 2.0),
     ({"capacity_protection_soc_hysteresis": 0}, 0.0),
     ({"capacity_protection_soc_hysteresis": 3}, 3.0),
     ({"capacity_protection_soc_hysteresis": -1}, 0.0),
     ({"capacity_protection_soc_hysteresis": "x"}, 2.0)],
)
def test_hysteresis_config_value(data, expected):
    from custom_components.omnibattery.const import capacity_protection_soc_hysteresis

    assert capacity_protection_soc_hysteresis(data) == expected
