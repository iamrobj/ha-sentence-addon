"""Assist Learner: learn local voice commands from LLM fallbacks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_RUN_TIME, DEFAULT_OPTIONS, DOMAIN
from .store import AssistLearnerStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation", "sensor"]

type AssistLearnerConfigEntry = ConfigEntry["RuntimeData"]


@dataclass
class RuntimeData:
    store: AssistLearnerStore
    listeners: list[Callable[[], None]] = field(default_factory=list)

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    @callback
    def async_notify_listeners(self) -> None:
        for cb in list(self.listeners):
            cb()


def get_options(entry: ConfigEntry) -> dict:
    """Entry options merged over defaults."""
    return {**DEFAULT_OPTIONS, **entry.options}


async def async_setup_entry(hass: HomeAssistant, entry: AssistLearnerConfigEntry) -> bool:
    store = AssistLearnerStore(hass)
    await store.async_load()
    entry.runtime_data = RuntimeData(store=store)
    _schedule_nightly(hass, entry)
    _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AssistLearnerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.store.async_save_all()
    return unloaded


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _schedule_nightly(hass: HomeAssistant, entry: ConfigEntry) -> None:
    run_time = dt_util.parse_time(get_options(entry)[CONF_RUN_TIME]) or dt_util.parse_time("03:15:00")

    async def _run(_now) -> None:
        from .proposer import async_run_proposer

        await async_run_proposer(hass, entry)

    entry.async_on_unload(
        async_track_time_change(
            hass, _run, hour=run_time.hour, minute=run_time.minute, second=run_time.second
        )
    )


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "run_proposer"):
        return

    async def _run_proposer(call: ServiceCall) -> None:
        from .proposer import async_run_proposer

        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await async_run_proposer(hass, entry)

    hass.services.async_register(DOMAIN, "run_proposer", _run_proposer)
