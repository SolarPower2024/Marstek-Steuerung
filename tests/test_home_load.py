"""Live solar total and home-balance helpers (tracking/home_load.py).

Pins the #354 fix: the system solar power counts the battery's own MPPT
panels, not only the configured external sensor, and survives the external
sensor being gone.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.omnibattery.tracking.home_load import (
    has_battery_charging,
    home_balance_is_suspicious,
    read_total_solar_power_kw,
)
from tests.conftest import FakeCoordinator


class _FakeStates:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, entity_id):
        return self._mapping.get(entity_id)


def _w(value):
    """A power state in watts."""
    return SimpleNamespace(state=str(value), attributes={"unit_of_measurement": "W"})


class _Reader:
    """Binds hass + controller like SystemSolarPowerSensor does."""

    def __init__(self, states, solar_sensor, coordinators):
        self.hass = SimpleNamespace(states=_FakeStates(states))
        self.controller = SimpleNamespace(
            solar_production_sensor=solar_sensor,
            coordinators=coordinators,
        )

    def total_kw(self):
        return read_total_solar_power_kw(self.hass, self.controller)


def _make_solar_tracker(states, solar_sensor, coordinators):
    return _Reader(states, solar_sensor, coordinators)


def _vunit(version, mppt_total, available=True):
    return FakeCoordinator(
        battery_version=version,
        data={"mppt1_power": mppt_total},
        is_available=available,
    )


def _aggregate_pv_unit(solar_power, available=True):
    return SimpleNamespace(
        capabilities=SimpleNamespace(
            has_mppt_pv=False,
            has_solar_telemetry=True,
        ),
        data={"solar_power": solar_power},
        is_available=available,
    )


def _max_ac_unit(solar_power=800, available=True):
    """Solarbank Max AC telemetry must not be treated as independent PV."""
    return SimpleNamespace(
        capabilities=SimpleNamespace(
            has_mppt_pv=False,
            has_solar_telemetry=False,
        ),
        data={"solar_power": solar_power},
        is_available=available,
    )


def test_total_solar_external_only():
    tracker = _make_solar_tracker({"sensor.aps": _w(1500)}, "sensor.aps", [])
    assert tracker.total_kw() == pytest.approx(1.5)


def test_total_solar_ignores_circular_omnibattery_external_sensor():
    tracker = _make_solar_tracker(
        {"sensor.omnibattery_solar_power": _w(999999)},
        "sensor.omnibattery_solar_power",
        [_aggregate_pv_unit(1500)],
    )

    assert tracker.total_kw() == pytest.approx(1.5)
    assert tracker.controller._solar_self_reference_warned is True


def test_total_solar_mppt_only_no_external():
    # No external sensor configured, panels on the Venus MPPT inputs.
    tracker = _make_solar_tracker({}, None, [_vunit("vA", 800)])
    assert tracker.total_kw() == pytest.approx(0.8)


def test_total_solar_aggregate_pv_only_no_external():
    # A unit may report aggregate PV rather than individual MPPT keys.
    tracker = _make_solar_tracker({}, None, [_aggregate_pv_unit(1500)])
    assert tracker.total_kw() == pytest.approx(1.5)


def test_total_solar_external_plus_aggregate_pv():
    tracker = _make_solar_tracker(
        {"sensor.aps": _w(1500)}, "sensor.aps", [_aggregate_pv_unit(800)]
    )
    assert tracker.total_kw() == pytest.approx(2.3)


def test_total_solar_max_ac_with_external_uses_only_external_sensor():
    tracker = _make_solar_tracker(
        {"sensor.aps": _w(1500)}, "sensor.aps", [_max_ac_unit(800)]
    )
    assert tracker.total_kw() == pytest.approx(1.5)


def test_total_solar_external_plus_mppt():
    tracker = _make_solar_tracker(
        {"sensor.aps": _w(1500)}, "sensor.aps", [_vunit("vA", 800), _vunit("vD", 200)]
    )
    assert tracker.total_kw() == pytest.approx(2.5)


def test_total_solar_ignores_non_pv_versions():
    # v2 has no MPPT registers; it must not contribute.
    tracker = _make_solar_tracker({}, None, [_vunit("v2", 999)])
    assert tracker.total_kw() is None


def test_total_solar_none_when_no_source():
    tracker = _make_solar_tracker({}, None, [])
    assert tracker.total_kw() is None


def test_total_solar_skips_disconnected_unit():
    # A disconnected unit keeps its last MPPT reading (coordinator.data is merged,
    # never expired). It must not be counted, or the solar total inflates.
    tracker = _make_solar_tracker(
        {}, None, [_vunit("vA", 800, available=False)]
    )
    assert tracker.total_kw() is None


def test_total_solar_counts_only_connected_units():
    tracker = _make_solar_tracker(
        {}, None, [_vunit("vA", 800), _vunit("vD", 500, available=False)]
    )
    assert tracker.total_kw() == pytest.approx(0.8)


def test_balance_is_suspicious_when_not_positive():
    assert home_balance_is_suspicious(0.0, battery_charging=False, last_valid_w=None)
    assert home_balance_is_suspicious(-5.0, battery_charging=False, last_valid_w=None)


def test_small_balance_only_suspicious_while_charging():
    assert not home_balance_is_suspicious(10.0, battery_charging=False, last_valid_w=None)
    assert home_balance_is_suspicious(10.0, battery_charging=True, last_valid_w=500.0)
    assert not home_balance_is_suspicious(25.0, battery_charging=True, last_valid_w=500.0)


def test_has_battery_charging_uses_ac_sign_and_skips_unavailable():
    charging = FakeCoordinator(data={"ac_power": -300}, is_available=True)
    offline = FakeCoordinator(data={"ac_power": -300}, is_available=False)
    idle = FakeCoordinator(data={"ac_power": 0}, is_available=True)
    assert has_battery_charging([idle, charging]) is True
    assert has_battery_charging([offline, idle]) is False
