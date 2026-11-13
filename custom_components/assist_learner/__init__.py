"""Assist Learner: learn local voice commands from LLM fallbacks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import websocket
from .commands.manager import CommandManager
from .const import CONF_RUN_TIME, DOMAIN
from .handlers import async_register_intents, async_unregister_intents
from .options import get_options
from .panel import async_register_panel
from .repairs import sync_issues
from .store import AssistLearnerStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["conversation", "sensor", "button"]

type AssistLearnerConfigEntry = ConfigEntry["RuntimeData"]


@dataclass
class RuntimeData:
    store: AssistLearnerStore
    listeners: list[Callable[[], None]] = field(default_factory=list)
    commands: CommandManager | None = None

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    @callback
    def async_notify_listeners(self) -> None:
        for cb in list(self.listeners):
            cb()


async def async_setup_entry(hass: HomeAssistant, entry: AssistLearnerConfigEntry) -> bool:
    store = AssistLearnerStore(hass)
    await store.async_load()
    entry.runtime_data = RuntimeData(store=store)
    entry.runtime_data.commands = CommandManager(hass, entry)
    _schedule_nightly(hass, entry)
    _register_services(hass)
    websocket.async_register(hass)
    await async_register_panel(hass)
    entry.async_on_unload(
        entry.runtime_data.async_add_listener(lambda: sync_issues(hass, entry))
    )
    sync_issues(hass, entry)
    async_register_intents(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # validate active commands once everything (services, entities) has started
    @callback
    def _started(_hass: HomeAssistant) -> None:
        entry.async_create_background_task(
            hass, entry.runtime_data.commands.revalidate_all(), name=f"{DOMAIN}_revalidate"
        )

    async_at_started(hass, _started)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AssistLearnerConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        async_unregister_intents(hass)
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


def _first_entry(hass: HomeAssistant) -> ConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Assist Learner is not set up")
    return entries[0]


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "run_proposer"):
        return

    async def run_proposer(call: ServiceCall) -> None:
        from .proposer import async_run_proposer

        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await async_run_proposer(hass, entry)

    async def approve(call: ServiceCall) -> None:
        from . import learned

        await learned.async_approve(
            hass, _first_entry(hass), call.data["proposal_id"], call.data.get("template")
        )

    async def reject(call: ServiceCall) -> None:
        from . import learned

        await learned.async_reject(hass, _first_entry(hass), call.data["proposal_id"], call.data.get("note"))

    async def disable_learned(call: ServiceCall) -> None:
        from . import learned

        await learned.async_set_enabled(hass, _first_entry(hass), call.data["learned_id"], False)

    async def delete_learned(call: ServiceCall) -> None:
        from . import learned

        await learned.async_delete(hass, _first_entry(hass), call.data["learned_id"])

    reg = async_register_admin_service
    reg(hass, DOMAIN, "run_proposer", run_proposer, vol.Schema({}))
    reg(hass, DOMAIN, "approve", approve, vol.Schema({vol.Required("proposal_id"): str, vol.Optional("template"): str}))
    reg(hass, DOMAIN, "reject", reject, vol.Schema({vol.Required("proposal_id"): str, vol.Optional("note"): str}))
    reg(hass, DOMAIN, "disable_learned", disable_learned, vol.Schema({vol.Required("learned_id"): str}))
    reg(hass, DOMAIN, "delete_learned", delete_learned, vol.Schema({vol.Required("learned_id"): str}))


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Config-entry migrations (version 1 is current; newer entries cannot be downgraded)."""
    if entry.version > 1:
        return False
    return True
