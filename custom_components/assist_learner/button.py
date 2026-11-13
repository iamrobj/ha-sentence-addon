"""Run-now button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .proposer import async_run_proposer


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([RunNowButton(entry)])


class RunNowButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Run now"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_run_now"
        self.entity_id = f"button.{DOMAIN}_run_now"

    async def async_press(self) -> None:
        await async_run_proposer(self.hass, self._entry)
