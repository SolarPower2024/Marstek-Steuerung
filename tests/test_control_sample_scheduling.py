"""Regression tests for separating meter health from control samples.

The controller is exercised with small stubs so these tests cover the real
``_run_control_cycle`` branches without requiring a live
Home Assistant event loop or a battery connection.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.core import State

from custom_components.omnibattery import ChargeDischargeController
from custom_components.omnibattery.tracking.non_responsive_tracker import (
    NonResponsiveTracker,
)

_TRACKER = "custom_components.omnibattery.tracking.non_responsive_tracker"


class _HashableNamespace(SimpleNamespace):
    __hash__ = object.__hash__
    __eq__ = object.__eq__


async def _async_noop(*_args, **_kwargs):
    return None


async def _async_false(*_args, **_kwargs):
    return False


def _state(value, reported_at, *, updated_at=None, attributes=None):
    return State(
        "sensor.grid_power",
        str(value),
        attributes=attributes or {},
        last_changed=updated_at or reported_at,
        last_reported=reported_at,
        last_updated=updated_at or reported_at,
    )


def _main_controller(state_holder, pd_calls):
    class _Coordinator(SimpleNamespace):
        __hash__ = object.__hash__

    coordinator = _Coordinator(
        _is_shutting_down=False,
        is_available=True,
        data={"battery_soc": 50},
        max_soc=90,
        min_soc=10,
        name="battery",
        commanded_charge_power=0,
        commanded_discharge_power=0,
    )

    async def _max_soc_measurement(*_args, **_kwargs):
        return False

    class _States:
        def get(self, _entity_id):
            return state_holder["state"]

    def _pd(error, sensor_elapsed_s, stale_safety_recalc):
        pd_calls.append((error, sensor_elapsed_s, stale_safety_recalc))
        return -500.0

    controller = SimpleNamespace(
        coordinators=[coordinator],
        _phase_power_limiter=SimpleNamespace(
            enabled=False,
            begin_cycle=lambda: None,
            update_degraded_warning=lambda: None,
        ),
        _non_responsive=SimpleNamespace(
            update_repairs=lambda *a: None,
            clear=lambda c, **k: None,
            last_reason=lambda c, default="": default,
        ),
        _balance_monitor=None,
        _pricing_mgr=SimpleNamespace(maybe_check_price_data_health=lambda: None),
        manual_mode_enabled=False,
        _weekly_charge_mgr=SimpleNamespace(
            handle_registers=_async_noop, is_battery_full=lambda c: False,
        ),
        _charge_delay_mgr=SimpleNamespace(handle_daily_reset_and_eval=lambda: None),
        _refresh_operation_blockers=lambda: None,
        _try_apply_manual_slot=_async_noop,
        _phase_safety_pending=False,
        _max_soc_mgr=SimpleNamespace(handle_measurement=_max_soc_measurement),
        _apply_dead_meter_fallback=_async_false,
        predictive_charging_enabled=False,
        previous_power=-400.0,
        previous_sensor=None,
        previous_error=0.0,
        first_execution=False,
        last_output_sign=-1,
        last_error_sign=0,
        sign_changes=0,
        error_integral=0.0,
        derivative_filtered=0.0,
        _stale_cycles=0,
        _last_sensor_report_time=None,
        _last_sensor_cadence_time=None,
        _last_control_sample_value=None,
        _control_sample_is_new=True,
        _slow_sensor_issue_created=False,
        _slow_sensor_intervals=0,
        _fast_sensor_intervals=0,
        _max_sensor_stale_s=65.0,
        consumption_sensor="sensor.grid_power",
        config_entry=SimpleNamespace(entry_id="control-sample", data={}),
        hass=SimpleNamespace(states=_States()),
        _apply_meter_transform=lambda state: float(state.state),
        _check_main_sensor_liveness=lambda _now=None: None,
        _check_missing_configured_sensors=lambda: None,
        _is_capacity_protection_soc_limited=lambda: False,
        _filter_grid_sample=lambda raw, _elapsed: raw,
        compute_active_target=lambda: 0.0,
        _resolve_home_consumption_sensor=lambda: None,
        _external_loads=SimpleNamespace(
            calculate_adjustment=lambda: 0.0,
            check_ev_charger_state=lambda: (False, False),
        ),
        _hourly_balance_mgr=None,
        _apply_capacity_protection=lambda sensor, target: (target, sensor),
        _apply_icp_excluded_protection=lambda _filtered, sensor, _target: sensor,
        _capacity_protection_force_idle=False,
        deadband=40.0,
        _is_charge_blocked=lambda *_args, **_kwargs: False,
        _is_discharge_blocked=lambda *_args, **_kwargs: False,
        is_charge_blocked=lambda *_args, **_kwargs: False,
        is_discharge_blocked=lambda *_args, **_kwargs: False,
        _stop_blocked_active_batteries=_async_false,
        _stop_all_batteries_for_block=_async_noop,
        _refresh_effective_system_capacities=lambda: None,
        no_pd_mode_enabled=False,
        _check_feedforward_step=lambda _error: False,
        _delivered_power_reliable=lambda: True,
        _compute_pd_new_power=_pd,
        _apply_zero_cross_hold=lambda power, _error, stale_recalc=False: power,
        _apply_min_power=lambda power, _error: power,
        _apply_relay_dwell=lambda power, _error, stale_recalc=False: power,
        _is_operation_allowed=lambda _is_charging: True,
        _price_based_discharge_blocked=False,
        _solar_surplus_discharge_blocked=False,
        _get_available_batteries=lambda is_charging, include_operation_blocks=True: [coordinator],
        _effective_system_capacity=lambda _batteries, is_charging: 2500.0,
        max_contracted_power=0,
        grid_charging_active=False,
        _power_distribution=SimpleNamespace(
            _select_batteries_for_operation=lambda _power, batteries, is_charging=None: batteries,
            _distribute_power_by_limits=lambda power, batteries, is_charging=None: {
                battery: power / len(batteries) for battery in batteries
            },
        ),
        _log_power_command_plan=lambda **_kwargs: None,
        _set_battery_power=_async_noop,
        _update_pd_quality_metrics=lambda *_args, **_kwargs: None,
        _pd_demand_blocked=lambda _error, _commanded_power: False,
        _set_pd_limited=lambda _value: None,
        _set_pd_blocked=lambda _value: None,
        _pd_limited=False,
        _pd_blocked=False,
        _active_discharge_batteries=[],
        _active_charge_batteries=[],
    )
    controller._run_control_cycle = ChargeDischargeController._run_control_cycle.__get__(
        controller, ChargeDischargeController
    )
    controller._track_sensor_report = ChargeDischargeController._track_sensor_report.__get__(
        controller, ChargeDischargeController
    )
    controller._sensor_age_seconds = ChargeDischargeController._sensor_age_seconds.__get__(
        controller, ChargeDischargeController
    )
    controller._sensor_is_within_stale_tolerance = ChargeDischargeController._sensor_is_within_stale_tolerance.__get__(
        controller, ChargeDischargeController
    )
    return controller


def test_repeated_publication_does_not_reapply_pd_but_real_change_runs_once():
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))
    assert len(pd_calls) == 1
    previous_power = controller.previous_power

    # Same transformed value, newer last_reported, unchanged last_updated.
    state_holder["state"] = _state(
        100,
        first_report + timedelta(seconds=4),
        updated_at=first_report,
    )
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=4)))
    assert len(pd_calls) == 1
    assert controller.previous_power == previous_power

    # A real value change is consumed exactly once by the incremental PD path.
    state_holder["state"] = _state(
        200,
        first_report + timedelta(seconds=8),
        updated_at=first_report + timedelta(seconds=8),
    )
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=8)))
    assert len(pd_calls) == 2

    # The watchdog sees the already-consumed state but must not apply P/D again.
    previous_power = controller.previous_power
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=10)))
    assert len(pd_calls) == 2
    assert controller.previous_power == previous_power


def test_full_battery_with_a_charge_episode_clears_the_tracker():
    """A battery at 100%/a confirmed BMS cutoff stops being commanded to
    charge (is_battery_full() in _get_available_batteries), so it never
    reaches _check_non_delivery's charge path again - the only other place
    that clears the non-responsive tracker via the BMS-full exemption.
    _run_control_cycle must clear a charge-side episode directly, or a
    battery that tapered through a brief non-delivery episode on its way to
    100% stays "degraded" forever once idle-full, and a battery_not_delivering
    Repair it already raised never resolves on its own.
    """
    reported_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, reported_at)}
    controller = _main_controller(state_holder, [])
    controller._weekly_charge_mgr.is_battery_full = lambda c: True
    controller._non_responsive = NonResponsiveTracker(fail_threshold=3)
    coord = controller.coordinators[0]
    for _ in range(6):  # grace round (wake) + second threshold-cross (exclude)
        controller._non_responsive.record_non_delivery(
            coord, 1250, 5, reason="charge_non_delivery",
        )
    assert controller._non_responsive.batteries[coord]["degraded_since"] is not None
    controller._non_responsive.batteries[coord]["repair_raised"] = True

    with patch(f"{_TRACKER}.ir.async_delete_issue") as delete_issue:
        asyncio.run(controller._run_control_cycle(now=reported_at))

    assert controller._non_responsive.batteries[coord]["fail_count"] == 0
    assert controller._non_responsive.batteries[coord]["degraded_since"] is None
    delete_issue.assert_called_once()


def test_full_battery_with_a_discharge_episode_is_left_alone():
    """A full battery is still a live discharge candidate: if it ACKs a
    discharge set-point but stays in Standby, _check_non_delivery deliberately
    records standby_no_delivery to drive the wake/reconnect recovery (#26).
    The full-battery sweep must not wipe that just because SOC also reads
    full, or a genuine discharge fault (or a record_comm_failure reason, none
    of which carry the charge_ prefix either) never reaches exclusion or its
    own Repair.
    """
    reported_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, reported_at)}
    controller = _main_controller(state_holder, [])
    controller._weekly_charge_mgr.is_battery_full = lambda c: True
    controller._non_responsive = NonResponsiveTracker(fail_threshold=3)
    coord = controller.coordinators[0]
    for _ in range(6):  # grace round (wake) + second threshold-cross (exclude)
        controller._non_responsive.record_non_delivery(
            coord, 600, 0, reason="standby_no_delivery",
        )
    fail_count_before = controller._non_responsive.batteries[coord]["fail_count"]
    degraded_since_before = controller._non_responsive.batteries[coord]["degraded_since"]
    assert degraded_since_before is not None
    # Repair already open for the discharge fault: it must stay open.
    degraded_since_before -= timedelta(days=1)
    controller._non_responsive.batteries[coord]["degraded_since"] = degraded_since_before
    controller._non_responsive.batteries[coord]["repair_raised"] = True

    with patch(f"{_TRACKER}.ir.async_delete_issue") as delete_issue:
        asyncio.run(controller._run_control_cycle(now=reported_at))

    delete_issue.assert_not_called()

    assert controller._non_responsive.batteries[coord]["fail_count"] == fail_count_before
    assert controller._non_responsive.batteries[coord]["degraded_since"] == degraded_since_before


def test_non_full_battery_is_left_alone():
    """The new full-battery sweep must not touch a battery that isn't full."""
    reported_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, reported_at)}
    controller = _main_controller(state_holder, [])
    controller._weekly_charge_mgr.is_battery_full = lambda c: False
    cleared = []
    controller._non_responsive.clear = lambda c, **k: cleared.append(c)

    asyncio.run(controller._run_control_cycle(now=reported_at))

    assert cleared == []


