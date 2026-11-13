"""Config flow and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AI_TASK,
    CONF_AUTO_APPROVE,
    CONF_AUTO_APPROVE_THRESHOLD,
    CONF_LANGUAGE,
    CONF_LOOKBACK_DAYS,
    CONF_MAX_PROPOSALS,
    CONF_MIN_OCCURRENCES,
    CONF_NOTIFY,
    CONF_RETENTION_DAYS,
    CONF_RUN_TIME,
    CONF_TARGET_AGENT,
    DEFAULT_OPTIONS,
    DOMAIN,
    NAME,
    NOTIFY_NONE,
    NOTIFY_PERSISTENT,
)


class AssistLearnerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        warnings_note = ""
        if user_input is not None:
            target = user_input[CONF_TARGET_AGENT]
            ent = self.hass.states.get(target)
            if ent is None:
                errors[CONF_TARGET_AGENT] = "agent_not_found"
            elif target == "conversation.assist_learner":
                errors[CONF_TARGET_AGENT] = "agent_is_self"
            else:
                return self.async_create_entry(title=NAME, data=user_input)
        schema = vol.Schema(
            {
                vol.Required(CONF_TARGET_AGENT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="conversation")
                ),
                vol.Required(CONF_LANGUAGE, default=self.hass.config.language): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={"note": warnings_note},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AssistLearnerOptionsFlow()


class AssistLearnerOptionsFlow(OptionsFlow):
    """Core options. The full option set (panel-related, actions, music) is added in later phases."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = {**DEFAULT_OPTIONS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_RUN_TIME, default=o[CONF_RUN_TIME]): selector.TimeSelector(),
                vol.Required(CONF_MIN_OCCURRENCES, default=o[CONF_MIN_OCCURRENCES]): vol.All(
                    int, vol.Range(min=1, max=10)
                ),
                vol.Required(CONF_LOOKBACK_DAYS, default=o[CONF_LOOKBACK_DAYS]): vol.All(
                    int, vol.Range(min=1, max=365)
                ),
                vol.Required(CONF_AUTO_APPROVE, default=o[CONF_AUTO_APPROVE]): bool,
                vol.Required(
                    CONF_AUTO_APPROVE_THRESHOLD, default=o[CONF_AUTO_APPROVE_THRESHOLD]
                ): vol.All(int, vol.Range(min=1, max=100)),
                vol.Required(CONF_RETENTION_DAYS, default=o[CONF_RETENTION_DAYS]): vol.All(
                    int, vol.Range(min=0, max=365)
                ),
                vol.Required(CONF_MAX_PROPOSALS, default=o[CONF_MAX_PROPOSALS]): vol.All(
                    int, vol.Range(min=1, max=100)
                ),
                vol.Required(CONF_NOTIFY, default=o[CONF_NOTIFY]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[NOTIFY_NONE, NOTIFY_PERSISTENT],
                        translation_key="notify",
                    )
                ),
                vol.Optional(
                    CONF_AI_TASK, description={"suggested_value": o[CONF_AI_TASK]}
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
