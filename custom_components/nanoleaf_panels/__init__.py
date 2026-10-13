"""Nanoleaf panel control service integration."""

from __future__ import annotations

from collections.abc import Mapping
import colorsys
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.template import Template

from .const import DATA_WRITERS, DEFAULT_TRANSITION_TIME, DOMAIN
from .nanoleaf_api import NanoleafApiClient
from .panel_writer import PanelWriter

SERVICE_SET_PANELS = "set_panels"

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
DATA_YAML_CONFIGURED = "yaml_configured"

PLATFORMS = [Platform.LIGHT]

ATTR_PANELS = "panels"
ATTR_PANEL_ID = "panel_id"
ATTR_NUMBER = "number"
ATTR_HUE = "hue"
ATTR_SATURATION = "saturation"
ATTR_BRIGHTNESS = "brightness"
ATTR_R = "r"
ATTR_G = "g"
ATTR_B = "b"
ATTR_WHITE = "white"
ATTR_TRANSITION_TIME = "transition_time"
ATTR_FRAMES = "frames"
ATTR_ANIMATION = "animation"
ATTR_SPEED = "speed"
ATTR_COLOR = "color"
ATTR_CONDITION = "condition"
ATTR_FALLBACK = "fallback"
SPEED_SLOW = 30
SPEED_MEDIUM = 15
SPEED_FAST = 5

_PANEL_TARGET_SCHEMA = {
    vol.Exclusive(ATTR_PANEL_ID, "panel_target"): cv.positive_int,
    vol.Exclusive(ATTR_NUMBER, "panel_target"): cv.positive_int,
    vol.Optional(ATTR_WHITE, default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    vol.Optional(ATTR_TRANSITION_TIME): cv.positive_int,
}

_COLOR_SCHEMA = vol.Any(
    vol.Schema(
        {
            vol.Required(ATTR_R): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(ATTR_G): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(ATTR_B): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }
    ),
    vol.Schema(
        {
            vol.Required(ATTR_HUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
            vol.Required(ATTR_SATURATION): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        }
    ),
)

_FRAME_SCHEMA = vol.Any(
    vol.Schema(
        {
            vol.Required(ATTR_R): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(ATTR_G): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(ATTR_B): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional(ATTR_WHITE, default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional(ATTR_TRANSITION_TIME, default=DEFAULT_TRANSITION_TIME): cv.positive_int,
        }
    ),
    vol.Schema(
        {
            vol.Required(ATTR_HUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
            vol.Required(ATTR_SATURATION): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional(ATTR_WHITE, default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Optional(ATTR_TRANSITION_TIME, default=DEFAULT_TRANSITION_TIME): cv.positive_int,
        }
    ),
)

_PANEL_FRAMES_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_PANEL_ID, "panel_target"): cv.positive_int,
        vol.Exclusive(ATTR_NUMBER, "panel_target"): cv.positive_int,
        vol.Required(ATTR_FRAMES): vol.All([_FRAME_SCHEMA], vol.Length(min=2)),
    }
)

_PANEL_ANIMATION_SCHEMA = vol.Schema(
    {
        vol.Exclusive(ATTR_PANEL_ID, "panel_target"): cv.positive_int,
        vol.Exclusive(ATTR_NUMBER, "panel_target"): cv.positive_int,
        vol.Required(ATTR_ANIMATION): vol.In(["breathe", "pulse", "strobe", "cycle", "fade"]),
        vol.Required(ATTR_COLOR): _COLOR_SCHEMA,
        vol.Optional(ATTR_SPEED, default="medium"): vol.In(["slow", "medium", "fast"]),
        vol.Optional(ATTR_FALLBACK): _COLOR_SCHEMA,
        vol.Optional(ATTR_CONDITION, default=True): vol.Any(str, bool),
    }
)

_PANEL_SCHEMA = vol.Any(
    vol.Schema(
        {
            **_PANEL_TARGET_SCHEMA,
            vol.Required(ATTR_HUE): vol.All(vol.Coerce(int), vol.Range(min=0, max=360)),
            vol.Required(ATTR_SATURATION): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Required(ATTR_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        }
    ),
    vol.Schema(
        {
            **_PANEL_TARGET_SCHEMA,
            vol.Required(ATTR_R): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(ATTR_G): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
            vol.Required(ATTR_B): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
        }
    ),
    _PANEL_FRAMES_SCHEMA,
    _PANEL_ANIMATION_SCHEMA,
)

SERVICE_SET_PANELS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_PANELS): vol.All([_PANEL_SCHEMA], vol.Length(min=1)),
    }
)


def _resolve_rgb(color: Mapping[str, int]) -> tuple[int, int, int]:
    if ATTR_HUE in color:
        return tuple(
            round(c * 255)
            for c in colorsys.hsv_to_rgb(
                color[ATTR_HUE] / 360,
                color[ATTR_SATURATION] / 100,
                color[ATTR_BRIGHTNESS] / 100,
            )
        )  # type: ignore[return-value]
    return color[ATTR_R], color[ATTR_G], color[ATTR_B]


def _get_speed_transition_time(speed: str) -> int:
    """Map speed string to transition time."""
    return {"slow": SPEED_SLOW, "medium": SPEED_MEDIUM, "fast": SPEED_FAST}.get(
        speed, SPEED_MEDIUM
    )


def _generate_preset_frames(
    preset: str, color: tuple[int, int, int], speed: str
) -> list[tuple[int, int, int, int, int]]:
    """Generate frame list from animation preset."""
    r, g, b = color
    t = _get_speed_transition_time(speed)
    t_short = max(1, t // 2)  # Shorter off-time for snappier animations

    if preset == "breathe":
        # Smooth breathing: peak -> dim -> off -> dim -> peak
        return [
            (r, g, b, 0, t),
            (round(r * 0.6), round(g * 0.6), round(b * 0.6), 0, t),
            (0, 0, 0, 0, t),
            (round(r * 0.6), round(g * 0.6), round(b * 0.6), 0, t),
        ]
    elif preset == "pulse":
        # On longer, off briefly
        return [(r, g, b, 0, t), (0, 0, 0, 0, t_short)]
    elif preset == "strobe":
        # Quick double flash, on longer
        return [
            (r, g, b, 0, t_short),
            (0, 0, 0, 0, t_short),
            (r, g, b, 0, t_short),
            (0, 0, 0, 0, t),
        ]
    elif preset == "cycle":
        # Cycle through brightness levels
        return [
            (r, g, b, 0, t),
            (round(r * 0.7), round(g * 0.7), round(b * 0.7), 0, t),
            (round(r * 0.4), round(g * 0.4), round(b * 0.4), 0, t),
        ]
    elif preset == "fade":
        # Fade in and out
        return [
            (0, 0, 0, 0, t),
            (round(r * 0.3), round(g * 0.3), round(b * 0.3), 0, t),
            (round(r * 0.7), round(g * 0.7), round(b * 0.7), 0, t),
            (r, g, b, 0, t),
        ]
    return [(r, g, b, 0, t)]


def _resolve_frame(frame: Mapping[str, int]) -> tuple[int, int, int, int, int]:
    r, g, b = _resolve_rgb(frame) if ATTR_HUE in frame else (frame[ATTR_R], frame[ATTR_G], frame[ATTR_B])
    return (r, g, b, frame[ATTR_WHITE], frame[ATTR_TRANSITION_TIME])


def _resolve_panels(
    panels: list[Mapping[str, Any]], panel_order: list[int]
) -> list[dict[str, Any]]:
    """Resolve service input to internal format.
    
    Returns list of dicts with keys:
    - panel_id: int
    - frames: list of (r, g, b, w, t) tuples (for non-conditional panels)
    - animation: preset name (for conditional/animation panels)
    - color: (r, g, b) tuple (for animation panels)
    - speed: str (for animation panels)
    - fallback: (r, g, b) tuple or None (for conditional panels)
    - condition: str or None (for conditional panels)
    """
    resolved_panels: list[dict[str, Any]] = []

    for panel in panels:
        if ATTR_PANEL_ID in panel:
            panel_id = panel[ATTR_PANEL_ID]
        else:
            panel_number = panel[ATTR_NUMBER]
            if panel_number < 1 or panel_number > len(panel_order):
                raise HomeAssistantError(
                    f"Panel number {panel_number} is out of range for this Nanoleaf"
                )
            panel_id = panel_order[panel_number - 1]

        resolved: dict[str, Any] = {"panel_id": panel_id}

        # Handle animation preset
        if ATTR_ANIMATION in panel:
            animation = panel[ATTR_ANIMATION]
            color = _resolve_rgb(panel[ATTR_COLOR])
            speed = panel[ATTR_SPEED]
            resolved["animation"] = animation
            resolved["color"] = color
            resolved["speed"] = speed
            resolved["condition"] = panel.get(ATTR_CONDITION)
            if ATTR_FALLBACK in panel:
                resolved["fallback"] = _resolve_rgb(panel[ATTR_FALLBACK])
            else:
                resolved["fallback"] = None
        # Handle manual frames
        elif ATTR_FRAMES in panel:
            frames = [_resolve_frame(f) for f in panel[ATTR_FRAMES]]
            resolved["frames"] = frames
        # Handle single color
        else:
            r, g, b = (
                _resolve_rgb(panel)
                if ATTR_HUE in panel
                else (panel[ATTR_R], panel[ATTR_G], panel[ATTR_B])
            )
            white = panel.get(ATTR_WHITE, 0)
            transition_time = panel.get(ATTR_TRANSITION_TIME, DEFAULT_TRANSITION_TIME)
            resolved["frames"] = [(r, g, b, white, transition_time)]

        resolved_panels.append(resolved)

    return resolved_panels


def _get_nanoleaf_config_entry(hass: HomeAssistant, entity_id: str) -> ConfigEntry:
    entity_entry = er.async_get(hass).async_get(entity_id)
    if entity_entry is None:
        raise HomeAssistantError(f"Entity {entity_id} was not found in the registry")

    entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
    if entry is None or entry.domain != "nanoleaf":
        raise HomeAssistantError(
            f"Entity {entity_id} is not managed by the Nanoleaf integration"
        )

    if CONF_HOST not in entry.data or CONF_TOKEN not in entry.data:
        raise HomeAssistantError("Nanoleaf host/token data is missing in config entry")

    return entry


def _get_or_create_api_client(
    hass: HomeAssistant, nanoleaf_entry_id: str, host: str, token: str
) -> NanoleafApiClient:
    """Return the cached NanoleafApiClient for the given entry, creating it if needed."""
    clients: dict[str, NanoleafApiClient] = (
        hass.data.setdefault(DOMAIN, {}).setdefault("clients", {})
    )
    if nanoleaf_entry_id not in clients:
        clients[nanoleaf_entry_id] = NanoleafApiClient(hass, host, token)
    return clients[nanoleaf_entry_id]


def _get_or_create_writer(
    hass: HomeAssistant, api_client: NanoleafApiClient, nanoleaf_entry_id: str
) -> PanelWriter:
    """Return the PanelWriter for the given entry, creating it if needed.

    Every write to a device must go through its one writer, so that the panel
    entities and the set_panels service are serialized against each other.
    """
    writers: dict[str, PanelWriter] = (
        hass.data.setdefault(DOMAIN, {}).setdefault(DATA_WRITERS, {})
    )
    if nanoleaf_entry_id not in writers:
        writers[nanoleaf_entry_id] = PanelWriter(hass, api_client, nanoleaf_entry_id)
    return writers[nanoleaf_entry_id]


async def _async_write_panels(
    hass: HomeAssistant,
    api_client: NanoleafApiClient,
    nanoleaf_entry_id: str,
    all_panel_ids: list[int],
    panels_override: dict[int, list[tuple[int, int, int, int, int]]],
    *,
    has_animation_panels: bool = False,
) -> None:
    """Merge panel overrides with current device/cache state and write to device.

    panels_override maps panel_id -> list of (r, g, b, w, t) frames.
    Panels not in panels_override keep their current device/cache color.
    has_animation_panels keeps the device in custom (loop) mode even when all
    current frames are static, to prevent flicker when conditions toggle.
    """
    writer = _get_or_create_writer(hass, api_client, nanoleaf_entry_id)
    writer.panel_ids = all_panel_ids
    await writer.async_write(
        panels_override, has_animation_panels=has_animation_panels
    )


async def _async_handle_set_panels(hass: HomeAssistant, service_call: ServiceCall) -> None:
    entry = _get_nanoleaf_config_entry(hass, service_call.data[ATTR_ENTITY_ID])

    api_client = _get_or_create_api_client(
        hass, entry.entry_id, entry.data[CONF_HOST], entry.data[CONF_TOKEN]
    )

    all_panel_ids = await api_client.async_get_panel_order()
    resolved_panels = _resolve_panels(service_call.data[ATTR_PANELS], all_panel_ids)

    # Keep custom mode if any panel is animation-capable to prevent flicker
    # when conditions toggle between animation and fallback.
    has_animation_panels = any("animation" in p for p in resolved_panels)

    panels_override: dict[int, list[tuple[int, int, int, int, int]]] = {}
    for panel in resolved_panels:
        if "condition" in panel:
            condition_value = panel["condition"]
            try:
                if isinstance(condition_value, bool):
                    use_animation = condition_value
                elif condition_value is None:
                    use_animation = False
                else:
                    condition_result = Template(condition_value, hass).render()
                    normalized = str(condition_result).strip().lower()
                    use_animation = normalized not in (
                        "false", "0", "", "none", "off", "unavailable", "unknown",
                    )
            except Exception:  # noqa: BLE001
                use_animation = False

            if use_animation:
                frames = _generate_preset_frames(
                    panel["animation"], panel["color"], panel["speed"]
                )
            elif panel.get("fallback"):
                r, g, b = panel["fallback"]
                frames = [(r, g, b, 0, DEFAULT_TRANSITION_TIME)]
            else:
                frames = [(0, 0, 0, 0, DEFAULT_TRANSITION_TIME)]
        elif "animation" in panel:
            frames = _generate_preset_frames(
                panel["animation"], panel["color"], panel["speed"]
            )
        else:
            frames = panel["frames"]

        panels_override[panel["panel_id"]] = frames

    await _async_write_panels(
        hass,
        api_client,
        entry.entry_id,
        all_panel_ids,
        panels_override,
        has_animation_panels=has_animation_panels,
    )


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Optional legacy YAML support.
    if DOMAIN in config:
        hass.data[DOMAIN][DATA_YAML_CONFIGURED] = True

        async def async_handle_set_panels(call: ServiceCall) -> None:
            await _async_handle_set_panels(hass, call)

        if not hass.services.has_service(DOMAIN, SERVICE_SET_PANELS):
            hass.services.async_register(
                DOMAIN,
                SERVICE_SET_PANELS,
                async_handle_set_panels,
                schema=SERVICE_SET_PANELS_SCHEMA,
            )

    return True


async def _async_options_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    async def async_handle_set_panels(call: ServiceCall) -> None:
        await _async_handle_set_panels(hass, call)

    if not hass.services.has_service(DOMAIN, SERVICE_SET_PANELS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_PANELS,
            async_handle_set_panels,
            schema=SERVICE_SET_PANELS_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    remaining_entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id
    ]

    # Remove service when the last config entry is removed and YAML is not used.
    if (
        not remaining_entries
        and not hass.data.get(DOMAIN, {}).get(DATA_YAML_CONFIGURED, False)
        and hass.services.has_service(DOMAIN, SERVICE_SET_PANELS)
    ):
        hass.services.async_remove(DOMAIN, SERVICE_SET_PANELS)

    return True
