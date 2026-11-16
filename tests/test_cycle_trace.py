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
