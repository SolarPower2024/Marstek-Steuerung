"""Sensor platform for the Omnibattery integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .infra.clock import local_now
from .infra.entity_naming import english_entity_id, system_entity_id, SYSTEM_UNIQUE_ID_PREFIX
from .const import (
    DOMAIN,
    EFFICIENCY_SENSOR_DEFINITIONS,
    STORED_ENERGY_SENSOR_DEFINITIONS,
    CYCLE_SENSOR_DEFINITIONS,
    SOLAR_POWER_SENSOR_DEFINITIONS,
    BATTERY_CELL_POWER_SENSOR_DEFINITIONS,
    SLOT_BATTERY_SCOPE_ALL,
    DEFAULT_SLOT_MODE,
    DEFAULT_SLOT_ALLOW_CHARGE,
    DEFAULT_SLOT_ALLOW_DISCHARGE,
)
from .infra.coordinator import MarstekVenusDataUpdateCoordinator
from .drivers.base import has_connected_mppt_pv
from .energy import (
    BACKUP_DISCHARGING_ENERGY_KEY,
    effective_total_discharging_energy,
)
from .tracking.home_load import read_total_solar_power_kw
from .sensors.aggregate_sensors import AGGREGATE_SENSOR_DEFINITIONS, SYSTEM_BATTERY_CELL_POWER_DEFINITION, MarstekVenusAggregateSensor, SystemAlarmSensor, PdControlQualitySensor
from .sensors.aggregate_sensors import RelayGuardSensor, PeakGridSourceSensor  # FORK: diagnostic sensors
from .sensors.calculated_sensors import (
    MarstekVenusEfficiencySensor,
    MarstekVenusStoredEnergySensor,
    MarstekVenusCycleSensor,
    MarstekVenusSolarPowerSensor,
    MarstekVenusBatteryCellPowerSensor,
    CumulativeDailyEnergySensor,
    CUMULATIVE_DAILY_ENERGY_SENSOR_DEFINITIONS,
)

_LOGGER = logging.getLogger(__name__)


def _remove_obsolete_solar_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    coordinators: list[MarstekVenusDataUpdateCoordinator],
) -> None:
    """Remove PV entities that no longer belong to the configured topology.

    Entity setup is additive from Home Assistant's point of view: omitting a
    definition on reload does not remove an old registry entry. Clean Venus A/D
    calculated PV entities when the user declares that no panels are connected.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    for coordinator in coordinators:
        if (
            getattr(coordinator.capabilities, "has_mppt_pv", False)
            and not has_connected_mppt_pv(coordinator)
        ):
            for suffix in ("solar_power", "battery_cell_power"):
                unique_id = f"{coordinator.device_key}_{suffix}"
                entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
                registered = registry.async_get(entity_id) if entity_id else None
                if registered and registered.config_entry_id == config_entry.entry_id:
                    registry.async_remove(entity_id)

    # The system aggregate is only meaningful when at least one connected
    # battery contributes independent PV. Existing installations may still
    # have this registry entry after upgrading from 1.4.0b4; remove it so the
    # configured external sensor remains the sole source for AC-only systems.
    has_independent_solar = any(
        bool(
            has_connected_mppt_pv(coordinator)
            or getattr(
                getattr(coordinator, "capabilities", None),
                "has_solar_telemetry",
                False,
            )
        )
        for coordinator in coordinators
    )
    if not has_independent_solar:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{SYSTEM_UNIQUE_ID_PREFIX}solar_power"
        )
        registered = registry.async_get(entity_id) if entity_id else None
        if registered and registered.config_entry_id == config_entry.entry_id:
            registry.async_remove(entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinators: list[MarstekVenusDataUpdateCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]

    _remove_obsolete_solar_entities(hass, entry, coordinators)

    entities = []

    # Add individual battery sensors. The driver owns the per-platform split, so
    # use its sensor_definitions directly (same pattern as number.py).
    for coordinator in coordinators:
        for definition in coordinator.sensor_definitions:
            entities.append(MarstekVenusSensor(coordinator, definition))

    # Add aggregate sensors. Created even for a single-battery system so the
    # "Marstek Venus System" device never exposes `unavailable` entities — with
    # one battery each aggregate simply mirrors that battery's value.
    for definition in AGGREGATE_SENSOR_DEFINITIONS:
        entities.append(MarstekVenusAggregateSensor(coordinators, definition, entry, hass))

    # System alarm sensor — only for batteries that expose alarm/fault registers (v2)
    alarm_coordinators = [c for c in coordinators if c.capabilities.has_alarm_registers]
    if alarm_coordinators:
        entities.append(SystemAlarmSensor(alarm_coordinators))

    # Add calculated sensors (efficiency, stored energy, cycle count) per battery
    for coordinator in coordinators:
        for definition in EFFICIENCY_SENSOR_DEFINITIONS:
            entities.append(MarstekVenusEfficiencySensor(coordinator, definition))
        for definition in STORED_ENERGY_SENSOR_DEFINITIONS:
            entities.append(MarstekVenusStoredEnergySensor(coordinator, definition))
        for definition in CYCLE_SENSOR_DEFINITIONS:
            entities.append(MarstekVenusCycleSensor(coordinator, definition))
        if not coordinator.capabilities.has_daily_energy_counters:
            for definition in CUMULATIVE_DAILY_ENERGY_SENSOR_DEFINITIONS:
                entities.append(CumulativeDailyEnergySensor(coordinator, definition))
        # DC-coupled PV total + solar-corrected battery power exist only on
        # units that expose independent DC-coupled PV telemetry. Venus D/A
        # exposes individual MPPT inputs.
        if has_connected_mppt_pv(coordinator):
            for definition in SOLAR_POWER_SENSOR_DEFINITIONS:
                entities.append(MarstekVenusSolarPowerSensor(coordinator, definition))
            for definition in BATTERY_CELL_POWER_SENSOR_DEFINITIONS:
                entities.append(MarstekVenusBatteryCellPowerSensor(coordinator, definition))

    # Add discharge window diagnostic sensor (always, even without slots)
    entities.append(DischargeWindowSensor(hass, entry))

    # Add active batteries diagnostic sensor. The controller updates its
    # load-sharing tracking even for a single battery (see
    # _select_batteries_for_operation), so this reflects charging/discharging/idle
    # instead of staying unavailable.
    controller = hass.data[DOMAIN][entry.entry_id].get("controller")
    if controller:
        entities.append(ActiveBatteriesSensor(hass, entry, controller, coordinators))

    # Add the three-phase protection status and per-phase diagnostic sensor.
    if controller:
        entities.append(ThreePhaseProtectionSensor(hass, entry, controller))

    # Add weekly full charge status sensor (when weekly charge is enabled)
    if controller and controller.weekly_full_charge_enabled:
        entities.append(WeeklyFullChargeSensor(hass, entry, controller))

    # Add integration status sensor (always, when controller is present)
    if controller:
        entities.append(IntegrationStatusSensor(hass, entry, controller))

    # Add non-responsive batteries sensor (always, when controller is present)
    if controller:
        entities.append(NonResponsiveBatteriesSensor(hass, entry, controller, coordinators))

    # Add PD control-quality diagnostic sensor (feeds the tuning-profile feedback)
    if controller:
        entities.append(PdControlQualitySensor(controller))
        # FORK: relay guard + peak grid source diagnostics
        entities.append(RelayGuardSensor(controller))
        entities.append(PeakGridSourceSensor(controller))

    has_solar_telemetry = any(
        bool(
            has_connected_mppt_pv(c)
            or getattr(
                getattr(c, "capabilities", None), "has_solar_telemetry", False
            )
        )
        for c in coordinators
    )
    # Live total solar power (external sensor + battery-reported DC PV). Only
    # useful when a battery actually has DC-coupled PV; without it the sensor
    # would just mirror the external sensor.
    if controller and has_solar_telemetry:
        entities.append(SystemSolarPowerSensor(controller))
    # Signed system battery power (+charge / -discharge). Always present so the
    # flow-diagram battery node and SOC card blocks can link to a single signed
    # aggregate instead of the unsigned system_charge_power. MPPT is included when
    # available; non-MPPT systems fall back to battery_power per coordinator.
    entities.append(MarstekVenusAggregateSensor(coordinators, SYSTEM_BATTERY_CELL_POWER_DEFINITION, entry, hass))

    async_add_entities(entities)

    # Balance monitor sensors (registered separately so they get their own setup call)
    from .sensors import balance_sensors as _balance_sensors
    await _balance_sensors.async_setup_entry(hass, entry, async_add_entities)


class MarstekVenusSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Marstek Venus sensor."""

    def __init__(
        self, coordinator: MarstekVenusDataUpdateCoordinator, definition: dict
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.definition = definition
        
        # Set entity attributes
        self._attr_has_entity_name = True
        self._attr_translation_key = definition["key"]
        self._attr_unique_id = f"{coordinator.device_key}_{definition['key']}"
        self.entity_id = english_entity_id("sensor", coordinator.name, definition["key"])
        self._attr_device_class = definition.get("device_class")
        self._attr_state_class = definition.get("state_class")
        self._attr_native_unit_of_measurement = definition.get("unit")
        self._attr_icon = definition.get("icon")
        self._attr_entity_registry_enabled_default = definition.get("enabled_by_default", True)
        if definition.get("category") == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if "precision" in definition and (definition.get("unit") or definition.get("state_class")):
            self._attr_suggested_display_precision = definition["precision"]
        self._attr_should_poll = False

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        key = self.definition["key"]
        value = self.coordinator.data.get(key)
        if key == "total_discharging_energy":
            corrected = effective_total_discharging_energy(self.coordinator.data)
            if corrected is not None:
                value = round(corrected, self.definition.get("precision", 2))
        
        if value is None:
            return None
        
        # Map numeric values to state names if available
        if "states" in self.definition:
            states = self.definition["states"]
            if value in states:
                return states[value]
            # Coordinators may store ints as floats after scale/round; try int key.
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                return value
            return states.get(ivalue, value)
        
        # For bit-described values, show which bits are active
        if "bit_descriptions" in self.definition:
            active_bits = []
            bit_descriptions = self.definition["bit_descriptions"]
            
            # Check bits based on data type
            max_bits = 64 if self.definition.get("data_type") == "uint64" else 32
            for bit_pos in range(max_bits):
                if value & (1 << bit_pos):
                    if bit_pos in bit_descriptions:
                        active_bits.append(bit_descriptions[bit_pos])
            
            if active_bits:
                return ", ".join(active_bits)
            else:
                return "No active alarms/faults"
        
        return value

    @property
    def extra_state_attributes(self):
        """Surface the driver's model label on the SOC sensor for the panel chip.

        The device-registry model is hardcoded "Venus", so the per-battery model
        (Marstek version) rides along here on the always-present
        battery_soc entity the panel already reads.
        """
        if self.definition["key"] == "total_discharging_energy":
            backup = self.coordinator.data.get(BACKUP_DISCHARGING_ENERGY_KEY)
            if backup is None:
                return None
            return {
                "hardware_discharging_energy": self.coordinator.data.get(
                    "total_discharging_energy"
                ),
                "backup_discharging_energy": backup,
            }
        if self.definition["key"] != "battery_soc":
            return None
        model = getattr(self.coordinator.driver, "model_label", None)
        attributes = {"model": model} if model else {}
        if getattr(self.coordinator.capabilities, "has_mppt_pv", False):
            attributes["dc_pv_connected"] = has_connected_mppt_pv(self.coordinator)
        return attributes or None

    @property
    def device_info(self):
        """Return device information."""
        return self.coordinator.battery_device_info


class DischargeWindowSensor(SensorEntity):
    """Diagnostic sensor showing whether we are currently inside an allowed discharge window."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the discharge window sensor."""
        self.hass = hass
        self.entry = entry

        self._attr_has_entity_name = True
        self._attr_translation_key = "discharge_window"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}discharge_window"
        self.entity_id = system_entity_id("sensor", "discharge_window")
        self._attr_icon = "mdi:clock-check-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = True

    @property
    def native_value(self) -> str:
        """Return the current discharge window status."""
        from datetime import time as dt_time

        all_slots = self.entry.data.get("no_discharge_time_slots", [])
        # Only slots that govern discharge define a discharge window. Charge-only
        # slots (allow_discharge=False) leave discharge unrestricted.
        enabled_slots = [
            s for s in all_slots
            if s.get("enabled", True) and s.get("allow_discharge", DEFAULT_SLOT_ALLOW_DISCHARGE)
        ]

        if not enabled_slots:
            return "no_slots"

        now = local_now()
        current_time = now.time()
        current_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]

        for i, slot in enumerate(enabled_slots):
            if current_day not in slot.get("days", []):
                continue
            try:
                start_time = dt_time.fromisoformat(slot["start_time"])
                end_time = dt_time.fromisoformat(slot["end_time"])
            except Exception:
                continue
            if start_time <= current_time <= end_time:
                return "active"

        return "inactive"

    @property
    def extra_state_attributes(self) -> dict:
        """Return configuration details of all time slots."""
        all_slots = self.entry.data.get("no_discharge_time_slots", [])
        enabled_slots = [
            s for s in all_slots
            if s.get("enabled", True) and s.get("allow_discharge", DEFAULT_SLOT_ALLOW_DISCHARGE)
        ]
        attrs = {
            "slots_configured": len(enabled_slots),
        }

        # Find active slot number
        from datetime import time as dt_time
        now = local_now()
        current_time = now.time()
        current_day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
        active_slot = None

        # Index over the full slot list so the number matches the panel's
        # "Franja N" (switch time_slot_{index}); skip slots that don't govern
        # discharge inline rather than enumerating the filtered list.
        for i, slot in enumerate(all_slots):
            if not slot.get("enabled", True) or not slot.get("allow_discharge", DEFAULT_SLOT_ALLOW_DISCHARGE):
                continue
            if current_day not in slot.get("days", []):
                continue
            try:
                start_time = dt_time.fromisoformat(slot["start_time"])
                end_time = dt_time.fromisoformat(slot["end_time"])
            except Exception:
                continue
            if start_time <= current_time <= end_time:
                active_slot = i + 1
                break

        attrs["active_slot"] = active_slot

        # Add details for each configured slot (all slots, not just enabled)
        for i, slot in enumerate(all_slots):
            n = i + 1
            days = slot.get("days", [])
            days_str = ", ".join(d.capitalize() for d in days) if days else "None"
            attrs[f"slot_{n}_schedule"] = f"{slot.get('start_time', '??')}-{slot.get('end_time', '??')}"
            attrs[f"slot_{n}_days"] = days_str
            attrs[f"slot_{n}_enabled"] = slot.get("enabled", True)
            attrs[f"slot_{n}_mode"] = slot.get("mode", DEFAULT_SLOT_MODE)
            attrs[f"slot_{n}_battery_scope"] = slot.get("battery_scope", SLOT_BATTERY_SCOPE_ALL)
            attrs[f"slot_{n}_allow_charge"] = slot.get("allow_charge", DEFAULT_SLOT_ALLOW_CHARGE)
            attrs[f"slot_{n}_allow_discharge"] = slot.get("allow_discharge", DEFAULT_SLOT_ALLOW_DISCHARGE)

        return attrs

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }


class ActiveBatteriesSensor(SensorEntity):
    """Diagnostic sensor showing which batteries are currently active in load sharing."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, controller, coordinators: list
    ) -> None:
        """Initialize the active batteries sensor."""
        self.hass = hass
        self.entry = entry
        self.controller = controller
        self._coordinators = coordinators

        self._attr_has_entity_name = True
        self._attr_translation_key = "active_batteries"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}active_batteries"
        self.entity_id = system_entity_id("sensor", "active_batteries")
        self._attr_icon = "mdi:battery-sync"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = True

    @property
    def native_value(self) -> str:
        """Return a summary of active batteries."""
        discharge = self.controller._active_discharge_batteries
        charge = self.controller._active_charge_batteries

        if discharge:
            names = ", ".join(c.name for c in discharge)
            return f"Discharging: {names}"
        elif charge:
            names = ", ".join(c.name for c in charge)
            return f"Charging: {names}"
        return "Idle"

    @property
    def extra_state_attributes(self) -> dict:
        """Return detailed load sharing state."""
        discharge = self.controller._active_discharge_batteries
        charge = self.controller._active_charge_batteries
        total = len(self._coordinators)
        manual = [
            c.name for c in self._coordinators
            if getattr(c, "battery_manual_mode_enabled", False)
        ]
        automatic = [
            c.name for c in self._coordinators
            if not getattr(c, "battery_manual_mode_enabled", False)
        ]

        attrs = {
            "total_batteries": total,
            "manual_batteries": manual,
            "automatic_batteries": automatic,
            "discharge_active": len(discharge),
            "discharge_batteries": [c.name for c in discharge],
            "charge_active": len(charge),
            "charge_batteries": [c.name for c in charge],
        }

        # Add per-battery SOC and lifetime energy for context
        for c in self._coordinators:
            if c.data:
                soc = c.data.get("battery_soc", "N/A")
                discharge_kwh = effective_total_discharging_energy(c.data)
                if discharge_kwh is None:
                    discharge_kwh = "N/A"
                charge_kwh = c.data.get("total_charging_energy", "N/A")
                attrs[f"{c.name}_soc"] = f"{soc}%"
                attrs[f"{c.name}_total_discharged"] = f"{discharge_kwh} kWh"
                attrs[f"{c.name}_total_charged"] = f"{charge_kwh} kWh"

        return attrs

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }


class ThreePhaseProtectionSensor(SensorEntity):
    """Diagnostic sensor showing the live three-phase protection envelope."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, controller) -> None:
        """Initialize the three-phase protection status sensor."""
        self.hass = hass
        self.entry = entry
        self._controller = controller

        self._attr_has_entity_name = True
        self._attr_translation_key = "three_phase_protection_status"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}three_phase_protection_status"
        self.entity_id = system_entity_id("sensor", "three_phase_protection_status")
        self._attr_icon = "mdi:shield-check-outline"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = True

    def _diagnostics(self) -> dict:
        """Return a fresh snapshot so HA attributes reflect the latest readings."""
        limiter = getattr(self._controller, "_phase_power_limiter", None)
        if limiter is None:
            return {
                "state": "disabled",
                "enabled": False,
                "protection_enabled": False,
                "limited_batteries": [],
                "limited_battery_details": [],
                "unassigned_batteries": [],
                "degraded_phases": [],
                "phases": {},
            }
        return limiter.diagnostics()

    @property
    def native_value(self) -> str:
        """Return the translated protection status key."""
        return str(self._diagnostics().get("state", "disabled"))

    @property
    def extra_state_attributes(self) -> dict:
        """Return configuration, phase budgets and limited-battery details."""
        details = self._diagnostics()
        details.pop("state", None)
        return details

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }


class WeeklyFullChargeSensor(SensorEntity):
    """Diagnostic sensor showing weekly full charge status."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, controller) -> None:
        """Initialize the weekly full charge sensor."""
        self.hass = hass
        self.entry = entry
        self._controller = controller

        self._attr_has_entity_name = True
        self._attr_translation_key = "weekly_full_charge"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}weekly_full_charge_status"
        self.entity_id = system_entity_id("sensor", "weekly_full_charge_status")
        self._attr_icon = "mdi:battery-clock"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = True

    @property
    def native_value(self) -> str:
        """Return the current weekly charge status as a translation key."""
        state = self._controller._weekly_charge_status.get("state", "Idle")
        return {
            "Idle": "idle",
            "Disabled": "disabled",
            "Charging to 100%": "charging",
            "Complete": "complete",
        }.get(state, "idle")

    @property
    def extra_state_attributes(self) -> dict:
        """Return weekly charge details as attributes."""
        attrs = {
            "weekly_charge_day": self._controller.weekly_full_charge_day,
        }
        completion_reason = self._controller._weekly_charge_status.get("completion_reason")
        if completion_reason:
            attrs["completion_reason"] = completion_reason
        batteries = self._controller._weekly_charge_status.get("batteries")
        if batteries:
            attrs["batteries"] = batteries
        return attrs

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }


class IntegrationStatusSensor(SensorEntity):
    """Primary status sensor showing what the integration is currently doing.

    Provides a single at-a-glance state representing the highest-priority
    active mode, from manual override down to normal PD control.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, controller) -> None:
        """Initialize the integration status sensor."""
        self.hass = hass
        self.entry = entry
        self._controller = controller

        self._attr_has_entity_name = True
        self._attr_translation_key = "integration_status"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}integration_status"
        self.entity_id = system_entity_id("sensor", "integration_status")
        self._attr_icon = "mdi:home-battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = True

    def _time_slot_blocked(self, direction: str) -> bool:
        """Return True when a time-slot whitelist blocks `direction` on every battery.

        Time-slot blockers are stored per-battery (`time_slot_charge` /
        `time_slot_discharge`), so the system-level status only reports the
        restriction when no available battery can act in that direction.
        """
        c = self._controller
        if direction == "discharge":
            getter, key = c.get_discharge_blockers, "time_slot_discharge"
        else:
            getter, key = c.get_charge_blockers, "time_slot_charge"
        coordinators = [
            coordinator
            for coordinator in c.coordinators
            if getattr(coordinator, "is_available", True)
            and not getattr(coordinator, "battery_manual_mode_enabled", False)
        ]
        if not coordinators:
            return False
        return all(key in getter(coordinator) for coordinator in coordinators)

    def _capacity_protection_state_key(self) -> str | None:
        """Return the integration-status key for peak-shaving activity."""
        c = self._controller
        if not getattr(c, "_capacity_protection_active", False):
            return None

        action = c._capacity_protection_status.get("action")
        return {
            "shaving": "peak_shaving",
            "shaving_excluded": "peak_shaving",
            "conserving": "capacity_conserving",
            "charging": "capacity_protection_charging",
        }.get(action, "capacity_protection")

    def _ev_charger_state_key(self) -> str | None:
        """Return the integration-status key for no-telemetry EV charger handling."""
        c = self._controller
        charge_blockers = c.get_charge_blockers()
        discharge_blockers = c.get_discharge_blockers()
        if "ev_pause" in charge_blockers or "ev_pause" in discharge_blockers:
            return "ev_charger_pause"
        if "ev_charging" in discharge_blockers:
            return "ev_discharge_blocked"
        return None

    def _balance_hold_batteries(self) -> list[str]:
        """Return batteries currently held by the cell-balance monitor."""
        return [
            coordinator.name
            for coordinator in self._controller.coordinators
            if getattr(coordinator, "balance_hold", False)
            and not getattr(coordinator, "battery_manual_mode_enabled", False)
        ]

    def _backup_cooldown_batteries(self) -> list[str]:
        """Return batteries temporarily excluded because backup/offgrid load was active."""

        now = dt_util.utcnow()
        return [
            coordinator.name
            for coordinator, cooldown_until in self._controller._backup_cooldown_until.items()
            if not getattr(coordinator, "battery_manual_mode_enabled", False)
            if cooldown_until and now < cooldown_until
        ]

    @property
    def native_value(self) -> str:
        """Return the current integration status as a translation key."""
        c = self._controller

        # Priority 1: Manual mode overrides everything
        if c.manual_mode_enabled:
            return "manual"

        # Priority 2: Weekly full charge in progress
        if c.weekly_full_charge_enabled:
            if c._weekly_charge_status.get("state") in ("Charging to 100%", "Active balancing"):
                return "weekly_full_charge"

        # Priority 3: Operational restrictions and feature overrides
        ev_state = self._ev_charger_state_key()
        if ev_state:
            return ev_state

        if self._balance_hold_batteries():
            return "cell_balance_hold"

        capacity_state = self._capacity_protection_state_key()
        if capacity_state:
            return capacity_state

        if self._backup_cooldown_batteries():
            return "backup_mode"

        # Priority 4: Manual time slot forcing batteries off the PD path
        if getattr(c, "_manual_slot_owned", None):
            return "time_slot_manual"

        # Priority 5: Outside all configured operating windows
        if self._time_slot_blocked("discharge"):
            return "no_discharge_slot"
        if self._time_slot_blocked("charge"):
            return "no_charge_slot"

        # Priority 6: PD control state from last command
        if c.first_execution:
            return "initializing"

        prev_power = c.previous_power
        if prev_power > 0:
            return "charging"
        elif prev_power < 0:
            return "discharging"
        return "balanced"

    @property
    def extra_state_attributes(self) -> dict:
        """Return current controller details for diagnostics."""
        c = self._controller
        attrs = {
            "setpoint_active": c.compute_active_target(),
            "previous_power_w": c.previous_power,
            "first_execution": c.first_execution,
            "manual_mode_enabled": c.manual_mode_enabled,
            "manual_batteries": [
                coordinator.name for coordinator in c.coordinators
                if getattr(coordinator, "battery_manual_mode_enabled", False)
            ],
            "automatic_batteries": [
                coordinator.name for coordinator in c.coordinators
                if not getattr(coordinator, "battery_manual_mode_enabled", False)
            ],
            "charge_blocked": c.is_charge_effectively_blocked(),
            "discharge_blocked": c.is_discharge_effectively_blocked(),
        }
        charge_blockers = c.get_charge_blockers()
        if charge_blockers:
            attrs["charge_blockers"] = charge_blockers
        discharge_blockers = c.get_discharge_blockers()
        if discharge_blockers:
            attrs["discharge_blockers"] = discharge_blockers
        battery_charge_blockers = c.get_battery_charge_blockers()
        if battery_charge_blockers:
            attrs["battery_charge_blockers"] = battery_charge_blockers
        battery_discharge_blockers = c.get_battery_discharge_blockers()
        if battery_discharge_blockers:
            attrs["battery_discharge_blockers"] = battery_discharge_blockers
        offsets = dict(c._setpoint_offsets)
        if offsets:
            attrs["setpoint_offsets"] = offsets
        overrides = {k: v[1] for k, v in c._setpoint_overrides.items()}
        if overrides:
            attrs["setpoint_overrides"] = overrides
        attrs["capacity_protection"] = dict(c._capacity_protection_status)

        external_loads = getattr(c, "_external_loads", None)
        if external_loads is not None:
            dynamic_status = dict(external_loads.dynamic_power_control_status)
            if dynamic_status.get("active"):
                attrs["dynamic_power_control"] = dynamic_status

        temp_mgr = getattr(c, "_temp_limit_mgr", None)
        if temp_mgr is not None and c.temp_charge_limit_enabled:
            attrs["temperature_charge_limit"] = temp_mgr.get_status()

        balance_hold_batteries = self._balance_hold_batteries()
        if balance_hold_batteries:
            attrs["balance_hold_batteries"] = balance_hold_batteries

        backup_cooldown_batteries = self._backup_cooldown_batteries()
        if backup_cooldown_batteries:
            attrs["backup_cooldown_batteries"] = backup_cooldown_batteries

        ev_chargers = [entity_id for entity_id, active in c._ev_charging_states.items() if active]
        if ev_chargers:
            attrs["ev_chargers_active"] = ev_chargers
        if c._ev_pause_until:
            attrs["ev_pause_until"] = {
                entity_id: pause_until.isoformat()
                for entity_id, pause_until in c._ev_pause_until.items()
                if pause_until is not None
            }

        normal_balance = c.get_max_soc_charge_status()
        if normal_balance:
            attrs["normal_balance_protection"] = normal_balance

        non_responsive = c.non_responsive_battery_names
        if non_responsive:
            attrs["non_responsive_batteries"] = non_responsive
        return attrs

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }


class NonResponsiveBatteriesSensor(SensorEntity):
    """Diagnostic sensor showing batteries that are unreachable or non-delivering."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, controller, coordinators: list
    ) -> None:
        """Initialize the non-responsive batteries sensor."""
        self.hass = hass
        self.entry = entry
        self._controller = controller
        self._coordinators = coordinators

        self._attr_has_entity_name = True
        self._attr_translation_key = "non_responsive_batteries"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}non_responsive_batteries"
        self.entity_id = system_entity_id("sensor", "non_responsive_batteries")
        self._attr_icon = "mdi:battery-alert"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = True

    @property
    def native_value(self) -> str:
        """Return names of non-responsive batteries, or 'None' if all are healthy."""
        names = self._controller.non_responsive_battery_names
        return ", ".join(names) if names else "None"

    @property
    def extra_state_attributes(self) -> dict:
        """Return per-battery non-responsive state details."""
        now = dt_util.utcnow()
        attrs = {}
        for coordinator in self._coordinators:
            # This check also expires a completed cooldown before attributes are
            # rendered, avoiding state="None" with excluded=true/0 minutes.
            currently_excluded = self._controller._non_responsive.is_excluded(
                coordinator
            )
            info = self._controller._non_responsive_batteries.get(coordinator)
            unreachable = (
                not coordinator.is_available
                and not getattr(coordinator, "_is_shutting_down", False)
                and getattr(coordinator, "_consecutive_failures", 0) > 0
            )
            if currently_excluded and info:
                cooldown_min = self._controller._non_responsive.cooldown_min
                elapsed_min = (now - info["excluded_at"]).total_seconds() / 60
                remaining_min = max(0.0, cooldown_min - elapsed_min)
                attrs[coordinator.name] = {
                    "excluded": True,
                    "unreachable": unreachable,
                    "reason": info.get("reason") or "non_delivery",
                    "retry_attempted": info.get("retry_attempted", False),
                    "wake_attempted": info.get("wake_attempted", False),
                    "cooldown_minutes": cooldown_min,
                    "remaining_minutes": round(remaining_min, 1),
                    "consecutive_failures": getattr(coordinator, "_consecutive_failures", 0),
                }
            else:
                attrs[coordinator.name] = {
                    "excluded": unreachable,
                    "unreachable": unreachable,
                    "reason": "connection_unavailable" if unreachable else (info.get("reason") if info else None),
                    "retry_attempted": info.get("retry_attempted", False) if info else False,
                    "wake_attempted": info.get("wake_attempted", False) if info else False,
                    "fail_count": info["fail_count"] if info else 0,
                    "consecutive_failures": getattr(coordinator, "_consecutive_failures", 0),
                }
        return attrs

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }


class SystemSolarPowerSensor(SensorEntity):
    """Instantaneous total solar production from external and independent PV.

    Sums the configured solar_production_sensor and independent battery PV —
    Venus MPPT inputs. The entity is omitted when no battery contributes
    independent PV so it cannot duplicate the external sensor.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "system_solar_power"
    _attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}solar_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W"
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:solar-power"
    _attr_should_poll = True

    def __init__(self, controller) -> None:
        """Initialize the system solar power sensor."""
        self._controller = controller
        self.entity_id = system_entity_id("sensor", "solar_power")

    @property
    def native_value(self) -> float | None:
        """Return total instantaneous solar production in W (None if no source readable)."""
        power_kw = read_total_solar_power_kw(self.hass, self._controller)
        if power_kw is None:
            return None
        return round(power_kw * 1000.0)

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }
