"""Diagnostics redaction, store migrations, replay harness, config-entry migration."""

from __future__ import annotations

import json

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.assist_learner import async_migrate_entry, learned
from custom_components.assist_learner.const import DOMAIN
from custom_components.assist_learner.diagnostics import async_get_config_entry_diagnostics
from custom_components.assist_learner.proposer import async_run_proposer
from custom_components.assist_learner.replay import main as replay_main
from custom_components.assist_learner.replay import replay
from custom_components.assist_learner.store import migrate_data
from tests.test_commands import _answer_cmd, _setup
from tests.test_proposer_run import _entry, _miss


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


async def test_diagnostics_redacts_raw_text_and_prompts(hass):
    entry, mgr, ai = await _setup(hass, retention_days=30)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    await _answer_cmd(hass, entry, mgr, ai)
    await async_run_proposer(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    blob = json.dumps(diag, default=str)
    assert "Lose Control" not in blob and "lose control" not in blob.lower()
    assert "how warm outside" not in blob and "answer with the outdoor temperature" not in blob  # descriptions redacted
    assert "**REDACTED**" in blob and diag["counts"]["proposals"] == 1


def test_store_migration_hook():
    assert migrate_data(1, {"items": {}}) == {"items": {}}
    with pytest.raises(ValueError):
        migrate_data(99, {})


async def test_config_entry_migration(hass):
    ok = MockConfigEntry(domain=DOMAIN, version=1)
    future = MockConfigEntry(domain=DOMAIN, version=2)
    assert await async_migrate_entry(hass, ok) is True
    assert await async_migrate_entry(hass, future) is False


def _rec(text, **kw):
    return {
        "text": text,
        "actions": [{"domain": "music_assistant", "service": "play_media",
                     "data": {"media_id": kw.get("track", "Lose Control"), "artist": "Teddy Swims", "entity_id": "media_player.lr"}}],
    }


def test_replay_groups_counts_and_reports():
    misses = [
        _rec("put on Lose Control from Teddy Swims"),
        _rec("put on lose control from teddy swim's"),
        _rec("play Lose Control by Teddy Swims in the living room"),
        _rec("nonsense that matches nothing"),
        {"text": "turn on light", "actions": [{"domain": "light", "service": "turn_on", "data": {}}]},
        _rec("lose control teddy swims"),  # no verb anchor
    ]
    out = replay(misses, min_occurrences=2, players={"media_player.lr": ["living room"]})
    assert out["proposals"][0]["template"] == "put on {track} from {artist}" and out["proposals"][0]["count"] == 2
    assert any(b["template"].startswith("play {track}") for b in out["below_threshold"])
    assert out["unalignable"] == ["nonsense that matches nothing"]
    assert out["skipped_non_music"] == 1 and out["rejected"]


def test_replay_cli(tmp_path, capsys):
    f = tmp_path / "m.json"
    f.write_text(json.dumps([_rec("put on Lose Control from Teddy Swims")]))
    assert replay_main([str(f)]) == 0
    assert "put on {track} from {artist}" in capsys.readouterr().out


async def test_learned_misc_paths(hass):
    entry = await _entry(hass)
    with pytest.raises(learned.ProposalError):
        await learned.async_set_enabled(hass, entry, "nope", True)
    with pytest.raises(learned.ProposalError):
        await learned.async_delete(hass, entry, "nope")
    with pytest.raises(learned.ProposalError):
        await learned.async_approve(hass, entry, "nope")
    learned.record_hit(entry, "nope")  # unknown id is a no-op


async def test_scriptable_command_services_and_bulk_and_suggestion(hass, hass_ws_client):
    from tests.test_commands import GOOD_ANSWER

    entry, mgr, ai = await _setup(hass, llm_generalise=True)
    ai.results += [GOOD_ANSWER]
    r = await hass.services.async_call(
        DOMAIN, "create_command",
        {"name": "Outside", "description": "answer outside temp", "entities": {"t": "sensor.outdoor_temperature"}},
        blocking=True, return_response=True,
    )
    cid = r["id"]
    g = await hass.services.async_call(DOMAIN, "generate_command", {"command_id": cid}, blocking=True, return_response=True)
    assert g["validation"]["ok"]
    d = await hass.services.async_call(DOMAIN, "decide_command", {"command_id": cid, "decision": "approve"}, blocking=True, return_response=True)
    assert d["status"] == "active"

    # LLM generalisation is only a suggestion and must pass all checks
    ai.results += [{"template": "(put on|play) {track} from {artist}"}]
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    assert p.suggestion == "(put on|play) {track} from {artist}" and p.template == "put on {track} from {artist}"
    assert not store.learned.values()  # never applied without approval

    client = await hass_ws_client(hass)
    store.misses.put("m1", _miss(1, "stick on Lose Control by Teddy Swims").as_dict())
    ai.results += [{"template": "not a valid {thing}"}]
    await async_run_proposer(hass, entry)
    pending = [x["id"] for x in store.proposals.values() if x["status"] == "pending"]
    assert len(pending) == 2
    assert next(x for x in store.proposals.values() if x["template"].startswith("stick"))["suggestion"] is None
    await client.send_json_auto_id({"type": "assist_learner/proposals/bulk", "proposal_ids": pending + ["bogus"], "decision": "approve"})
    res = (await client.receive_json())["result"]
    assert len(res["done"]) == 2 and res["failed"][0]["id"] == "bogus"
    await client.send_json_auto_id({"type": "assist_learner/misses/export"})
    assert isinstance((await client.receive_json())["result"], list)
