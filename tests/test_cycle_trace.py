"""FORK: control-cycle trace (docs/fork/08-trace.md).

The trace must never influence control: off, it records nothing; on, the
control cycles behave exactly as without it and a line per cycle appears.
"""
from __future__ import annotations

import logging

import pytest

from custom_components.omnibattery.control import residual_load
from custom_components.omnibattery.control.residual_load import apply_guards
from custom_components.omnibattery.infra import cycle_trace

from tests import test_control_sample_scheduling as cycles
from tests.test_residual_load import _battery, _controller


@pytest.fixture
def trace_on(caplog):
    caplog.set_level(logging.DEBUG, logger=cycle_trace.TRACE_LOGGER_NAME)
    yield caplog
    logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.NOTSET)


def _trace_lines(caplog, kind):
    return [
        r.getMessage() for r in caplog.records
        if r.name == cycle_trace.TRACE_LOGGER_NAME and r.getMessage().startswith(kind + " ")
    ]


def test_off_by_default_records_nothing(caplog):
    logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.WARNING)
    controller = _controller([_battery("Marstek", ac_power=30)])
    cycle_trace.begin(controller)
    apply_guards(controller, -30.0, -190.0)
    cycle_trace.end(controller)
    assert controller._trace_ctx is None
    assert not _trace_lines(caplog, "CYC")
    logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.NOTSET)


def test_guard_inputs_and_latch_are_traced(trace_on, monkeypatch):
    monkeypatch.setattr(residual_load, "SURPLUS_GUARD_MIN_HOLD_S", 0.0)
    controller = _controller([_battery("Marstek", ac_power=30)], enabled=False)
    cycle_trace.begin(controller)
    assert apply_guards(controller, -30.0, -190.0) == 0
    cycle_trace.end(controller)

    [line] = _trace_lines(trace_on, "CYC")
    assert "unc=[grid-190.0+Marstek:ac_power:30.0=-160.0" in line
    assert "dem-160.0" in line and "/0>1/" in line
    assert "ag=[-30.0>-30.0>0.0>0.0]" in line
    assert "bat={Marstek:soc50/bp-/ac30/" in line
    [latch] = _trace_lines(trace_on, "SG_LATCH")
    assert "demand=-160.0" in latch


def test_build_failure_never_reaches_control(trace_on):
    controller = _controller([])
    cycle_trace.begin(controller)
    cycle_trace.put_lazy(controller, lambda: {"x": controller.missing_method()})
    cycle_trace.end(controller)
    [line] = _trace_lines(trace_on, "CYC")
    assert "trace_err=AttributeError" in line


@pytest.mark.parametrize("scenario", [
    cycles.test_repeated_publication_does_not_reapply_pd_but_real_change_runs_once,
    cycles.test_manual_grid_charge_does_not_induce_automatic_discharge_when_idle,
    cycles.test_manual_grid_charge_reduces_automatic_charge_without_discharge,
    cycles.test_silent_sensor_uses_stale_safety_without_reapplying_pd,
    cycles.test_paced_tick_with_pending_safety_runs_a_normal_cycle,
])
def test_real_cycles_behave_the_same_with_trace_on(trace_on, scenario):
    scenario()
    lines = _trace_lines(trace_on, "CYC")
    assert lines
    assert all("exc=" not in line for line in lines)
    assert any("exit=" in line for line in lines)


# ---------------------------------------------------------------------------
# D (log-analyse-2026-11-16-ursache.md): phase review, write origin, PD terms
# ---------------------------------------------------------------------------
class _Bat:
    """Hashable stand-in battery (the review keys dicts by coordinator)."""

    def __init__(self, ac_power):
        self.name = "Marstek"
        self.data = {"ac_power": ac_power}
        self.commanded_charge_power = 0
        self.commanded_discharge_power = 0


