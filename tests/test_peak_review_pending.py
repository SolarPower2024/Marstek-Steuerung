"""P4/P5: a new real-meter sample re-runs peak shaving while the control sensor is constant."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.omnibattery import ChargeDischargeController
from custom_components.omnibattery.control.grid_view import GridView
from tests.test_control_sample_scheduling import _main_controller, _state
from tests.test_peak_shaving import _controller

LIMIT = 3000.0


class _States:
    def __init__(self, values):
        self.values = values

    def get(self, entity_id):
        value = self.values.get(entity_id)
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return SimpleNamespace(state=str(value), attributes={},
                                   last_reported=datetime.now(timezone.utc))
        return value


def _view(states, value):
    view = GridView()
    view.configure(physical_entity="sensor.real", physical_inverted=False,
                   limit_entity=None, limit_margin_pct=0)
    states.values["sensor.real"] = value
    return view


def _pending_ctrl(*, physical, previous_power=0.0, reads=1, active=False, enabled=True):
    ctrl = _controller(previous_power=previous_power)
    ctrl.capacity_protection_enabled = enabled
    ctrl._capacity_protection_active = active
    states = _States({})
    ctrl.hass = SimpleNamespace(states=states)
    ctrl.offgrid_mode_enabled, ctrl.offgrid_power_sensor = False, None
    ctrl._grid_view = _view(states, physical)
    value = None
    for _ in range(reads):
        value = ctrl._read_physical_grid_w()
    return ctrl, value


def test_without_second_sensor_never_pending():
    ctrl = _controller(previous_power=0.0)
    assert ctrl._peak_review_pending(None) is False
    ctrl._capacity_protection_active = True
    assert ctrl._peak_review_pending(None) is False
    # GridView present but not configured: read yields None.
    ctrl._grid_view = GridView()
    assert ctrl._peak_review_pending(None) is False


def test_value_not_new_is_not_pending():
    ctrl, value = _pending_ctrl(physical=5000.0, reads=2, active=True)
    assert value == pytest.approx(5000.0)
    assert ctrl._peak_review_pending(value) is False


def test_below_limit_and_peak_inactive_is_not_pending():
    ctrl, value = _pending_ctrl(physical=2500.0)
    assert ctrl._peak_review_pending(value) is False


def test_over_limit_is_pending():
    ctrl, value = _pending_ctrl(physical=3500.0)
    assert ctrl._peak_review_pending(value) is True


def test_battery_power_is_subtracted():
    # 2500 W real import while discharging 1000 W -> house 3500 W > 3000 W.
    ctrl, value = _pending_ctrl(physical=2500.0, previous_power=-1000.0)
    assert ctrl._peak_review_pending(value) is True
    # Charging 1000 W: house only 1500 W.
    ctrl, value = _pending_ctrl(physical=2500.0, previous_power=1000.0)
    assert ctrl._peak_review_pending(value) is False


def test_peak_was_active_is_pending_even_below_limit():
    ctrl, value = _pending_ctrl(physical=500.0, active=True)
    assert ctrl._peak_review_pending(value) is True


def test_peak_shaving_off_is_not_pending():
    ctrl, value = _pending_ctrl(physical=9000.0, active=True, enabled=False)
    assert ctrl._peak_review_pending(value) is False


def test_pending_does_not_consume_the_sample():
    ctrl, value = _pending_ctrl(physical=3500.0)
    assert ctrl._peak_review_pending(value) is True
    assert ctrl._peak_review_pending(value) is True


def test_track_physical_sample_elapsed():
    ctrl, value = _pending_ctrl(physical=1000.0)
    assert ChargeDischargeController._track_physical_sample(ctrl, value) is None
    ctrl._last_physical_sample_mono -= 5.0
    ctrl.hass.states.values["sensor.real"] = 1200.0
    value = ctrl._read_physical_grid_w()
    assert ChargeDischargeController._track_physical_sample(ctrl, value) == pytest.approx(5.0, abs=0.5)
    # unchanged value: no new sample
    value = ctrl._read_physical_grid_w()
    assert ChargeDischargeController._track_physical_sample(ctrl, value) is None


# --- control loop ----------------------------------------------------------

def _loop_controller(*, with_physical):
    first_report = datetime.now(timezone.utc)
    control_state = _state(0, first_report)
    pd_calls, writes, cp_calls = [], [], []
    ctrl = _main_controller({"state": control_state}, pd_calls)
    ctrl.previous_power = 0.0
    ctrl.last_output_sign = 0
    states = _States({"sensor.grid_power": control_state})
    ctrl.hass = SimpleNamespace(states=states)
    ctrl.capacity_protection_enabled = True
    ctrl.capacity_protection_limit = LIMIT
    ctrl._capacity_protection_active = False
    ctrl._capacity_protection_status = {}
    ctrl.offgrid_mode_enabled, ctrl.offgrid_power_sensor = False, None
    if with_physical:
        ctrl._grid_view = _view(states, 1000.0)

    def _cp(sensor, target, physical_actual=None):
        # Shaving decision of _apply_capacity_protection (SOC below threshold).
        cp_calls.append(physical_actual)
        if physical_actual is not None and physical_actual - ctrl.previous_power > LIMIT:
            ctrl._capacity_protection_active = True
            ctrl._capacity_protection_status["action"] = "shaving"
            return LIMIT, physical_actual
        ctrl._capacity_protection_status["action"] = "idle"
        return target, sensor

    async def _write(coordinator, charge, discharge):
        writes.append((charge, discharge))

    def _pd(error, sensor_elapsed_s, stale_safety_recalc):
        pd_calls.append((error, sensor_elapsed_s, stale_safety_recalc))
        return ctrl.previous_power - 0.5 * error

    ctrl._apply_capacity_protection = _cp
    ctrl._set_battery_power = _write
    ctrl._compute_pd_new_power = _pd
    ctrl.deadband = 0.0  # keep the stub free of the deadband bookkeeping
    return ctrl, states, first_report, pd_calls, writes, cp_calls


def test_real_meter_over_limit_discharges_while_control_sensor_is_constant():
    ctrl, states, t0, pd_calls, writes, cp_calls = _loop_controller(with_physical=True)
    # Cycle 1: control sample new (0 W on target), real 1000 W.
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=1)))
    assert len(pd_calls) == 1 and ctrl.previous_power == pytest.approx(0.0)
    pd_calls.clear()
    writes.clear()
    # Cycle 2: control sensor unchanged, real meter jumps over the limit.
    states.values["sensor.real"] = 4000.0
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=3)))
    assert len(pd_calls) == 1
    error, _elapsed, stale = pd_calls[0]
    assert error == pytest.approx(4000.0 - LIMIT)
    assert stale is False
    assert ctrl.previous_power == pytest.approx(-500.0)
    assert any(discharge > 0 for _charge, discharge in writes)
    # Cycle 3: nothing new on either meter -> P/D is not reapplied.
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=5)))
    assert len(pd_calls) == 1
    # Cycle 4: real load drops while the control sensor stays constant: the
    # cycle is reviewed (peak was active), so capacity protection runs again.
    calls_before = len(cp_calls)
    states.values["sensor.real"] = 500.0
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=7)))
    assert len(cp_calls) == calls_before + 1


def test_without_second_sensor_last_command_is_kept():
    ctrl, states, t0, pd_calls, writes, cp_calls = _loop_controller(with_physical=False)
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=1)))
    pd_calls.clear()
    writes.clear()
    ctrl._cycle_kept_last_command = False
    states.values["sensor.real"] = 4000.0  # not configured: ignored
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=3)))
    assert pd_calls == []
    assert ctrl._cycle_kept_last_command is True
    assert ctrl.previous_power == pytest.approx(0.0)
    assert writes == []
