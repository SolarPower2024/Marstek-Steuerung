"""Diagnostics for Omnibattery config entries.

Home Assistant calls :func:`async_get_config_entry_diagnostics` when the user
presses *Download diagnostics* on the integration. It returns a JSON-serialisable
dump of connection health, driver traits and non-responsive-tracker state
(everything that otherwise lives only in transient logs), with host/serial and
connection credentials redacted.
"""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Identifiers and user sensor references that could deanonymise the dump.
# async_redact_data recurses into nested dicts/lists, so per-battery "host"
# entries inside a batteries list are covered too.
# Configured sensor entity ids are intentionally kept: they are often enough
# to diagnose an issue and do not expose connection or authentication details.
TO_REDACT = {
    "host",
    "username",
    "password",
    "serial",
    "serial_port",
    "ip_address",
    "mac",
}


def _json_safe(value: object) -> object:
    """Make best-effort diagnostics values JSON serialisable.

    Diagnostics should remain useful while the controller is starting, being
    reconfigured, or represented by a lightweight test double. In particular,
    blocker records can contain datetimes and arbitrary detail values.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def _driver_info(coordinator) -> dict[str, Any]:
    """Static driver traits (no host/serial, which are identifiers)."""
    driver = coordinator.driver
    caps = coordinator.capabilities
    return {
        "connected": driver.connected,
        "model_label": driver.model_label,
        "capabilities": asdict(caps) if is_dataclass(caps) else str(caps),
    }


def _tracker_info(controller, coordinator) -> dict[str, Any]:
    """Non-responsive exclusion state for one battery (side-effect free)."""
    tracker = getattr(controller, "_non_responsive", None)
    if tracker is None:
        return {}
    info = tracker.batteries.get(coordinator, {})
    return {
        # excluded_names() reads without mutating; is_excluded() would reset the
        # fail counter on cooldown expiry, which a read-only dump must not do.
        "excluded": coordinator.name in tracker.excluded_names(),
        "fail_count": info.get("fail_count", 0),
        "reason": info.get("reason"),
        "retry_attempted": info.get("retry_attempted", False),
        "wake_used": info.get("wake_used", False),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a redacted health/driver/tracker dump for one config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinators = data.get("coordinators") or []
    controller = data.get("controller")

    batteries = [
        {
            "health": coord.health_snapshot(),
            "driver": _driver_info(coord),
            "tracker": _tracker_info(controller, coord),
            "runtime": {
                "battery_manual_mode_enabled": bool(
                    getattr(coord, "battery_manual_mode_enabled", False)
                ),
                "automatic_pool": not bool(
                    getattr(coord, "battery_manual_mode_enabled", False)
                ),
            },
        }
        for coord in coordinators
    ]

    manual_batteries = [
        coord.name for coord in coordinators
        if getattr(coord, "battery_manual_mode_enabled", False)
    ]
    automatic_batteries = [
        coord.name for coord in coordinators
        if not getattr(coord, "battery_manual_mode_enabled", False)
    ]

    consumption_profile = {}
    solar_profile = {}
    vacation = {}
    recorder_backfill = {}
    tracker = getattr(controller, "_consumption_tracker", None)
    profile = getattr(tracker, "consumption_profile", None)
    if profile is not None:
        try:
            consumption_profile = _json_safe(profile.diagnostics())
        except Exception as exc:  # noqa: BLE001
            consumption_profile = {"error": str(exc)}
    backfill_info = getattr(tracker, "backfill_diagnostics", None)
    if callable(backfill_info):
        try:
            recorder_backfill = _json_safe(backfill_info())
        except Exception as exc:  # noqa: BLE001
            recorder_backfill = {"error": str(exc)}
    vacation_info = getattr(tracker, "vacation_diagnostics", None)
    if callable(vacation_info):
        try:
            vacation = _json_safe(vacation_info())
        except Exception as exc:  # noqa: BLE001
            vacation = {"error": str(exc)}
    solar_tracker = getattr(tracker, "solar_profile", None)
    if solar_tracker is not None:
        try:
            solar_profile = _json_safe(solar_tracker.diagnostics())
        except Exception as exc:  # noqa: BLE001
            solar_profile = {"error": str(exc)}

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "batteries": batteries,
        "control_pool": {
            "manual_batteries": manual_batteries,
            "automatic_batteries": automatic_batteries,
        },
        "consumption_profile": consumption_profile,
        "recorder_backfill": recorder_backfill,
        "vacation_learning": vacation,
        "solar_profile": solar_profile,
        "phase_protection": async_redact_data(
            controller._phase_power_limiter.diagnostics(), TO_REDACT
        )
        if controller is not None
        and getattr(controller, "_phase_power_limiter", None) is not None
        else {},
    }
