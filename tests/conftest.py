"""Shared fixtures."""

from __future__ import annotations

import pytest
from homeassistant.components import conversation
from homeassistant.components.conversation import chat_log as chat_log_mod
from homeassistant.core import Context
from homeassistant.helpers import intent, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.assist_learner.const import CONF_LANGUAGE, CONF_TARGET_AGENT, DOMAIN

FAKE_TARGET = "fake_target_agent"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components in every test."""
    return


class FakeTargetAgent(conversation.AbstractConversationAgent):
    """Stands in for conversation.claude_haiku: emits a tool call + a service call."""

    def __init__(self, hass, speech="Playing it.", fail=False):
        self.hass = hass
        self.speech = speech
        self.fail = fail
        self.calls: list[str] = []
        self.service_calls: list[dict] = []

    @property
    def supported_languages(self):
        return "*"

    async def async_process(self, user_input):
        self.calls.append(user_input.text)
        log = chat_log_mod.current_chat_log.get()
        if self.service_calls_to_make:
            tool_input = llm.ToolInput(
                tool_name="music_assistant_play",
                tool_args={"query": "x"},
                id="call1",
            )
            log.content.append(
                conversation.AssistantContent(agent_id=FAKE_TARGET, tool_calls=[tool_input])
            )
            log.content.append(
                conversation.ToolResultContent(
                    agent_id=FAKE_TARGET,
                    tool_call_id="call1",
                    tool_name="music_assistant_play",
                    result=llm.ToolResult(data={"ok": True}),
                )
            )
            # child context, as the real LLM tool path would create
            child = Context(parent_id=user_input.context.id)
            for sc in self.service_calls_to_make:
                await self.hass.services.async_call(
                    sc["domain"], sc["service"], sc["data"], blocking=True, context=child
                )
        response = intent.IntentResponse(language=user_input.language)
        if self.fail:
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, "boom")
        else:
            response.async_set_speech(self.speech)
        return conversation.ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )

    service_calls_to_make: list[dict] = []


@pytest.fixture
def entry():
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={CONF_TARGET_AGENT: FAKE_TARGET, CONF_LANGUAGE: "en"},
    )
