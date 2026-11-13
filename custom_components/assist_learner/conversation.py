"""Proxy conversation agent: forwards to the target agent and records the miss."""

from __future__ import annotations

import logging
import uuid
from typing import Literal, override

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .capture import ServiceCallCapture, extract_tool_calls, normalise
from .const import CONF_TARGET_AGENT, DOMAIN
from .models import MissRecord

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AssistLearnerAgent(entry)])


class AssistLearnerAgent(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """Sits between Assist and the LLM agent; never alters the target's reply."""

    _attr_has_entity_name = False
    _attr_name = "Assist Learner"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = entry.entry_id

    @property
    def _target_id(self) -> str:
        return self._entry.data[CONF_TARGET_AGENT]

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        target = conversation.async_get_agent(self.hass, self._target_id)
        if target is not None and target is not self:
            return target.supported_languages
        return MATCH_ALL

    @override
    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        runtime = self._entry.runtime_data
        start = len(chat_log.content)
        result: conversation.ConversationResult | None = None
        error: str | None = None

        with ServiceCallCapture(self.hass, user_input.context) as capture:
            try:
                result = await conversation.async_converse(
                    self.hass,
                    text=user_input.text,
                    conversation_id=user_input.conversation_id,
                    context=user_input.context,
                    language=user_input.language,
                    agent_id=self._target_id,
                    device_id=user_input.device_id,
                    satellite_id=user_input.satellite_id,
                    extra_system_prompt=user_input.extra_system_prompt,
                )
            except Exception as err:  # target missing or crashed: record, then re-raise
                error = f"{type(err).__name__}: {err}"
                self._schedule_record(user_input, [], [], False, error)
                raise

        response = result.response
        failed = response.response_type == intent.IntentResponseType.ERROR
        if failed:
            error = "target agent returned an error response"
        tool_calls = extract_tool_calls(chat_log.content[start:])
        self._schedule_record(user_input, tool_calls, capture.actions, not failed, error)
        runtime.store.bump("fallbacks")
        return result

    def _schedule_record(
        self, user_input, tool_calls, actions, success, error
    ) -> None:
        """Write the miss in the background so the reply is never delayed."""
        self._entry.async_create_background_task(
            self.hass,
            self._record(user_input, tool_calls, actions, success, error),
            name=f"{DOMAIN}_record_miss",
        )

    async def _record(self, user_input, tool_calls, actions, success, error) -> None:
        runtime = self._entry.runtime_data
        area_id = None
        if user_input.device_id:
            device = dr.async_get(self.hass).async_get(user_input.device_id)
            area_id = device.area_id if device else None
        miss = MissRecord(
            id=uuid.uuid4().hex,
            timestamp=dt_util.utcnow().isoformat(),
            text=user_input.text,
            normalised=normalise(user_input.text),
            language=user_input.language,
            device_id=user_input.device_id,
            area_id=area_id,
            target_agent=self._target_id,
            tool_calls=tool_calls,
            actions=actions,
            success=success,
            error=error,
        )
        runtime.store.misses.put(miss.id, miss.as_dict())
        runtime.async_notify_listeners()
