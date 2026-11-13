"""FORK: configuration surface for relay guard, physical grid meter, peak limit entity."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.omnibattery.const import (
    CONFIG_NUMBER_DEFINITIONS,
    CONF_CAPACITY_PROTECTION_ENABLED,
    CONF_CAPACITY_PROTECTION_LIMIT_ENTITY,
    CONF_CAPACITY_PROTECTION_LIMIT_MARGIN,
    CONF_PANEL_MINIMAL_OVERVIEW,
    CONF_PD_DIRECTION_LOCK,
    CONF_PD_ENGAGE_MARGIN,
    CONF_PD_GUARD_BYPASS_ERROR,
    CONF_PD_RELAY_COOLDOWN,
    CONF_PD_RELAY_GUARD_ENABLED,
    CONF_PD_RELAY_MIN_OFF,
    CONF_PD_RELAY_SWITCH_BUDGET,
    CONF_PHYSICAL_GRID_INVERTED,
    CONF_PHYSICAL_GRID_SENSOR,
)

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "omnibattery"
DEFS = {d["key"]: d for d in CONFIG_NUMBER_DEFINITIONS}

# key -> (min, max, step, default)
EXPECTED = {
    CONF_PD_RELAY_COOLDOWN: (0, 600, 1, 0),
    CONF_PD_RELAY_MIN_OFF: (0, 600, 5, 0),
    CONF_PD_DIRECTION_LOCK: (0, 900, 5, 0),
    CONF_PD_ENGAGE_MARGIN: (0, 500, 5, 0),
    CONF_PD_GUARD_BYPASS_ERROR: (50, 3000, 10, 200),
    CONF_PD_RELAY_SWITCH_BUDGET: (0, 120, 1, 0),
    CONF_CAPACITY_PROTECTION_LIMIT_MARGIN: (0, 30, 1, 0),
}


def test_key_names():
    assert CONF_PD_RELAY_GUARD_ENABLED == "pd_relay_guard_enabled"
    assert CONF_PHYSICAL_GRID_SENSOR == "physical_grid_sensor"
    assert CONF_PHYSICAL_GRID_INVERTED == "physical_grid_inverted"
    assert CONF_CAPACITY_PROTECTION_LIMIT_ENTITY == "capacity_protection_limit_entity"
    assert CONF_PANEL_MINIMAL_OVERVIEW == "panel_minimal_overview"


@pytest.mark.parametrize("key", EXPECTED)
def test_number_definition(key):
    d = DEFS[key]
    assert (d["min"], d["max"], d["step"], d["default"]) == EXPECTED[key]


def test_cooldown_maximum_is_600():
    assert DEFS[CONF_PD_RELAY_COOLDOWN]["max"] == 600


def test_limit_margin_condition():
    assert DEFS[CONF_CAPACITY_PROTECTION_LIMIT_MARGIN]["condition"] == CONF_CAPACITY_PROTECTION_ENABLED


def _load(rel):
    return json.loads((PKG / rel).read_text(encoding="utf-8"))


FILES = ["strings.json", "translations/en.json", "translations/de.json"] + [
    f"translations/{lang}.json" for lang in ("es", "fr", "nl", "ca")
]


@pytest.mark.parametrize("rel", FILES)
def test_translations_complete(rel):
    data = _load(rel)
    for key in ("pd_relay_guard_enabled", "panel_minimal_overview"):
        assert data["entity"]["switch"][key]["name"]
    for key in (
        CONF_PD_RELAY_MIN_OFF,
        CONF_PD_DIRECTION_LOCK,
        CONF_PD_ENGAGE_MARGIN,
        CONF_PD_GUARD_BYPASS_ERROR,
        CONF_PD_RELAY_SWITCH_BUDGET,
        CONF_CAPACITY_PROTECTION_LIMIT_MARGIN,
    ):
        assert data["entity"]["number"][key]["name"]
    step = data["options"]["step"]["sensors"]
    for key in (CONF_PHYSICAL_GRID_SENSOR, CONF_PHYSICAL_GRID_INVERTED, CONF_CAPACITY_PROTECTION_LIMIT_ENTITY):
        assert step["data"][key]
        assert step["data_description"][key]


def test_german_names():
    de = _load("translations/de.json")
    assert de["entity"]["switch"]["pd_relay_guard_enabled"]["name"] == "Relais-Schutz"
    assert de["entity"]["switch"]["panel_minimal_overview"]["name"] == "Reduzierte Übersicht"
    assert de["entity"]["number"]["pd_direction_lock"]["name"] == "Sperrzeit Richtungswechsel"


def test_missing_keys_mean_old_behaviour():
    data: dict = {}
    assert data.get(CONF_PD_RELAY_GUARD_ENABLED, False) is False
    assert not data.get(CONF_PHYSICAL_GRID_SENSOR)
    assert not data.get(CONF_CAPACITY_PROTECTION_LIMIT_ENTITY)
