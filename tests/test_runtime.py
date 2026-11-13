"""Phase 3: approval writes YAML, reload makes the template match locally, handler plays + counts."""

from __future__ import annotations

import pytest
import yaml
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.assist_learner import learned
from custom_components.assist_learner.players import list_players, resolve_player
from custom_components.assist_learner.proposer import async_run_proposer
from custom_components.assist_learner.sentences import build_document, sentences_path
from tests.test_proposer_run import _entry, _miss


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


def _player(hass, entry_id_suffix, name, *, area=None, device=None):
    reg = er.async_get(hass)
    ent = reg.async_get_or_create(
        "media_player",
        "music_assistant",
        f"ma_{entry_id_suffix}",
        suggested_object_id=entry_id_suffix,
        original_name=name,
        device_id=device,
    )
    if area:
        reg.async_update_entity(ent.entity_id, area_id=area)
    async_expose_entity(hass, "conversation", ent.entity_id, True)
    hass.states.async_set(ent.entity_id, "idle", {"friendly_name": name})
    return ent.entity_id


def test_build_document_shape():
    doc = build_document(
        "en",
        [
            {"id": "lt_1", "template": "put on {track} from {artist}", "enabled": True},
            {"id": "lt_2", "template": "play {track}", "enabled": False},
        ],
        [],
        [{"in": "kitchen", "out": "media_player.kitchen"}],
    )
    blocks = doc["intents"]["AssistLearnerPlayMedia"]["data"]
    assert len(blocks) == 1 and blocks[0]["slots"]["template_id"] == "lt_1"
    assert doc["lists"]["track"] == {"wildcard": True}
    assert doc["lists"]["player"]["values"][0]["out"] == "media_player.kitchen"


async def test_approved_template_handles_locally_and_counts(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    kitchen = _player(hass, "kitchen", "Kitchen speaker")
    played: list[ServiceCall] = []

    async def play(call):
        played.append(call)

    hass.services.async_register("music_assistant", "play_media", play)

    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    row = await learned.async_approve(hass, entry, p.id)

    doc = yaml.safe_load(sentences_path(hass, "en").read_text())
    assert doc["intents"]["AssistLearnerPlayMedia"]["data"][0]["sentences"] == [
        "put on {track} from {artist}"
    ]

    result = await conversation.async_converse(
        hass,
        "put on Despacito from Luis Fonsi",
        None,
        Context(),
        agent_id=conversation.HOME_ASSISTANT_AGENT,
    )
    # no device and no default player -> asks which speaker (proves local handler ran, not the LLM)
    assert "which speaker" in result.response.speech["plain"]["speech"].lower()
    assert played == []

    # with a spoken player
    store.learned.put(row["id"], {**row, "template": "put on {track} from {artist} in [the] {player}"})
    await learned.async_set_enabled(hass, entry, row["id"], True)
    result = await conversation.async_converse(
        hass,
        "put on Despacito from Luis Fonsi in the kitchen speaker",
        None,
        Context(),
        agent_id=conversation.HOME_ASSISTANT_AGENT,
    )
    assert len(played) == 1
    assert played[0].data["entity_id"] == kitchen
    assert played[0].data["media_id"].lower() == "despacito"
    assert played[0].data["artist"].lower() == "luis fonsi"
    assert "Playing" in result.response.speech["plain"]["speech"]
    assert store.learned.get(row["id"])["hits"] == 1
    assert store.today("local_hits") == 1


async def test_disable_removes_sentence(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    row = await learned.async_approve(hass, entry, p.id)
    await learned.async_set_enabled(hass, entry, row["id"], False)
    doc = yaml.safe_load(sentences_path(hass, "en").read_text())
    assert "intents" not in doc
    await learned.async_delete(hass, entry, row["id"])
    assert store.learned.values() == []


async def test_reject_records_fingerprint_and_clears_examples(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    await learned.async_reject(hass, entry, p.id, "nope")
    assert p.fingerprint in store.rejected.items
    assert store.proposals.get(p.id)["examples"] == []
    assert store.proposals.get(p.id)["status"] == "rejected"


async def test_edit_then_approve_revalidates(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    with pytest.raises(learned.ProposalError):
        await learned.async_approve(hass, entry, p.id, template="{track} by {artist}")
    row = await learned.async_approve(hass, entry, p.id, template="(play|put on) {track} from {artist}")
    assert row["template"] == "(play|put on) {track} from {artist}"


async def test_player_resolution_order(hass):
    await _entry(hass)
    dev_reg = dr.async_get(hass)
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    from homeassistant.helpers import area_registry as ar

    area = ar.async_get(hass).async_create("Living Room")
    voice = dev_reg.async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("t", "voice")})
    dev_reg.async_update_device(voice.id, area_id=area.id)
    plain_dev = dev_reg.async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("t", "voice2")})
    dev_reg.async_update_device(plain_dev.id, area_id=area.id)

    a = _player(hass, "lr_a", "Sonos", area=area.id)
    kw = dict(default_player="media_player.default", preferred_by_area={}, prefer_own=True)

    # 1 spoken wins
    assert resolve_player(hass, spoken="media_player.x", device_id=voice.id, **kw).entity_id == "media_player.x"
    # 3 same-area single player
    assert resolve_player(hass, spoken=None, device_id=voice.id, **kw).entity_id == a
    # 3b two players -> preferred, else ask
    b = _player(hass, "lr_b", "Bose", area=area.id)
    assert "which speaker" in resolve_player(hass, spoken=None, device_id=voice.id, **kw).error.lower()
    pref = {**kw, "preferred_by_area": {area.id: b}}
    assert resolve_player(hass, spoken=None, device_id=voice.id, **pref).entity_id == b
    # 2 own player (player attached to the speaking device)
    own = _player(hass, "own", "Voice PE", area=area.id, device=voice.id)
    assert resolve_player(hass, spoken=None, device_id=voice.id, **kw).entity_id == own
    assert resolve_player(hass, spoken=None, device_id=voice.id, **{**kw, "prefer_own": False}).error
    # 4 no device -> default, else ask
    assert resolve_player(hass, spoken=None, device_id=None, **kw).entity_id == "media_player.default"
    assert resolve_player(hass, spoken=None, device_id=None, **{**kw, "default_player": None}).error
    assert {p.entity_id for p in list_players(hass)} >= {a, b, own}
