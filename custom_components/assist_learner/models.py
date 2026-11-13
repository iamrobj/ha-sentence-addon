"""Plain dataclasses persisted in .storage via store.py."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Self


class _Serializable:
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class ResolvedAction(_Serializable):
    """A service call observed while the target agent handled a request."""

    domain: str
    service: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRecord(_Serializable):
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    result: Any = None


@dataclass
class MissRecord(_Serializable):
    id: str
    timestamp: str  # ISO, UTC
    text: str
    normalised: str
    language: str
    device_id: str | None
    area_id: str | None
    target_agent: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: str | None = None
    unalignable: bool = False
    processed_by_proposal_id: str | None = None
    processed: bool = False


@dataclass
class Proposal(_Serializable):
    id: str
    type: str  # "new" | "update"
    family: str
    template: str
    slots: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)  # max 3, deleted on decision
    count: int = 1
    confidence: float = 0.0
    status: str = "pending"
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ""
    replaces: str | None = None  # learned template id for updates
    diff: str | None = None
    suggestion: str | None = None  # optional LLM generalisation; never applied without approval
    created: str = ""
    decided: str | None = None
    note: str | None = None


@dataclass
class LearnedTemplate(_Serializable):
    id: str
    family: str
    template: str
    fingerprint: str
    enabled: bool = True
    hits: int = 0
    last_used: str | None = None
    created: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Candidate(_Serializable):
    fingerprint: str
    template: str
    family: str
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)
