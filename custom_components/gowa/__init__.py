"""Home Assistant notifications and status through an existing GOWA server."""

import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GowaAuthError, GowaClient, GowaError, GowaSendUncertainError
from .const import (
    CONF_DEFAULT_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PHONE,
    CONF_RECIPIENTS,
    DOMAIN,
    ISSUE_CONNECTION,
    PLATFORMS,
    SERVICE_SEND_MESSAGE,
)
from .coordinator import GowaCoordinator
from .phone import normalize_phone

type GowaConfigEntry = ConfigEntry[GowaCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the dynamic-recipient action once, independent of entry reloads."""

    async def handle_send(call: ServiceCall) -> dict[str, str]:
        entries = [
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if not call.data.get("config_entry_id")
            or entry.entry_id == call.data["config_entry_id"]
        ]
        if len(entries) != 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="select_connection",
            )
        coordinator = entries[0].runtime_data
        message_id = await async_send(
            coordinator, call.data[CONF_PHONE], call.data["message"]
        )
        return {"message_id": message_id, "status": "accepted"}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        handle_send,
        schema=vol.Schema(
            {
                vol.Required(CONF_PHONE): cv.string,
                vol.Required("message"): cv.string,
                vol.Optional("config_entry_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GowaConfigEntry) -> bool:
    """Connect without starting, pairing or modifying GOWA."""
    # Migrate only our previous default name. Preserve user-defined names and
    # all registered entity IDs, unique IDs, options and credentials.
    if entry.title == "GOWA WhatsApp":
        hass.config_entries.async_update_entry(entry, title="GOWA")
    devices = dr.async_get(hass)
    if (
        device := devices.async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
    ) and device.name == "GOWA WhatsApp":
        devices.async_update_device(device.id, name=entry.title)
    client = GowaClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DEVICE_ID],
    )
    coordinator = entry.runtime_data = GowaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    # Remove only this integration's deleted recipient entities. Names/numbers
    # can change without changing the recipient's stable UUID or entity ID.
    registry = er.async_get(hass)
    valid_ids = {
        f"{entry.entry_id}_recipient_{recipient['id']}"
        for recipient in entry.options.get(CONF_RECIPIENTS, [])
    }
    retired_ids = {
        (domain, f"{entry.entry_id}_{key}")
        for domain, key in (
            ("binary_sensor", "api_reachable"),
            ("binary_sensor", "authenticated"),
            ("binary_sensor", "connected"),
            ("binary_sensor", "logged_in"),
            ("sensor", "status"),
            ("sensor", "last_send_result"),
        )
    }
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (entity.domain == "notify" and entity.unique_id not in valid_ids) or (
            entity.domain,
            entity.unique_id,
        ) in retired_ids:
            registry.async_remove(entity.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: GowaConfigEntry) -> None:
    """Apply recipient and connection changes through the normal lifecycle."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GowaConfigEntry) -> bool:
    """Unload entities and coordinator listeners without closing HA's session."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GowaConfigEntry) -> None:
    """Remove the corresponding health repair, never the GOWA device."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_CONNECTION}_{entry.entry_id}")
    hass.data.get(DOMAIN, {}).get("send_locks", {}).pop(entry.entry_id, None)


async def async_send(coordinator: GowaCoordinator, phone: str, message: str) -> str:
    """Shared sender for both actions; fail visibly and never retry a send."""
    try:
        phone = normalize_phone(
            phone, coordinator.entry.options.get(CONF_DEFAULT_COUNTRY, "")
        )
        if not message.strip():
            raise ValueError
    except ValueError:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_message_or_phone"
        ) from None
    try:
        async with coordinator.send_lock:
            message_id = await coordinator.client.async_send_message(phone, message)
    except GowaError as err:
        if isinstance(err, GowaAuthError):
            coordinator.entry.async_start_reauth(coordinator.hass)
        await coordinator.async_request_refresh()
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="send_uncertain"
            if isinstance(err, GowaSendUncertainError)
            else "send_failed",
            translation_placeholders={"error": str(err)},
        ) from None
    return message_id