def test_unavailable_consumption_sensor_does_not_spam_warnings(caplog):
    reported_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state("unavailable", reported_at)}
    controller = _main_controller(state_holder, [])
    controller.meter_inverted = False
    controller._apply_meter_transform = (
        ChargeDischargeController._apply_meter_transform.__get__(
            controller, ChargeDischargeController
        )
    )
    controller._log_consumption_sensor_issue = (
        ChargeDischargeController._log_consumption_sensor_issue.__get__(
            controller, ChargeDischargeController
        )
    )
    caplog.set_level(logging.DEBUG, logger="custom_components.omnibattery")

    asyncio.run(controller._run_control_cycle(now=reported_at))
    asyncio.run(controller._run_control_cycle(now=reported_at + timedelta(seconds=2)))

    matching = [
        record
        for record in caplog.records
        if "Consumption sensor sensor.grid_power" in record.getMessage()
    ]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert [record.levelno for record in matching] == [logging.DEBUG]
    assert "is unavailable" in matching[0].getMessage()

    # A numeric sample ends the episode, so a later outage is reported once again.
    state_holder["state"] = _state(100, reported_at + timedelta(seconds=4))
    asyncio.run(controller._run_control_cycle(now=reported_at + timedelta(seconds=4)))
    state_holder["state"] = _state("unknown", reported_at + timedelta(seconds=6))
    asyncio.run(controller._run_control_cycle(now=reported_at + timedelta(seconds=6)))

    matching = [
        record
        for record in caplog.records
        if "Consumption sensor sensor.grid_power" in record.getMessage()
    ]
    assert [record.levelno for record in matching] == [logging.DEBUG, logging.DEBUG]
    assert "is unknown" in matching[1].getMessage()


