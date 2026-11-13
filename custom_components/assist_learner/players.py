"""Music Assistant player discovery and the 4-step resolution order (plan §3.4)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

MA_PLATFORM = "music_assistant"


@dataclass
class PlayerInfo:
    entity_id: str
    name: str
    area_id: str | None
    area_name: str | None
    device_id: str | None


def _area_of(hass: HomeAssistant, entry: er.RegistryEntry) -> str | None:
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (dev := dr.async_get(hass).async_get(entry.device_id)):
        return dev.area_id
    return None


def list_players(hass: HomeAssistant) -> list[PlayerInfo]:
    """Music Assistant media_players that are exposed to Assist."""
    area_reg = ar.async_get(hass)
    out: list[PlayerInfo] = []
    for entry in er.async_get(hass).entities.values():
        if entry.platform != MA_PLATFORM or entry.domain != "media_player" or entry.disabled:
            continue
        if not async_should_expose(hass, "conversation", entry.entity_id):
            continue
        state = hass.states.get(entry.entity_id)
        name = (state.name if state else None) or entry.name or entry.original_name or entry.entity_id
        area_id = _area_of(hass, entry)
        area = area_reg.async_get_area(area_id) if area_id else None
        out.append(PlayerInfo(entry.entity_id, name, area_id, area.name if area else None, entry.device_id))
    return out


def player_list_values(players: list[PlayerInfo]) -> list[dict[str, str]]:
    """`lists.player.values` for the YAML: friendly name and area name both map to the entity."""
    seen: set[str] = set()
    values: list[dict[str, str]] = []
    for p in players:
        for phrase in (p.name, p.area_name):
            if phrase and phrase.lower() not in seen:
                seen.add(phrase.lower())
                values.append({"in": phrase.lower(), "out": p.entity_id})
    return values


@dataclass
class Resolution:
    entity_id: str | None = None
    error: str | None = None  # spoken error when entity_id is None


def resolve_player(
    hass: HomeAssistant,
    *,
    spoken: str | None,
    device_id: str | None,
    default_player: str | None,
    preferred_by_area: dict[str, str],
    prefer_own: bool = True,
) -> Resolution:
    """Spoken player > calling device's own player > same-area player > default player."""
    if spoken:
        return Resolution(entity_id=spoken)

    players = list_players(hass)
    if device_id:
        dev = dr.async_get(hass).async_get(device_id)
        if prefer_own:
            own = [p for p in players if p.device_id == device_id]
            if own:
                return Resolution(entity_id=own[0].entity_id)
        area_id = dev.area_id if dev else None
        if area_id:
            in_area = [p for p in players if p.area_id == area_id]
            if len(in_area) == 1:
                return Resolution(entity_id=in_area[0].entity_id)
            if len(in_area) > 1:
                pref = preferred_by_area.get(area_id)
                if pref and any(p.entity_id == pref for p in in_area):
                    return Resolution(entity_id=pref)
                return Resolution(error="Which speaker should I use?")
        if default_player:
            return Resolution(entity_id=default_player)
        return Resolution(error="I don't know which room you're in. Which speaker should I use?")
    if default_player:
        return Resolution(entity_id=default_player)
    return Resolution(error="Which speaker should I use?")
