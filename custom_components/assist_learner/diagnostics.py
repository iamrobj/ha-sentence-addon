"""Diagnostics download. Raw user text and LLM prompts are redacted (plan risk 8)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .options import get_options

REDACT = {
    "text", "normalised", "examples", "example", "description", "prompt", "result", "response_template",
    "unavailable_template", "device_id", "area_id", "note", "notes",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    store = entry.runtime_data.store
    return {
        "options": get_options(entry),
        "counts": {
            "misses": len(store.misses.values()),
            "proposals": len(store.proposals.values()),
            "learned": len(store.learned.values()),
            "commands": len(store.commands.values()),
            "executions": len(store.executions.values()),
            "rejected": len(store.rejected.values()),
        },
        "stats": store.stats.items,
        "misses": async_redact_data(store.misses.values(), REDACT | {"actions", "tool_calls"}),
        "proposals": async_redact_data(store.proposals.values(), REDACT),
        "learned": [
            {k: t.get(k) for k in ("id", "family", "template", "enabled", "hits", "last_used")}
            for t in store.learned.values()
        ],
        "commands": [
            async_redact_data(
                {k: c.get(k) for k in ("id", "name", "type", "status", "hits", "entities", "sentences", "actions", "validation", "description", "response_template")},
                REDACT,
            )
            for c in store.commands.values()
        ],
        "generation_log": async_redact_data(store.generation_log.values(), REDACT),
        "executions": async_redact_data(store.executions.values(), REDACT),
    }
