"""Phase 8: discover and import external sentence files; originals never modified."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from homeassistant.setup import async_setup_component

from custom_components.assist_learner.commands.manager import CommandError
from tests.test_commands import _setup

SENTENCES = {
    "language": "en",
    "intents": {
        "LoungeLevel": {"data": [{"sentences": ["lounge lights to {level} percent", "dim the lounge to {level} percent"]}]},
        "LoungeStatus": {"data": [{"sentences": ["how bright is the lounge", "lounge brightness please"]}]},
        "WildTalk": {"data": [{"sentences": ["tell {thing} now", "announce {thing}"]}]},
        "Complex": {"data": [{"sentences": ["do the complex thing", "complex time"]}]},
        "Unlock": {"data": [{"sentences": ["open sesame please", "sesame open"]}]},
        "NoScript": {"data": [{"sentences": ["nothing here at all", "empty intent here"]}]},
    },
    "lists": {"level": {"range": {"from": 0, "to": 100}}, "thing": {"wildcard": True}},
}
CONFIG = {
    "intent_script": {
        "LoungeLevel": {
            "action": [{"action": "light.turn_on", "target": {"entity_id": "light.lounge"}, "data": {"brightness_pct": "{{ level }}"}}],
            "speech": {"text": "Lounge at {{ level }} percent"},
        },
        "LoungeStatus": {
            "speech": {"text": "Lounge is at {{ state_attr('light.lounge', 'brightness') | int }}"},
        },
        "WildTalk": {"speech": {"text": "{{ thing }}"}},
        "Complex": {"action": [{"choose": [{"conditions": [], "sequence": []}]}]},
        "Unlock": {"action": [{"action": "lock.unlock", "target": {"entity_id": "lock.front_door"}}]},
    }
}
AUTOMATIONS = [
    {"id": "a1", "alias": "Goodnight", "triggers": [{"trigger": "conversation", "command": ["good night"]}], "actions": []}
]


@pytest.fixture
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    d = tmp_path / "custom_sentences" / "en"
    d.mkdir(parents=True)
    (d / "mine.yaml").write_text(yaml.safe_dump(SENTENCES))
    (tmp_path / "configuration.yaml").write_text(yaml.safe_dump(CONFIG))
    (tmp_path / "automations.yaml").write_text(yaml.safe_dump(AUTOMATIONS))
    return tmp_path


def _digest(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha1(p.read_bytes()).hexdigest() for p in root.rglob("*.yaml")}


async def test_discover_lists_files_and_read_only_automations(hass, config_dir):
    entry, mgr, _ = await _setup(hass)
    found = await mgr.import_discover()
    assert {f["intent"] for f in found["files"]} >= {"LoungeLevel", "Complex"}
    assert found["automations"][0]["sentences"] == ["good night"]
    assert found["automations"][0]["importable"] is False


async def test_preview_maps_simple_and_explains_unmappable(hass, config_dir):
    entry, mgr, _ = await _setup(hass)
    hass.states.async_set("lock.front_door", "locked")
    rows = {r["intent"]: r for r in await mgr.import_preview()}

    lvl = rows["LoungeLevel"]
    # the only complaint is the expected overlap with the still-active original file
    assert lvl["importable"] and lvl["validation"]["collisions"]
    assert [e for e in lvl["validation"]["errors"] if "already matches" not in e] == []
    cmd = lvl["command"]
    assert cmd["type"] == "answer_and_action"
    assert cmd["slots"]["level"] == {"type": "number", "min": 0, "max": 100, "step": 1}
    assert cmd["actions"] == [{"service": "light.turn_on", "targets": ["lounge"], "data": {"brightness_pct": "{level}"}}]
    assert cmd["response_template"] == "Lounge at {{ slots.level }} percent"

    status = rows["LoungeStatus"]["command"]
    assert status["type"] == "answer" and status["entities"] == {"lounge": "light.lounge"}

    assert "wildcard" in rows["WildTalk"]["unmappable"]
    assert "choose" in rows["Complex"]["unmappable"]
    assert "no matching intent_script" in rows["NoScript"]["unmappable"]
    assert not rows["Complex"]["importable"]
    # blocked service maps structurally but fails the same validation as anything authored
    assert rows["Unlock"]["importable"] and not rows["Unlock"]["validation"]["ok"]
    assert any("blocked" in e for e in rows["Unlock"]["validation"]["errors"])


async def test_apply_creates_pending_and_never_touches_originals(hass, config_dir):
    entry, mgr, _ = await _setup(hass)
    before = _digest(config_dir)
    created = await mgr.import_apply(["LoungeLevel"])
    assert len(created) == 1 and created[0]["status"] == "pending"
    assert created[0]["imported_from"]["intent"] == "LoungeLevel"
    assert _digest(config_dir) == before
    with pytest.raises(CommandError, match="cannot be imported"):
        await mgr.import_apply(["Complex"])


async def test_collision_flagged_while_original_active(hass, config_dir):
    entry, mgr, _ = await _setup(hass)
    # the user's file is loaded by Assist, so the managed copy collides until the original is removed
    await async_setup_component(hass, "intent_script", CONFIG)
    await hass.services.async_call("conversation", "reload", {}, blocking=True)
    rows = {r["intent"]: r for r in await mgr.import_preview()}
    lvl = rows["LoungeLevel"]
    assert lvl["validation"]["collisions"]
    assert not lvl["validation"]["ok"]
    assert "LoungeLevel" in lvl["collision_note"]
