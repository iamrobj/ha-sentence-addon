"""Storage wrappers around homeassistant.helpers.storage.Store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORE_VERSION = 1
SAVE_DELAY = 5

COLLECTIONS = (
    "misses",
    "proposals",
    "learned",
    "candidates",
    "executions",
    "rejected",
    "commands",
    "generation_log",
    "stats",
)


class _MigratingStore(Store[dict[str, Any]]):
    """Versioned JSON store with one migration step per schema version."""

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        return migrate_data(old_major_version, old_data)


def migrate_data(version: int, data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade stored data one version at a time up to STORE_VERSION (v1 is the first schema)."""
    if version > STORE_VERSION:
        raise ValueError(f"Stored data version {version} is newer than supported {STORE_VERSION}")
    # example for the future: if version < 2: data = {...}; version = 2
    return data


class Collection:
    """A dict-of-dicts persisted to one storage file."""

    def __init__(self, hass: HomeAssistant, name: str) -> None:
        self.name = name
        self._store: Store[dict[str, Any]] = _MigratingStore(hass, STORE_VERSION, f"{DOMAIN}.{name}")
        self.items: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self.items = (data or {}).get("items", {})

    def _data(self) -> dict[str, Any]:
        return {"items": self.items}

    def schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(self._data())

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.items[key] = value
        self.schedule_save()

    def get(self, key: str) -> dict[str, Any] | None:
        return self.items.get(key)

    def delete(self, key: str) -> None:
        if self.items.pop(key, None) is not None:
            self.schedule_save()

    def values(self) -> list[dict[str, Any]]:
        return list(self.items.values())

    def prune(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        """Delete every item for which predicate is true; return how many."""
        doomed = [k for k, v in self.items.items() if predicate(v)]
        for k in doomed:
            del self.items[k]
        if doomed:
            self.schedule_save()
        return len(doomed)


class AssistLearnerStore:
    """All collections used by the integration."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.misses = Collection(hass, "misses")
        self.proposals = Collection(hass, "proposals")
        self.learned = Collection(hass, "learned")
        self.candidates = Collection(hass, "candidates")
        self.executions = Collection(hass, "executions")
        self.rejected = Collection(hass, "rejected")
        self.commands = Collection(hass, "commands")
        self.generation_log = Collection(hass, "generation_log")
        self.stats = Collection(hass, "stats")  # key = YYYY-MM-DD

    async def async_load(self) -> None:
        for name in COLLECTIONS:
            await getattr(self, name).async_load()

    async def async_save_all(self) -> None:
        for name in COLLECTIONS:
            await getattr(self, name).async_save()

    # --- daily counters -------------------------------------------------
    def bump(self, counter: str, amount: int = 1) -> None:
        """Increment a per-day counter (fallbacks, local_hits)."""
        today = dt_util.now().date().isoformat()
        row = self.stats.get(today) or {}
        row[counter] = row.get(counter, 0) + amount
        self.stats.put(today, row)
        cutoff = (dt_util.now().date() - timedelta(days=60)).isoformat()
        for key in [k for k in self.stats.items if k < cutoff]:
            del self.stats.items[key]

    def today(self, counter: str) -> int:
        row = self.stats.get(dt_util.now().date().isoformat()) or {}
        return int(row.get(counter, 0))
