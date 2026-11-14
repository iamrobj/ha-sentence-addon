"""Nightly proposer end-to-end against the store."""

from __future__ import annotations

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.assist_learner.const import CONF_LANGUAGE, CONF_TARGET_AGENT, DOMAIN
from custom_components.assist_learner.models import MissRecord
from custom_components.assist_learner.proposer import async_run_proposer, core_of


async def _entry(hass, **options):
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={CONF_TARGET_AGENT: "conversation.x", CONF_LANGUAGE: "en"},
        options=options,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _miss(i, text, ts="2099-01-01T00:00:00+00:00", **kw):
    return MissRecord(
        id=f"m{i}",
        timestamp=ts,
        text=text,
        normalised=text.lower(),
        language="en",
        device_id=None,
        area_id=None,
        target_agent="x",
        actions=[
            {
                "domain": "music_assistant",
                "service": "play_media",
                "data": {"media_id": "Lose Control", "artist": "Teddy Swims", "entity_id": "media_player.lr"},
            }
        ],
        **kw,
    )


async def test_creates_proposal_and_deletes_raw_text(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    for i, t in enumerate(
        ["put on Lose Control from Teddy Swims", "put on lose control from teddy swim's"]
    ):
        store.misses.put(f"m{i}", _miss(i, t).as_dict())
    created = await async_run_proposer(hass, entry)
    assert len(created) == 1
    p = created[0]
    assert p.template == "put on {track} from {artist}"
    assert p.count == 2 and p.status == "pending" and len(p.examples) == 2
    assert store.misses.values() == []  # retention 0: raw text gone
    assert store.candidates.get(p.fingerprint)["count"] == 2
    assert "text" not in str(store.candidates.get(p.fingerprint))


async def test_min_occurrences_holds_back_and_counts_persist(hass):
    entry = await _entry(hass, min_occurrences=2)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    assert await async_run_proposer(hass, entry) == []
    store.misses.put("m1", _miss(1, "put on Lose Control from Teddy Swims").as_dict())
    created = await async_run_proposer(hass, entry)
    assert len(created) == 1 and created[0].count == 2


async def test_nightly_cap_defers_rest(hass):
    entry = await _entry(hass, max_proposals_per_night=1)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    store.misses.put("m1", _miss(1, "stick on Lose Control by Teddy Swims").as_dict())
    first = await async_run_proposer(hass, entry)
    assert len(first) == 1
    second = await async_run_proposer(hass, entry)
    assert len(second) == 1 and second[0].fingerprint != first[0].fingerprint


async def test_rejected_fingerprint_never_returns(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    p = (await async_run_proposer(hass, entry))[0]
    store.rejected.put(p.fingerprint, {"at": "now"})
    store.proposals.delete(p.id)
    store.misses.put("m1", _miss(1, "put on Lose Control from Teddy Swims").as_dict())
    assert await async_run_proposer(hass, entry) == []


async def test_greedy_template_blocked(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    # "turn on {track}" is a verb-anchored template that would steal "turn on the kitchen lights"
    m = _miss(0, "turn on Lose Control")
    store.misses.put("a", m.as_dict())
    assert await async_run_proposer(hass, entry) == []


async def test_unalignable_flagged(hass):
    entry = await _entry(hass, retention_days=30)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "play some tunes").as_dict())
    assert await async_run_proposer(hass, entry) == []
    assert store.misses.get("m0")["unalignable"] is True


def test_core_of_detects_update():
    assert core_of("(play|put on) {track} [please] by {artist}") == "play {track} by {artist}"
    assert core_of("play {track} by {artist}") == core_of("[please] play {track} by {artist}")


async def test_summary_explains_zero_proposals(hass, tmp_path):
    """A failed request yields 0 proposals and is deleted (retention 0); the summary says why."""
    hass.config.config_dir = str(tmp_path)
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims", success=False).as_dict())
    assert await async_run_proposer(hass, entry) == []
    s = entry.runtime_data.last_run
    assert s["considered"] == 1 and s["failed"] == 1 and s["created"] == 0
    assert store.misses.values() == []  # retention 0: processed raw text is gone
