"""Phases 6-7: custom answer commands and action commands."""

from __future__ import annotations

import copy

import pytest
from homeassistant.components import conversation
from homeassistant.core import Context, SupportsResponse
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.assist_learner.commands.manager import CommandError
from tests.test_proposer_run import _entry

GOOD_ANSWER = {
    "type": "answer",
    "sentences": ["how warm is it outside", "what's the outside temperature"],
    "slots": {},
    "response_template": "It's {{ states('sensor.outdoor_temperature') | round(0) | int }} degrees outside.",
    "unavailable_template": "I can't read the outdoor sensor right now.",
    "actions": [],
    "notes": "ok",
    "warnings": [],
}

GOOD_ACTION = {
    "type": "action",
    "sentences": ["turn the lounge lights to {level} percent", "set the lounge lights to {level} percent"],
    "slots": {"level": {"type": "number", "min": 0, "max": 100, "step": 1}},
    "response_template": "Lounge lights set to {{ slots.level | int }} percent.",
    "unavailable_template": "",
    "actions": [
        {"service": "light.turn_on", "targets": ["lounge"], "data": {"brightness_pct": "{level}"}}
    ],
    "notes": "",
    "warnings": [],
}


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


class FakeAI:
    def __init__(self, hass):
        self.results: list[dict] = []
        self.payloads: list[dict] = []
        hass.services.async_register(
            "ai_task", "generate_data", self._handle, supports_response=SupportsResponse.ONLY
        )

    async def _handle(self, call):
        self.payloads.append(dict(call.data))
        return {"data": copy.deepcopy(self.results.pop(0))}


async def _setup(hass, **options):
    entry = await _entry(hass, ai_task_entity="ai_task.fake", **options)
    await async_setup_component(hass, "light", {})
    hass.states.async_set("sensor.outdoor_temperature", "7.2", {"unit_of_measurement": "°C", "friendly_name": "Outdoor"})
    hass.states.async_set("light.lounge", "on", {"friendly_name": "Lounge", "brightness": 100})
    ai = FakeAI(hass)
    return entry, entry.runtime_data.commands, ai


async def _answer_cmd(hass, entry, mgr, ai, result=None):
    ai.results.append(result or GOOD_ANSWER)
    cmd = mgr.create(
        "Outside temp",
        "when I ask how warm it is outside, answer with the outdoor temperature",
        {"outdoor_temp": "sensor.outdoor_temperature"},
    )
    return await mgr.generate(cmd["id"])


async def _say(hass, text):
    return (
        await conversation.async_converse(hass, text, None, Context(), agent_id=conversation.HOME_ASSISTANT_AGENT)
    ).response.speech["plain"]["speech"]


# ------------------------------------------------------------------ answers -----------------------------


async def test_generate_preview_approve_and_answer_locally(hass):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai)
    assert cmd["status"] == "pending" and cmd["validation"]["ok"], cmd["validation"]
    assert cmd["validation"]["preview"] == "It's 7 degrees outside."
    # the exact payload sent to the LLM contains only the selected entity
    sent = ai.payloads[0]["instructions"]
    assert "sensor.outdoor_temperature" in sent and "light.lounge" not in sent

    await mgr.approve(cmd["id"])
    assert await _say(hass, "how warm is it outside") == "It's 7 degrees outside."
    assert len(ai.payloads) == 1  # no LLM involved at runtime
    assert entry.runtime_data.store.commands.get(cmd["id"])["hits"] == 1

    hass.states.async_set("sensor.outdoor_temperature", "unavailable")
    assert await _say(hass, "how warm is it outside") == "I can't read the outdoor sensor right now."


async def test_regenerate_with_feedback_and_history_revert(hass):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai)
    second = {**GOOD_ANSWER, "response_template": "Outside it is {{ states('sensor.outdoor_temperature') | int }}."}
    ai.results.append(second)
    cmd = await mgr.generate(cmd["id"], feedback="shorter")
    assert "shorter" in ai.payloads[1]["instructions"] and "Previous result" in ai.payloads[1]["instructions"]
    assert cmd["response_template"].startswith("Outside it is")
    assert len(mgr.history(cmd["id"])) == 2  # empty draft, then first generation
    cmd = await mgr.revert(cmd["id"], 1)
    assert cmd["response_template"] == GOOD_ANSWER["response_template"]


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"response_template": "{{ states('sensor.secret') }}"}, "not in the command's entity list"),
        ({"response_template": "{{ states(x) }}"}, "literal entity id"),
        ({"response_template": "{% for s in states %}{{ s }}{% endfor %}"}, "not allowed"),
        ({"sentences": ["what's the weather outside {thing}", "outside {thing}"]}, "undefined slot"),
        ({"sentences": ["turn on the kitchen lights", "switch on the kitchen lights"]}, "too greedy"),
        ({"sentences": ["only one sentence"]}, "at least 2"),
        ({"sentences": ["(unclosed", "another one"]}, "does not parse"),
        ({"response_template": ""}, "response template is required"),
    ],
)
async def test_bad_generations_rejected_with_clear_message(hass, mutation, fragment):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai, {**GOOD_ANSWER, **mutation})
    assert not cmd["validation"]["ok"]
    assert any(fragment in e for e in cmd["validation"]["errors"]), cmd["validation"]["errors"]
    with pytest.raises(CommandError):
        await mgr.approve(cmd["id"])


