"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.assist_learner.const import CONF_LANGUAGE, CONF_TARGET_AGENT, DOMAIN


async def test_flow_creates_entry(hass):
    await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("conversation.claude_haiku", "unknown")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TARGET_AGENT: "conversation.claude_haiku", CONF_LANGUAGE: "en"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TARGET_AGENT] == "conversation.claude_haiku"


async def test_flow_rejects_self(hass):
    await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("conversation.assist_learner", "unknown")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TARGET_AGENT: "conversation.assist_learner", CONF_LANGUAGE: "en"},
    )
    assert result["errors"][CONF_TARGET_AGENT] == "agent_is_self"
