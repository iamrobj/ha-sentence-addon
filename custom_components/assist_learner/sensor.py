"""Sensors: pending proposals, LLM fallbacks today, local hits today."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, STATUS_PENDING


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(
        [
            PendingProposalsSensor(entry),
            CounterSensor(entry, "fallbacks", "llm_fallbacks_today", "LLM fallbacks today"),
            CounterSensor(entry, "local_hits", "local_hits_today", "Local hits today"),
        ]
    )


class _Base(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, key: str, name: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self.entity_id = f"sensor.{DOMAIN}_{key}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._entry.runtime_data.async_add_listener(self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class PendingProposalsSensor(_Base):
    _attr_native_unit_of_measurement = "proposals"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "pending_proposals", "Pending proposals")

    def _pending(self) -> list[dict]:
        store = self._entry.runtime_data.store
        return [p for p in store.proposals.values() if p.get("status") == STATUS_PENDING]

    @property
    def native_value(self) -> int:
        return len(self._pending())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "proposals": [
                {k: p.get(k) for k in ("id", "type", "template", "count", "confidence", "warnings")}
                for p in self._pending()
            ]
        }


class CounterSensor(_Base):
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, entry: ConfigEntry, counter: str, key: str, name: str) -> None:
        super().__init__(entry, key, name)
        self._counter = counter

    @property
    def native_value(self) -> int:
        return self._entry.runtime_data.store.today(self._counter)
