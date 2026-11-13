"""Peak shaving and ICP clamp on the optional physical grid sensor (K1-K6)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.omnibattery.control.grid_view import GridView
from tests.test_peak_shaving import _controller


def _states(**values):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(get=lambda eid: (
        SimpleNamespace(state=str(values[eid]), attributes={}, last_reported=now)
        if eid in values else None))


def test_k1_control_sensor_zeroed_real_over_limit_shaves():
    ctrl = _controller(previous_power=0.0)
    target, sensor = ctrl._apply_capacity_protection(0.0, 0.0, physical_actual=4000.0)
    assert ctrl._capacity_protection_status["action"] == "shaving"
    assert ctrl._capacity_protection_status["grid_source"] == "physical"
    assert target == pytest.approx(3000.0)
    assert sensor == pytest.approx(4000.0)


def test_k2_real_below_limit_does_not_shave():
    ctrl = _controller(previous_power=0.0)
    ctrl._apply_capacity_protection(4000.0, 0.0, physical_actual=1000.0)
    assert ctrl._capacity_protection_status["action"] != "shaving"


def test_k3_battery_discharging_conserves_on_control_sensor():
    ctrl = _controller(previous_power=-500.0)
    target, sensor = ctrl._apply_capacity_protection(123.0, 0.0, physical_actual=2000.0)
    status = ctrl._capacity_protection_status
    assert status["action"] == "conserving"
    assert status["estimated_house_load"] == 2500
    assert sensor == pytest.approx(123.0)
    assert target == pytest.approx(123.0)


@pytest.mark.parametrize("grid", [4000.0, 1000.0, 321.4])
def test_k4_none_equals_call_without_physical(grid):
    a, b = _controller(previous_power=-200.0), _controller(previous_power=-200.0)
    assert (a._apply_capacity_protection(grid, 0.0, physical_actual=None)
            == b._apply_capacity_protection(grid, 0.0))
    assert a._capacity_protection_status["action"] == b._capacity_protection_status["action"]
    assert a._capacity_protection_status["grid_source"] == "control"


def test_k5_dynamic_limit_from_entity():
    ctrl = _controller(previous_power=0.0)
    view = GridView()
    view.configure(physical_entity=None, physical_inverted=False,
                   limit_entity="sensor.peak", limit_margin_pct=0)
    ctrl._grid_view = view
    ctrl.hass = SimpleNamespace(states=_states(**{"sensor.peak": 2500}))
    target, _ = ctrl._apply_capacity_protection(0.0, 0.0, physical_actual=2800.0)
    status = ctrl._capacity_protection_status
    assert status["action"] == "shaving"
    assert target == pytest.approx(2500.0)
    assert status["peak_limit"] == 2500


def test_k6_icp_clamp_uses_real_sensor():
    ctrl = _controller(previous_power=0.0)
    ctrl.max_contracted_power = 5000
    # real 4800 W, control sensor 0 W, wish 1000 W charge -> capped to 200 W
    assert ctrl._icp_charge_import_cap(0.0, 4800.0) == pytest.approx(200.0)
    # without real sensor: control reading is used as before
    assert ctrl._icp_charge_import_cap(0.0, None) == pytest.approx(5000.0)


def test_read_physical_none_without_grid_view_or_in_offgrid():
    ctrl = _controller(previous_power=0.0)
    assert ctrl._read_physical_grid_w() is None
    view = GridView()
    view.configure(physical_entity="sensor.real", physical_inverted=False,
                   limit_entity=None, limit_margin_pct=0)
    ctrl._grid_view = view
    ctrl.hass = SimpleNamespace(states=_states(**{"sensor.real": 1234}))
    ctrl.offgrid_mode_enabled = False
    ctrl.offgrid_power_sensor = None
    assert ctrl._read_physical_grid_w() == pytest.approx(1234.0)
    ctrl.offgrid_mode_enabled, ctrl.offgrid_power_sensor = True, "sensor.og"
    assert ctrl._read_physical_grid_w() is None
