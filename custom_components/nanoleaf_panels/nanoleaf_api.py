"""Adapter for aionanoleaf2 device interactions, isolated in one place.

All calls to the private aionanoleaf2 ``_request`` method live here so that
any future breakage from library updates is contained to a single module.
The adapter is backed by the shared Home Assistant HTTP session, which avoids
creating a new ``ClientSession`` on every API call.
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

from aiohttp import ClientError, ClientResponse
from aionanoleaf2 import Nanoleaf, NanoleafException

from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# The Nanoleaf API carries the auth token in the URL path (/api/v1/<token>/...),
# and aiohttp errors quote the request URL.
_TOKEN_IN_URL = re.compile(r"(/api/v1/)[^/\s'\")]+")
_REDACTED = "**REDACTED**"


def redact_token(text: str, token: str | None = None) -> str:
    """Remove the Nanoleaf auth token from text that may quote a request URL."""
    if token:
        text = text.replace(token, _REDACTED)
    return _TOKEN_IN_URL.sub(rf"\g<1>{_REDACTED}", text)


def describe_error(err: BaseException) -> str:
    """Describe an exception together with its cause, without the auth token.

    aionanoleaf2 raises its exceptions without a message (e.g. a bare
    ``Unavailable`` once its retries run out), which would otherwise log as an
    empty string. The cause is usually an aiohttp error, whose message can
    include the request URL and so the token.
    """

    def _one(exc: BaseException) -> str:
        return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

    if err.__cause__ is None:
        return redact_token(_one(err))
    return redact_token(f"{_one(err)} ({_one(err.__cause__)})")


def _parse_anim_data(anim_data_str: str) -> dict[int, tuple[int, int, int, int, int]]:
    """Parse an animData string into {panel_id: (r, g, b, w, t)}."""
    parts = list(map(int, anim_data_str.split()))
    panel_colors: dict[int, tuple[int, int, int, int, int]] = {}
    idx = 0
    count = parts[idx]
    idx += 1
    for _ in range(count):
        panel_id = parts[idx]
        num_colors = parts[idx + 1]
        r, g, b, w, t = (
            parts[idx + 2],
            parts[idx + 3],
            parts[idx + 4],
            parts[idx + 5],
            parts[idx + 6],
        )
        panel_colors[panel_id] = (r, g, b, w, t)
        idx += 2 + num_colors * 5
    return panel_colors


class NanoleafApiClient:
    """Thin adapter over aionanoleaf2 using the shared HA HTTP session.

    All private ``_request`` calls are encapsulated here so any upstream API
    change only needs to be fixed in one place.  The underlying
    ``aiohttp.ClientSession`` is obtained via ``async_get_clientsession`` and
    is shared across the entire HA instance, eliminating connection-per-call
    overhead.

    The host and token are read from the official Nanoleaf config entry before
    every request.  Discovery rewrites that entry when the device moves to a
    new address, and following it keeps this client working without a reload.
    """

    def __init__(
        self, hass: HomeAssistant, nanoleaf_entry_id: str, host: str, token: str
    ) -> None:
        self._hass = hass
        self._nanoleaf_entry_id = nanoleaf_entry_id
        self._host = host
        self._token = token
        self._nanoleaf = Nanoleaf(async_get_clientsession(hass), host, token)

    @property
    def host(self) -> str:
        """Return the device host address."""
        return self._host

    def _current_nanoleaf(self) -> Nanoleaf:
        """Return the library client, rebuilt if the entry's host or token changed."""
        entry = self._hass.config_entries.async_get_entry(self._nanoleaf_entry_id)
        if entry is not None:
            host = entry.data.get(CONF_HOST, self._host)
            token = entry.data.get(CONF_TOKEN, self._token)
            if (host, token) != (self._host, self._token):
                _LOGGER.info(
                    "Nanoleaf config entry changed (host %s -> %s); reconnecting",
                    self._host,
                    host,
                )
                self._host, self._token = host, token
                self._nanoleaf = Nanoleaf(
                    async_get_clientsession(self._hass), host, token
                )
        return self._nanoleaf

    async def _async_request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> ClientResponse:
        """Send a request to the device at its current address."""
        nanoleaf = self._current_nanoleaf()
        try:
            return await nanoleaf._request(method, path, data)
        except (NanoleafException, ClientError) as err:
            # Not chained: the original exception's message includes the token,
            # and a traceback would print it.
            reason = redact_token(describe_error(err), self._token)
            raise HomeAssistantError(
                f"Request to Nanoleaf at {self._host} failed: {reason}"
            ) from None

    async def async_get_device_info(self) -> dict[str, Any]:
        """Fetch full device info from the Nanoleaf."""
        resp = await self._async_request("get", "")
        data = cast(dict[str, Any], await resp.json())
        resp.release()
        return data

    async def async_get_panel_order(self) -> list[int]:
        """Return ordered list of panel IDs (excluding the controller panel 0)."""
        data = await self.async_get_device_info()
        try:
            return [
                cast(int, panel["panelId"])
                for panel in data["panelLayout"]["layout"]["positionData"]
                if cast(int, panel["panelId"]) != 0
            ]
        except KeyError as err:
            raise HomeAssistantError("Nanoleaf panel layout is not available") from err

    async def async_get_current_panel_colors(
        self,
    ) -> dict[int, tuple[int, int, int, int, int]]:
        """Request the current static panel colors. Returns {} if unavailable."""
        try:
            response = await self._async_request(
                "put",
                "effects",
                {"write": {"command": "request", "animName": "*Static*"}},
            )
            data = cast(dict[str, Any], await response.json())
            response.release()
            anim_data_str = data.get("animData", "")
            if anim_data_str:
                return _parse_anim_data(anim_data_str)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not fetch current panel colors from %s: %s",
                self._host,
                describe_error(err),
            )
        return {}

    async def async_write_effects(self, payload: dict[str, Any]) -> None:
        """Send an effects write command to the Nanoleaf."""
        response = await self._async_request("put", "effects", payload)
        response.release()
