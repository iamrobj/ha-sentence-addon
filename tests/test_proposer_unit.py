"""Unit tests: alignment, templating, music learner."""

from __future__ import annotations

from custom_components.assist_learner.models import MissRecord
from custom_components.assist_learner.proposer.align import find_span
from custom_components.assist_learner.proposer.music import MusicLearner
from custom_components.assist_learner.proposer.templating import (
    fingerprint,
    generalise,
    has_verb_anchor,
    sample_text,
    wildcards_separated,
)

VERBS = ["play", "put on", "stick on", "queue", "start"]


def make_miss(text, media_id="Lose Control", artist="Teddy Swims", entity="media_player.living_room"):
    return MissRecord(
        id="m1",
        timestamp="2026-11-13T00:00:00+00:00",
        text=text,
        normalised=text.lower(),
        language="en",
        device_id=None,
        area_id=None,
        target_agent="conversation.claude_haiku",
        actions=[
            {
                "domain": "music_assistant",
                "service": "play_media",
                "data": {"media_id": media_id, "artist": artist, "entity_id": entity},
            }
        ],
    )


def test_fuzzy_span_handles_stt_errors():
    tokens = "put on lose control from teddy swim's in the living room".replace("'", "").split()
    span = find_span(tokens, "Teddy Swims")
    assert span is not None and tokens[span.start : span.end] == ["teddy", "swims"]


def test_span_missing_returns_none():
    assert find_span("turn on the lights".split(), "Teddy Swims") is None


def test_music_template_with_player():
    learner = MusicLearner(player_phrases=lambda e, d: ["living room speaker", "living room"])
    cand = learner.extract(make_miss("Put on Lose Control from Teddy Swims in the living room"))
    assert cand is not None
    assert cand.template == "put on {track} from {artist} in [the] {player}"


def test_music_template_without_player():
    learner = MusicLearner(player_phrases=lambda e, d: ["living room"])
    cand = learner.extract(make_miss("please play Lose Control by Teddy Swim's"))
    assert cand.template == "[please] play {track} by {artist}"
    assert "player" not in cand.template


def test_unalignable_when_track_absent():
    assert MusicLearner().extract(make_miss("play some music")) is None


def test_accepts_only_successful_ma_play():
    learner = MusicLearner()
    assert learner.accepts(make_miss("x"))
    bad = make_miss("x")
    bad.success = False
    assert not learner.accepts(bad)


def test_generalise_and_fingerprint():
    assert generalise("can you play {track} by {artist} please") == (
        "[can you|could you|would you|hey] play {track} by {artist} [please]"
    )
    assert fingerprint("A  b") == fingerprint("a b")


def test_anchor_and_boundary_rules():
    assert has_verb_anchor("put on {track} from {artist}", VERBS)
    assert not has_verb_anchor("{track} by {artist}", VERBS)
    assert not has_verb_anchor("play {track}", VERBS)  # plan: >= 2 fixed words
    assert wildcards_separated("play {track} by {artist}")
    assert not wildcards_separated("play {track} {artist}")


def test_sample_text():
    assert (
        sample_text("(play|put on) {track} [please] by {artist}", {"track": "a", "artist": "b"})
        == "play a by b"
    )
