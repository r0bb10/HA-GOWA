"""Poll GOWA and expose actionable health without managing the server."""

import logging
from dataclasses import dataclass
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    GowaApiError,
    GowaAuthError,
    GowaClient,
    GowaConnectionError,
    GowaError,
    GowaStatus,
)
from .const import DOMAIN, ISSUE_CONNECTION, METADATA_INTERVAL, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GowaHealth:
    """Unknown session state is distinct from a confirmed disconnected session."""

    reachable: bool
    authenticated: bool | None
    status: str
    session: GowaStatus | None = None


class GowaCoordinator(DataUpdateCoordinator[GowaHealth]):
    """Keep diagnostic entities alive even when the API is unavailable."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: GowaClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.client = client
        self.entry = entry
        self.last_send_result = "never_sent"
        self.last_send_error: str | None = None
        self.server_version: str | None = None
        self._next_metadata_refresh = 0.0
        devices = dr.async_get(hass)
        if (
            device := devices.async_get_device_by_identifier(
                (DOMAIN, entry.entry_id), entry.entry_id
            )
        ) and device.configuration_url == client.base_url:
            self.server_version = device.sw_version

    async def _async_update_data(self) -> GowaHealth:
        try:
            session = await self.client.async_status()
        except GowaAuthError:
            health = GowaHealth(True, False, "authentication_failed")
            self.entry.async_start_reauth(self.hass)
        except GowaConnectionError:
            health = GowaHealth(False, None, "unreachable")
        except GowaApiError:
            health = GowaHealth(True, None, "api_error")
        else:
            status = "healthy"
            if not session.connected:
                status = "disconnected"
            elif not session.logged_in:
                status = "connected_not_logged_in"
            health = GowaHealth(True, True, status, session)

        if health.authenticated:
            await self._async_refresh_metadata()

        issue_id = f"{ISSUE_CONNECTION}_{self.entry.entry_id}"
        if health.status == "healthy":
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        else:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CONNECTION,
                translation_placeholders={"status": health.status},
            )
        return health

    async def _async_refresh_metadata(self) -> None:
        """Metadata is optional and never changes the session health result."""
        now = monotonic()
        if now < self._next_metadata_refresh:
            return
        self._next_metadata_refresh = now + METADATA_INTERVAL.total_seconds()
        try:
            version = await self.client.async_server_version()
        except GowaError:
            # Older servers/proxies may not expose /app/info. Keep the last
            # known version; status and notification support remain intact.
            return
        self.server_version = version
        devices = dr.async_get(self.hass)
        if device := devices.async_get_device_by_identifier(
            (DOMAIN, self.entry.entry_id), self.entry.entry_id
        ):
            devices.async_update_device(device.id, sw_version=version)

    def record_send(self, error: str | None = None) -> None:
        """Update send feedback without retaining recipient or message content."""
        self.last_send_result = "failed" if error else "accepted"
        self.last_send_error = error
        self.async_update_listeners()