def test_manual_grid_charge_does_not_induce_automatic_discharge_when_idle():
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(700, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)
    controller.previous_power = 0.0
    controller.last_output_sign = 0
    controller.ki = 0.0
    controller._power_distribution._rebalance_expired_load_sharing_hold = _async_false

    manual = _HashableNamespace(
        _is_shutting_down=False,
        battery_manual_mode_enabled=True,
        data={"battery_power": 700},
        name="manual battery",
    )
    controller.coordinators.append(manual)

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))

    # With no automatic charge active, the intentional manual import is not a
    # reason to discharge an automatic battery.
    assert pd_calls == []
    assert controller.previous_power == 0.0


def test_manual_grid_charge_reduces_automatic_charge_without_discharge():
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(1000, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)
    controller.previous_power = 2000.0
    controller.last_output_sign = 1
    controller.ki = 0.0
    controller._power_distribution._rebalance_expired_load_sharing_hold = _async_false
    controller.coordinators[0].data = {
        "battery_soc": 50,
        "battery_power": 2000,
    }

    def _reduce_automatic_charge(error, sensor_elapsed_s, stale_safety_recalc):
        pd_calls.append((error, sensor_elapsed_s, stale_safety_recalc))
        return 1000.0

    controller._compute_pd_new_power = _reduce_automatic_charge
    manual = _HashableNamespace(
        _is_shutting_down=False,
        battery_manual_mode_enabled=True,
        data={"battery_power": 1000},
        name="manual battery",
    )
    controller.coordinators.append(manual)

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))

    # The 1 kW import is used to reduce the automatic 2 kW charge to 1 kW;
    # it must not be turned into an automatic discharge command.
    assert pd_calls and pd_calls[0][0] == 1000
    assert controller.previous_power == 1000.0