async def test_greedy_sentence_blocked(hass):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(
        hass, entry, mgr, ai, {**GOOD_ANSWER, "sentences": ["[turn] (on|off) [the] [kitchen] [lights]", "lights"]}
    )
    assert any("greedy" in e or "already matches" in e for e in cmd["validation"]["errors"])


async def test_tampered_template_disables_command(hass):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai)
    await mgr.approve(cmd["id"])
    stored = entry.runtime_data.store.commands.get(cmd["id"])
    stored["response_template"] = "hacked"
    assert await _say(hass, "how warm is it outside") == "That command is switched off."
    assert entry.runtime_data.store.commands.get(cmd["id"])["status"] == "disabled"


async def test_revalidate_all_disables_broken(hass):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai)
    await mgr.approve(cmd["id"])
    hass.states.async_remove("sensor.outdoor_temperature")
    await mgr.revalidate_all()
    assert entry.runtime_data.store.commands.get(cmd["id"])["status"] == "disabled"


async def test_ai_task_not_configured(hass):
    entry = await _entry(hass)
    mgr = entry.runtime_data.commands
    cmd = mgr.create("x", "y", {"a": "sensor.a"})
    with pytest.raises(Exception, match="AI Task"):
        await mgr.generate(cmd["id"])


# ------------------------------------------------------------------ actions -----------------------------


async def _action_cmd(hass, entry, mgr, ai, result=None, entities=None):
    ai.results.append(result or GOOD_ACTION)
    cmd = mgr.create(
        "Lounge dim",
        "set the lounge lights to a percentage",
        entities or {"lounge": "light.lounge"},
        cmd_type="action",
    )
    return await mgr.generate(cmd["id"])


async def test_action_command_generated_approved_and_runs_locally(hass):
    entry, mgr, ai = await _setup(hass)
    calls = async_mock_service(hass, "light", "turn_on")
    cmd = await _action_cmd(hass, entry, mgr, ai)
    assert cmd["validation"]["ok"], cmd["validation"]
    assert cmd["validation"]["calls"] == [
        {"service": "light.turn_on", "targets": ["light.lounge"], "data": {"brightness_pct": "{level}"}}
    ]
    await mgr.approve(cmd["id"])
    ctx = Context()
    result = await conversation.async_converse(
        hass, "turn the lounge lights to 40 percent", None, ctx, agent_id=conversation.HOME_ASSISTANT_AGENT
    )
    assert result.response.speech["plain"]["speech"] == "Lounge lights set to 40 percent."
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == ["light.lounge"] and calls[0].data["brightness_pct"] == 40
    assert calls[0].context.id == ctx.id  # attributed to the voice request
    log = entry.runtime_data.store.executions.values()
    assert len(log) == 1 and log[0]["ok"] and log[0]["calls"][0]["service"] == "light.turn_on"


@pytest.mark.parametrize(
    "mutation, entities, fragment",
    [
        ({"actions": [{"service": "lock.unlock", "targets": ["lounge"], "data": {}}]}, None, "blocked"),
        ({"actions": [{"service": "light.turn_on", "targets": ["other"], "data": {}}]}, None, "not in the command's entity list"),
        ({"slots": {"level": {"type": "number", "min": 0, "max": 500}}}, None, "exceeds"),
        ({"sentences": ["lounge to {x}", "lounge {x} percent"], "slots": {}}, None, "undefined slot"),
        ({"actions": [{"service": "light.turn_on", "targets": ["lounge"], "data": {"brightness_pct": "{{ 5 }}"}}]}, None, "templates are not allowed"),
        ({"actions": [{"service": "light.turn_on", "targets": ["lounge"], "data": {"bogus": 1}}]}, None, "unknown field"),
        ({"actions": [{"service": "light.turn_on", "targets": ["lounge"], "data": {"brightness_pct": 300}}]}, None, "outside the allowed range"),
        ({"actions": [{"service": "homeassistant.restart", "targets": ["lounge"], "data": {}}]}, None, "blocked"),
        ({"actions": [{"service": "light.turn_on", "targets": ["lounge"], "data": {}}, {"service": "light.explode", "targets": ["lounge"], "data": {}}]}, None, "allow-list"),
        ({"actions": []}, None, "no actions"),
    ],
)
async def test_bad_actions_rejected(hass, mutation, entities, fragment):
    entry, mgr, ai = await _setup(hass)
    cmd = await _action_cmd(hass, entry, mgr, ai, {**GOOD_ACTION, **mutation}, entities)
    assert not cmd["validation"]["ok"]
    assert any(fragment in e for e in cmd["validation"]["errors"]), cmd["validation"]["errors"]
    with pytest.raises(CommandError):
        await mgr.approve(cmd["id"])


