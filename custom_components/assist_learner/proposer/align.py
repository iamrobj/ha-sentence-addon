"""Fuzzy alignment of resolved slot values against the (STT) text."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from ..capture import normalise

MIN_RATIO = 0.8


@dataclass
class Span:
    start: int  # token index, inclusive
    end: int  # token index, exclusive
    ratio: float


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def find_span(tokens: list[str], value: str, taken: set[int] | None = None) -> Span | None:
    """Best-matching token window for `value`, ignoring token indexes in `taken`."""
    target = normalise(value)
    if not target:
        return None
    n = len(target.split())
    taken = taken or set()
    best: Span | None = None
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(0, len(tokens) - size + 1):
            idx = range(i, i + size)
            if any(j in taken for j in idx):
                continue
            window = " ".join(tokens[i : i + size])
            r = _ratio(window, target)
            # prefer higher ratio; on ties prefer the window whose size equals the value's size
            if r >= MIN_RATIO and (
                best is None or r > best.ratio + 1e-9 or (abs(r - best.ratio) < 1e-9 and size == n)
            ):
                best = Span(i, i + size, r)
    return best


def templatise(tokens: list[str], spans: dict[str, Span]) -> str:
    """Replace each span with {slot}; returns a space-joined template."""
    starts = {s.start: (name, s) for name, s in spans.items()}
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i in starts:
            name, span = starts[i]
            out.append("{" + name + "}")
            i = span.end
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)
