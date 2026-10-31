"""Selected WhatsApp account status and paired number, never session management."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GowaConfigEntry
from .coordinator import GowaCoordinator
from .entity import GowaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GowaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([GowaAccountSensor(entry.runtime_data)])


class GowaAccountSensor(GowaEntity, SensorEntity):
    """Unknown account status is unavailable, never stale readiness."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ready", "disconnected", "not_logged_in"]

    def __init__(self, coordinator: GowaCoordinator) -> None:
        super().__init__(coordinator, "whatsapp")

    @property
    def available(self) -> bool:
        health = self.coordinator.data
        return bool(health and health.session)

    @property
    def native_value(self) -> str | None:
        health = self.coordinator.data
        if not health or not (session := health.session):
            return None
        if not session.logged_in:
            return "not_logged_in"
        return "ready" if session.connected else "disconnected"

    @property
    def extra_state_attributes(self) -> dict:
        health = self.coordinator.data
        session = health.session if health else None
        return {
            "paired_number": session.paired_number if session else None,
            "device_id": self.coordinator.client.device_id,
            "connected": session.connected if session else None,
            "logged_in": session.logged_in if session else None,
            "error": health.account_error or (None if health.healthy else health.status)
            if health
            else "unknown",
        }
