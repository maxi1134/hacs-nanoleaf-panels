"""Config flow for Nanoleaf Panels."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
)

from .const import CONF_EXPOSE_PANELS, CONF_NANOLEAF_ENTITY, DOMAIN


class NanoleafPanelsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nanoleaf Panels."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            entity_id: str = user_input[CONF_NANOLEAF_ENTITY]

            # Prevent the same Nanoleaf entity from being configured twice.
            for entry in self._async_current_entries():
                if entry.options.get(CONF_NANOLEAF_ENTITY) == entity_id:
                    return self.async_abort(reason="already_configured")

            state = self.hass.states.get(entity_id)
            title = state.name if state else entity_id

            return self.async_create_entry(
                title=title,
                data={},
                options={
                    CONF_NANOLEAF_ENTITY: entity_id,
                    CONF_EXPOSE_PANELS: True,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NANOLEAF_ENTITY): EntitySelector(
                        EntitySelectorConfig(integration="nanoleaf", domain="light")
                    ),
                }
            ),
        )

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NanoleafPanelsOptionsFlow:
        return NanoleafPanelsOptionsFlow()


class NanoleafPanelsOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Nanoleaf Panels."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_EXPOSE_PANELS,
                        default=self.config_entry.options.get(CONF_EXPOSE_PANELS, False),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_NANOLEAF_ENTITY,
                        default=self.config_entry.options.get(CONF_NANOLEAF_ENTITY, ""),
                    ): EntitySelector(
                        EntitySelectorConfig(integration="nanoleaf", domain="light")
                    ),
                }
            ),
        )