def _phase_controller(battery, calls, enabled=True):
    import asyncio  # noqa: F401 - used by callers
    from types import SimpleNamespace

    from custom_components.omnibattery import ChargeDischargeController

    async def _set_battery_power(coordinator, charge, discharge):
        calls.append((coordinator.name, charge, discharge))

    controller = SimpleNamespace(
        coordinators=[battery],
        _phase_power_limiter=SimpleNamespace(enabled=enabled),
        _power_distribution=SimpleNamespace(
            _distribute_power_by_limits=lambda total, selected, is_charging: {
                selected[0]: total
            },
        ),
        _coordinator_delivered_power=ChargeDischargeController._coordinator_delivered_power,
        _set_battery_power=_set_battery_power,
        _phase_safety_pending=True,
        grid_charging_active=False,
        previous_power=0.0,
    )
    controller._signed_power_from_allocations = (
        lambda charging, discharging: ChargeDischargeController._signed_power_from_allocations(
            controller, charging, discharging
        )
    )
    return controller


def test_phase_review_measured_fallback_is_traced(trace_on):
    """The 16.11. case: command 0 W, battery still discharging 25 W -> the
    review writes a 25 W discharge. The PHASE line must show that the value
    came from the measurement, not from a command."""
    import asyncio
    from types import SimpleNamespace

    from custom_components.omnibattery import ChargeDischargeController

    battery = _Bat(ac_power=25)
    calls = []
    controller = _phase_controller(battery, calls)
    cycle_trace.begin(controller)
    asyncio.run(ChargeDischargeController._apply_phase_safety_review(controller))
    cycle_trace.end(controller)

    assert calls == [("Marstek", 0, 25.0)]
    [phase] = _trace_lines(trace_on, "PHASE")
    assert "Marstek:d:cmd0.0/0.0:meas-25.0:meas=25.0" in phase
    assert "Marstek:c:cmd0.0/0.0:meas-25.0:none=0.0" in phase
    assert "act=write" in phase and "prev_p=0.0" in phase and "new_p=-25.0" in phase
    [cyc] = _trace_lines(trace_on, "CYC")
    assert "phase=0.0>-25.0" in cyc


def test_phase_review_disabled_is_traced_and_writes_nothing(trace_on):
    import asyncio
    from types import SimpleNamespace

    from custom_components.omnibattery import ChargeDischargeController

    battery = _Bat(ac_power=25)
    calls = []
    controller = _phase_controller(battery, calls, enabled=False)
    asyncio.run(ChargeDischargeController._apply_phase_safety_review(controller))

    assert calls == []
    [phase] = _trace_lines(trace_on, "PHASE")
    assert "skip=disabled" in phase


def test_phase_review_unchanged_with_trace_off(caplog):
    import asyncio
    from types import SimpleNamespace

    from custom_components.omnibattery import ChargeDischargeController

    logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.WARNING)
    battery = _Bat(ac_power=25)
    calls = []
    controller = _phase_controller(battery, calls)
    asyncio.run(ChargeDischargeController._apply_phase_safety_review(controller))
    logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.NOTSET)

    assert calls == [("Marstek", 0, 25.0)]
    assert controller.previous_power == -25.0
    assert not _trace_lines(caplog, "PHASE")


def test_caller_names_the_awaiting_coroutine(trace_on):
    import asyncio

    async def _write():
        return cycle_trace.caller()

    async def _origin():
        return await _write()

    src = asyncio.run(_origin())
    assert src is not None and src.startswith("_origin:")


def test_caller_is_none_while_off():
    logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.WARNING)
    try:
        assert cycle_trace.caller() is None
    finally:
        logging.getLogger(cycle_trace.TRACE_LOGGER_NAME).setLevel(logging.NOTSET)


def test_trace_file_fits_one_ha_mcp_read():
    # ha-mcp caps a response at 1 MB; JSON escaping adds a little per line.
    assert cycle_trace.TRACE_FILE_MAX_BYTES <= 850 * 1024
    assert cycle_trace.TRACE_FILE_MAX_BYTES * (cycle_trace.TRACE_FILE_BACKUPS + 1) >= 90 * 1024 * 1024


def test_pd_terms_are_traced(trace_on):
    from tests.test_pd_direction_hysteresis import _ctrl, _run

    ctrl = _ctrl(last_output_sign=-1, previous_power=0)
    cycle_trace.begin(ctrl)
    assert _run(ctrl, error=-50) == 0  # 17.5 W charge suppressed by hysteresis
    cycle_trace.end(ctrl)
    [line] = _trace_lines(trace_on, "CYC")
    assert "pd=base0.0/P-17.5/I0.0/D" in line
    assert "/raw17.5/rl17.5/hy0.0" in line
