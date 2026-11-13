"""FORK: fixes from the code review of the relay guard / grid view hooks.

Numbers refer to the review findings: 1 frozen real meter, 2/7 peak exit
hold, 5 ICP excluded protection on the real meter, 6 real-meter trigger,
8 grid-at-min-SOC power, 9 relay guard bypass.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.omnibattery import ChargeDischargeController as C
from custom_components.omnibattery.const import integration_const as fork_const
from custom_components.omnibattery.control.grid_view import PHYSICAL_FROZEN_S, GridView
from tests.test_peak_review_pending import _loop_controller
from tests.test_peak_shaving import _controller


class _States:
    def __init__(self, values):
        self.values = values

    def get(self, entity_id):
        value = self.values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(state=str(value), attributes={},
                               last_reported=datetime.now(timezone.utc))


def _with_view(ctrl, value):
    view = GridView()
    view.configure(physical_entity="sensor.real", physical_inverted=False,
                   limit_entity=None, limit_margin_pct=0)
    ctrl._grid_view = view
    ctrl.hass = SimpleNamespace(states=_States({"sensor.real": value}))
    ctrl.offgrid_mode_enabled, ctrl.offgrid_power_sensor = False, None
    return view


# --- 1: frozen real meter --------------------------------------------------

def test_frozen_value_during_physical_shaving_falls_back():
    ctrl = _controller(previous_power=-3000.0)
    view = _with_view(ctrl, 15000.0)
    ctrl._peak_physical_prev = True
    assert ctrl._read_physical_grid_w() == pytest.approx(15000.0)
    # value unchanged (but freshly reported) for longer than PHYSICAL_FROZEN_S
    view._changed_at -= timedelta(seconds=PHYSICAL_FROZEN_S + 1)
    assert ctrl._read_physical_grid_w() is None
    assert view.status()["physical_source"] == "fallback"
    # sticky: stays unusable even when the next cycle is no longer "shaving"
    ctrl._peak_physical_prev = False
    assert ctrl._read_physical_grid_w() is None
    # recovers on the next value change
    ctrl.hass.states.values["sensor.real"] = 14800.0
    assert ctrl._read_physical_grid_w() == pytest.approx(14800.0)


def test_no_frozen_check_outside_physical_shaving():
    ctrl = _controller(previous_power=0.0)
    view = _with_view(ctrl, 300.0)
    ctrl._peak_physical_prev = False
    ctrl._read_physical_grid_w()
    view._changed_at -= timedelta(seconds=PHYSICAL_FROZEN_S + 100)
    assert ctrl._read_physical_grid_w() == pytest.approx(300.0)
    assert view.status()["physical_source"] == "physical"


# --- 2/7: peak exit hold ---------------------------------------------------

def test_exit_from_physical_shaving_holds_before_force_idle():
    ctrl = _controller(previous_power=-500.0)
    ctrl._peak_physical_prev = True
    ctrl._apply_capacity_protection(123.0, 0.0, physical_actual=2000.0)
    assert ctrl._capacity_protection_status["action"] == "conserving"
    assert ctrl._capacity_protection_force_idle is False  # hold: keep last command
    assert C._track_peak_state(ctrl, 2000.0) is False  # conserving keeps the timer
    assert ctrl._peak_exit_since is not None
    # still inside the hold
    ctrl._apply_capacity_protection(123.0, 0.0, physical_actual=2000.0)
    assert ctrl._capacity_protection_force_idle is False
    # hold elapsed without interruption -> force idle
    ctrl._peak_exit_since -= fork_const.PEAK_EXIT_HOLD_S + 0.1
    ctrl._apply_capacity_protection(123.0, 0.0, physical_actual=2000.0)
    assert ctrl._capacity_protection_force_idle is True
    assert ctrl._peak_exit_since is None


def test_exit_hold_timer_dropped_when_condition_ends():
    ctrl = _controller(previous_power=-500.0)
    ctrl._peak_physical_prev = True
    ctrl._apply_capacity_protection(123.0, 0.0, physical_actual=2000.0)
    assert ctrl._peak_exit_since is not None
    # back to shaving: timer discarded
    ctrl._capacity_protection_status["action"] = "shaving"
    assert C._track_peak_state(ctrl, 4000.0) is True
    assert ctrl._peak_exit_since is None
    # idle branch without real meter: timer discarded, no physical flag
    ctrl._capacity_protection_status["action"] = "idle"
    assert C._track_peak_state(ctrl, None) is False
    assert ctrl._peak_physical_prev is False


@pytest.mark.parametrize("prev_flag", [False, None])
def test_without_previous_physical_shaving_force_idle_is_immediate(prev_flag):
    # Unchanged upstream behaviour (no second sensor, or not shaving before).
    ctrl = _controller(previous_power=-500.0)
    if prev_flag is not None:
        ctrl._peak_physical_prev = prev_flag
    ctrl._apply_capacity_protection(321.4, 0.0)
    assert ctrl._capacity_protection_status["action"] == "conserving"
    assert ctrl._capacity_protection_force_idle is True


def test_track_peak_state_needs_real_value():
    ctrl = _controller(previous_power=0.0)
    ctrl._capacity_protection_status = {"action": "shaving"}
    assert C._track_peak_state(ctrl, None) is False
    assert C._track_peak_state(ctrl, 5000.0) is True


# --- 2/9: relay guard bypass ----------------------------------------------

@pytest.mark.parametrize(
    ("active", "action", "icp_w", "expected"),
    [
        (True, "shaving", 0.0, True),
        (True, "shaving_excluded", 0.0, True),
        (True, "conserving", 0.0, False),
        (False, "charging", 0.0, False),
        (False, "idle", 0.0, False),
        (False, "idle", 150.0, True),
        (True, "conserving", 1.0, True),
    ],
)
def test_guard_peak_bypass(active, action, icp_w, expected):
    ctrl = _controller(previous_power=0.0)
    ctrl._capacity_protection_active = active
    ctrl._capacity_protection_status = {"action": action}
    ctrl._icp_excluded_protection_w = icp_w
    assert C._guard_peak_bypass(ctrl) is expected


# --- 5: ICP excluded protection skipped on the real meter -----------------

def test_icp_excluded_protection_skipped_while_shaving_on_real_meter():
    ctrl, states, t0, _pd, _writes, _cp = _loop_controller(with_physical=True)
    calls = []

    def _icp(sensor_filtered, sensor_actual, active_target):
        calls.append(sensor_actual)
        ctrl._icp_excluded_protection_w = 999.0
        return sensor_actual + 999.0

    ctrl._apply_icp_excluded_protection = _icp
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=1)))
    assert len(calls) == 1  # control-sensor cycle: unchanged behaviour
    states.values["sensor.real"] = 4000.0
    asyncio.run(ctrl._run_control_cycle(now=t0 + timedelta(seconds=3)))
    assert ctrl._capacity_protection_status["action"] == "shaving"
    assert len(calls) == 1  # not called on the real meter
    assert ctrl._icp_excluded_protection_w == 0.0


# --- 6: real-meter trigger gives the cycle interval back -------------------

def _rate_ctrl():
    ctrl = object.__new__(C)
    ctrl.previous_power = 0.0
    ctrl._unloading = False
    ctrl.no_pd_mode_enabled = False
    ctrl._no_pd_command_delay = 0
    ctrl._min_cycle_interval_s = 5.0
    ctrl._last_cycle_monotonic = 0.0
    ctrl._control_lock = asyncio.Lock()

    async def _cycle(now):
        ctrl._cycle_kept_last_command = True

    ctrl._run_control_cycle = _cycle
    return ctrl


def test_physical_trigger_that_kept_command_gives_interval_back():
    ctrl = _rate_ctrl()
    ctrl._physical_trigger_pending = True
    asyncio.run(ctrl.async_update_charge_discharge())
    assert ctrl._last_cycle_monotonic == 0.0
    assert ctrl._physical_trigger_pending is False


def test_control_sensor_event_keeps_upstream_rate_limit():
    ctrl = _rate_ctrl()
    before = time.monotonic()
    asyncio.run(ctrl.async_update_charge_discharge())
    assert ctrl._last_cycle_monotonic >= before


# --- 8: grid-at-min-SOC power ---------------------------------------------

def test_grid_at_min_soc_power_without_second_sensor_is_sensor_actual():
    ctrl = _controller(previous_power=0.0)
    ctrl.read_statistics_grid_w = lambda: 9999.0  # must not be used
    assert C._grid_at_min_soc_power(ctrl, 321.5) == 321.5
    ctrl._grid_view = GridView()  # present but not configured
    assert C._grid_at_min_soc_power(ctrl, 321.5) == 321.5


def test_grid_at_min_soc_power_with_second_sensor_uses_statistics():
    ctrl = _controller(previous_power=0.0)
    _with_view(ctrl, 0.0)
    ctrl.read_statistics_grid_w = lambda: 1500.0
    assert C._grid_at_min_soc_power(ctrl, 321.5) == 1500.0
    ctrl.read_statistics_grid_w = lambda: -200.0
    assert C._grid_at_min_soc_power(ctrl, 321.5) == 0.0
    ctrl.read_statistics_grid_w = lambda: None
    assert C._grid_at_min_soc_power(ctrl, 321.5) == 321.5


# --- 11/13: repair issue and config robustness ----------------------------

def _config_ctrl(data):
    ctrl = object.__new__(C)
    ctrl.config_entry = SimpleNamespace(data=data, entry_id="e1")
    ctrl.hass = None
    from custom_components.omnibattery.control.relay_guard import RelayGuard
    ctrl._relay_guard = RelayGuard()
    ctrl._grid_view = GridView()
    return ctrl


def test_configure_tolerates_none_values():
    c = fork_const
    ctrl = _config_ctrl({
        c.CONF_PD_RELAY_GUARD_ENABLED: None,
        c.CONF_PD_RELAY_MIN_OFF: None,
        c.CONF_PD_DIRECTION_LOCK: None,
        c.CONF_PD_ENGAGE_MARGIN: None,
        c.CONF_PD_GUARD_BYPASS_ERROR: None,
        c.CONF_PD_RELAY_SWITCH_BUDGET: None,
        c.CONF_PHYSICAL_GRID_SENSOR: None,
        c.CONF_PHYSICAL_GRID_INVERTED: None,
        c.CONF_CAPACITY_PROTECTION_LIMIT_ENTITY: None,
        c.CONF_CAPACITY_PROTECTION_LIMIT_MARGIN: None,
    })
    ctrl._configure_relay_guard()
    ctrl._configure_grid_view()
    assert ctrl._pd_relay_guard_enabled is False
    assert ctrl._grid_view.has_physical is False


def test_configure_grid_view_clears_repair_issue(monkeypatch):
    import custom_components.omnibattery as integration

    deleted = []
    monkeypatch.setattr(integration.ir, "async_delete_issue",
                        lambda hass, domain, issue_id: deleted.append(issue_id))
    ctrl = _config_ctrl({})
    ctrl.hass = SimpleNamespace()
    ctrl._physical_grid_issue_created = True
    ctrl._configure_grid_view()
    assert deleted == ["physical_grid_sensor_unusable_e1"]
    assert ctrl._physical_grid_issue_created is False


def test_no_repair_issue_in_offgrid_mode(monkeypatch):
    import custom_components.omnibattery as integration

    created = []
    monkeypatch.setattr(integration.ir, "async_create_issue",
                        lambda *a, **k: created.append(a))
    ctrl = _controller(previous_power=0.0)
    view = _with_view(ctrl, None)  # entity missing -> unusable
    ctrl.config_entry = SimpleNamespace(entry_id="e1")
    view.read_physical(ctrl.hass.states, datetime.now(timezone.utc) - timedelta(hours=1))
    ctrl.offgrid_mode_enabled, ctrl.offgrid_power_sensor = True, "sensor.og"
    C._check_physical_grid_sensor(ctrl)
    assert created == []
    ctrl.offgrid_mode_enabled, ctrl.offgrid_power_sensor = False, None
    C._check_physical_grid_sensor(ctrl)
    assert len(created) == 1
