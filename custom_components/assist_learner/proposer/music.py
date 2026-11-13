"""Music playback learner: 'play X by Y on Z' via Music Assistant."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..capture import normalise
from ..models import MissRecord
from .align import Span, find_span, templatise
from .base import Candidate
from .templating import generalise

FAMILY = "music_play"
INTENT_NAME = "AssistLearnerPlayMedia"

PlayerPhrases = Callable[[str, str | None], list[str]]
"""(player_entity_id, miss.device_id) -> phrases the user might say for this player."""


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value not in (None, "") else None


class MusicLearner:
    family = FAMILY

    def __init__(
        self,
        player_phrases: PlayerPhrases | None = None,
        is_ma_player: Callable[[str], bool] | None = None,
    ) -> None:
        self._player_phrases = player_phrases or (lambda _e, _d: [])
        self._is_ma_player = is_ma_player or (lambda _e: False)

    # -- Learner protocol ---------------------------------------------------
    def _play_action(self, miss: MissRecord) -> dict[str, Any] | None:
        for a in miss.actions:
            if a.get("domain") == "music_assistant" and a.get("service") == "play_media":
                return a
            if a.get("domain") == "media_player" and a.get("service") == "play_media":
                ent = _first((a.get("data") or {}).get("entity_id"))
                if ent and self._is_ma_player(ent):
                    return a
        return None

    def accepts(self, miss: MissRecord) -> bool:
        return miss.success and self._play_action(miss) is not None

    def extract(self, miss: MissRecord) -> Candidate | None:
        action = self._play_action(miss)
        if action is None:
            return None
        data = action.get("data") or {}
        track = _first(data.get("media_id") or data.get("track"))
        artist = _first(data.get("artist"))
        media_type = _first(data.get("media_type")) or "track"
        if not track or "://" in track or media_type not in ("track", "album", "playlist", "artist"):
            return None

        tokens = normalise(miss.text).split()
        taken: set[int] = set()
        spans: dict[str, Span] = {}
        samples: dict[str, str] = {}
        ratios: list[float] = []

        # longest value first so short values don't steal tokens from long titles
        wanted = [("track", track)] + ([("artist", artist)] if artist else [])
        for name, value in sorted(wanted, key=lambda kv: -len(kv[1])):
            span = find_span(tokens, value, taken)
            if span is None:
                return None  # unalignable
            spans[name] = span
            taken.update(range(span.start, span.end))
            ratios.append(span.ratio)
            samples[name] = " ".join(tokens[span.start : span.end])

        entity = _first(data.get("entity_id"))
        if entity:
            for phrase in sorted(self._player_phrases(entity, miss.device_id), key=len, reverse=True):
                span = find_span(tokens, phrase, taken)
                if span is not None and span.ratio >= 0.9:
                    spans["player"] = span
                    taken.update(range(span.start, span.end))
                    samples["player"] = " ".join(tokens[span.start : span.end])
                    break

        template = generalise(templatise(tokens, spans))
        return Candidate(
            family=FAMILY,
            template=template,
            slots={"media_type": media_type, "samples": samples},
            confidence=round(min(ratios), 3),
            example=miss.text,
        )
