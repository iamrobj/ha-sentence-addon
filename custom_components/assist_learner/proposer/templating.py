"""Template generalisation, fingerprinting and sampling (deterministic, no LLM)."""

from __future__ import annotations

import hashlib
import re

from hassil.parse_expression import parse_sentence
from hassil.sample import sample_sentence

POLITE_LEAD = ("can you", "could you", "would you", "will you", "hey")
_SLOT_RE = re.compile(r"\{(\w+)\}")


def generalise(template: str) -> str:
    """Make politeness fillers optional and fold determiners before {player} into optionals."""
    t = f" {template} "
    lead = "|".join(POLITE_LEAD)
    t = re.sub(rf"^ (?:{lead}) ", " [can you|could you|would you|hey] ", t)
    t = re.sub(r" please ", " [please] ", t)
    t = re.sub(r" (the|my) (\{player\})", r" [\1] \2", t)
    return " ".join(t.split())


def fingerprint(template: str) -> str:
    return hashlib.sha1(" ".join(template.lower().split()).encode()).hexdigest()[:16]


def sample_text(template: str, values: dict[str, str]) -> str:
    """Minimal expansion of a template: optionals dropped, first alternative, slots filled."""
    filled = _SLOT_RE.sub(lambda m: values.get(m.group(1), m.group(1)), template)
    sentence = parse_sentence(filled)
    for text in sample_sentence(sentence, skip_optionals=True):
        return text
    return filled


def literal_words(template: str) -> list[str]:
    """Fixed words that are always present: slots and optional groups removed, first alternative."""
    text = sample_text(template, {k: "" for k in _SLOT_RE.findall(template)})
    return text.split()


def has_verb_anchor(template: str, verbs: list[str]) -> bool:
    """>=2 literal words, at least one a carrier verb (multi-word verbs like 'put on' supported)."""
    words = literal_words(template)
    if len(words) < 2:
        return False
    joined = " " + " ".join(words) + " "
    return any(f" {v} " in joined for v in verbs)


def wildcards_separated(template: str) -> bool:
    """Two slots must be separated by at least one literal word."""
    stripped = re.sub(r"\[[^\]]*\]", "", template)  # optionals don't count as separators
    return not re.search(r"\}\s*\{", stripped)
