"""Safety checks for proposed templates (any failure drops or flags the proposal)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .. import recognizer
from ..sentences import MUSIC_INTENT
from .templating import has_verb_anchor, sample_text, wildcards_separated

PROBES: list[dict[str, str]] = [
    {"track": "the kitchen lights", "artist": "the ceiling", "player": "the bedroom"},
    {"track": "a timer", "artist": "five minutes", "player": "the oven"},
]


class CheckResult:
    def __init__(self) -> None:
        self.drop_reason: str | None = None  # template is useless: discard silently
        self.block_reason: str | None = None  # template is dangerous: discard and surface
        self.warnings: list[str] = []

    @property
    def ok(self) -> bool:
        return self.drop_reason is None and self.block_reason is None


async def async_check_template(
    hass: HomeAssistant,
    template: str,
    samples: dict[str, str],
    language: str,
    verbs: list[str],
    *,
    allow_learned: bool = False,
) -> CheckResult:
    res = CheckResult()

    if not has_verb_anchor(template, verbs):
        res.block_reason = "needs at least two fixed words including a carrier verb"
        return res
    if not wildcards_separated(template):
        res.block_reason = "two wildcards are not separated by a literal word"
        return res

    # Already local? (built-ins, blueprint, earlier learned templates)
    text = sample_text(template, samples)
    if (m := await recognizer.async_match(hass, text, language)) is not None and not (
        allow_learned and m.intent_name == MUSIC_INTENT
    ):
        res.drop_reason = f"already handled locally by {m.intent_name}"
        return res

    # Too greedy? Probe with non-music values; if it already matches something else, it would steal it.
    for probe in PROBES:
        probe_text = sample_text(template, probe)
        if (m := await recognizer.async_match(hass, probe_text, language)) is not None and not (
            allow_learned and m.intent_name == MUSIC_INTENT
        ):
            res.block_reason = f"too greedy: '{probe_text}' already matches {m.intent_name}"
            return res

    if " by " in f" {template} " and "{track}" in template and "{artist}" in template:
        res.warnings.append("titles containing 'by' (e.g. 'Stand by Me') may be ambiguous")
    return res
