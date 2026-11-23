"""Tests for which battery is filled first, and with how much.

Charge power differs by an order of magnitude between an AC battery and a hybrid
inverter. Sharing a surplus by power limit hands the slow one the smaller share
-- while it is the one needing the most hours to finish. Measured on the
reference installation: the 7 kW inverter full by early afternoon, the 2.5 kW
battery still at 52 % at sunset with a kilowatt going to the grid.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.omnibattery.control.charge_order import (
    battery_remaining_kwh,
    charge_order,
    time_to_full_h,
)


class _Battery:
    """A stand-in coordinator.

    A class rather than a namespace: the distributor keys its allocation
    dictionaries by coordinator.
    """

    def __init__(self, name, *, capacity, soc, limit_w, dc_coupled=False, max_soc=100):
        self.name = name
        self.battery_capacity_kwh = capacity
        self.max_soc = max_soc
        self.data = {"battery_soc": soc}
        self.is_available = True
        self.driver = SimpleNamespace(dc_coupled=dc_coupled)
        self._limit_w = limit_w


def _battery(name, **kwargs):
    return _Battery(name, **kwargs)


def _controller(batteries, *, priority=""):
    """A controller stub carrying the pieces the charge order reads."""
    return SimpleNamespace(
        coordinators=batteries,
        charge_priority=priority,
        _active_charge_batteries=[],
        _battery_power_limit=lambda coordinator, is_charging: coordinator._limit_w,
    )


def _reference():
    """The reference installation mid-afternoon: hybrid full, AC battery half."""
    return [
        _battery("Marstek", capacity=15.36, soc=52, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=100, limit_w=7000, dc_coupled=True),
    ]


# ----------------------------------------------------------------------
# how long each battery still needs
# ----------------------------------------------------------------------
def test_remaining_energy_respects_the_configured_ceiling():
    battery = _battery("Marstek", capacity=15.36, soc=50, limit_w=2500, max_soc=90)
    assert battery_remaining_kwh(battery) == pytest.approx(6.144)


def test_a_full_battery_wants_nothing():
    assert battery_remaining_kwh(_reference()[1]) == 0.0


def test_hours_to_full_is_energy_over_power_not_state_of_charge():
    """The 7 kW inverter at 40 % is quicker than the 2.5 kW battery at 60 %."""
    controller = _controller([])
    slow = _battery("Marstek", capacity=15.36, soc=60, limit_w=2500)
    fast = _battery("Hybrid", capacity=13.8, soc=40, limit_w=7000, dc_coupled=True)
    assert time_to_full_h(controller, slow) == pytest.approx(2.458, abs=0.01)
    assert time_to_full_h(controller, fast) == pytest.approx(1.183, abs=0.01)
    # Lower state of charge, yet done sooner -- which is the whole point.
    assert time_to_full_h(controller, fast) < time_to_full_h(controller, slow)


def test_a_battery_that_cannot_charge_needs_no_hours():
    blocked = _battery("Marstek", capacity=15.36, soc=10, limit_w=0)
    assert time_to_full_h(_controller([]), blocked) == 0.0


# ----------------------------------------------------------------------
# the order
# ----------------------------------------------------------------------
def test_the_slow_battery_goes_first():
    batteries = [
        _battery("Marstek", capacity=15.36, soc=20, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=20, limit_w=7000, dc_coupled=True),
    ]
    controller = _controller(batteries)
    assert [c.name for c in charge_order(controller, batteries)] == ["Marstek", "Hybrid"]


def test_a_nominated_battery_overrides_the_order():
    batteries = [
        _battery("Marstek", capacity=15.36, soc=20, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=20, limit_w=7000, dc_coupled=True),
    ]
    controller = _controller(batteries, priority="Hybrid")
    assert charge_order(controller, batteries)[0].name == "Hybrid"


def test_a_charging_battery_keeps_a_small_edge():
    """Nearly equal claims must not trade places from one cycle to the next."""
    a = _battery("A", capacity=10.0, soc=50, limit_w=1000)
    b = _battery("B", capacity=10.0, soc=49, limit_w=1000)
    controller = _controller([a, b])
    assert charge_order(controller, [a, b])[0].name == "B"
    controller._active_charge_batteries = [a]
    assert charge_order(controller, [a, b])[0].name == "A"


# ----------------------------------------------------------------------
# how the surplus is split
# ----------------------------------------------------------------------
def _distributor(batteries):
    from custom_components.omnibattery.control.power_distribution import PowerDistribution

    controller = _controller(batteries)
    controller._clamp_to_system_capacity = lambda power, _b, _c: power
    selector = PowerDistribution.__new__(PowerDistribution)
    selector._controller = controller
    selector._is_battery_manual_owned = lambda _c: False
    return selector


def _split(batteries, watts):
    allocation = _distributor(batteries)._distribute_power_by_limits(
        watts, batteries, is_charging=True
    )
    return {c.name: allocation[c] for c in batteries}


def test_the_split_follows_the_room_left_not_the_power_rating():
    """4 kW across a 2.5 kW battery with room and a 7 kW one nearly full.

    By power rating the slow one was offered 1053 W of this while the inverter
    took 2947 W -- and was still at 52 % at sunset with a kilowatt exported.
    """
    batteries = [
        _battery("Marstek", capacity=15.36, soc=20, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=90, limit_w=7000, dc_coupled=True),
    ]
    split = _split(batteries, 4000)
    assert split["Marstek"] == 2500
    assert split["Hybrid"] == 1500


def test_two_alike_batteries_still_share_evenly():
    batteries = [
        _battery("A", capacity=10.0, soc=50, limit_w=1000),
        _battery("B", capacity=10.0, soc=50, limit_w=1000),
    ]
    assert _split(batteries, 1000) == {"A": 500, "B": 500}


def test_batteries_are_aimed_at_finishing_together():
    """Shares proportional to the room left means one finish time, not two."""
    batteries = [
        _battery("Marstek", capacity=15.36, soc=67.4, limit_w=2500),   # 5.0 kWh left
        _battery("Hybrid", capacity=13.8, soc=0, limit_w=7000, dc_coupled=True),
    ]
    split = _split(batteries, 4000)
    hours = {
        "Marstek": 5.0 / (split["Marstek"] / 1000),
        "Hybrid": 13.8 / (split["Hybrid"] / 1000),
    }
    assert hours["Marstek"] == pytest.approx(hours["Hybrid"], rel=0.02)


def test_a_full_battery_is_passed_over():
    assert _split(_reference(), 2000) == {"Marstek": 2000, "Hybrid": 0}


def test_nothing_is_left_on_the_table():
    """Surplus stops flowing only once every battery is at its limit."""
    batteries = [
        _battery("Marstek", capacity=15.36, soc=0, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=0, limit_w=7000, dc_coupled=True),
    ]
    assert sum(_split(batteries, 6000).values()) == 6000


def test_an_unknown_capacity_falls_back_to_the_old_share():
    batteries = [
        _battery("A", capacity=0, soc=50, limit_w=1000),
        _battery("B", capacity=0, soc=50, limit_w=3000),
    ]
    for battery in batteries:
        battery.data = {}
    assert _split(batteries, 2000) == {"A": 500, "B": 1500}


def test_one_unknown_capacity_takes_the_whole_split_back_to_ratings():
    """Mixing watt-hours and watts in one normalization is not a weaker answer,
    it is a meaningless one."""
    known = _battery("A", capacity=10.0, soc=50, limit_w=1000)
    unknown = _battery("B", capacity=0, soc=50, limit_w=3000)
    unknown.data = {}
    assert _split([known, unknown], 2000) == {"A": 500, "B": 1500}


def test_discharge_still_shares_by_power():
    """Only charging changed; discharge is a question of who can deliver now."""
    batteries = [
        _battery("Marstek", capacity=15.36, soc=50, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=50, limit_w=7000, dc_coupled=True),
    ]
    allocation = _distributor(batteries)._distribute_power_by_limits(
        4750, batteries, is_charging=False
    )
    assert {c.name: allocation[c] for c in batteries} == {"Marstek": 1250, "Hybrid": 3500}


def test_the_head_battery_reaches_its_own_rating():
    """What the surplus feedforward is for. Mid-morning on the reference
    installation: the hybrid at 97 % with almost no room, the AC battery at 21 %
    with 12 kWh of it. A system figure capped at 2500 W left the AC battery on
    2418 -- its share of its own rating -- while 6.8 kW of surplus went past."""
    batteries = [
        _battery("Marstek", capacity=15.36, soc=21, limit_w=2500),
        _battery("Hybrid", capacity=13.8, soc=97, limit_w=7000, dc_coupled=True),
    ]
    assert _split(batteries, 2500)["Marstek"] == 2420    # the old, capped figure
    assert _split(batteries, 6828)["Marstek"] == 2500    # the whole surplus


# ----------------------------------------------------------------------
# the discharge ladder is untouched apart from the primary
# ----------------------------------------------------------------------
def _ladder(batteries, primary="", is_charging=False, **kwargs):
    from custom_components.omnibattery.control.power_distribution import PowerDistribution

    controller = _controller(batteries, **kwargs)
    controller.primary_battery = primary
    controller._active_discharge_batteries = []
    selector = PowerDistribution.__new__(PowerDistribution)
    selector._controller = controller
    selector._is_battery_manual_owned = lambda _c: False
    return [
        c.name
        for c in selector._ordered_batteries_for_operation(batteries, is_charging)
    ]


def test_the_selector_brings_batteries_in_by_hours_not_by_state_of_charge():
    """The ordering reaches the selector, not just the helper: the emptier
    hybrid still finishes sooner, so the slow battery has to start first."""
    batteries = [
        _battery("Marstek", capacity=15.36, soc=30, limit_w=2500),   # 4.3 h
        _battery("Hybrid", capacity=13.8, soc=10, limit_w=7000, dc_coupled=True),  # 1.8 h
    ]
    assert _ladder(batteries, is_charging=True) == [
        "Marstek", "Hybrid",
    ]


def test_discharge_normally_starts_with_the_fullest():
    batteries = [
        _battery("Hybrid", capacity=13.8, soc=40, limit_w=7000, dc_coupled=True),
        _battery("Marstek", capacity=15.36, soc=55, limit_w=2500),
    ]
    assert _ladder(batteries) == ["Marstek", "Hybrid"]


def test_a_nominated_primary_goes_first_even_when_emptier():
    batteries = [
        _battery("Hybrid", capacity=13.8, soc=90, limit_w=7000, dc_coupled=True),
        _battery("Marstek", capacity=15.36, soc=20, limit_w=2500),
    ]
    assert _ladder(batteries, primary="Marstek") == ["Marstek", "Hybrid"]
