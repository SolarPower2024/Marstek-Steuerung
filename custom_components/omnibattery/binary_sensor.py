"""Binary sensor platform for the Omnibattery integration."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    CONF_CAPACITY_PROTECTION_ENABLED,
)
from .infra.coordinator import MarstekVenusDataUpdateCoordinator
from .infra.entity_naming import english_entity_id, system_entity_id, SYSTEM_UNIQUE_ID_PREFIX

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinators: list[MarstekVenusDataUpdateCoordinator] = hass.data[DOMAIN][entry.entry_id]["coordinators"]
    controller = hass.data[DOMAIN][entry.entry_id].get("controller")
    entities = []

    # Add regular battery binary sensors (version-specific)
    for coordinator in coordinators:
        for definition in coordinator.binary_sensor_definitions:
            entities.append(MarstekVenusBinarySensor(coordinator, definition))

        # Add charge hysteresis sensor for batteries with hysteresis enabled
        if coordinator.enable_charge_hysteresis:
            entities.append(ChargeHysteresisActiveSensor(coordinator))

    # Add capacity protection status sensor (system-level, when configured, regardless of enabled state)
    if controller and CONF_CAPACITY_PROTECTION_ENABLED in entry.data:
        entities.append(CapacityProtectionStatusSensor(hass, entry, controller))

    async_add_entities(entities)


class MarstekVenusBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a Marstek Venus binary sensor."""

    def __init__(
        self, coordinator: MarstekVenusDataUpdateCoordinator, definition: dict
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.definition = definition
        
        self._attr_has_entity_name = True
        self._attr_translation_key = definition["key"]
        self._attr_unique_id = f"{coordinator.device_key}_{definition['key']}"
        self.entity_id = english_entity_id("binary_sensor", coordinator.name, definition["key"])
        self._attr_device_class = definition.get("device_class")
        self._attr_icon = definition.get("icon")
        self._attr_entity_registry_enabled_default = definition.get("enabled_by_default", True)
        if definition.get("category") == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_should_poll = False

    @property
    def is_on(self):
        """Return the state of the binary sensor."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.definition["key"])

    @property
    def device_info(self):
        """Return device information."""
        return self.coordinator.battery_device_info


class ChargeHysteresisActiveSensor(RestoreEntity, BinarySensorEntity):
    """Binary sensor indicating if charge hysteresis is active for a battery.

    This sensor persists its state across reboots using RestoreEntity.
    When hysteresis is active, the battery won't charge until SOC drops
    below (max_soc - hysteresis_percent).
    """

    # current_soc changes every poll, so recording it rewrites the whole row
    # each cycle for no history value. Restore reads only .state, never attrs.
    _unrecorded_attributes = frozenset({"current_soc"})

    def __init__(self, coordinator: MarstekVenusDataUpdateCoordinator) -> None:
        """Initialize the hysteresis sensor."""
        self.coordinator = coordinator

        self._attr_has_entity_name = True
        self._attr_translation_key = "charge_hysteresis"
        self._attr_unique_id = f"{coordinator.device_key}_charge_hysteresis_active"
        self.entity_id = english_entity_id("binary_sensor", coordinator.name, "charge_hysteresis_active")
        self._attr_icon = "mdi:battery-lock"
        self._attr_should_poll = True
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_added_to_hass(self) -> None:
        """Restore hysteresis state when entity is added to hass."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()

        if last_state is None:
            _LOGGER.debug(
                "[%s] No previous hysteresis state found - starting with hysteresis inactive",
                self.coordinator.name
            )
            return

        # Restore the hysteresis state to the coordinator
        was_active = last_state.state == "on"
        self.coordinator._hysteresis_active = was_active

        _LOGGER.info(
            "[%s] Restored charge hysteresis state: %s",
            self.coordinator.name,
            "ACTIVE" if was_active else "inactive"
        )

    @property
    def is_on(self):
        """Return true if charge hysteresis is active."""
        return self.coordinator._hysteresis_active

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        current_soc = None
        if self.coordinator.data:
            current_soc = self.coordinator.data.get("battery_soc")

        # Use the latched base SOC (the ceiling actually hit) as the threshold
        # base, matching the control logic in _refresh_battery_charge_limit_blocks;
        # fall back to max_soc when not latched. Reporting max_soc here was
        # misleading whenever the latch captured a different SOC.
        base = (
            self.coordinator._hysteresis_base_soc
            if self.coordinator._hysteresis_base_soc is not None
            else self.coordinator.max_soc
        )
        charge_threshold = base - self.coordinator.charge_hysteresis_percent

        return {
            "max_soc": self.coordinator.max_soc,
            "hysteresis_percent": self.coordinator.charge_hysteresis_percent,
            "base_soc": base,
            "charge_resume_threshold": charge_threshold,
            "current_soc": current_soc,
        }

    @property
    def device_info(self):
        """Return device information."""
        return self.coordinator.battery_device_info


class CapacityProtectionStatusSensor(BinarySensorEntity):
    """Binary sensor indicating if capacity protection is currently intervening."""

    # While protection is active these numerics change every poll; keep the
    # on/off state + action/threshold in history, drop the per-cycle churn.
    _unrecorded_attributes = frozenset({
        "avg_soc", "peak_limit_w", "estimated_house_load_w",
        "original_target_w", "adjusted_target_w",
    })

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, controller) -> None:
        """Initialize the status sensor."""
        self.hass = hass
        self.entry = entry
        self.controller = controller

        self._attr_has_entity_name = True
        self._attr_translation_key = "capacity_protection_active"
        self._attr_unique_id = f"{SYSTEM_UNIQUE_ID_PREFIX}capacity_protection_active"
        self.entity_id = system_entity_id("binary_sensor", "capacity_protection_active")
        self._attr_device_class = "running"
        self._attr_icon = "mdi:shield-alert"
        self._attr_should_poll = True
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        """Return true if capacity protection is actively intervening."""
        return self.controller._capacity_protection_active

    @property
    def extra_state_attributes(self):
        """Return diagnostic attributes about the protection state."""
        status = self.controller._capacity_protection_status
        return {
            "enabled": self.controller.capacity_protection_enabled,
            "excluded_devices_enabled": (
                self.controller.capacity_protection_excluded_devices
            ),
            "avg_soc": status.get("avg_soc"),
            "soc_threshold": status.get("soc_threshold"),
            "peak_limit_w": status.get("peak_limit"),
            "estimated_house_load_w": status.get("estimated_house_load"),
            "action": status.get("action"),
            "original_target_w": status.get("original_target"),
            "adjusted_target_w": status.get("adjusted_target"),
            "excluded_peak_excess_w": status.get("excluded_peak_excess", 0),
        }

    @property
    def device_info(self):
        """Return device information for the system."""
        return {
            "identifiers": {(DOMAIN, "marstek_venus_system")},
            "name": "Omnibattery System",
            "manufacturer": "Omnibattery",
            "model": "Multi-Battery System",
        }

