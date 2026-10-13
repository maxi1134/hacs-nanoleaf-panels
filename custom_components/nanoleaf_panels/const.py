"""Shared constants for the Nanoleaf Panels integration.

Kept in their own module so ``panel_writer`` can be imported from
``__init__`` without a circular import.
"""

from __future__ import annotations

DOMAIN = "nanoleaf_panels"

CONF_EXPOSE_PANELS = "expose_panels"
CONF_NANOLEAF_ENTITY = "nanoleaf_entity"

DEFAULT_TRANSITION_TIME = 20

# A fully dark panel frame: (r, g, b, w, transition_time).
BLACK: tuple[int, int, int, int, int] = (0, 0, 0, 0, DEFAULT_TRANSITION_TIME)

# Keys used inside hass.data[DOMAIN].
DATA_PANEL_STATE = "panel_state"
DATA_WRITERS = "writers"
