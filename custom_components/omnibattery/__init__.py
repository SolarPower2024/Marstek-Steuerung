"""The Omnibattery integration."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    Platform,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
)
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import event as event_helpers
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from pymodbus.exceptions import ConnectionException

from .const import (
    DOMAIN,
    NOTIFICATION_ID_PREFIX,
    CONF_HOUSEHOLD_CONSUMPTION_SENSOR,
    CONF_SOLAR_PRODUCTION_SENSOR,
    CONF_MAX_CONTRACTED_POWER,
    CONF_OFFGRID_POWER_SENSOR,
    CONF_OFFGRID_METER_INVERTED,
    CONF_OFFGRID_MODE_ENABLED,
    CONF_THREE_PHASE_ENABLED,
    CONF_PHASE_1_CURRENT_SENSOR,
    CONF_PHASE_2_CURRENT_SENSOR,
    CONF_PHASE_3_CURRENT_SENSOR,
    CONF_BATTERY_PHASE,
    CONF_BATTERY_VERSION,
    CONF_DC_PV_CONNECTED,
    normalize_battery_phase,
    DEFAULT_THREE_PHASE_ENABLED,
    CONF_ENABLE_WEEKLY_FULL_CHARGE,
    CONF_WEEKLY_FULL_CHARGE_DAY,
    CONF_ENABLE_TEMP_CHARGE_LIMIT,
    DEFAULT_ENABLE_TEMP_CHARGE_LIMIT,
    CONF_TEMP_CHARGE_LIMIT_C,
    DEFAULT_TEMP_CHARGE_LIMIT_C,
    CONF_TEMP_CHARGE_LIMIT_BAND_C,
    DEFAULT_TEMP_CHARGE_LIMIT_BAND_C,
    CONF_TEMP_CHARGE_LIMIT_FLOOR_PCT,
    DEFAULT_TEMP_CHARGE_LIMIT_FLOOR_PCT,
    CONF_TEMP_LIMIT_APPLY_DISCHARGE,
    DEFAULT_TEMP_LIMIT_APPLY_DISCHARGE,
    CONF_PD_KP,
    CONF_PD_KD,
    CONF_PD_DEADBAND,
    CONF_PD_MAX_POWER_CHANGE,
    CONF_PD_DIRECTION_HYSTERESIS,
    DEFAULT_PD_KP,
    DEFAULT_PD_KD,
    DEFAULT_PD_DEADBAND,
    DEFAULT_PD_MAX_POWER_CHANGE,
    DEFAULT_PD_DIRECTION_HYSTERESIS,
    CONF_PD_MIN_CHARGE_POWER,
    CONF_PD_MIN_DISCHARGE_POWER,
    DEFAULT_PD_MIN_CHARGE_POWER,
    DEFAULT_PD_MIN_DISCHARGE_POWER,
    CONF_PD_RELAY_COOLDOWN,
    DEFAULT_PD_RELAY_COOLDOWN,
    RELAY_COOLDOWN_HOLD_POWER,
    CONF_PD_MIN_CYCLE_INTERVAL,
    DEFAULT_PD_MIN_CYCLE_INTERVAL,
    CONF_TARGET_GRID_POWER,
    DEFAULT_TARGET_GRID_POWER,
    CONF_NO_PD_MODE_ENABLED,
    CONF_CHARGE_PRIORITY,
    DEFAULT_CHARGE_PRIORITY,
    CONF_PRIMARY_BATTERY,
    DEFAULT_PRIMARY_BATTERY,
    CONF_PRIMARY_FEEDFORWARD_ENABLED,
    DEFAULT_PRIMARY_FEEDFORWARD_ENABLED,
    CONF_NO_PD_COMMAND_DELAY,
    DEFAULT_NO_PD_MODE_ENABLED,
    DEFAULT_NO_PD_COMMAND_DELAY,
    DEFAULT_GRID_FILTER_TAU,
    CONF_ENABLE_SYSTEM_POWER_LIMITS,
    CONF_SYSTEM_MAX_CHARGE_POWER,
    CONF_SYSTEM_MAX_DISCHARGE_POWER,
    DEFAULT_SYSTEM_MAX_CHARGE_POWER,
    DEFAULT_SYSTEM_MAX_DISCHARGE_POWER,
    CONF_CAPACITY_PROTECTION_ENABLED,
    CONF_CAPACITY_PROTECTION_EXCLUDED_DEVICES,
    CONF_CAPACITY_PROTECTION_SOC_THRESHOLD,
    CONF_CAPACITY_PROTECTION_LIMIT,
    DEFAULT_CAPACITY_PROTECTION_SOC,
    DEFAULT_CAPACITY_PROTECTION_LIMIT,
    CONF_MANUAL_MODE_ENABLED,
    CONF_BATTERY_MANUAL_MODE_ENABLED,
    CONF_METER_INVERTED,
    NORMAL_BALANCE_PAUSE_CELL_VOLTAGE,
    NORMAL_BALANCE_RECAL_INVERTER_STANDBY,
    INVERTER_STATE_AC_BYPASS,
    NORMAL_BALANCE_RECAL_RETRY_CELL_VOLTAGE,
    BMS_DISCHARGE_CUTOFF_SOC,
    LOW_SOC_REFUSAL_CONFIRM_S,
    PD_READBACK_EVERY_N_WRITES,
    ACK_INEXACT_STREAK_WARN,
    FEEDFORWARD_STEP_FLOOR_W,
    FEEDFORWARD_CONFIRM_RATIO,
    FEEDFORWARD_CANDIDATE_MAX_AGE_S,
    FEEDFORWARD_COOLDOWN_S,
    FEEDFORWARD_PULSE_GUARD_S,
    PD_ZERO_CROSS_MIN_HOLD_S,
    SLOW_SENSOR_WARNING_INTERVAL_S,
    MAX_SENSOR_STALE_S,
    MAIN_SENSOR_DEAD_S,
    SLOW_SENSOR_WARN_INTERVALS,
    SLOW_SENSOR_RECOVERY_INTERVALS,
    MISSING_SENSOR_ISSUE_DELAY_S,
    HOT_PATH_READBACK_MAX_LATENCY_S,
    DISCHARGE_ENGAGE_GRACE_S,
    ENGAGE_GRACE_STEP_FACTOR,
    PD_QUALITY_RESTART_GAP_S,
    PD_QUALITY_RESTART_SETTLE_S,
    HIGH_SOC_CHARGE_TAPER_FLOOR,
    HIGH_SOC_CHARGE_TAPER_GRACE_S,
    IDLE_RUNAWAY_POWER_W,
    IDLE_RUNAWAY_GRACE_S,
    DISCHARGE_MIN_SOC_REENTRY_MARGIN,
    CONF_FULL_CHARGE_VOLTAGE_TAPER_ENABLED,
    DEFAULT_FULL_CHARGE_VOLTAGE_TAPER_ENABLED,
    MIN_CHARGE_HYSTERESIS_PERCENT,
    DEFAULT_CHARGE_HYSTERESIS_PERCENT,
    DEBUG_CONTROL_LOOP_DETAIL,
)
from .infra.lifecycle import is_reload_pending
from .control.residual_load import apply_guards, guards_pending
from .infra import cycle_trace  # FORK: trace (docs/fork/08-trace.md)
from .drivers.base import DELIVERED_AC_POWER_KEY, has_connected_mppt_pv
from .infra.coordinator import MarstekVenusDataUpdateCoordinator
from .infra.mac_tracking import publishable_macs
from .infra.device_lookup import get_entry_device
from .tracking.non_responsive_tracker import NonResponsiveTracker
from .control.pack_soc import control_vmax, soc_vs_ceiling
from .control.weekly_full_charge import WeeklyFullChargeManager
from .control.max_soc_charge import MaxSocChargeManager
from .control.temperature_limit import TemperatureChargeLimitManager
from .control.phase_power_limit import PhasePowerLimiter
# FORK: relay guard (docs/fork/02-architektur.md)
from .const import integration_const as _fork_const
from .control.relay_guard import RelayGuard
# FORK: grid view (docs/fork/02-architektur.md)
from .control.grid_view import GridView


_LOGGER = logging.getLogger(__name__)

# Charge taper is voltage-only. SOC is deliberately ignored near the top because
# some batteries report unstable SOC values while cell voltage remains reliable.
FULL_CHARGE_TAPER_STEPS = ()


# List of platforms to support.
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
]

# Sidebar dashboard panel served with the integration.
PANEL_URL_PATH = "omnibattery"
PANEL_STATIC_PATH = "/omnibattery_static"
PANEL_TITLE = "Omnibattery"
PANEL_ICON = "mdi:home-battery"
_PANEL_REGISTERED_KEY = "_panel_registered"
_STATIC_REGISTERED_KEY = "_panel_static_registered"


def _excluded_devices_panel_config(data: dict, ent_reg) -> list[dict]:
    """Return the excluded-load data the flow diagram needs.

    The entity registry lookup preserves live enable/disable toggles when their
    switch is loaded. The persisted value remains the fallback when a user has
    disabled that switch, in which case Home Assistant deliberately creates no
    state for it.
    """
    from .infra.entity_naming import SYSTEM_UNIQUE_ID_PREFIX

    devices = []
    for index, device in enumerate(data.get("excluded_devices", [])):
        enabled_entity = ent_reg.async_get_entity_id(
            "switch",
            DOMAIN,
            f"{SYSTEM_UNIQUE_ID_PREFIX}excluded_device_enabled_{index}",
        )
        devices.append(
            {
                "power_sensor": device.get("power_sensor"),
                "included_in_consumption": device.get(
                    "included_in_consumption", True
                ),
                "enabled": device.get("enabled", True),
                "enabled_entity": enabled_entity,
                "is_ev_charger": device.get("is_ev_charger", False),
                # No-telemetry chargers animate from their state sensor;
                # legacy entries stored it in power_sensor.
                "activity_sensor": device.get("activity_sensor")
                or (
                    device.get("power_sensor")
                    if device.get("ev_charger_no_telemetry")
                    else None
                ),
                "ev_charger_no_telemetry": device.get("ev_charger_no_telemetry", False),
            }
        )
    return devices


def _check_ev_charger_type_notice(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask once to mark EV chargers among excluded devices (flow diagram only).

    Devices saved before the field existed lack the key; re-saving the excluded
    devices in the options flow writes it (True or False) and clears the issue.
    """
    issue_id = f"excluded_device_ev_type_{entry.entry_id}"
    if any("is_ev_charger" not in d for d in entry.data.get("excluded_devices", [])):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="excluded_device_ev_type",
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def _has_battery_reported_solar(coordinators) -> bool:
    """Return whether any connected model has an independent PV source."""
    return any(
        bool(
            has_connected_mppt_pv(coordinator)
            or getattr(
                getattr(coordinator, "capabilities", None),
                "has_solar_telemetry",
                False,
            )
        )
        for coordinator in coordinators or ()
    )


def _panel_solar_entity(coordinators, ent_reg, external_entity: str | None) -> str | None:
    """Choose the panel's complete solar source from live model capabilities.

    The system aggregate is preferred only when a connected battery really
    contributes independent PV. If the aggregate entity is not registered yet
    (the first registration happens before platforms finish) or no such battery
    exists, fall back to the configured external sensor.
    """
    if _has_battery_reported_solar(coordinators):
        solar_entity = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, "marstek_venus_system_solar_power"
        )
        if solar_entity:
            return solar_entity
    return external_entity


async def _async_register_frontend_panel(hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
    """Register (or refresh) the custom sidebar panel.

    Serves the integration's ``frontend`` directory as a static path (once per
    HA run) and (re)registers the ``marstek-venus-panel`` web component as a
    sidebar panel on every setup, so the module URL and config payload refresh
    when the integration reloads. The configured grid/home power sensors are
    forwarded to the panel so the energy-flow diagram can wire its Grid/Home
    nodes without hardcoding.

    The module URL is cache-busted by the JS file's mtime so any edit to the
    dashboard is picked up by the browser after a reload — without needing an
    integration version bump.
    Non-critical: failures are logged but never block integration setup.
    """
    try:
        from pathlib import Path

        from homeassistant.components import frontend, panel_custom
        from homeassistant.components.http import StaticPathConfig

        frontend_dir = Path(__file__).parent / "frontend"
        domain_data = hass.data.setdefault(DOMAIN, {})

        # Static path can only be registered once per HA run.
        if not domain_data.get(_STATIC_REGISTERED_KEY):
            await hass.http.async_register_static_paths(
                [StaticPathConfig(PANEL_STATIC_PATH, str(frontend_dir), cache_headers=False)]
            )
            domain_data[_STATIC_REGISTERED_KEY] = True

        # Cache-bust by the JS file mtime (changes on every edit/deploy); fall
        # back to the integration version if the file can't be stat'd.
        js_file = frontend_dir / "marstek-panel.js"
        try:
            cache_bust = str(int(js_file.stat().st_mtime))
        except Exception:  # noqa: BLE001
            from homeassistant.loader import async_get_integration

            try:
                cache_bust = (await async_get_integration(hass, DOMAIN)).version or "0"
            except Exception:  # noqa: BLE001
                cache_bust = "0"

        panel_config = {"domain": DOMAIN, "title": PANEL_TITLE}
        if entry is not None:
            from .const import CONF_SOLAR_PRODUCTION_SENSOR

            data = entry.data
            # FORK: Panel minimal overview flag (read by the frontend as
            # panel_config.minimal_overview); applied on the next panel registration.
            panel_config["minimal_overview"] = bool(
                data.get(_fork_const.CONF_PANEL_MINIMAL_OVERVIEW, False)
            )
            # consumption_sensor is the net grid meter (+import / -export) the PD
            # loop regulates — it is the Grid node of the flow diagram.
            # FORK: grid view (docs/fork/02-architektur.md), B5 - the real meter, when
            # configured, is the Grid node (with its own invert flag).
            if data.get(_fork_const.CONF_PHYSICAL_GRID_SENSOR):
                panel_config["grid_entity"] = data[_fork_const.CONF_PHYSICAL_GRID_SENSOR]
                panel_config["grid_inverted"] = bool(
                    data.get(
                        _fork_const.CONF_PHYSICAL_GRID_INVERTED,
                        _fork_const.DEFAULT_PHYSICAL_GRID_INVERTED,
                    )
                )
            elif data.get("consumption_sensor"):
                panel_config["grid_entity"] = data["consumption_sensor"]
                # PD convention is +import / -export; if the user's meter is wired
                # the other way the integration negates it (meter_inverted). Forward
                # the flag so the panel applies the same sign to the Grid node and
                # the power-history chart.
                panel_config["grid_inverted"] = bool(data.get(CONF_METER_INVERTED, False))
            # Home node = the integration's derived Home Consumption aggregate sensor
            # (grid + battery AC + solar). The dedicated household sensor was removed
            # from the config flow, so the derived sensor is the single home source.
            # Resolve by the stable unique_id (never changes) instead of a literal
            # entity_id, so the link survives a user "Recreate entity IDs" rename to
            # sensor.omnibattery_home_consumption.
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(hass)
            panel_config["excluded_devices"] = _excluded_devices_panel_config(
                data, ent_reg
            )
            home_eid = ent_reg.async_get_entity_id(
                "sensor", DOMAIN, "marstek_venus_system_home_consumption"
            )
            if home_eid:
                panel_config["home_entity"] = home_eid
            # Solar node click target. Use the capabilities of the live
            # coordinators. The second panel registration at the end of setup
            # refreshes this payload after all models are known.
            coordinator_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
            coordinators = coordinator_data.get("coordinators", ())
            solar_eid = _panel_solar_entity(
                coordinators,
                ent_reg,
                data.get(CONF_SOLAR_PRODUCTION_SENSOR),
            )
            if solar_eid:
                panel_config["solar_entity"] = solar_eid

        # Remove any previous registration so the module URL / config refresh.
        # warn_if_unknown=False: on first setup after restart the panel isn't
        # registered yet, and HA would log "Removing unknown panel marstek-venus".
        try:
            frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        except Exception:  # noqa: BLE001 - not registered yet is fine
            pass

        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name="marstek-venus-panel",
            module_url=f"{PANEL_STATIC_PATH}/marstek-panel.js?v={cache_bust}",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            require_admin=False,
            config=panel_config,
        )
        domain_data[_PANEL_REGISTERED_KEY] = True
        _LOGGER.info(
            "Registered Marstek Venus sidebar panel at /%s (v=%s)", PANEL_URL_PATH, cache_bust
        )
    except Exception as e:  # noqa: BLE001 - panel is optional, never block setup
        _LOGGER.warning("Could not register Marstek Venus sidebar panel: %s", e)


@callback
def _async_unregister_frontend_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel when the last config entry unloads."""
    if not hass.data.get(DOMAIN, {}).get(_PANEL_REGISTERED_KEY):
        return
    try:
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
    except Exception as e:  # noqa: BLE001
        _LOGGER.debug("Error removing Marstek Venus panel: %s", e)
    finally:
        hass.data[DOMAIN][_PANEL_REGISTERED_KEY] = False


def _backup_switch_enabled(value) -> bool:
    """Whether a battery's backup output is armed.

    The register publishes the switch itself: 0 is on, 1 is off.
    """
    if value is None:
        return False
    return value == 0


def _inverter_in_ac_bypass(value) -> bool:
    """Whether the reported inverter state is the grid-bypass state (raw code 6)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == INVERTER_STATE_AC_BYPASS


def _delivered_toward(data: dict, cell_power: float, *, is_charge: bool) -> float:
    """Power (W, unsigned) delivered toward the commanded direction.

    Two independent signals can prove a move command is being followed: the cells
    (``battery_power``) and, on drivers that measure it, the device's own AC port
    (``DELIVERED_AC_POWER_KEY``). They disagree whenever PV feeds the same DC bus
    as the actuator — the battery can export the commanded discharge while its
    cells keep charging from surplus PV, or absorb a commanded charge straight
    from that PV with nothing crossing the AC port (issue #399). Judging on the
    cells alone reads the first case as 0 W out and excludes a healthy battery.

    Take whichever signal shows the command being obeyed. Drivers that publish no
    AC value keep the historical cell-only judgement unchanged.
    """
    sign = 1.0 if is_charge else -1.0
    delivered = sign * float(cell_power)
    ac_power = data.get(DELIVERED_AC_POWER_KEY)
    if ac_power is not None:
        delivered = max(delivered, sign * float(ac_power))
    return max(0.0, delivered)


def _charge_hysteresis_latch_is_stale(coordinator) -> bool:
    """Whether max_soc was raised above the ceiling the hysteresis latched under.

    Such a latch captured a lower, since-raised ceiling (e.g. Target SOC bumped
    back up after a temporary reduction) and must not hold the battery below the
    new target. A latch set below the ceiling by a BMS cutoff or the top-cell
    voltage is not stale: it holds until SOC falls through the hysteresis band,
    like a latch at the ceiling. A latch without a recorded ceiling is judged
    against its base SOC.
    """
    base = coordinator._hysteresis_base_soc
    if base is None:
        return False
    latched_under = getattr(coordinator, "_hysteresis_latch_max_soc", None)
    return coordinator.max_soc > (base if latched_under is None else latched_under)


class ChargeDischargeController:
    """Controller to manage charge/discharge logic for all batteries."""

    def __init__(self, hass: HomeAssistant, coordinators: list[MarstekVenusDataUpdateCoordinator], consumption_sensor: str, config_entry: ConfigEntry):
        """Initialize the controller."""
        self.hass = hass
        self.coordinators = coordinators
        self.config_entry = config_entry
        self.primary_consumption_sensor = consumption_sensor
        self.offgrid_power_sensor = config_entry.data.get(CONF_OFFGRID_POWER_SENSOR)
        self.offgrid_meter_inverted = config_entry.data.get(
            CONF_OFFGRID_METER_INVERTED, False
        )
        self.offgrid_mode_enabled = bool(
            self.offgrid_power_sensor
            and config_entry.data.get(CONF_OFFGRID_MODE_ENABLED, False)
        )

        # State tracking
        self.previous_sensor = None
        self.previous_power = 0
        self.first_execution = True
        self._phase_safety_pending = False

        # Grid meter options
        self.meter_inverted = config_entry.data.get(CONF_METER_INVERTED, False)
        # Phase protection is deliberately isolated from the global target
        # calculation.  It only constrains automatic battery assignments.
        self._phase_power_limiter = PhasePowerLimiter(hass, config_entry, self)

        # Load PD controller parameters from config (with backward-compatible defaults)
        self.deadband = config_entry.data.get(CONF_PD_DEADBAND, DEFAULT_PD_DEADBAND)
        self.kp = config_entry.data.get(CONF_PD_KP, DEFAULT_PD_KP)
        self.kd = config_entry.data.get(CONF_PD_KD, DEFAULT_PD_KD)
        self.max_power_change_per_cycle = config_entry.data.get(CONF_PD_MAX_POWER_CHANGE, DEFAULT_PD_MAX_POWER_CHANGE)
        self.direction_hysteresis = config_entry.data.get(CONF_PD_DIRECTION_HYSTERESIS, DEFAULT_PD_DIRECTION_HYSTERESIS)
        self.min_charge_power = config_entry.data.get(CONF_PD_MIN_CHARGE_POWER, DEFAULT_PD_MIN_CHARGE_POWER)
        self.min_discharge_power = config_entry.data.get(CONF_PD_MIN_DISCHARGE_POWER, DEFAULT_PD_MIN_DISCHARGE_POWER)
        # Relay anti-chatter (shut-off dwell). _relay_shutoff_since is stamped the
        # moment the controller first asks the battery to return to idle; the dwell
        # keeps it engaged at minimum power until _relay_cooldown_s elapses from that
        # instant, so the relay doesn't click off as soon as demand falls.
        self._relay_cooldown_s = config_entry.data.get(CONF_PD_RELAY_COOLDOWN, DEFAULT_PD_RELAY_COOLDOWN)
        self._relay_shutoff_since = None
        # FORK: relay guard (docs/fork/02-architektur.md)
        self._relay_guard = RelayGuard()
        self._relay_guard_day = None
        self._pd_relay_guard_enabled = False
        self._configure_relay_guard()
        # FORK: grid view (docs/fork/02-architektur.md)
        self._grid_view = GridView()
        self._last_physical_grid_w = None
        self._peak_physical_prev = False  # FORK: review 1/2 - last cycle shaved on real meter
        self._peak_exit_since = None  # FORK: review 2/7 - peak exit hold timer (monotonic)
        self._physical_trigger_pending = False  # FORK: review 6 - cycle requested by real meter
        self._configure_grid_view()
        # Zero-cross hold (direction-flip dwell): stamped on the first cycle that
        # requests the opposite charge/discharge direction; the flip is clamped to
        # 0 until the request persists past the actuator settle window.
        self._zero_cross_since = None
        # True when this cycle's 0 W request came from the direction hysteresis
        # suppressing a flip (not from a real idle decision): the armed
        # zero-cross timer must survive it (#849).
        self._flip_hyst_suppressed = False
        # Event-driven cycle rate limit: drop grid-sensor triggers that arrive
        # closer together than this, so fast meters can't flood the Modbus bridge.
        # NOT raised to the slowest actuator's latency: a slow battery must not
        # throttle the shared loop for the whole fleet. Slow-actuator pacing
        # belongs per-battery in the power distribution, not in the loop cadence.
        self._min_cycle_interval_s = config_entry.data.get(CONF_PD_MIN_CYCLE_INTERVAL, DEFAULT_PD_MIN_CYCLE_INTERVAL)
        self._last_cycle_monotonic = 0.0
        # Set by a cycle that found no new meter sample and kept the last command.
        self._cycle_kept_last_command = False
        self._timer_tick_paced = False
        self._background_tasks: set[asyncio.Task] = set()
        self._unloading = False
        self.target_grid_power = config_entry.data.get(CONF_TARGET_GRID_POWER, DEFAULT_TARGET_GRID_POWER)
        # No-PD direct-tracking mode (opt-in): see _apply_no_pd_overrides. Overrides
        # are applied at the end of __init__, after the grid filter tau is set below.
        self.no_pd_mode_enabled = config_entry.data.get(CONF_NO_PD_MODE_ENABLED, DEFAULT_NO_PD_MODE_ENABLED)
        # Mixed-fleet control (see control/residual_load.py and control/charge_order.py).
        # Which battery is filled first, which serves the house first, and whether
        # that one is handed the real demand instead of waiting for a grid error.
        self.charge_priority = config_entry.data.get(CONF_CHARGE_PRIORITY, DEFAULT_CHARGE_PRIORITY)
        self.primary_battery = config_entry.data.get(CONF_PRIMARY_BATTERY, DEFAULT_PRIMARY_BATTERY)
        self.primary_feedforward_enabled = config_entry.data.get(
            CONF_PRIMARY_FEEDFORWARD_ENABLED, DEFAULT_PRIMARY_FEEDFORWARD_ENABLED
        )
        # Latched while there is a surplus to spare, so a cloud edge cannot toggle
        # the battery in step with the light.
        self._surplus_guard_latched = False
        # When the surplus that would latch it was first seen (None: not seen).
        self._surplus_guard_since = None
        self._no_pd_command_delay = config_entry.data.get(CONF_NO_PD_COMMAND_DELAY, DEFAULT_NO_PD_COMMAND_DELAY)
        self._no_pd_debounce_unsub = None  # cancel handle for a pending debounced cycle
        self.enable_system_power_limits = config_entry.data.get(
            CONF_ENABLE_SYSTEM_POWER_LIMITS,
            (
                (config_entry.data.get(CONF_SYSTEM_MAX_CHARGE_POWER, DEFAULT_SYSTEM_MAX_CHARGE_POWER) or 0) > 0
                or (config_entry.data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, DEFAULT_SYSTEM_MAX_DISCHARGE_POWER) or 0) > 0
            ),
        )
        self.system_max_charge_power = config_entry.data.get(CONF_SYSTEM_MAX_CHARGE_POWER, DEFAULT_SYSTEM_MAX_CHARGE_POWER)
        self.system_max_discharge_power = config_entry.data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, DEFAULT_SYSTEM_MAX_DISCHARGE_POWER)

        # Sensor filtering to avoid reacting to instantaneous spikes. Time-constant
        # EMA (alpha = elapsed/(tau+elapsed)) instead of a fixed N-sample average, so
        # the smoothing time stays constant under the variable event-driven cadence.
        self._grid_filter_tau = DEFAULT_GRID_FILTER_TAU  # seconds; larger = smoother but more lag
        self._grid_filter_ema = None    # filtered grid value (W); None until first sample

        # PID controller state variables (Ki currently disabled)
        self.ki = 0.0          # Integral gain (DISABLED - using pure PD control)
        self.error_integral = 0.0      # Accumulated error
        self.previous_error = 0.0      # Previous error for derivative
        self.dt = 2.0                  # Nominal control loop time (s); used to normalize cadence-dependent terms
        self.integral_decay = 0.90     # Leaky integrator: 10% decay per cycle

        # Derivative low-pass filter: smooth the noisy grid derivative so the D term
        # does not inject sensor/PWM/quantization noise into the output. EMA whose
        # alpha is computed per-cycle from real elapsed time (alpha = dt/(tau+dt)).
        self.derivative_tau = 3.0       # seconds; larger = smoother but more lag
        self.derivative_filtered = 0.0  # filtered derivative state

        # Feedforward step detection state (see _check_feedforward_step)
        self._step_candidate = None              # (baseline_error_w, jump_w, monotonic_ts) awaiting confirmation
        self._last_feedforward_monotonic = None  # monotonic ts of the last feedforward fire
        self._last_feedforward_sign = 0          # sign of the last fired jump (+1/-1)

        # Control-quality metrics surfaced via the system_pd_control_quality sensor
        # so the user can see the effect of the PD profile/sliders. Time-constant
        # EMAs (alpha = dt/(tau+dt)) keep the averaging window constant under the
        # variable event-driven cadence, like the rest of the loop.
        self._pd_quality_tau = 60.0      # seconds; metric averaging window
        self._pd_quality_rms_ema = None  # EMA of error^2 (W^2); sqrt -> RMS error
        self._pd_quality_osc_ema = 0.0   # EMA of error-sign changes per minute
        self._pd_quality_last_ts = None  # monotonic ts of last metric update
        # Separate from _pd_quality_last_ts, which is also bumped on skipped cycles
        # to keep the EMA step small when it resumes. Only this one marks a real
        # advance, so it is what tells the sensor its verdict has gone stale.
        self._pd_quality_last_advance_ts = None
        # Ignore the tracking transient after any setpoint/target step (capacity
        # protection, user target change, ...) so it doesn't
        # inflate RMS/oscillation. Source-agnostic: keys on active_target moving.
        self._pd_quality_step_grace_s = 10.0  # skip the metric this long after a step
        self._pd_quality_settle_until = 0.0   # monotonic deadline; skip while now < this
        self._pd_quality_prev_target = None   # previous active_target for step detection
        # Start of the warm-up after a restart (see PD_QUALITY_RESTART_GAP_S):
        # the end of the settle window until the first reading after it arrives,
        # then that reading's time. None when the metric is not warming up.
        self._pd_quality_warmup_start = None
        # True when the PD has no headroom to reduce the error (battery full while it
        # would charge, empty while it would discharge, or output pinned at the power
        # rail). Surfaced as the "battery_limited" quality state; not a tuning fault.
        self._pd_limited = False
        # True when the direction the grid error demands is not allowed to run
        # (time slot, EV/solar-surplus block, capacity protection). The residual
        # error is then a muzzled loop, not a tuning fault: the metric skips it
        # and the sensor reports "blocked".
        self._pd_blocked = False
        # Both flags are written only in the PD tail, which many cycles never
        # reach (weekly full charge owning the cycle, max
        # SOC handling, manual mode, ...). Clearing them at the top of the cycle
        # therefore erased a verdict that was still true, while never clearing
        # them latched a stale one for the whole session. Each is instead stamped
        # when set and expires on its own; see pd_blocked / pd_limited.
        self._pd_flag_ttl_s = 60.0
        self._pd_limited_ts = None
        self._pd_blocked_ts = None

        # Measured-power anti-windup (back-calculation): re-anchor the incremental
        # base to the battery's real AC output when commanded power is not being
        # delivered (saturation/ramp lag not captured by the capacity clamp).
        self.saturation_backcalc_threshold = 150.0  # W shortfall to count as saturation
        self.saturation_backcalc_cycles = 3          # sustained cycles before re-anchoring
        self._saturation_cycles = 0
        # Re-anchoring is gated on a REAL limit being active (SOC/taper/blocker/cap):
        # a slow actuator takes seconds to ramp, and that
        # ramp lag must NOT be mistaken for saturation or the base is yanked down to
        # the lagging measurement every few cycles and the command never reaches the
        # cap. The long fallback below still re-anchors on a sustained shortfall with
        # no known cause (e.g. unmodelled thermal derate), so windup stays bounded.
        self.saturation_backcalc_fallback_s = 15.0   # re-anchor after sustained unexplained shortfall
        self._saturation_shortfall_since = None

        # Oscillation detection for auto-reset
        self.sign_changes = 0           # Count of consecutive sign changes in error
        self.last_error_sign = 0        # Track sign of previous error (1, -1, or 0)
        self.oscillation_threshold = 3  # Reset PID after 3 sign changes

        # Last output sign for directional hysteresis
        self.last_output_sign = 0        # Track last output direction (1=charge, -1=discharge, 0=idle)

        # Stale sensor detection
        self._last_sensor_report_time = None    # datetime of last real sensor publication (HA last_reported)
        self._last_valid_meter_publication = None  # same clock, but read every cycle for health
        self._meter_wait_since = None           # utc time liveness began waiting for a first valid reading
        self._last_sensor_cadence_time = None   # latest publication consumed by the cadence detector
        self._last_control_sample_value = None  # last transformed value consumed by P/D
        self._control_sample_is_new = True      # result of the current control-loop sample
        self._stale_cycles = 0                  # consecutive cycles without a sensor publication
        self._max_sensor_stale_s = MAX_SENSOR_STALE_S
        self._consumption_sensor_issue = None   # invalid/missing state already logged this episode
        self._control_lock = asyncio.Lock()     # serialize control cycle across timer + sensor-event triggers
        self._slow_sensor_issue_created = False  # slow-sensor repair currently raised
        self._dead_sensor_issue_created = None   # dead-sensor repair raised (None: not checked yet this run)
        self._main_sensor_dead = False          # meter silent past MAIN_SENSOR_DEAD_S
        self._main_sensor_never_reported = False  # dead without a single valid reading in this run
        self._dead_meter_fallback_engaged = False  # automatic batteries stopped for it
        self._slow_sensor_intervals = 0         # consecutive slow sensor intervals
        self._fast_sensor_intervals = 0         # consecutive fast intervals used to clear the repair

        # Normal high-SOC charge protection. These must exist before the first
        # capacity calculation because _battery_power_limit() reads them.
        self._normal_balance_date = dt_util.now().date()
        self._normal_balance_voltage_tapered: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_bms_cutoff_active: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_bms_cutoff_retry_pending: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_bms_cutoff_retry_active: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_bms_cutoff_retry_accept_count: dict[MarstekVenusDataUpdateCoordinator, int] = {}
        self._normal_balance_bms_cutoff_measurement: dict[
            MarstekVenusDataUpdateCoordinator, str
        ] = {}
        self._normal_balance_phases: dict[MarstekVenusDataUpdateCoordinator, str] = {}
        self._normal_balance_measure_started: dict[MarstekVenusDataUpdateCoordinator, datetime] = {}
        self._normal_balance_last_delta_v: dict[MarstekVenusDataUpdateCoordinator, float] = {}
        # SOC recalibration override: keep charging past the taper when the
        # BMS reports a low SOC at the top voltage, until the BMS itself cuts off.
        self._normal_balance_recal_override: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_recal_cutoff_count: dict[MarstekVenusDataUpdateCoordinator, int] = {}
        self._normal_balance_recal_latched: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_recal_retry_pending: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_recal_retry_active: dict[MarstekVenusDataUpdateCoordinator, bool] = {}
        self._normal_balance_recal_first_cutoff_voltage: dict[MarstekVenusDataUpdateCoordinator, float] = {}
        self._max_soc_mgr = MaxSocChargeManager(hass, self)
        self._temp_limit_mgr = TemperatureChargeLimitManager(hass, self)
        # Temperature-based charge derate: settings must load before the first
        # capacity calculation because _battery_power_limit() reads them.
        self.temp_charge_limit_enabled = config_entry.data.get(CONF_ENABLE_TEMP_CHARGE_LIMIT, DEFAULT_ENABLE_TEMP_CHARGE_LIMIT)
        self._temp_charge_limit_c = config_entry.data.get(CONF_TEMP_CHARGE_LIMIT_C, DEFAULT_TEMP_CHARGE_LIMIT_C)
        self._temp_charge_limit_band_c = config_entry.data.get(CONF_TEMP_CHARGE_LIMIT_BAND_C, DEFAULT_TEMP_CHARGE_LIMIT_BAND_C)
        self._temp_charge_limit_floor_pct = config_entry.data.get(CONF_TEMP_CHARGE_LIMIT_FLOOR_PCT, DEFAULT_TEMP_CHARGE_LIMIT_FLOOR_PCT)
        self.temp_limit_apply_discharge = config_entry.data.get(CONF_TEMP_LIMIT_APPLY_DISCHARGE, DEFAULT_TEMP_LIMIT_APPLY_DISCHARGE)

        # Calculate dynamic anti-windup limits based on total system capacity
        self.max_charge_capacity = self._effective_system_capacity(coordinators, is_charging=True)
        self.max_discharge_capacity = self._effective_system_capacity(coordinators, is_charging=False)

        # Load sharing state: track which batteries were active last cycle.
        # Active-battery lists stay here (sensor.py/switch.py and the control loop
        # read/mutate them); the wall-clock split holds live in PowerDistribution.
        self._active_discharge_batteries = []
        self._active_charge_batteries = []

        # Non-responsive battery tracking: excludes batteries that ACK commands but don't deliver power
        self._non_responsive = NonResponsiveTracker()
        # Alias to the tracker's internal dict for backward-compat with sensor.py diagnostics
        self._non_responsive_batteries = self._non_responsive.batteries
        # Direction engage grace: sign of the last commanded net power per battery
        # (+1 charge / -1 discharge / 0 idle) and the time a move started.
        # Non-delivery is suppressed for the controller default (or the driver's
        # declared engage grace) after either direction flip so a slow inverter is
        # not excluded while it is engaging. See _set_battery_power.
        self._last_commanded_net_sign: dict[MarstekVenusDataUpdateCoordinator, int] = {}
        self._charge_engage_started: dict[MarstekVenusDataUpdateCoordinator, datetime] = {}
        self._discharge_engage_started: dict[MarstekVenusDataUpdateCoordinator, datetime] = {}
        # Top-of-charge taper grace: the time a battery was first seen charging
        # at SOC >= HIGH_SOC_CHARGE_TAPER_FLOOR without clearing the 10%-of-
        # commanded delivery bar. See _check_non_delivery.
        self._high_soc_taper_started: dict[MarstekVenusDataUpdateCoordinator, datetime] = {}
        # Idle ramp-down grace: the time the commanded direction flipped from a
        # move into idle. The idle-runaway judgment is suppressed for
        # IDLE_RUNAWAY_GRACE_S after the flip so a battery still ramping down
        # (lagging battery_power telemetry) is not mistaken for a runaway.
        self._idle_commanded_started: dict[MarstekVenusDataUpdateCoordinator, datetime] = {}
        # Idle-runaway episode guard: True while a battery commanded idle is still
        # running free (issue #434), so the wake/re-assert fires once per episode
        # instead of every control cycle (which floods the log). Cleared when the
        # battery returns to idle or is commanded to move. See _set_battery_power.
        self._idle_runaway_handled: dict[MarstekVenusDataUpdateCoordinator, bool] = {}

        # Coordinators currently owned by a manual time-slot this cycle.
        # PD logic must not touch these — _set_battery_power short-circuits.
        self._manual_slot_owned: set = set()

        # Backup function cooldown: prevents re-entering PD control immediately after offgrid load drops.
        # Format: coordinator -> datetime (UTC) until which the battery stays excluded
        self._backup_cooldown_until: dict = {}

        # EV charger no-telemetry state tracking
        self._ev_charging_states: dict[str, bool] = {}  # sensor_id -> is EV currently charging
        self._ev_pause_until: dict[str, Optional[datetime]] = {}  # sensor_id -> pause end time (UTC)
        
        self.solar_production_sensor = config_entry.data.get(CONF_SOLAR_PRODUCTION_SENSOR, None)
        self.max_contracted_power = config_entry.data.get(CONF_MAX_CONTRACTED_POWER, 7000)

        # Derived Home Consumption sensor (our own aggregate). Resolved lazily by
        # stable unique_id once the entity exists, and used by ExternalLoads for
        # PV-surplus accounting (#421/#415). Survives a user "recreate entity IDs"
        # rename because the unique_id never changes.
        self.home_consumption_sensor: Optional[str] = None

        # Solar surplus excluded device flag (set each cycle by calculate_adjustment, consumed by PD section)
        self._solar_surplus_discharge_blocked: bool = False
        self._global_charge_blockers: dict[str, dict] = {}
        self._global_discharge_blockers: dict[str, dict] = {}
        self._battery_charge_blockers: dict[MarstekVenusDataUpdateCoordinator, dict[str, dict]] = {}
        self._battery_discharge_blockers: dict[MarstekVenusDataUpdateCoordinator, dict[str, dict]] = {}
        self._missing_sensors_since = None     # monotonic ts a configured sensor entity went missing
        self._missing_sensors_reported = None  # entity ids named by the current Repairs issue
        # The issue is persistent, so one raised before a restart is restored as
        # active while _missing_sensors_reported starts empty. Without this flag
        # the first healthy check had nothing to clear it against.
        self._missing_sensors_issue_cleared = False

        # Manual mode state
        self.manual_mode_enabled = config_entry.data.get(CONF_MANUAL_MODE_ENABLED, False)

        # Setpoint offset registry (reference = 0 W grid flow)
        # - Additive offsets: summed to form the base target
        # - Absolute overrides: highest priority wins, replaces additive sum
        self._setpoint_offsets: dict[str, float] = {
            "user_target": self.target_grid_power,  # user's preference from config
        }
        self._setpoint_overrides: dict[str, tuple[int, float]] = {}  # source → (priority, value_w)

        self._rate_limiter_was_active = False
        self._rate_limiter_last_direction = 0
        self._rate_limiter_last_logged_change: float | None = None

        # Capacity Protection Mode state
        self.capacity_protection_enabled = config_entry.data.get(CONF_CAPACITY_PROTECTION_ENABLED, False)
        self.capacity_protection_excluded_devices = config_entry.data.get(
            CONF_CAPACITY_PROTECTION_EXCLUDED_DEVICES, False
        )
        self.capacity_protection_soc_threshold = config_entry.data.get(CONF_CAPACITY_PROTECTION_SOC_THRESHOLD, DEFAULT_CAPACITY_PROTECTION_SOC)
        # FORK: release hysteresis on top of the SOC threshold (L, 23.11.2026)
        self.capacity_protection_soc_hysteresis = _fork_const.capacity_protection_soc_hysteresis(config_entry.data)
        self._capacity_protection_soc_latched = False
        self.capacity_protection_limit = config_entry.data.get(CONF_CAPACITY_PROTECTION_LIMIT, DEFAULT_CAPACITY_PROTECTION_LIMIT)
        self._capacity_protection_active = False  # True while either peak-shaving mode intervenes
        self._excluded_included_adjustment = 0.0  # Tracks excluded device adjustment for included_in_consumption devices
        self._icp_excluded_protection_w = 0.0
        self._capacity_protection_status = {
            "active": False,
            "avg_soc": None,
            "soc_threshold": self.capacity_protection_soc_threshold,
            "peak_limit": self.capacity_protection_limit,
            "estimated_house_load": None,
            "action": "idle",  # idle, shaving, shaving_excluded, conserving, charging
            "original_target": None,
            "adjusted_target": None,
        }
        self._capacity_protection_force_idle = False
        # FORK: below the SOC threshold and outside shaving, capacity protection
        # allows no discharge (set per cycle in _apply_capacity_protection).
        self._capacity_protection_block_discharge = False

        # Weekly Full Charge state
        self.weekly_full_charge_enabled = config_entry.data.get(CONF_ENABLE_WEEKLY_FULL_CHARGE, False)
        self.weekly_full_charge_day = config_entry.data.get(CONF_WEEKLY_FULL_CHARGE_DAY, "sun")
        self.weekly_full_charge_complete = False  # True when the weekly charge to 100% has completed
        self.last_checked_weekday = None  # Track day transitions for reset logic
        self.weekly_full_charge_registers_written = False  # True when register 44000 set to 100%
        self._weekly_charge_needs_restore = False  # True when day changed mid-charge and hardware restore is pending
        self._weekly_charge_saved_max_soc: dict[str, int] = {}  # coordinator.name → original max_soc before writing 100%

        self._force_full_charge = False         # Manual trigger via button, resets on day change
        self._balance_monitor = None  # Set from async_setup_entry after monitor is created

        # Minimal status dict for WeeklyFullChargeSensor (charge state only, not delay)
        self._weekly_charge_status = {
            "state": "Disabled" if not self.weekly_full_charge_enabled else "Idle",
        }

        # Weekly full charge management (owns its own Store internally)
        self._weekly_charge_mgr = WeeklyFullChargeManager(hass, config_entry, self)
        # Backward-compat alias to the manager's underlying Store
        self._store = self._weekly_charge_mgr.store

        # Apply no-PD direct-tracking overrides last, so they win over the PD params
        # loaded above (and the grid filter tau just set).
        self._apply_no_pd_overrides()

        _LOGGER.info("PD Controller initialized (user-configurable): Kp=%.2f, Ki=%.2f, Kd=%.2f, "
                     "Deadband=±%dW, Filter τ=%.1fs, Hysteresis=%dW, MaxChange=%dW/cycle, Limits: ±%dW",
                     self.kp, self.ki, self.kd,
                     self.deadband, self._grid_filter_tau, self.direction_hysteresis,
                     self.max_power_change_per_cycle, self.max_discharge_capacity)

        _LOGGER.info("Weekly Full Charge: %s (day: %s)",
                     "ENABLED" if self.weekly_full_charge_enabled else "DISABLED",
                     self.weekly_full_charge_day.upper() if self.weekly_full_charge_enabled else "N/A")

        _LOGGER.info("Capacity Protection: %s (SOC threshold: %d%%, peak limit: %dW)",
                     "ENABLED" if self.capacity_protection_enabled else "DISABLED",
                     self.capacity_protection_soc_threshold,
                     self.capacity_protection_limit)

    @property
    def consumption_sensor(self) -> str:
        """Return the meter currently feeding control and derived statistics."""
        if self.offgrid_mode_enabled and self.offgrid_power_sensor:
            return self.offgrid_power_sensor
        return self.primary_consumption_sensor

    @property
    def consumption_sensor_ids(self) -> list[str]:
        """Return every meter that may become active without an entry reload."""
        return list(
            dict.fromkeys(
                sensor
                for sensor in (
                    self.primary_consumption_sensor,
                    self.offgrid_power_sensor,
                )
                if sensor
            )
        )

    def _reset_consumption_source_tracking(self) -> None:
        """Start a clean sample series after selecting a different meter."""
        self._grid_filter_ema = None
        self._last_sensor_report_time = None
        self._last_valid_meter_publication = None
        self._meter_wait_since = None
        self._last_sensor_cadence_time = None
        self._last_control_sample_value = None
        self._control_sample_is_new = True
        self._stale_cycles = 0
        self._consumption_sensor_issue = None

    def set_offgrid_mode(self, enabled: bool) -> None:
        """Select the alternate meter without changing any battery setting."""
        enabled = bool(enabled and self.offgrid_power_sensor)
        if enabled == self.offgrid_mode_enabled:
            return
        self.offgrid_mode_enabled = enabled
        self._reset_consumption_source_tracking()

    def _configured_system_limit(self, is_charging: bool) -> int:
        """Return the optional system-wide power limit for the direction.

        0 means disabled, preserving the legacy behavior where only per-battery
        limits define total system capacity.
        """
        if not self.enable_system_power_limits:
            return 0

        raw_limit = (
            self.system_max_charge_power if is_charging
            else self.system_max_discharge_power
        )
        try:
            limit = int(raw_limit or 0)
        except (TypeError, ValueError):
            limit = 0
        return max(0, limit)

    def _effective_system_capacity(self, batteries: list, is_charging: bool) -> int:
        """Return available capacity after applying the optional global cap."""
        batteries = [
            coordinator for coordinator in batteries
            if not getattr(coordinator, CONF_BATTERY_MANUAL_MODE_ENABLED, False)
        ]
        total_capacity = sum(
            self._battery_power_limit(c, is_charging)
            for c in batteries
        )
        system_limit = self._configured_system_limit(is_charging)
        if system_limit > 0:
            return min(total_capacity, system_limit)
        return total_capacity

    @staticmethod
    def _is_battery_manual_owned(coordinator) -> bool:
        """Return whether an individual battery is outside automatic control."""
        return bool(
            getattr(coordinator, CONF_BATTERY_MANUAL_MODE_ENABLED, False)
        )

    def _get_automatic_batteries(self) -> list:
        """Return the batteries available to automatic planning and control."""
        return [
            coordinator for coordinator in self.coordinators
            if not ChargeDischargeController._is_battery_manual_owned(coordinator)
        ]

    def _reset_battery_ownership_state(
        self, coordinator, *, reset_controller_state: bool = True
    ) -> None:
        """Remove one battery from transient automatic-control ownership state.

        An ownership transition must not reset the shared PD command: doing so
        makes the next cycle stop every automatic battery before the controller
        computes its replacement allocation. The incremental dynamics are
        reset so the controller can settle after the pool changes.
        """
        for active in (
            getattr(self, "_active_charge_batteries", []),
            getattr(self, "_active_discharge_batteries", []),
        ):
            while coordinator in active:
                active.remove(coordinator)

        manual_slots = getattr(self, "_manual_slot_owned", None)
        if manual_slots is not None:
            manual_slots.discard(coordinator)

        power_distribution = getattr(self, "_power_distribution", None)
        if power_distribution is not None:
            for attr in ("_charge_selection_hold_until", "_discharge_selection_hold_until"):
                holds = getattr(power_distribution, attr, None)
                if holds is not None:
                    holds.pop(coordinator, None)

        phase_limiter = getattr(self, "_phase_power_limiter", None)
        if phase_limiter is not None:
            for attr in ("_planned", "_limited_batteries"):
                state = getattr(phase_limiter, attr, None)
                if state is not None:
                    state.pop(coordinator, None)

        for attr in (
            "_last_commanded_net_sign",
            "_charge_engage_started",
            "_discharge_engage_started",
            "_high_soc_taper_started",
            "_idle_commanded_started",
            "_idle_runaway_handled",
        ):
            state = getattr(self, attr, None)
            if state is not None:
                state.pop(coordinator, None)

        bms_cutoff_state = getattr(self, "_normal_balance_bms_cutoff_active", None)
        if bms_cutoff_state is not None:
            bms_cutoff_state.pop(coordinator, None)
        for attr in (
            "_normal_balance_bms_cutoff_retry_pending",
            "_normal_balance_bms_cutoff_retry_active",
            "_normal_balance_bms_cutoff_retry_accept_count",
        ):
            retry_state = getattr(self, attr, None)
            if retry_state is not None:
                retry_state.pop(coordinator, None)
        bms_cutoff_measurement = getattr(
            self, "_normal_balance_bms_cutoff_measurement", None
        )
        if bms_cutoff_measurement is not None:
            bms_cutoff_measurement.pop(coordinator, None)

        weekly_manager = getattr(self, "_weekly_charge_mgr", None)
        cutoff_counts = getattr(weekly_manager, "_bms_cutoff_counts", None)
        if cutoff_counts is not None:
            cutoff_counts.pop(getattr(coordinator, "name", coordinator), None)
            # Accept older/test state keyed directly by coordinator identity.
            cutoff_counts.pop(coordinator, None)

        if not reset_controller_state:
            return

        # Reset the incremental dynamics after an ownership transition, but
        # retain the live aggregate command and filtered meter value. Zeroing
        # either makes unrelated automatic batteries stop for one cycle before
        # the controller computes their replacement allocation.
        for attr, value in (
            ("previous_error", 0.0),
            ("error_integral", 0.0),
            ("derivative_filtered", 0.0),
            ("last_error_sign", 0),
            ("last_output_sign", 0),
            ("sign_changes", 0),
            ("_zero_cross_since", None),
            ("_flip_hyst_suppressed", False),
            ("_relay_shutoff_since", None),
            ("_saturation_cycles", 0),
            ("_saturation_shortfall_since", None),
        ):
            if hasattr(self, attr):
                setattr(self, attr, value)

    async def _set_battery_manual_mode(self, coordinator, enabled: bool) -> None:
        """Enter or leave individual manual control with a verified idle handoff.

        With RS485 control off by the user nothing is written (the battery runs
        under its own app), so idle counts as already reached (upstream #895).
        """
        async with self._control_lock:
            if enabled:
                # Persist ownership before touching the network. A restart in
                # the middle of the handoff must keep the battery excluded from
                # automatic control.
                coordinator.battery_manual_mode_enabled = True
                coordinator.persist_battery_config(
                    CONF_BATTERY_MANUAL_MODE_ENABLED, True
                )
                self._reset_battery_ownership_state(
                    coordinator, reset_controller_state=False
                )
                coordinator.manual_force_mode = "None"
                coordinator.manual_set_charge_power = 0
                coordinator.manual_set_discharge_power = 0
                coordinator.persist_battery_config("manual_force_mode", "None")
                coordinator.persist_battery_config("manual_set_charge_power", 0)
                coordinator.persist_battery_config("manual_set_discharge_power", 0)

                idle_ok = getattr(
                    coordinator, "rs485_user_disabled", False
                ) or await self._set_battery_power(
                    coordinator,
                    0,
                    0,
                    bypass_blockers=True,
                    force_write=True,
                    owner="battery_manual",
                )
                if not idle_ok:
                    _LOGGER.error(
                        "[%s] Individual manual mode enabled but safe idle could not be verified",
                        coordinator.name,
                    )
                    raise HomeAssistantError(
                        f"Could not place {coordinator.name} in safe idle"
                    )
                await coordinator.async_request_refresh()
                return

            # Keep ownership asserted while the final zero-power command is
            # acknowledged, so an automatic cycle cannot race the handoff.
            coordinator.battery_manual_mode_enabled = True
            idle_ok = getattr(
                coordinator, "rs485_user_disabled", False
            ) or await self._set_battery_power(
                coordinator,
                0,
                0,
                bypass_blockers=True,
                force_write=True,
                owner="battery_manual",
            )
            if not idle_ok:
                _LOGGER.error(
                    "[%s] Individual manual mode remains enabled: safe idle failed",
                    coordinator.name,
                )
                raise HomeAssistantError(
                    f"Could not leave {coordinator.name} safely idle"
                )

            coordinator.manual_force_mode = "None"
            coordinator.manual_set_charge_power = 0
            coordinator.manual_set_discharge_power = 0
            coordinator.persist_battery_config("manual_force_mode", "None")
            coordinator.persist_battery_config("manual_set_charge_power", 0)
            coordinator.persist_battery_config("manual_set_discharge_power", 0)
            await coordinator.async_request_refresh()
            coordinator.battery_manual_mode_enabled = False
            coordinator.persist_battery_config(
                CONF_BATTERY_MANUAL_MODE_ENABLED, False
            )
            self._reset_battery_ownership_state(coordinator)
            self.schedule_control_cycle()

    def _refresh_effective_system_capacities(self) -> None:
        """Refresh cached capacities used by PD anti-windup diagnostics."""
        self.max_charge_capacity = self._effective_system_capacity(
            self.coordinators,
            is_charging=True,
        )
        self.max_discharge_capacity = self._effective_system_capacity(
            self.coordinators,
            is_charging=False,
        )

    def _clamp_to_system_capacity(self, power: float, batteries: list, is_charging: bool) -> float:
        """Clamp a positive direction-specific power request to available capacity."""
        return min(power, self._effective_system_capacity(batteries, is_charging))

    def _normal_balance_reset_if_new_day(self) -> None:
        """Delegate daily reset of top-of-charge state (weekly_full_charge calls this)."""
        self._max_soc_mgr.reset_if_new_day()

    def _refresh_normal_balance_blocks(self) -> None:
        """Delegate top-of-charge protection blockers to MaxSocChargeManager."""
        self._max_soc_mgr.refresh_blocks()

    def get_max_soc_charge_status(self) -> dict:
        """Return top-of-charge diagnostics for the integration status sensor."""
        return self._max_soc_mgr.get_status()

    def _pd_house_demand_present(self) -> bool:
        """Return True when the PD input indicates household/grid demand."""
        consumption_state = self.hass.states.get(self.consumption_sensor)
        sensor_raw = self._apply_meter_transform(consumption_state)
        if sensor_raw is None:
            return False
        active_target = self.compute_active_target()
        return sensor_raw > active_target + self.deadband

    def _slot_manual_direction_for(self, slot: dict | None, coordinator) -> tuple[str, int] | None:
        """Return (direction, power_w) when `slot` is a valid manual single-direction
        slot for `coordinator`, or None.
        """
        if not slot or slot.get("mode") != "manual":
            return None
        if not slot.get("power_override_enabled"):
            return None
        allow_c = bool(slot.get("allow_charge"))
        allow_d = bool(slot.get("allow_discharge"))
        if allow_c and allow_d:
            return None  # ambiguous: degrade to PD
        limits = self._slot_battery_limits(slot, coordinator)
        if allow_d:
            val = limits.get("max_discharge_power_w")
            if val is None:
                return None
            return ("discharge", int(val))
        if allow_c:
            val = limits.get("max_charge_power_w")
            if val is None:
                return None
            return ("charge", int(val))
        return None

    async def _try_apply_manual_slot(self) -> None:
        """Drive batteries with an active manual time slot directly, bypassing PD.

        Manual slots take a battery off the PD control path for the
        cycle. Safety blockers (min/max SOC and EV pause) still
        apply — if a safety block is set, the manual write is skipped.
        """
        self._manual_slot_owned = set()
        for coord in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coord):
                continue
            if not coord.is_available:
                continue
            if self._is_backup_function_active(coord):
                continue
            if coord.rs485_user_disabled:
                continue

            slot = self._get_active_slot(coord, "any")
            manual = self._slot_manual_direction_for(slot, coord)
            if manual is None:
                if slot and slot.get("mode") == "manual" \
                   and bool(slot.get("allow_charge")) and bool(slot.get("allow_discharge")):
                    _LOGGER.warning(
                        "[%s] Manual slot has both charge and discharge allowed — falling back to PD",
                        coord.name,
                    )
                continue
            direction, power = manual

            charge_blockers = self.get_charge_blockers(coord)
            discharge_blockers = self.get_discharge_blockers(coord)
            # Time-slot blockers don't apply against the slot that owns the battery.
            charge_safety = {k: v for k, v in charge_blockers.items() if k != "time_slot_charge"}
            discharge_safety = {
                k: v for k, v in discharge_blockers.items() if k != "time_slot_discharge"
            }
            if direction == "charge" and charge_safety:
                _LOGGER.debug(
                    "[%s] Manual slot charge skipped — safety blockers: %s",
                    coord.name, ", ".join(charge_safety.keys()),
                )
                continue
            if direction == "discharge" and discharge_safety:
                _LOGGER.debug(
                    "[%s] Manual slot discharge skipped — safety blockers: %s",
                    coord.name, ", ".join(discharge_safety.keys()),
                )
                continue

            net = power if direction == "charge" else -power
            result = await coord.apply_power(net)

            # Failure = writes rejected (not ok) or the confirmation read never
            # followed (feedback_timeout, ok but flagged) — same set the old
            # atomic write reported as None.
            if not result.ok or result.failure_reason is not None:
                _LOGGER.warning("[%s] Manual slot write failed", coord.name)
                continue

            self._manual_slot_owned.add(coord)
            _LOGGER.debug(
                "[%s] Manual slot active: direction=%s power=%dW",
                coord.name, direction, power,
            )

    def _is_manual_slot_owned(self, coordinator) -> bool:
        return coordinator in self._manual_slot_owned

    def _apply_slot_power_ceiling(self, coordinator, is_charging: bool, current_limit: int) -> int:
        """Cap the per-battery power limit with the active slot's power override (PD mode only)."""
        slot = self._get_active_slot(coordinator, "charge" if is_charging else "discharge")
        if not slot or not slot.get("power_override_enabled"):
            return current_limit
        if slot.get("mode") == "manual":
            return current_limit
        limits = self._slot_battery_limits(slot, coordinator)
        key = "max_charge_power_w" if is_charging else "max_discharge_power_w"
        val = limits.get(key)
        if val is None:
            return current_limit
        try:
            return min(int(current_limit), int(val))
        except (TypeError, ValueError):
            return current_limit

    def _battery_power_limit(self, coordinator, is_charging: bool) -> int:
        """Return the effective per-battery power limit for the current cycle."""
        if not is_charging:
            limit = self._temp_limit_mgr.apply_discharge_limit(
                coordinator,
                getattr(
                    coordinator,
                    "effective_max_discharge_power",
                    coordinator.max_discharge_power,
                ),
            )
            return self._apply_slot_power_ceiling(coordinator, False, limit)

        limit = getattr(
            coordinator,
            "effective_max_charge_power",
            coordinator.max_charge_power,
        )
        if coordinator.data is None:
            return self._apply_slot_power_ceiling(coordinator, True, limit)
        limit = self._max_soc_mgr.apply_charge_taper(coordinator, limit)
        limit = self._temp_limit_mgr.apply_temperature_limit(coordinator, limit)
        return self._apply_slot_power_ceiling(coordinator, True, limit)

    def _apply_no_pd_overrides(self):
        """Read the grid sensor raw while no-PD direct-tracking mode is active.

        The control law itself is swapped in _run_control_cycle (raw deadbeat
        `new_power = previous - error`, one cycle, gain 1) — not via the PD gains.
        The only runtime parameter no-PD touches is the grid EMA smoothing time
        constant: drop it to 0 (raw, unsmoothed sensor) when on, restore the
        default when off. Deadband, min charge/discharge power, relay min-ON dwell
        and grid setpoint are reused unchanged.

        Idempotent: called after every parameter (re)load in __init__ and
        update_pd_parameters, so toggling the mode flips behaviour cleanly.
        """
        # The grid filter is a SHARED signal feeding one loop, so it is NOT widened
        # to the slowest actuator: doing so smooths the spike away for the fast
        # batteries too. Slow-actuator pacing belongs per-battery in distribution.
        self._grid_filter_tau = 0.0 if self.no_pd_mode_enabled else DEFAULT_GRID_FILTER_TAU

    # FORK: relay guard (docs/fork/02-architektur.md)
    @property
    def pd_relay_guard_enabled(self) -> bool:
        return self._pd_relay_guard_enabled

    @pd_relay_guard_enabled.setter
    def pd_relay_guard_enabled(self, value: bool) -> None:
        # The switch entity setattr()s this; reconfigure so it takes effect now.
        self._pd_relay_guard_enabled = bool(value)
        self._configure_relay_guard()

    def _configure_relay_guard(self) -> None:
        # FORK: relay guard (docs/fork/02-architektur.md)
        c = _fork_const
        # FORK: review 13 - a stored None must not break setup / hot reload.
        def d(key, default):
            value = self.config_entry.data.get(key)
            return default if value is None else value
        self._pd_relay_guard_enabled = bool(d(c.CONF_PD_RELAY_GUARD_ENABLED, c.DEFAULT_PD_RELAY_GUARD_ENABLED))
        self._relay_guard.configure(
            enabled=self._pd_relay_guard_enabled,
            min_off_s=d(c.CONF_PD_RELAY_MIN_OFF, c.DEFAULT_PD_RELAY_MIN_OFF),
            direction_lock_s=d(c.CONF_PD_DIRECTION_LOCK, c.DEFAULT_PD_DIRECTION_LOCK),
            engage_margin_w=d(c.CONF_PD_ENGAGE_MARGIN, c.DEFAULT_PD_ENGAGE_MARGIN),
            bypass_error_w=d(c.CONF_PD_GUARD_BYPASS_ERROR, c.DEFAULT_PD_GUARD_BYPASS_ERROR),
            budget_per_hour=d(c.CONF_PD_RELAY_SWITCH_BUDGET, c.DEFAULT_PD_RELAY_SWITCH_BUDGET),
        )

    def _configure_grid_view(self) -> None:
        # FORK: grid view (docs/fork/02-architektur.md)
        c, data = _fork_const, self.config_entry.data
        self._grid_view.configure(
            physical_entity=data.get(c.CONF_PHYSICAL_GRID_SENSOR) or None,
            physical_inverted=bool(data.get(c.CONF_PHYSICAL_GRID_INVERTED) or False),
            limit_entity=data.get(c.CONF_CAPACITY_PROTECTION_LIMIT_ENTITY) or None,
            limit_margin_pct=data.get(c.CONF_CAPACITY_PROTECTION_LIMIT_MARGIN) or c.DEFAULT_CAPACITY_PROTECTION_LIMIT_MARGIN,
        )
        # FORK: review 11 - start every (re)configuration without a stale
        # repair issue; the cycle re-creates it if the meter is still unusable.
        self._physical_grid_issue_created = False
        hass = getattr(self, "hass", None)
        if hass is not None:
            try:
                ir.async_delete_issue(
                    hass, DOMAIN, f"physical_grid_sensor_unusable_{self.config_entry.entry_id}"
                )
            except Exception:  # noqa: BLE001 - diagnostics must never block setup
                _LOGGER.debug("Could not clear physical grid sensor issue", exc_info=True)

    def _read_physical_grid_w(self) -> float | None:
        # FORK: grid view (docs/fork/02-architektur.md)
        view = getattr(self, "_grid_view", None)
        if view is None or (self.offgrid_mode_enabled and self.offgrid_power_sensor):
            return None
        now = dt_util.utcnow()
        # FORK: review 1 - while shaving runs on the real meter, a value that has
        # not changed for PHYSICAL_FROZEN_S is a frozen (cached) reading.
        if getattr(self, "_peak_physical_prev", False) and view.latch_frozen(now):
            _LOGGER.debug("Real grid meter value frozen during peak shaving - fallback")
        return view.read_physical(self.hass.states, now)

    def _track_peak_state(self, physical: float | None) -> bool:
        """Return True if this cycle's peak shaving regulates the real meter.

        FORK: review 2/7. Remembers the result for the next cycle and drops the
        peak exit hold timer whenever the peak action is no longer "conserving".
        """
        status = getattr(self, "_capacity_protection_status", None) or {}
        action = status.get("action")
        peak_physical = physical is not None and action == "shaving"
        self._peak_physical_prev = peak_physical
        if action != "conserving":
            self._peak_exit_since = None
        return peak_physical

    def _peak_exit_hold_active(self) -> bool:
        """Delay force-idle when leaving physical-meter shaving for "conserving".

        FORK: review 2/7. Armed only when the previous cycle shaved on the real
        meter (never without a second sensor); keeps the last command until the
        "conserving" result has held for PEAK_EXIT_HOLD_S without interruption.
        """
        now = time.monotonic()
        since = getattr(self, "_peak_exit_since", None)
        if since is None:
            if not getattr(self, "_peak_physical_prev", False):
                return False
            self._peak_exit_since = since = now
        if now - since < _fork_const.PEAK_EXIT_HOLD_S:
            return True
        self._peak_exit_since = None
        return False

    def _guard_peak_bypass(self) -> bool:
        """Relay guard bypass: peak discharge or ICP excluded-device protection.

        FORK: review 2/9. Not for "conserving"/"charging": those need no
        immediate engage, so the anti-chatter locks may apply.
        """
        status = getattr(self, "_capacity_protection_status", None) or {}
        return (
            bool(getattr(self, "_capacity_protection_active", False))
            and status.get("action") in ("shaving", "shaving_excluded")
        ) or (getattr(self, "_icp_excluded_protection_w", 0.0) or 0.0) > 0

    def read_statistics_grid_w(self) -> float | None:
        """Grid power (+import / -export) for statistics and display.

        FORK: grid view (docs/fork/02-architektur.md), B5. The real meter when
        configured and usable, otherwise the regulation meter. Uses
        ``peek_physical`` (no side effects): ``read_physical`` would consume the
        control loop's new-sample detection, and ``_last_physical_grid_w`` has no
        timestamp, so timer-driven callers could see a stale value.
        """
        view = getattr(self, "_grid_view", None)
        if view is not None and view.has_physical and not (
            self.offgrid_mode_enabled and self.offgrid_power_sensor
        ):
            value = view.peek_physical(self.hass.states, dt_util.utcnow())
            if value is not None:
                return value
        return self._apply_meter_transform(self.hass.states.get(self.consumption_sensor))

    def _check_physical_grid_sensor(self) -> None:
        """Repair issue while the configured real grid sensor is unusable > 5 min.

        FORK: grid view (docs/fork/02-architektur.md), C. Cleared on recovery.
        """
        view = getattr(self, "_grid_view", None)
        if view is None or getattr(self, "hass", None) is None:
            return
        issue_id = f"physical_grid_sensor_unusable_{self.config_entry.entry_id}"
        # FORK: review 11 - no issue in off-grid mode (the real meter is unused).
        offgrid = bool(getattr(self, "offgrid_mode_enabled", False) and getattr(self, "offgrid_power_sensor", None))
        since = view.status()["unusable_since"] if view.has_physical and not offgrid else None
        unusable = (
            since is not None
            and (dt_util.utcnow() - since).total_seconds() > _fork_const.PHYSICAL_GRID_UNUSABLE_ISSUE_DELAY_S
        )
        reported = getattr(self, "_physical_grid_issue_created", False)
        if unusable and not reported:
            self._physical_grid_issue_created = True
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                issue_domain=DOMAIN,
                severity=ir.IssueSeverity.WARNING,
                translation_key="physical_grid_sensor_unusable",
                translation_placeholders={"sensor": view.status()["physical_entity"] or ""},
            )
        elif not unusable and reported:
            self._physical_grid_issue_created = False
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _effective_peak_limit(self) -> float:
        # FORK: grid view (docs/fork/02-architektur.md)
        view, hass = getattr(self, "_grid_view", None), getattr(self, "hass", None)
        if view is None or hass is None:
            return self.capacity_protection_limit
        return view.peak_limit(hass.states, self.capacity_protection_limit, dt_util.utcnow())

    def _peak_review_pending(self, physical: float | None) -> bool:
        """True if a new real-meter sample needs peak shaving to re-run P/D.

        FORK: grid view (docs/fork/02-architektur.md), P5. Read-only: relies on
        the single ``read_physical`` of this cycle having been done already.
        """
        view = getattr(self, "_grid_view", None)
        if physical is None or view is None or not getattr(self, "capacity_protection_enabled", False):
            return False
        if not view.physical_is_new():
            return False
        if getattr(self, "_capacity_protection_active", False):
            return True
        reader = getattr(self, "_measured_battery_power", None)
        measured = reader() if callable(reader) else None
        battery_power = measured if measured is not None else self.previous_power
        return physical - battery_power > ChargeDischargeController._effective_peak_limit(self)

    def _track_physical_sample(self, physical: float | None) -> float | None:
        """Seconds since the previous new real-meter sample, or None.

        FORK: grid view (docs/fork/02-architektur.md). Only a changed value
        counts as a sample (same rule as ``_track_control_sample``).
        """
        view = getattr(self, "_grid_view", None)
        if physical is None or view is None or not view.physical_is_new():
            return None
        now_mono = time.monotonic()
        last = getattr(self, "_last_physical_sample_mono", None)
        self._last_physical_sample_mono = now_mono
        return None if last is None else now_mono - last

    def _icp_charge_import_cap(self, sensor_filtered: float, physical: float | None) -> float:
        # FORK: grid view (docs/fork/02-architektur.md)
        grid = sensor_filtered if physical is None else physical
        return self.max_contracted_power - grid + self.previous_power

    def get_relay_guard_status(self) -> dict:
        # FORK: relay guard (docs/fork/02-architektur.md)
        status = self._relay_guard.status(time.monotonic())
        if status["last_switch"] is not None:
            age = time.monotonic() - status["last_switch"]
            status["last_switch"] = dt_util.utcnow() - timedelta(seconds=age)
        return status

    def update_pd_parameters(self):
        """Re-read PD controller parameters from config_entry.data (hot-reload)."""
        old_consumption_sensor = self.consumption_sensor
        self.offgrid_power_sensor = self.config_entry.data.get(CONF_OFFGRID_POWER_SENSOR)
        self.offgrid_meter_inverted = self.config_entry.data.get(
            CONF_OFFGRID_METER_INVERTED, False
        )
        self.offgrid_mode_enabled = bool(
            self.offgrid_power_sensor
            and self.config_entry.data.get(CONF_OFFGRID_MODE_ENABLED, False)
        )
        if self.consumption_sensor != old_consumption_sensor:
            self._reset_consumption_source_tracking()
        self.meter_inverted = self.config_entry.data.get(CONF_METER_INVERTED, False)
        if self._phase_power_limiter is not None:
            self._phase_power_limiter.refresh_config()
            self._phase_power_limiter.update_manual_mode_warning(
                self.config_entry.entry_id,
                bool(self.config_entry.data.get(CONF_MANUAL_MODE_ENABLED, False)),
            )
        # Update weekly full charge settings; reset completion state if day changed
        new_weekly_day = self.config_entry.data.get(CONF_WEEKLY_FULL_CHARGE_DAY, "sun")
        new_weekly_enabled = self.config_entry.data.get(CONF_ENABLE_WEEKLY_FULL_CHARGE, False)
        day_changed = new_weekly_day != self.weekly_full_charge_day
        feature_disabled = self.weekly_full_charge_enabled and not new_weekly_enabled
        if day_changed or feature_disabled:
            _LOGGER.info("Weekly Full Charge: %s - resetting completion state",
                         f"day changed from {self.weekly_full_charge_day.upper()} to {new_weekly_day.upper()}"
                         if day_changed else "feature disabled")
            # If registers were written for a charge still in progress, schedule a hardware restore
            if self.weekly_full_charge_registers_written and not self.weekly_full_charge_complete:
                _LOGGER.info("Weekly Full Charge: Mid-charge abort detected - hardware restore pending")
                self._weekly_charge_needs_restore = True
            self.weekly_full_charge_complete = False
            self.weekly_full_charge_registers_written = False
        self.weekly_full_charge_enabled = new_weekly_enabled
        self.weekly_full_charge_day = new_weekly_day

        self.deadband = self.config_entry.data.get(CONF_PD_DEADBAND, DEFAULT_PD_DEADBAND)
        self.kp = self.config_entry.data.get(CONF_PD_KP, DEFAULT_PD_KP)
        self.kd = self.config_entry.data.get(CONF_PD_KD, DEFAULT_PD_KD)
        self.max_power_change_per_cycle = self.config_entry.data.get(CONF_PD_MAX_POWER_CHANGE, DEFAULT_PD_MAX_POWER_CHANGE)
        self.direction_hysteresis = self.config_entry.data.get(CONF_PD_DIRECTION_HYSTERESIS, DEFAULT_PD_DIRECTION_HYSTERESIS)
        self.min_charge_power = self.config_entry.data.get(CONF_PD_MIN_CHARGE_POWER, DEFAULT_PD_MIN_CHARGE_POWER)
        self.min_discharge_power = self.config_entry.data.get(CONF_PD_MIN_DISCHARGE_POWER, DEFAULT_PD_MIN_DISCHARGE_POWER)
        self._relay_cooldown_s = self.config_entry.data.get(CONF_PD_RELAY_COOLDOWN, DEFAULT_PD_RELAY_COOLDOWN)
        self._configure_relay_guard()  # FORK: relay guard (docs/fork/02-architektur.md)
        self._configure_grid_view()  # FORK: grid view (docs/fork/02-architektur.md)
        self._min_cycle_interval_s = self.config_entry.data.get(CONF_PD_MIN_CYCLE_INTERVAL, DEFAULT_PD_MIN_CYCLE_INTERVAL)
        self.target_grid_power = self.config_entry.data.get(CONF_TARGET_GRID_POWER, DEFAULT_TARGET_GRID_POWER)
        self.enable_system_power_limits = self.config_entry.data.get(
            CONF_ENABLE_SYSTEM_POWER_LIMITS,
            (
                (self.config_entry.data.get(CONF_SYSTEM_MAX_CHARGE_POWER, DEFAULT_SYSTEM_MAX_CHARGE_POWER) or 0) > 0
                or (self.config_entry.data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, DEFAULT_SYSTEM_MAX_DISCHARGE_POWER) or 0) > 0
            ),
        )
        self.system_max_charge_power = self.config_entry.data.get(CONF_SYSTEM_MAX_CHARGE_POWER, DEFAULT_SYSTEM_MAX_CHARGE_POWER)
        self.system_max_discharge_power = self.config_entry.data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, DEFAULT_SYSTEM_MAX_DISCHARGE_POWER)
        self._refresh_effective_system_capacities()
        self._setpoint_offsets["user_target"] = self.target_grid_power
        # No-PD direct-tracking: re-read flags and (re)apply/release the overrides.
        # Must run after the PD params above are reloaded so the override wins.
        self.no_pd_mode_enabled = self.config_entry.data.get(CONF_NO_PD_MODE_ENABLED, DEFAULT_NO_PD_MODE_ENABLED)
        self.charge_priority = self.config_entry.data.get(CONF_CHARGE_PRIORITY, DEFAULT_CHARGE_PRIORITY)
        self.primary_battery = self.config_entry.data.get(CONF_PRIMARY_BATTERY, DEFAULT_PRIMARY_BATTERY)
        self.primary_feedforward_enabled = self.config_entry.data.get(
            CONF_PRIMARY_FEEDFORWARD_ENABLED, DEFAULT_PRIMARY_FEEDFORWARD_ENABLED
        )
        self._no_pd_command_delay = self.config_entry.data.get(CONF_NO_PD_COMMAND_DELAY, DEFAULT_NO_PD_COMMAND_DELAY)
        self._apply_no_pd_overrides()
        self.max_contracted_power = self.config_entry.data.get(CONF_MAX_CONTRACTED_POWER, 7000)
        # Temperature-based charge derate
        self.temp_charge_limit_enabled = self.config_entry.data.get(CONF_ENABLE_TEMP_CHARGE_LIMIT, DEFAULT_ENABLE_TEMP_CHARGE_LIMIT)
        self._temp_charge_limit_c = self.config_entry.data.get(CONF_TEMP_CHARGE_LIMIT_C, DEFAULT_TEMP_CHARGE_LIMIT_C)
        self._temp_charge_limit_band_c = self.config_entry.data.get(CONF_TEMP_CHARGE_LIMIT_BAND_C, DEFAULT_TEMP_CHARGE_LIMIT_BAND_C)
        self._temp_charge_limit_floor_pct = self.config_entry.data.get(CONF_TEMP_CHARGE_LIMIT_FLOOR_PCT, DEFAULT_TEMP_CHARGE_LIMIT_FLOOR_PCT)
        self.temp_limit_apply_discharge = self.config_entry.data.get(CONF_TEMP_LIMIT_APPLY_DISCHARGE, DEFAULT_TEMP_LIMIT_APPLY_DISCHARGE)
        self.solar_production_sensor = self.config_entry.data.get(CONF_SOLAR_PRODUCTION_SENSOR, None)
        self.capacity_protection_enabled = self.config_entry.data.get(CONF_CAPACITY_PROTECTION_ENABLED, False)
        self.capacity_protection_excluded_devices = self.config_entry.data.get(
            CONF_CAPACITY_PROTECTION_EXCLUDED_DEVICES, False
        )
        self.capacity_protection_soc_threshold = self.config_entry.data.get(CONF_CAPACITY_PROTECTION_SOC_THRESHOLD, DEFAULT_CAPACITY_PROTECTION_SOC)
        self.capacity_protection_soc_hysteresis = _fork_const.capacity_protection_soc_hysteresis(self.config_entry.data)  # FORK
        self.capacity_protection_limit = self.config_entry.data.get(CONF_CAPACITY_PROTECTION_LIMIT, DEFAULT_CAPACITY_PROTECTION_LIMIT)

        _LOGGER.info(
            "PD parameters hot-reloaded: Kp=%.2f, Kd=%.2f, deadband=%d, max_change=%d, "
            "hysteresis=%d, min_charge=%d, min_discharge=%d, system_limits=%s, system_max_charge=%d, "
            "system_max_discharge=%d",
            self.kp, self.kd, self.deadband, self.max_power_change_per_cycle,
            self.direction_hysteresis, self.min_charge_power, self.min_discharge_power,
            self.enable_system_power_limits,
            self.system_max_charge_power, self.system_max_discharge_power,
        )

    def _update_pd_quality_metrics(self, error: float, sign_changed: bool, active_target: float, pd_limited: bool) -> None:
        """Update control-quality EMAs (grid-error RMS and oscillation rate).

        Called once per active PD cycle (skipped when the controller is paused by
        restrictions). Uses real monotonic elapsed time so the averaging window is
        constant under the variable event-driven cadence.

        A setpoint/target step (capacity protection, a user target
        change, ...) makes the error spike while the battery ramps to the new target;
        that transient is skipped through a short grace window so it doesn't inflate
        the metric. Detection is source-agnostic: it keys on active_target moving.

        After a long gap (the PD was not in control: a grid-charge slot, the weekly
        full charge, manual mode, ...) the stored numbers describe the situation
        before the gap, and the batteries are still running the previous command
        when the PD takes over. The metric then starts over: it skips the hand-over
        through the same settle window, and weighs the readings after it equally
        until they cover one metric window. Until then it reports no RMS, so the
        sensor shows "collecting_data" instead of a verdict on a few seconds.

        While the PD is battery-limited (no headroom to reduce the error) the residual
        error is not a tuning fault, so the metric is skipped too — the sensor reports
        the "battery_limited" state instead.
        """
        now = time.monotonic()
        if (
            self._pd_quality_prev_target is not None
            and abs(active_target - self._pd_quality_prev_target) > max(self.deadband, 20.0)
        ):
            self._pd_quality_settle_until = now + self._pd_quality_step_grace_s
        self._pd_quality_prev_target = active_target
        if (
            self._pd_quality_last_ts is not None
            and now - self._pd_quality_last_ts > PD_QUALITY_RESTART_GAP_S
        ):
            self._pd_quality_settle_until = max(
                self._pd_quality_settle_until, now + PD_QUALITY_RESTART_SETTLE_S
            )
            self._pd_quality_rms_ema = None
            self._pd_quality_osc_ema = 0.0
            self._pd_quality_warmup_start = self._pd_quality_settle_until

        if pd_limited or now < self._pd_quality_settle_until:
            # Keep the timestamp fresh so the EMA resumes smoothly (small dt) instead
            # of seeing one huge gap that would snap it to the post-step value.
            self._pd_quality_last_ts = now
            return

        if self._pd_quality_last_ts is None:
            self._pd_quality_last_ts = now
            self._pd_quality_last_advance_ts = now
            self._pd_quality_rms_ema = error * error
            return
        dt = now - self._pd_quality_last_ts
        self._pd_quality_last_ts = now
        if dt <= 0:
            return
        self._pd_quality_last_advance_ts = now
        tau = self._pd_quality_tau
        if self._pd_quality_warmup_start is not None and self._pd_quality_rms_ema is not None:
            # Restart warm-up: the window grows with the data, so every reading
            # since the restart weighs the same until a full window is covered.
            warm_s = now - self._pd_quality_warmup_start
            if warm_s >= tau:
                self._pd_quality_warmup_start = None
            else:
                tau = warm_s
        alpha = dt / (tau + dt)
        sq = error * error
        if self._pd_quality_rms_ema is None:
            self._pd_quality_rms_ema = sq
            if self._pd_quality_warmup_start is not None:
                self._pd_quality_warmup_start = now
        else:
            self._pd_quality_rms_ema += alpha * (sq - self._pd_quality_rms_ema)
        # Oscillation rate in events/min: the instantaneous rate for this gap is
        # (60/dt) when a sign change occurred this cycle, 0 otherwise; smoothed.
        inst_per_min = (60.0 / dt) if sign_changed else 0.0
        self._pd_quality_osc_ema += alpha * (inst_per_min - self._pd_quality_osc_ema)

    def _set_pd_limited(self, value: bool) -> None:
        """Set the battery-limited flag and stamp it for the TTL."""
        self._pd_limited = value
        self._pd_limited_ts = time.monotonic() if value else None

    def _set_pd_blocked(self, value: bool) -> None:
        """Set the demand-blocked flag and stamp it for the TTL."""
        self._pd_blocked = value
        self._pd_blocked_ts = time.monotonic() if value else None

    def _pd_flag_live(self, value: bool, stamped_at: float | None) -> bool:
        """Return True while a set flag is still within its TTL."""
        if not value or stamped_at is None:
            return False
        return (time.monotonic() - stamped_at) <= self._pd_flag_ttl_s

    @property
    def pd_limited(self) -> bool:
        """Battery-limited, as long as a cycle confirmed it recently."""
        return self._pd_flag_live(self._pd_limited, self._pd_limited_ts)

    @property
    def pd_blocked(self) -> bool:
        """Demand-blocked, as long as a cycle confirmed it recently."""
        return self._pd_flag_live(self._pd_blocked, self._pd_blocked_ts)

    @property
    def pd_quality_rms_error(self) -> float | None:
        """RMS of the grid-control error over the metric window (W), or None.

        None also while the metric restarts after a long gap and its fresh
        readings don't cover one metric window yet.
        """
        if self._pd_quality_rms_ema is None:
            return None
        if (
            self._pd_quality_warmup_start is not None
            and time.monotonic() - self._pd_quality_warmup_start < self._pd_quality_tau
        ):
            return None
        return math.sqrt(max(0.0, self._pd_quality_rms_ema))

    @property
    def pd_quality_age_s(self) -> float | None:
        """Seconds since the quality metric last advanced, or None if never.

        The metric only advances on cycles where the loop is genuinely in
        control; a long age means the EMAs describe a situation that is hours
        old and must not be presented as a live verdict. Skipped cycles bump
        _pd_quality_last_ts (the EMA anchor) but not the advance timestamp, so
        this measures the age of the numbers rather than of the last call.
        """
        if self._pd_quality_last_advance_ts is None:
            return None
        return max(0.0, time.monotonic() - self._pd_quality_last_advance_ts)

    @property
    def pd_quality_oscillation_per_min(self) -> float:
        """Smoothed error-sign-change rate (events/min); a hunting indicator."""
        return self._pd_quality_osc_ema

    def _make_block_record(self, registry: dict, source: str, reason: str, details: dict | None) -> dict:
        """Build a blocker record, preserving the original activation time."""
        existing = registry.get(source)
        return {
            "reason": reason,
            "details": details or {},
            "since": existing.get("since") if existing else dt_util.utcnow(),
        }

    def _serialize_blockers(self, registry: dict[str, dict]) -> dict:
        """Return blockers with JSON/state-attribute friendly values."""
        return {
            source: {
                "reason": record.get("reason"),
                "details": dict(record.get("details") or {}),
                "since": record["since"].isoformat() if record.get("since") else None,
            }
            for source, record in registry.items()
        }

    @staticmethod
    def _format_blockers_for_log(blockers: dict) -> str:
        """Return a compact one-line blocker summary for logs."""
        if not blockers:
            return "none"

        parts = []
        for source, record in blockers.items():
            reason = record.get("reason") or source
            details = record.get("details") or {}
            detail_text = ",".join(
                f"{key}={value}"
                for key, value in details.items()
                if value is not None
            )
            if detail_text:
                parts.append(f"{source}:{reason}({detail_text})")
            else:
                parts.append(f"{source}:{reason}")
        return ";".join(parts)

    def _format_setpoint_summary_for_log(self) -> str:
        """Return current target contributors in a compact form."""
        offsets = ",".join(
            f"{source}={value:.1f}W"
            for source, value in self._setpoint_offsets.items()
        ) or "none"
        overrides = {
            source: {"priority": priority, "value": round(value, 1)}
            for source, (priority, value) in self._setpoint_overrides.items()
        }
        if self._setpoint_overrides:
            active_source, (_, active_value) = max(
                self._setpoint_overrides.items(),
                key=lambda item: item[1][0],
            )
            override = f"{active_source}={active_value:.1f}W"
        else:
            override = "none"
        return f"offsets={offsets} active_override={override} overrides={overrides or 'none'}"

    def _should_log_rate_limiter(self, requested_change_w: float) -> bool:
        """Return True when rate limiting newly matters enough to log."""
        direction = 1 if requested_change_w > 0 else -1
        previous_change = self._rate_limiter_last_logged_change
        change_threshold = max(250.0, self.max_power_change_per_cycle * 0.25)

        should_log = (
            not self._rate_limiter_was_active
            or direction != self._rate_limiter_last_direction
            or previous_change is None
            or abs(requested_change_w - previous_change) >= change_threshold
        )

        self._rate_limiter_was_active = True
        self._rate_limiter_last_direction = direction
        if should_log:
            self._rate_limiter_last_logged_change = requested_change_w
        return should_log

    def _clear_rate_limiter_state(self) -> None:
        """Mark the rate limiter as inactive so the next clamp is logged once."""
        self._rate_limiter_was_active = False
        self._rate_limiter_last_direction = 0
        self._rate_limiter_last_logged_change = None

    def _block_registry(self, is_charging: bool, coordinator=None) -> dict:
        """Return the mutable blocker registry for a direction and scope."""
        if coordinator is None:
            return self._global_charge_blockers if is_charging else self._global_discharge_blockers
        registries = self._battery_charge_blockers if is_charging else self._battery_discharge_blockers
        return registries.setdefault(coordinator, {})

    def _set_operation_block(self, is_charging: bool, source: str, reason: str, details: dict | None = None, coordinator=None) -> None:
        registry = self._block_registry(is_charging, coordinator)
        old = registry.get(source)
        registry[source] = self._make_block_record(registry, source, reason, details)
        if old is None:
            scope = "global" if coordinator is None else coordinator.name
            _LOGGER.debug(
                "%s block added [%s]: %s",
                "Charge" if is_charging else "Discharge",
                scope,
                self._format_blockers_for_log({source: registry[source]}),
            )

    def _remove_operation_block(self, is_charging: bool, source: str, coordinator=None) -> None:
        if coordinator is None:
            registry = self._global_charge_blockers if is_charging else self._global_discharge_blockers
        else:
            registries = self._battery_charge_blockers if is_charging else self._battery_discharge_blockers
            registry = registries.get(coordinator)
            if registry is None:
                return
        removed = registry.pop(source, None)
        if removed is not None:
            scope = "global" if coordinator is None else coordinator.name
            _LOGGER.debug(
                "%s block removed [%s]: %s",
                "Charge" if is_charging else "Discharge",
                scope,
                self._format_blockers_for_log({source: removed}),
            )
        if coordinator is not None and not registry:
            registries.pop(coordinator, None)

    def set_charge_block(self, source: str, reason: str, details: dict | None = None, coordinator=None) -> None:
        """Register or update a charge blocker."""
        self._set_operation_block(True, source, reason, details, coordinator)

    def remove_charge_block(self, source: str, coordinator=None) -> None:
        """Remove a charge blocker."""
        self._remove_operation_block(True, source, coordinator)

    def set_discharge_block(self, source: str, reason: str, details: dict | None = None, coordinator=None) -> None:
        """Register or update a discharge blocker."""
        self._set_operation_block(False, source, reason, details, coordinator)

    def remove_discharge_block(self, source: str, coordinator=None) -> None:
        """Remove a discharge blocker."""
        self._remove_operation_block(False, source, coordinator)

    def is_charge_blocked(self, coordinator=None) -> bool:
        """Return True if charge is blocked globally or for the given battery."""
        if self._global_charge_blockers:
            return True
        return bool(coordinator is not None and self._battery_charge_blockers.get(coordinator))

    def is_discharge_blocked(self, coordinator=None) -> bool:
        """Return True if discharge is blocked globally or for the given battery."""
        if self._global_discharge_blockers:
            return True
        return bool(coordinator is not None and self._battery_discharge_blockers.get(coordinator))

    def get_charge_blockers(self, coordinator=None) -> dict:
        """Return charge blockers for the requested scope."""
        if coordinator is None:
            return self._serialize_blockers(self._global_charge_blockers)
        merged = dict(self._global_charge_blockers)
        merged.update(self._battery_charge_blockers.get(coordinator, {}))
        return self._serialize_blockers(merged)

    def get_discharge_blockers(self, coordinator=None) -> dict:
        """Return discharge blockers for the requested scope."""
        if coordinator is None:
            return self._serialize_blockers(self._global_discharge_blockers)
        merged = dict(self._global_discharge_blockers)
        merged.update(self._battery_discharge_blockers.get(coordinator, {}))
        return self._serialize_blockers(merged)

    def get_battery_charge_blockers(self) -> dict:
        """Return per-battery charge blockers for diagnostics."""
        return {
            coordinator.name: self._serialize_blockers(blockers)
            for coordinator, blockers in self._battery_charge_blockers.items()
            if blockers
        }

    def get_battery_discharge_blockers(self) -> dict:
        """Return per-battery discharge blockers for diagnostics."""
        return {
            coordinator.name: self._serialize_blockers(blockers)
            for coordinator, blockers in self._battery_discharge_blockers.items()
            if blockers
        }

    def _known_batteries_for_block_summary(self) -> list:
        """Return batteries with enough data to summarize effective blockers."""
        return [
            coordinator
            for coordinator in self.coordinators
            if coordinator.data is not None
            and coordinator.is_available
            and not ChargeDischargeController._is_battery_manual_owned(coordinator)
        ]

    def is_charge_effectively_blocked(self) -> bool:
        """Return True when no known battery can currently accept charge."""
        if self._global_charge_blockers:
            return True
        batteries = self._known_batteries_for_block_summary()
        return bool(batteries) and all(
            self.is_charge_blocked(coordinator) for coordinator in batteries
        )

    def is_discharge_effectively_blocked(self) -> bool:
        """Return True when no known battery can currently discharge."""
        if self._global_discharge_blockers:
            return True
        batteries = self._known_batteries_for_block_summary()
        return bool(batteries) and all(
            self.is_discharge_blocked(coordinator) for coordinator in batteries
        )

    def _slot_battery_key(self, coordinator) -> str | None:
        """Return 'battery_<N>' for this coordinator's index, or None if unknown."""
        try:
            idx = self.coordinators.index(coordinator)
        except ValueError:
            return None
        return f"battery_{idx + 1}"

    def _slot_battery_limits(self, slot: dict, coordinator) -> dict:
        """Return per-battery override values from `slot['battery_limits']` for this coord."""
        bkey = self._slot_battery_key(coordinator)
        if bkey is None:
            return {}
        return slot.get("battery_limits", {}).get(bkey) or {}

    def _slot_applies_to_battery(self, slot: dict, coordinator) -> bool:
        """Return True if `slot.battery_scope` matches this coordinator (or is 'all')."""
        scope = slot.get("battery_scope", "all")
        if scope == "all":
            return True
        bkey = self._slot_battery_key(coordinator)
        if bkey is None:
            return False
        return scope == bkey

    @staticmethod
    def _slot_time_matches(slot: dict, now_time) -> bool:
        """Return True if the current local time falls within the slot's window.

        Supports midnight crossing when start_time > end_time (e.g. 22:00–06:00).
        """
        from datetime import time as dt_time
        try:
            start = dt_time.fromisoformat(slot["start_time"])
            end = dt_time.fromisoformat(slot["end_time"])
        except Exception as e:
            _LOGGER.error("Error parsing time slot: %s", e)
            return False
        if start <= end:
            return start <= now_time <= end
        # Midnight crossing: matches if outside the [end, start] gap.
        return now_time >= start or now_time <= end

    def _is_time_slot_allowed(self, coordinator, is_charging: bool) -> bool:
        """Per-battery, per-direction whitelist check for time slots.

        Behaviour:
          - No slots configured → allowed.
          - No slot for this battery has `allow_<direction>=True` → whitelist
            inactive for that direction → allowed.
          - Otherwise: allowed only if the current time matches a slot whose
            `allow_<direction>=True`, scope applies, and day matches.
        """
        from datetime import datetime

        all_slots = self.config_entry.data.get("no_discharge_time_slots", [])
        slots = [s for s in all_slots if s.get("enabled", True)]
        if not slots:
            return True

        field = "allow_charge" if is_charging else "allow_discharge"
        relevant = [s for s in slots if self._slot_applies_to_battery(s, coordinator)]
        if not any(s.get(field, False) for s in relevant):
            return True

        now = datetime.now()
        current_time = now.time()
        current_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]

        for slot in relevant:
            if not slot.get(field, False):
                continue
            if current_day not in slot.get("days", []):
                continue
            if self._slot_time_matches(slot, current_time):
                return True
        return False

    def _refresh_time_slot_blocks(self) -> None:
        """Update per-battery charge/discharge blockers from the configured slots."""
        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                self.remove_charge_block("time_slot_charge", coordinator=coordinator)
                self.remove_discharge_block("time_slot_discharge", coordinator=coordinator)
                continue
            if self._is_time_slot_allowed(coordinator, True):
                self.remove_charge_block("time_slot_charge", coordinator=coordinator)
            else:
                self.set_charge_block(
                    "time_slot_charge",
                    "time_slot",
                    {"direction": "charge", "battery": coordinator.name},
                    coordinator=coordinator,
                )

            if self._is_time_slot_allowed(coordinator, False):
                self.remove_discharge_block("time_slot_discharge", coordinator=coordinator)
            else:
                self.set_discharge_block(
                    "time_slot_discharge",
                    "time_slot",
                    {"direction": "discharge", "battery": coordinator.name},
                    coordinator=coordinator,
                )

    def _refresh_user_battery_blocks(self) -> None:
        """Update per-battery blockers from the software allow switches."""
        for coordinator in self.coordinators:
            if getattr(coordinator, "allow_charge", True):
                self.remove_charge_block("user_battery_charge_disabled", coordinator=coordinator)
            else:
                self.set_charge_block(
                    "user_battery_charge_disabled",
                    "user_disabled",
                    {"battery": coordinator.name},
                    coordinator=coordinator,
                )

            if getattr(coordinator, "allow_discharge", True):
                self.remove_discharge_block("user_battery_discharge_disabled", coordinator=coordinator)
            else:
                self.set_discharge_block(
                    "user_battery_discharge_disabled",
                    "user_disabled",
                    {"battery": coordinator.name},
                    coordinator=coordinator,
                )

    def _weekly_full_charge_unlocked(self) -> bool:
        """Return True when charging to 100% should bypass configured max SOC."""
        return self._weekly_charge_mgr.is_active()

    def _slot_charge_soc_max(self, coordinator) -> Optional[int]:
        """Return the active charge slot's SOC ceiling override, or None."""
        slot = self._get_active_slot(coordinator, "charge")
        if not slot or not slot.get("soc_override_enabled"):
            return None
        slot_max = self._slot_battery_limits(slot, coordinator).get("soc_max")
        if slot_max is None:
            return None
        try:
            return max(12, min(100, int(slot_max)))
        except (TypeError, ValueError):
            return None

    def _effective_charge_max_soc(
        self, coordinator, weekly_100_unlocked: bool
    ) -> tuple[float, str]:
        """Return the current per-battery charge ceiling and the source of that ceiling."""
        slot_cap = ChargeDischargeController._slot_charge_soc_max(self, coordinator)

        if weekly_100_unlocked:
            return 100, "weekly_full_charge"

        if slot_cap is not None:
            return slot_cap, "slot_soc_override"

        return coordinator.max_soc, "max_soc"

    def _effective_discharge_min_soc(self, coordinator) -> tuple[float, str]:
        """Return the current per-battery discharge floor and the source of that floor."""
        slot = self._get_active_slot(coordinator, "discharge")
        if slot and slot.get("soc_override_enabled"):
            limits = self._slot_battery_limits(slot, coordinator)
            slot_min = limits.get("soc_min")
            if slot_min is not None:
                try:
                    return max(12, min(100, int(slot_min))), "slot_soc_override"
                except (TypeError, ValueError):
                    pass
        return coordinator.min_soc, "min_soc"

    def _refresh_battery_charge_limit_blocks(self) -> None:
        """Expose max-SOC and hysteresis charge availability as per-battery blockers."""
        weekly_100_unlocked = self._weekly_full_charge_unlocked()

        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                self.remove_charge_block("max_soc", coordinator=coordinator)
                self.remove_charge_block("charge_hysteresis", coordinator=coordinator)
                self.remove_charge_block("bms_cutoff_retry", coordinator=coordinator)
                continue
            if coordinator.data is None:
                self.remove_charge_block("max_soc", coordinator=coordinator)
                self.remove_charge_block("charge_hysteresis", coordinator=coordinator)
                self.remove_charge_block("bms_cutoff_retry", coordinator=coordinator)
                continue

            current_soc = coordinator.data.get("battery_soc", 0)

            retry_pending = getattr(
                self, "_normal_balance_bms_cutoff_retry_pending", {}
            ).get(coordinator, False)
            if retry_pending:
                # A first Venus A/D refusal is provisional. Keep the battery
                # idle while the top cell relaxes; the retry path re-opens it
                # once the cell reaches the configured relaxation voltage.
                coordinator._hysteresis_active = False
                coordinator._hysteresis_base_soc = None
                self.remove_charge_block("max_soc", coordinator=coordinator)
                self.remove_charge_block("charge_hysteresis", coordinator=coordinator)
                self.set_charge_block(
                    "bms_cutoff_retry",
                    "bms_cutoff_retry",
                    {
                        "battery": coordinator.name,
                        "state": "waiting_for_relaxation",
                        "retry_voltage": NORMAL_BALANCE_RECAL_RETRY_CELL_VOLTAGE,
                        "soc": current_soc,
                    },
                    coordinator=coordinator,
                )
                continue
            self.remove_charge_block("bms_cutoff_retry", coordinator=coordinator)

            if weekly_100_unlocked:
                if coordinator.enable_charge_hysteresis and coordinator._hysteresis_active:
                    _LOGGER.debug("%s: Overriding hysteresis for weekly full charge", coordinator.name)
                coordinator._hysteresis_active = False
                coordinator._hysteresis_base_soc = None
                self.remove_charge_block("max_soc", coordinator=coordinator)
                self.remove_charge_block("charge_hysteresis", coordinator=coordinator)
                continue

            effective_max_soc, max_soc_source = self._effective_charge_max_soc(
                coordinator,
                weekly_100_unlocked,
            )
            # A coupled-pack battery is full when its *least* full pack reaches
            # the ceiling; its aggregate gets there while the last pack is still
            # filling (issue #350). Equals current_soc without pack telemetry.
            ceiling_soc = soc_vs_ceiling(coordinator, current_soc)

            should_charge_to_bms = getattr(self, "_should_charge_to_bms_cutoff", None)
            if should_charge_to_bms is not None and should_charge_to_bms(
                coordinator, effective_max_soc
            ):
                # Venus A/D can have coupled packs whose top-voltage telemetry
                # represents only the first pack. Keep the tapered charge alive
                # until the BMS itself confirms the cutoff.
                coordinator._hysteresis_active = False
                coordinator._hysteresis_base_soc = None
                self.remove_charge_block("max_soc", coordinator=coordinator)
                self.remove_charge_block("charge_hysteresis", coordinator=coordinator)
                continue

            if self._normal_balance_recal_override.get(coordinator):
                # SOC recalibration: don't let top-voltage hysteresis stop the
                # charge before the BMS cutoff.
                if coordinator.enable_charge_hysteresis and coordinator._hysteresis_active:
                    _LOGGER.debug("%s: Overriding hysteresis for SOC recalibration", coordinator.name)
                coordinator._hysteresis_active = False
                coordinator._hysteresis_base_soc = None
                self.remove_charge_block("max_soc", coordinator=coordinator)
                self.remove_charge_block("charge_hysteresis", coordinator=coordinator)
                continue

            bms_cutoff = self._weekly_charge_mgr.is_battery_full(coordinator)

            at_ceiling = ceiling_soc >= effective_max_soc or bms_cutoff

            if coordinator.enable_charge_hysteresis:
                # Activate hysteresis when cell voltage hits the BMS cutoff threshold,
                # regardless of whether the charge tapper feature is enabled.
                # Uses effective_max_soc so slot overrides are respected.
                taper_at_top_voltage = False
                if effective_max_soc >= 100:
                    _vmax = control_vmax(coordinator)
                    if _vmax is not None:
                        taper_at_top_voltage = _vmax >= NORMAL_BALANCE_PAUSE_CELL_VOLTAGE
                # Clear a stale latch so charge can resume toward the raised
                # target; a genuine top-of-charge re-arms immediately below.
                if _charge_hysteresis_latch_is_stale(coordinator):
                    coordinator._hysteresis_active = False
                    coordinator._hysteresis_base_soc = None

                if ceiling_soc >= coordinator.max_soc or bms_cutoff or taper_at_top_voltage:
                    coordinator._hysteresis_active = True
                    if coordinator._hysteresis_base_soc is None:
                        # Base stays the aggregate: it answers "how far must the
                        # whole battery fall before recharging", not "is it full".
                        coordinator._hysteresis_base_soc = current_soc
                        coordinator._hysteresis_latch_max_soc = coordinator.max_soc

                hysteresis_base = (
                    coordinator._hysteresis_base_soc
                    if coordinator._hysteresis_base_soc is not None
                    else coordinator.max_soc
                )
                charge_threshold = hysteresis_base - coordinator.charge_hysteresis_percent

                if current_soc < charge_threshold:
                    coordinator._hysteresis_active = False
                    coordinator._hysteresis_base_soc = None

                if coordinator._hysteresis_active:
                    if at_ceiling:
                        self.set_charge_block(
                            "max_soc",
                            "max_soc",
                            {
                                "battery": coordinator.name,
                                "soc": current_soc,
                                "max_soc": coordinator.max_soc,
                                "effective_max_soc": effective_max_soc,
                                "source": max_soc_source,
                                "bms_cutoff": bms_cutoff,
                            },
                            coordinator=coordinator,
                        )
                    else:
                        self.remove_charge_block("max_soc", coordinator=coordinator)
                    self.set_charge_block(
                        "charge_hysteresis",
                        "hysteresis",
                        {
                            "battery": coordinator.name,
                            "soc": current_soc,
                            "max_soc": coordinator.max_soc,
                            "threshold": charge_threshold,
                            "base_soc": hysteresis_base,
                            "hysteresis_percent": coordinator.charge_hysteresis_percent,
                            "bms_cutoff": bms_cutoff,
                        },
                        coordinator=coordinator,
                    )
                    continue

            self.remove_charge_block("charge_hysteresis", coordinator=coordinator)

            if at_ceiling:
                self.set_charge_block(
                    "max_soc",
                    "max_soc",
                    {
                        "battery": coordinator.name,
                        "soc": current_soc,
                        "max_soc": coordinator.max_soc,
                        "effective_max_soc": effective_max_soc,
                        "source": max_soc_source,
                        "bms_cutoff": bms_cutoff,
                        **({"min_pack_soc": ceiling_soc} if ceiling_soc != current_soc else {}),
                    },
                    coordinator=coordinator,
                )
            else:
                self.remove_charge_block("max_soc", coordinator=coordinator)

    def _should_charge_to_bms_cutoff(self, coordinator, effective_max_soc: float) -> bool:
        """Return whether a top-voltage battery must remain charge-eligible."""
        manager = getattr(self, "_max_soc_mgr", None)
        should_charge = getattr(manager, "should_charge_to_bms_cutoff", None)
        if should_charge is None:
            return False
        return bool(should_charge(coordinator, effective_max_soc))

    def _refresh_battery_discharge_limit_blocks(self) -> None:
        """Expose min-SOC discharge availability as per-battery blockers."""
        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                self.remove_discharge_block("min_soc", coordinator=coordinator)
                continue
            if coordinator.data is None:
                self.remove_discharge_block("min_soc", coordinator=coordinator)
                continue

            current_soc = coordinator.data.get("battery_soc", 0)
            effective_min_soc, min_soc_source = self._effective_discharge_min_soc(coordinator)
            # The aggregate, also on a coupled-pack battery: its BMS rotates
            # packs and stops on the total, not on the first pack (issue #545).
            if current_soc <= effective_min_soc:
                self.set_discharge_block(
                    "min_soc",
                    "min_soc",
                    {
                        "battery": coordinator.name,
                        "soc": current_soc,
                        "min_soc": coordinator.min_soc,
                        "effective_min_soc": effective_min_soc,
                        "source": min_soc_source,
                    },
                    coordinator=coordinator,
                )
            else:
                self.remove_discharge_block("min_soc", coordinator=coordinator)

    def _refresh_ev_blocks(self) -> None:
        """Update EV charger blockers from no-telemetry charger state."""
        ev_pause_active, ev_charging_active = self._external_loads.check_ev_charger_state()
        if ev_pause_active:
            self.set_charge_block("ev_pause", "ev_pause", {"duration": "5_min"})
            self.set_discharge_block("ev_pause", "ev_pause", {"duration": "5_min"})
        else:
            self.remove_charge_block("ev_pause")
            self.remove_discharge_block("ev_pause")

        if ev_charging_active:
            self.set_discharge_block("ev_charging", "ev_charging")
        else:
            self.remove_discharge_block("ev_charging")

    def _refresh_dynamic_power_control_block(self) -> None:
        """Let self-regulating excluded loads react before battery operation."""
        status = self._external_loads.refresh_dynamic_power_control()
        details = {
            "phases": status["phases"],
            "hold_remaining_s": status["hold_remaining_s"],
            "yield_remaining_s": status["yield_remaining_s"],
        }
        if status["charge_blocked"]:
            self.set_charge_block(
                "excluded_device_dynamic_power_control",
                "dynamic_power_control",
                {
                    "devices": ",".join(status["blocked_devices"]),
                    **details,
                },
            )
        else:
            self.remove_charge_block("excluded_device_dynamic_power_control")

        if status["discharge_blocked"]:
            self.set_discharge_block(
                "excluded_device_dynamic_power_control",
                "dynamic_power_control",
                {
                    "devices": ",".join(status["discharge_blocked_devices"]),
                    **details,
                },
            )
        else:
            self.remove_discharge_block("excluded_device_dynamic_power_control")

    def _refresh_operation_blockers(self) -> None:
        """Refresh all runtime operation blockers for the current control cycle."""
        self._refresh_time_slot_blocks()
        self._refresh_ev_blocks()
        self._refresh_dynamic_power_control_block()
        self._refresh_user_battery_blocks()
        self._refresh_normal_balance_blocks()
        self._refresh_battery_charge_limit_blocks()
        self._refresh_battery_discharge_limit_blocks()

    def _is_operation_allowed(self, is_charging: bool) -> bool:
        """Return True if the refreshed blocker registry allows this operation."""
        return not (self.is_charge_blocked() if is_charging else self.is_discharge_blocked())

    def _operation_blockers_for_log(self, is_charging: bool) -> str:
        """Return the actual global blocker keys for an operation log."""
        blockers = self.get_charge_blockers() if is_charging else self.get_discharge_blockers()
        return ", ".join(blockers) or "unknown"

    def _pd_demand_blocked(self, error: float, commanded_power: float) -> bool:
        """Return True when the loop cannot act on what the grid error demands.

        The cycle's own restriction check keys on the *commanded* power, which is
        already 0 once a previous cycle was blocked: a blocked charge demand then
        reads as "not charging", the discharge direction is checked instead, and
        the loop counts as active while it cannot act at all. The demand direction
        comes from the error sign (error < 0 = export = charge demand) and does
        not decay, so it is what the quality metric must key on.

        A closed gate alone is not enough, though. While discharging into house
        load with charging blocked, an export error is answered by discharging
        *less* — the loop has headroom in the direction it is already running, and
        its tracking there is a fair tuning verdict. Only when the command carries
        no such headroom (0 W, or already running into the blocked direction) is
        the loop truly muzzled.
        """
        if abs(error) <= self.deadband:
            return False
        demand_is_charging = error < 0
        # Effective = global blockers OR every PD-owned battery blocked, so a
        # per-battery block (charge hysteresis, limits) on all of them counts.
        blocked = (
            self.is_charge_effectively_blocked()
            if demand_is_charging
            else self.is_discharge_effectively_blocked()
        )
        if not blocked:
            return False
        demand_sign = 1 if demand_is_charging else -1
        # Opposite-signed command = room to move toward the demand without
        # entering the blocked direction.
        return commanded_power * demand_sign >= 0

    def _get_active_slot(self, coordinator=None, direction: str = "any") -> dict | None:
        """Return the active slot for a battery/direction, or None.

        Args:
            coordinator: per-battery filter. If None, ignore battery_scope.
            direction: "charge", "discharge", or "any". When "charge"/"discharge",
                only slots with `allow_<direction>=True` are considered.
        """
        from datetime import datetime

        slots = self.config_entry.data.get("no_discharge_time_slots", [])
        if not slots:
            return None

        now = datetime.now()
        current_time = now.time()
        current_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]

        for slot in slots:
            if not slot.get("enabled", True):
                continue
            if coordinator is not None and not self._slot_applies_to_battery(slot, coordinator):
                continue
            if direction == "charge" and not slot.get("allow_charge", False):
                continue
            if direction == "discharge" and not slot.get("allow_discharge", False):
                continue
            if current_day not in slot.get("days", []):
                continue
            if self._slot_time_matches(slot, current_time):
                return slot
        return None

    def _get_available_batteries(
        self,
        is_charging: bool,
        include_operation_blocks: bool = True,
    ) -> list:
        """Get list of available batteries for the current operation.
        
        For charging with hysteresis:
          1. Battery charges normally until reaching max_soc
          2. Once max_soc is reached, hysteresis activates
          3. Battery won't charge again until SOC drops below (max_soc - hysteresis_percent)
          4. When SOC drops below threshold, hysteresis deactivates and charging resumes
        
        For discharging: only checks min_soc
        """
        available_batteries = []
        for coordinator in self.coordinators:
            if coordinator.data is None:
                continue

            # Individual manual mode is an ownership boundary, not an
            # operation blocker. Exclude it before availability and blocker
            # evaluation so planning cannot select or classify it as automatic.
            if getattr(coordinator, CONF_BATTERY_MANUAL_MODE_ENABLED, False):
                _LOGGER.debug(
                    "%s: Skipping - individual manual mode owns this battery",
                    coordinator.name,
                )
                continue

            # Skip batteries that are unreachable
            if not coordinator.is_available:
                _LOGGER.debug("%s: Skipping - battery unreachable (failures: %d)",
                             coordinator.name, coordinator._consecutive_failures)
                continue

            # Skip batteries excluded due to non-responsive behavior
            if self._non_responsive.is_excluded(coordinator):
                _LOGGER.debug("%s: Skipping - excluded due to non-responsive behavior", coordinator.name)
                continue

            # Skip batteries with backup function active (they manage themselves autonomously)
            if self._is_backup_function_active(coordinator):
                _LOGGER.debug("%s: Skipping - backup function is active", coordinator.name)
                continue

            # Skip batteries the user excluded from integration control: RS485 control
            # disabled means the battery is driven by the official app / its own logic.
            if coordinator.rs485_user_disabled:
                _LOGGER.debug("%s: Skipping - RS485 control disabled by user", coordinator.name)
                continue

            if self._is_manual_slot_owned(coordinator):
                _LOGGER.debug("%s: Skipping - manual time slot owns this battery", coordinator.name)
                continue

            if include_operation_blocks and is_charging:
                charge_blockers = self.get_charge_blockers(coordinator)
                if charge_blockers:
                    _LOGGER.debug(
                        "%s: Skipping charge - blocked by %s",
                        coordinator.name,
                        ", ".join(charge_blockers.keys()),
                    )
                    continue

            if include_operation_blocks and not is_charging:
                if self.is_discharge_blocked(coordinator):
                    _LOGGER.debug(
                        "%s: Skipping discharge - blocked by %s",
                        coordinator.name,
                        ", ".join(self.get_discharge_blockers(coordinator).keys()),
                    )
                    continue

            current_soc = coordinator.data.get("battery_soc", 0)
            
            if is_charging:
                # Check if weekly full charge is active AND 100% is actually unlocked
                weekly_100_unlocked = self._weekly_full_charge_unlocked()

                # Determine effective max SOC (respects slot overrides)
                effective_max_soc, max_soc_source = self._effective_charge_max_soc(
                    coordinator,
                    weekly_100_unlocked,
                )

                should_charge_to_bms = getattr(self, "_should_charge_to_bms_cutoff", None)
                charge_to_bms_cutoff = bool(
                    should_charge_to_bms is not None
                    and should_charge_to_bms(coordinator, effective_max_soc)
                )
                # Judged on the least full pack for a coupled-pack battery, so a
                # finished first pack cannot end the charge (issue #350).
                # Identical to current_soc without pack telemetry.
                ceiling_soc = soc_vs_ceiling(coordinator, current_soc)

                # Update hysteresis state if enabled
                if coordinator.enable_charge_hysteresis:
                    if weekly_100_unlocked:
                        # Force-disable hysteresis during weekly full charge.
                        if coordinator._hysteresis_active:
                            _LOGGER.debug(
                                "%s: Overriding hysteresis for weekly full charge",
                                coordinator.name,
                            )
                        coordinator._hysteresis_active = False
                        coordinator._hysteresis_base_soc = None
                    elif self._normal_balance_recal_override.get(coordinator):
                        # SOC recalibration: bypass top-voltage hysteresis so the
                        # charge continues to the BMS cutoff.
                        if coordinator._hysteresis_active:
                            _LOGGER.debug(
                                "%s: Overriding hysteresis for SOC recalibration",
                                coordinator.name,
                            )
                        coordinator._hysteresis_active = False
                        coordinator._hysteresis_base_soc = None
                    elif charge_to_bms_cutoff:
                        # Venus A/D may report 100% as soon as the first coupled
                        # pack is full. Do not let that or the 3.60 V top-cell
                        # reading block the remaining packs before BMS cutoff.
                        if coordinator._hysteresis_active:
                            _LOGGER.debug(
                                "%s: Continuing tapered charge until Venus A/D BMS cutoff",
                                coordinator.name,
                            )
                        coordinator._hysteresis_active = False
                        coordinator._hysteresis_base_soc = None
                    else:
                        # Normal hysteresis logic
                        _vmax_hysteresis = control_vmax(coordinator)
                        _taper_at_top = False
                        if effective_max_soc >= 100 and _vmax_hysteresis is not None:
                            _taper_at_top = _vmax_hysteresis >= NORMAL_BALANCE_PAUSE_CELL_VOLTAGE
                        # Clear a stale latch so charge resumes toward the
                        # raised target; a genuine top re-arms below.
                        if _charge_hysteresis_latch_is_stale(coordinator):
                            coordinator._hysteresis_active = False
                            coordinator._hysteresis_base_soc = None

                        if ceiling_soc >= coordinator.max_soc or _taper_at_top:
                            coordinator._hysteresis_active = True
                            # Capture the actual SOC that triggered hysteresis (may be 100% after full charge)
                            if coordinator._hysteresis_base_soc is None:
                                coordinator._hysteresis_base_soc = current_soc
                                coordinator._hysteresis_latch_max_soc = coordinator.max_soc

                        # Use actual peak SOC as threshold base (handles post-full-charge case)
                        hysteresis_base = coordinator._hysteresis_base_soc if coordinator._hysteresis_base_soc else coordinator.max_soc
                        charge_threshold = hysteresis_base - coordinator.charge_hysteresis_percent
                        if current_soc < charge_threshold:
                            coordinator._hysteresis_active = False
                            coordinator._hysteresis_base_soc = None

                        if coordinator._hysteresis_active:
                            _LOGGER.debug("%s: Skipping charge - Hysteresis active (SOC %.1f%%, threshold: %.1f%%, base: %.1f%%)",
                                         coordinator.name, current_soc, charge_threshold, hysteresis_base)
                            continue

                if max_soc_source == "weekly_full_charge":
                    _LOGGER.debug("%s: Weekly Full Charge active - effective_max_soc=100%% (configured: %d%%)",
                                 coordinator.name, coordinator.max_soc)

                # BMS cutoff detection: counter is maintained by tick_bms_cutoff() which
                # runs unconditionally at the top of handle_registers() each cycle.
                # is_battery_full() is shared with handle_registers() and prepares
                # provisional Venus A/D retries before reporting a battery full.
                # A retry keeps the one-shot 200 W command eligible until the
                # second cutoff; the normal SOC-recalibration path does the same.
                normal_recal_active = self._normal_balance_recal_override.get(
                    coordinator, False
                )
                if (
                    self._weekly_charge_mgr.is_battery_full(coordinator)
                    and not normal_recal_active
                    and not charge_to_bms_cutoff
                ):
                    if coordinator.enable_charge_hysteresis and not coordinator._hysteresis_active:
                        coordinator._hysteresis_active = True
                        if coordinator._hysteresis_base_soc is None:
                            coordinator._hysteresis_base_soc = current_soc
                            coordinator._hysteresis_latch_max_soc = coordinator.max_soc
                        _LOGGER.debug(
                            "%s: BMS cutoff at %d%% — activating hysteresis",
                            coordinator.name, current_soc,
                        )
                    else:
                        _LOGGER.debug(
                            "%s: BMS cutoff at %d%% — skipping charge allocation",
                            coordinator.name, current_soc,
                        )
                    continue

                # Only charge if below effective max SOC
                if ceiling_soc < effective_max_soc or charge_to_bms_cutoff:
                    available_batteries.append(coordinator)
            else:  # discharging
                # MIN-SOC RE-ENTRY HYSTERESIS: after emptying to min_soc the
                # resting SOC rebounds 1-2% (cell relaxation) and would re-admit
                # the battery for a sliver of discharge — relay ping-pong and
                # micro-cycles at the worst SOC region. Latch the exclusion at
                # min_soc; release only after a real recovery margin.
                # A battery that stopped delivering above min_soc (see
                # _check_non_delivery) is held out the same way, from the SOC
                # it stopped at.
                refused_soc = getattr(coordinator, "_discharge_refused_soc", None)
                if (
                    refused_soc is not None
                    and current_soc >= refused_soc + DISCHARGE_MIN_SOC_REENTRY_MARGIN
                ):
                    coordinator._discharge_refused_soc = refused_soc = None
                if current_soc <= coordinator.min_soc:
                    coordinator._discharge_min_soc_latched = True
                elif current_soc >= coordinator.min_soc + DISCHARGE_MIN_SOC_REENTRY_MARGIN:
                    coordinator._discharge_min_soc_latched = False
                if current_soc > coordinator.min_soc:
                    if getattr(coordinator, "_discharge_min_soc_latched", False):
                        _LOGGER.debug(
                            "%s: Skipping discharge - min-SOC re-entry hysteresis "
                            "(SOC %.1f%%, releases at %.1f%%)",
                            coordinator.name, current_soc,
                            coordinator.min_soc + DISCHARGE_MIN_SOC_REENTRY_MARGIN,
                        )
                    elif refused_soc is not None:
                        _LOGGER.debug(
                            "%s: Skipping discharge - stopped delivering at SOC "
                            "%.1f%% (releases at %.1f%%)",
                            coordinator.name, refused_soc,
                            refused_soc + DISCHARGE_MIN_SOC_REENTRY_MARGIN,
                        )
                    else:
                        available_batteries.append(coordinator)
        
        return available_batteries

    # -------------------------------------------------------------------------
    # Non-responsive battery detection helpers
    # -------------------------------------------------------------------------

    def _is_backup_function_active(self, coordinator) -> bool:
        """Return True if the battery must be excluded from PD control due to backup mode.

        A battery is excluded when:
          - The Backup Function switch is enabled (register value == 0) AND
          - The AC offgrid power sensor reads above the user-configured threshold
            (default 50 W), OR the sensor is unavailable.

        Additionally, a 5-minute cooldown is applied after the offgrid load
        drops to 0: the battery stays excluded until the cooldown expires to
        avoid sending write commands immediately after a backup event ends.

        The switch turning OFF clears the cooldown immediately.
        """
        if coordinator.data is None:
            return False

        now = dt_util.utcnow()

        # From SWITCH_DEFINITIONS: command_on = 0 (enabled), command_off = 1 (disabled)
        backup_value = coordinator.data.get("backup_function")
        if not _backup_switch_enabled(backup_value):
            # Switch is off — clear any lingering cooldown and allow PD control
            self._backup_cooldown_until.pop(coordinator, None)
            return False

        # Switch is ON. Check whether the battery is actively providing offgrid power.
        ac_offgrid = coordinator.data.get("ac_offgrid_power")

        # In Bypass the grid is passed through to the backup port, so a load
        # connected there reads as off-grid power although the battery supplies
        # nothing. Only a real outage (backup state) means the battery is
        # feeding the port, so treat the port as idle here and let the normal
        # post-backup cooldown run out. The two values are read in separate
        # register groups, and a failed read leaves the old value in place, so a
        # stale Bypass must not hide a fresh port load: only trust it when both
        # came from the same poll.
        if _inverter_in_ac_bypass(
            coordinator.data.get("inverter_state")
        ) and coordinator.readings_from_same_poll(
            "inverter_state", "ac_offgrid_power"
        ):
            ac_offgrid = 0

        # Small permanent loads (e.g. a PoE switch, router, or AP connected to the
        # offgrid port) should not trigger backup exclusion. Only a substantial load
        # — indicative of a real grid-outage scenario — warrants excluding the battery
        # from PD control. The threshold is user-configurable (default 50 W).
        threshold = coordinator.backup_offgrid_threshold

        if ac_offgrid is not None and ac_offgrid <= threshold:
            # Offgrid power is zero or a small standby load — check post-backup cooldown
            cooldown_until = self._backup_cooldown_until.get(coordinator)
            if cooldown_until and now < cooldown_until:
                remaining = int((cooldown_until - now).total_seconds() / 60)
                _LOGGER.debug(
                    "%s: Backup cooldown active — %d min remaining before re-entering PD control",
                    coordinator.name, remaining
                )
                return True
            # Cooldown expired (or was never set) — allow PD control
            self._backup_cooldown_until.pop(coordinator, None)
            return False

        # Offgrid power > threshold (or sensor not available): backup is actively running.
        # Refresh the cooldown window so it starts counting from the last active reading.
        if ac_offgrid is not None:
            _LOGGER.debug(
                "%s: Backup active — offgrid load %.0fW exceeds %.0fW threshold, excluding from PD control",
                coordinator.name, ac_offgrid, threshold
            )
        self._backup_cooldown_until[coordinator] = now + timedelta(minutes=5)
        return True

    @property
    def non_responsive_battery_names(self) -> list[str]:
        """Return names of batteries excluded or currently unreachable."""
        names: list[str] = []
        for name in self._non_responsive.excluded_names():
            if name not in names:
                names.append(name)

        for coordinator in self.coordinators:
            if (
                not coordinator.is_available
                and not getattr(coordinator, "_is_shutting_down", False)
                and getattr(coordinator, "_consecutive_failures", 0) > 0
                and coordinator.name not in names
            ):
                names.append(coordinator.name)

        return names

    # -------------------------------------------------------------------------


    def _apply_meter_transform(self, state) -> float | None:
        """Read and transform a grid meter state.

        Handles:
        - Auto kW detection: if unit_of_measurement is 'kW', multiplies by 1000.
        - Inverted sign: applies the setting belonging to the active meter.

        Returns the value in Watts with correct sign convention, or None on error.
        """
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None
        unit = state.attributes.get("unit_of_measurement", "W")
        if unit == "kW":
            value *= 1000.0
        offgrid_active = bool(
            getattr(self, "offgrid_mode_enabled", False)
            and getattr(self, "offgrid_power_sensor", None)
        )
        meter_inverted = (
            getattr(self, "offgrid_meter_inverted", False)
            if offgrid_active
            else getattr(self, "meter_inverted", False)
        )
        if meter_inverted:
            value = -value
        return value

    def _log_consumption_sensor_issue(self, state) -> None:
        """Log a grid-meter problem once until the sensor becomes valid again."""
        sensor_state = None if state is None else state.state
        if getattr(self, "_consumption_sensor_issue", None) == self.consumption_sensor:
            return
        self._consumption_sensor_issue = self.consumption_sensor

        if state is None:
            _LOGGER.warning("Consumption sensor %s not found", self.consumption_sensor)
        elif sensor_state in ("unknown", "unavailable"):
            _LOGGER.debug(
                "Consumption sensor %s is %s; pausing automatic control",
                self.consumption_sensor,
                sensor_state,
            )
        else:
            _LOGGER.warning(
                "Could not parse consumption sensor %s state: %s",
                self.consumption_sensor,
                sensor_state,
            )

    # -------------------------------------------------------------------------
    # Setpoint offset management
    #
    # Reference = 0 W (zero grid flow). Two layers:
    #
    #   1. Additive offsets (_setpoint_offsets):
    #      Summed to form the default target.
    #      Use for preferences that compose with each other.
    #      Examples:
    #        "user_target" = -50 W  (slight export preference from config)
    #        "<feature>" = +200 W  (shift the target for a feature)
    #
    #   2. Absolute overrides (_setpoint_overrides):
    #      Each has a priority (int). When any override is active, the one
    #      with the highest priority wins and REPLACES the additive sum.
    #      Use for modes that need full control of the target.
    #      Examples:
    #        "capacity_protection" (pri=10) → 2000 W  (peak shaving limit)
    #
    # Resolution:
    #   active_target = highest-priority override  (if any override exists)
    #                 | sum(additive offsets)       (otherwise)
    # -------------------------------------------------------------------------

    def compute_active_target(self) -> float:
        """Compute the effective PD target from offsets and overrides."""
        if self._setpoint_overrides:
            # Highest priority override wins
            source, (_, value) = max(self._setpoint_overrides.items(), key=lambda x: x[1][0])
            return value
        return sum(self._setpoint_offsets.values())

    def compute_active_target_excluding(self, excluded_source: str) -> float:
        """Compute the active target while ignoring one override source."""
        overrides = {
            source: override
            for source, override in self._setpoint_overrides.items()
            if source != excluded_source
        }
        if overrides:
            source, (_, value) = max(overrides.items(), key=lambda x: x[1][0])
            return value
        return sum(self._setpoint_offsets.values())

    def set_setpoint_offset(self, source: str, offset_w: float) -> None:
        """Register or update an additive offset (summed with others)."""
        old = self._setpoint_offsets.get(source)
        self._setpoint_offsets[source] = offset_w
        if old != offset_w:
            _LOGGER.debug("Setpoint offset '%s': %s → %.0fW",
                          source, f"{old:.0f}W" if old is not None else "None", offset_w)

    def remove_setpoint_offset(self, source: str) -> None:
        """Remove an additive offset. No-op if not present."""
        removed = self._setpoint_offsets.pop(source, None)
        if removed is not None:
            _LOGGER.debug("Setpoint offset '%s' removed (was %.0fW)", source, removed)

    def set_setpoint_override(self, source: str, value_w: float, priority: int = 0) -> None:
        """Register an absolute override. Highest priority wins over all offsets."""
        old = self._setpoint_overrides.get(source)
        self._setpoint_overrides[source] = (priority, value_w)
        old_str = f"{old[1]:.0f}W (pri={old[0]})" if old else "None"
        _LOGGER.debug("Setpoint override '%s': %s → %.0fW (pri=%d)",
                      source, old_str, value_w, priority)

    def remove_setpoint_override(self, source: str) -> None:
        """Remove an absolute override. No-op if not present."""
        removed = self._setpoint_overrides.pop(source, None)
        if removed is not None:
            _LOGGER.debug("Setpoint override '%s' removed (was %.0fW, pri=%d)",
                          source, removed[1], removed[0])

    def get_setpoint_offset(self, source: str) -> float:
        """Return the current additive offset for *source*, or 0.0 if not set."""
        return self._setpoint_offsets.get(source, 0.0)

    def clear_all_setpoint_offsets(self) -> None:
        """Remove all additive offsets and overrides."""
        if self._setpoint_offsets:
            _LOGGER.debug("Clearing all setpoint offsets: %s", dict(self._setpoint_offsets))
            self._setpoint_offsets.clear()
        if self._setpoint_overrides:
            _LOGGER.debug("Clearing all setpoint overrides: %s",
                          {k: v[1] for k, v in self._setpoint_overrides.items()})
            self._setpoint_overrides.clear()

    def _capacity_protection_coordinators(self) -> list:
        """Batteries whose SOC may drive the peak-shaving decision.

        A battery the control loop cannot move - unavailable or cooling down
        in ``_non_responsive`` - keeps its last SOC in ``data`` while
        delivering nothing. Averaging that stale value in held the fleet
        average above the threshold while the remaining battery drained past
        it (#803), so only batteries that can actually discharge count here,
        mirroring the pool the control path selects.
        """
        is_excluded = getattr(
            getattr(self, "_non_responsive", None), "is_excluded", None
        )
        eligible = []
        for coordinator in self.coordinators:
            if not coordinator.data:
                continue
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            if not getattr(coordinator, "is_available", True):
                continue
            if callable(is_excluded) and is_excluded(coordinator):
                continue
            eligible.append(coordinator)
        return eligible

    def _apply_capacity_protection(
        self, sensor_actual: float, active_target: float,
        physical_actual: float | None = None,
    ) -> tuple[float, float]:
        """Apply peak-shaving override and return the effective target and sensor value."""
        # FORK: re-decided every cycle; only "conserving" and "charging" set it.
        self._capacity_protection_block_discharge = False
        if not self.capacity_protection_enabled:
            self._capacity_protection_soc_latched = False  # FORK: SOC hysteresis
            self.remove_setpoint_override("capacity_protection")
            self._capacity_protection_active = False
            self._capacity_protection_status["active"] = False
            self._capacity_protection_status["action"] = "disabled"
            self._capacity_protection_status["excluded_peak_excess"] = 0
            self._capacity_protection_status["excluded_devices_enabled"] = (
                self.capacity_protection_excluded_devices
            )
            return self.compute_active_target(), sensor_actual

        # Stale SOC from a battery that cannot answer stays in ``data`` and
        # used to be averaged in here (#803): the fleet average sat above the
        # threshold while the remaining battery drained past it.
        coordinators_with_data = self._capacity_protection_coordinators()
        if coordinators_with_data:
            avg_soc = (
                sum(c.data.get("battery_soc", 0) for c in coordinators_with_data)
                / len(coordinators_with_data)
            )
        else:
            # No battery can respond: assume full so peak shaving stays idle
            # rather than acting on SOC it cannot influence.
            avg_soc = 100  # Assume full if no data, don't activate protection

        # Use the non-capacity-protection target for decisions. The previous
        # cycle's capacity_protection override may still be registered here; if
        # we compare against it, normal below-limit import can be mistaken for
        # solar surplus and the controller starts a short discharge/stop loop.
        active_target = self.compute_active_target_excluding("capacity_protection")
        original_target = active_target
        # Reconstruct from AC telemetry co-incident with the grid meter.  A
        # command can lag by several seconds, so ``previous_power`` is a last-resort fallback,
        # never the preferred source for a peak-safety decision.
        measured_power = None
        measured_reader = getattr(self, "_measured_battery_power", None)
        if callable(measured_reader):
            measured_power = measured_reader()
        battery_power = (
            measured_power if measured_power is not None else self.previous_power
        )
        # FORK: the AC telemetry trails a fresh charge command by 1-6 s. Read as
        # 0 W it makes the charge's own effect on the meter look like house
        # import, and "conserving" force-idles a solar-surplus charge - which
        # re-engages on the next surplus sample (chatter, 18.11.2026). While a
        # charge is commanded, never assume less than the command. Charge side
        # only: a too-high charge estimate at worst lets the PD trim the charge itself.
        try:
            commanded_charge = max(0.0, float(self.previous_power or 0))
        except (TypeError, ValueError):
            commanded_charge = 0.0
        if commanded_charge > 0 and commanded_charge > battery_power:
            battery_power = commanded_charge
        # FORK: grid view (docs/fork/02-architektur.md)
        limit = self._effective_peak_limit()
        if physical_actual is not None:
            # Real meter already contains the excluded load: no add-back.
            estimated_house_load = physical_actual - battery_power
        else:
            estimated_house_load = (
                sensor_actual + self._excluded_included_adjustment
            ) - battery_power
        self._capacity_protection_status["excluded_peak_excess"] = 0

        soc_protected = ChargeDischargeController._capacity_protection_soc_below(self, avg_soc)
        self._capacity_protection_soc_latched = soc_protected  # FORK: SOC hysteresis
        if soc_protected:
            # Estimate house consumption: grid reading minus what the battery is currently doing
            # sensor_actual = grid power (positive=import), previous_power > 0 = charging, < 0 = discharging
            # Add back excluded-device adjustment so capacity protection sees the REAL grid load
            # including devices marked as "included in consumption". This ensures capacity
            # protection can shave peaks even when those devices are normally excluded.
            if estimated_house_load > limit:
                # House load exceeds peak limit: discharge only the excess
                # Undo excluded-device adjustment so PD controller can discharge against real grid
                if physical_actual is not None:
                    sensor_actual = physical_actual  # FORK: grid view (docs/fork/02-architektur.md)
                elif self._excluded_included_adjustment > 0:
                    _LOGGER.info(
                        "Capacity Protection overriding excluded device adjustment (%.0fW) for peak shaving",
                        self._excluded_included_adjustment,
                    )
                    sensor_actual += self._excluded_included_adjustment
                self.set_setpoint_override("capacity_protection", limit, priority=10)
                active_target = self.compute_active_target()
                _LOGGER.info(
                    "Capacity Protection ACTIVE: SOC=%.1f%% < %d%%, house_load=%.0fW > limit=%dW -> target=%dW",
                    avg_soc,
                    self.capacity_protection_soc_threshold,
                    estimated_house_load,
                    limit,
                    active_target,
                )
                self._capacity_protection_active = True
                self._capacity_protection_status.update({
                    "active": True, "avg_soc": round(avg_soc, 1),
                    "estimated_house_load": round(estimated_house_load),
                    "action": "shaving",
                    "original_target": original_target, "adjusted_target": active_target,
                })
            elif estimated_house_load > active_target:
                # House load is below peak limit but above normal target: hold the
                # current grid level and stop any existing battery command immediately.
                # Undo excluded-device adjustment so target aligns with real grid reading
                if self._excluded_included_adjustment > 0:
                    _LOGGER.info(
                        "Capacity Protection overriding excluded device adjustment (%.0fW) for conservation",
                        self._excluded_included_adjustment,
                    )
                    sensor_actual += self._excluded_included_adjustment
                self.set_setpoint_override("capacity_protection", sensor_actual, priority=10)
                active_target = self.compute_active_target()
                peak_exit_hold = False
                if self.previous_power != 0:
                    peak_exit_hold = ChargeDischargeController._peak_exit_hold_active(self)  # FORK: review 2/7
                    if not peak_exit_hold:
                        self._capacity_protection_force_idle = True
                # FORK: target = actual gives err 0, but the D-term and the minimum
                # power can still turn a few watts into a 30 W discharge. The peak
                # exit hold keeps its discharge on purpose.
                self._capacity_protection_block_discharge = not peak_exit_hold
                _LOGGER.info(
                    "Capacity Protection ACTIVE: SOC=%.1f%% < %d%%, house_load=%.0fW <= limit=%dW -> idle (target=%.0fW)",
                    avg_soc,
                    self.capacity_protection_soc_threshold,
                    estimated_house_load,
                    limit,
                    active_target,
                )
                self._capacity_protection_active = True
                self._capacity_protection_status.update({
                    "active": True, "avg_soc": round(avg_soc, 1),
                    "estimated_house_load": round(estimated_house_load),
                    "action": "conserving",
                    "original_target": original_target, "adjusted_target": active_target,
                })
            else:
                # Solar surplus: normal charging, but SOC is still below threshold
                self.remove_setpoint_override("capacity_protection")
                active_target = self.compute_active_target()
                # FORK: this branch decides on the raw (physical) meter while the PD
                # regulates the EMA-filtered control value. After a drop in import
                # the filter still shows import (raw 10 W, filtered 54 W at
                # 13:41:39 on 18.11.2026) and the PD discharged at SOC < threshold.
                # Charging only.
                self._capacity_protection_block_discharge = True
                self._capacity_protection_active = True
                self._capacity_protection_status.update({
                    "active": True, "avg_soc": round(avg_soc, 1),
                    "estimated_house_load": round(estimated_house_load),
                    "action": "charging",
                    "original_target": original_target, "adjusted_target": active_target,
                })
        else:
            # Above the conservation threshold, normal self-consumption remains
            # active. Optionally reduce only the excluded share that would leave
            # grid import above the peak limit:
            #
            #   physical grid target = normal target + excluded adjustment
            #
            # Adding just the excess back to sensor_actual preserves the normal
            # target while making the PD controller cover the above-limit share.
            self.remove_setpoint_override("capacity_protection")
            active_target = self.compute_active_target()
            excluded_peak_excess = 0.0
            if (
                self.capacity_protection_excluded_devices
                and self._excluded_included_adjustment > 0
            ):
                excluded_peak_excess = max(
                    0.0,
                    active_target
                    + self._excluded_included_adjustment
                    - limit,
                )

            if excluded_peak_excess > 0:
                sensor_actual += excluded_peak_excess
                self._capacity_protection_active = True
                self._capacity_protection_status.update({
                    "active": True, "avg_soc": round(avg_soc, 1),
                    "estimated_house_load": round(estimated_house_load),
                    "action": "shaving_excluded",
                    "excluded_peak_excess": round(excluded_peak_excess),
                    "original_target": original_target,
                    "adjusted_target": active_target,
                })
                _LOGGER.info(
                    "Peak shaving for excluded devices ACTIVE: excluded=%.0fW, "
                    "excess=%.0fW, limit=%dW",
                    self._excluded_included_adjustment,
                    excluded_peak_excess,
                    limit,
                )
            else:
                self._capacity_protection_active = False
                self._capacity_protection_status.update({
                    "active": False, "avg_soc": round(avg_soc, 1),
                    "estimated_house_load": None,
                    "action": "idle",
                    "excluded_peak_excess": 0,
                    "original_target": original_target,
                    "adjusted_target": active_target,
                })

        # Always keep thresholds up to date
        self._capacity_protection_status["soc_threshold"] = self.capacity_protection_soc_threshold
        # FORK: SOC at which a running protection ends (threshold + hysteresis)
        self._capacity_protection_status["soc_release"] = (
            self.capacity_protection_soc_threshold
            + (getattr(self, "capacity_protection_soc_hysteresis", 0) or 0)
        )
        self._capacity_protection_status["peak_limit"] = limit
        self._capacity_protection_status["grid_source"] = (  # FORK: grid view (docs/fork/02-architektur.md)
            "physical" if physical_actual is not None else "control"
        )
        self._capacity_protection_status["excluded_devices_enabled"] = (
            self.capacity_protection_excluded_devices
        )
        return active_target, sensor_actual

    def _apply_icp_excluded_protection(
        self, sensor_filtered: float, sensor_actual: float, active_target: float
    ) -> float:
        """Cover excluded-device load that would exceed contracted power.

        This is a safety measure, not an economic one: the breaker sees the
        physical meter while excluded devices are hidden from the PD controller
        by design. Scope is excluded devices only (v1 of
        docs/plans/proteccion-potencia-contratada-descarga.md).
        """
        self._icp_excluded_protection_w = 0.0
        if self.max_contracted_power <= 0 or self._excluded_included_adjustment <= 0:
            return sensor_actual

        hidden = sensor_filtered - sensor_actual
        if hidden <= 0:
            return sensor_actual

        # Clamp to the still-hidden excluded share so prior add-backs are not counted twice.
        excess = min(
            max(0.0, active_target + hidden - self.max_contracted_power),
            hidden,
            self._excluded_included_adjustment,
        )
        self._icp_excluded_protection_w = excess
        if excess > 0:
            sensor_actual += excess
            _LOGGER.info(
                "ICP protection for excluded devices ACTIVE: excluded=%.0fW, "
                "excess=%.0fW, contracted=%.0fW",
                self._excluded_included_adjustment,
                excess,
                self.max_contracted_power,
            )

        return sensor_actual

    def _apply_capacity_protection_discharge_block(self, new_power):
        """FORK: below the SOC threshold capacity protection allows a discharge
        only for shaving (incl. the peak exit hold and ICP excluded-device
        protection). Run after the mixed-fleet guards and again after the
        shut-off dwell, so no stage downstream lifts it to a minimum discharge.
        """
        if (
            new_power < 0
            and getattr(self, "_capacity_protection_block_discharge", False)
            and not ChargeDischargeController._guard_peak_bypass(self)
        ):
            _LOGGER.debug(
                "Capacity Protection: below SOC threshold without shaving - "
                "discharge %.0fW blocked", new_power,
            )
            cycle_trace.put(self, p_cp=0.0)
            return 0
        return new_power

    def _capacity_protection_soc_below(self, avg_soc: float) -> bool:
        """FORK: SOC test of capacity protection with a release hysteresis.

        Protection starts below the configured threshold and, once started,
        ends only at threshold + hysteresis. Without it the SOC hovering at
        the threshold alternated discharge (at 40 %) and stop (at 39 %),
        6x in 3 h on 21.11.2026.
        """
        threshold = self.capacity_protection_soc_threshold
        if avg_soc < threshold:
            return True
        try:
            hysteresis = max(0.0, float(getattr(self, "capacity_protection_soc_hysteresis", 0) or 0))
        except (TypeError, ValueError):
            hysteresis = 0.0
        return (
            getattr(self, "_capacity_protection_soc_latched", False)
            and avg_soc < threshold + hysteresis
        )

    def _is_capacity_protection_soc_limited(self) -> bool:
        """Return True when peak shaving should be active based on current SOC."""
        if not self.capacity_protection_enabled:
            return False
        # Same pool as _apply_capacity_protection: a non-responsive battery's
        # last SOC must not keep this "still holding?" check False (#803).
        coordinators_with_data = self._capacity_protection_coordinators()
        if not coordinators_with_data:
            return False
        avg_soc = (
            sum(c.data.get("battery_soc", 0) for c in coordinators_with_data)
            / len(coordinators_with_data)
        )
        return ChargeDischargeController._capacity_protection_soc_below(self, avg_soc)

    def reset_pid_state(self):
        """Manually reset PID controller state. Useful when system is unstable."""
        _LOGGER.warning("PID: MANUAL RESET requested - clearing all PID state variables")
        _LOGGER.info("PID: Previous state - integral=%.1fW (%.1f%%), previous_error=%.1fW, sign_changes=%d",
                    self.error_integral, 
                    (abs(self.error_integral) / max(self.max_charge_capacity, self.max_discharge_capacity)) * 100,
                    self.previous_error, self.sign_changes)
        
        self.error_integral = 0.0
        self.previous_error = 0.0
        self.sign_changes = 0
        self.last_error_sign = 0
        self.last_output_sign = 0
        self.previous_power = 0
        self._grid_filter_ema = None
        self.first_execution = True  # Force re-initialization on next cycle
        
        _LOGGER.info("PID: State reset complete - system will re-initialize on next control cycle")

    def _log_power_command_plan(
        self,
        *,
        phase: str,
        grid_w: float,
        target_w: float,
        previous_power_w: float,
        requested_power_w: float,
        is_charging: bool,
        available_batteries: list,
        selected_batteries: list,
        power_allocation: dict,
        operation_restricted: bool = False,
    ) -> None:
        """Log one compact control decision before per-battery writes."""
        mode = "charge" if is_charging else ("discharge" if requested_power_w < 0 else "idle")
        selected_names = [coordinator.name for coordinator in selected_batteries]
        allocation = {
            coordinator.name: int(power)
            for coordinator, power in power_allocation.items()
        }
        charge_blocks = self._format_blockers_for_log(self.get_charge_blockers())
        discharge_blocks = self._format_blockers_for_log(self.get_discharge_blockers())
        setpoints = self._format_setpoint_summary_for_log()

        _LOGGER.debug(
            "Power plan [%s]: mode=%s grid=%.1fW target=%.1fW error=%.1fW "
            "prev=%.1fW request=%.1fW allocated=%dW available=%d selected=%s "
            "allocation=%s restricted=%s charge_blocks=%s discharge_blocks=%s setpoints=%s",
            phase,
            mode,
            grid_w,
            target_w,
            grid_w - target_w,
            previous_power_w,
            requested_power_w,
            sum(allocation.values()),
            len(available_batteries),
            selected_names,
            allocation,
            operation_restricted,
            charge_blocks,
            discharge_blocks,
            setpoints,
        )

    def _log_low_power_delivery(
        self,
        coordinator: MarstekVenusDataUpdateCoordinator,
        *,
        command: str,
        commanded_power: float,
        actual_power: float,
    ) -> None:
        """Log a compact diagnostic when ACK succeeds but delivered power is low."""
        data = coordinator.data or {}
        actual_abs = abs(actual_power)
        threshold = max(25.0, commanded_power * 0.10)

        if commanded_power < 100 or actual_abs >= threshold:
            return

        _LOGGER.debug(
            "[%s] Power delivery low: command=%s commanded=%dW actual=%dW "
            "threshold=%.0fW soc=%s%% min_soc=%d%% max_soc=%d%% inverter=%s",
            coordinator.name,
            command,
            int(commanded_power),
            actual_power,
            threshold,
            data.get("battery_soc"),
            coordinator.min_soc,
            coordinator.max_soc,
            data.get("inverter_state"),
        )

    async def _apply_software_manual_setpoints(self, global_mode: bool = True) -> None:
        """Re-assert a manual battery's Charge/Discharge intent while global or
        individual manual mode is active.

        Marstek batteries are driven by the user's own register writes; they are
        only re-asserted when the polled direction no longer matches the stored
        intent (a V150 v3 drops forced mode to None/0 W during a Modbus stall,
        issue #477).
        """
        for coordinator in self.coordinators:
            individual_mode = bool(
                getattr(coordinator, CONF_BATTERY_MANUAL_MODE_ENABLED, False)
            )
            if not global_mode and not individual_mode:
                continue
            await self._reassert_register_manual_intent(coordinator)

    @staticmethod
    async def _reassert_register_manual_intent(coordinator) -> None:
        """Rewrite a register battery's manual Charge/Discharge if it slipped.

        Only the direction is compared, not the watts: apply_power clamps to the
        live ceiling, so a watt comparison could rewrite forever. Idle intent is
        never asserted, and a battery the user handed back to its app (RS485 off)
        or that is unreachable is left alone.
        """
        mode = coordinator.manual_force_mode
        if mode == "Charge":
            intended = int(coordinator.manual_set_charge_power)
        elif mode == "Discharge":
            intended = -int(coordinator.manual_set_discharge_power)
        else:
            return
        if (
            intended == 0
            or not coordinator.is_available
            or coordinator.rs485_user_disabled
        ):
            return
        current = coordinator.driver.net_power_from_data(coordinator.data or {})
        if current is None or (current != 0 and (current > 0) == (intended > 0)):
            return
        # A battery that keeps refusing must not hog the v3 single TCP slot.
        now = time.monotonic()
        if now - getattr(coordinator, "_manual_reassert_ts", 0.0) < 30:
            return
        coordinator._manual_reassert_ts = now
        _LOGGER.warning(
            "[%s] Manual %s %dW lost on the battery (reads %dW) - re-asserting",
            coordinator.name, mode, abs(intended), current,
        )
        await coordinator.apply_power(intended)

    async def _set_battery_power(
        self,
        coordinator: MarstekVenusDataUpdateCoordinator,
        charge_power: float,
        discharge_power: float,
        ignore_charge_blockers: set[str] | None = None,
        ignore_discharge_blockers: set[str] | None = None,
        bypass_blockers: bool = False,
        force_write: bool = False,
        preserve_non_responsive_episode: bool = False,
        owner: str = "automatic",
    ) -> bool:
        """Set charge/discharge power for a single battery with ACK verification.

        ``force_write`` bypasses the bus-load skip-write so the command is always
        written, even when the battery's set-points already match — used by the
        non-responsive recovery to re-pin a battery that has slipped its control.

        ``preserve_non_responsive_episode`` marks the recovery path's internal
        standby write. It must not turn the following discharge command into a
        new episode that clears the existing failure count and wake budget.

        Returns True if command was acknowledged, False otherwise.
        """
        trace_src = cycle_trace.caller()  # FORK: trace - who asked for this write
        if owner == "automatic" and ChargeDischargeController._is_battery_manual_owned(
            coordinator
        ):
            _LOGGER.debug(
                "[%s] Skipping automatic power write - individual manual mode owns this battery",
                getattr(coordinator, "name", coordinator),
            )
            return False

        # Skip if battery is unreachable
        if not coordinator.is_available:
            _LOGGER.debug(
                "[%s] Skipping power write - battery unreachable (failures: %d)",
                coordinator.name, coordinator._consecutive_failures
            )
            return False

        # Skip if backup function is active (battery manages itself autonomously)
        if self._is_backup_function_active(coordinator):
            _LOGGER.debug(
                "[%s] Skipping power write - backup function is active",
                coordinator.name
            )
            return False

        # Skip if the user disabled RS485 control (battery driven by the official
        # app / its own logic — must stay out of all PD power writes).
        if coordinator.rs485_user_disabled:
            _LOGGER.debug(
                "[%s] Skipping power write - RS485 control disabled by user",
                coordinator.name
            )
            return False

        # Skip if a manual time slot already commanded this coord this cycle.
        if owner == "automatic" and self._is_manual_slot_owned(coordinator):
            _LOGGER.debug(
                "[%s] Skipping power write - manual time slot owns this battery",
                coordinator.name
            )
            return False

        # Enforce the live per-battery ceilings at the final controller write
        # boundary too. Most automatic paths are already allocated below these
        # limits, but software-manual and recovery paths deliberately bypass
        # normal blockers and can otherwise carry an old/persisted setpoint.
        try:
            charge_limit = max(
                0,
                int(
                    getattr(
                        coordinator,
                        "effective_max_charge_power",
                        coordinator.max_charge_power,
                    )
                ),
            )
            discharge_limit = max(
                0,
                int(
                    getattr(
                        coordinator,
                        "effective_max_discharge_power",
                        coordinator.max_discharge_power,
                    )
                ),
            )
            original_charge_power = charge_power
            original_discharge_power = discharge_power
            charge_power = min(charge_power, charge_limit)
            discharge_power = min(discharge_power, discharge_limit)
            if (
                charge_power != original_charge_power
                or discharge_power != original_discharge_power
            ):
                _LOGGER.debug(
                    "[%s] Power command capped to configured limits: charge=%.0fW "
                    "discharge=%.0fW (limits: %dW/%dW)",
                    coordinator.name,
                    charge_power,
                    discharge_power,
                    charge_limit,
                    discharge_limit,
                )
        except (AttributeError, TypeError, ValueError):
            # Lightweight third-party/test coordinators may not expose the
            # optional live limits; the driver still enforces its hardware cap.
            pass

        if bypass_blockers:
            charge_blockers = {}
            discharge_blockers = {}
        else:
            if charge_power > 0:
                charge_blockers = self.get_charge_blockers(coordinator)
                if ignore_charge_blockers:
                    charge_blockers = {
                        source: block
                        for source, block in charge_blockers.items()
                        if source not in ignore_charge_blockers
                    }
            else:
                charge_blockers = {}

            if discharge_power > 0:
                discharge_blockers = self.get_discharge_blockers(coordinator)
                if ignore_discharge_blockers:
                    discharge_blockers = {
                        source: block
                        for source, block in discharge_blockers.items()
                        if source not in ignore_discharge_blockers
                    }
            else:
                discharge_blockers = {}

        if charge_power > 0 and charge_blockers:
            _LOGGER.debug(
                "[%s] Charge command suppressed by blockers: %s",
                coordinator.name,
                ", ".join(charge_blockers.keys()),
            )
            charge_power = 0

        if discharge_power > 0 and discharge_blockers:
            _LOGGER.debug(
                "[%s] Discharge command suppressed by blockers: %s",
                coordinator.name,
                ", ".join(discharge_blockers.keys()),
            )
            discharge_power = 0

        # Clear any legacy balance hold that may have been restored from storage.
        if not bypass_blockers and coordinator.balance_hold and discharge_power > 0:
            _LOGGER.debug("[%s] Legacy balance hold active - discharge suppressed", coordinator.name)
            discharge_power = 0

        # Last automatic guard: active-balance, phase-safety replays and
        # other specialized routes all converge here.  The normal distributor
        # registers its full allocation as a plan, so this check cannot consume
        # the phase budget twice; direct automatic commands are capped against
        # the other batteries' current orders.
        phase_limiter = getattr(self, "_phase_power_limiter", None)
        if owner == "automatic" and phase_limiter is not None and phase_limiter.enabled:
            charge_power, discharge_power = phase_limiter.limit_single_command(
                coordinator,
                charge_power,
                discharge_power,
            )

        # Determine expected force mode (used in log messages below)
        if charge_power > 0:
            expected_force_mode = 1  # Charge
        elif discharge_power > 0:
            expected_force_mode = 2  # Discharge
        else:
            expected_force_mode = 0  # None

        # Translate the control decision into one signed net power for the
        # brand-agnostic driver: +charge / -discharge / 0 = idle. charge_power and
        # discharge_power are mutually exclusive here, so the sign maps 1:1 to
        # expected_force_mode.
        if charge_power > 0:
            net_power = int(charge_power)
        elif discharge_power > 0:
            net_power = -int(discharge_power)
        else:
            net_power = 0

        # Engage-grace bookkeeping: stamp the moment either commanded direction
        # starts so non-delivery detection below can give a slow inverter time to
        # engage before judging it. Done before the skip-write short-circuit so
        # the flip is seen even on a cycle that skips the write, and the tracker is
        # reset so a stale count from a prior session cannot carry over.
        net_sign = 1 if net_power > 0 else -1 if net_power < 0 else 0
        if (
            not preserve_non_responsive_episode
            and net_sign == 1
            and self._last_commanded_net_sign.get(coordinator) != 1
        ):
            self._charge_engage_started[coordinator] = dt_util.utcnow()
            # A stale taper timestamp from a prior charge session must not
            # survive into this one: a battery that tapered near 100% SOC,
            # then idled/discharged for a while (still reporting SOC >= the
            # taper floor) before charging again would otherwise walk straight
            # into "past the grace" on the very first low-power reading of the
            # new session.
            self._high_soc_taper_started.pop(coordinator, None)
            self._non_responsive.clear(coordinator, delivering=False)
        if (
            not preserve_non_responsive_episode
            and net_sign == -1
            and self._last_commanded_net_sign.get(coordinator) != -1
        ):
            self._discharge_engage_started[coordinator] = dt_util.utcnow()
            self._non_responsive.clear(coordinator, delivering=False)
            coordinator._low_soc_refusal_started = None
            coordinator._low_soc_standby_wake_done = False
        # A large step up within the same direction restarts only its engage
        # clock: the battery was following the smaller command and needs time to
        # ramp. The non-responsive episode, the taper clock and the low-SOC
        # refusal clock carry on, so a stall that outlasts the grace still counts.
        if (
            not preserve_non_responsive_episode
            and net_sign != 0
            and self._last_commanded_net_sign.get(coordinator) == net_sign
        ):
            is_charge = net_sign == 1
            previous_command = getattr(
                coordinator,
                "commanded_charge_power" if is_charge else "commanded_discharge_power",
                0,
            ) or 0
            previous_power = (coordinator.data or {}).get("battery_power")
            if (
                previous_command > 0
                and abs(net_power) >= ENGAGE_GRACE_STEP_FACTOR * previous_command
                and previous_power is not None
                and _delivered_toward(
                    coordinator.data, float(previous_power), is_charge=is_charge
                ) >= 0.10 * previous_command
            ):
                engage_times = (
                    self._charge_engage_started
                    if is_charge
                    else self._discharge_engage_started
                )
                engage_times[coordinator] = dt_util.utcnow()
                _LOGGER.debug(
                    "[%s] %s command stepped up %dW -> %dW — restarting the "
                    "engage grace",
                    coordinator.name, "Charge" if is_charge else "Discharge",
                    previous_command, abs(net_power),
                )
        # Mirror stamp for the opposite transition: a flip from a move into idle
        # starts the ramp-down grace for the idle-runaway judgment below. A
        # battery idle from the start (no prior commanded move) gets no grace —
        # there is no ramp-down to wait out, and a genuine runaway found at
        # startup (the original #434 case) should trip immediately.
        if (
            not preserve_non_responsive_episode
            and net_sign == 0
            and self._last_commanded_net_sign.get(coordinator, 0) != 0
        ):
            self._idle_commanded_started[coordinator] = dt_util.utcnow()
        if not preserve_non_responsive_episode:
            self._last_commanded_net_sign[coordinator] = net_sign
        if net_sign != 0:
            # Commanding a move ends any idle-runaway episode.
            self._idle_runaway_handled.pop(coordinator, None)

        # Record the live commanded setpoint so the manual sliders / force_mode
        # select can mirror it (parity with the Marstek register entities).
        # Done before the skip-write short-circuit so it tracks intent even when
        # the battery is already in the commanded state.
        coordinator.commanded_charge_power = net_power if net_power > 0 else 0
        coordinator.commanded_discharge_power = -net_power if net_power < 0 else 0

        # Bus-load reduction: skip the atomic write+readback when the battery is
        # already in the commanded state. coordinator.driver.net_power_from_data()
        # derives the current net from brand-native telemetry keys (Marstek:
        # force_mode + set_charge/discharge_power), which coordinator.data keeps fresh via polling and
        # write readbacks. External writers and BMS reverts self-correct on the
        # next poll.
        #
        # For a move command we additionally require the battery to actually be
        # delivering in the requested direction (polled battery_power within the
        # same 10% tolerance the non-responsive tracker uses). If a battery silently
        # stops while its set-points still match (the v3 non-responsive failure
        # mode), delivery drops and we fall through to a real write so the tracker
        # keeps seeing it.
        data = coordinator.data or {}
        readback_latency_s = getattr(
            coordinator.capabilities, "readback_latency_s", None
        )
        if readback_latency_s is None:
            readback_latency_s = coordinator.capabilities.actuator_latency_s
        hot_path_readback = (
            readback_latency_s <= HOT_PATH_READBACK_MAX_LATENCY_S
        )
        current_net = coordinator.driver.net_power_from_data(data)
        if not force_write and current_net is not None and current_net == net_power:
            skip_write = True
            if net_power == 0:
                # Commanded idle but the battery is actually discharging means it
                # has slipped out of RS485 forced mode into its own internal logic
                # (a v3 reverts to its app mode and can export to grid this way —
                # issue #434). The matching standby set-points are not trustworthy:
                # fall through to a real standby write so the battery is pinned back
                # to idle instead of running free.
                #
                # Only a *discharge* (export) is a runaway. Charging while idle is
                # harmless — on a DC-coupled vA/vD the battery_power register lumps
                # in the DC PV feeding the bus (see BATTERY_CELL_POWER_SENSOR_
                # DEFINITIONS), so a unit resting at idle while absorbing its own
                # solar reads positive; forcing standby there would dump that PV to
                # grid. Sign: + charge / - discharge.
                batt_power = data.get("battery_power")
                # Ramp-down grace: right after a discharge→idle flip the
                # set-points already read standby while battery_power telemetry
                # still shows the old discharge (actuator settle + poll grain).
                # Judging runaway there re-asserts RS485 on every ordinary
                # transition — suppress until the grace expires.
                idle_cmd_at = self._idle_commanded_started.get(coordinator)
                in_rampdown_grace = (
                    idle_cmd_at is not None
                    and (dt_util.utcnow() - idle_cmd_at).total_seconds()
                    < IDLE_RUNAWAY_GRACE_S
                )
                if (
                    not in_rampdown_grace
                    and batt_power is not None
                    and float(batt_power) <= -IDLE_RUNAWAY_POWER_W
                ):
                    skip_write = False
                    # Wake/re-assert only once per runaway episode. A v3 that
                    # dropped forced mode ignores register writes over the live
                    # socket, so re-asserting every ~2 s cycle just floods the log
                    # without recovering — _attempt_wake escalates to a fresh
                    # reconnect (restart-equivalent) if the re-assert doesn't take,
                    # the same recovery the discharge non-delivery path uses. Later
                    # cycles keep pinning standby via the fall-through write below.
                    if (
                        coordinator.capabilities.has_rs485_control
                        and not coordinator.rs485_user_disabled
                        and not self._idle_runaway_handled.get(coordinator, False)
                    ):
                        self._idle_runaway_handled[coordinator] = True
                        _LOGGER.warning(
                            "[%s] Commanded idle but discharging %.0fW — re-asserting "
                            "RS485 control and forcing standby",
                            coordinator.name, abs(float(batt_power)),
                        )
                        await self._attempt_wake(coordinator)
                        return True
                else:
                    # Back at genuine idle — end the episode so a future runaway
                    # re-arms the wake.
                    self._idle_runaway_handled.pop(coordinator, None)
            elif net_power < 0 and abs(net_power) >= 100:
                batt_power = data.get("battery_power")
                skip_write = (
                    batt_power is not None
                    and _delivered_toward(data, float(batt_power), is_charge=False)
                    >= 0.10 * abs(net_power)
                )
                # Slow actuators never read back per-write, so the
                # ACK-path non-delivery detection further down never runs for them.
                # This poll-time judgment on the freshly polled battery_power is the
                # only place a silently stalled registerless battery in a pool
                # surfaces — feed the tracker here so it is EXCLUDED, not just
                # re-commanded forever (the write below still re-asserts as a nudge).
                # A delivering (skip_write) cycle must reach it too: it is the only
                # thing that clears the tracker, so without it isolated 0 W samples
                # pile up across a long steady run into an exclusion, and an
                # excluded battery that is delivering stays excluded.
                if batt_power is not None and (skip_write or not hot_path_readback):
                    await self._check_non_delivery(
                        coordinator, abs(net_power), float(batt_power), attempt=0,
                        direction="discharge",
                    )
            elif net_power > 0 and net_power >= 100:
                batt_power = data.get("battery_power")
                skip_write = (
                    batt_power is not None
                    and _delivered_toward(data, float(batt_power), is_charge=True)
                    >= 0.10 * net_power
                )
                if batt_power is not None and (skip_write or not hot_path_readback):
                    await self._check_non_delivery(
                        coordinator, net_power, float(batt_power), attempt=0,
                        direction="charge",
                    )
            if skip_write:
                # Polled set-points match the commanded values exactly — this is
                # the deferred settled verification a tolerance-only ACK left
                # pending (see the readback branch below).
                coordinator._ack_inexact_streak = 0
                _LOGGER.debug(
                    "[%s] Power write skipped - already at force=%d charge=%dW "
                    "discharge=%dW",
                    coordinator.name, expected_force_mode,
                    int(charge_power), int(discharge_power),
                )
                return True

        # A real write that changes the commanded power is about to be issued below:
        # arm the transient burst poll so the delivered-power reading refreshes
        # faster while the actuator ramps to the new setpoint (see
        # start_burst_poll / group_scan_interval_s in infra/coordinator.py).
        if current_net != net_power:
            coordinator.start_burst_poll()

        # Bus-load / latency reduction: only read back (verify ACK + run non-delivery
        # detection) every Nth real write, and never on the hot path for a slow
        # actuator — its readback needs a multi-second settle that
        # would block the shared control loop while it holds the lock. Option-B skips
        # above don't reach here, so the cadence is measured in actual writes;
        # write-only cycles skip the readback and its settle delay. HTTP drivers
        # therefore never run ACK-based non-delivery detection here; the skip-write
        # block above runs the same judgment at poll time for slow actuators so a
        # stalled registerless battery is still excluded from the pool.
        write_count = getattr(coordinator, "_pd_write_count", 0)
        coordinator._pd_write_count = write_count + 1
        read_back = (
            (write_count % PD_READBACK_EVERY_N_WRITES) == 0
            and hot_path_readback
        )

        # Attempt the setpoint + verify, with one retry on failure.
        # last_fail_reason carries the most specific failure category seen across
        # both attempts so the non-responsive tracker can surface *why*.
        last_fail_reason: str | None = None
        cycle_trace.event(  # FORK: trace - every real write, inside a cycle or not
            "WRITE", self, bat=coordinator.name, net=net_power, prev_net=current_net,
            force=expected_force_mode, readback=read_back, src=trace_src, owner=owner,
        )
        for attempt in range(2):
            result = await coordinator.apply_power(net_power, read_back=read_back)

            if not result.ok:
                last_fail_reason = result.failure_reason or "comm_failure"
                if not coordinator._is_shutting_down:
                    _LOGGER.warning(
                        "[%s] Power write/feedback failed (attempt %d/2, reason=%s)",
                        coordinator.name, attempt + 1, last_fail_reason
                    )
                continue

            # Write-only cycle: no readback this cycle, so no ACK check or
            # non-delivery detection. The write itself succeeded.
            if not read_back:
                _LOGGER.debug(
                    "[%s] Power write (no readback this cycle): force=%d charge=%dW "
                    "discharge=%dW",
                    coordinator.name, expected_force_mode,
                    int(charge_power), int(discharge_power),
                )
                return True

            if result.confirmed:
                # Deferred exact verification: a tolerance-only ACK (exact=False)
                # means the echo was still ramping when read. Settled state is
                # proven by an exact echo here or by the poll comparison in the
                # skip-write block above (both reset the streak). A long streak
                # of tolerance-only ACKs with neither means the write chain lags
                # beyond what the settle window ever covers — surface it once.
                if result.exact:
                    coordinator._ack_inexact_streak = 0
                else:
                    streak = getattr(coordinator, "_ack_inexact_streak", 0) + 1
                    coordinator._ack_inexact_streak = streak
                    if streak == ACK_INEXACT_STREAK_WARN:
                        _LOGGER.warning(
                            "[%s] %d consecutive power ACKs confirmed only within "
                            "tolerance — the set-point echo keeps lagging the "
                            "write. Commands are being delivered, but check the "
                            "RS485 bridge serial settings (baud, packing/gap time).",
                            coordinator.name, streak,
                        )
                actual_power = result.battery_power_w
                _LOGGER.debug(
                    "[%s] Power ACK: force=%d charge=%dW discharge=%dW battery=%sW",
                    coordinator.name,
                    expected_force_mode,
                    int(charge_power),
                    int(discharge_power),
                    actual_power,
                )
                if charge_power > 0 and actual_power is not None:
                    self._log_low_power_delivery(
                        coordinator,
                        command="charge",
                        commanded_power=charge_power,
                        actual_power=actual_power,
                    )
                elif discharge_power > 0 and actual_power is not None:
                    self._log_low_power_delivery(
                        coordinator,
                        command="discharge",
                        commanded_power=discharge_power,
                        actual_power=actual_power,
                    )
                # Detect non-responsive battery: ACK ok but not delivering power in
                # the commanded direction. Register drivers reach this only on a
                # readback cycle; slow actuators run the same judgment at poll time
                # (see skip-write block). Skip when delivered power is unknown.
                if (
                    max(charge_power, discharge_power) >= 100
                    and not (charge_power > 0 and discharge_power > 0)
                    and actual_power is not None
                ):
                    direction = "charge" if charge_power > 0 else "discharge"
                    await self._check_non_delivery(
                        coordinator,
                        max(charge_power, discharge_power),
                        actual_power,
                        attempt=attempt,
                        direction=direction,
                    )
                return True

            # Readback happened but the set-points did not match (mismatch), or the
            # confirmation read never followed (feedback_timeout). Both retryable.
            last_fail_reason = result.failure_reason or "ack_mismatch"
            if attempt == 0:
                # On a driver whose readback lags the write (the device echoes the
                # previous limit for seconds), a first-attempt mismatch is expected
                # echo/engage latency that the retry resolves — log it at debug, not
                # warning, so it does not read as a fault. Register drivers, whose
                # readback is immediate, keep the warning.
                _log = (
                    _LOGGER.warning
                    if coordinator.driver.capabilities.setpoint_confirm_reliable
                    else _LOGGER.debug
                )
                if result.failure_reason == "feedback_timeout":
                    _log(
                        "[%s] Power feedback read failed (attempt 1/2), retrying.",
                        coordinator.name,
                    )
                else:
                    echo = result.applied or {}
                    _log(
                        "[%s] Power command not ACK'd (attempt 1/2), retrying. "
                        "requested(force=%d charge=%dW discharge=%dW) "
                        "readback(force=%s charge=%sW discharge=%sW battery=%sW)",
                        coordinator.name,
                        expected_force_mode,
                        int(charge_power),
                        int(discharge_power),
                        echo.get("force_mode"),
                        echo.get("set_charge_power"),
                        echo.get("set_discharge_power"),
                        echo.get("battery_power"),
                    )

        # Both attempts failed at the Modbus/ACK level — feed the tracker so the
        # diagnostic sensor can report the specific reason (and so repeated comms
        # failures eventually exclude the battery, same as non-delivery).
        if not coordinator._is_shutting_down:
            self._non_responsive.record_comm_failure(
                coordinator, last_fail_reason or "comm_failure"
            )
            _LOGGER.error(
                "[%s] Power command failed after 2 attempts (reason=%s). "
                "Battery may not have received command.",
                coordinator.name, last_fail_reason or "comm_failure"
            )
        return False

    async def _check_non_delivery(
        self,
        coordinator,
        commanded_power,
        actual_power,
        *,
        attempt,
        direction: str = "discharge",
    ) -> None:
        """Judge a move command that delivers ~0 W and feed the tracker.

        Applies direction engage-grace and legitimate BMS cutoff exemptions,
        then records a non-delivery (excluding the battery once the tracker's
        threshold is crossed) or clears it when power is flowing.

        Called from the per-write readback path (register drivers, fresh ACK
        power) and, for slow actuators whose per-write readback is skipped, from
        the poll-time delivery check using the last polled battery_power — so a
        silently stalled registerless battery in a pool is excluded, not
        re-commanded forever.
        """
        is_charge = direction == "charge"
        delivered_power = _delivered_toward(
            coordinator.data or {}, actual_power, is_charge=is_charge
        )
        if delivered_power >= 0.10 * commanded_power:
            self._high_soc_taper_started.pop(coordinator, None)
            self._non_responsive.clear(coordinator)
            coordinator._low_soc_refusal_started = None
            coordinator._low_soc_standby_wake_done = False
            return
        engage_times = (
            self._charge_engage_started
            if is_charge
            else self._discharge_engage_started
        )
        engage_started = engage_times.get(coordinator)
        engage_grace_s = getattr(coordinator.capabilities, "engage_grace_s", None)
        if engage_grace_s is None:
            engage_grace_s = DISCHARGE_ENGAGE_GRACE_S
        within_engage_grace = (
            engage_started is not None
            and (dt_util.utcnow() - engage_started).total_seconds()
            < engage_grace_s
        )
        if within_engage_grace:
            # A slow inverter takes seconds to reverse into
            # discharge from charge/idle — up to ~20-30 s on a cold
            # charge→discharge transition — or to ramp after a large step up.
            # 0 W out this soon after the direction flip or step is engage
            # latency, not a fault; give it time before judging. A flip
            # already reset the tracker; a step leaves its count in place.
            _LOGGER.debug(
                "[%s] No %s delivered yet but within %ds engage "
                "grace — inverter still engaging, not a fault",
                coordinator.name, direction, engage_grace_s,
            )
            return
        if is_charge:
            weekly_mgr = getattr(self, "_weekly_charge_mgr", None)
            if (
                weekly_mgr is not None
                and weekly_mgr.is_battery_full(coordinator)
            ):
                _LOGGER.debug(
                    "[%s] No charge delivered because the BMS full-charge "
                    "cutoff is active — not a fault",
                    coordinator.name,
                )
                self._high_soc_taper_started.pop(coordinator, None)
                self._non_responsive.clear(coordinator)
                return
            # Top-of-charge tail: the last stretch before 100% often tapers hard
            # (CC/CV tail current) well before is_battery_full() above fires or
            # tick_bms_cutoff() confirms a cutoff (which needs power <= 10 W
            # *and* Standby). Observed on a hybrid inverter: 48-189 W of a
            # 7000 W command for ~15 minutes while SOC climbed 99% -> 100%,
            # inverter never in Standby - genuinely still charging, just at a
            # reduced tail current below the 10%-of-commanded bar above.
            # Bounded by HIGH_SOC_CHARGE_TAPER_GRACE_S: a battery that gets
            # physically stuck in this band rather than tapering to completion
            # still surfaces as a fault, just later.
            current_soc = coordinator.data.get("battery_soc", 0) if coordinator.data else 0
            if current_soc >= HIGH_SOC_CHARGE_TAPER_FLOOR:
                now = dt_util.utcnow()
                taper_started = self._high_soc_taper_started.setdefault(coordinator, now)
                taper_elapsed_s = (now - taper_started).total_seconds()
                if taper_elapsed_s < HIGH_SOC_CHARGE_TAPER_GRACE_S:
                    _LOGGER.debug(
                        "[%s] Low charge power (%.0fW of %.0fW commanded) at "
                        "SOC %.1f%% — top-of-charge taper (%.0fs of %ds "
                        "grace), not a fault",
                        coordinator.name, delivered_power, commanded_power,
                        current_soc, taper_elapsed_s, HIGH_SOC_CHARGE_TAPER_GRACE_S,
                    )
                    self._non_responsive.clear(coordinator)
                    return
                _LOGGER.debug(
                    "[%s] Still only %.0fW of %.0fW commanded after %.0fs at "
                    "SOC %.1f%% — past the top-of-charge taper grace, judging "
                    "as a fault",
                    coordinator.name, delivered_power, commanded_power,
                    taper_elapsed_s, current_soc,
                )
            else:
                self._high_soc_taper_started.pop(coordinator, None)
        # ACK'd but no power: separate a battery sitting in standby
        # (likely dropped RS485 control) from one that is awake but
        # still refusing.
        inv_state = coordinator.data.get("inverter_state") if coordinator.data else None
        try:
            is_standby = (
                inv_state is not None
                and int(inv_state) == NORMAL_BALANCE_RECAL_INVERTER_STANDBY
            )
        except (TypeError, ValueError):
            is_standby = False
        # Skip non-responsive recording when the BMS is legitimately
        # refusing discharge: either at/near the configured min-SOC, or
        # anywhere below the low-SOC protective floor where the BMS may
        # cut discharge on its own (e.g. a weak cell sagging under load)
        # even though the reported SOC is still above min_soc. 0W output
        # is then expected behaviour, not a fault. Low-SOC counterpart to
        # the high-SOC BMS-cutoff handling.
        current_soc = coordinator.data.get("battery_soc", 100) if coordinator.data else 100
        bms_cutoff_floor = max(coordinator.min_soc + 1, BMS_DISCHARGE_CUTOFF_SOC)
        if not is_charge and current_soc <= bms_cutoff_floor:
            _LOGGER.debug(
                "[%s] No discharge delivered but SOC=%.1f%% is in the BMS "
                "low-SOC cutoff range (min_soc=%d%%, floor=%d%%) — not a fault",
                coordinator.name, current_soc, coordinator.min_soc, bms_cutoff_floor,
            )
            # Comms and battery are fine, just protecting itself.
            self._non_responsive.clear(coordinator)
            # A battery parked in Standby may instead be stuck (a short AC
            # interruption leaves it there with RS485 still reading enabled,
            # #720), which looks the same as a cutoff. Give it the one-shot
            # standby wake once per refusal episode before judging it empty:
            # a real cutoff costs one reconnect, a stuck one is recovered.
            if is_standby and not getattr(
                coordinator, "_low_soc_standby_wake_done", False
            ):
                coordinator._low_soc_standby_wake_done = True
                coordinator._low_soc_refusal_started = dt_util.utcnow()
                await self._attempt_wake(coordinator, is_standby=True)
                return
            # Not a fault, but not a battery to keep asking either: a device
            # that stops above min_soc (its own cutoff set higher in the app, a
            # BMS stopping early) was otherwise commanded for hours while the
            # house imported (#408, #545). Once the refusal outlasts a pack
            # hand-off, treat the battery as empty at this SOC.
            # A clock older than twice the window was not kept running by
            # consecutive refusals (sub-100 W discharge is never judged, so a
            # delivery in between may not have reset it): start over.
            now = dt_util.utcnow()
            started = getattr(coordinator, "_low_soc_refusal_started", None)
            elapsed = (now - started).total_seconds() if started else None
            if elapsed is None or elapsed > 2 * LOW_SOC_REFUSAL_CONFIRM_S:
                coordinator._low_soc_refusal_started = now
            elif elapsed >= LOW_SOC_REFUSAL_CONFIRM_S:
                coordinator._low_soc_refusal_started = None
                coordinator._discharge_refused_soc = current_soc
                _LOGGER.info(
                    "[%s] Battery stopped delivering at SOC %.1f%% — out of the "
                    "discharge pool until it recovers to %.1f%%",
                    coordinator.name, current_soc,
                    current_soc + DISCHARGE_MIN_SOC_REENTRY_MARGIN,
                )
            return
        # Reaching the top voltage during the previous charge is not an exemption
        # here. The discharge engage grace above covers the legitimate transition
        # out of BMS-full standby; once it expires, a battery that still ACKs the
        # discharge set-point but remains in standby is genuinely not delivering
        # and must reach the wake/reconnect recovery path (issue #26).
        reason_prefix = "charge_" if is_charge else ""
        reason = (
            f"{reason_prefix}standby_no_delivery"
            if is_standby
            else f"{reason_prefix}non_delivery"
        )
        outcome = self._non_responsive.record_non_delivery(
            coordinator, commanded_power, delivered_power,
            reason=reason, retry_attempted=attempt > 0,
        )
        # First threshold-cross: a one-shot wake nudge (reconnect/re-assert), but
        # the tracker resets the fail counter instead of excluding — the battery
        # stays in the pool so the very next real PD cycle proves whether the
        # wake worked, instead of paying a 5-minute cooldown for nothing if it
        # did. Only a second consecutive threshold-cross in the same episode
        # (post-wake) actually excludes it, and that one gets no further wake
        # (no-op on drivers without RS485 control).
        if outcome == "wake":
            woke = await self._attempt_wake(coordinator, is_standby=is_standby)
            self._non_responsive.set_wake_attempted(coordinator, woke)

    async def _attempt_wake(self, coordinator, *, is_standby: bool = False) -> bool:
        """Re-assert RS485 control (or toggle it) on an unresponsive battery.

        A battery that ACKs power commands but delivers ~0 W has usually dropped its
        RS485 forced mode and reverted to its own internal logic (a v3 in internal
        logic can export to grid on its own — issue #434). The safe recovery for
        that case is to re-enable RS485 without a disable step and force a real
        standby write, since disabling first hands control to the internal logic
        we are trying to override, opening an export window.

        When the battery is already sitting in ``inverter_state == standby``
        (``is_standby``), that risk doesn't apply — it isn't running any internal
        logic to hand control to. A plain re-assert is a no-op if the BMS already
        believes RS485 is enabled and is simply stuck (exactly the reported case:
        RS485 reads enabled, yet discharge never engages). Users confirm only an
        HA restart or a manual command recovers it, and a restart's fix is the
        fresh TCP connection (see ``async_reconnect_fresh``), not a register
        toggle — so go straight to that restart-equivalent here rather than
        guessing with a toggle. Skipped when the user has disabled RS485 control.
        Returns True if the re-enable succeeded.
        """
        if ChargeDischargeController._is_battery_manual_owned(coordinator):
            return False
        if coordinator.rs485_user_disabled:
            return False
        if not coordinator.capabilities.has_rs485_control:
            return False
        if is_standby:
            _LOGGER.info(
                "[%s] Non-delivery (standby) — reconnecting fresh "
                "(restart-equivalent)", coordinator.name,
            )
            ok = await coordinator.async_reconnect_fresh()
            await self._set_battery_power(
                coordinator, 0, 0, bypass_blockers=True, force_write=True,
                preserve_non_responsive_episode=True,
            )
            return ok and getattr(
                coordinator, "_last_rs485_reenable_success", True
            ) is not False
        _LOGGER.info(
            "[%s] Non-delivery — re-asserting RS485 control + forcing standby",
            coordinator.name,
        )
        ok = await coordinator.set_rs485_control(True)
        # A v3 that dropped forced mode at the BMS full-charge cutoff ACKs this
        # write over the existing socket but ignores it (register still reads
        # disabled) — only a fresh TCP connection makes it accept control, which
        # is why an HA restart recovers the battery. If the re-assert didn't
        # take, do that restart-equivalent here. Read error (None) is left to the
        # read-failure health path; only a definitive "still disabled" escalates.
        reconnected = False
        if await coordinator.rs485_control_enabled() is False:
            _LOGGER.warning(
                "[%s] RS485 re-assert didn't take over the live connection — "
                "reconnecting fresh (restart-equivalent)", coordinator.name,
            )
            ok = await coordinator.async_reconnect_fresh()
            reconnected = True
        await self._set_battery_power(
            coordinator, 0, 0, bypass_blockers=True, force_write=True,
            preserve_non_responsive_episode=True,
        )
        if reconnected and getattr(
            coordinator, "_last_rs485_reenable_success", True
        ) is False:
            return False
        return ok

    async def _stop_all_batteries_for_block(self, direction: str) -> None:
        """Stop all battery commands after a global operation block becomes active."""
        _LOGGER.debug("ChargeDischargeController: stopping all batteries due to %s block", direction)
        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            await self._set_battery_power(coordinator, 0, 0)
        self.previous_power = 0
        self._active_discharge_batteries = []
        self._active_charge_batteries = []

    async def _stop_blocked_active_batteries(self) -> bool:
        """Stop batteries that were active before a per-battery block appeared."""
        stopped = False
        for coordinator in list(self._active_charge_batteries):
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            if self.is_charge_blocked(coordinator):
                await self._set_battery_power(coordinator, 0, 0)
                if coordinator in self._active_charge_batteries:
                    self._active_charge_batteries.remove(coordinator)
                stopped = True
        for coordinator in list(self._active_discharge_batteries):
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            if self.is_discharge_blocked(coordinator):
                await self._set_battery_power(coordinator, 0, 0)
                if coordinator in self._active_discharge_batteries:
                    self._active_discharge_batteries.remove(coordinator)
                stopped = True
        return stopped

    @staticmethod
    def _coordinator_delivered_power(coordinator):
        """Measured delivery for one battery in controller convention (+charge/-discharge).

        Marstek exposes ``ac_power`` (+discharge/-charge), so it is negated.
        A driver without an ``ac_power`` register may publish its AC port as
        ``DELIVERED_AC_POWER_KEY`` (+charge/-discharge). That comes
        before ``battery_power``, which is cell-side and includes the device's own
        DC PV: with sun it reads a charge the grid never sees, so the PD re-anchored
        to it and commanded grid charge while the house imported (#634).
        ``battery_power`` stays the last fallback so the controller never reads the
        battery as 0 W. Returns None when nothing is reported (e.g. after a restart).
        """
        data = getattr(coordinator, "data", None)
        if not data:
            return None
        ac = data.get("ac_power")
        if ac is not None:
            try:
                return -float(ac)
            except (TypeError, ValueError):
                return None
        delivered = data.get(DELIVERED_AC_POWER_KEY)
        if delivered is not None:
            try:
                return float(delivered)
            except (TypeError, ValueError):
                return None
        battery_power = data.get("battery_power")
        if battery_power is not None:
            try:
                return float(battery_power)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _manual_battery_commanded_power(coordinator) -> float:
        """Return a manual battery's signed setpoint as a fallback measurement."""
        mode = getattr(coordinator, "manual_force_mode", "None")
        if mode == "Charge":
            value = getattr(coordinator, "manual_set_charge_power", 0)
            if not value:
                value = getattr(coordinator, "commanded_charge_power", 0)
            sign = 1
        elif mode == "Discharge":
            value = getattr(coordinator, "manual_set_discharge_power", 0)
            if not value:
                value = getattr(coordinator, "commanded_discharge_power", 0)
            sign = -1
        else:
            charge = getattr(coordinator, "commanded_charge_power", 0) or 0
            discharge = getattr(coordinator, "commanded_discharge_power", 0) or 0
            if charge or discharge:
                try:
                    return float(charge) - float(discharge)
                except (TypeError, ValueError):
                    return 0.0

            data = getattr(coordinator, "data", None) or {}
            driver = getattr(coordinator, "driver", None)
            net_power_from_data = getattr(driver, "net_power_from_data", None)
            if callable(net_power_from_data):
                try:
                    value = net_power_from_data(data)
                except (TypeError, ValueError, KeyError):
                    value = None
                if value is not None:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return 0.0

            # Keep the fallback usable for lightweight/test coordinators and
            # for driver data received before the driver object is attached.
            try:
                force_mode = int(round(float(data.get("force_mode"))))
            except (TypeError, ValueError):
                force_mode = 0
            if force_mode == 1:
                value = data.get("set_charge_power", 0)
                sign = 1
            elif force_mode == 2:
                value = data.get("set_discharge_power", 0)
                sign = -1
            else:
                return 0.0

            try:
                return sign * max(0.0, float(value or 0))
            except (TypeError, ValueError):
                return 0.0

        try:
            return sign * max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0

    def _manual_battery_power_for_grid_feedback(self) -> float:
        """Return manual batteries' signed AC contribution to the grid meter.

        Prefer measured AC-side power, which excludes DC-coupled solar on drivers
        that expose it; fall back to the manual setpoint only when no delivered-
        power telemetry is available. The control loop uses this contribution
        conditionally: while automatic batteries are charging it remains visible
        so they can reduce their charge to keep the meter at zero; once automatic
        charging is idle, a manual grid charge is excluded so it cannot trigger an
        automatic discharge.
        """
        total = 0.0
        for coordinator in self.coordinators:
            if not ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            measured = ChargeDischargeController._coordinator_delivered_power(
                coordinator
            )
            power = (
                measured
                if measured is not None
                else ChargeDischargeController._manual_battery_commanded_power(
                    coordinator
                )
            )
            try:
                if power is not None and math.isfinite(float(power)):
                    total += float(power)
            except (TypeError, ValueError):
                continue
        return total

    def _measured_battery_power(self):
        """Aggregate measured automatic-battery power in controller convention.

        Controller convention is + charge / - discharge. Uses the AC-side power (what
        the grid meter sees, excludes DC PV on vA/vD) where available. Manual
        batteries are omitted because their output is not part of the automatic
        controller's command. Returns None if no automatic battery reports a value
        (e.g. right after a restart).
        """
        total = 0.0
        seen = False
        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            delivered = self._coordinator_delivered_power(coordinator)
            if delivered is None:
                continue
            total += delivered
            seen = True
        return total if seen else None

    def _delivered_power_reliable(self) -> bool:
        """False when any automatic battery's delivered-power telemetry lags reality (#522)."""
        return all(
            getattr(getattr(c, "capabilities", None), "delivered_power_reliable", True)
            for c in self.coordinators
            if not ChargeDischargeController._is_battery_manual_owned(c)
        )

    def _backcalc_is_saturated(self, is_charging: bool) -> bool:
        """Return True when the command shortfall is explained by real limits.

        Re-anchoring the incremental base to measured power is only correct when
        the batteries genuinely cannot deliver more — every active battery is
        blocked, at its power cap, or not reporting. If any active battery is
        unblocked and still has headroom below its own limit, the shortfall is
        most likely actuator ramp lag (slow MQTT/HTTP drivers ramp over seconds),
        and re-anchoring would starve the command before the device finishes
        ramping.
        """
        phase_limiter = getattr(self, "_phase_power_limiter", None)
        if phase_limiter is not None and getattr(phase_limiter, "enabled", False):
            # A phase envelope is a real actuator limit just like a battery's
            # own rail.  Treat an invalid phase as saturated too: that phase is
            # intentionally held at 0 W until telemetry recovers, otherwise the
            # incremental PD base would wind up behind the safety stop.
            try:
                snapshots = phase_limiter.all_snapshots()
            except (AttributeError, TypeError, ValueError):
                snapshots = {}
            for phase, snapshot in snapshots.items():
                phase_batteries = [
                    coordinator
                    for coordinator in self.coordinators
                    if (
                        getattr(coordinator, "phase", None) == phase
                        or getattr(coordinator, CONF_BATTERY_PHASE, None) == phase
                    ) and not ChargeDischargeController._is_battery_manual_owned(coordinator)
                ]
                if not phase_batteries:
                    continue
                if snapshot.get("reason") == "not_configured":
                    # An intentionally empty phase has no envelope to saturate;
                    # its batteries follow the normal controller limits.
                    continue
                if snapshot.get("degraded"):
                    return True
                budget_key = "charge_budget_w" if is_charging else "discharge_budget_w"
                budget = float(snapshot.get(budget_key) or 0)
                commanded = sum(
                    max(
                        0.0,
                        float(
                            getattr(
                                coordinator,
                                "commanded_charge_power"
                                if is_charging
                                else "commanded_discharge_power",
                                0,
                            )
                            or 0
                        ),
                    )
                    for coordinator in phase_batteries
                )
                if commanded >= budget - self.saturation_backcalc_threshold:
                    return True

        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            if not coordinator.data:
                continue
            blocked = (
                self.is_charge_blocked(coordinator)
                if is_charging
                else self.is_discharge_blocked(coordinator)
            )
            if blocked:
                continue
            limit = self._battery_power_limit(coordinator, is_charging)
            if limit <= 0:
                continue
            delivered_signed = self._coordinator_delivered_power(coordinator)
            if delivered_signed is None:
                # Unknown delivery: cannot prove saturation — assume ramp lag.
                return False
            delivered = delivered_signed if is_charging else -delivered_signed
            if delivered < limit - self.saturation_backcalc_threshold:
                return False
        return True

    def _resolve_home_consumption_sensor(self) -> Optional[str]:
        """Resolve & cache the derived Home Consumption entity_id by stable unique_id.

        Resolved lazily because the aggregate entity is created after the
        controller is constructed; retries each cycle until it appears, then
        caches. Used by ExternalLoads for PV-surplus accounting (#421/#415).
        """
        if not self.home_consumption_sensor:
            from homeassistant.helpers import entity_registry as er
            self.home_consumption_sensor = er.async_get(self.hass).async_get_entity_id(
                "sensor", DOMAIN, "marstek_venus_system_home_consumption"
            )
        return self.home_consumption_sensor

    def _filter_grid_sample(self, sensor_raw, elapsed_s):
        """Time-constant EMA on the grid sample (replaces the fixed 2-sample average).

        alpha = elapsed/(tau+elapsed) keeps the smoothing time constant regardless of
        the variable event-driven cadence. The first sample seeds the filter directly.
        elapsed_s == 0 (a stale recalculation, no new data) leaves the value unchanged;
        elapsed_s None (callers that don't track elapsed) falls back to the nominal dt.

        Adaptive collapse: the EMA is tuned to smooth sensor noise (tens of W), not
        to react to a genuine load step (a kettle/EV charger swinging kW). When the
        raw sample deviates from the current EMA by more than the step threshold,
        it is not noise — smoothing it would only add multi-second lag to a real
        step — so the EMA snaps straight to the raw value (alpha = 1) instead of
        blending.
        """
        if self._grid_filter_ema is None:
            self._grid_filter_ema = sensor_raw
        elif elapsed_s is None or elapsed_s > 0:
            step_threshold = max(3 * self.deadband, 200.0)
            if abs(sensor_raw - self._grid_filter_ema) > step_threshold:
                self._grid_filter_ema = sensor_raw
            else:
                dt = elapsed_s if (elapsed_s is not None and elapsed_s > 0) else self.dt
                alpha = dt / (self._grid_filter_tau + dt)
                self._grid_filter_ema += alpha * (sensor_raw - self._grid_filter_ema)
        return self._grid_filter_ema

    @callback
    def schedule_control_cycle(self, now=None):
        """Launch a control cycle as a config-entry background task.

        Timer and state-change trackers run their callbacks as HA-tracked
        tasks, and HA startup waits for tracked tasks before wrapping up. A
        cycle stuck in Modbus retries against a slow gateway would block the
        whole bootstrap ("Something is blocking Home Assistant..."), delaying
        every integration set up after this one. Background tasks are exempt
        from the startup gate and are still cancelled on entry unload.
        """
        if getattr(self, "_unloading", False):
            return
        coroutine = self.async_update_charge_discharge(now)
        create = getattr(self, "_create_entry_background_task", None)
        if callable(create):
            create(coroutine, "omnibattery_control_cycle")
        else:
            self.config_entry.async_create_background_task(
                self.hass, coroutine, "omnibattery_control_cycle"
            )

    def _create_entry_background_task(
        self, coroutine, name: str
    ) -> asyncio.Task | None:
        """Create and retain an entry-owned task until it has finished."""
        if getattr(self, "_unloading", False):
            close = getattr(coroutine, "close", None)
            if callable(close):
                close()
            return None
        create = getattr(self.config_entry, "async_create_background_task", None)
        task = None
        if callable(create):
            try:
                task = create(self.hass, coroutine, name)
            except TypeError:
                task = create(coroutine, name)
        else:
            create = getattr(self.hass, "async_create_task", None)
            if callable(create):
                try:
                    task = create(coroutine, name=name)
                except TypeError:
                    task = create(coroutine)
            else:
                try:
                    task = asyncio.get_running_loop().create_task(coroutine, name=name)
                except TypeError:
                    task = asyncio.get_running_loop().create_task(coroutine)
        if isinstance(task, asyncio.Task):
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return task
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()
        return None

    async def async_stop_background_tasks(self) -> None:
        """Stop entry-owned control tasks before hardware teardown."""
        self._unloading = True
        self._cancel_no_pd_debounced_run()
        current = asyncio.current_task()
        tasks = {
            task
            for task in self._background_tasks
            if task is not current and not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Entry-owned persistence tasks above are deliberately cancelled so an
        # old runtime cannot write after unload. Flush their managers directly
        # now, while this generation still owns the final coherent state.
        for manager in (self._weekly_charge_mgr,):
            flush = getattr(manager, "async_flush_state", None)
            if callable(flush):
                await flush()

    async def async_update_charge_discharge(self, now=None):
        """Run one control cycle, guarded against overlapping triggers.

        Invoked by both the periodic safety timer and the consumption-sensor
        state-change event. If a cycle is already running, the overlapping
        trigger is skipped: the in-flight cycle already reads the current state,
        so re-entering would only risk concurrent Modbus writes.
        """
        # FORK: relay guard (docs/fork/02-architektur.md)
        guard = getattr(self, "_relay_guard", None)
        if guard is not None:
            guard.observe(self.previous_power, time.monotonic())
            if (day := dt_util.now().date()) != self._relay_guard_day:
                self._relay_guard_day = day
                guard.reset_daily()
        if getattr(self, "_unloading", False):
            return
        # No-PD command delay (debounce): on a sensor event, defer the cycle by
        # the configured delay and collapse any further events in that window into
        # the single deferred run, which reads the latest sensor value at fire time.
        # Replaces the rate-limit throttle below while active. The periodic safety
        # timer (now is a datetime) is never deferred.
        if now is None and self.no_pd_mode_enabled and self._no_pd_command_delay > 0:
            self._schedule_no_pd_debounced_run()
            return
        # Event-driven rate limit: drop a consumption-sensor trigger that lands
        # within _min_cycle_interval_s of the last cycle, so a fast-publishing
        # meter can't flood slow Modbus bridges (e.g. Elfin EW11) with write
        # bursts. The periodic safety timer (now is a datetime) always runs, to
        # keep the time-based subsystems going; inside this window it only keeps
        # the last command (see _timer_tick_paced). 0 = disabled.
        if now is None and self._min_cycle_interval_s > 0:
            elapsed = time.monotonic() - self._last_cycle_monotonic
            if elapsed < self._min_cycle_interval_s:
                if DEBUG_CONTROL_LOOP_DETAIL:
                    _LOGGER.debug(
                        "Event trigger throttled: %.2fs since last cycle < %.2fs min interval",
                        elapsed, self._min_cycle_interval_s,
                    )
                return
        if self._control_lock.locked():
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug("Control cycle already running; skipping overlapping trigger.")
            return
        async with self._control_lock:
            previous_cycle_monotonic = self._last_cycle_monotonic
            # Safety-timer pacing (#654): a tick inside the user's pacing window
            # (No-PD Command Delay, else PD Min Cycle Interval) still runs the
            # structural/safety review, but keeps the last command instead of
            # acting on a fresh meter sample. Otherwise a fast meter turns the
            # 2 s timer into a command every tick, defeating both settings.
            pace_s = (
                self._no_pd_command_delay
                if self.no_pd_mode_enabled and self._no_pd_command_delay > 0
                else self._min_cycle_interval_s
            )
            self._timer_tick_paced = (
                now is not None
                and pace_s > 0
                and time.monotonic() - previous_cycle_monotonic < pace_s
            )
            self._last_cycle_monotonic = time.monotonic()
            self._cycle_kept_last_command = False
            # FORK: review 6 - a real-meter trigger that only kept the last
            # command gives the interval back like a timer tick does.
            physical_trigger = now is None and getattr(self, "_physical_trigger_pending", False)
            self._physical_trigger_pending = False
            await self._run_control_cycle(now)
            # A safety-timer tick that found no new meter sample and kept the
            # last command gives PD Min Cycle Interval back, so it doesn't hold
            # off the next meter update. A tick that acted on a new sample
            # still counts: with a fast meter it usually does, and leaving it
            # out would let ticks add cycles on top of the paced meter updates.
            if (now is not None or physical_trigger) and self._cycle_kept_last_command:  # FORK: review 6
                self._last_cycle_monotonic = previous_cycle_monotonic

    def _schedule_no_pd_debounced_run(self):
        """Arm a one-shot deferred control cycle for the no-PD command delay.

        If a deferred run is already pending, do nothing: it will read the latest
        sensor value when it fires, so events arriving inside the window collapse
        into that single run (one command per delay window, on fresh data).
        """
        if self._no_pd_debounce_unsub is not None:
            return
        self._no_pd_debounce_unsub = async_call_later(
            self.hass, self._no_pd_command_delay, self._fire_no_pd_debounced_run
        )

    @callback
    def _fire_no_pd_debounced_run(self, _now):
        """Launch the deferred no-PD control cycle (called by async_call_later).

        Sync callback + background task for the same reason as
        schedule_control_cycle: async_call_later would otherwise run the cycle
        as a startup-tracked task.
        """
        self._no_pd_debounce_unsub = None
        if getattr(self, "_unloading", False):
            return
        coroutine = self._run_no_pd_debounced_cycle()
        create = getattr(self, "_create_entry_background_task", None)
        if callable(create):
            create(coroutine, "omnibattery_no_pd_cycle")
        else:
            self.config_entry.async_create_background_task(
                self.hass, coroutine, "omnibattery_no_pd_cycle"
            )

    async def _run_no_pd_debounced_cycle(self):
        """Run the deferred no-PD control cycle."""
        # #654: a safety-timer tick may have acted since this run was armed.
        # Wait out the rest of its window so commands stay spaced by the delay.
        remaining = self._no_pd_command_delay - (
            time.monotonic() - self._last_cycle_monotonic
        )
        if remaining > 0.01:  # tolerance: the re-armed timer fires at ~0 remaining
            if self._no_pd_debounce_unsub is None:
                self._no_pd_debounce_unsub = async_call_later(
                    self.hass, remaining, self._fire_no_pd_debounced_run
                )
            return
        if self._control_lock.locked():
            return
        async with self._control_lock:
            self._timer_tick_paced = False
            self._last_cycle_monotonic = time.monotonic()
            await self._run_control_cycle()

    def _cancel_no_pd_debounced_run(self):
        """Cancel any pending deferred no-PD cycle (e.g. on mode-off / unload)."""
        if self._no_pd_debounce_unsub is not None:
            self._no_pd_debounce_unsub()
            self._no_pd_debounce_unsub = None

    async def _apply_phase_safety_review(self) -> None:
        """Re-limit already-running automatic commands after a phase event.

        This is used when the main grid sensor is unavailable or stale enough to
        take the normal control path out early.  It never computes a new Grid 0
        target; it only replays the currently commanded direction through the
        phase-aware distributor.
        """
        limiter = self._phase_power_limiter
        if limiter is None or not limiter.enabled or not hasattr(self, "_power_distribution"):
            cycle_trace.event("PHASE", self, skip="disabled")  # FORK: trace
            return

        requested_by_direction: dict[bool, dict[Any, float]] = {
            True: {},
            False: {},
        }
        # FORK: trace - per battery and direction: command, measured fallback, used
        trace_req: list[str] | None = [] if cycle_trace.enabled() else None
        # Capture both directions before issuing any writes. Writing the first
        # direction must not erase the second direction's live intent.
        for is_charging in (True, False):
            for coordinator in self.coordinators:
                if ChargeDischargeController._is_battery_manual_owned(coordinator):
                    continue
                charge = getattr(coordinator, "commanded_charge_power", 0) or 0
                discharge = getattr(coordinator, "commanded_discharge_power", 0) or 0
                value = charge if is_charging else discharge
                source = "cmd"
                delivered = None
                if value <= 0:
                    delivered = self._coordinator_delivered_power(coordinator)
                    if delivered is not None and (
                        delivered > 0 if is_charging else delivered < 0
                    ):
                        value = abs(delivered)
                        source = "meas"
                if value > 0:
                    requested_by_direction[is_charging][coordinator] = float(value)
                if trace_req is not None:
                    cycle_trace.trace_phase_request(
                        trace_req, coordinator, is_charging, charge, discharge,
                        delivered, source if value > 0 else "none", value,
                    )

        if not any(requested_by_direction.values()):
            self._phase_safety_pending = False
            cycle_trace.event("PHASE", self, req=trace_req, act="none",
                              prev_p=getattr(self, "previous_power", None))  # FORK: trace
            return

        allocations: dict[bool, dict[Any, float]] = {True: {}, False: {}}
        for is_charging, requested in requested_by_direction.items():
            if not requested:
                continue
            allocations[is_charging] = self._power_distribution._distribute_power_by_limits(
                sum(requested.values()),
                list(requested),
                is_charging,
            )

        for coordinator in self.coordinators:
            if ChargeDischargeController._is_battery_manual_owned(coordinator):
                continue
            await self._set_battery_power(
                coordinator,
                allocations[True].get(coordinator, 0),
                allocations[False].get(coordinator, 0),
            )

        trace_prev = getattr(self, "previous_power", None)  # FORK: trace
        self.previous_power = (
            sum(allocations[True].values()) - sum(allocations[False].values())
        )
        self._phase_safety_pending = False
        cycle_trace.event(  # FORK: trace
            "PHASE", self, req=trace_req, act="write",
            alloc_c=sum(allocations[True].values()),
            alloc_d=sum(allocations[False].values()),
            prev_p=trace_prev, new_p=self.previous_power,
        )
        cycle_trace.put(self, phase=f"{trace_prev}>{self.previous_power}")

    def _compute_no_pd_new_power(self, error):
        """No-PD direct-tracking control law: deadbeat 1:1 load tracking.

        The grid meter reading already includes the battery's ACTUAL output, so
        reconstruct the home load from measured power and command it directly:
        home = grid - measured = sensor_actual - measured, new = target - home,
        which collapses to new = measured - error. No integral, derivative,
        smoothing, rate limiter or hysteresis.

        Anchoring to MEASURED power (not the last command) is what makes the
        deadbeat stable across the inverter ramp + meter latency. A previous_power
        anchor assumes the battery is already at the last command; during the
        multi-second ramp it isn't, so every mid-ramp sample attributes the
        still-uncovered error to the load, doubles the correction, overshoots, and
        the loop oscillates rail-to-rail. Measured power is co-incident with the
        grid reading (both physical AC measurements), so the reconstruction holds
        at any point in the ramp. Falls back to previous_power only when no battery
        reports delivered power yet (e.g. just after a restart).
        """
        measured = self._measured_battery_power()
        base = measured if measured is not None else self.previous_power
        return base - error

    def _check_feedforward_step(self, error):
        """Two-sample load-step detector for the one-shot feedforward (PD mode).

        A kettle/oven-sized load step takes ~13 s to cover through the incremental
        P term (Kp=0.35 corrects ~35% per nominal cycle) and raising Kp globally
        reintroduces hunting. Instead, when this returns True the caller commands
        ONE deadbeat cycle (measured - error, the no-PD law) and the PD resumes
        fine adjustment from the new operating point.

        Detection (2 samples, both compared against the pre-step baseline):
        an error jump beyond max(5*deadband, FEEDFORWARD_STEP_FLOOR_W) arms a
        candidate; it fires only if the NEXT sample still shows the deviation
        (same sign, >= FEEDFORWARD_CONFIRM_RATIO of the jump). A single-sample
        excursion is a meter spike and is rejected. The threshold sits above the
        adaptive filter's collapse threshold (3*deadband/200W) on purpose: the
        filter merely passes a step through, the feedforward acts on it.

        Anti-hunting guards:
        - Cooldown: at most one fire per FEEDFORWARD_COOLDOWN_S, covering the
          actuator ramp (3-6 s) so the correction transient cannot re-trigger.
        - Pulse guard: a confirmed step of OPPOSITE sign within
          FEEDFORWARD_PULSE_GUARD_S of the last fire is a pulsing load
          (induction hob) — do not fire; the slow PD averaging such loads out
          is the desired behavior.
        - A candidate older than FEEDFORWARD_CANDIDATE_MAX_AGE_S (deadband or
          blocked cycles in between) is stale and cannot confirm.
        """
        now = time.monotonic()
        candidate = self._step_candidate
        self._step_candidate = None
        if candidate is not None:
            baseline, jump, armed_ts = candidate
            deviation = error - baseline
            if (
                now - armed_ts <= FEEDFORWARD_CANDIDATE_MAX_AGE_S
                and (deviation > 0) == (jump > 0)
                and abs(deviation) >= FEEDFORWARD_CONFIRM_RATIO * abs(jump)
            ):
                sign = 1 if jump > 0 else -1
                last_ts = self._last_feedforward_monotonic
                if last_ts is not None and now - last_ts < FEEDFORWARD_COOLDOWN_S:
                    _LOGGER.debug(
                        "Feedforward: confirmed %.0fW step suppressed by cooldown (%.1fs < %.0fs)",
                        jump, now - last_ts, FEEDFORWARD_COOLDOWN_S,
                    )
                    return False
                if (
                    last_ts is not None
                    and sign != self._last_feedforward_sign
                    and now - last_ts < FEEDFORWARD_PULSE_GUARD_S
                ):
                    _LOGGER.debug(
                        "Feedforward: opposite-sign %.0fW step %.1fs after last fire - pulsing load, letting PD average it",
                        jump, now - last_ts,
                    )
                    return False
                self._last_feedforward_monotonic = now
                self._last_feedforward_sign = sign
                return True
        jump = error - self.previous_error
        if abs(jump) > max(5 * self.deadband, FEEDFORWARD_STEP_FLOOR_W):
            self._step_candidate = (self.previous_error, jump, now)
            _LOGGER.debug(
                "Feedforward: %.0fW error jump armed as step candidate (awaiting confirmation)",
                jump,
            )
        return False

    def _compute_pd_new_power(self, error, sensor_elapsed_s, stale_safety_recalc):
        """Incremental PD control law: anti-windup re-anchor, optional integral,
        filtered derivative, P/I/D terms, rate limiter and directional hysteresis.

        Returns the new commanded power in watts (+charge / -discharge). The shared
        tail (min power, relay dwell, restrictions, distribution) runs in
        _run_control_cycle for both modes. Bypassed entirely by no-PD
        direct-tracking mode, which commands raw deadbeat (previous - error).
        """
        # ANTI-WINDUP (back-calculation): the incremental loop assumes the batteries
        # delivered exactly the last commanded power. When they can't (SOC/voltage
        # taper, ramp lag, internal derating not captured by the capacity clamp),
        # previous_power drifts past reality and the integral-like P term winds up,
        # causing an overshoot/export spike when load later drops. Re-anchor the
        # increment base to the MEASURED AC power once under-delivery is sustained
        # (a single cycle may just be scan-interval lag). The sign guard prevents a
        # transient near-zero reading from flipping direction, and we only ever clamp
        # the base DOWN toward reality, never inflate it.
        measured_power = self._measured_battery_power()
        shortfall_active = (
            measured_power is not None
            and self.previous_power != 0
            and (self.previous_power > 0) == (measured_power >= 0)
            and abs(self.previous_power) - abs(measured_power) > self.saturation_backcalc_threshold
        )
        if shortfall_active:
            saturated = self._backcalc_is_saturated(self.previous_power > 0)
            if self._saturation_shortfall_since is None:
                self._saturation_shortfall_since = dt_util.utcnow()
            sustained_s = (
                dt_util.utcnow() - self._saturation_shortfall_since
            ).total_seconds()
            # Fast path: a real limit is active, so the shortfall is genuine
            # saturation — re-anchor after a few cycles. Slow path: no known
            # limit (likely actuator ramp lag), so only re-anchor after a long
            # sustained shortfall as a windup safety net for unmodelled derate.
            # The slow path is skipped when the telemetry itself lags (#522): the "shortfall" is then a stale reading, and re-anchoring
            # to it yanks the command back every ~15 s and makes the loop hunt.
            if saturated:
                self._saturation_cycles += 1
            else:
                self._saturation_cycles = 0
            if (
                saturated and self._saturation_cycles >= self.saturation_backcalc_cycles
            ) or (
                sustained_s >= self.saturation_backcalc_fallback_s
                and self._delivered_power_reliable()
            ):
                _LOGGER.debug(
                    "PD anti-windup: re-anchoring base %.0fW -> measured %.0fW "
                    "(shortfall %.0fW, saturated=%s, sustained %.0fs)",
                    self.previous_power, measured_power,
                    abs(self.previous_power) - abs(measured_power),
                    saturated, sustained_s,
                )
                self.previous_power = measured_power
                self._saturation_cycles = 0
                self._saturation_shortfall_since = None
        else:
            self._saturation_cycles = 0
            self._saturation_shortfall_since = None

        # Note: Oscillation detection moved to end of method (after checking restrictions)
        # This prevents false positives when controller is paused by time slot restrictions

        # Only process integral if Ki > 0 (integral is enabled)
        if self.ki > 0:
            # DIRECTIONAL RESET: If integral is working AGAINST the current error, it's obsolete
            # Example: integral is positive (wants to charge) but error is negative (should discharge)
            # This means the integral accumulated from old conditions and must be cleared
            integral_sign = 1 if self.error_integral > 0 else (-1 if self.error_integral < 0 else 0)
            error_sign = 1 if error > 0 else (-1 if error < 0 else 0)
            
            if integral_sign != 0 and error_sign != 0 and integral_sign != error_sign:
                # Integral and error have opposite signs - integral is working against the error
                _LOGGER.error("PID DIRECTIONAL CONFLICT: Integral=%.1fW (%s) but Error=%.1fW (%s) - RESETTING integral!",
                            self.error_integral, "charge" if integral_sign > 0 else "discharge",
                            error, "charge" if error_sign > 0 else "discharge")
                self.error_integral = 0.0
                self.sign_changes = 0  # Reset oscillation counter too
            
            # LEAKY INTEGRATOR: Apply decay before adding new error
            # This prevents the integral from growing unbounded and helps it "forget" old errors
            self.error_integral *= self.integral_decay
            
            # Calculate potential new integral value
            new_integral = self.error_integral + error * self.dt
            
            # CONDITIONAL INTEGRATION (Anti-windup):
            # Only accumulate integral if we're NOT saturated at the limits
            # This prevents integral windup when output is already at maximum
            is_saturated_positive = new_integral > self.max_charge_capacity
            is_saturated_negative = new_integral < -self.max_discharge_capacity
            
            if is_saturated_positive:
                self.error_integral = self.max_charge_capacity
                _LOGGER.warning("PID anti-windup: Integral SATURATED at max charge capacity +%dW (not accumulating)", 
                              self.max_charge_capacity)
            elif is_saturated_negative:
                self.error_integral = -self.max_discharge_capacity
                _LOGGER.warning("PID anti-windup: Integral SATURATED at max discharge capacity -%dW (not accumulating)", 
                              self.max_discharge_capacity)
            else:
                # Not saturated, safe to accumulate
                self.error_integral = new_integral
                _LOGGER.debug("PID: Integral updated to %.1fW (within limits)", self.error_integral)
        else:
            # Integral disabled - ensure it stays at zero
            self.error_integral = 0.0
        
        # Time bases for the cadence-dependent terms. The derivative keeps a 1 s floor
        # (dividing by a sub-second dt would amplify noise into a spike); the P-term and
        # rate-limiter scaling use a smaller floor so they stay accurate for sub-second
        # sensors (a 1 s floor there would over-weight fast cadences).
        if stale_safety_recalc:
            # Safety valve: suppress derivative to avoid spike from stale data
            real_dt = self.dt
            scale_dt = self.dt
            error_derivative = 0.0
            self.derivative_filtered = 0.0  # drop stale derivative state
        else:
            base_dt = sensor_elapsed_s if (sensor_elapsed_s and sensor_elapsed_s > 0) else self.dt
            real_dt = max(1.0, min(base_dt, 30.0))
            scale_dt = max(0.1, min(base_dt, 30.0))
            error_derivative_raw = (error - self.previous_error) / real_dt
            # Low-pass the derivative: differentiating a barely-filtered grid signal
            # (2-sample moving average) amplifies PWM/quantization noise, which the D
            # term would otherwise inject into the output. EMA with a real-time alpha.
            d_alpha = real_dt / (self.derivative_tau + real_dt)
            self.derivative_filtered += d_alpha * (error_derivative_raw - self.derivative_filtered)
            error_derivative = self.derivative_filtered
        
        # PID terms
        # The P term is applied incrementally (new_power -= P) every cycle, so it acts
        # as integral action whose effective rate scales with cycle frequency. The loop
        # is now event-driven (variable cadence, ~1 s) rather than a fixed 2 s timer, so
        # scale by real elapsed time normalized to the nominal dt — this keeps the
        # per-second correction, and therefore the tuning, independent of cadence.
        # Cap the cadence multiplier on the incremental (integral-like) P term so the
        # effective per-update gain (kp * ratio) stays within the discrete stability
        # bound. Scaling P up by elapsed/dt is only valid while the loop closes between
        # samples; for a slow sensor the sample interval IS the feedback dead time, so an
        # uncapped step is applied open-loop and oscillates rail-to-rail (Keff > 1).
        p_scale = scale_dt / self.dt
        if self.kp > 0:
            p_scale = min(p_scale, max(1.0, 1.0 / self.kp))
        if stale_safety_recalc:
            p_scale = 0.0  # hold command; no fresh grid data to integrate (see above)
        P = self.kp * error * p_scale
        I = self.ki * self.error_integral
        D = self.kd * error_derivative
        
        # Calculate ADJUSTMENT to apply to current power (incremental control)
        # P term responds to current error
        # D term dampens rapid changes
        pd_adjustment = P + I + D
        
        # Apply adjustment to previous power to get new target
        new_power_raw = self.previous_power - pd_adjustment  # Minus because we're correcting the imbalance
        
        # RATE LIMITER: Prevent abrupt changes that cause overshoot. The configured
        # value is a per-cycle cap calibrated for the nominal dt; scale by real elapsed
        # time so the effective ramp rate (W/s) stays constant under the variable
        # event-driven cadence (otherwise faster cycles would multiply the ramp rate).
        max_change = self.max_power_change_per_cycle * (scale_dt / self.dt)
        power_change = new_power_raw - self.previous_power
        if abs(power_change) > max_change:
            # Clamp the change to maximum allowed rate
            sign = 1 if power_change > 0 else -1
            new_power = self.previous_power + (sign * max_change)
            if self._should_log_rate_limiter(power_change):
                _LOGGER.info(
                    "PD rate limiter: requested_change=%.1fW limit=+/-%.0fW applied_change=%.1fW",
                    power_change,
                    max_change,
                    new_power - self.previous_power,
                )
        else:
            self._clear_rate_limiter_state()
            new_power = new_power_raw
        
        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("PD: Adjustment=%.1fW, Previous power=%.1fW, New target=%.1fW",
                         pd_adjustment, self.previous_power, new_power)
        
        trace_after_rl = new_power  # FORK: trace - PD output before the hysteresis
        # DIRECTIONAL HYSTERESIS: Prevent rapid switching between charge/discharge
        # If we're changing direction, the new power must overcome the hysteresis threshold
        current_output_sign = 1 if new_power > 0 else (-1 if new_power < 0 else 0)
        self._flip_hyst_suppressed = False
        
        if self.last_output_sign != 0 and current_output_sign != 0:
            if self.last_output_sign != current_output_sign:
                # Direction is changing - check if it overcomes hysteresis.
                # The grid error is checked too: after a suppressed flip the
                # increment base is 0, so |new_power| is just the kp-scaled error
                # and understates the demand — gating on it alone would either
                # block small flips forever (dead zone up to hysteresis/kp) or,
                # with the sign memory zeroed, not at all. The error is the
                # physical demand signal and does not decay across cycles.
                if (
                    abs(new_power) < self.direction_hysteresis
                    and abs(error) < self.direction_hysteresis
                ):
                    _LOGGER.info("PD: Direction change suppressed by hysteresis - output=%.1fW, error=%.1fW < threshold=%dW, staying at 0W",
                                new_power, error, self.direction_hysteresis)
                    new_power = 0
                    current_output_sign = 0
                    self._flip_hyst_suppressed = True
                else:
                    _LOGGER.info("PD: Direction change ALLOWED - output=%.1fW or error=%.1fW > threshold=%dW",
                                abs(new_power), abs(error), self.direction_hysteresis)
        # Log control output
        if self.ki > 0:
            # Calculate integral utilization percentage for monitoring
            if self.error_integral > 0:  # Integral is positive (charging direction)
                integral_percent = (self.error_integral / self.max_charge_capacity) * 100 if self.max_charge_capacity > 0 else 0
            elif self.error_integral < 0:  # Integral is negative (discharging direction)
                integral_percent = (abs(self.error_integral) / self.max_discharge_capacity) * 100 if self.max_discharge_capacity > 0 else 0
            else:
                integral_percent = 0
            
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug("ChargeDischargeController: PD Control - Grid=%.1fW, P=%.1fW, I=%.1fW (%.0f%%), D=%.1fW, Adjustment=%.1fW, New=%.1fW",
                              error, P, I, integral_percent, D, pd_adjustment, new_power)
        else:
            # Integral disabled - simpler log
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug("ChargeDischargeController: PD Control - Grid=%.1fW, P=%.1fW, D=%.1fW, Adjustment=%.1fW, New=%.1fW",
                              error, P, D, pd_adjustment, new_power)
        # FORK: trace - base/P/I/D/raw/after rate limit/after hysteresis
        cycle_trace.put_lazy(self, lambda: {"pd": (
            f"base{cycle_trace._fmt(float(self.previous_power))}"
            f"/P{cycle_trace._fmt(float(P))}/I{cycle_trace._fmt(float(I))}"
            f"/D{cycle_trace._fmt(float(D))}/raw{cycle_trace._fmt(float(new_power_raw))}"
            f"/rl{cycle_trace._fmt(float(trace_after_rl))}"
            f"/hy{cycle_trace._fmt(float(new_power))}"
        )})
        return new_power

    def _apply_min_power(self, new_power, error):
        """MINIMUM POWER CHECK: avoid inefficient low-power operation.

        A sub-minimum PD output cannot simply be zeroed: zeroing also resets
        ``previous_power``, so the incremental loop restarts from 0 every cycle
        and a steady sub-minimum demand never accumulates up to the minimum —
        a dead zone up to ~minimum/kp where the load is never covered at all.

        Instead, engage AT the minimum when the grid error is large enough that
        the resulting over-correction lands inside the deadband (error >=
        minimum - deadband): that is a stable point the deadband then holds.
        Smaller errors stay idle (also stable) rather than bouncing on/off
        around the minimum. Larger errors bootstrap normal incremental ramping
        from the minimum on the next cycle.
        """
        min_charge = self.min_charge_power
        min_discharge = self.min_discharge_power
        if new_power > 0 and min_charge > 0 and new_power < min_charge:
            if -error >= min_charge - self.deadband:
                _LOGGER.debug("PD: Charge power %.1fW below minimum %dW, engaging at minimum (error=%.1fW)",
                              new_power, min_charge, error)
                return min_charge
            _LOGGER.debug("PD: Charge power %.1fW below minimum %dW, setting to idle",
                          new_power, min_charge)
            return 0
        if new_power < 0 and min_discharge > 0 and abs(new_power) < min_discharge:
            if error >= min_discharge - self.deadband:
                _LOGGER.debug("PD: Discharge power %.1fW below minimum %dW, engaging at minimum (error=%.1fW)",
                              abs(new_power), min_discharge, error)
                return -min_discharge
            _LOGGER.debug("PD: Discharge power %.1fW below minimum %dW, setting to idle",
                          abs(new_power), min_discharge)
            return 0
        return new_power

    def _apply_relay_dwell(self, new_power, error, stale_recalc=False):
        """RELAY ANTI-CHATTER (shut-off dwell).

        When the controller decides to send the battery back to idle, keep it
        engaged at minimum power for at least ``_relay_cooldown_s`` seconds first, so
        the relay doesn't click off the moment demand falls and back on when it
        returns. The dwell is timed from the instant idle was FIRST requested
        (``_relay_shutoff_since``), not from when the battery engaged, so it always
        delivers the full hold even after a long active run.

        Only the active->idle transition is gated; charge<->discharge flips keep the
        relay engaged anyway. A large imbalance bypasses the hold (cost-capped: we
        only hold while the over/under-shoot stays small, ~3x deadband), so a sudden
        real load isn't left on the grid. The cap measures imbalance BEYOND the power
        the battery was already handling: at shut-off the grid swings by
        ~previous_power (the battery's own delivery now reads as grid export/import),
        so comparing raw error would trip the cap on every shut-off above ~3x deadband
        and skip the hold entirely.

        EXCEPTION to the bypass: a zero that comes from a zero-cross-suppressed
        flip (``_zero_cross_since`` armed) is not a real idle decision — the PD
        wants the OPPOSITE direction, and once the flip passes the settle window
        the relay stays engaged in that direction anyway. Bypassing there dropped
        the relay for exactly the settle window on every direction swing (constant
        chatter under a volatile adjusted sensor, e.g. an excluded A/C riding solar
        clouds), so a suppressed flip always holds: the wrong-direction cost is
        min power for at most the settle window.

        ``stale_recalc`` marks a pass without a new meter sample: its command is
        the frozen previous one (the held minimum), not a fresh "active" decision,
        so an armed timer must survive it (same trap as #117 in the zero-cross hold).

        Returns the (possibly held) power and manages the dwell timer as a side effect.
        """
        if stale_recalc and self._relay_shutoff_since is not None:
            return new_power
        suppressed_flip = self._zero_cross_since is not None
        wants_idle = (
            self._relay_cooldown_s > 0
            and new_power == 0
            and self.previous_power != 0
            and (
                suppressed_flip
                or abs(error) - abs(self.previous_power)
                < max(self.deadband * 3, RELAY_COOLDOWN_HOLD_POWER)
            )
        )
        if not wants_idle:
            # Battery is active (or a large imbalance bypassed the hold): re-arm.
            self._relay_shutoff_since = None
            return new_power

        if self._relay_shutoff_since is None:
            self._relay_shutoff_since = dt_util.utcnow()
        held_s = (dt_util.utcnow() - self._relay_shutoff_since).total_seconds()
        if held_s >= self._relay_cooldown_s:
            # Dwell satisfied; let the battery fall to idle and re-arm for next time.
            self._relay_shutoff_since = None
            return new_power

        # Minimum power required to hold the current direction.
        requested_hold_w = (
            self.min_charge_power
            if self.previous_power > 0
            else self.min_discharge_power
        ) or RELAY_COOLDOWN_HOLD_POWER
        # Never hold above the power already flowing (upstream #909): the hold
        # must not raise a small charge/discharge up to the fallback minimum.
        capped_hold_w = min(requested_hold_w, abs(self.previous_power))
        held_power = capped_hold_w if self.previous_power > 0 else -capped_hold_w
        _LOGGER.debug(
            "Relay cooldown: holding %s engaged at %.0fW "
            "(previous=%.0fW, requested=%.0fW, elapsed=%.0f/%.0fs)",
            "charge" if held_power > 0 else "discharge",
            abs(held_power), self.previous_power, requested_hold_w,
            held_s, self._relay_cooldown_s,
        )
        return held_power

    def _apply_zero_cross_hold(self, new_power, error, stale_recalc=False):
        """ZERO-CROSS HOLD (direction-flip dwell).

        On a downward load step the discharging battery keeps delivering its old
        setpoint for the actuator settle time (~3-6 s measured), so the grid shows
        a transient export of hundreds of watts. The incremental PD (request =
        previous + Kp*error...) crosses zero on that transient and, if charging is
        allowed, emits a real charge command — assigned to another battery while
        the assignment loop zeroes the discharger. Result: 0 W discharge with the
        house importing, then the PD swings back — ping-pong every 1-3 min. The
        direction hysteresis (magnitude-based, ~60 W) cannot stop a -500/-1500 W
        transient, and the relay dwell only gates active->idle, not flips.

        Gate: the first cycle that requests the OPPOSITE direction to
        ``last_output_sign`` is clamped to 0 and arms ``_zero_cross_since``; the
        flip only passes once the opposite-direction request has persisted for the
        settle window. A transient export collapses back to the previous direction
        within a couple of cycles and re-arms the timer; a legitimate sustained
        surplus flips after the window (a few seconds' delay, harmless). Requests
        of 0 or of the previous direction pass through untouched and re-arm.

        Runs on every control path (PD, feedforward, no-PD) and BEFORE the
        min-power floor, so a suppressed flip can never be bootstrapped up to
        pd_min_charge_power; the relay dwell downstream then decides whether the
        previous battery holds at minimum power or drops to 0.

        ``stale_recalc`` marks the safety recalculation that runs on a silent
        sensor: its 0 W command is the frozen previous command, not a fresh idle
        decision, so the armed timer must survive it.
        """
        requested_sign = 1 if new_power > 0 else (-1 if new_power < 0 else 0)
        # Consume once: no-PD paths never reach _compute_pd_new_power, so a flag
        # left over from an earlier PD cycle must not survive into them.
        hyst_suppressed, self._flip_hyst_suppressed = self._flip_hyst_suppressed, False
        if stale_recalc and requested_sign == 0 and self._zero_cross_since is not None:
            # Issue #117: on a sensor slower than the stale window, every
            # stale recalc in between cleared the timer, so the flip re-armed at
            # 0.0s on each fresh sample and could never accumulate the window.
            return new_power
        if (
            requested_sign == 0
            and self._zero_cross_since is not None
            and hyst_suppressed
        ):
            # Issue #849: the 0 W is the direction hysteresis suppressing a
            # sub-threshold flip, not an idle decision. Clearing the timer here
            # made ALLOWED/suppressed cycles alternate and never reach the window.
            return new_power
        if (
            self.last_output_sign == 0
            or requested_sign == 0
            or requested_sign == self.last_output_sign
        ):
            self._zero_cross_since = None
            return new_power

        # Window: at least the fixed floor, stretched for slow actuators (2x
        # latency ~= command->ramp + telemetry grain).
        latency_s = max(
            (c.capabilities.actuator_latency_s for c in self.coordinators),
            default=0.0,
        )
        window_s = max(PD_ZERO_CROSS_MIN_HOLD_S, 2.0 * latency_s)

        now = dt_util.utcnow()
        if self._zero_cross_since is None:
            self._zero_cross_since = now
        held_s = (now - self._zero_cross_since).total_seconds()
        if held_s >= window_s:
            self._zero_cross_since = None
            _LOGGER.info(
                "PD zero-cross hold: %s request persisted %.1fs >= %.1fs, allowing direction flip (%.0fW)",
                "charge" if requested_sign > 0 else "discharge",
                held_s, window_s, new_power,
            )
            return new_power
        _LOGGER.info(
            "PD zero-cross hold: suppressing %s->%s flip (%.0fW, error=%.0fW) while actuator settles (%.1fs/%.1fs)",
            "charge" if self.last_output_sign > 0 else "discharge",
            "charge" if requested_sign > 0 else "discharge",
            new_power, error, held_s, window_s,
        )
        return 0

    def _check_missing_configured_sensors(self) -> None:
        """Name configured sensors whose entity does not exist (#419).

        The options flow keeps a stored sensor when the entity picker could not
        render it, so a reference to a deleted or renamed entity would otherwise
        be invisible and unclearable: this issue is what makes it actionable.
        The delay rides out a restart, when the integration that provides the
        entity may not have finished setting up yet.
        """
        issue_id = f"configured_sensor_missing_{self.config_entry.entry_id}"
        missing = sorted(
            entity
            for entity in (
                self.config_entry.data.get(key)
                for key in (
                    "consumption_sensor",
                    CONF_OFFGRID_POWER_SENSOR,
                    CONF_SOLAR_PRODUCTION_SENSOR,
                    _fork_const.CONF_PHYSICAL_GRID_SENSOR,  # FORK: grid view (docs/fork/02-architektur.md)
                    _fork_const.CONF_CAPACITY_PROTECTION_LIMIT_ENTITY,
                )
            )
            if entity and self.hass.states.get(entity) is None
        )

        if not missing:
            self._missing_sensors_since = None
            if (
                self._missing_sensors_reported is not None
                or not self._missing_sensors_issue_cleared
            ):
                self._missing_sensors_reported = None
                self._missing_sensors_issue_cleared = True
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        mono = time.monotonic()
        if self._missing_sensors_since is None:
            self._missing_sensors_since = mono
            return
        if (
            self._missing_sensors_reported == missing
            or mono - self._missing_sensors_since < MISSING_SENSOR_ISSUE_DELAY_S
        ):
            return

        self._missing_sensors_reported = missing
        self._missing_sensors_issue_cleared = False
        _LOGGER.warning(
            "Configured sensor(s) %s do not exist in Home Assistant - Omnibattery "
            "keeps the stored reference; pick a replacement in the options flow",
            ", ".join(missing),
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="configured_sensor_missing",
            translation_placeholders={"sensors": ", ".join(missing)},
        )

    @staticmethod
    def _sensor_report_time(sensor_state):
        """Return the publication timestamp available on a Home Assistant state."""
        if sensor_state is None:
            return None
        return (
            getattr(sensor_state, "last_reported", None)
            or getattr(sensor_state, "last_updated", None)
        )

    def _observe_sensor_cadence(self, sensor_report_time):
        """Feed one actual sensor publication into the slow-sensor detector.

        The control loop is deliberately allowed to run slower than the grid meter
        (battery I/O can occupy it for several seconds).  Therefore cadence must be
        measured from state publications, not from the times at which the control loop
        happens to sample ``hass.states``.  The state-change callback calls this for
        every publication; the control loop also calls it as a fallback.  The timestamp
        makes the two paths idempotent.
        """
        if sensor_report_time is None:
            return None

        previous_report_time = getattr(
            self, "_last_sensor_cadence_time", None
        )
        if previous_report_time == sensor_report_time:
            return None
        self._last_sensor_cadence_time = sensor_report_time

        if previous_report_time is None:
            return None

        sensor_elapsed_s = (
            sensor_report_time - previous_report_time
        ).total_seconds()
        if sensor_elapsed_s > 0:
            ChargeDischargeController._check_sensor_cadence(
                self, sensor_elapsed_s
            )
            return sensor_elapsed_s
        return None

    def _track_control_sample(self, sensor_value):
        """Record whether the transformed meter value is new for control.

        ``last_reported`` answers whether the meter is alive, not whether the
        incremental controller has a new measurement to act on.  The transformed
        value is the only meter input used by the controller, so it is also the
        appropriate fingerprint: changes to unrelated state attributes do not
        reapply P/D, while unit changes that alter the transformed value still do.
        """
        previous_value = getattr(self, "_last_control_sample_value", None)
        is_new = previous_value is None or sensor_value != previous_value
        self._last_control_sample_value = sensor_value
        self._control_sample_is_new = is_new
        return is_new

    def _track_sensor_report(self, sensor_state, sensor_value=None):
        """Track real sensor publications, including unchanged state reports.

        Home Assistant leaves ``last_updated`` unchanged when an integration
        republishes the same state and attributes. ``last_reported`` still advances,
        so it is the correct source for cadence and stale-data health. The fallback
        keeps compatibility with State-like objects from older Home Assistant versions.

        ``sensor_value`` is optional to preserve the helper's State-like test/API
        compatibility. Production control paths pass the value returned by
        ``_apply_meter_transform`` so health tracking and control freshness remain
        separate.
        """
        sensor_report_time = ChargeDischargeController._sensor_report_time(
            sensor_state
        )
        previous_report_time = self._last_sensor_report_time
        self._last_sensor_report_time = sensor_report_time
        is_stale = (
            previous_report_time is not None
            and sensor_report_time == previous_report_time
        )
        sensor_elapsed_s = (
            (sensor_report_time - previous_report_time).total_seconds()
            if previous_report_time is not None and sensor_report_time is not None
            else None
        )
        ChargeDischargeController._observe_sensor_cadence(
            self, sensor_report_time
        )
        if sensor_value is not None:
            ChargeDischargeController._track_control_sample(self, sensor_value)
        return sensor_report_time, sensor_elapsed_s, is_stale

    def _observe_consumption_report(self, event):
        """Record a state publication for health without scheduling control.

        ``EVENT_STATE_REPORTED`` is intentionally cadence-only. A changed state
        still schedules through its separate callback; repeated P1 publications
        are consumed here and by the watchdog without re-running incremental P/D.
        """
        entity_id = event.data.get("entity_id")
        if entity_id and entity_id != self.consumption_sensor:
            return None
        new_state = event.data.get("new_state")
        report_time = ChargeDischargeController._sensor_report_time(new_state)
        if report_time is None:
            report_time = event.data.get("last_reported")
        return self._observe_sensor_cadence(report_time)

    def _check_sensor_cadence(self, sensor_elapsed_s):
        """Raise a repair while the main sensor cadence is slow, clear it when it recovers.

        Slow sensors remain supported up to the stale tolerance. The repair is
        guidance about control quality, not a rejection of the sensor. Only positive,
        real update intervals reach the debounce; watchdog ticks report 0 and must
        not reset its streak.

        Debounced over consecutive intervals: a single long gap is far more likely to be
        an outage or a restart than the configured cadence, because the stored sensor
        timestamp is not advanced while the sensor reads unavailable, so the first sample
        after any downtime measures the whole gap.

        The repair describes the sensor as it behaves now, so a cadence that recovers
        must clear it: SLOW_SENSOR_RECOVERY_INTERVALS consecutive fast intervals delete
        the issue, whether it was created in this run or persisted from an earlier one.
        Creating needs only SLOW_SENSOR_WARN_INTERVALS, so the asymmetry is the
        hysteresis that keeps a sensor hovering around the threshold from churning
        create/delete. Each transition acts once per streak.

        A repair persisted from an earlier run is cleared by the same recovery streak,
        so it survives roughly SLOW_SENSOR_RECOVERY_INTERVALS publications into a new
        run before disappearing.
        """
        if sensor_elapsed_s is None or sensor_elapsed_s <= 0:
            return

        issue_id = f"slow_main_sensor_{self.config_entry.entry_id}"
        if sensor_elapsed_s < SLOW_SENSOR_WARNING_INTERVAL_S:
            self._slow_sensor_intervals = 0
            self._fast_sensor_intervals += 1
            if self._fast_sensor_intervals == SLOW_SENSOR_RECOVERY_INTERVALS:
                self._slow_sensor_issue_created = False
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        self._fast_sensor_intervals = 0
        self._slow_sensor_intervals += 1
        if (
            self._slow_sensor_intervals < SLOW_SENSOR_WARN_INTERVALS
            or self._slow_sensor_issue_created
        ):
            return
        self._slow_sensor_issue_created = True
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key="slow_main_sensor",
            translation_placeholders={
                "sensor": self.consumption_sensor,
                "observed_interval": f"{sensor_elapsed_s:.0f}",
                "warning_interval": f"{SLOW_SENSOR_WARNING_INTERVAL_S:.0f}",
                "stale_limit": f"{MAX_SENSOR_STALE_S:.0f}",
            },
        )

    def _check_main_sensor_liveness(self, now=None):
        """Raise a repair while the main grid sensor has stopped publishing.

        The slow-sensor repair next door is publication-driven, so it can only
        describe a meter that still speaks. A meter that stops entirely produces
        no further publications and therefore never reaches it - the one failure
        that matters most is the one that says nothing (issue #452).

        Both flavours land here, which is why this is judged on the age of the
        last real publication rather than on the sensor's current state:

        * unavailable/unknown/missing - ``_apply_meter_transform`` returns None
          and the cycle returns early, logging once at debug.
        * frozen on a valid value - a wedged P1 bridge, or a template sensor
          whose inputs stopped moving. Nothing is logged at all, because from
          the state machine's point of view the entity is perfectly healthy.

        In both cases the meter's own last publication stops advancing. A meter
        that has not published a usable reading at all in this run has no
        publication to age, so its age counts from when this check started
        waiting for one, once Home Assistant has finished starting. A crash
        skips the unload that idles the batteries, and most brands hold the
        last command on their own, so without this a meter that never comes
        back would leave that command running with no repair raised. Past
        MAX_SENSOR_STALE_S the stale safety recalculation zeroes the P scale and
        the derivative, so the loop holds its last command; past
        MAIN_SENSOR_DEAD_S this check sets ``_main_sensor_dead`` and
        ``_apply_dead_meter_fallback`` stops automatic control, so the exposure
        is bounded in time rather than only by the SOC blockers.

        Called before every early return in the cycle, for the same reason the
        other health checks are: manual mode, a block or a max-SOC measurement
        taking ownership must not starve it, or an issue raised earlier could
        never be cleared.
        """
        if not self.consumption_sensor:
            self._main_sensor_dead = False
            return
        issue_id = f"dead_main_sensor_{self.config_entry.entry_id}"

        report_time = self._live_sensor_report_time()
        waiting = report_time is None
        if waiting:
            # Never a usable reading in this run. While Home Assistant is still
            # starting, the integration behind the meter may simply not be set
            # up yet; after that, the wait itself is the age. Once started it
            # keeps counting, so a shutdown cannot release the fallback.
            if (
                self._meter_wait_since is None
                and self.hass.state == CoreState.running
            ):
                self._meter_wait_since = (
                    now if isinstance(now, datetime) else dt_util.utcnow()
                )
            report_time = self._meter_wait_since
        age_s = (
            self._sensor_age_seconds(report_time, now)
            if report_time is not None
            else 0.0
        )

        if age_s < MAIN_SENSOR_DEAD_S:
            self._main_sensor_dead = False
            # Not known yet on the first check: the issue is persistent, so one
            # raised before a restart is restored as active, and only this check
            # would ever clear it.
            if self._dead_sensor_issue_created is not False:
                self._dead_sensor_issue_created = False
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        self._main_sensor_dead = True
        self._main_sensor_never_reported = waiting
        if self._last_valid_meter_publication is None and not waiting:
            # Pin the age to the tracked read time: the fallback clears that
            # timestamp, and without a high-water mark the age would collapse
            # to 0 and release the fallback the very next cycle.
            self._last_valid_meter_publication = report_time
        if self._dead_sensor_issue_created:
            return
        self._dead_sensor_issue_created = True
        # Raised ahead of the fallback in the same cycle, so previous_power is
        # still the command that ran blind. Without a reading in this run there
        # is no such command to name: what may still be running is whatever the
        # battery was told before the restart. The warning log belongs to the
        # fallback, which is what actually acts on it.
        placeholders = {
            "sensor": self.consumption_sensor,
            "minutes": f"{age_s / 60:.0f}",
        }
        if waiting:
            translation_key = "dead_main_sensor_since_start"
        else:
            translation_key = "dead_main_sensor"
            placeholders["power"] = f"{abs(self.previous_power):.0f}"
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=placeholders,
        )

    async def _apply_dead_meter_fallback(self) -> bool:
        """Stop automatic control while the main grid sensor is dead.

        ``_check_main_sensor_liveness`` decides; this acts. Holding the last
        command against a house nobody is reading is bounded only by the SOC
        blockers, so once the meter has been silent for MAIN_SENSOR_DEAD_S every
        automatic battery goes to 0W and stays there until it publishes again.
        An immediate stop rather than a ramp: the command has already run blind
        for minutes, and the operation blocks stop the same way.

        The caller places this after manual mode, manual slots and max-SOC
        balancing, which are not driven by the meter.
        ``_set_battery_power`` already skips batteries a user owns.

        Entering resets the controller the way a restart would: no PD state,
        filter or sample timestamp survives the outage, so the first reading
        after recovery goes through the first-execution initialisation instead
        of integrating a gap of minutes as one sample.

        Returns True while the fallback owns the cycle.
        """
        if not self._main_sensor_dead:
            if self._dead_meter_fallback_engaged:
                self._dead_meter_fallback_engaged = False
                _LOGGER.info(
                    "Grid sensor %s is publishing again - resuming automatic control",
                    self.consumption_sensor,
                )
            return False

        if not self._dead_meter_fallback_engaged:
            self._dead_meter_fallback_engaged = True
            if self._main_sensor_never_reported:
                _LOGGER.warning(
                    "Grid sensor %s has not published a valid reading in the %.0f "
                    "minutes since Home Assistant started - stopping automatic "
                    "battery control, in case a battery still runs a command from "
                    "before the restart, until it reports",
                    self.consumption_sensor, MAIN_SENSOR_DEAD_S / 60,
                )
            else:
                _LOGGER.warning(
                    "Grid sensor %s has not published for over %.0f minutes - stopping "
                    "automatic battery control (command was %.0fW) until it reports again",
                    self.consumption_sensor, MAIN_SENSOR_DEAD_S / 60, self.previous_power,
                )
            self.error_integral = 0.0
            self.previous_error = 0.0
            self.derivative_filtered = 0.0
            self.sign_changes = 0
            self.last_error_sign = 0
            self.previous_sensor = None
            self._zero_cross_since = None
            self._clear_rate_limiter_state()
            self._grid_filter_ema = None
            self._last_sensor_report_time = None
            self._last_control_sample_value = None
            self._control_sample_is_new = True
            self._stale_cycles = 0
            self.first_execution = True

        # Re-asserted every cycle: the bus-load skip makes repeats free, and a
        # battery that was unreachable when the fallback engaged still gets it.
        for coordinator in self.coordinators:
            await self._set_battery_power(coordinator, 0, 0)
        self.previous_power = 0
        self.last_output_sign = 0
        self._active_discharge_batteries = []
        self._active_charge_batteries = []
        self._phase_safety_pending = False
        return True

    def _live_sensor_report_time(self):
        """Return when the meter itself last published, not when we last read it.

        ``_last_sensor_report_time`` is written by the two control paths that
        reach the grid read. Every cycle that returns earlier - manual mode, an
        operation block, a max-SOC measurement that owns the cycle - leaves it untouched, so it ages while a
        perfectly healthy meter keeps publishing every second. Judging liveness
        on it therefore reports the controller's own early returns as a dead
        meter: a P1 reading once a second raised this repair after five minutes
        of blocked discharge.

        The entity's ``last_reported`` is the meter's own clock, so it answers
        the question this repair asks. It counts only while the state still
        transforms to a reading: ``unavailable``/``unknown`` keeps being
        republished, and treating that as a publication would hide exactly the
        silence this check exists to name.

        A valid reading therefore stamps its own high-water mark, which is what
        the ``unavailable`` branch falls back to. Reaching for the tracked read
        time there would re-open this same bug from the other side: a two-second
        meter reload during a half-hour block would inherit that frozen
        timestamp and report half an hour of silence. This check runs on every
        cycle ahead of the early returns, so the high-water mark keeps up with
        the meter whatever the control path does.
        """
        state = self.hass.states.get(self.consumption_sensor)
        if self._apply_meter_transform(state) is None:
            # Nothing usable right now: report when it was last usable.
            return self._last_valid_meter_publication or self._last_sensor_report_time
        published = self._sensor_report_time(state)
        if published is None:
            return self._last_valid_meter_publication or self._last_sensor_report_time
        previous = self._last_valid_meter_publication
        if previous is None or published > previous:
            self._last_valid_meter_publication = published
        return self._last_valid_meter_publication

    def _sensor_age_seconds(self, sensor_report_time, now=None):
        """Return the real age of the current grid sample."""
        reference_time = now if isinstance(now, datetime) else dt_util.utcnow()
        return max(0.0, (reference_time - sensor_report_time).total_seconds())

    def _sensor_is_within_stale_tolerance(self, sensor_report_time, now=None):
        """Return whether the latest grid sample must remain authoritative."""
        return (
            ChargeDischargeController._sensor_age_seconds(
                self, sensor_report_time, now
            )
            <= self._max_sensor_stale_s
        )

    async def _run_control_cycle(self, now=None):
        """Update the charge/discharge power of the batteries.

        FORK: trace (docs/fork/08-trace.md) - wraps the cycle in one trace record.
        """
        cycle_trace.begin(self)
        try:
            await ChargeDischargeController._run_control_cycle_inner(self, now)
        except BaseException as err:
            cycle_trace.end(self, err)
            raise
        cycle_trace.end(self)

    async def _run_control_cycle_inner(self, now=None):
        """Update the charge/discharge power of the batteries."""
        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("ChargeDischargeController: async_update_charge_discharge started.")

        # === SHUTDOWN CHECK (absolute priority) ===
        # Skip all operations if any coordinator is shutting down (integration unloading)
        if any(c._is_shutting_down for c in self.coordinators):
            return
        self._phase_power_limiter.begin_cycle()
        self._phase_power_limiter.update_degraded_warning()
        # A battery at 100% SOC or a confirmed BMS cutoff (is_battery_full())
        # stops being commanded to charge (see _get_available_batteries below)
        # and therefore never reaches _check_non_delivery's charge path again -
        # the only place that clears the non-responsive tracker via the
        # BMS-full exemption. Without this, a battery that tapered through a
        # brief non-delivery episode on its way to full stays "degraded"
        # forever once idle-full, and a battery_not_delivering Repair it
        # already raised never resolves on its own. Runs ahead of
        # update_repairs() so a battery that just became full this cycle
        # clears before the Repair check below reads it.
        #
        # Gated on the *last recorded reason* being charge-side: a full battery
        # is still a live discharge candidate, and clearing unconditionally
        # would wipe a genuine discharge non-delivery (standby_no_delivery,
        # issue #26) or comms failure (record_comm_failure) every cycle,
        # before it ever reaches exclusion or a Repair of its own.
        weekly_mgr = getattr(self, "_weekly_charge_mgr", None)
        if weekly_mgr is not None:
            for coordinator in self.coordinators:
                if (
                    weekly_mgr.is_battery_full(coordinator)
                    and self._non_responsive.last_reason(coordinator, "").startswith("charge_")
                ):
                    self._non_responsive.clear(coordinator)
        self._non_responsive.update_repairs(
            self.hass,
            getattr(self.config_entry, "entry_id", "") or "",
            self.coordinators,
        )
        # A meter that stopped publishing freezes the command, and neither the
        # slow-sensor repair nor the invalid-state log can see that. Must sit
        # ahead of the manual-mode / block early returns below.
        self._check_main_sensor_liveness(now)

        # === BALANCE MONITOR ===
        # Run before manual mode and PD control checks so readings are never gated
        # by deadband, stale sensor, or any other early return in the control loop.
        if self._balance_monitor is not None:
            for coordinator in self.coordinators:
                await self._balance_monitor.async_process(coordinator)

        # === MANUAL MODE CHECK (highest priority) ===
        # If manual mode is enabled, skip all automatic control logic
        if self.manual_mode_enabled:
            _LOGGER.debug("Manual Mode active - skipping automatic control")
            # Register-based drivers (Marstek) obey the user's force_mode /
            # set_*_power register writes directly, so we just freeze the
            # controller; a v3 that dropped the forced mode is re-asserted below.
            await self._apply_software_manual_setpoints()
            # Do not set batteries to 0 - preserve user's manual settings
            # Do not update PD state - freeze controller state
            self._phase_safety_pending = False
            cycle_trace.put(self, exit="manual_mode")
            return

        # Individual manual batteries remain under their own software setpoints
        # while the rest of the fleet continues through automatic planning.
        apply_manual_setpoints = getattr(self, "_apply_software_manual_setpoints", None)
        if apply_manual_setpoints is not None:
            await apply_manual_setpoints(global_mode=False)

        # === WEEKLY FULL CHARGE REGISTER MANAGEMENT ===
        # Handle register writes and completion detection before PD control.
        # This ensures weekly charge works regardless of active control mode
        await self._weekly_charge_mgr.handle_registers()

        # Refresh all operation blockers before mode dispatch and PD early returns.
        # This makes charge/discharge permission a shared registry instead of a
        # collection of independent flags and one-off checks.
        self._refresh_operation_blockers()

        # Manual time slots take ownership of their batteries before any other
        # control logic runs. Owned batteries are skipped by PD.
        await self._try_apply_manual_slot()

        # Phase staleness is caused by the passage of time, so it cannot depend
        # on receiving another phase event. Check it on every safety cycle and
        # enforce the current envelope before max-SOC early returns
        # can preserve an old command while Grid 0 is unavailable.
        if (
            self._phase_power_limiter.enabled
            and (
                self._phase_safety_pending
                or self._phase_power_limiter.has_degraded_phase()
            )
        ):
            await self._apply_phase_safety_review()

        if await self._max_soc_mgr.handle_measurement():
            self.previous_power = 0
            self.previous_sensor = None
            self.previous_error = 0
            self.last_output_sign = 0
            self.sign_changes = 0
            self._active_discharge_batteries = []
            self._active_charge_batteries = []
            cycle_trace.put(self, exit="max_soc")
            return

        # A dead meter stops automatic control before PD runs blind.
        if await self._apply_dead_meter_fallback():
            cycle_trace.put(self, exit="dead_meter")
            return

        # === Operation blockers: enforce BEFORE deadband / stale early-returns ===
        # Without this guard the deadband and stale-sensor paths could keep a
        # command alive after a feature or user switch blocked that direction.
        if self.previous_power > 0 and self.is_charge_blocked():
            _LOGGER.debug(
                "ChargeDischargeController: Charge block active - stopping charge (was %.0fW)",
                abs(self.previous_power),
            )
            await self._stop_all_batteries_for_block("charge")
            cycle_trace.put(self, exit="charge_block")
            return

        if self.previous_power < 0 and self.is_discharge_blocked():
            _LOGGER.debug(
                "ChargeDischargeController: Discharge block active - stopping discharge (was %.0fW)",
                abs(self.previous_power),
            )
            await self._stop_all_batteries_for_block("discharge")
            cycle_trace.put(self, exit="discharge_block")
            return

        blocked_active_changed = await self._stop_blocked_active_batteries()

        # === Continue with normal PD control ===
        # Before the grid-sensor read: a missing grid sensor returns below, and
        # that is precisely a case this issue has to name.
        self._check_missing_configured_sensors()
        ChargeDischargeController._check_physical_grid_sensor(self)  # FORK: grid view (docs/fork/02-architektur.md)
        consumption_state = self.hass.states.get(self.consumption_sensor)
        sensor_raw = self._apply_meter_transform(consumption_state)
        if sensor_raw is None:
            cycle_trace.put(self, exit="no_sensor")
            self._log_consumption_sensor_issue(consumption_state)
            if self._phase_safety_pending:
                await self._apply_phase_safety_review()
            return
        self._consumption_sensor_issue = None

        # Detect real sensor publications, even when the numeric value is unchanged.
        previous_report_time = self._last_sensor_report_time
        sensor_report_time, sensor_elapsed_s, is_stale = (
            self._track_sensor_report(consumption_state, sensor_raw)
        )
        has_new_control_sample = getattr(self, "_control_sample_is_new", True)
        # A report timestamp drives health/cadence. Only a new transformed value
        # drives the cadence-dependent filter, derivative, P scaling and limiter.

        # FORK: grid view (docs/fork/02-architektur.md) - read the real meter exactly
        # once per cycle, before the freshness check (physical_is_new compares reads).
        physical = ChargeDischargeController._read_physical_grid_w(self)
        self._last_physical_grid_w = physical
        physical_new = physical is not None and self._grid_view.physical_is_new()
        physical_elapsed_s = ChargeDischargeController._track_physical_sample(self, physical)
        peak_review_pending = ChargeDischargeController._peak_review_pending(self, physical)
        cycle_trace.put(
            self, raw=sensor_raw, new=has_new_control_sample, el=sensor_elapsed_s,
            stale=is_stale, phys=physical, phys_new=physical_new,
            paced=getattr(self, "_timer_tick_paced", False),
        )

        # Generic safety recalc on a silent sensor must re-evaluate structural state
        # (SOC/limits/blockers) but must NOT integrate the P term: the grid error is
        # already-acted-on stale data, so a factor-1 P push every 2s tick winds up and
        # ramps the command rail-to-rail on sensors slower than the watchdog (~30s).
        stale_safety_recalc = False
        capacity_protection_must_recheck = (
            self.previous_power < 0
            and self._is_capacity_protection_soc_limited()
        )
        sensor_age_s = (
            self._sensor_age_seconds(sensor_report_time, now)
            if sensor_report_time is not None
            else None
        )
        sensor_within_stale_tolerance = (
            sensor_report_time is None
            or self._sensor_is_within_stale_tolerance(sensor_report_time, now)
        )

        if is_stale and not has_new_control_sample:
            self._stale_cycles += 1
        else:
            self._stale_cycles = 0

        paced_tick = has_new_control_sample and getattr(self, "_timer_tick_paced", False)
        if not self.first_execution and (not has_new_control_sample or paced_tick):
            # last_reported may advance on an identical P1 publication. That is
            # fresh health data, but it is not a new feedback sample for the
            # incremental P/D law. Structural checks above have already run; keep
            # the command unless stale safety or a structural transition requires
            # the downstream limits/availability path to review it.
            if (
                sensor_within_stale_tolerance
                and not capacity_protection_must_recheck
                and not blocked_active_changed
                and not self._phase_safety_pending
                # A quiet meter is not evidence the guards have nothing to do:
                # it can read on target precisely because another regulator is
                # carrying the load. See control/residual_load.guards_pending.
                and not guards_pending(self, sensor_raw)
                and not peak_review_pending  # FORK: grid view (docs/fork/02-architektur.md)
            ):
                if DEBUG_CONTROL_LOOP_DETAIL:
                    _LOGGER.debug(
                        "ChargeDischargeController: No new control sample (age %.1fs/%s), maintaining last command %.1fW",
                        sensor_age_s if sensor_age_s is not None else 0.0,
                        f"{self._max_sensor_stale_s:.1f}s" if sensor_age_s is not None else "unknown age",
                        self.previous_power,
                    )
                if paced_tick:
                    # Leave the sample unconsumed so the next unpaced cycle
                    # still sees it as new and acts on it, with its elapsed
                    # measured from the last consumed report (not 0, which
                    # would freeze the EMA on the old value).
                    self._last_control_sample_value = None
                    self._last_sensor_report_time = previous_report_time
                self._cycle_kept_last_command = True
                cycle_trace.put(self, exit="keep_last")
                return
            # A paced tick that a safety condition needs reviewed runs a normal
            # cycle on its fresh sample: protections never wait for pacing.
            stale_safety_recalc = not paced_tick
            cycle_trace.put(self, recalc=stale_safety_recalc,
                            cp_recheck=capacity_protection_must_recheck,
                            blk_changed=blocked_active_changed,
                            phase_pend=self._phase_safety_pending,
                            peak_pend=peak_review_pending)
            if paced_tick:
                pass
            elif capacity_protection_must_recheck:
                _LOGGER.debug(
                    "ChargeDischargeController: No new control sample but peak shaving is SOC-limited; reviewing discharge %.1fW",
                    self.previous_power,
                )
            elif sensor_age_s is not None and not sensor_within_stale_tolerance:
                _LOGGER.debug(
                    "ChargeDischargeController: Sensor stale for %.1fs. Safety recalculation.",
                    sensor_age_s,
                )
        # Smooth instantaneous spikes with a time-constant EMA (advances only on a real
        # update; a stale recalculation passes elapsed 0 and keeps the last value).
        sensor_filtered = self._filter_grid_sample(
            sensor_raw, 0.0 if not has_new_control_sample else sensor_elapsed_s
        )

        active_target = self.compute_active_target()

        # Use the real filtered meter reading while automatic batteries are
        # charging. A manual grid charge must then count as load so the automatic
        # charge is reduced (for example, 2 kW automatic + 1 kW manual becomes
        # 1 kW automatic). Once the automatic command is idle or discharging,
        # remove only the manual charging contribution: otherwise the intentional
        # manual import would make automatic batteries discharge to compensate it.
        sensor_actual = sensor_filtered
        manual_grid_power = ChargeDischargeController._manual_battery_power_for_grid_feedback(
            self
        )
        if manual_grid_power > 0 and self.previous_power <= 0:
            sensor_actual -= manual_grid_power
            _LOGGER.debug(
                "Ignoring %.0fW manual-battery charging power while automatic batteries are not charging",
                manual_grid_power,
            )

        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("Sensor: raw=%.1fW, filtered=%.1fW", sensor_raw, sensor_filtered)

        # Adjust for excluded/additional devices before dynamic setpoint decisions.
        # Positive adjustment = reduce battery discharge (excluded devices)
        # Negative adjustment = increase battery discharge (additional devices not in home sensor)
        self._resolve_home_consumption_sensor()
        excluded_adjustment = self._external_loads.calculate_adjustment()
        if excluded_adjustment != 0:
            if excluded_adjustment > 0:
                _LOGGER.info("Reducing battery demand by %.1fW (excluded devices)", excluded_adjustment)
            else:
                _LOGGER.info("Increasing battery demand by %.1fW (additional devices)", abs(excluded_adjustment))
            sensor_actual -= excluded_adjustment

        # CAPACITY PROTECTION MODE: When enabled and SOC is below threshold,
        # only discharge to cover consumption above the peak limit. This must run
        # before deadband and first-execution handling, otherwise a previous
        # discharge can be kept alive by an early return.
        active_target, sensor_actual = self._apply_capacity_protection(
            sensor_actual, active_target,
            **({} if physical is None else {"physical_actual": physical}))
        # FORK: grid view (docs/fork/02-architektur.md) - while shaving, the real
        # meter is the controlled variable, so its samples (not the control
        # sensor's) decide freshness and the P/D time base. The EMA above stays
        # on the control sensor and is not touched.
        peak_physical = ChargeDischargeController._track_peak_state(self, physical)  # FORK: review 2/7
        if peak_physical:
            has_new_control_sample = physical_new
            if physical_new:
                stale_safety_recalc = False
                sensor_elapsed_s = physical_elapsed_s
            # FORK: review 5 - the real meter already contains the excluded load.
            self._icp_excluded_protection_w = 0.0
        else:
            sensor_actual = self._apply_icp_excluded_protection(sensor_filtered, sensor_actual, active_target)

        cycle_trace.put_lazy(self, lambda: dict(
            filt=sensor_filtered,
            man=manual_grid_power if self.previous_power <= 0 else 0.0,
            excl=excluded_adjustment, peak_phys=peak_physical,
            icp=getattr(self, "_icp_excluded_protection_w", None),
            act=sensor_actual, tgt=active_target, db=self.deadband,
        ))
        cycle_trace.put_lazy(self, lambda: {"sp": self._format_setpoint_summary_for_log()})
        if self._capacity_protection_force_idle:
            cycle_trace.put(self, exit="cp_force_idle")
            self._capacity_protection_force_idle = False
            _LOGGER.info(
                "Capacity Protection conserving capacity: stopping existing battery command"
            )
            for coordinator in self.coordinators:
                await self._set_battery_power(coordinator, 0, 0)
            self.previous_power = 0
            self.previous_sensor = sensor_actual
            self.previous_error = 0
            self.last_output_sign = 0
            self.sign_changes = 0
            self._active_discharge_batteries = []
            self._active_charge_batteries = []
            return

        # CRITICAL: Check deadband on the automatic-control sensor before the
        # external-load and capacity-protection adjustments below.
        # Deadband is centered around the active target grid power
        # Skip on first_execution: controller hasn't initialized yet; returning here keeps
        # first_execution=True forever when the grid happens to be balanced at startup.
        if (
            not self.first_execution
            and not blocked_active_changed
            and not self._phase_safety_pending
            and abs(sensor_actual - active_target) < self.deadband
            and not guards_pending(self, sensor_actual)
        ):
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug(
                    "ChargeDischargeController: Filtered sensor %.1fW within deadband of target %dW (+/-%dW), no action.",
                    sensor_actual,
                    active_target,
                    self.deadband,
                )
            
            # Reset integral when within deadband to prevent accumulation (only if Ki > 0)
            if self.ki > 0 and self.error_integral != 0.0:
                _LOGGER.info("PD: Resetting integral term (was %.1fW) - system is balanced within deadband", 
                           self.error_integral)
                self.error_integral = 0.0
                self.sign_changes = 0  # Reset oscillation counter
            
            # Update previous_sensor for next cycle
            self.previous_sensor = sensor_actual
            # Keep the derivative reference current while idling in the deadband, so
            # leaving it does not compute Δerror against a stale pre-deadband error
            # over one sample (a derivative kick). Drop the filtered derivative too.
            self.previous_error = sensor_actual - active_target
            self.derivative_filtered = 0.0
            # Drop any armed zero-cross timer: reaching the deadband ends the flip
            # streak, so a much later flip must start its own settle window instead
            # of inheriting a stale timestamp that would let it pass instantly.
            self._zero_cross_since = None
            # Feed the quality metric here too: time on target is part of how
            # well the loop controls. Without it the metric only averages the
            # cycles outside the deadband and overstates the error.
            self._update_pd_quality_metrics(
                sensor_actual - active_target, False, active_target, False
            )
            # NOTE: Do NOT clear load sharing state here. Batteries keep executing
            # their last command during deadband, so the active battery lists must
            # remain accurate for the diagnostic sensor.
            if await self._power_distribution._rebalance_expired_load_sharing_hold(
                grid_w=sensor_actual,
                target_w=active_target,
            ):
                _LOGGER.debug(
                    "Load sharing: expired wall-clock hold released while within deadband"
                )
            cycle_trace.put(self, exit="deadband")
            return
        
        if len(self.coordinators) == 0:
            _LOGGER.debug("ChargeDischargeController: No batteries configured.")
            return

        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("ChargeDischargeController: sensor_actual=%fW, previous_sensor=%s, previous_power=%fW",
                          sensor_actual, self.previous_sensor, self.previous_power)

        # FIRST EXECUTION: Initialize with sensor reading
        if self.first_execution:
            _LOGGER.info("ChargeDischargeController: First execution - initializing with sensor value: %fW (target: %dW)", sensor_actual, active_target)
            self.previous_sensor = sensor_actual
            # Initial power counteracts the difference from target grid power
            self.previous_power = -(sensor_actual - active_target)
            self.derivative_filtered = 0.0  # drop any derivative carried across a mode change
            self.first_execution = False

            # Get available batteries and set initial power
            is_charging = self.previous_power > 0

            # Check time slot restrictions BEFORE sending any power to batteries
            operation_allowed = self._is_operation_allowed(is_charging)
            if not operation_allowed:
                if is_charging:
                    _LOGGER.debug(
                        "ChargeDischargeController: First execution - Charging NOT ALLOWED by blockers [%s], starting at 0W",
                        self._operation_blockers_for_log(True),
                    )
                else:
                    _LOGGER.debug(
                        "ChargeDischargeController: First execution - Discharging NOT ALLOWED by blockers [%s], starting at 0W",
                        self._operation_blockers_for_log(False),
                    )
                self.previous_power = 0
                is_charging = False
                # Initialize PD state at 0
                self.error_integral = 0.0
                self.previous_error = -(sensor_actual - active_target)
                self.last_output_sign = 0
                self.sign_changes = 0
                self._active_discharge_batteries = []
                self._active_charge_batteries = []
                # Set all batteries to 0
                for coordinator in self.coordinators:
                    await self._set_battery_power(coordinator, 0, 0)
                return

            available_batteries = self._get_available_batteries(is_charging)

            if not available_batteries:
                _LOGGER.debug("ChargeDischargeController: No available batteries for initial setup.")
                self._active_discharge_batteries = []
                self._active_charge_batteries = []
                return

            if self.previous_power != 0:
                limit = self._effective_system_capacity(available_batteries, is_charging)
                if is_charging and self.previous_power > limit:
                    self.previous_power = limit
                elif not is_charging and abs(self.previous_power) > limit:
                    self.previous_power = -limit

            # Select batteries via load sharing, then distribute power
            selected_batteries = self._power_distribution._select_batteries_for_operation(abs(self.previous_power), available_batteries, is_charging)
            power_allocation = self._power_distribution._distribute_power_by_limits(abs(self.previous_power), selected_batteries, is_charging)
            requested_initial_power = self.previous_power
            assigned_initial_power = sum(power_allocation.values())
            if self._phase_power_limiter.enabled:
                self.previous_power = (
                    assigned_initial_power if is_charging else -assigned_initial_power
                )

            self._log_power_command_plan(
                phase="initial",
                grid_w=sensor_actual,
                target_w=active_target,
                previous_power_w=0,
                requested_power_w=requested_initial_power,
                is_charging=is_charging,
                available_batteries=available_batteries,
                selected_batteries=selected_batteries,
                power_allocation=power_allocation,
            )

            allocated_batteries = {
                coordinator
                for coordinator, power in power_allocation.items()
                if power > 0
            }
            for coordinator, power in power_allocation.items():
                if power <= 0:
                    continue
                if is_charging:
                    await self._set_battery_power(coordinator, power, 0)
                else:
                    await self._set_battery_power(coordinator, 0, power)

            # Set all other batteries to 0 (non-available + available-but-not-selected)
            for coordinator in self.coordinators:
                if coordinator not in allocated_batteries:
                    await self._set_battery_power(coordinator, 0, 0)

            # Reset PD state for clean start (CRITICAL: clear saturated integral)
            self.error_integral = 0.0
            self.previous_error = -(sensor_actual - active_target)
            self.last_output_sign = 1 if self.previous_power > 0 else (-1 if self.previous_power < 0 else 0)
            self.sign_changes = 0
            self._phase_safety_pending = False
            _LOGGER.info("PD state initialized: previous_error=%.1fW, last_output_sign=%d, integral=0 (cleared)",
                        self.previous_error, self.last_output_sign)
            cycle_trace.put(self, exit="first_exec")
            return

        # SUBSEQUENT EXECUTIONS: Continue with PD control
        # Deadband was already checked on filtered sensor before compensation
        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("ChargeDischargeController: sensor_actual=%fW, UPDATING BATTERIES!",
                          sensor_actual)
        self._refresh_effective_system_capacities()
        
        # PD CONTROLLER: Calculate adjustment based on grid imbalance relative to target
        # error > 0: grid power above target → need to discharge more / charge less
        # error < 0: grid power below target → need to charge more / discharge less
        # active_target was calculated before deadband check (reuse it here)
        error = sensor_actual - active_target
        # FORK: grid view (docs/fork/02-architektur.md) - no derivative kick when
        # the controlled variable switches between control sensor and real meter.
        if peak_physical != getattr(self, "_pd_on_physical", False):
            self._pd_on_physical = peak_physical
            self.previous_error = error
            self.derivative_filtered = 0.0
            self._step_candidate = None  # FORK: review 10 - candidate measured the other variable

        feedforward_fired = False
        if not has_new_control_sample:
            # Safety/structural checks below may still clamp or stop this command,
            # but the incremental controller must not integrate a repeated sample.
            new_power = self.previous_power
        elif self.no_pd_mode_enabled:
            new_power = self._compute_no_pd_new_power(error)
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug(
                    "No-PD direct tracking: error=%.1fW, previous=%.1fW, new=%.1fW",
                    error, self.previous_power, new_power,
                )
        elif (
            not stale_safety_recalc
            # The deadbeat anchors on measured power; a lagging reading (#522)
            # would turn it into a wrong-sized kick, so the PD covers the step.
            and self._delivered_power_reliable()
            and self._check_feedforward_step(error)
        ):
            # Confirmed load step: one deadbeat cycle (measured - error), then the
            # PD resumes fine adjustment. Skips the rate limiter on purpose (a
            # 400W/s clamp would forfeit the burst response) but keeps the
            # directional hysteresis. Runs in the same position as the PD law, so
            # every downstream blocker (time slots, EV, capacity) still applies.
            feedforward_fired = True
            new_power = self._compute_no_pd_new_power(error)
            ff_sign = 1 if new_power > 0 else (-1 if new_power < 0 else 0)
            if (
                self.last_output_sign != 0
                and ff_sign != 0
                and ff_sign != self.last_output_sign
                and abs(new_power) < self.direction_hysteresis
                and abs(error) < self.direction_hysteresis
            ):
                new_power = 0
            # Re-anchor derivative state so the next PD cycle doesn't turn the step
            # into a derivative kick (same pattern as the deadband exit above).
            self.previous_error = error
            self.derivative_filtered = 0.0
            _LOGGER.info(
                "PD feedforward: confirmed load step, deadbeat command %.1fW (error=%.1fW, measured-anchored)",
                new_power, error,
            )
        else:
            new_power = self._compute_pd_new_power(
                error, sensor_elapsed_s, stale_safety_recalc
            )
        # MIXED-FLEET GUARDS: floor the command at the demand another regulator
        # may already be hiding, refuse a discharge into a surplus, and cap a
        # discharge at what the house actually left uncovered. All three measure
        # against the active target rather than zero, so a deliberate negative
        # target (a negative pd_target_grid_power) is a
        # demand they serve rather than one they cancel. Applied here, before
        # every downstream blocker, so time slots and capacity limits keep
        # the last word over them.
        cycle_trace.put_lazy(self, lambda: dict(err=error, law=(
            "hold" if not has_new_control_sample else "nopd" if self.no_pd_mode_enabled
            else "ff" if feedforward_fired else "pd"), p_pd=new_power))
        guarded = apply_guards(self, new_power, sensor_actual)
        cycle_trace.put(self, p_guard=guarded)
        if guarded != new_power:
            # One line per acting cycle. The guards themselves log their reasoning
            # at debug: they are also run over the standing command by
            # guards_pending, so an INFO inside them would print twice.
            _LOGGER.info(
                "Mixed-fleet guards: %.0fW -> %.0fW (target %.0fW)",
                new_power, guarded, self.compute_active_target(),
            )
        new_power = guarded

        # FORK: capacity protection below its SOC threshold allows a discharge only
        # for shaving (incl. the peak exit hold and ICP excluded-device protection).
        # Applied before the zero-cross hold, minimum power and relay guard, so no
        # stage downstream lifts it to a minimum discharge or counts a switch.
        new_power = ChargeDischargeController._apply_capacity_protection_discharge_block(self, new_power)

        # ZERO-CROSS HOLD: a charge<->discharge flip must survive the actuator
        # settle window before it becomes a real opposite-direction command (see
        # _apply_zero_cross_hold). Must run before _apply_min_power so a clamped
        # flip cannot be raised to the minimum charge power.
        new_power = self._apply_zero_cross_hold(
            new_power, error, stale_recalc=stale_safety_recalc
        )
        cycle_trace.put(self, p_zc=new_power)

        # Final commanded direction (feeds last_output_sign at end of cycle). In the
        # PD path the hysteresis inside _compute_pd_new_power already zeroed new_power
        # for a suppressed direction change, so recomputing from new_power matches.
        current_output_sign = 1 if new_power > 0 else (-1 if new_power < 0 else 0)
        
        # Note: last_output_sign and previous_error will be updated at the end of the method
        # This is done conditionally based on whether the operation is restricted by time slots

        new_power = self._apply_min_power(new_power, error)
        cycle_trace.put(self, p_min=new_power)

        new_power = self._apply_relay_dwell(
            new_power, error, stale_recalc=stale_safety_recalc
        )
        cycle_trace.put(self, p_dwell=new_power)
        # FORK: the shut-off dwell holds a running discharge at minimum power and
        # undid the block once (21.11.2026 11:14:58: p_cp=0 -> p_dwell=-30). Re-apply
        # it after the dwell; the relay guard passes a request for 0 unchanged.
        new_power = ChargeDischargeController._apply_capacity_protection_discharge_block(self, new_power)
        # FORK: relay guard (docs/fork/02-architektur.md)
        guard = getattr(self, "_relay_guard", None)
        if guard is not None:
            decision = guard.filter(
                new_power, error, previous_power=self.previous_power,
                min_charge_w=self.min_charge_power, min_discharge_w=self.min_discharge_power,
                peak_bypass=ChargeDischargeController._guard_peak_bypass(self),  # FORK: review 2/9
                stale_recalc=stale_safety_recalc, now=time.monotonic(),
            )
            if decision.power != new_power:
                _LOGGER.debug("Relay guard: %s, %.0fW -> %.0fW (remaining %.0fs)",
                              decision.reason, new_power, decision.power, decision.remaining_s)
            cycle_trace.put_lazy(self, lambda: {
                "rg": f"{decision.reason}:{decision.remaining_s:.0f}s",
                "p_rg": decision.power,
            })
            new_power = decision.power

        # Determine if charging or discharging (before applying restrictions)
        is_charging = new_power > 0
        
        # Check if the operation is allowed based on time slots
        operation_restricted = not self._is_operation_allowed(is_charging)
        if operation_restricted:
            if is_charging:
                _LOGGER.debug(
                    "ChargeDischargeController: Charging NOT ALLOWED by blockers [%s] - controller paused",
                    self._operation_blockers_for_log(True),
                )
            else:
                _LOGGER.debug(
                    "ChargeDischargeController: Discharging NOT ALLOWED by blockers [%s] - controller paused",
                    self._operation_blockers_for_log(False),
                )
            new_power = 0
            is_charging = False  # Reset since we're forcing to 0
            self._active_discharge_batteries = []
            self._active_charge_batteries = []

        # Check EV charger no-telemetry: 5-min full pause then discharge-block mode
        if not operation_restricted:
            ev_pause_active, ev_charging_active = self._external_loads.check_ev_charger_state()
            if ev_pause_active:
                _LOGGER.info(
                    "ChargeDischargeController: EV charger detected – 5-minute battery pause, forcing 0W"
                )
                new_power = 0
                is_charging = False
                self._active_discharge_batteries = []
                self._active_charge_batteries = []
                operation_restricted = True  # Freeze PD state during pause
            elif ev_charging_active and new_power < 0:
                # EV is charging (pause expired) – block discharge, solar charging still allowed
                _LOGGER.info(
                    "ChargeDischargeController: EV charging active – blocking battery discharge"
                )
                new_power = 0
                self._active_discharge_batteries = []
                operation_restricted = True

        # Solar surplus excluded device active: battery may charge but must not discharge.
        # Discharge would cause oscillation because the device adjustment flips sign with
        # previous_power — there is no stable fixed point when device_power > solar_surplus.
        if not operation_restricted and self._solar_surplus_discharge_blocked and new_power < 0:
            _LOGGER.info(
                "ChargeDischargeController: Solar surplus excluded device active – blocking battery discharge"
            )
            new_power = 0
            self._active_discharge_batteries = []
            operation_restricted = True

        # Get available batteries (after checking restrictions to determine correct operation mode)
        available_batteries = self._get_available_batteries(is_charging)
        
        # Apply limits: calculate max total power based on AVAILABLE batteries (not all coordinators)
        # This ensures we only compare against batteries that can actually participate
        if available_batteries:
            max_total_discharge = self._effective_system_capacity(
                available_batteries,
                is_charging=False,
            )
            max_total_charge = self._effective_system_capacity(
                available_batteries,
                is_charging=True,
            )
        else:
            # No batteries available, use zero limits
            max_total_discharge = 0
            max_total_charge = 0
        
        # Clamp new_power to realistic limits (only if not already restricted to 0)
        # Convention: new_power > 0 = charging, new_power < 0 = discharging
        if not operation_restricted and new_power != 0:
            if new_power > max_total_charge:
                new_power = max_total_charge
            elif new_power < -max_total_discharge:
                new_power = -max_total_discharge

        # ICP CONTRACTED-POWER CLAMP: cap battery charging so the projected grid
        # import stays at or below the contracted power, preventing the main breaker
        # from tripping. Uses the real meter reading (sensor_filtered), not the
        # excluded-devices/capacity-protection-adjusted sensor_actual, because the
        # breaker sees total grid flow. Marginal model: shifting battery power by
        # (new_power - previous_power) shifts grid by the same amount (more charge =
        # more import). Only limits charging; never forces a discharge.
        if self.max_contracted_power > 0 and new_power > 0:
            icp_grid = sensor_filtered if physical is None else physical  # FORK: grid view (docs/fork/02-architektur.md)
            charge_import_cap = ChargeDischargeController._icp_charge_import_cap(self, sensor_filtered, physical)
            if new_power > charge_import_cap:
                clamped = max(0.0, charge_import_cap)
                _LOGGER.info(
                    "ICP clamp: limiting charge %.0fW -> %.0fW (grid %.0fW, contracted %.0fW)",
                    new_power, clamped, icp_grid, self.max_contracted_power,
                )
                new_power = clamped

        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("ChargeDischargeController: sensor_actual=%fW, previous_power=%fW, new_power=%fW (available: %d batteries)",
                         sensor_actual, self.previous_power, new_power, len(available_batteries))

        # Refresh the per-battery discharge re-entry latches (min-SOC and
        # stopped-delivering, see _get_available_batteries) every cycle, also
        # while charging. This evaluation used to run as part of the removed
        # grid-at-min-SOC accumulator; keeping it keeps the latch timing as-is.
        self._get_available_batteries(
            is_charging=False,
            include_operation_blocks=False,
        )

        if not available_batteries:
            # NOTE: this path ends the cycle before the end-of-cycle PD state update,
            # so last_output_sign stays latched at the last real flow direction. That
            # is deliberate: the battery may still be ramping down (or the 0W write may
            # not have landed on an unreachable battery), so the next flip must still
            # prove itself against the zero-cross settle window. Issue #117 was the
            # settle timer being cleared underneath that latch, not the latch itself.
            _LOGGER.debug("ChargeDischargeController: No available batteries, setting all to 0.")
            cycle_trace.put(self, exit="no_available", restricted=operation_restricted,
                            p_final=new_power)
            for coordinator in self.coordinators:
                await self._set_battery_power(coordinator, 0, 0)
            self.previous_power = 0
            self.previous_sensor = sensor_actual
            # The base restarts from 0, so the derivative restarts too: a frozen
            # previous_error re-fed the same stale step every blocked cycle and
            # wound D up into a grid charge while importing.
            self.previous_error = error
            self.derivative_filtered = 0.0
            self._active_discharge_batteries = []
            self._active_charge_batteries = []
            # No battery can act: demand outside the deadband is battery-limited, not
            # a tuning fault (surfaced as "battery_limited", keeps the metric clean).
            self._set_pd_limited(abs(error) > self.deadband)
            # Everything is at 0 W here, so there is no headroom in any direction.
            self._set_pd_blocked(self._pd_demand_blocked(error, 0))
            return
        
        # Select batteries via load sharing, then distribute power
        selected_batteries = self._power_distribution._select_batteries_for_operation(abs(new_power), available_batteries, is_charging)
        requested_distributed_power = new_power
        power_allocation = self._power_distribution._distribute_power_by_limits(abs(new_power), selected_batteries, is_charging)
        assigned_power = sum(power_allocation.values())
        # Each battery's share is rounded to 5 W, so up to 2.5 W per battery can
        # go missing without any phase cap; a real cap always cuts whole 5 W steps.
        phase_limited = self._phase_power_limiter.enabled and (
            assigned_power + 2.5 * len(selected_batteries) + 1
            < abs(requested_distributed_power)
        )
        if self._phase_power_limiter.enabled:
            new_power = assigned_power if is_charging else -assigned_power

        self._log_power_command_plan(
            phase=(
                "track" if self.no_pd_mode_enabled
                else "feedforward" if feedforward_fired
                else "pd"
            ),
            grid_w=sensor_actual,
            target_w=active_target,
            previous_power_w=self.previous_power,
            requested_power_w=requested_distributed_power,
            is_charging=is_charging,
            available_batteries=available_batteries,
            selected_batteries=selected_batteries,
            power_allocation=power_allocation,
            operation_restricted=operation_restricted,
        )

        cycle_trace.put(self, exit="written", restricted=operation_restricted,
                        p_final=new_power)
        cycle_trace.put_lazy(self, lambda: dict(
            alloc={c.name: int(p) for c, p in power_allocation.items()},
            cblk=self._format_blockers_for_log(self.get_charge_blockers()),
            dblk=self._format_blockers_for_log(self.get_discharge_blockers()),
        ))
        # Write to selected batteries
        allocated_batteries = {
            coordinator
            for coordinator, power in power_allocation.items()
            if power > 0
        }
        for coordinator, power in power_allocation.items():
            if power <= 0:
                continue
            if is_charging:
                await self._set_battery_power(coordinator, power, 0)
            else:
                await self._set_battery_power(coordinator, 0, power)

        # Set all other batteries to 0 (non-available + available-but-not-selected)
        for coordinator in self.coordinators:
            if coordinator not in allocated_batteries:
                await self._set_battery_power(coordinator, 0, 0)
        
        # Update state for next cycle
        self.previous_power = new_power
        self.previous_sensor = sensor_actual
        self._phase_safety_pending = False
        current_output_sign = 1 if new_power > 0 else (-1 if new_power < 0 else 0)
        
        # CRITICAL: Only update PD controller state if NOT restricted by time slots
        # This prevents false oscillation warnings when controller is paused
        if not operation_restricted:
            # Controller is active - perform oscillation detection and update state
            
            # OSCILLATION DETECTION: Detect if system is oscillating (frequent sign changes)
            # Key principle: Only track oscillations OUTSIDE deadband
            # - Inside deadband: System is stable, fluctuations are acceptable
            # - Outside deadband: Controller is active, sign changes indicate instability
            error_outside_deadband = abs(error) > self.deadband
            sign_changed = False  # captured for the control-quality oscillation metric

            if error_outside_deadband:
                # Error is outside deadband - controller is actively trying to correct
                current_error_sign = 1 if error > 0 else (-1 if error < 0 else 0)

                # Only count sign changes when BOTH current and previous errors were outside deadband
                if current_error_sign != 0 and self.last_error_sign != 0:
                    if current_error_sign != self.last_error_sign:
                        # Sign changed while outside deadband - potential oscillation
                        self.sign_changes += 1
                        sign_changed = True
                        
                        # If too many consecutive sign changes, reset PID to stabilize
                        if self.sign_changes >= self.oscillation_threshold:
                            _LOGGER.debug("PID: Oscillation detected (grid swinging ±%.1fW). Resetting PID state.",
                                          abs(error))
                            self.error_integral = 0.0
                            self.previous_error = 0.0
                            self.sign_changes = 0
                            # Don't return, allow proportional control to continue
                    else:
                        # Same sign, reset counter (system is stable in one direction)
                        if self.sign_changes > 0:
                            _LOGGER.debug("PID: Error sign stable outside deadband, resetting oscillation counter (was %d)", 
                                         self.sign_changes)
                            self.sign_changes = 0
                
                # Update last_error_sign only when outside deadband
                self.last_error_sign = current_error_sign
            else:
                # Inside deadband - reset oscillation counter if any
                # This prevents false positives from small fluctuations within tolerance
                if self.sign_changes > 0:
                    _LOGGER.debug("PID: Back inside deadband (error=%.1fW < ±%dW), resetting oscillation counter (was %d)", 
                                 error, self.deadband, self.sign_changes)
                    self.sign_changes = 0
                # Note: last_error_sign is NOT updated when inside deadband
                # This ensures we only track sign changes that matter (outside deadband)
            # Battery-limited: the PD commanded the most it can in the needed
            # direction but the error persists (battery full/empty, or surplus beyond
            # the charge/discharge rate). Not a tuning fault — flag it so the metric
            # skips it and the sensor reports "battery_limited".
            pd_limited = abs(error) > self.deadband and (
                (error < 0 and new_power >= max_total_charge - 1)
                or (error > 0 and new_power <= -max_total_discharge + 1)
            )
            pd_limited = pd_limited or phase_limited
            self._set_pd_limited(pd_limited)
            # The demand direction can be blocked while the commanded power is 0,
            # which leaves this branch "unrestricted" even though the loop is
            # muzzled. Skip the metric there too, else a blocked charge demand
            # (e.g. a time slot + solar surplus) scores as sluggish tuning.
            self._set_pd_blocked(self._pd_demand_blocked(error, new_power))
            self._update_pd_quality_metrics(
                error, sign_changed, active_target, pd_limited or self._pd_blocked
            )
            self.previous_error = error
            # Keep the last direction of flow across idle cycles so directional
            # hysteresis still applies to the next flip. Zeroing it here made the
            # hysteresis one-shot: a suppressed flip cleared the memory, so the
            # very next cycle allowed any tiny opposite-direction command through.
            if current_output_sign != 0:
                self.last_output_sign = current_output_sign
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug("ChargeDischargeController: PD state updated - previous_error=%.1fW, error_sign=%d, output_sign=%d",
                             self.previous_error, self.last_error_sign, self.last_output_sign)
        else:
            # Controller is paused by restrictions - DO NOT update error tracking
            # This prevents false oscillation detection from natural load fluctuations
            self._set_pd_blocked(True)
            if DEBUG_CONTROL_LOOP_DETAIL:
                _LOGGER.debug("ChargeDischargeController: PD state FROZEN (restricted) - error tracking paused to prevent false oscillation warnings")
        
        if DEBUG_CONTROL_LOOP_DETAIL:
            _LOGGER.debug("ChargeDischargeController: async_update_charge_discharge finished.")


def _migrate_time_slots_v2_to_v3(old_slots: list[dict]) -> list[dict]:
    """Convert legacy slots ({start, end, days, apply_to_charge}) to v3 schema.

    Preserves existing behaviour: apply_to_charge=False slot → discharge whitelist
    only. apply_to_charge=True slot → both directions whitelisted.
    """
    from .const import (
        DEFAULT_SLOT_MODE,
        SLOT_BATTERY_SCOPE_ALL,
    )

    new_slots: list[dict] = []
    for s in old_slots or []:
        if not isinstance(s, dict):
            continue
        apply_to_charge = bool(s.get("apply_to_charge", False))
        new_slots.append({
            "start_time": s.get("start_time", "00:00:00"),
            "end_time": s.get("end_time", "00:00:00"),
            "days": s.get("days", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]),
            "enabled": s.get("enabled", True),
            "battery_scope": SLOT_BATTERY_SCOPE_ALL,
            "allow_charge": apply_to_charge,
            "allow_discharge": True,
            "soc_override_enabled": False,
            "power_override_enabled": False,
            "battery_limits": {},
            "mode": DEFAULT_SLOT_MODE,
        })
    return new_slots


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry versions.

    v1 -> v2: add port to unique_ids and device identifiers.
    v2 -> v3: expand time slots from {apply_to_charge} to per-direction tick schema.
    v3 -> v4: lower PD defaults (Kp 0.65->0.35, Kd 0.5->0.3) for installs still on
              the old defaults, to curb overshoot under the cadence-independent loop.
    v4 -> v5: re-enable cell voltage sensors that the integration disabled before
              they were switched to enabled_by_default. Only re-enables entities
              disabled by the integration, leaving user-disabled ones untouched.
    v5 -> v6: drop the legacy household_consumption_sensor key. It was removed from
              the config flow; home consumption is now always derived (grid +
              battery AC + solar). Leaving it in data let it keep driving consumption
              calculations on old installs.
    v6 -> v7: fix the Home Consumption aggregate sensor from the incorrect
              marstek_venus_system_system_home_consumption (double "system") to
              marstek_venus_system_home_consumption. Renames both the unique_id
              and the registry entity_id (the entity_id is not derived from the
              unique_id, so it must be renamed explicitly).
    v7 -> v8: charge hysteresis is now mandatory. Per battery: force
              enable_charge_hysteresis=True; batteries that already had it enabled
              keep their configured percent; batteries that had it off (or unset)
              get the MIN_CHARGE_HYSTERESIS_PERCENT floor. Any value is clamped up
              to the floor so SOC drift can't shrink the deadband into chatter.
    v8 -> v9: re-key system-level entity unique_ids off the config entry_id and
              onto a stable "marstek_venus_system_" prefix, and heal the duplicate
              entities the Omnibattery domain migration created (orphan + `_2`).
    v9 -> v10: rename config entry title to "Omnibattery".
    v10 -> v11: add the disabled-by-default three-phase protection schema and
                normalize an empty battery phase on existing batteries.
    v11 -> v12: distinguish MPPT-capable Venus A/D hardware from installations
                that actually have panels connected; preserve existing behaviour.
    v12 -> v16: only touched keys of features this fork has removed. The steps were dropped; their stored
                keys are simply ignored. The version still advances to 16.
    """
    if entry.version >= 16:
        return True

    new_data = dict(entry.data)

    if entry.version < 2:
        from homeassistant.helpers import entity_registry as er
        from homeassistant.helpers import device_registry as dr

        pairs: list[tuple[str, int]] = []
        for battery in entry.data.get("batteries", []):
            host = battery.get(CONF_HOST)
            port = battery.get(CONF_PORT)
            if host is not None and port is not None:
                pairs.append((host, port))

        if not pairs:
            _LOGGER.error("Cannot migrate to v2: no batteries with host/port in entry.data")
            return False

        new_prefixes = {f"{h}_{p}_" for h, p in pairs}

        @callback
        def _update_unique_id(entity_entry):
            uid = entity_entry.unique_id
            if not uid or any(uid.startswith(np) for np in new_prefixes):
                return None
            for h, p in pairs:
                old_prefix = f"{h}_"
                if uid.startswith(old_prefix):
                    return {"new_unique_id": f"{h}_{p}_" + uid[len(old_prefix):]}
            return None

        await er.async_migrate_entries(hass, entry.entry_id, _update_unique_id)

        dev_reg = dr.async_get(hass)
        for h, p in pairs:
            device = get_entry_device(dev_reg, (DOMAIN, h), entry.entry_id)
            if device:
                dev_reg.async_update_device(device.id, new_identifiers={(DOMAIN, f"{h}_{p}")})

        _LOGGER.info("Marstek: migrated config entry to version 2 (unique_ids now include port)")

    if entry.version < 3:
        old_slots = entry.data.get("no_discharge_time_slots", []) or []
        new_slots = _migrate_time_slots_v2_to_v3(old_slots)
        new_data["no_discharge_time_slots"] = new_slots
        _LOGGER.info(
            "Marstek: migrated config entry to version 3 (expanded %d time slot(s) to per-direction schema)",
            len(new_slots),
        )

    if entry.version < 4:
        # Lower the PD defaults to reduce overshoot. Only migrate installs still on
        # the OLD defaults (or that never set Kp/Kd); hand-tuned values are left
        # untouched. Require BOTH Kp and Kd to match the old defaults so a user who
        # customized only one is treated as tuned.
        OLD_DEFAULT_PD_KP = 0.65
        OLD_DEFAULT_PD_KD = 0.5
        on_old_kp = abs(float(new_data.get(CONF_PD_KP, OLD_DEFAULT_PD_KP)) - OLD_DEFAULT_PD_KP) < 1e-9
        on_old_kd = abs(float(new_data.get(CONF_PD_KD, OLD_DEFAULT_PD_KD)) - OLD_DEFAULT_PD_KD) < 1e-9
        if on_old_kp and on_old_kd:
            new_data[CONF_PD_KP] = DEFAULT_PD_KP
            new_data[CONF_PD_KD] = DEFAULT_PD_KD
            _LOGGER.info(
                "Marstek: migrated config entry to version 4 (PD defaults Kp->%.2f, Kd->%.2f)",
                DEFAULT_PD_KP, DEFAULT_PD_KD,
            )
        else:
            _LOGGER.info(
                "Marstek: config entry to version 4 (PD gains hand-tuned, left as Kp=%s, Kd=%s)",
                new_data.get(CONF_PD_KP), new_data.get(CONF_PD_KD),
            )

    if entry.version < 5:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)
        targets = ("_max_cell_voltage", "_min_cell_voltage")
        count = 0
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if (
                ent.disabled_by is er.RegistryEntryDisabler.INTEGRATION
                and ent.unique_id.endswith(targets)
            ):
                ent_reg.async_update_entity(ent.entity_id, disabled_by=None)
                count += 1
        _LOGGER.info(
            "Marstek: migrated config entry to version 5 "
            "(re-enabled %d integration-disabled cell voltage sensor(s))",
            count,
        )

    if entry.version < 6:
        if new_data.pop(CONF_HOUSEHOLD_CONSUMPTION_SENSOR, None) is not None:
            _LOGGER.info(
                "Marstek: migrated config entry to version 6 "
                "(removed legacy household_consumption_sensor; home consumption is "
                "now always derived from grid + battery AC + solar)"
            )

    if entry.version < 7:
        from homeassistant.helpers import entity_registry as er

        @callback
        def _fix_home_consumption_uid(entity_entry):
            if entity_entry.unique_id == "marstek_venus_system_system_home_consumption":
                return {"new_unique_id": "marstek_venus_system_home_consumption"}
            return None

        await er.async_migrate_entries(hass, entry.entry_id, _fix_home_consumption_uid)

        # Renaming the unique_id does not change the registry entity_id (HA keeps
        # them separate), so rename it explicitly too — otherwise the entity keeps
        # the double-"system" id and the dashboard Home node stays unavailable.
        # Skip if the target id is already taken (e.g. the user renamed it by hand).
        ent_reg = er.async_get(hass)
        old_eid = "sensor.marstek_venus_system_system_home_consumption"
        new_eid = "sensor.marstek_venus_system_home_consumption"
        if ent_reg.async_get(old_eid) is not None and ent_reg.async_get(new_eid) is None:
            ent_reg.async_update_entity(old_eid, new_entity_id=new_eid)

        _LOGGER.info(
            "Marstek: migrated config entry to version 7 "
            "(fixed Home Consumption sensor unique_id + entity_id: removed duplicate 'system' prefix)"
        )

    if entry.version < 8:
        migrated_batteries = []
        for battery in new_data.get("batteries", []):
            nb = dict(battery)
            was_enabled = nb.get("enable_charge_hysteresis", False)
            nb["enable_charge_hysteresis"] = True
            # Preserve a previously-configured percent; otherwise apply the floor.
            pct = nb.get("charge_hysteresis_percent") if was_enabled else MIN_CHARGE_HYSTERESIS_PERCENT
            try:
                pct = int(pct)
            except (TypeError, ValueError):
                pct = MIN_CHARGE_HYSTERESIS_PERCENT
            nb["charge_hysteresis_percent"] = max(MIN_CHARGE_HYSTERESIS_PERCENT, pct)
            migrated_batteries.append(nb)
        new_data["batteries"] = migrated_batteries
        _LOGGER.info(
            "Marstek: migrated config entry to version 8 "
            "(charge hysteresis now mandatory; min %d%%, configured values preserved)",
            MIN_CHARGE_HYSTERESIS_PERCENT,
        )

    if entry.version < 9:
        # System-level entities used to key their unique_id on the config
        # entry_id (`f"{entry.entry_id}_{key}"`). The Omnibattery domain
        # migration creates a NEW config entry (new entry_id), so those
        # unique_ids changed and HA registered duplicates: the old entities
        # became orphans (device_id None, stale entry_id prefix) while the new
        # ones got bumped to `_2` entity_ids. The dashboard (which matches by
        # translation_key) then grabbed the dead orphan and rendered blanks.
        #
        # Fix: re-key these unique_ids to a STABLE prefix ("marstek_venus_system_",
        # matching the aggregate sensors so future entry recreation can't churn
        # them) and heal any duplicates. The entry_id is either a 26-char ULID
        # (current HA) or a 32-char lowercase hex (`uuid4().hex`, older installs
        # that migrate 2.0.x -> 3.0.0), so the logical key is everything after the
        # first `<entry_id>_`. Per-battery entities key on device_key (host_port)
        # and aggregates already use the stable prefix, so neither matches the
        # entry_id pattern and both are left untouched.
        import re as _re
        from homeassistant.helpers import entity_registry as er

        STABLE = "marstek_venus_system_"
        _entry_key = _re.compile(r"^(?:[0-9A-Z]{26}|[0-9a-f]{32})_(.+)$")

        ent_reg = er.async_get(hass)
        by_key: dict[str, list] = {}
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            m = _entry_key.match(ent.unique_id)
            if m:
                by_key.setdefault(m.group(1), []).append(ent)

        healed = 0
        for key, cands in by_key.items():
            new_uid = f"{STABLE}{key}"

            # Keeper = the entity to preserve: prefer one bound to the device
            # AND on the current entry_id (the live `_2` in the already-migrated
            # case), then any device-bound one (fresh-migrant moved entity),
            # else current-entry, else first.
            keeper = next(
                (c for c in cands
                 if c.device_id and c.unique_id.startswith(entry.entry_id + "_")),
                None,
            ) or next((c for c in cands if c.device_id), None) \
              or next((c for c in cands if c.unique_id.startswith(entry.entry_id + "_")), None) \
              or cands[0]

            # Delete the duplicate orphan(s); the first one holds the clean
            # (non-suffixed) entity_id the keeper should reclaim.
            clean_eid = None
            for o in cands:
                if o is keeper:
                    continue
                if clean_eid is None:
                    clean_eid = o.entity_id
                ent_reg.async_remove(o.entity_id)

            update: dict = {}
            if not ent_reg.async_get_entity_id(keeper.domain, DOMAIN, new_uid):
                update["new_unique_id"] = new_uid
            if (clean_eid and clean_eid != keeper.entity_id
                    and ent_reg.async_get(clean_eid) is None):
                update["new_entity_id"] = clean_eid
            if update:
                ent_reg.async_update_entity(keeper.entity_id, **update)
                healed += 1

        _LOGGER.info(
            "Omnibattery: migrated config entry to version 9 "
            "(re-keyed %d system entity unique_id(s) to stable prefix; "
            "removed post-rebrand duplicates)",
            healed,
        )

    if entry.version < 10:
        _LOGGER.info("Omnibattery: migrated config entry to version 10 (renamed title to Omnibattery)")

    if entry.version < 11:
        # Never infer or enable a physical phase layout during migration.  Empty
        # assignments are normalized so a later activation can collect them in
        # one atomic options-flow step.
        new_data[CONF_THREE_PHASE_ENABLED] = DEFAULT_THREE_PHASE_ENABLED
        new_data["batteries"] = [
            {
                **dict(battery),
                CONF_BATTERY_PHASE: normalize_battery_phase(
                    battery.get(CONF_BATTERY_PHASE)
                ),
            }
            for battery in new_data.get("batteries", [])
        ]
        _LOGGER.info(
            "Omnibattery: migrated config entry to version 11 "
            "(three-phase protection disabled; battery phases normalized)",
        )

    if entry.version < 12:
        new_data["batteries"] = [
            {
                **dict(battery),
                CONF_DC_PV_CONNECTED: bool(
                    battery.get(
                        CONF_DC_PV_CONNECTED,
                        battery.get(CONF_BATTERY_VERSION) in ("vA", "vD"),
                    )
                ),
            }
            for battery in new_data.get("batteries", [])
        ]
        _LOGGER.info(
            "Omnibattery: migrated config entry to version 12 "
            "(recorded whether Venus A/D MPPT panels are connected)",
        )

    hass.config_entries.async_update_entry(
        entry,
        title="Omnibattery",
        data=new_data,
        version=16,
    )
    return True


_LEGACY_ACTIVE_BALANCE_PREFIX = "active_balance_mode_"
_LEGACY_ACTIVE_BALANCE_MARKERS = (
    "active_balance_mode_enabled",
    "active_balance_mode_started_ts",
    "active_balance_mode_phase",
    "active_balance_mode_saved_max_soc",
)


def _legacy_active_balance_keys(battery_config: dict) -> set[str]:
    """Return persisted keys owned by the removed integrated balance runner."""
    return {
        key for key in battery_config
        if isinstance(key, str) and key.startswith(_LEGACY_ACTIVE_BALANCE_PREFIX)
    }


def _legacy_active_balance_is_running(battery_config: dict) -> bool:
    """Return whether legacy data indicates an interrupted active-balance run."""
    return any(
        bool(battery_config.get(key))
        for key in _LEGACY_ACTIVE_BALANCE_MARKERS
        if key != "active_balance_mode_saved_max_soc"
    ) or battery_config.get("active_balance_mode_saved_max_soc") is not None


def _legacy_active_balance_entity_id(hass: HomeAssistant, coordinator) -> str | None:
    """Resolve the old per-battery switch entity from its stable unique ID."""
    from homeassistant.helpers import entity_registry as er

    return er.async_get(hass).async_get_entity_id(
        "switch",
        DOMAIN,
        f"{coordinator.device_key}_active_balance_mode",
    )


async def _dismiss_legacy_active_balance_notifications(
    hass: HomeAssistant, coordinator, battery_config: dict | None = None
) -> None:
    """Dismiss notifications emitted by the removed integrated runner."""
    notification_ids = {
        f"marstek_active_balance_mode_{suffix}_{coordinator.device_key}"
        for suffix in ("start", "result")
    }
    # Later releases added the run timestamp (and, for results, the reason) to
    # the notification ID.  The timestamp is still in the legacy config when
    # an interrupted run is migrated, so dismiss those IDs too.
    if battery_config:
        started_ts = battery_config.get("active_balance_mode_started_ts")
        if started_ts:
            sanitized_device = "".join(
                char if char.isalnum() else "_"
                for char in str(coordinator.device_key)
            ).strip("_") or "unknown"
            sanitized_ts = "".join(
                char if char.isalnum() else "_" for char in str(started_ts)
            ).strip("_") or "unknown"
            notification_ids.add(
                f"{NOTIFICATION_ID_PREFIX}marstek_active_balance_mode_start_"
                f"{sanitized_device}_{sanitized_ts}"
            )
            reason = battery_config.get("active_balance_mode_completion_reason")
            if reason:
                sanitized_reason = "".join(
                    char if char.isalnum() else "_" for char in str(reason)
                ).strip("_") or "unknown"
                notification_ids.add(
                    f"{NOTIFICATION_ID_PREFIX}marstek_active_balance_mode_result_"
                    f"{sanitized_device}_{sanitized_ts}_{sanitized_reason}"
                )

    for notification_id in notification_ids:
        await hass.services.async_call(
            "persistent_notification",
            "dismiss",
            {"notification_id": notification_id},
        )


async def _async_migrate_legacy_active_balance(
    hass: HomeAssistant,
    entry: ConfigEntry,
    controller,
    coordinators: list,
) -> None:
    """Remove the old active-balance state after a safe manual handoff.

    A stale, inactive record is metadata-only and can be removed immediately.
    An interrupted run is different: it may have left a temporary 100% charge
    cutoff behind. The battery is therefore acquired through the existing manual
    ownership path, idled and verified, restored, and released before its legacy
    keys and entity are deleted. Failures deliberately retain both the legacy
    record and manual ownership so setup can retry without guessing the hardware
    state.
    """
    if not coordinators:
        return

    original_data = entry.data
    batteries = [dict(item) for item in original_data.get("batteries", [])]
    data_changed = False

    for coordinator in coordinators:
        index = next(
            (
                idx
                for idx, battery in enumerate(batteries)
                if battery.get(CONF_HOST) == coordinator.host
                and battery.get(CONF_PORT) == coordinator.port
                and battery.get("slave_id", 1) == coordinator.slave_id
            ),
            None,
        )
        if index is None:
            continue

        battery = batteries[index]
        legacy_keys = _legacy_active_balance_keys(battery)
        old_entity_id = _legacy_active_balance_entity_id(hass, coordinator)
        if not legacy_keys and not old_entity_id:
            continue

        legacy_notification_config = dict(battery)
        active = _legacy_active_balance_is_running(battery)
        if active and not getattr(coordinator, "is_available", True):
            # An interrupted run is handed off through the hardware (idle,
            # verify, restore), which an unreachable battery cannot do: the
            # failure path would latch manual mode into the entry and freeze the
            # battery once it returns. Keep the legacy record and migrate it on
            # the reload that follows the battery answering again.
            _LOGGER.info(
                "[%s] Deferring the integrated active-balance handoff: the battery is unreachable",
                coordinator.name,
            )
            continue
        if not active:
            for key in legacy_keys:
                battery.pop(key, None)
            if old_entity_id:
                from homeassistant.helpers import entity_registry as er

                er.async_get(hass).async_remove(old_entity_id)
            await _dismiss_legacy_active_balance_notifications(
                hass, coordinator, legacy_notification_config
            )
            data_changed = data_changed or bool(legacy_keys)
            _LOGGER.info(
                "[%s] Removed stale integrated active-balance state",
                coordinator.name,
            )
            continue

        already_manual = bool(
            getattr(coordinator, CONF_BATTERY_MANUAL_MODE_ENABLED, False)
        )
        acquired_manual = False
        try:
            # Re-enter the ownership path even when another manual owner is
            # already present.  The old runner may have left a non-zero
            # hardware command behind, and the ownership helper is the one
            # place that performs the verified zero-power handoff.
            await controller._set_battery_manual_mode(coordinator, True)
            acquired_manual = not already_manual

            battery[CONF_BATTERY_MANUAL_MODE_ENABLED] = True
            battery["manual_force_mode"] = "None"
            battery["manual_set_charge_power"] = 0
            battery["manual_set_discharge_power"] = 0

            saved_max_soc = battery.get("active_balance_mode_saved_max_soc")
            if saved_max_soc is not None:
                try:
                    saved_max_soc_f = float(saved_max_soc)
                except (TypeError, ValueError) as err:
                    raise HomeAssistantError(
                        f"Invalid legacy max SOC {saved_max_soc!r}"
                    ) from err
                if not math.isfinite(saved_max_soc_f) or not 12 <= saved_max_soc_f <= 100:
                    raise HomeAssistantError(
                        f"Legacy max SOC out of range: {saved_max_soc!r}"
                    )
                restored_max_soc = int(round(saved_max_soc_f))
                coordinator.max_soc = restored_max_soc
                if coordinator.capabilities.hardware_soc_cutoff:
                    cutoff_ok = await coordinator.set_charge_cutoff(restored_max_soc)
                    if not cutoff_ok:
                        raise HomeAssistantError(
                            f"Could not restore charge cutoff to {restored_max_soc}%"
                        )
                battery["max_soc"] = restored_max_soc

            if acquired_manual:
                await controller._set_battery_manual_mode(coordinator, False)
                battery[CONF_BATTERY_MANUAL_MODE_ENABLED] = False
                battery["manual_force_mode"] = "None"
                battery["manual_set_charge_power"] = 0
                battery["manual_set_discharge_power"] = 0

            for key in legacy_keys:
                battery.pop(key, None)
            if old_entity_id:
                from homeassistant.helpers import entity_registry as er

                er.async_get(hass).async_remove(old_entity_id)
            await _dismiss_legacy_active_balance_notifications(
                hass, coordinator, legacy_notification_config
            )
            data_changed = True
            _LOGGER.info(
                "[%s] Migrated interrupted integrated active-balance state safely",
                coordinator.name,
            )
        except Exception as err:
            # The manual flag is the safety boundary. Keep it asserted even if
            # restoring the cutoff or releasing the ownership failed.
            coordinator.battery_manual_mode_enabled = True
            battery[CONF_BATTERY_MANUAL_MODE_ENABLED] = True
            battery["manual_force_mode"] = "None"
            battery["manual_set_charge_power"] = 0
            battery["manual_set_discharge_power"] = 0
            data_changed = True
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": (
                        f"{NOTIFICATION_ID_PREFIX}active_balance_migration_"
                        f"{coordinator.device_key}"
                    ),
                    "title": f"{coordinator.name}: active balance migration paused",
                    "message": (
                        "The old integrated active-balance run could not be migrated "
                        f"safely ({err}). The battery is held in manual mode at 0 W. "
                        "Keep it available and reload Omnibattery to retry."
                    ),
                },
            )
            _LOGGER.error(
                "[%s] Could not migrate integrated active-balance state; "
                "manual ownership retained: %s",
                coordinator.name,
                err,
            )

    if data_changed:
        new_data = dict(entry.data)
        new_data["batteries"] = batteries
        hass.config_entries.async_update_entry(entry, data=new_data)


async def _setup_batteries_per_link(batteries, setup_battery):
    """Run ``setup_battery(index, config)`` for every battery, concurrently per link.

    Startup then costs the slowest battery, not the sum of all of them (#560:
    four proxied batteries held HA ~70 s). Batteries behind the same link (a
    gateway with several slave ids, a single-slot V3/EW11B port, one serial
    port) stay sequential: concurrent connects would fight over the one TCP
    slot and stretch the retry ladder. Returns coordinators in config order.
    """
    from .const import CONF_SERIAL_PORT

    links: dict[tuple, list] = {}
    for battery_index, battery_config in enumerate(batteries):
        link = (
            battery_config.get(CONF_HOST),
            battery_config.get(CONF_PORT),
            battery_config.get(CONF_SERIAL_PORT),
        )
        links.setdefault(link, []).append((battery_index, battery_config))

    ready: dict = {}

    async def _setup_link(members):
        for battery_index, battery_config in members:
            ready[battery_index] = await setup_battery(battery_index, battery_config)

    results = await asyncio.gather(
        *(_setup_link(members) for members in links.values()),
        return_exceptions=True,
    )
    failure = next((r for r in results if isinstance(r, BaseException)), None)
    if failure is not None:
        # The other links kept connecting while one failed; release them so the
        # setup retry does not find their single TCP slots still held.
        for coordinator in ready.values():
            with contextlib.suppress(Exception):
                await coordinator.disconnect()
        raise failure
    return [ready[i] for i in sorted(ready)]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Omnibattery from a config entry.

    A setup that raises or is cancelled after a battery connected is never
    unloaded, so its connections stayed open. A Marstek v3 or a single-slot
    bridge (EW11B) accepts one Modbus TCP client, and that orphaned one refused
    every later setup and reload until Home Assistant restarted. Each battery
    coordinator is released here before the failure is passed on.
    """
    opened: list = []
    # FORK: trace (docs/fork/08-trace.md) - own file under <config>/omnibattery_trace/
    with contextlib.suppress(Exception):
        await hass.async_add_executor_job(
            cycle_trace.setup_file, hass.config.path(cycle_trace.TRACE_DIR_NAME)
        )
    try:
        return await _async_setup_entry(hass, entry, opened)
    except BaseException:
        for coordinator in opened:
            with contextlib.suppress(Exception):
                coordinator.set_shutting_down(True)
                await coordinator.disconnect()
        raise


async def _async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, opened: list) -> bool:
    """Set up the entry; every battery coordinator created is added to ``opened``."""
    hass.data.setdefault(DOMAIN, {})

    # Register the sidebar dashboard panel (once per HA instance, non-blocking).
    await _async_register_frontend_panel(hass, entry)

    # Migration: Add default version for existing installations
    from .const import (
        CONF_BATTERY_VERSION, DEFAULT_VERSION, CONF_SLAVE_ID, DEFAULT_SLAVE_ID,
        CONF_SERIAL_PORT, CONF_RS485_GATEWAY,
    )

    # Only Marstek batteries are supported. An entry written by a build that
    # still knew other brands cannot be set up; say so instead of failing
    # somewhere deep in the coordinator.
    for battery_config in entry.data["batteries"]:
        brand = battery_config.get("brand", "marstek")
        if brand != "marstek":
            _LOGGER.error(
                "Battery %s is configured with the unsupported brand '%s'; only "
                "Marstek batteries are supported. Remove that battery from the "
                "integration configuration.",
                battery_config.get(CONF_NAME, "?"), brand,
            )
            raise ConfigEntryError(
                f"Battery {battery_config.get(CONF_NAME, '?')} uses the unsupported "
                f"brand '{brand}'; only Marstek batteries are supported"
            )

    for battery_config in entry.data["batteries"]:
        if CONF_BATTERY_VERSION not in battery_config:
            battery_config[CONF_BATTERY_VERSION] = DEFAULT_VERSION
            _LOGGER.info("Migrated %s to %s (default for existing installations)",
                        battery_config[CONF_NAME], DEFAULT_VERSION)

    # Backfill feature enable keys no longer written by the config/options flows.
    # The feature switch/slider entities are gated on key *presence*, so entries
    # predating a feature need the key for its dashboard controls to exist. A
    # missing key means the feature was never enabled → backfill False (no
    # behavior change); system power limits keep their legacy value-derived
    # default.
    _backfill = {
        _key: False
        for _key in (
            CONF_ENABLE_TEMP_CHARGE_LIMIT,
            CONF_CAPACITY_PROTECTION_ENABLED,
            CONF_CAPACITY_PROTECTION_EXCLUDED_DEVICES,
        )
        if _key not in entry.data
    }
    if CONF_ENABLE_SYSTEM_POWER_LIMITS not in entry.data:
        _backfill[CONF_ENABLE_SYSTEM_POWER_LIMITS] = (
            (entry.data.get(CONF_SYSTEM_MAX_CHARGE_POWER, 0) or 0) > 0
            or (entry.data.get(CONF_SYSTEM_MAX_DISCHARGE_POWER, 0) or 0) > 0
        )
    if _backfill:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **_backfill})
        _LOGGER.info("Backfilled feature enable keys: %s", _backfill)

    # Persist a copy of the config so a full integration delete stays recoverable
    # (see config_backup.py). Survives a config-entry deletion that the seamless
    # domain migration cannot, because it can't grab a deleted entry.
    from .config_backup import async_save_config_backup
    await async_save_config_backup(hass)

    from .backup_discharge_store import async_get_backup_discharge_store
    backup_discharge_store = await async_get_backup_discharge_store(hass)

    # A MAC shared by several batteries belongs to a Modbus gateway, not to a
    # battery; publishing it would merge them into one registry device.
    entry_macs = publishable_macs(entry.data["batteries"])

    async def _setup_battery(battery_index, battery_config):
        coordinator = MarstekVenusDataUpdateCoordinator(
            hass,
            name=battery_config[CONF_NAME],
            host=battery_config[CONF_HOST],
            port=battery_config[CONF_PORT],
            slave_id=battery_config.get(CONF_SLAVE_ID, DEFAULT_SLAVE_ID),
            consumption_sensor=entry.data["consumption_sensor"],
            battery_version=battery_config.get(CONF_BATTERY_VERSION, DEFAULT_VERSION),
            max_charge_power=battery_config["max_charge_power"],
            max_discharge_power=battery_config["max_discharge_power"],
            device_max_charge_power=battery_config.get("device_max_charge_power"),
            device_max_discharge_power=battery_config.get("device_max_discharge_power"),
            ems_version=battery_config.get("ems_version"),
            max_soc=battery_config["max_soc"],
            min_soc=battery_config["min_soc"],
            charge_hysteresis_percent=battery_config.get(
                "charge_hysteresis_percent", DEFAULT_CHARGE_HYSTERESIS_PERCENT
            ),
            backup_offgrid_threshold=battery_config.get("backup_offgrid_threshold", 50),
            allow_charge=battery_config.get("allow_charge", True),
            allow_discharge=battery_config.get("allow_discharge", True),
            full_charge_voltage_taper_enabled=battery_config.get(
                CONF_FULL_CHARGE_VOLTAGE_TAPER_ENABLED,
                DEFAULT_FULL_CHARGE_VOLTAGE_TAPER_ENABLED,
            ),
            backup_discharge_store=backup_discharge_store,
            backup_discharge_store_key=f"{entry.entry_id}:{battery_index}",
            serial_port=battery_config.get(CONF_SERIAL_PORT) or None,
            rs485_gateway=battery_config.get(CONF_RS485_GATEWAY, False),
            battery_manual_mode_enabled=battery_config.get(
                CONF_BATTERY_MANUAL_MODE_ENABLED, False
            ),
            dc_pv_connected=battery_config.get(
                CONF_DC_PV_CONNECTED,
                battery_config.get(CONF_BATTERY_VERSION) in ("vA", "vD"),
            ),
            mac=entry_macs[battery_index],
        )
        # Physical phase is metadata for the safety limiter only.  It is never
        # used as an input to the global Grid 0 controller.
        coordinator.phase = normalize_battery_phase(
            battery_config.get(CONF_BATTERY_PHASE)
        )

        # Restore persisted RS485 user preference and store entry reference for future persistence
        coordinator._config_entry = entry
        coordinator.rs485_user_disabled = battery_config.get("rs485_user_disabled", False)
        coordinator.battery_manual_mode_enabled = bool(
            battery_config.get(CONF_BATTERY_MANUAL_MODE_ENABLED, False)
        )
        coordinator.battery_capacity_kwh = battery_config.get("battery_capacity_kwh", 0.0)
        # Restore the user's configured ceilings into the normalized coordinator
        # model. ``effective_max_*`` then applies the physical/device cap without
        # destroying the value selected by the user.
        coordinator.configured_max_charge_power = battery_config.get(
            "user_max_charge_power",
            battery_config.get("max_charge_power", coordinator.configured_max_charge_power),
        )
        coordinator.configured_max_discharge_power = battery_config.get(
            "user_max_discharge_power",
            battery_config.get("max_discharge_power", coordinator.configured_max_discharge_power),
        )

        # Manual-control state.
        coordinator.manual_force_mode = battery_config.get("manual_force_mode", "None")
        coordinator.manual_set_charge_power = min(
            battery_config.get("manual_set_charge_power", 0),
            coordinator.effective_max_charge_power,
        )
        coordinator.manual_set_discharge_power = min(
            battery_config.get("manual_set_discharge_power", 0),
            coordinator.effective_max_discharge_power,
        )
        # Seed the live display from the persisted manual targets until the first
        # control cycle refreshes them.
        coordinator.commanded_charge_power = coordinator.manual_set_charge_power
        coordinator.commanded_discharge_power = coordinator.manual_set_discharge_power
        coordinator._shadow_selects = {
            k[len("shadow_select_"):]: v
            for k, v in battery_config.items()
            if k.startswith("shadow_select_")
        }

        opened.append(coordinator)

        # Connect and fetch initial data
        try:
            connected = await coordinator.connect()
            if not connected:
                # V3 batteries / Modbus bridges (e.g. EW11B) accept only one TCP
                # connection; the slot from the previous session may not be released
                # yet on restart. Retry with escalating delays before giving up.
                for _delay in (2.0, 5.0, 10.0):
                    _LOGGER.warning(
                        "Initial connection to %s failed, retrying in %.0fs...",
                        coordinator.host, _delay,
                    )
                    await asyncio.sleep(_delay)
                    connected = await coordinator.connect()
                    if connected:
                        break
        except Exception as e:
            # A driver whose connect() raises (an HTTP session, an entity lookup)
            # is no different from one returning False: the battery is not there.
            _LOGGER.warning(
                "Connecting to %s at %s:%s raised %s",
                battery_config[CONF_NAME], coordinator.host, coordinator.port, e,
            )
            # A close on a link that never came up can raise in its own right
            # (an MQTT publish, a second client). Letting that escape would put
            # the entry in ERROR with no retry, which is the failure this whole
            # branch exists to avoid.
            with contextlib.suppress(Exception):
                await coordinator.disconnect()
            connected = False

        if not connected:
            # A battery that does not answer is set up unreachable instead of
            # failing the config entry. Raising ConfigEntryNotReady here tore the
            # whole system down over one switched-off battery: every other
            # battery, the controller and the dashboard went with it, and the
            # entry stayed in a setup-retry loop for as long as the device stayed
            # off. This battery instead starts unreachable, and the coordinator
            # reloads the entry once the device answers (the connect-time entity
            # definitions and the initial hardware configuration write can only
            # be redone by re-running setup) — see the coordinator's
            # _schedule_setup_reload_if_deferred.
            _LOGGER.warning(
                "Battery %s at %s:%s did not answer during setup; it starts unreachable "
                "and the integration reloads once it responds",
                battery_config[CONF_NAME], coordinator.host, coordinator.port,
            )
            coordinator.reload_entry_when_reachable = True
            # _consecutive_failures is what every consumer gates on to tell an
            # unreachable battery from an idle one (non_responsive_battery_names,
            # the non_responsive_batteries sensor, diagnostics). A battery that
            # never answered starts counted, so it is visibly unreachable before
            # the first poll instead of looking healthy until then.
            coordinator._consecutive_failures = 1
            # Every consumer of coordinator.data assumes setup ran a first
            # refresh, so hand them the same empty snapshot a battery whose reads
            # all failed produces. Assigned rather than pushed through
            # async_set_updated_data: nothing was read, so this is not an update.
            coordinator.data = {}
            return coordinator

        try:
            # Enable RS485 Control Mode first (required to apply configuration changes)
            # Only done during integration setup/reload, not repeated during runtime
            # Skip if the user explicitly disabled RS485 via the switch.
            if coordinator.rs485_user_disabled:
                _LOGGER.info("Skipping RS485 enable for %s (user disabled)", battery_config[CONF_NAME])
            else:
                _LOGGER.info("Enabling RS485 Control Mode for %s (only on initial setup)", battery_config[CONF_NAME])
                if coordinator.capabilities.has_rs485_control:
                    await coordinator.set_rs485_control(True)
                    await asyncio.sleep(0.1)

            # Write initial configuration values to the battery: hardware SOC
            # cut-offs (v2 only) + max charge/discharge power caps. The driver
            # owns which registers exist for this version and the scaling.
            #
            max_charge_power = int(battery_config["max_charge_power"])
            max_discharge_power = int(battery_config["max_discharge_power"])

            _LOGGER.info("Writing initial configuration for %s (%s): max_soc=%d%%, min_soc=%d%%, max_charge=%dW, max_discharge=%dW",
                       battery_config[CONF_NAME], coordinator.battery_version,
                       battery_config["max_soc"], battery_config["min_soc"],
                       max_charge_power, max_discharge_power)

            await coordinator.apply_config(
                max_soc_pct=battery_config["max_soc"],
                min_soc_pct=battery_config["min_soc"],
                max_charge_power_w=max_charge_power,
                max_discharge_power_w=max_discharge_power,
            )

            # Manually trigger first refresh and wait for it
            await coordinator.async_request_refresh()
            # Give a moment for the data to be processed
            await asyncio.sleep(0.5)
        except Exception as e:
            # Disconnect on any setup error
            await coordinator.disconnect()
            raise ConfigEntryNotReady(f"Failed to set up {coordinator.host}: {e}") from e

        return coordinator

    coordinators = await _setup_batteries_per_link(entry.data["batteries"], _setup_battery)

    # Set up the charge/discharge controller BEFORE storing in hass.data
    # This allows the controller to register itself in hass.data[DOMAIN]["pid_controller"]
    controller = ChargeDischargeController(hass, coordinators, entry.data["consumption_sensor"], entry)
    _check_ev_charger_type_notice(hass, entry)

    # Calculated sensors that need a short Recorder recovery query (the daily
    # charge/discharge counter migration) share one entry-owned, cancellable
    # coordinator. Attach it before platform setup starts.
    from .tracking.backfill import RecorderBackfillCoordinator
    recorder_coordinator = RecorderBackfillCoordinator(hass, entry)
    controller._recorder_coordinator = recorder_coordinator
    for coordinator in coordinators:
        coordinator._omnibattery_backfill_coordinator = recorder_coordinator

    from .infra.external_loads import ExternalLoads
    controller._external_loads = ExternalLoads(hass, entry, controller)

    from .control.power_distribution import PowerDistribution
    controller._power_distribution = PowerDistribution(hass, entry, controller)
    controller._phase_power_limiter.update_manual_mode_warning(
        entry.entry_id,
        controller.manual_mode_enabled,
    )

    # Remove the former integrated active-balance runner only after all
    # coordinators and the manual ownership path are ready. Interrupted legacy
    # runs are migrated with a verified zero-power handoff before their state is
    # discarded.
    await _async_migrate_legacy_active_balance(hass, entry, controller, coordinators)

    # Restore weekly charge completion state from previous session
    await controller._weekly_charge_mgr.load_state()

    # Set up the control safety timer and store unsub callbacks for manual cancellation during unload.
    # Each unsub is registered twice: stored in hass.data so async_unload_entry can cancel
    # the timers early (before platform teardown), and via entry.async_on_unload so HA cleans
    # up on setup failure. The state-change tracker's unsub raises on a second call
    # (list.remove(x): x not in list), so wrap every unsub to be call-once.
    def _call_once(unsub):
        done = False

        def _wrapped():
            nonlocal done
            if not done:
                done = True
                unsub()

        return _wrapped

    unsub_control = _call_once(async_track_time_interval(
        hass, controller.schedule_control_cycle, timedelta(seconds=2.0)
    ))
    entry.async_on_unload(unsub_control)

    # Event-driven control: also run the control cycle the instant the grid
    # consumption sensor publishes a new value, so PD reacts at the sensor's
    # native cadence instead of waiting for the next safety-timer tick. The
    # state-reported listener also covers publications whose value and attributes
    # are unchanged. The timer above stays as a watchdog (runs the time-based
    # subsystems and forces a safety recalculation if the sensor goes silent).
    # Overlapping triggers are serialized by the controller's _control_lock.
    @callback
    def _on_consumption_changed(event):
        if event.data.get("entity_id") != controller.consumption_sensor:
            return
        # Record the publication before scheduling control.  A cycle can be busy
        # with battery I/O when the next meter update arrives; measuring cadence
        # only when that cycle eventually samples hass.states would turn a fast P1
        # into a false 65-second sensor.
        controller._observe_consumption_report(event)
        # Do not forward the Event as `now`; the handler expects datetime|None.
        controller.schedule_control_cycle()

    unsub_consumption = _call_once(async_track_state_change_event(
        hass, controller.consumption_sensor_ids, _on_consumption_changed
    ))
    entry.async_on_unload(unsub_consumption)

    # Newer Home Assistant versions emit EVENT_STATE_REPORTED for a publication
    # that leaves the state unchanged. Use it for exact cadence tracking only:
    # last_reported is health data, not a new feedback sample for the incremental
    # controller. Older versions simply keep the changed-state listener and the
    # control-loop fallback above.
    unsub_consumption_reported = None
    track_state_report_event = getattr(
        event_helpers, "async_track_state_report_event", None
    )
    if track_state_report_event is not None:
        @callback
        def _on_consumption_reported(event):
            controller._observe_consumption_report(event)

        unsub_consumption_reported = _call_once(track_state_report_event(
            hass, controller.consumption_sensor_ids, _on_consumption_reported
        ))
        entry.async_on_unload(unsub_consumption_reported)

    # FORK: grid view (docs/fork/02-architektur.md), P4 - the real meter also
    # schedules a cycle, but only when peak shaving can act on it (SOC-limited
    # or active). Deliberately no cadence/stale/dead-meter observation: that
    # stays on the control sensor (frozen values: GridView.latch_frozen).
    unsub_physical_grid = None
    physical_grid_sensor = entry.data.get(_fork_const.CONF_PHYSICAL_GRID_SENSOR)
    if physical_grid_sensor and physical_grid_sensor not in controller.consumption_sensor_ids:
        @callback
        def _on_physical_grid_changed(_event):
            if controller.offgrid_mode_enabled and controller.offgrid_power_sensor:
                return
            # FORK: review 6 - only while peak shaving can act on the real meter
            # (the ICP clamp reads it in the next regular cycle anyway).
            if controller.capacity_protection_enabled and (
                controller._is_capacity_protection_soc_limited()
                or controller._capacity_protection_active
            ):
                controller._physical_trigger_pending = True
                controller.schedule_control_cycle()

        unsub_physical_grid = _call_once(async_track_state_change_event(
            hass, [physical_grid_sensor], _on_physical_grid_changed
        ))
        entry.async_on_unload(unsub_physical_grid)

    # Phase meters are safety inputs only. Their events trigger an immediate
    # review but deliberately do not enter the main-sensor cadence detector or
    # alter sensor_actual / active_target / Grid 0. Subscribe even when the
    # protection switch is currently off so enabling it from the dashboard does
    # not require an integration reload; the limiter ignores them while off.
    #
    # They schedule the cycle exactly like the grid-meter event does (no `now`),
    # so CONF_PD_MIN_CYCLE_INTERVAL paces them too. Passing a timestamp marked
    # them as the periodic safety timer, which is never gated - three phase
    # sensors on a 1 Hz P1 meter then drove several ungated control cycles per
    # second, each one a set-point write burst, and on a slow bridge (Elfin EW11) the queue overflowed and the writes never
    # reached the battery (issue #452). Nothing is lost by pacing them:
    # _phase_safety_pending stays set until a cycle services it, and the 2 s
    # safety timer runs ungated regardless.
    phase_sensors = list(
        dict.fromkeys(
            entry.data.get(key)
            for key in (
                CONF_PHASE_1_CURRENT_SENSOR,
                CONF_PHASE_2_CURRENT_SENSOR,
                CONF_PHASE_3_CURRENT_SENSOR,
            )
            if entry.data.get(key)
        )
    )
    unsub_phase = None
    unsub_phase_reported = None
    if phase_sensors:
        @callback
        def _on_phase_sensor_changed(_event):
            controller._phase_safety_pending = True
            controller.schedule_control_cycle()

        unsub_phase = _call_once(
            async_track_state_change_event(
                hass,
                phase_sensors,
                _on_phase_sensor_changed,
            )
        )
        entry.async_on_unload(unsub_phase)

        if track_state_report_event is not None:
            @callback
            def _on_phase_sensor_reported(event):
                controller._phase_safety_pending = True
                controller.schedule_control_cycle()

            unsub_phase_reported = _call_once(
                track_state_report_event(
                    hass,
                    phase_sensors,
                    _on_phase_sensor_reported,
                )
            )
            entry.async_on_unload(unsub_phase_reported)

    # Set up balance monitor. This is always enabled so users always get
    # battery health history from top-voltage balance measurements.
    from .tracking.balance_monitor import BalanceMonitor
    balance_monitor = BalanceMonitor(hass, entry, controller)
    await balance_monitor.async_setup()
    for coordinator in coordinators:
        await balance_monitor.async_restore_coordinator(coordinator)
    controller._balance_monitor = balance_monitor

    from .tracking.blueprint_measurement import (
        async_register_blueprint_balance_measurement_listener,
    )
    unsub_blueprint_measurement = _call_once(
        async_register_blueprint_balance_measurement_listener(
            hass,
            coordinators,
            balance_monitor,
        )
    )
    entry.async_on_unload(unsub_blueprint_measurement)

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinators": coordinators,
        "controller": controller,
        "unsub_control": unsub_control,
        "unsub_consumption": unsub_consumption,
        "unsub_consumption_reported": unsub_consumption_reported,
        "unsub_phase": unsub_phase,
        "unsub_phase_reported": unsub_phase_reported,
        "unsub_physical_grid": unsub_physical_grid,  # FORK: grid view (docs/fork/02-architektur.md)
        "unsub_blueprint_measurement": unsub_blueprint_measurement,
        "balance_monitor": balance_monitor,
    }

    # Listen for config entry updates so config entities refresh their state
    async def _async_update_listener(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        """Handle config entry updates (from Options Flow or config entities)."""
        if is_reload_pending(hass, updated_entry.entry_id) or getattr(
            controller, "_unloading", False
        ):
            _LOGGER.debug(
                "Ignoring stale config update callback for entry %s",
                updated_entry.entry_id,
            )
            return
        _LOGGER.debug("Config entry updated, hot-reloading controller parameters")
        if controller:
            controller.update_pd_parameters()
        # Keep the recovery copy in sync with the latest options.
        from .config_backup import async_save_config_backup
        await async_save_config_backup(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Refresh once the platforms have populated the entity registry. On a fresh
    # install the early registration above cannot yet resolve the per-device
    # Enabled switches; this second pass supplies their final (rename-safe) IDs
    # so runtime toggles immediately override the persisted panel fallback.
    await _async_register_frontend_panel(hass, entry)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data:
        coordinators = data.get("coordinators", [])
        controller = data.get("controller")
        recorder_coordinator = getattr(controller, "_recorder_coordinator", None)

        # Stop producers first. Set the guard synchronously before removing
        # trackers so a callback already queued by HA cannot create a new task
        # while the entry is being torn down.
        if controller is not None:
            controller._unloading = True

        # Cancel periodic timers/callbacks before waiting for long work. The
        # coordinator owns its 1.5-second update interval; there is no second
        # explicit refresh loop.
        for key in (
            "unsub_control",
            "unsub_consumption",
            "unsub_consumption_reported",
            "unsub_phase",
            "unsub_phase_reported",
            "unsub_physical_grid",  # FORK: grid view (docs/fork/02-architektur.md)
            "unsub_blueprint_measurement",
        ):
            if unsub := data.get(key):
                unsub()

        # Invalidate lifecycle generations and await every task that can still
        # touch the entry before any platform/hardware teardown starts.
        if controller is not None:
            stop_tasks = getattr(controller, "async_stop_background_tasks", None)
            if callable(stop_tasks):
                await stop_tasks()
        if recorder_coordinator is not None:
            await recorder_coordinator.async_cancel()

        # Set shutdown flag on all coordinators to suppress expected errors.
        for coordinator in coordinators:
            coordinator.set_shutting_down(True)

        # Give callbacks owned by older HA versions a short chance to observe
        # the shutdown flag after their entry task has been cancelled.
        await asyncio.sleep(0.3)

    # Unload platforms (removes entities)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Write shutdown registers and disconnect (no more interference from timers)
    if data := hass.data.get(DOMAIN, {}).get(entry.entry_id):
        coordinators = data.get("coordinators", [])

        _LOGGER.info("Shutting down integration - stopping all battery operations")
        for coordinator in coordinators:
            try:
                # Skip shutdown writes if device was unreachable to avoid blocking
                # on TCP connection timeout (~10s per register write attempt)
                if not coordinator._is_connected:
                    _LOGGER.info(
                        "Skipping shutdown writes for %s - device was not connected",
                        coordinator.name,
                    )
                    continue

                # Skip batteries that are actively providing offgrid backup power
                # (backup switch ON and ac_offgrid_power exceeds threshold, or sensor unavailable)
                if coordinator.data and _backup_switch_enabled(
                    coordinator.data.get("backup_function")
                ):
                    ac_offgrid = coordinator.data.get("ac_offgrid_power")
                    if ac_offgrid is None or ac_offgrid > coordinator.backup_offgrid_threshold:
                        _LOGGER.info("%s: Skipping shutdown writes - backup function active with offgrid load", coordinator.name)
                        continue

                # Set all power commands to 0 (idle) via the driver. standby()
                # paces its own writes — the client's inter-message pacing is
                # suppressed during shutdown.
                _LOGGER.info("Setting %s to standby mode", coordinator.name)
                await coordinator.standby()

                # Disable RS485 Control Mode (return control to battery's internal logic)
                _LOGGER.info("Disabling RS485 control mode for %s", coordinator.name)
                if coordinator.capabilities.has_rs485_control:
                    await coordinator.set_rs485_control(False)
                    await asyncio.sleep(0.1)

                _LOGGER.info("%s: Shutdown complete - all control registers reset", coordinator.name)
            except Exception as e:
                _LOGGER.error("Error shutting down battery %s: %s", coordinator.name, e)

        # Disconnect from all coordinators. One driver failing to close must
        # not skip the others, nor the persistence below.
        results = await asyncio.gather(
            *[c.disconnect() for c in coordinators], return_exceptions=True
        )
        for coordinator, result in zip(coordinators, results):
            if isinstance(result, BaseException):
                _LOGGER.error(
                    "Error disconnecting %s: %r", coordinator.name, result
                )

        if unload_ok:
            hass.data[DOMAIN].pop(entry.entry_id, None)

    # Remove the sidebar panel only when no config entries remain, and only
    # once the platforms really unloaded (a failed unload keeps the entry).
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]
    if unload_ok and not remaining:
        _async_unregister_frontend_panel(hass)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removal of stale battery devices via the HA UI.

    Returns True when the device is not associated with any currently
    configured battery, letting the user delete orphaned devices left
    behind after the battery count was reduced or a battery's host/port
    changed.
    """
    from .const import CONF_SLAVE_ID, DEFAULT_SLAVE_ID

    active_identifiers: set[tuple[str, str]] = {(DOMAIN, "marstek_venus_system")}
    for battery in config_entry.data.get("batteries", []):
        host = battery.get(CONF_HOST)
        port = battery.get(CONF_PORT)
        if host and port:
            # Must match MarstekVenusDataUpdateCoordinator.device_key: slave id 1
            # keeps the historical {host}_{port} form, others get a suffix.
            slave_id = battery.get(CONF_SLAVE_ID, DEFAULT_SLAVE_ID)
            device_key = f"{host}_{port}" if slave_id == 1 else f"{host}_{port}_{slave_id}"
            active_identifiers.add((DOMAIN, device_key))

    return not (device_entry.identifiers & active_identifiers)
