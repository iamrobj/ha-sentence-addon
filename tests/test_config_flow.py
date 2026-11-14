"""Config flow tests."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
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
    with pytest.raises(InvalidData):  # excluded from the picker, so the schema refuses it
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_TARGET_AGENT: "conversation.assist_learner", CONF_LANGUAGE: "en"},
        )


async def test_flow_rejects_home_assistant_local_agent(hass):
    await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("conversation.home_assistant", "unknown")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TARGET_AGENT: "conversation.home_assistant", CONF_LANGUAGE: "en"}
        )


async def test_local_target_raises_repair_and_reconfigure_fixes_it(hass):
    from homeassistant.helpers import issue_registry as ir
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.assist_learner.repairs import TARGET_ISSUE

    await async_setup_component(hass, "homeassistant", {})
    # an entry created before the check existed, pointing at the local agent
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN,
                            data={CONF_TARGET_AGENT: "conversation.home_assistant", CONF_LANGUAGE: "en"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, TARGET_ISSUE) is not None

    hass.states.async_set("conversation.claude", "unknown")
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TARGET_AGENT: "conversation.home_assistant", CONF_LANGUAGE: "en"}
        )
    done = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TARGET_AGENT: "conversation.claude", CONF_LANGUAGE: "en"}
    )
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_TARGET_AGENT] == "conversation.claude"
    assert ir.async_get(hass).async_get_issue(DOMAIN, TARGET_ISSUE) is None


def test_target_error_backstop(hass):
    from custom_components.assist_learner.config_flow import target_error

    assert target_error(hass, "conversation.home_assistant") == "agent_is_local"
    assert target_error(hass, "conversation.assist_learner") == "agent_is_self"
    assert target_error(hass, "conversation.missing") == "agent_not_found"
