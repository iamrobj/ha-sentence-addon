"""Option helpers (separate module to avoid import cycles with __init__)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import DEFAULT_OPTIONS


def get_options(entry: ConfigEntry) -> dict[str, Any]:
    """Entry options merged over defaults."""
    return {**DEFAULT_OPTIONS, **entry.options}
