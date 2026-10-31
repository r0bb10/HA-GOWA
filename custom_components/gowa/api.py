"""Small asynchronous client for GOWA; never manages WhatsApp sessions."""

import re
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any

import aiohttp
from yarl import URL


class GowaError(Exception):
    """A sanitized GOWA error safe to display in Home Assistant."""


class GowaAuthError(GowaError):
    """Credentials were rejected."""


class GowaConnectionError(GowaError):
    """The server could not be reached."""


class GowaSendUncertainError(GowaError):
    """The request may have sent a message; repeating it could duplicate it."""


class GowaApiError(GowaError):
    """An HTTP or application-level error."""

    def __init__(self, *, status: int | None = None, code: str | None = None) -> None:
        self.status = status
        # Only known codes are exposed; upstream messages may contain private data.
        reasons = {
            "AUTHENTICATION_ERROR": (
                "The WhatsApp account is disconnected or not logged in."
            ),
            "CROSS_SITE_REQUEST_BLOCKED": (
                "GOWA blocked access due to an origin restriction."
            ),
            "DEVICE_NOT_FOUND": "The selected GOWA device no longer exists.",
            "DEVICE_MANAGER_UNAVAILABLE": "GOWA's device manager is unavailable.",
            "WA_REACHOUT_TIMELOCK": "WhatsApp has restricted starting new chats.",
        }
        self.code = code if code in reasons else None
        super().__init__(
            reasons.get(self.code)
            or (
                f"GOWA rejected the request (HTTP {status})."
                if status is not None
                else "GOWA returned an invalid or unsuccessful API response."
            )
        )


@dataclass(frozen=True)
class GowaDevice:
    """Only the fields needed to select an existing sending device."""

    id: str
    name: str


@dataclass(frozen=True)
class GowaStatus:
    """Verified device-scoped session state."""

    device_id: str
    connected: bool
    logged_in: bool
    paired_number: str | None = None


def normalize_url(value: str) -> str:
    """Infer a scheme without probing or sending credentials over a fallback."""
    try:
        raw = value.strip()
        explicit_scheme = "://" in raw
        if not raw or any(char.isspace() for char in raw) or "\\" in raw:
            raise ValueError
        if not explicit_scheme and (raw.startswith("/") or ":/" in raw):
            raise ValueError
        url = URL(raw if explicit_scheme else f"https://{raw}")
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.user is not None
            or url.password is not None
            or url.query_string
            or url.fragment
        ):
            raise ValueError
        # Validate malformed/out-of-range ports too.
        _ = url.port
        if not explicit_scheme:
            host = url.host.rstrip(".").lower()
            try:
                address = ip_address(host)
            except ValueError:
                local = "." not in host or host.endswith(
                    (".local", ".lan", ".home", ".internal", ".localhost", ".home.arpa")
                )
            else:
                local = (
                    address.is_private or address.is_loopback or address.is_link_local
                )
            if local:
                # Reparse to preserve an explicitly supplied port, including
                # :443. with_scheme() may otherwise change the default port.
                url = URL(f"http://{raw}")
    except (TypeError, ValueError) as err:
        raise ValueError(
            "Enter a hostname or HTTP(S) URL without credentials, queries or fragments."
        ) from err
    return str(url).rstrip("/")


class GowaClient:
    """Use HA's HTTP session and Basic Auth; deliberately never retry sends."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
        device_id: str | None = None,
    ) -> None:
        self._session = session
        self.base_url = normalize_url(base_url)
        self._authorization = aiohttp.encode_basic_auth(username, password)
        self.device_id = device_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        device_scoped: bool = False,
        payload: dict[str, str] | None = None,
    ) -> Any:
        headers = {"Authorization": self._authorization}
        if device_scoped and self.device_id:
            headers["X-Device-Id"] = self.device_id
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60 if method == "POST" else 10),
                # Never forward credentials or a message to a redirect target.
                allow_redirects=False,
            ) as response:
                try:
                    data = await response.json()
                except ValueError, aiohttp.ContentTypeError:
                    data = None
                code = data.get("code") if isinstance(data, dict) else None
                if response.status == 401 and not code:
                    raise GowaAuthError("GOWA authentication failed.")
                if method == "POST" and (
                    response.status in (408, 502, 504)
                    or code == "CONTEXT_ERROR"
                    or (200 <= response.status < 300 and not isinstance(data, dict))
                ):
                    raise GowaSendUncertainError(
                        "Send outcome is unknown; "
                        "GOWA may already have sent the message."
                    )
                if not 200 <= response.status < 300 or code != "SUCCESS":
                    raise GowaApiError(
                        status=response.status,
                        code=code if isinstance(code, str) else None,
                    )
        except (TimeoutError, aiohttp.ClientError) as err:
            # Raw exception strings can contain URLs, credentials or responses.
            if method == "POST":
                raise GowaSendUncertainError(
                    "Send outcome is unknown after a timeout or network interruption; "
                    "GOWA may already have sent the message."
                ) from err
            raise GowaConnectionError(
                "Cannot reach GOWA or the request timed out."
            ) from err
        if "results" not in data:
            if method == "POST":
                raise GowaSendUncertainError(
                    "Send outcome is unknown: GOWA returned no confirmation."
                )
            raise GowaApiError
        return data["results"]

    async def async_devices(self) -> list[GowaDevice]:
        """Discover registered device slots without creating or changing them."""
        try:
            results = await self._request("GET", "/devices")
        except GowaApiError as err:
            # Older installations can still supply a device through /app/status.
            if err.status not in (404, 405):
                raise
            status = await self.async_status()
            return [GowaDevice(status.device_id, status.device_id)]
        if not isinstance(results, list):
            raise GowaApiError
        devices = []
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise GowaApiError
            if not item["id"]:
                raise GowaApiError
            name = item.get("display_name")
            devices.append(
                GowaDevice(
                    item["id"], name if isinstance(name, str) and name else item["id"]
                )
            )
        return devices

    async def async_server_info(self) -> str | None:
        """Probe authenticated server health; a version field is optional."""
        result = await self._request("GET", "/app/info")
        if not isinstance(result, dict):
            raise GowaApiError
        version = result.get("version")
        if (
            isinstance(version, str)
            and version.strip()
            and len(version) <= 128
            and version.isprintable()
        ):
            return version.strip()
        return None

    async def async_status(self) -> GowaStatus:
        """Read /app/status with the same device header used for sending."""
        result = await self._request("GET", "/app/status", device_scoped=True)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("device_id"), str)
            or not result["device_id"]
            or type(result.get("is_connected")) is not bool
            or type(result.get("is_logged_in")) is not bool
            or (self.device_id is not None and result["device_id"] != self.device_id)
        ):
            raise GowaApiError
        jid = result.get("jid")
        number = None
        if isinstance(jid, str) and (
            match := re.fullmatch(r"([0-9]{1,15})(?::[0-9]+)?@s\.whatsapp\.net", jid)
        ):
            number = f"+{match[1]}"
        return GowaStatus(
            result["device_id"], result["is_connected"], result["is_logged_in"], number
        )

    async def async_send_message(self, phone: str, message: str) -> str:
        """Return GOWA's accepted-message ID, not a delivery confirmation."""
        if not self.device_id:
            raise GowaApiError
        result = await self._request(
            "POST",
            "/send/message",
            device_scoped=True,
            payload={"phone": str(phone), "message": str(message)},
        )
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("message_id"), str)
            or not result["message_id"]
        ):
            raise GowaSendUncertainError(
                "Send outcome is unknown: GOWA returned no valid message ID."
            )
        return result["message_id"]
