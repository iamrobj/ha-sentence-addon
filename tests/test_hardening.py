"""Edge cases for actions, validation, proposer updates and import mapping."""

from __future__ import annotations

import pytest
from homeassistant.core import Context
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.assist_learner import learned
from custom_components.assist_learner.commands import actions as act
from custom_components.assist_learner.commands.importer import ExternalIntent, convert
from custom_components.assist_learner.proposer import async_run_proposer
from tests.test_commands import GOOD_ANSWER, _answer_cmd, _setup
from tests.test_proposer_run import _entry, _miss


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


# ------------------------------------------------------------------------------------------- actions ----
async def _validate(hass, actions, entities=None, slots=None):
    await async_setup_component(hass, "light", {})
    await async_setup_component(hass, "climate", {})
    await async_setup_component(hass, "cover", {})
    hass.states.async_set("light.l", "on")
    hass.states.async_set("climate.c", "heat", {"min_temp": 10, "max_temp": 25})
    hass.states.async_set("cover.g", "closed", {"device_class": "garage"})
    hass.states.async_set("cover.b", "closed", {"device_class": "blind"})
    hass.states.async_set("lock.d", "locked")
    ents = entities or {"l": "light.l", "c": "climate.c", "g": "cover.g", "b": "cover.b", "d": "lock.d", "ghost": "light.ghost"}
    return await act.async_validate_actions(hass, actions, ents, slots or {})


@pytest.mark.parametrize(
    "actions, fragment",
    [
        ([{"service": "nodot", "targets": ["l"]}], "domain.service"),
        ([{"service": "light.turn_on", "targets": ["c"]}], "not a light entity"),
        ([{"service": "light.turn_on", "targets": ["ghost"]}], "does not exist"),
        ([{"service": "light.turn_on", "targets": []}], "at least one target"),
        ([{"service": "light.turn_on", "targets": ["l"], "data": {"brightness_pct": [1]}}], "literal or a slot"),
        ([{"service": "light.turn_on", "targets": ["l"], "data": {"brightness_pct": "{nope}"}}], "unknown slot"),
        ([{"service": "cover.open_cover", "targets": ["g"]}], "garage"),
        ([{"service": "climate.set_temperature", "targets": ["c"], "data": {"temperature": 30}}], "outside"),
        ([{"service": "climate.set_temperature", "targets": ["c"], "data": {"temperature": "{t}"}}], "outside"),
    ],
)
async def test_action_validation_rejects(hass, actions, fragment):
    slots = {"t": {"type": "number", "min": 5, "max": 30, "step": 1}}
    rep = await _validate(hass, actions, slots=slots)
    assert not rep.ok and any(fragment in e for e in rep.errors), rep.errors


async def test_action_validation_accepts_cover_blind_and_climate_in_range(hass):
    rep = await _validate(
        hass,
        [
            {"service": "cover.open_cover", "targets": ["b"]},
            {"service": "climate.set_temperature", "targets": ["c"], "data": {"temperature": 21}},
        ],
    )
    assert rep.ok, rep.errors


async def test_executor_rechecks_blocks_and_bounds(hass):
    hass.states.async_set("cover.g", "closed", {"device_class": "garage"})
    cmd = {"id": "x", "name": "n", "entities": {"g": "cover.g"}, "slots": {},
           "actions": [{"service": "cover.open_cover", "targets": ["g"], "data": {}}]}
    res = await act.async_execute(hass, cmd, {}, Context())
    assert not res.ok and "garage" in res.error


def test_rate_limiter_window_and_choice_slots():
    rl = act.RateLimiter()
    assert rl.allow("a", 1) and not rl.allow("a", 1) and rl.allow("b", 1)
    slots = {"scene": {"type": "choice", "values": ["day", "night"]}}
    assert act.check_slot_values(slots, {"scene": "day"}) is None
    assert "one of" in act.check_slot_values(slots, {"scene": "party"})


