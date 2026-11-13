"""Capture what the target agent did: chat-log tool calls and call_service events."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from homeassistant.core import Context, Event, HomeAssistant, callback

_FILLER_PUNCT = re.compile(r"[^\w\s']", re.UNICODE)
_APOS = str.maketrans({"’": "'", "‘": "'", "`": "'"})


def normalise(text: str) -> str:
    """Lowercase, unify apostrophes, strip punctuation and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_APOS).lower()
    text = _FILLER_PUNCT.sub(" ", text)
    return " ".join(text.split())


class ServiceCallCapture:
    """Collect call_service events whose context descends from a root context."""

    def __init__(self, hass: HomeAssistant, root: Context) -> None:
        self._hass = hass
        self._ids: set[str] = {root.id}
        self.actions: list[dict[str, Any]] = []
        self._unsub = None

    def __enter__(self) -> ServiceCallCapture:
        self._unsub = self._hass.bus.async_listen("call_service", self._on_event)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _on_event(self, event: Event) -> None:
        ctx = event.context
        if ctx.id not in self._ids and ctx.parent_id not in self._ids:
            return
        self._ids.add(ctx.id)
        data = event.data
        self.actions.append(
            {
                "domain": data.get("domain"),
                "service": data.get("service"),
                "data": dict(data.get("service_data") or {}),
            }
        )


def extract_tool_calls(contents: list[Any]) -> list[dict[str, Any]]:
    """Pull tool calls and their results out of chat-log content added this turn."""
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for content in contents:
        role = getattr(content, "role", None)
        if role == "assistant" and getattr(content, "tool_calls", None):
            for tc in content.tool_calls:
                rec = {
                    "tool_name": tc.tool_name,
                    "tool_args": dict(tc.tool_args or {}),
                    "result": None,
                }
                calls.append(rec)
                by_id[tc.id] = rec
        elif role == "tool_result":
            rec = by_id.get(getattr(content, "tool_call_id", ""))
            if rec is not None:
                result = getattr(content, "result", None)  # `tool_result` is deprecated (2027.11)
                rec["result"] = getattr(result, "data", result)
    return calls
