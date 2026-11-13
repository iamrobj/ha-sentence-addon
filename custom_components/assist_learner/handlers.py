"""Runtime intent handlers registered in Python (not intent_script)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .commands.actions import async_execute, log_execution
from .commands.model import content_hash, slot_scope
from .const import (
    CONF_AREA_PLAYERS,
    CONF_DEFAULT_PLAYER,
    CONF_EXEC_LOG_SIZE,
    CONF_PREFER_OWN_PLAYER,
    CONF_RATE_LIMIT,
)
from .learned import record_hit
from .options import get_options
from .players import resolve_player
from .sentences import COMMAND_INTENT, MUSIC_INTENT

_LOGGER = logging.getLogger(__name__)


def async_register_intents(hass: HomeAssistant, entry: ConfigEntry) -> None:
    intent.async_register(hass, PlayMediaHandler(entry))
    intent.async_register(hass, CustomCommandHandler(entry))


def async_unregister_intents(hass: HomeAssistant) -> None:
    intent.async_remove(hass, MUSIC_INTENT)
    intent.async_remove(hass, COMMAND_INTENT)


def _slot(intent_obj: intent.Intent, name: str) -> str | None:
    slot = intent_obj.slots.get(name)
    if slot is None:
        return None
    value = slot.get("value")
    return str(value) if value not in (None, "") else None


class PlayMediaHandler(intent.IntentHandler):
    intent_type = MUSIC_INTENT

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        opts = get_options(self._entry)
        response = intent_obj.create_response()

        track = _slot(intent_obj, "track")
        artist = _slot(intent_obj, "artist")
        media_type = _slot(intent_obj, "media_type") or "track"
        spoken = _slot(intent_obj, "player")
        if spoken == "none":
            spoken = None
        template_id = _slot(intent_obj, "template_id")

        if not track:
            response.async_set_speech("I didn't catch what to play.")
            return response

        res = resolve_player(
            hass,
            spoken=spoken,
            device_id=intent_obj.device_id,
            default_player=opts[CONF_DEFAULT_PLAYER],
            preferred_by_area=opts[CONF_AREA_PLAYERS],
            prefer_own=opts[CONF_PREFER_OWN_PLAYER],
        )
        if res.entity_id is None:
            response.async_set_speech(res.error or "Which speaker should I use?")
            return response

        data = {"entity_id": res.entity_id, "media_id": track, "media_type": media_type}
        if artist:
            data["artist"] = artist
        label = f"{track} by {artist}" if artist else track
        try:
            await hass.services.async_call(
                "music_assistant", "play_media", data, blocking=True, context=intent_obj.context
            )
        except Exception as err:  # MA found nothing / player offline: say so, no LLM fallback possible
            _LOGGER.debug("play_media failed: %s", err)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE, f"I couldn't find {label}"
            )
            return response

        if template_id:
            record_hit(self._entry, template_id)
        state = hass.states.get(res.entity_id)
        where = state.name if state else res.entity_id
        response.async_set_speech(f"Playing {label} in the {where}")
        return response


class CustomCommandHandler(intent.IntentHandler):
    """Runs approved custom commands: validates, (optionally) acts, renders the spoken response."""

    intent_type = COMMAND_INTENT

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        response = intent_obj.create_response()
        manager = self._entry.runtime_data.commands
        store = self._entry.runtime_data.store
        opts = get_options(self._entry)

        cmd_id = _slot(intent_obj, "command_id")
        cmd = store.commands.get(cmd_id) if cmd_id else None
        if cmd is None or cmd["status"] != "active" or cmd.get("hash") != content_hash(cmd):
            if cmd is not None and cmd["status"] == "active":
                cmd["status"] = "disabled"
                cmd["notes"] = "Disabled: content changed outside the panel"
                store.commands.put(cmd["id"], cmd)
            response.async_set_speech("That command is switched off.")
            return response

        slots = cmd.get("slots") or {}
        values = {n: _slot(intent_obj, slot_scope(cmd["id"], n)) for n in slots}

        if cmd["type"] in ("action", "answer_and_action"):
            if not manager.rate_limiter.allow(cmd["id"], opts[CONF_RATE_LIMIT]):
                response.async_set_speech("I'm doing that too often. Try again in a minute.")
                return response
            result = await async_execute(hass, cmd, values, intent_obj.context)
            log_execution(
                store, cmd, intent_obj.device_id, result, opts[CONF_EXEC_LOG_SIZE]
            )
            if not result.ok:
                response.async_set_error(
                    intent.IntentResponseErrorCode.FAILED_TO_HANDLE, f"Sorry, that failed. {result.error}"
                )
                return response

        text = self._render(hass, cmd, values)
        cmd["hits"] = cmd.get("hits", 0) + 1
        cmd["last_used"] = dt_util.utcnow().isoformat()
        store.commands.put(cmd["id"], cmd)
        store.bump("local_hits")
        response.async_set_speech(text)
        return response

    @staticmethod
    def _render(hass: HomeAssistant, cmd: dict, values: dict) -> str:
        typed = {
            k: (float(v) if (cmd["slots"][k]["type"] == "number" and v is not None) else v)
            for k, v in values.items()
        }
        template = cmd.get("response_template") or "Done."
        unavailable = cmd.get("unavailable_template")
        if unavailable and any(
            (s := hass.states.get(e)) is None or s.state in ("unavailable", "unknown")
            for e in cmd["entities"].values()
        ):
            template = unavailable
        for _ in (0, 1):
            try:
                return Template(template, hass).async_render({"slots": typed}, parse_result=False).strip() or "Done."
            except Exception as err:
                _LOGGER.warning("Rendering command %s failed: %s", cmd["id"], err)
                if not unavailable or template == unavailable:
                    break
                template = unavailable
        return "Sorry, I couldn't work that out."
