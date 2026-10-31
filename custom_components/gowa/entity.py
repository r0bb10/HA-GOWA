"""Shared GOWA device and entity metadata."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GowaCoordinator


class GowaEntity(CoordinatorEntity[GowaCoordinator]):
    """Entities belong to this API connection, not an HA person or tracker."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GowaCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name=coordinator.entry.title,
            manufacturer="GOWA",
            model="WhatsApp API",
            sw_version=coordinator.server_version,
            configuration_url=coordinator.client.base_url,
        )