def test_busy_loop_cadence_does_not_consume_pending_real_change():
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))
    assert len(pd_calls) == 1

    # Publication callbacks continue to feed cadence while the previous cycle is
    # busy. They intentionally do not update the last control fingerprint.
    for seconds in (4, 8):
        ChargeDischargeController._observe_sensor_cadence(
            controller, first_report + timedelta(seconds=seconds)
        )

    state_holder["state"] = _state(
        250,
        first_report + timedelta(seconds=8),
        updated_at=first_report + timedelta(seconds=8),
    )
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=9)))

    assert len(pd_calls) == 2
    assert controller._last_sensor_cadence_time == first_report + timedelta(seconds=8)


def test_silent_sensor_uses_stale_safety_without_reapplying_pd():
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))
    previous_power = controller.previous_power

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=70)))

    assert len(pd_calls) == 1
    assert controller.previous_power == previous_power


def test_only_a_cycle_without_a_new_sample_reports_that_it_kept_the_last_command():
    """The flag lets a safety-timer tick give PD Min Cycle Interval back."""
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)

    def _cycle(seconds):
        controller._cycle_kept_last_command = False
        asyncio.run(
            controller._run_control_cycle(now=first_report + timedelta(seconds=seconds))
        )
        return controller._cycle_kept_last_command

    # A new sample runs P/D.
    assert _cycle(1) is False
    assert len(pd_calls) == 1
    # The next tick sees the same sample and keeps the last command.
    assert _cycle(3) is True
    assert len(pd_calls) == 1
    # A silent meter forces a safety recalculation, which is not kept.
    assert _cycle(70) is False



def test_paced_tick_keeps_command_but_leaves_new_sample_for_next_cycle():
    """#654: a 2 s tick inside the pacing window must not act on a fresh sample."""
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))
    assert len(pd_calls) == 1

    state_holder["state"] = _state(200, first_report + timedelta(seconds=2))
    controller._timer_tick_paced = True
    controller._cycle_kept_last_command = False
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=2)))
    assert len(pd_calls) == 1
    assert controller._cycle_kept_last_command is True

    # Same value, next unpaced cycle: still treated as new, so P/D acts on it.
    controller._timer_tick_paced = False
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=4)))
    assert len(pd_calls) == 2
    # Elapsed spans the last consumed report, not 0 (which freezes the EMA).
    assert pd_calls[-1][1] == 2.0


def test_paced_tick_with_pending_safety_runs_a_normal_cycle():
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(100, first_report)}
    pd_calls = []
    controller = _main_controller(state_holder, pd_calls)
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))

    state_holder["state"] = _state(200, first_report + timedelta(seconds=2))
    controller._timer_tick_paced = True
    controller._phase_safety_pending = True
    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=2)))
    assert len(pd_calls) == 2
    assert pd_calls[-1][2] is False  # fresh sample, not a stale recalc


async def _record_write(writes, coordinator, charge, discharge):
    writes.append((coordinator, charge, discharge))


def _phase_limited_after_cycle(assigned_w):
    """Run one 1003 W charge cycle with phase protection on and return the flag."""
    first_report = datetime(2026, 1, 1, tzinfo=timezone.utc)
    state_holder = {"state": _state(-1003, first_report)}
    controller = _main_controller(state_holder, [])
    controller.previous_power = 0.0
    controller.last_output_sign = 0
    controller.ki = 0.0
    controller._power_distribution._rebalance_expired_load_sharing_hold = _async_false
    controller._compute_pd_new_power = lambda *_args: 1003.0
    controller._phase_power_limiter.enabled = True
    controller._phase_power_limiter.has_degraded_phase = lambda: False
    controller._power_distribution._distribute_power_by_limits = (
        lambda _power, batteries, is_charging=None: {batteries[0]: assigned_w}
    )
    flags = []
    controller._set_pd_limited = flags.append

    asyncio.run(controller._run_control_cycle(now=first_report + timedelta(seconds=1)))
    return flags[-1]


def test_5w_rounding_is_not_reported_as_phase_limited():
    # Issue #559: 1003 W rounded to 1000 W read as a phase cap.
    assert _phase_limited_after_cycle(1000) is False


def test_real_phase_cap_is_still_reported_as_limited():
    assert _phase_limited_after_cycle(800) is True

