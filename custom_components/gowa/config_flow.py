"""Connection onboarding, reauthentication and named recipient management."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import uuid4

import probatio as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GowaAuthError,
    GowaClient,
    GowaConnectionError,
    GowaDevice,
    GowaError,
    normalize_url,
)
from .const import (
    CONF_DEFAULT_COUNTRY,
    CONF_DEVICE_ID,
    CONF_PERSON,
    CONF_PHONE,
    CONF_RECIPIENTS,
    DOMAIN,
)
from .phone import normalize_country, normalize_phone


def connection_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Do not put the stored password back into a displayed form."""
    return vol.Schema(
        {
            vol.Required(
                CONF_URL, default=defaults.get(CONF_URL, "")
            ): selector.TextSelector(),
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): selector.TextSelector(),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class GowaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Select existing GOWA devices; never pair or modify them."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection: dict[str, Any] = {}
        self._devices: list[GowaDevice] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> GowaOptionsFlow:
        return GowaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_connection("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_connection("reconfigure", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_connection("reauth_confirm", user_input)

    def _current_entry(self) -> config_entries.ConfigEntry | None:
        if self.source == config_entries.SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        if self.source == config_entries.SOURCE_REAUTH:
            return self._get_reauth_entry()
        return None

    def _client(self) -> GowaClient:
        return GowaClient(
            async_get_clientsession(self.hass),
            self._connection[CONF_URL],
            self._connection[CONF_USERNAME],
            self._connection[CONF_PASSWORD],
            self._connection.get(CONF_DEVICE_ID),
        )

    async def _async_connection(
        self, step: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        errors = {}
        entry = self._current_entry()
        defaults = dict(entry.data) if entry else {}
        if user_input is not None:
            defaults.update(user_input)
            try:
                self._connection = {
                    CONF_URL: normalize_url(user_input[CONF_URL]),
                    CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
                if ":" in self._connection[CONF_USERNAME]:
                    raise ValueError
                self._devices = await self._client().async_devices()
                if not self._devices:
                    errors["base"] = "no_devices"
                elif self.source == config_entries.SOURCE_REAUTH:
                    # Reauth changes credentials, never silently switches account.
                    assert entry is not None
                    device_id = entry.data[CONF_DEVICE_ID]
                    if device_id not in {device.id for device in self._devices}:
                        errors["base"] = "device_missing"
                    else:
                        return await self.async_step_device({CONF_DEVICE_ID: device_id})
                elif len(self._devices) == 1:
                    return await self.async_step_device(
                        {CONF_DEVICE_ID: self._devices[0].id}
                    )
                else:
                    return await self.async_step_device()
            except ValueError:
                errors["base"] = "invalid_connection"
            except GowaAuthError:
                errors["base"] = "invalid_auth"
            except GowaConnectionError:
                errors["base"] = "cannot_connect"
            except GowaError:
                errors["base"] = "api_error"
        return self.async_show_form(
            step_id=step, data_schema=connection_schema(defaults), errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            if device_id not in {device.id for device in self._devices}:
                errors["base"] = "device_missing"
            else:
                self._connection[CONF_DEVICE_ID] = device_id
                try:
                    # Offline/logged-out devices are valid: HA can monitor them.
                    await self._client().async_status()
                except GowaAuthError:
                    errors["base"] = "invalid_auth"
                except GowaConnectionError:
                    errors["base"] = "cannot_connect"
                except GowaError:
                    errors["base"] = "api_error"
                else:
                    return await self._async_finish()
        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=device.id, label=device.name
                                )
                                for device in self._devices
                            ]
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def _async_finish(self) -> FlowResult:
        entry = self._current_entry()
        identity = sha256(
            f"{self._connection[CONF_URL]}|{self._connection[CONF_DEVICE_ID]}".encode()
        ).hexdigest()
        await self.async_set_unique_id(identity)
        configured = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, identity
        )
        if configured is not None and (
            entry is None or configured.entry_id != entry.entry_id
        ):
            return self.async_abort(reason="already_configured")
        if entry is not None:
            # A loaded entry already schedules reloads through its update
            # listener. Do not schedule a second reload through the flow.
            finish = (
                self.async_update_and_abort
                if entry.update_listeners
                else self.async_update_reload_and_abort
            )
            return finish(entry, unique_id=identity, data_updates=self._connection)
        return self.async_create_entry(title="GOWA", data=self._connection)


class GowaOptionsFlow(config_entries.OptionsFlow):
    """Small, structured recipient editor with stable IDs and optional persons."""

    def __init__(self) -> None:
        self._selected: str | None = None

    def _recipients(self) -> list[dict[str, Any]]:
        return [
            dict(item) for item in self.config_entry.options.get(CONF_RECIPIENTS, [])
        ]

    def _save(self, recipients: list[dict[str, Any]]) -> FlowResult:
        return self.async_create_entry(
            title="", data={**self.config_entry.options, CONF_RECIPIENTS: recipients}
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "add_recipient",
                "edit_recipient",
                "remove_recipient",
                "settings",
            ],
        )

    def _recipient_schema(self, defaults: dict[str, Any]) -> vol.Schema:
        phone = defaults.get(CONF_PHONE, "")
        if defaults.get("id"):
            phone = f"+{phone}"
        fields = {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, "")
            ): selector.TextSelector(),
            vol.Required(CONF_PHONE, default=phone): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEL)
            ),
        }
        person_key = vol.Optional(
            CONF_PERSON, description={"suggested_value": defaults.get(CONF_PERSON)}
        )
        fields[person_key] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="person")
        )
        return vol.Schema(fields)

    async def async_step_add_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._selected = None
        return await self._async_recipient("add_recipient", user_input)

    async def async_step_edit_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if not self._recipients():
            return self.async_abort(reason="no_recipients")
        if user_input is not None:
            self._selected = user_input["recipient"]
            if self._selected not in {item["id"] for item in self._recipients()}:
                return self.async_abort(reason="recipient_missing")
            return await self.async_step_recipient()
        return self._selection_form("edit_recipient")

    async def async_step_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._async_recipient("recipient", user_input)

    async def _async_recipient(
        self, step: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        recipients = self._recipients()
        existing = next(
            (item for item in recipients if item["id"] == self._selected), {}
        )
        errors = {}
        if user_input is not None:
            try:
                name = user_input[CONF_NAME].strip()
                if not name:
                    raise ValueError
                phone = normalize_phone(
                    user_input[CONF_PHONE],
                    self.config_entry.options.get(CONF_DEFAULT_COUNTRY, ""),
                )
                person = user_input.get(CONF_PERSON)
                if person and (
                    not person.startswith("person.")
                    or self.hass.states.get(person) is None
                ):
                    errors[CONF_PERSON] = "invalid_person"
                elif any(
                    item["id"] != self._selected and item[CONF_PHONE] == phone
                    for item in recipients
                ):
                    errors[CONF_PHONE] = "duplicate_phone"
                else:
                    recipient = {
                        "id": self._selected or uuid4().hex,
                        CONF_NAME: name,
                        CONF_PHONE: phone,
                    }
                    if person:
                        recipient[CONF_PERSON] = person
                    if existing:
                        recipients[recipients.index(existing)] = recipient
                    else:
                        recipients.append(recipient)
                    return self._save(recipients)
            except ValueError:
                errors["base"] = "invalid_recipient"
        return self.async_show_form(
            step_id=step,
            data_schema=self._recipient_schema(
                user_input if user_input is not None else existing
            ),
            errors=errors,
        )

    def _selection_form(self, step: str) -> FlowResult:
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(
                {
                    vol.Required("recipient"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=item["id"], label=item[CONF_NAME]
                                )
                                for item in self._recipients()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_remove_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if not self._recipients():
            return self.async_abort(reason="no_recipients")
        if user_input is not None:
            self._selected = user_input["recipient"]
            if self._selected not in {item["id"] for item in self._recipients()}:
                return self.async_abort(reason="recipient_missing")
            return await self.async_step_confirm_remove()
        return self._selection_form("remove_recipient")

    async def async_step_confirm_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        recipients = self._recipients()
        selected = next(
            (item for item in recipients if item["id"] == self._selected), None
        )
        if selected is None:
            return self.async_abort(reason="recipient_missing")
        if user_input is not None:
            return self._save(
                [item for item in recipients if item["id"] != self._selected]
            )
        return self.async_show_form(
            step_id="confirm_remove",
            data_schema=vol.Schema({}),
            description_placeholders={"name": selected[CONF_NAME]},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors = {}
        country = self.config_entry.options.get(CONF_DEFAULT_COUNTRY, "")
        if user_input is not None:
            try:
                country = normalize_country(user_input.get(CONF_DEFAULT_COUNTRY, ""))
            except ValueError:
                errors[CONF_DEFAULT_COUNTRY] = "invalid_country"
            else:
                return self.async_create_entry(
                    title="",
                    data={**self.config_entry.options, CONF_DEFAULT_COUNTRY: country},
                )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEFAULT_COUNTRY, default=country
                    ): selector.TextSelector()
                }
            ),
            errors=errors,
        )
