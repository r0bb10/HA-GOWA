"""Overall GOWA health and the most recent send result."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GowaConfigEntry
from .coordinator import GowaCoordinator
from .entity import GowaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GowaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up diagnostics."""
    async_add_entities(
        [GowaStatusSensor(entry.runtime_data), GowaSendSensor(entry.runtime_data)]
    )


class GowaStatusSensor(GowaEntity, SensorEntity):
    """Human-readable state remains available during server outages."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "healthy",
        "disconnected",
        "connected_not_logged_in",
        "authentication_failed",
        "unreachable",
        "api_error",
    ]

    def __init__(self, coordinator: GowaCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.status if self.coordinator.data else None


class GowaSendSensor(GowaEntity, SensorEntity):
    """Accepted never implies delivered/read; content is never stored."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["never_sent", "accepted", "failed"]

    def __init__(self, coordinator: GowaCoordinator) -> None:
        super().__init__(coordinator, "last_send_result")

    @property
    def native_value(self) -> str:
        return self.coordinator.last_send_result

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"error": self.coordinator.last_send_error}
