"""Tests for the Time Slot charge SOC cap in ``_effective_charge_max_soc``.

A Time Slot that explicitly caps charging (``soc_override_enabled`` +
``soc_max``) bounds the charge ceiling; a weekly full charge that has unlocked
100% still overrides it.

Exercised unbound with the shared FakeCoordinator and a light controller stub
(same pattern as test_charge_hysteresis.py).
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.omnibattery import ChargeDischargeController

from tests.conftest import FakeCoordinator


def _coord(soc=50, *, max_soc=95):
    return FakeCoordinator(max_soc=max_soc, data={"battery_soc": soc})


def _ctrl(coord, *, slot_max=None):
    slot = None
    if slot_max is not None:
        slot = {
            "enabled": True,
            "allow_charge": True,
            "soc_override_enabled": True,
            "battery_limits": {"battery_1": {"soc_max": slot_max}},
        }
    return SimpleNamespace(
        coordinators=[coord],
        _get_active_slot=lambda c, direction="any": slot if direction == "charge" else None,
        _slot_battery_limits=lambda s, c: s.get("battery_limits", {})["battery_1"],
    )


def _ceiling(ctrl, coord, weekly_unlocked=False):
    return ChargeDischargeController._effective_charge_max_soc(
        ctrl, coord, weekly_unlocked
    )


def test_no_slot_falls_back_to_max_soc():
    c = _coord()
    assert _ceiling(_ctrl(c), c) == (95, "max_soc")


def test_slot_cap_applies():
    c = _coord()
    assert _ceiling(_ctrl(c, slot_max=80), c) == (80, "slot_soc_override")


def test_weekly_full_charge_overrides_the_slot_cap():
    c = _coord()
    assert _ceiling(_ctrl(c, slot_max=80), c, weekly_unlocked=True) == (
        100,
        "weekly_full_charge",
    )


def test_unparsable_slot_cap_is_ignored():
    c = _coord()
    assert _ceiling(_ctrl(c, slot_max="not-a-number"), c) == (95, "max_soc")
