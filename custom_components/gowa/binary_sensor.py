"""Authenticated GOWA API health, independent of WhatsApp readiness."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GowaConfigEntry
from .coordinator import GowaCoordinator
from .entity import GowaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GowaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([GowaHealthSensor(entry.runtime_data)])


class GowaHealthSensor(GowaEntity, BinarySensorEntity):
    """Remain available to report failed API probes."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: GowaCoordinator) -> None:
        super().__init__(coordinator, "health")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.healthy)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        health = self.coordinator.data
        return {
            "reason": None
            if health and health.healthy
            else health.status
            if health
            else "unknown"
        }