async def test_blocked_entities_cannot_even_be_selected_as_targets(hass):
    entry, mgr, ai = await _setup(hass)
    hass.states.async_set("lock.front_door", "locked")
    hass.states.async_set("cover.garage", "closed", {"device_class": "garage"})
    for ent, svc in (("lock.front_door", "lock.unlock"), ("cover.garage", "cover.open_cover")):
        domain = ent.split(".")[0]
        cmd = await _action_cmd(
            hass, entry, mgr, ai,
            {**GOOD_ACTION, "slots": {}, "sentences": ["open it up now", "do the thing now"],
             "actions": [{"service": svc, "targets": ["t"], "data": {}}]},
            {"t": ent},
        )
        assert not cmd["validation"]["ok"], domain


async def test_rate_limit_and_stop_on_first_failure(hass):
    entry, mgr, ai = await _setup(hass, rate_limit_per_minute=2)
    async_mock_service(hass, "light", "turn_on")
    two = {
        **GOOD_ACTION,
        "actions": [
            {"service": "light.turn_on", "targets": ["lounge"], "data": {"brightness_pct": "{level}"}},
            {"service": "light.turn_on", "targets": ["lounge"], "data": {}},
        ],
    }
    cmd = await _action_cmd(hass, entry, mgr, ai, two)
    await mgr.approve(cmd["id"])
    for _ in range(2):
        assert "Lounge lights" in await _say(hass, "turn the lounge lights to 10 percent")
    assert "too often" in await _say(hass, "turn the lounge lights to 10 percent")


async def test_stop_on_first_failure_says_so(hass):
    entry, mgr, ai = await _setup(hass)

    async def boom(call):
        raise RuntimeError("bulb offline")

    hass.services.async_register("light", "turn_on", boom)
    cmd = await _action_cmd(hass, entry, mgr, ai)
    await mgr.approve(cmd["id"])
    speech = await _say(hass, "turn the lounge lights to 40 percent")
    assert "failed" in speech and "bulb offline" in speech
    assert entry.runtime_data.store.executions.values()[0]["ok"] is False


async def test_slot_bounds_enforced_in_executor(hass):
    from custom_components.assist_learner.commands.actions import check_slot_values

    slots = GOOD_ACTION["slots"]
    assert check_slot_values(slots, {"level": "40"}) is None
    assert "between" in check_slot_values(slots, {"level": "150"})
    assert "number" in check_slot_values(slots, {"level": "abc"})
    assert "missing" in check_slot_values(slots, {})


async def test_run_once_and_editing_active_command_revalidates(hass):
    entry, mgr, ai = await _setup(hass)
    calls = async_mock_service(hass, "light", "turn_on")
    cmd = await _action_cmd(hass, entry, mgr, ai)
    await mgr.approve(cmd["id"])
    result = await mgr.run_once(cmd["id"], {"level": 25})
    assert result.ok and calls[0].data["brightness_pct"] == 25
    with pytest.raises(CommandError):  # an edit that breaks validation is refused for active commands
        await mgr.update(cmd["id"], {"response_template": "{{ states('sensor.nope') }}"})
    edited = await mgr.update(cmd["id"], {"response_template": "Dimmed to {{ slots.level | int }}."})
    assert edited["status"] == "active" and edited["hash"]
    assert await _say(hass, "set the lounge lights to 30 percent") == "Dimmed to 30."


async def test_script_targets_are_flagged(hass):
    entry, mgr, ai = await _setup(hass)
    await async_setup_component(hass, "script", {"script": {"movie": {"sequence": []}}})
    cmd = await _action_cmd(
        hass, entry, mgr, ai,
        {**GOOD_ACTION, "slots": {}, "sentences": ["start movie night", "put the movie on"],
         "response_template": "Started.", "actions": [{"service": "script.turn_on", "targets": ["s"], "data": {}}]},
        {"s": "script.movie"},
    )
    assert cmd["validation"]["ok"], cmd["validation"]
    assert any("full power" in f for f in cmd["validation"]["flags"])


async def test_collision_with_another_active_command(hass):
    entry, mgr, ai = await _setup(hass)
    first = await _answer_cmd(hass, entry, mgr, ai)
    await mgr.approve(first["id"])
    second = await _answer_cmd(hass, entry, mgr, ai)
    assert any("another Assist Learner item" in e for e in second["validation"]["errors"])
    assert second["validation"]["collisions"]
    # the user can knowingly accept an overlap
    accepted = await mgr.update(second["id"], {"accept_collisions": True})
    assert accepted["validation"]["ok"] and accepted["validation"]["warnings"]
