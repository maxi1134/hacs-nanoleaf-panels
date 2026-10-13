"""Per-panel light entities for Nanoleaf Panels integration."""

from __future__ import annotations

import colorsys
from datetime import timedelta
import logging
import math
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import _get_or_create_api_client, _get_or_create_writer
from .const import (
    BLACK,
    CONF_EXPOSE_PANELS,
    CONF_NANOLEAF_ENTITY,
    DEFAULT_TRANSITION_TIME,
)
from .nanoleaf_api import NanoleafApiClient, describe_error
from .panel_writer import PanelWriter, get_cached_panel_state, set_cached_panel_state

_LOGGER = logging.getLogger(__name__)


def _kelvin_to_rgb(kelvin: int, brightness: float) -> tuple[int, int, int]:
    """Approximate Kelvin color temperature → (r, g, b); brightness in 0‒1."""
    temp = kelvin / 100
    if temp <= 66:
        r = 255
        g = max(0, min(255, round(99.4708025861 * math.log(temp) - 161.1195681661)))
        b = 0 if temp <= 19 else max(0, min(255, round(138.5177312231 * math.log(temp - 10) - 305.0447927307)))
    else:
        r = max(0, min(255, round(329.698727446 * (temp - 60) ** -0.1332047592)))
        g = max(0, min(255, round(288.1221695283 * (temp - 60) ** -0.0755148492)))
        b = 255
    return (round(r * brightness), round(g * brightness), round(b * brightness))


