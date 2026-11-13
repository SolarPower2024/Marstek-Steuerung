"""FORK: relay guard + peak grid source diagnostic sensors."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.omnibattery.sensors.aggregate_sensors import (
    PeakGridSourceSensor,
    RelayGuardSensor,
)

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "omnibattery"

RELAY_STATUS = {
    "state": "min_off",
    "remaining_s": 12.5,
    "switches_last_hour": 3,
    "switches_today": 9,
    "blocked_today": 4,
    "throttled": False,
    "last_switch": "2026-01-01T00:00:00+00:00",
}
GRID_STATUS = {
    "physical_entity": "sensor.meter",
    "physical_w": 420.0,
    "physical_source": "fallback",
    "unusable_since": None,
    "peak_limit_w": 5000,
    "peak_limit_source": "entity",
}


def _make(cls, controller):
    return cls(controller)


def test_relay_guard_state_and_attributes():
    ctrl = SimpleNamespace(get_relay_guard_status=lambda: dict(RELAY_STATUS))
    s = _make(RelayGuardSensor, ctrl)
    assert s.native_value == "min_off"
    attrs = s.extra_state_attributes
    assert set(attrs) == set(RelayGuardSensor._ATTRS)
    assert attrs["remaining_s"] == 12.5
    assert attrs["switches_today"] == 9
    assert s._attr_unique_id == "marstek_venus_system_relay_guard"
    assert s._attr_translation_key == "system_relay_guard"
    assert s.entity_id == "sensor.omnibattery_system_relay_guard" or s.entity_id.endswith("relay_guard")


def test_relay_guard_missing_or_unknown():
    assert _make(RelayGuardSensor, SimpleNamespace()).native_value is None
    ctrl = SimpleNamespace(get_relay_guard_status=lambda: {"state": "weird"})
    assert _make(RelayGuardSensor, ctrl).native_value is None


def test_peak_grid_source_state_and_attributes():
    view = SimpleNamespace(status=lambda: dict(GRID_STATUS))
    s = _make(PeakGridSourceSensor, SimpleNamespace(_grid_view=view))
    assert s.native_value == "fallback"
    attrs = s.extra_state_attributes
    assert set(attrs) == set(PeakGridSourceSensor._ATTRS)
    assert attrs["peak_limit_w"] == 5000
    assert s._attr_unique_id == "marstek_venus_system_peak_grid_source"
    assert s._attr_translation_key == "system_peak_grid_source"


def test_peak_grid_source_without_grid_view():
    s = _make(PeakGridSourceSensor, SimpleNamespace())
    assert s.native_value is None
    assert s.extra_state_attributes["physical_w"] is None


@pytest.mark.parametrize(
    "rel",
    ["strings.json", "translations/en.json", "translations/de.json"]
    + [f"translations/{lang}.json" for lang in ("es", "fr", "nl", "ca")],
)
def test_translations_cover_all_states(rel):
    sensor = json.loads((PKG / rel).read_text(encoding="utf-8"))["entity"]["sensor"]
    for key, cls in (
        ("system_relay_guard", RelayGuardSensor),
        ("system_peak_grid_source", PeakGridSourceSensor),
    ):
        assert sensor[key]["name"]
        for state in cls._STATES:
            assert sensor[key]["state"][state]


def test_german_names():
    de = json.loads((PKG / "translations/de.json").read_text(encoding="utf-8"))["entity"]["sensor"]
    assert de["system_relay_guard"]["name"] == "Relais-Schutz"
    assert de["system_peak_grid_source"]["name"] == "Peak-Netzquelle"
