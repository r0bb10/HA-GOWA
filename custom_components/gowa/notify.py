"""One native notify entity per stable, configured recipient."""

from typing import Any

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import GowaConfigEntry, async_send
from .const import CONF_PERSON, CONF_PHONE, CONF_RECIPIENTS
from .coordinator import GowaCoordinator
from .entity import GowaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GowaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Standard notify.send_message needs no extra phone/target fields."""
    async_add_entities(
        GowaNotifyEntity(entry.runtime_data, recipient)
        for recipient in entry.options.get(CONF_RECIPIENTS, [])
    )


class GowaNotifyEntity(GowaEntity, NotifyEntity):
    """Optional person association is metadata, never presence tracking."""

    _attr_entity_category = None
    _attr_supported_features = NotifyEntityFeature.TITLE
    _attr_translation_key = None

    def __init__(self, coordinator: GowaCoordinator, recipient: dict[str, Any]) -> None:
        super().__init__(coordinator, f"recipient_{recipient['id']}")
        self._attr_translation_key = None
        self._attr_name = recipient["name"]
        self._phone = recipient[CONF_PHONE]
        self._person = recipient.get(CONF_PERSON)

    @property
    def available(self) -> bool:
        health = self.coordinator.data
        return bool(
            health
            and health.session
            and health.session.connected
            and health.session.logged_in
        )

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        # Do not expose phone numbers or message content in states/recorder.
        return {CONF_PERSON: self._person} if self._person else None

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """A title is rendered as text because GOWA sends a plain text body."""
        body = f"{title}\n\n{message}" if title else message
        # Stored recipients are canonical, independent of future country changes.
        await async_send(self.coordinator, f"+{self._phone}", body)
