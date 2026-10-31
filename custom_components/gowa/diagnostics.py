"""Allowlisted diagnostics: no URL, credentials, numbers, persons or messages."""

from homeassistant.core import HomeAssistant

from . import GowaConfigEntry
from .const import CONF_RECIPIENTS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GowaConfigEntry
) -> dict:
    """Never copy arbitrary entry data or API responses into diagnostics."""
    coordinator = entry.runtime_data
    health = coordinator.data
    return {
        "server_version": coordinator.server_version,
        "recipient_count": len(entry.options.get(CONF_RECIPIENTS, [])),
        "status": health.status if health else "unknown",
        "api_reachable": health.reachable if health else None,
        "authenticated": health.authenticated if health else None,
        "connected": health.session.connected if health and health.session else None,
        "logged_in": health.session.logged_in if health and health.session else None,
        "account_error": health.account_error if health else None,
    }