class NanoleafPanelCoordinator(
    DataUpdateCoordinator[dict[int, tuple[int, int, int, int, int]]]
):
    """Polls the Nanoleaf device for per-panel color state."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: NanoleafApiClient,
        writer: PanelWriter,
        nanoleaf_entry_id: str,
        nanoleaf_entity_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Nanoleaf panels ({api_client.host})",
            update_interval=timedelta(seconds=60),
        )
        self._api_client = api_client
        self.writer = writer
        self._nanoleaf_entry_id = nanoleaf_entry_id
        self.nanoleaf_entity_id = nanoleaf_entity_id
        self.panel_ids: list[int] = []
        # True only when the most recent poll returned actual per-panel colors
        # from the device (i.e. device was in static/custom mode).  False means
        # the device is in effect mode or just turned on — cache cannot be
        # trusted to reflect which individual panels are lit.
        self.last_poll_had_device_data: bool = False

    async def _async_update_data(self) -> dict[int, tuple[int, int, int, int, int]]:
        """Poll current panel colors; merge with in-memory cache as fallback."""
        # Hold the write lock so a poll can't interleave with a write and
        # cache the colors from before it.
        async with self.writer.lock:
            data = await self._async_poll()
            # Keep the write path's cache in step with what the entities
            # show, so the next write merges against the same colors.
            set_cached_panel_state(self.hass, self._nanoleaf_entry_id, data)
        return data

    async def _async_poll(self) -> dict[int, tuple[int, int, int, int, int]]:
        cache = get_cached_panel_state(self.hass, self._nanoleaf_entry_id)

        if self.writer.recently_written:
            # The device is still catching up with our last write and would
            # report the colors from before it; our own record is current.
            self.last_poll_had_device_data = True
            return dict(cache)

        try:
            info = await self._api_client.async_get_device_info()

            state = info.get("state", {})
            selected_effect = info.get("effects", {}).get("selectedEffect")
            color_mode = state.get("colorMode", "")

            if selected_effect == "*Static*":
                # Device is running our custom per-panel animation.
                device_colors = await self._api_client.async_get_current_panel_colors()
                self.last_poll_had_device_data = bool(device_colors)
                return {**cache, **device_colors}

            if color_mode in ("hs", "ct"):
                # Uniform color set via the official integration.
                # Derive per-panel color from device state so all panels
                # reflect the correct color and brightness.
                bri = state.get("brightness", {}).get("value", 100) / 100
                if color_mode == "hs":
                    h = state.get("hue", {}).get("value", 0) / 360
                    s = state.get("sat", {}).get("value", 100) / 100
                    r, g, b = colorsys.hsv_to_rgb(h, s, bri)
                    rgb = (round(r * 255), round(g * 255), round(b * 255))
                else:
                    ct = state.get("ct", {}).get("value", 4000)
                    rgb = _kelvin_to_rgb(ct, bri)
                uniform: dict[int, tuple[int, int, int, int, int]] = {
                    pid: (*rgb, 0, DEFAULT_TRANSITION_TIME)
                    for pid in self.panel_ids
                }
                self.last_poll_had_device_data = True
                return {**cache, **uniform}

        except Exception as err:  # noqa: BLE001
            reason = str(err) or describe_error(err)
            raise UpdateFailed(f"Failed to fetch Nanoleaf state: {reason}") from err

        # Effect mode with a non-static animation — per-panel colors unavailable.
        self.last_poll_had_device_data = False
        return dict(cache)

    @callback
    def async_handle_write(
        self, state: dict[int, tuple[int, int, int, int, int]]
    ) -> None:
        """Show what was just written without re-polling the device.

        Re-polling right away would race the device, which hasn't updated its
        *Static* animation yet.
        """
        self.last_poll_had_device_data = True
        self.async_set_updated_data(dict(state))


class NanoleafPanelLight(
    CoordinatorEntity[NanoleafPanelCoordinator], LightEntity
):
    """A light entity representing a single Nanoleaf panel."""

    _attr_color_mode = ColorMode.HS
    _attr_supported_color_modes = {ColorMode.HS}
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NanoleafPanelCoordinator,
        entry: ConfigEntry,
        panel_id: int,
        panel_number: int,
        device_info: DeviceInfo | None,
    ) -> None:
        super().__init__(coordinator)
        self._panel_id = panel_id
        self._attr_name = f"Panel {panel_number}"
        self._attr_unique_id = f"{entry.entry_id}_panel_{panel_id}"
        if device_info is not None:
            self._attr_device_info = device_info

    @property
    def _panel_state(self) -> tuple[int, int, int, int, int] | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._panel_id)

    @property
    def is_on(self) -> bool:
        # Mirror the parent Nanoleaf entity's on/off state so turning off the
        # device via the official integration is immediately reflected here.
        # The writer also counts a device we just lit as on, before the parent
        # entity has caught up.
        if not self.coordinator.writer.device_is_lit:
            return False
        # If the last poll returned no device data the Nanoleaf is in effect
        # mode (or just turned on) and the cache cannot be trusted to reflect
        # which individual panels are lit.  Show all panels as on so they
        # don't wrongly stay "off" because of a stale all-zero cache.
        if not self.coordinator.last_poll_had_device_data:
            # Device data unavailable (effect mode or mid-transition).
            # If the cache explicitly shows this panel as black (e.g. we just
            # turned it off), respect that; otherwise assume on.
            s = self._panel_state
            if s is not None and s[0] == 0 and s[1] == 0 and s[2] == 0:
                return False
            return True
        s = self._panel_state
        return s is not None and (s[0] > 0 or s[1] > 0 or s[2] > 0)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        s = self._panel_state
        if s is None or (s[0] == 0 and s[1] == 0 and s[2] == 0):
            return None
        h, sat, _ = colorsys.rgb_to_hsv(s[0] / 255, s[1] / 255, s[2] / 255)
        return (h * 360, sat * 100)

    @property
    def brightness(self) -> int | None:
        s = self._panel_state
        if s is None:
            return None
        _, _, v = colorsys.rgb_to_hsv(s[0] / 255, s[1] / 255, s[2] / 255)
        return round(v * 255)

    async def async_turn_on(self, **kwargs: Any) -> None:
        hs: tuple[float, float] | None = kwargs.get(ATTR_HS_COLOR)
        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)

        # Decompose current state into HSV so each axis is updated independently.
        # This avoids cascading scaling errors: e.g. dimming to 50% then back to
        # 75% correctly restores to 75% of the original color, not 75% of the
        # already-dimmed value.
        s = self._panel_state
        if s and (s[0] > 0 or s[1] > 0 or s[2] > 0):
            cur_h, cur_s, cur_v = colorsys.rgb_to_hsv(
                s[0] / 255, s[1] / 255, s[2] / 255
            )
        else:
            cur_h, cur_s, cur_v = 0.0, 0.0, 1.0  # default: white

        new_h = (hs[0] / 360) if hs is not None else cur_h
        new_s = (hs[1] / 100) if hs is not None else cur_s
        new_v = (brightness / 255) if brightness is not None else cur_v

        r_f, g_f, b_f = colorsys.hsv_to_rgb(new_h, new_s, new_v)
        r, g, b = round(r_f * 255), round(g_f * 255), round(b_f * 255)

        # When the device is dark, the writer forces all other panels to
        # black so that only this panel lights up (not the full cached scene).
        # The write is batched with any concurrent panel calls and pushed to
        # the coordinator by the writer.
        await self.coordinator.writer.async_write(
            {self._panel_id: [(r, g, b, 0, DEFAULT_TRANSITION_TIME)]},
            blank_others_when_off=True,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        # If the device is already dark, all panel entities already report
        # off — nothing to write to the device.
        if not self.coordinator.writer.device_is_lit:
            return

        await self.coordinator.writer.async_write({self._panel_id: [BLACK]})

        # If every panel is now black, turn off the parent Nanoleaf entity.
        new_data = get_cached_panel_state(
            self.hass, self.coordinator._nanoleaf_entry_id
        )
        all_off = all(
            new_data.get(pid, BLACK)[:3] == (0, 0, 0)
            for pid in self.coordinator.panel_ids
        )
        if all_off:
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": self.coordinator.nanoleaf_entity_id},
            )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up panel light entities from a config entry."""
    if not entry.options.get(CONF_EXPOSE_PANELS):
        return

    nanoleaf_entity_id: str | None = entry.options.get(CONF_NANOLEAF_ENTITY)
    if not nanoleaf_entity_id:
        _LOGGER.warning(
            "Panel entities are enabled but no Nanoleaf entity is configured"
        )
        return

    entity_entry = er.async_get(hass).async_get(nanoleaf_entity_id)
    if entity_entry is None:
        _LOGGER.error(
            "Configured Nanoleaf entity '%s' not found in registry", nanoleaf_entity_id
        )
        return

    nanoleaf_config_entry = hass.config_entries.async_get_entry(
        entity_entry.config_entry_id
    )
    if nanoleaf_config_entry is None or nanoleaf_config_entry.domain != "nanoleaf":
        _LOGGER.error(
            "Could not find a Nanoleaf config entry for entity '%s'",
            nanoleaf_entity_id,
        )
        return

    host: str = nanoleaf_config_entry.data[CONF_HOST]
    token: str = nanoleaf_config_entry.data[CONF_TOKEN]
    nanoleaf_entry_id: str = nanoleaf_config_entry.entry_id

    # Link panel entities to the existing Nanoleaf device so entity IDs are
    # scoped per device (e.g. light.nanoleaf_pascal_panel_1).
    device_info: DeviceInfo | None = None
    if entity_entry.device_id:
        device_entry = dr.async_get(hass).async_get(entity_entry.device_id)
        if device_entry is not None:
            device_info = DeviceInfo(identifiers=device_entry.identifiers)

    api_client = _get_or_create_api_client(hass, nanoleaf_entry_id, host, token)
    # Register the client so the service handler reuses this instance
    # instead of creating a second one for the same device.
    try:
        panel_ids = await api_client.async_get_panel_order()
    except Exception as err:  # noqa: BLE001
        # Home Assistant retries the platform in the background, so the panels
        # appear once the device is reachable again.
        reason = str(err) or describe_error(err)
        raise PlatformNotReady(
            f"Could not fetch the Nanoleaf panel layout: {reason}"
        ) from err

    # The writer is shared with the set_panels service so that every write
    # to this device is serialized, whichever path it comes from.
    writer = _get_or_create_writer(hass, api_client, nanoleaf_entry_id)
    writer.panel_ids = panel_ids
    writer.nanoleaf_entity_id = nanoleaf_entity_id
    # A writer that outlived a reload may remember lighting a device that has
    # since been switched off; start from the parent entity's view instead.
    writer.note_device_off()

    coordinator = NanoleafPanelCoordinator(
        hass, api_client, writer, nanoleaf_entry_id, nanoleaf_entity_id
    )
    coordinator.panel_ids = panel_ids
    # Not async_config_entry_first_refresh(): the ConfigEntryNotReady it raises
    # is rejected in a forwarded platform and never retried.
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        raise PlatformNotReady(
            f"Could not read the Nanoleaf panel colors: {coordinator.last_exception}"
        )
    entry.async_on_unload(writer.async_add_listener(coordinator.async_handle_write))

    # When the parent Nanoleaf entity changes state (on ↔ off ↔ unavailable):
    # - Turning OFF: re-broadcast current data immediately so panel entities
    #   reflect "off" without waiting for the next 60 s poll.
    # - Turning ON:  trigger a real device poll so panel entities pick up the
    #   actual current colors (works well in static mode; in effect mode the
    #   poll returns nothing and panels keep their last cached state).
    @callback
    def _on_nanoleaf_state_change(event: Any) -> None:
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is not None and new_state.state == "on":
            coordinator.hass.async_create_task(coordinator.async_request_refresh())
            return
        # Only a real on -> off transition, or the device dropping off the
        # network, means it went dark.  An attribute update while the parent
        # still reads "off" can arrive right after we lit the device, before
        # the official integration has caught up with the write.
        if (
            new_state is None
            or new_state.state != "off"
            or (old_state is not None and old_state.state == "on")
        ):
            writer.note_device_off()
        coordinator.async_set_updated_data(coordinator.data or {})

    entry.async_on_unload(
        async_track_state_change_event(
            hass, [nanoleaf_entity_id], _on_nanoleaf_state_change
        )
    )

    async_add_entities(
        [
            NanoleafPanelLight(coordinator, entry, panel_id, idx + 1, device_info)
            for idx, panel_id in enumerate(panel_ids)
        ]
    )
