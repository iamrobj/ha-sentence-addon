"""Phase 1: proxy passes results through unchanged and records misses."""

from __future__ import annotations

from homeassistant.components import conversation
from homeassistant.core import Context, ServiceCall
from homeassistant.setup import async_setup_component

from .conftest import FAKE_TARGET, FakeTargetAgent


async def _setup(hass, entry, agent):
    assert await async_setup_component(hass, "homeassistant", {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    # register fake target under the id the proxy will forward to
    conversation.async_set_agent(
        hass, type("E", (), {"entry_id": FAKE_TARGET})(), agent
    )
    await hass.async_block_till_done()


async def test_proxy_passthrough_and_miss_recorded(hass, entry):
    played: list[ServiceCall] = []

    async def play(call: ServiceCall):
        played.append(call)

    hass.services.async_register("music_assistant", "play_media", play)
    agent = FakeTargetAgent(hass, speech="Playing Lose Control.")
    agent.service_calls_to_make = [
        {
            "domain": "music_assistant",
            "service": "play_media",
            "data": {"media_id": "Lose Control", "artist": "Teddy Swims"},
        }
    ]
    await _setup(hass, entry, agent)

    result = await conversation.async_converse(
        hass,
        "put on Lose Control from Teddy Swims in the living room",
        None,
        Context(),
        agent_id="conversation.assist_learner",
    )
    await hass.async_block_till_done()

    assert result.response.speech["plain"]["speech"] == "Playing Lose Control."
    assert len(played) == 1

    store = entry.runtime_data.store
    misses = store.misses.values()
    assert len(misses) == 1
    miss = misses[0]
    assert miss["success"] is True
    assert miss["normalised"] == "put on lose control from teddy swims in the living room"
    assert miss["tool_calls"][0]["tool_name"] == "music_assistant_play"
    assert miss["actions"] == [
        {
            "domain": "music_assistant",
            "service": "play_media",
            "data": {"media_id": "Lose Control", "artist": "Teddy Swims"},
        }
    ]
    assert store.today("fallbacks") == 1


async def test_target_error_response_recorded_as_failed(hass, entry):
    agent = FakeTargetAgent(hass, fail=True)
    await _setup(hass, entry, agent)
    result = await conversation.async_converse(
        hass, "gibberish", None, Context(), agent_id="conversation.assist_learner"
    )
    await hass.async_block_till_done()
    assert result.response.response_type.value == "error"
    miss = entry.runtime_data.store.misses.values()[0]
    assert miss["success"] is False
