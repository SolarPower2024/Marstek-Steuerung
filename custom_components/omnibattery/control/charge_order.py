"""Which battery is filled first, and how much of the surplus each one gets.

Two different questions, kept apart on purpose:

* :func:`charge_order` decides who has to *start* first, which matters while
  the surplus is too small to activate every battery.
* :func:`charge_allocation_weights` decides how a surplus large enough for
  several of them is split, and the answer is the room each has left rather
  than its power rating.

Sharing by power rating is the wrong shape for charging. Charge power differs by
an order of magnitude between an AC battery and a hybrid inverter, so the
proportional share hands the slow one the smaller slice -- while it is the one
needing the most hours to finish. Observed: a 2.5 kW battery offered 1053 W of a
4 kW surplus beside a 7 kW inverter taking 2947 W; the inverter full by early
afternoon and the other still at 52 % at sunset with a kilowatt going to the
grid. Shares proportional to the room left aim every battery at the same finish
time, which is the earliest any of them can be done. Discharge is untouched:
there the question is who can deliver now, and the power rating answers it.

The order: a nominated battery first, then the one needing the most hours at
full power, because it is the one at risk of not finishing.
"""
from __future__ import annotations


def battery_remaining_kwh(coordinator):
    """How much room a battery still has, in kWh, or None if unknown."""
    capacity = getattr(coordinator, "battery_capacity_kwh", 0) or 0
    if capacity <= 0 and coordinator.data:
        capacity = coordinator.data.get("battery_total_energy") or 0
    if not capacity:
        return None
    soc = coordinator.data.get("battery_soc") if coordinator.data else None
    if soc is None:
        return None
    ceiling = float(getattr(coordinator, "max_soc", 100) or 100)
    return max(0.0, capacity * (ceiling - float(soc)) / 100.0)


def time_to_full_h(controller, coordinator) -> float:
    """Hours of charging at full power before this battery is done.

    The criterion that decides which battery has to start first. Charge power
    differs by an order of magnitude across a mixed fleet, so ordering by state
    of charge says nothing about who is at risk of not finishing before the sun
    goes.
    """
    remaining = battery_remaining_kwh(coordinator)
    if remaining is None:
        return 0.0
    limit = controller._battery_power_limit(coordinator, True)
    if limit <= 0:
        return 0.0
    return remaining / (limit / 1000.0)


def charge_allocation_weights(batteries, limits) -> dict:
    """What each battery's share of a charge surplus is measured against.

    The room it has left, so the fleet finishes together rather than the fastest
    one finishing first and the slowest running out of daylight. Falls back to
    the power limits -- the previous behaviour -- when the room cannot be worked
    out for *every* battery: a mixed sum of watt-hours and watts is not a
    weaker answer, it is a meaningless one.
    """
    weights = {}
    for coordinator in batteries:
        remaining = battery_remaining_kwh(coordinator)
        if remaining is None:
            return dict(limits)
        weights[coordinator] = remaining * 1000.0
    if sum(weights.values()) <= 0:
        # Everything full: nothing to weigh, and dividing by it would raise.
        return dict(limits)
    return weights


def charge_order(controller, batteries) -> list:
    """Batteries in the order they should be filled.

    Longest time to full first -- that battery is the one at risk of not
    finishing, and the others can catch up in the time it needs anyway. A
    nominated battery overrides this.
    """
    named = (getattr(controller, "charge_priority", "") or "").strip()
    active = getattr(controller, "_active_charge_batteries", None) or []

    def sort_key(coordinator):
        chosen = 0 if coordinator.name == named else 1
        # A battery already charging keeps a small edge, so two of them with
        # nearly equal claims do not trade places from one cycle to the next.
        head_start = 1.1 if coordinator in active else 1.0
        return (chosen, -time_to_full_h(controller, coordinator) * head_start)

    return sorted(batteries, key=sort_key)
