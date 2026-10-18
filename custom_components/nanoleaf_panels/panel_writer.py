"""Serialized, coalescing writer for Nanoleaf per-panel updates.

The Nanoleaf ``effects/write`` API only accepts a frame for the *whole*
display, so every per-panel update is a read-modify-write of the full panel
set.  Without serialization, concurrent calls (e.g. turning several panel
entities on from one script) each read the pre-burst state and the last
write wins -- only one panel ends up lit.

``PanelWriter`` fixes that by funnelling every write for a device through a
single lock, and by merging overrides that arrive close together into one
device write.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import BLACK, DATA_PANEL_STATE, DOMAIN
from .nanoleaf_api import NanoleafApiClient

_LOGGER = logging.getLogger(__name__)

Frame = tuple[int, int, int, int, int]

# How long a batch waits for further overrides before it is written.  Long
# enough to merge a burst of service calls into one device write, short
# enough to be imperceptible.
COALESCE_DELAY = 0.05

# How long after a write our own record of the panels is trusted over what
# the device reports.  The device lags in updating its *Static* animation,
# so reading it back right after a write returns the previous colors --
# merging against those would switch off panels lit moments earlier.
READBACK_GRACE = 5.0


def build_anim_data(panels: list[dict[str, Any]]) -> str:
    """Serialize panels into the Nanoleaf ``animData`` string format."""
    parts = [str(len(panels))]
    for panel in panels:
        frames = panel["frames"]
        parts.append(f"{panel['panel_id']} {len(frames)}")
        for r, g, b, w, t in frames:
            parts.append(f"{r} {g} {b} {w} {t}")
    return " ".join(parts)


def get_cached_panel_state(
    hass: HomeAssistant, nanoleaf_entry_id: str
) -> dict[int, Frame]:
    """Return the last known per-panel state of a Nanoleaf device."""
    return (
        hass.data.get(DOMAIN, {}).get(DATA_PANEL_STATE, {}).get(nanoleaf_entry_id, {})
    )


def set_cached_panel_state(
    hass: HomeAssistant, nanoleaf_entry_id: str, state: dict[int, Frame]
) -> None:
    """Record the per-panel state of a Nanoleaf device."""
    hass.data.setdefault(DOMAIN, {}).setdefault(DATA_PANEL_STATE, {})[
        nanoleaf_entry_id
    ] = state


class PanelWriter:
    """Serializes and batches panel writes for a single Nanoleaf device.

    Callers queue overrides with ``async_write``.  The first override of a
    batch schedules a flush task that waits ``COALESCE_DELAY``, takes the
    lock and writes everything queued by then in one request; overrides that
    arrive while the batch is still waiting for the lock join it too.  Every
    caller waits for the batch it joined, so errors reach all of them.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: NanoleafApiClient,
        nanoleaf_entry_id: str,
    ) -> None:
        self.hass = hass
        self.api_client = api_client
        self.nanoleaf_entry_id = nanoleaf_entry_id
        self.panel_ids: list[int] = []
        # The parent light from the official Nanoleaf integration; only set
        # when panel entities are enabled.
        self.nanoleaf_entity_id: str | None = None
        # Held for the whole read-merge-write cycle.  The panel coordinator
        # holds it while polling too, so a poll can't cache a pre-write state.
        self.lock = asyncio.Lock()

        self._pending: dict[int, list[Frame]] = {}
        self._pending_animation = False
        self._pending_blank = False
        self._flush_task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[dict[int, Frame]], None]] = []
        self._last_write: float | None = None
        # Whether our last write left any panel lit.  The parent entity's
        # state lags our writes until the official integration catches up,
        # so on its own it can't tell that an earlier call in a burst has
        # already lit the device.
        self._lit = False

    @property
    def device_is_lit(self) -> bool:
        """Return whether the device is currently showing anything."""
        if self._lit:
            return True
        if self.nanoleaf_entity_id is None:
            return False
        parent = self.hass.states.get(self.nanoleaf_entity_id)
        return parent is not None and parent.state == "on"

    @property
    def recently_written(self) -> bool:
        """Return whether our last write is still newer than the device's report."""
        return (
            self._last_write is not None
            and time.monotonic() - self._last_write < READBACK_GRACE
        )

    @callback
    def note_device_off(self) -> None:
        """Forget that we lit the device after it went off or unavailable."""
        self._lit = False

    @callback
    def async_add_listener(
        self, listener: Callable[[dict[int, Frame]], None]
    ) -> Callable[[], None]:
        """Call ``listener`` with the full panel state after every write."""
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(listener)

        return remove_listener

    async def async_write(
        self,
        panels_override: dict[int, list[Frame]],
        *,
        has_animation_panels: bool = False,
        blank_others_when_off: bool = False,
    ) -> None:
        """Queue overrides and wait until they have been written to the device.

        panels_override maps panel_id -> list of (r, g, b, w, t) frames.
        Panels not in panels_override keep their current device/cache color.
        has_animation_panels keeps the device in custom (loop) mode even when
        all current frames are static, to prevent flicker when conditions
        toggle.  blank_others_when_off switches every other panel off when
        the device is dark, so only the requested panels light up.
        """
        self._pending.update(panels_override)
        self._pending_animation |= has_animation_panels
        self._pending_blank |= blank_others_when_off

        task = self._flush_task
        if task is None or task.done():
            task = self._flush_task = self.hass.async_create_task(
                self._async_flush_pending(),
                name=f"{DOMAIN} write to {self.api_client.host}",
            )
        # Shield the batch so one caller being cancelled doesn't abort the
        # write for everyone else in it.
        await asyncio.shield(task)

    async def _async_flush_pending(self) -> None:
        """Wait for the batch to fill up, then write it under the lock."""
        await asyncio.sleep(COALESCE_DELAY)
        async with self.lock:
            # Overrides queued from here on start the next batch.
            self._flush_task = None
            overrides, self._pending = self._pending, {}
            has_animation_panels, self._pending_animation = (
                self._pending_animation,
                False,
            )
            blank_requested, self._pending_blank = self._pending_blank, False
            # Decide whether the device is dark now, under the lock, rather
            # than when the overrides were queued: by now any earlier batch
            # of the burst has landed, and its panels must not be blanked.
            blank_others = blank_requested and not self.device_is_lit
            await self._async_write_locked(
                overrides, has_animation_panels, blank_others
            )

    async def _async_write_locked(
        self,
        panels_override: dict[int, list[Frame]],
        has_animation_panels: bool,
        blank_others: bool,
    ) -> None:
        """Merge overrides with the current panel colors and write them."""
        if blank_others:
            # Every panel we aren't setting stays dark, so there is nothing
            # worth reading back from the device first.
            current_colors: dict[int, Frame] = {}
        elif self.recently_written:
            current_colors = get_cached_panel_state(self.hass, self.nanoleaf_entry_id)
        else:
            current_colors = await self._async_get_current_colors()

        is_animated = any(len(frames) > 1 for frames in panels_override.values())
        use_custom_mode = has_animation_panels or is_animated

        final_panels = [
            {
                "panel_id": pid,
                "frames": panels_override[pid]
                if pid in panels_override
                else [current_colors[pid]]
                if pid in current_colors
                else [BLACK],
            }
            for pid in self.panel_ids
        ]

        if use_custom_mode:
            final_panels = [
                {
                    "panel_id": p["panel_id"],
                    "frames": p["frames"]
                    if len(p["frames"]) > 1
                    else [p["frames"][0], p["frames"][0]],
                }
                for p in final_panels
            ]

        payload = {
            "write": {
                "command": "display",
                "animType": "custom" if use_custom_mode else "static",
                "animName": "",
                "animData": build_anim_data(final_panels),
                "loop": use_custom_mode,
                "palette": [],
            }
        }
        _LOGGER.debug(
            "Writing %d panel override(s) to %s",
            len(panels_override),
            self.api_client.host,
        )
        await self.api_client.async_write_effects(payload)

        state = {p["panel_id"]: p["frames"][0] for p in final_panels}
        set_cached_panel_state(self.hass, self.nanoleaf_entry_id, state)
        self._last_write = time.monotonic()
        self._lit = any(
            frame[:3] != (0, 0, 0) for p in final_panels for frame in p["frames"]
        )
        for listener in list(self._listeners):
            listener(state)

    async def _async_get_current_colors(self) -> dict[int, Frame]:
        """Read the colors the device is showing, falling back to the cache."""
        # Only trust the *Static* animation when the device is actually
        # running it.  In hs/ct color mode the device ignores the animation
        # and shows a uniform solid color; the *Static* data would be stale
        # from the last custom write.
        info = await self.api_client.async_get_device_info()
        if info.get("effects", {}).get("select") == "*Static*":
            colors = await self.api_client.async_get_current_panel_colors()
            if colors:
                return colors
        return get_cached_panel_state(self.hass, self.nanoleaf_entry_id)
