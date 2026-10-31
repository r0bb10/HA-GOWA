"""GOWA reachability, authentication and WhatsApp session diagnostics."""

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
    """Add a separate entity for each observable health dimension."""
    async_add_entities(
        GowaBinarySensor(entry.runtime_data, key)
        for key in ("api_reachable", "authenticated", "connected", "logged_in")
    )


class GowaBinarySensor(GowaEntity, BinarySensorEntity):
    """Never report stale session data as a current healthy state."""

    def __init__(self, coordinator: GowaCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self.key = key
        if key in ("api_reachable", "connected"):
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def available(self) -> bool:
        if not (health := self.coordinator.data):
            return False
        if self.key == "api_reachable":
            return True
        if self.key == "authenticated":
            return health.authenticated is not None
        return health.session is not None

    @property
    def is_on(self) -> bool | None:
        if not (health := self.coordinator.data):
            return None
        if self.key == "api_reachable":
            return health.reachable
        if self.key == "authenticated":
            return health.authenticated
        if health.session is None:
            return None
        return (
            health.session.connected
            if self.key == "connected"
            else health.session.logged_in
        )
