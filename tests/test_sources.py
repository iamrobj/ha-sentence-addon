"""Sentence sources match what HA's default agent actually loads (language variant dirs, config intents,
automations from any file), and AI Task slots/actions survive real providers' schema conversion."""

from __future__ import annotations

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.assist_learner import recognizer
from custom_components.assist_learner.commands.generate import STRUCTURE, normalise_result
from custom_components.assist_learner.const import (
    CONF_AI_TASK,
    CONF_LANGUAGE,
    CONF_TARGET_AGENT,
    DOMAIN,
)
from tests.test_commands import GOOD_ANSWER, FakeAI, _say


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


async def call(client, type_, **kw):
    await client.send_json_auto_id({"type": f"assist_learner/{type_}", **kw})
    return await client.receive_json()


async def _entry_gb(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        data={CONF_TARGET_AGENT: "conversation.x", CONF_LANGUAGE: "en-GB"},
        options={CONF_AI_TASK: "ai_task.fake"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_sentences_language_variant():
    assert recognizer.sentences_language("en-GB") == "en"
    assert recognizer.sentences_language("en") == "en"
    assert recognizer.sentences_language("de-DE") == "de"
    assert recognizer.sentences_language("zz-ZZ") == "zz-ZZ"


async def test_en_gb_writes_where_ha_loads_and_command_works(hass, tmp_path):
    entry = await _entry_gb(hass)
    stale = tmp_path / "custom_sentences" / "en-GB" / "assist_learner.yaml"
    stale.parent.mkdir(parents=True)
    stale.write_text("language: en-GB\nintents: {}\n")
    hass.states.async_set("sensor.outdoor_temperature", "7.2", {"unit_of_measurement": "°C"})
    ai = FakeAI(hass)
    ai.results.append(GOOD_ANSWER)
    mgr = entry.runtime_data.commands
    cmd = mgr.create("Outside", "outdoor temperature", {"outdoor_temp": "sensor.outdoor_temperature"})
    await mgr.generate(cmd["id"])
    await mgr.approve(cmd["id"])
    await hass.async_block_till_done()

    assert (tmp_path / "custom_sentences" / "en" / "assist_learner.yaml").exists()
    assert not stale.exists()
    assert await _say(hass, "how warm is it outside") == "It's 7 degrees outside."


async def test_discovers_files_in_variant_dir_config_intents_and_all_automations(hass, hass_ws_client, tmp_path):
    d = tmp_path / "custom_sentences" / "en"
    d.mkdir(parents=True)
    (d / "mine.yaml").write_text("language: en\nintents:\n  MyIntent:\n    data:\n    - sentences:\n      - do my thing\n")
    (tmp_path / "automations.yaml").write_text(
        "- id: 'a1'\n  alias: In file\n  triggers:\n  - trigger: conversation\n    command: file sentence\n  actions: []\n"
    )
    entry = await _entry_gb(hass)
    # what conversation's async_setup does with `conversation: intents:` from configuration.yaml / packages
    from homeassistant.components.conversation.agent_manager import get_agent_manager
    from homeassistant.components.conversation.util import get_config_intents

    get_agent_manager(hass).update_config_intents(
        get_config_intents({"conversation": {"intents": {"ConfIntent": ["conf sentence"]}}}, str(tmp_path))
    )
    # an automation loaded from somewhere other than automations.yaml (e.g. a package)
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "pkg1", "alias": "From package", "triggers": {"trigger": "conversation", "command": ["package sentence"]}, "actions": []},
    ]})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    rows = (await call(client, "sentences/list", source="external"))["result"]
    by_title = {r["title"]: r for r in rows}

    assert by_title["MyIntent"]["kind"] == "file" and by_title["MyIntent"]["external_editable"]
    assert by_title["ConfIntent"]["kind"] == "config" and by_title["ConfIntent"]["sentences"] == ["conf sentence"]
    assert not by_title["ConfIntent"]["external_editable"]
    assert by_title["From package"]["sentences"] == ["package sentence"]
    assert not by_title["From package"]["external_editable"]
    assert entry.data[CONF_LANGUAGE] == "en-GB"


async def test_automation_in_file_is_editable_when_loaded(hass, hass_ws_client, tmp_path):
    (tmp_path / "automations.yaml").write_text(
        "- id: 'a1'\n  alias: In file\n  triggers:\n  - trigger: conversation\n    command: file sentence\n  actions: []\n"
    )
    await _entry_gb(hass)
    assert await async_setup_component(hass, "automation", {"automation": [
        {"id": "a1", "alias": "In file", "triggers": {"trigger": "conversation", "command": "file sentence"}, "actions": []},
    ]})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    rows = (await call(client, "sentences/list", source="external"))["result"]
    row = next(r for r in rows if r["title"] == "In file")
    assert row["external_editable"] and row["sentences"] == ["file sentence"]


def test_ai_task_structure_has_no_fieldless_objects():
    """Real providers turn a field-less `object` selector into an empty-object-only schema."""
    for name, spec in STRUCTURE.items():
        assert "object" not in spec["selector"], name


def test_normalise_parses_json_text_fields():
    out = normalise_result({
        "type": "action",
        "sentences": ["set the lounge to {level} percent"],
        "slots": '```json\n{"level": {"type": "number", "min": 0, "max": 100, "step": 1}}\n```',
        "actions": '[{"service": "light.turn_on", "targets": ["lounge"], "data": {"brightness_pct": "{level}"}}]',
        "response_template": "ok",
    })
    assert out["slots"]["level"]["max"] == 100
    assert out["actions"][0]["service"] == "light.turn_on"
    assert out["warnings"] == []

    bad = normalise_result({"sentences": ["x"], "slots": "{level: oops", "response_template": "ok"})
    assert bad["slots"] == {} and "isn't valid JSON" in bad["warnings"][0]