async def test_execution_log_is_capped(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    cmd = {"id": "c1", "name": "n"}
    for _ in range(5):
        act.log_execution(store, cmd, None, act.ExecResult(True), limit=3)
    assert len(store.executions.values()) == 3


# ----------------------------------------------------------------------------------------- validation ----
@pytest.mark.parametrize(
    "mutation, fragment",
    [
        ({"slots": {"Bad Name": {"type": "number", "min": 0, "max": 1}}}, "lowercase"),
        ({"slots": {"x": {"type": "text"}}}, "free-text"),
        ({"slots": {"x": {"type": "number", "min": 5, "max": 1}}}, "min < max"),
        ({"slots": {"x": {"type": "number"}}}, "numeric min and max"),
        ({"slots": {"x": {"type": "choice", "values": []}}}, "non-empty"),
        ({"actions": [{"service": "light.turn_on", "targets": ["x"]}]}, "cannot have actions"),
        ({"type": "weird"}, "type must be"),
        ({"sentences": [f"sentence number {i} here" for i in range(30)]}, "at most"),
        ({"response_template": "{{ states('sensor.outdoor_temperature') | round(0) }}", "unavailable_template": ""}, "unavailable"),
        ({"unavailable_template": "{{ states('sensor.nope') }}"}, "unavailable_template"),
    ],
)
async def test_validation_edge_cases(hass, mutation, fragment):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai, {**GOOD_ANSWER, **mutation})
    assert any(fragment in e for e in cmd["validation"]["errors"]), cmd["validation"]["errors"]


async def test_missing_entity_and_choice_slot_command(hass):
    entry, mgr, ai = await _setup(hass)
    hass.states.async_set("input_boolean.movie", "off")
    async_mock_service(hass, "input_boolean", "turn_on")
    await async_setup_component(hass, "input_boolean", {"input_boolean": {"movie": {}}})
    result = {
        "type": "action",
        "sentences": ["switch scene to {mode} now", "activate the {mode} mode now"],
        "slots": {"mode": {"type": "choice", "values": ["movie", "reading"]}},
        "response_template": "Switched to {{ slots.mode }}.",
        "actions": [{"service": "input_boolean.turn_on", "targets": ["m"], "data": {}}],
    }
    ai.results.append(result)
    cmd = mgr.create("Mode", "switch modes", {"m": "input_boolean.movie", "ghost": "sensor.ghost"}, cmd_type="action")
    cmd = await mgr.generate(cmd["id"])
    assert any("does not exist" in e for e in cmd["validation"]["errors"])
    cmd = await mgr.update(cmd["id"], {"entities": {"m": "input_boolean.movie"}})
    assert cmd["validation"]["ok"], cmd["validation"]
    await mgr.approve(cmd["id"])
    from tests.test_commands import _say

    assert await _say(hass, "switch scene to movie now") == "Switched to movie."


async def test_manager_edge_paths(hass):
    from custom_components.assist_learner.commands.manager import CommandError

    entry, mgr, ai = await _setup(hass)
    with pytest.raises(CommandError):
        mgr.create(" ", "x", {})
    with pytest.raises(CommandError):
        await mgr.approve("nope")
    cmd = await _answer_cmd(hass, entry, mgr, ai)
    with pytest.raises(CommandError, match="No such version"):
        await mgr.revert(cmd["id"], 99)
    with pytest.raises(CommandError, match="nothing to run"):
        await mgr.run_once(cmd["id"])
    rejected = await mgr.reject(cmd["id"], "nope")
    assert rejected["status"] == "draft"
    # AI task failure is surfaced and logged
    async def boom(call):
        raise RuntimeError("down")

    hass.services.async_remove("ai_task", "generate_data")
    from homeassistant.core import SupportsResponse

    hass.services.async_register("ai_task", "generate_data", boom, supports_response=SupportsResponse.ONLY)
    with pytest.raises(Exception, match="AI Task call failed"):
        await mgr.generate(cmd["id"])
    assert entry.runtime_data.store.generation_log.values()[-1]["outcome"].startswith("AI Task call failed")
    ai.results.append({"nonsense": True})  # unusable output still normalises, then fails validation
    hass.services.async_remove("ai_task", "generate_data")
    from tests.test_commands import FakeAI

    ai2 = FakeAI(hass)
    ai2.results.append({"nonsense": True})
    out = await mgr.generate(cmd["id"])
    assert not out["validation"]["ok"]


