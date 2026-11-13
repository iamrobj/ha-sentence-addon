"""Action commands: service allow/block lists, validation, rate-limited executor."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.util import dt as dt_util

# Allowed by default, and only on entities in the command's own list.
ALLOWED_SERVICES: dict[str, set[str]] = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage", "increase_speed", "decrease_speed"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "media_player": {
        "media_play", "media_pause", "media_stop", "media_next_track", "media_previous_track",
        "volume_set", "volume_up", "volume_down", "volume_mute", "turn_on", "turn_off", "toggle",
    },  # not arbitrary play_media URLs
    "climate": {"set_temperature", "set_hvac_mode", "turn_on", "turn_off"},
    "vacuum": {"start", "stop", "pause", "return_to_base", "locate"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position", "toggle"},
    "scene": {"turn_on"},
    "script": {"turn_on"},
    "automation": {"trigger"},
}

# Hard-blocked in v1, no override.
BLOCKED_DOMAINS = {
    "lock", "alarm_control_panel", "siren", "valve", "shell_command", "python_script",
    "hassio", "homeassistant", "persistent_notification",
}
BLOCKED_COVER_CLASSES = {"garage", "gate", "door"}
POWERFUL_DOMAINS = {"script", "automation"}  # flagged on the approval screen

SLOT_REF_PREFIX = "{"


def is_slot_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("{") and value.endswith("}") and value[1:-1].isidentifier()


@dataclass
class ActionReport:
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # e.g. script/automation warnings
    calls: list[dict[str, Any]] = field(default_factory=list)  # resolved preview

    @property
    def ok(self) -> bool:
        return not self.errors


def blocked_reason(hass: HomeAssistant, entity_id: str, allowed: dict[str, set[str]] | None = None) -> str | None:
    domain = entity_id.split(".", 1)[0]
    if domain in BLOCKED_DOMAINS:
        return f"{domain} entities are blocked in v1 and cannot be controlled"
    if domain == "cover":
        state = hass.states.get(entity_id)
        if state and state.attributes.get("device_class") in BLOCKED_COVER_CLASSES:
            return f"{entity_id} is a {state.attributes['device_class']} cover and is blocked in v1"
    return None


async def async_validate_actions(
    hass: HomeAssistant,
    actions: list[dict[str, Any]],
    entities: dict[str, str],
    slots: dict[str, dict[str, Any]],
    allowed: dict[str, set[str]] | None = None,
) -> ActionReport:
    """Dry-run validation against the live service descriptions. Nothing is executed."""
    allowed = allowed or ALLOWED_SERVICES
    rep = ActionReport()
    if not actions:
        rep.errors.append("action command has no actions")
        return rep
    descriptions = await async_get_all_descriptions(hass)

    for i, act in enumerate(actions, 1):
        where = f"action {i}"
        svc = act.get("service", "")
        if "." not in svc:
            rep.errors.append(f"{where}: service must look like 'domain.service'")
            continue
        domain, name = svc.split(".", 1)
        if domain in BLOCKED_DOMAINS:
            rep.errors.append(f"{where}: {domain} services are blocked in v1")
            continue
        if name not in allowed.get(domain, set()):
            rep.errors.append(f"{where}: {svc} is not on the allow-list")
            continue
        if not hass.services.has_service(domain, name):
            rep.errors.append(f"{where}: service {svc} does not exist")
            continue
        if domain in POWERFUL_DOMAINS:
            rep.flags.append(f"{where}: {svc} runs a {domain} with full power - check it carefully")

        targets: list[str] = []
        for role in act.get("targets") or []:
            ent = entities.get(role)
            if ent is None:
                rep.errors.append(f"{where}: target '{role}' is not in the command's entity list")
                continue
            if ent.split(".", 1)[0] != domain:
                rep.errors.append(f"{where}: {ent} is not a {domain} entity")
                continue
            if (why := blocked_reason(hass, ent)) is not None:
                rep.errors.append(f"{where}: {why}")
                continue
            if hass.states.get(ent) is None:
                rep.errors.append(f"{where}: entity {ent} does not exist")
                continue
            targets.append(ent)
        if not targets and not rep.errors:
            rep.errors.append(f"{where}: needs at least one target")

        fields = ((descriptions.get(domain) or {}).get(name) or {}).get("fields") or {}
        data = act.get("data") or {}
        for key, value in data.items():
            if key not in fields:
                rep.errors.append(f"{where}: unknown field '{key}' for {svc}")
                continue
            if is_slot_ref(value):
                sname = value[1:-1]
                sdef = slots.get(sname)
                if sdef is None:
                    rep.errors.append(f"{where}: unknown slot {value}")
                    continue
                _check_slot_in_field(rep, where, key, sdef, fields[key])
            elif isinstance(value, str) and ("{{" in value or "{%" in value):
                rep.errors.append(f"{where}: templates are not allowed in action data ('{key}')")
            elif not isinstance(value, (str, int, float, bool)):
                rep.errors.append(f"{where}: '{key}' must be a literal or a slot reference")
            else:
                _check_literal(rep, where, key, value, fields[key])
        for key, fdef in fields.items():
            if fdef.get("required") and key not in data and key != "entity_id":
                rep.errors.append(f"{where}: missing required field '{key}'")

        if domain == "climate":
            _check_climate(hass, rep, where, targets, data, slots)

        rep.calls.append({"service": svc, "targets": targets, "data": data})
    return rep


def _number_bounds(fdef: dict[str, Any]) -> tuple[float | None, float | None]:
    num = (fdef.get("selector") or {}).get("number") or {}
    return num.get("min"), num.get("max")


def _check_literal(rep: ActionReport, where: str, key: str, value: Any, fdef: dict[str, Any]) -> None:
    lo, hi = _number_bounds(fdef)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            rep.errors.append(f"{where}: {key}={value} is outside the allowed range {lo}..{hi}")


def _check_slot_in_field(
    rep: ActionReport, where: str, key: str, sdef: dict[str, Any], fdef: dict[str, Any]
) -> None:
    if sdef.get("type") != "number":
        return
    lo, hi = _number_bounds(fdef)
    if lo is not None and sdef["min"] < lo or hi is not None and sdef["max"] > hi:
        rep.errors.append(
            f"{where}: slot range {sdef['min']}..{sdef['max']} exceeds {key}'s allowed range {lo}..{hi}"
        )


def _check_climate(hass, rep, where, targets, data, slots) -> None:
    temp = data.get("temperature")
    if temp is None:
        return
    for ent in targets:
        st = hass.states.get(ent)
        lo, hi = st.attributes.get("min_temp"), st.attributes.get("max_temp")
        if is_slot_ref(temp):
            s = slots.get(temp[1:-1], {})
            vals = [s.get("min"), s.get("max")]
        else:
            vals = [temp]
        for v in vals:
            if v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi)):
                rep.errors.append(f"{where}: temperature {v} is outside {ent}'s own range {lo}..{hi}")


# ---------------------------------------------------------------- runtime ---------------------------


class RateLimiter:
    """Max N runs per minute, per command."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def allow(self, command_id: str, limit: int) -> bool:
        now = time.monotonic()
        q = self._hits.setdefault(command_id, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


@dataclass
class ExecResult:
    ok: bool
    error: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)


