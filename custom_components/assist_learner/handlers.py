"""Runtime intent handlers registered in Python (not intent_script)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .const import CONF_AREA_PLAYERS, CONF_DEFAULT_PLAYER, CONF_PREFER_OWN_PLAYER
from .learned import record_hit
from .options import get_options
from .players import resolve_player
from .sentences import MUSIC_INTENT

_LOGGER = logging.getLogger(__name__)


def async_register_intents(hass: HomeAssistant, entry: ConfigEntry) -> None:
    intent.async_register(hass, PlayMediaHandler(entry))


def async_unregister_intents(hass: HomeAssistant) -> None:
    intent.async_remove(hass, MUSIC_INTENT)


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