# -------------------------------------------------------------------------------------------- proposer ----
async def test_update_proposal_when_only_optionals_differ(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    row = await learned.async_approve(hass, entry, p.id)
    store.misses.put("m1", _miss(1, "please put on Lose Control from Teddy Swims").as_dict())
    upd = (await async_run_proposer(hass, entry))[0]
    assert upd.type == "update" and upd.replaces == row["id"] and "[please]" in upd.diff
    done = await learned.async_approve(hass, entry, upd.id)
    assert done["id"] == row["id"] and done["template"].startswith("[please]") and len(done["history"]) == 1


async def test_more_evidence_added_to_pending_proposal(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    store.misses.put("m1", _miss(1, "put on lose control from teddy swim's").as_dict())
    assert await async_run_proposer(hass, entry) == []
    row = store.proposals.get(p.id)
    assert row["count"] == 2 and len(row["examples"]) == 2


async def test_retention_keeps_processed_until_old_enough(hass):
    entry = await _entry(hass, retention_days=30)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    store.misses.put("m9", _miss(9, "old one", ts="2000-01-01T00:00:00+00:00").as_dict() | {"processed": True})
    await async_run_proposer(hass, entry)
    ids = {m["id"] for m in store.misses.values()}
    assert "m0" in ids and "m9" not in ids  # recent processed kept; ancient processed pruned


async def test_non_music_and_failed_misses_are_ignored(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    m = _miss(0, "turn on the light").as_dict()
    m["actions"] = [{"domain": "light", "service": "turn_on", "data": {}}]
    store.misses.put("m0", m)
    assert await async_run_proposer(hass, entry) == []
    assert store.misses.values() == []


async def test_media_player_play_media_on_ma_player_accepted(hass):
    from homeassistant.helpers import entity_registry as er

    entry = await _entry(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("media_player", "music_assistant", "u1", suggested_object_id="lr")
    m = _miss(0, "put on Lose Control from Teddy Swims").as_dict()
    m["actions"][0]["domain"] = "media_player"
    m["actions"][0]["data"]["entity_id"] = "media_player.lr"
    entry.runtime_data.store.misses.put("m0", m)
    assert len(await async_run_proposer(hass, entry)) == 1


async def test_notify_service_and_none(hass):
    entry = await _entry(hass, notify="notify.mobile_app_x")
    sent = async_mock_service(hass, "notify", "mobile_app_x")
    entry.runtime_data.store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    await async_run_proposer(hass, entry)
    assert sent and "1 new proposed" in sent[0].data["message"]


# ---------------------------------------------------------------------------------------------- import ----
@pytest.mark.parametrize(
    "script, lists, fragment",
    [
        ({"action": [{"action": "{{ x }}", "target": {"entity_id": "light.a"}}]}, {}, "templated"),
        ({"action": [{"action": "light.turn_on"}]}, {}, "targets are missing"),
        ({"action": [{"action": "light.turn_on", "target": {"area_id": "x"}}]}, {}, "targets are missing"),
        ({"action": [{"action": "light.turn_on", "target": {"entity_id": "light.a", "area_id": "k"}}]}, {}, "area/device"),
        ({"action": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}, "data": {"b": "{{ 1 + 2 }}"}}]}, {}, "plain slot"),
        ({"action": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}, "data": {"b": [1]}}]}, {}, "not a literal"),
        ({"action": [{"action": "light.turn_on", "target": {"entity_id": "light.a"}, "weird": 1}]}, {}, "unsupported keys"),
        ({"action": ["nope"]}, {}, "not a service call"),
        ({"action": [{"delay": "00:00:01"}]}, {}, "delay"),
        ({}, {}, "neither actions nor speech"),
    ],
)
def test_import_unmappable_reasons(script, lists, fragment):
    cand = convert(ExternalIntent("I", "/x/f.yaml", ["a b c", "d e f"], lists=lists), script)
    assert cand.command is None and fragment in cand.unmappable, cand.unmappable


def test_import_slot_list_mapping():
    lists = {"mode": {"values": ["day", {"in": "night", "out": "night"}]}, "bad": {"values": [{"in": "a", "out": "b"}]}, "odd": {"foo": 1}}
    s = ExternalIntent("I", "/x/f.yaml", ["go {mode}", "set {mode} now"], lists=lists)
    cand = convert(s, {"speech": {"text": "Mode {{ mode }}"}})
    assert cand.command["slots"]["mode"]["values"] == ["day", "night"]
    assert cand.command["response_template"] == "Mode {{ slots.mode }}"
    for name, frag in (("bad", "different outputs"), ("odd", "unsupported list"), ("missing", "no list definition")):
        c = convert(ExternalIntent("I", "/f", [f"x {{{name}}}", "y z"], lists=lists), {"speech": {"text": "t"}})
        assert frag in c.unmappable


async def test_slots_must_be_object(hass):
    entry, mgr, ai = await _setup(hass)
    cmd = await _answer_cmd(hass, entry, mgr, ai)
    cmd = await mgr.update(cmd["id"], {"slots": ["nope"]})
    assert any("must be an object" in e for e in cmd["validation"]["errors"])
