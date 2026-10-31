"""Poll GOWA and expose actionable health without managing the server."""

import logging
from asyncio import Lock
from dataclasses import dataclass

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
    GowaStatus,
)
from .const import DOMAIN, ISSUE_CONNECTION, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GowaHealth:
    """Unknown session state is distinct from a confirmed disconnected session."""

    reachable: bool
    authenticated: bool | None
    status: str
    session: GowaStatus | None = None
    account_error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"


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
        # Options/reconfigure reloads must not bypass an in-flight send's lock.
        locks = hass.data.setdefault(DOMAIN, {}).setdefault("send_locks", {})
        self.send_lock = locks.setdefault(entry.entry_id, Lock())
        self.server_version: str | None = None
        devices = dr.async_get(hass)
        if (
            device := devices.async_get_device_by_identifier(
                (DOMAIN, entry.entry_id), entry.entry_id
            )
        ) and device.configuration_url == client.base_url:
            self.server_version = device.sw_version

    async def _async_update_data(self) -> GowaHealth:
        try:
            version = await self.client.async_server_info()
        except GowaAuthError:
            health = GowaHealth(True, False, "authentication_failed")
            self.entry.async_start_reauth(self.hass)
        except GowaConnectionError:
            health = GowaHealth(False, None, "unreachable")
        except GowaApiError as err:
            health = GowaHealth(True, None, err.code or "api_error")
        else:
            if version:
                self._update_version(version)
            try:
                session = await self.client.async_status()
            except GowaAuthError:
                health = GowaHealth(True, False, "authentication_failed")
                self.entry.async_start_reauth(self.hass)
            except GowaConnectionError:
                health = GowaHealth(True, True, "healthy", account_error="unreachable")
            except GowaApiError as err:
                health = GowaHealth(
                    True, True, "healthy", account_error=err.code or "api_error"
                )
            else:
                health = GowaHealth(True, True, "healthy", session)

        issue_id = f"{ISSUE_CONNECTION}_{self.entry.entry_id}"
        session = health.session
        if health.healthy and session and session.connected and session.logged_in:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        else:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CONNECTION,
                translation_placeholders={
                    "status": health.account_error
                    or (
                        "not_logged_in"
                        if session and not session.logged_in
                        else "disconnected"
                        if session and not session.connected
                        else health.status
                    )
                },
            )
        return health

    def _update_version(self, version: str) -> None:
        """Refresh device metadata from the health probe, without another request."""
        if version == self.server_version:
            return
        self.server_version = version
        devices = dr.async_get(self.hass)
        if device := devices.async_get_device_by_identifier(
            (DOMAIN, self.entry.entry_id), self.entry.entry_id
        ):
            devices.async_update_device(device.id, sw_version=version)
