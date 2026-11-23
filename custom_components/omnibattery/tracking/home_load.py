"""Helpers for the live Home Consumption and System Solar Power sensors."""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from ..drivers.base import DELIVERED_AC_POWER_KEY, has_connected_mppt_pv
from ..infra.entity_naming import is_omnibattery_solar_entity

_LOGGER = logging.getLogger(__name__)


# Grid, solar and battery telemetry are published independently. During a
# battery charge, a short-lived mismatch can make the derived household
# balance negative or implausibly small even though the house is still using
# power. The display sensor keeps its last coherent value during that mismatch.
HOME_CONSUMPTION_MIN_BALANCE_W = 20.0


def coordinator_ac_power_w(coordinator: Any) -> float | None:
    """Return a coordinator's signed AC power in watts.

    Same source order as the aggregate Home Consumption sensor: the device's own
    AC port (``ac_delivered_power``, published in ``battery_power`` convention)
    before Marstek's ``ac_power`` register, and cell-side ``battery_power`` only
    as a last resort — on a battery with PV on its own DC bus the cells read
    "charging" from the sun with nothing crossing the AC port, which would bill
    the array to the house (issue #453).
    """
    data = getattr(coordinator, "data", None)
    if not data:
        return None
    value = data.get(DELIVERED_AC_POWER_KEY)
    if value is not None:
        value = -value
    else:
        value = data.get("ac_power")
    if value is None:
        battery_power = data.get("battery_power")
        if battery_power is None:
            return None
        value = -battery_power
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def has_battery_charging(coordinators: Any) -> bool:
    """Return whether an available battery is currently charging."""
    for coordinator in coordinators or ():
        if getattr(coordinator, "is_available", True) is False:
            continue
        ac_power_w = coordinator_ac_power_w(coordinator)
        if ac_power_w is not None and ac_power_w < -1.0:
            return True
    return False


def home_balance_is_suspicious(
    balance_w: float,
    *,
    battery_charging: bool,
    last_valid_w: float | None,
) -> bool:
    """Identify an impossible or transiently collapsed home-power balance.

    A positive low load can be legitimate when no battery is charging. While
    charging, only a balance below the small absolute floor is considered
    suspicious. A relative-to-previous-value threshold is intentionally avoided:
    real household demand can change by more than half between samples, and
    rejecting that change turns a valid positive reading into ``unknown``.
    """
    if not math.isfinite(balance_w) or balance_w <= 0.0:
        return True
    if not battery_charging:
        return False

    return balance_w < HOME_CONSUMPTION_MIN_BALANCE_W


def read_power_kw(hass: Any, entity_id: str, owner: Any = None) -> Optional[float]:
    """Read a power entity and return its value in kW, or None if unusable.

    ``owner`` (normally the controller) carries the warn-once flag for an
    OmniBattery solar output configured as its own external input.
    """
    if is_omnibattery_solar_entity(hass, entity_id):
        if not getattr(owner, "_solar_self_reference_warned", False):
            _LOGGER.error(
                "Ignoring invalid solar production sensor %s: an OmniBattery "
                "solar output cannot be reused as an external input",
                entity_id,
            )
            if owner is not None:
                owner._solar_self_reference_warned = True
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        value = float(state.state)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value) or value < 0.0:
        return None
    unit = str(state.attributes.get("unit_of_measurement", "W")).strip().lower()
    if unit == "kw":
        return value
    if unit == "w":
        return value / 1000.0
    return None


def read_total_solar_power_kw(hass: Any, controller: Any) -> Optional[float]:
    """Total instantaneous solar production (kW): external sensor + battery PV.

    Sums the configured external solar_production_sensor (e.g. an APS/ECU
    feed) and only battery PV sources marked independent by capabilities.
    Venus vA/vD contributes its MPPT inputs. Returns None only when no source has a
    usable reading.
    """
    total_kw = 0.0
    have_reading = False
    if getattr(controller, "solar_production_sensor", None):
        ext_kw = read_power_kw(hass, controller.solar_production_sensor, controller)
        if ext_kw is not None:
            total_kw += max(0.0, ext_kw)
            have_reading = True
    for coordinator in controller.coordinators:
        # Skip disconnected units: their PV readings go stale (merged dict,
        # never expired) and would inflate the total.
        capabilities = getattr(coordinator, "capabilities", None)
        has_mppt = has_connected_mppt_pv(coordinator)
        # ``has_solar_telemetry`` means an independent PV source here.
        has_aggregate = bool(getattr(capabilities, "has_solar_telemetry", False))
        if not (has_mppt or has_aggregate):
            continue
        if not coordinator.is_available or not coordinator.data:
            continue
        mppt_w = 0.0
        seen = False
        pv_keys = (
            ("mppt1_power", "mppt2_power", "mppt3_power", "mppt4_power")
            if has_mppt
            else ("solar_power",)
        )
        for key in pv_keys:
            value = coordinator.data.get(key)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(parsed) and parsed >= 0.0:
                mppt_w += parsed
                seen = True
        if seen:
            total_kw += max(0.0, mppt_w) / 1000.0
            have_reading = True
    return total_kw if have_reading else None
