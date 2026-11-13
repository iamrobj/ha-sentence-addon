"""Learner protocol: one implementation per intent family."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import MissRecord


@dataclass
class Candidate:
    """A template extracted from one miss."""

    family: str
    template: str
    slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    example: str = ""  # raw text of the miss; never persisted in candidate counters

    @property
    def fingerprint(self) -> str:
        from .templating import fingerprint

        return fingerprint(self.template)


class Learner(Protocol):
    family: str

    def accepts(self, miss: MissRecord) -> bool: ...

    def extract(self, miss: MissRecord) -> Candidate | None:
        """Return a candidate, or None when the miss cannot be aligned (caller flags it unalignable)."""
        ...
