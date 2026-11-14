"""Config flow and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.conversation import HOME_ASSISTANT_AGENT
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_AI_TASK,
    CONF_AREA_PLAYERS,
    CONF_AUTO_APPROVE,
    CONF_AUTO_APPROVE_THRESHOLD,
    CONF_CARRIER_VERBS,
    CONF_DEFAULT_PLAYER,
    CONF_ENABLED_LEARNERS,
    CONF_EXEC_LOG_SIZE,
    CONF_LANGUAGE,
    CONF_LLM_GENERALISE,
    CONF_LOOKBACK_DAYS,
    CONF_MAX_PROPOSALS,
    CONF_MAX_SENTENCES,
    CONF_MIN_OCCURRENCES,
    CONF_NOTIFY,
    CONF_PREFER_OWN_PLAYER,
    CONF_RATE_LIMIT,
    CONF_RETENTION_DAYS,
    CONF_RUN_TIME,
    CONF_SEND_STATES,
    CONF_TARGET_AGENT,
    DEFAULT_OPTIONS,
    DOMAIN,
    NAME,
    NOTIFY_NONE,
    NOTIFY_PERSISTENT,
)
from .players import list_players

SELF_AGENT = "conversation.assist_learner"


def target_error(hass: HomeAssistant, target: str) -> str | None:
    """Why this target can't be used, or None. The target must be an LLM agent, not a local one."""
    if target == HOME_ASSISTANT_AGENT:
        return "agent_is_local"
    if target == SELF_AGENT:
        return "agent_is_self"
    if hass.states.get(target) is None:
        return "agent_not_found"
    return None


def _setup_schema(hass: HomeAssistant, language: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_TARGET_AGENT): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="conversation", exclude_entities=[HOME_ASSISTANT_AGENT, SELF_AGENT])
            ),
            vol.Required(CONF_LANGUAGE, default=language): str,
        }
    )


class AssistLearnerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        warnings_note = ""
        if user_input is not None:
            if err := target_error(self.hass, user_input[CONF_TARGET_AGENT]):
                errors[CONF_TARGET_AGENT] = err
            else:
                return self.async_create_entry(title=NAME, data=user_input)
        schema = _setup_schema(self.hass, self.hass.config.language)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={"note": warnings_note},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the target agent / language without losing learned data."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if err := target_error(self.hass, user_input[CONF_TARGET_AGENT]):
                errors[CONF_TARGET_AGENT] = err
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        schema = _setup_schema(self.hass, entry.data[CONF_LANGUAGE])
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AssistLearnerOptionsFlow()


class AssistLearnerOptionsFlow(OptionsFlow):
    """Options in two steps: learning/review, then runtime (players, commands, safety)."""

    def __init__(self) -> None:
        self._collected: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        return {**DEFAULT_OPTIONS, **self.config_entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            self._collected.update(user_input)
            return await self.async_step_runtime()
        o = self._current()
        schema = vol.Schema(
            {
                vol.Required(CONF_RUN_TIME, default=o[CONF_RUN_TIME]): selector.TimeSelector(),
                vol.Required(CONF_MIN_OCCURRENCES, default=o[CONF_MIN_OCCURRENCES]): vol.All(
                    int, vol.Range(min=1, max=10)
                ),
                vol.Required(CONF_LOOKBACK_DAYS, default=o[CONF_LOOKBACK_DAYS]): vol.All(
                    int, vol.Range(min=1, max=365)
                ),
                vol.Required(CONF_ENABLED_LEARNERS, default=o[CONF_ENABLED_LEARNERS]): cv_multi(
                    ["music_play"]
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
                        custom_value=True,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_runtime(self, user_input: dict[str, Any] | None = None) -> Any:
        if user_input is not None:
            self._collected.update(user_input)
            for key in (CONF_AI_TASK, CONF_DEFAULT_PLAYER):
                if not self._collected.get(key):
                    self._collected[key] = None
            return await self.async_step_areas()
        o = self._current()
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_AI_TASK, description={"suggested_value": o[CONF_AI_TASK]}
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
                vol.Required(CONF_SEND_STATES, default=o[CONF_SEND_STATES]): bool,
                vol.Required(CONF_MAX_SENTENCES, default=o[CONF_MAX_SENTENCES]): vol.All(
                    int, vol.Range(min=2, max=50)
                ),
                vol.Required(CONF_RATE_LIMIT, default=o[CONF_RATE_LIMIT]): vol.All(
                    int, vol.Range(min=1, max=60)
                ),
                vol.Required(CONF_EXEC_LOG_SIZE, default=o[CONF_EXEC_LOG_SIZE]): vol.All(
                    int, vol.Range(min=10, max=5000)
                ),
                vol.Required(CONF_CARRIER_VERBS, default=o[CONF_CARRIER_VERBS]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=o[CONF_CARRIER_VERBS], multiple=True, custom_value=True
                    )
                ),
                vol.Optional(
                    CONF_DEFAULT_PLAYER, description={"suggested_value": o[CONF_DEFAULT_PLAYER]}
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
                vol.Required(CONF_PREFER_OWN_PLAYER, default=o[CONF_PREFER_OWN_PLAYER]): bool,
                vol.Required(CONF_LLM_GENERALISE, default=o[CONF_LLM_GENERALISE]): bool,
            }
        )
        return self.async_show_form(step_id="runtime", data_schema=schema)


    async def async_step_areas(self, user_input: dict[str, Any] | None = None) -> Any:
        """Preferred player for each area that has several Music Assistant players."""
        by_area: dict[str, list[str]] = {}
        for p in list_players(self.hass):
            if p.area_id:
                by_area.setdefault(p.area_id, []).append(p.entity_id)
        multi = {a: ents for a, ents in by_area.items() if len(ents) > 1}
        if user_input is not None or not multi:
            chosen = {a: user_input[a] for a in multi if user_input and user_input.get(a)}
            return self.async_create_entry(data={**self._collected, CONF_AREA_PLAYERS: chosen})
        current = self._current()[CONF_AREA_PLAYERS]
        schema = vol.Schema(
            {
                vol.Optional(a, description={"suggested_value": current.get(a)}): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player", include_entities=ents)
                )
                for a, ents in multi.items()
            }
        )
        return self.async_show_form(step_id="areas", data_schema=schema)


def cv_multi(options: list[str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=True)
    )
