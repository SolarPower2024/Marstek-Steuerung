"""FORK: read_statistics_grid_w (B5) and the real-grid-sensor repair issue (C)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.omnibattery import ChargeDischargeController as C
from custom_components.omnibattery.control.grid_view import GridView

PHYS = "sensor.real"
MAIN = "sensor.main"


def _state(value, unit="W"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        state=str(value), attributes={"unit_of_measurement": unit},
        last_reported=now, last_updated=now,
    )


def _ctrl(states, *, physical=PHYS, inverted=False):
    view = GridView()
    view.configure(physical_entity=physical, physical_inverted=inverted,
                   limit_entity=None, limit_margin_pct=0)
    ctrl = SimpleNamespace(
        _grid_view=view,
        hass=SimpleNamespace(states=SimpleNamespace(get=states.get)),
        offgrid_mode_enabled=False,
        offgrid_power_sensor=None,
        consumption_sensor=MAIN,
        config_entry=SimpleNamespace(entry_id="e1"),
        _apply_meter_transform=lambda s: None if s is None else float(s.state) * 2,
    )
    return ctrl


def test_repair_issue_created_after_5_min_and_cleared_on_recovery():
    ctrl = _ctrl({})
    now = datetime.now(timezone.utc)
    ctrl._grid_view._unusable_since = now - timedelta(seconds=120)
    with patch("custom_components.omnibattery.ir") as ir:
        C._check_physical_grid_sensor(ctrl)
        ir.async_create_issue.assert_not_called()
        ctrl._grid_view._unusable_since = now - timedelta(seconds=400)
        C._check_physical_grid_sensor(ctrl)
        C._check_physical_grid_sensor(ctrl)  # no duplicate
        assert ir.async_create_issue.call_count == 1
        assert ir.async_create_issue.call_args.kwargs["translation_key"] == "physical_grid_sensor_unusable"
        ctrl._grid_view._unusable_since = None
        C._check_physical_grid_sensor(ctrl)
        ir.async_delete_issue.assert_called_once()
        C._check_physical_grid_sensor(ctrl)
        assert ir.async_delete_issue.call_count == 1


def test_repair_check_tolerates_stub_without_grid_view():
    C._check_physical_grid_sensor(SimpleNamespace())