def check_slot_values(slots: dict[str, dict[str, Any]], values: dict[str, Any]) -> str | None:
    for name, sdef in slots.items():
        if name not in values:
            return f"missing value for '{name}'"
        v = values[name]
        if sdef["type"] == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                return f"'{name}' must be a number"
            if v < sdef["min"] or v > sdef["max"]:
                return f"'{name}' must be between {sdef['min']} and {sdef['max']}"
        elif sdef["type"] == "choice" and v not in sdef["values"]:
            return f"'{name}' must be one of {sdef['values']}"
    return None


def _resolve_data(data: dict[str, Any], values: dict[str, Any], slots: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if is_slot_ref(v):
            name = v[1:-1]
            val = values[name]
            if slots[name]["type"] == "number":
                f = float(val)
                val = int(f) if f.is_integer() else f
            out[k] = val
        else:
            out[k] = v
    return out


async def async_execute(
    hass: HomeAssistant,
    command: dict[str, Any],
    values: dict[str, Any],
    context: Context,
    *,
    entities: dict[str, str] | None = None,
) -> ExecResult:
    """Run an action command's service calls in order; stop on the first failure."""
    entities = entities or command["entities"]
    slots = command.get("slots") or {}
    if (err := check_slot_values(slots, values)) is not None:
        return ExecResult(False, err)
    done: list[dict[str, Any]] = []
    for act in command.get("actions") or []:
        domain, name = act["service"].split(".", 1)
        data = _resolve_data(act.get("data") or {}, values, slots)
        data["entity_id"] = [entities[r] for r in act["targets"]]
        # re-check at runtime: entity lists may have been edited, hard blocks always apply
        for ent in data["entity_id"]:
            if (why := blocked_reason(hass, ent)) is not None:
                return ExecResult(False, why, done)
        try:
            await hass.services.async_call(domain, name, data, blocking=True, context=context)
        except Exception as err:
            done.append({"service": act["service"], "data": data, "error": str(err)})
            return ExecResult(False, f"{act['service']} failed: {err}", done)
        done.append({"service": act["service"], "data": data})
    return ExecResult(True, None, done)


def log_execution(
    store, command: dict[str, Any], origin_device: str | None, result: ExecResult, limit: int, source: str = "voice"
) -> None:
    """Append to the capped execution log."""
    key = f"{dt_util.utcnow().isoformat()}_{command['id']}"
    store.executions.put(
        key,
        {
            "time": dt_util.utcnow().isoformat(),
            "command_id": command["id"],
            "command": command["name"],
            "device_id": origin_device,
            "source": source,
            "calls": result.calls,
            "ok": result.ok,
            "error": result.error,
        },
    )
    extra = len(store.executions.items) - limit
    if extra > 0:
        for k in sorted(store.executions.items)[:extra]:
            store.executions.delete(k)
