"""Websocket API for the panel (all commands require admin)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import learned, recognizer
from .const import CONF_LANGUAGE, DOMAIN, STATUS_PENDING
from .options import get_options
from .proposer import async_run_proposer
from .sentences import COMMAND_INTENT, MUSIC_INTENT

P = "assist_learner"
_REGISTERED = f"{DOMAIN}_ws_registered"

Handler = Callable[[HomeAssistant, ConfigEntry, dict[str, Any]], Coroutine[Any, Any, Any]]


def _entry(hass: HomeAssistant) -> ConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Assist Learner is not set up")
    return entries[0]


def _cmd(name: str, schema: dict[Any, Any], handler: Handler):
    """Build a registered websocket command: admin only, errors reported back, result sent."""

    @websocket_api.websocket_command({vol.Required("type"): f"{P}/{name}", **schema})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def _ws(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            result = await handler(hass, _entry(hass), msg)
        except HomeAssistantError as err:
            connection.send_error(msg["id"], "failed", str(err))
            return
        connection.send_result(msg["id"], result)

    return _ws


def _scrub(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "history"} | {"versions": len(row.get("history", []))}


# ------------------------------------------------------------------------------------ handlers ---------
async def h_overview(hass, entry, msg):
    s = entry.runtime_data.store
    opts = get_options(entry)
    return {
        "pending_proposals": sum(1 for p in s.proposals.values() if p["status"] == STATUS_PENDING),
        "pending_commands": sum(1 for c in s.commands.values() if c["status"] == "pending"),
        "learned": len(s.learned.values()),
        "commands": len(s.commands.values()),
        "fallbacks_today": s.today("fallbacks"),
        "local_hits_today": s.today("local_hits"),
        "ai_task": opts["ai_task_entity"],
        "language": entry.data[CONF_LANGUAGE],
        "send_states": opts["send_states_to_llm"],
    }


async def h_proposals_list(hass, entry, msg):
    return [p for p in entry.runtime_data.store.proposals.values() if p["status"] == STATUS_PENDING]


async def h_proposals_decide(hass, entry, msg):
    if msg["decision"] == "approve":
        row = await learned.async_approve(hass, entry, msg["proposal_id"], msg.get("template"))
        return _scrub(row)
    await learned.async_reject(hass, entry, msg["proposal_id"], msg.get("note"))
    return {}


async def h_proposals_bulk(hass, entry, msg):
    done, failed = [], []
    for pid in msg["proposal_ids"]:
        try:
            if msg["decision"] == "approve":
                await learned.async_approve(hass, entry, pid)
            else:
                await learned.async_reject(hass, entry, pid, msg.get("note"))
            done.append(pid)
        except HomeAssistantError as err:
            failed.append({"id": pid, "error": str(err)})
    return {"done": done, "failed": failed}


async def h_misses_export(hass, entry, msg):
    """Full miss records for the offline replay harness (raw text exists only while retained)."""
    return entry.runtime_data.store.misses.values()


async def h_run_proposer(hass, entry, msg):
    created = await async_run_proposer(hass, entry)
    return {"created": len(created)}


async def h_sentences_list(hass, entry, msg):
    s = entry.runtime_data.store
    rows: list[dict[str, Any]] = []
    for t in s.learned.values():
        rows.append(
            {
                "source": "learned", "id": t["id"], "title": t["template"], "sentences": [t["template"]],
                "enabled": t["enabled"], "hits": t["hits"], "last_used": t["last_used"],
                "versions": len(t.get("history", [])), "editable": True,
            }
        )
    for c in s.commands.values():
        if c["status"] == "pending" and not msg.get("include_pending"):
            continue
        rows.append(
            {
                "source": "custom", "id": c["id"], "title": c["name"], "sentences": c["sentences"],
                "enabled": c["status"] == "active", "status": c["status"], "type": c["type"],
                "response": c["response_template"], "actions": c["actions"], "hits": c["hits"],
                "last_used": c["last_used"], "versions": len(c["history"]), "editable": True,
                "collisions": (c.get("validation") or {}).get("collisions", []),
                "imported_from": c.get("imported_from"),
            }
        )
    ext = await entry.runtime_data.commands.import_discover()
    for f in ext["files"]:
        rows.append({"source": "external", "kind": "file", "id": f"{f['file']}#{f['intent']}", "title": f["intent"],
                     "sentences": f["sentences"], "file": f["file"], "editable": False, "importable": True})
    for a in ext["automations"]:
        rows.append({"source": "external", "kind": "automation", "id": a.get("id"), "title": a.get("alias"),
                     "sentences": a["sentences"], "editable": False, "importable": False})
    want = msg.get("source")
    return [r for r in rows if not want or r["source"] == want]


async def h_sentences_test(hass, entry, msg):
    """What would match this text, with the extracted slots? Nothing is executed."""
    m = await recognizer.async_match(hass, msg["text"], entry.data[CONF_LANGUAGE])
    if m is None:
        return {"matched": False}
    out: dict[str, Any] = {"matched": True, "intent": m.intent_name, "slots": m.slots, "source": "builtin"}
    s = entry.runtime_data.store
    if m.intent_name == MUSIC_INTENT and (row := s.learned.get(m.slots.get("template_id", ""))):
        out.update(source="learned", id=row["id"], title=row["template"])
    elif m.intent_name == COMMAND_INTENT and (c := s.commands.get(m.slots.get("command_id", ""))):
        out.update(source="custom", id=c["id"], title=c["name"])
    return out


async def h_learned_update(hass, entry, msg):
    if "template" in msg:
        await learned.async_edit(hass, entry, msg["item_id"], msg["template"])
    if "enabled" in msg:
        await learned.async_set_enabled(hass, entry, msg["item_id"], msg["enabled"])
    return _scrub(entry.runtime_data.store.learned.get(msg["item_id"]))


async def h_learned_delete(hass, entry, msg):
    await learned.async_delete(hass, entry, msg["item_id"])
    return {}


async def h_learned_history(hass, entry, msg):
    row = entry.runtime_data.store.learned.get(msg["item_id"])
    return row.get("history", []) if row else []


async def h_learned_revert(hass, entry, msg):
    return _scrub(await learned.async_revert(hass, entry, msg["item_id"], msg["index"]))


# commands
async def h_cmd_list(hass, entry, msg):
    return [_scrub(c) for c in entry.runtime_data.commands.list()]


async def h_cmd_get(hass, entry, msg):
    return _scrub(entry.runtime_data.store.commands.get(msg["item_id"]) or {})


async def h_cmd_create(hass, entry, msg):
    return _scrub(
        entry.runtime_data.commands.create(
            msg["name"], msg["description"], msg["entities"], msg.get("examples"), msg.get("cmd_type", "answer")
        )
    )


async def h_cmd_payload(hass, entry, msg):
    return await entry.runtime_data.commands.payload(msg["item_id"], msg.get("feedback"))


async def h_cmd_generate(hass, entry, msg):
    return _scrub(await entry.runtime_data.commands.generate(msg["item_id"], msg.get("feedback")))


async def h_cmd_update(hass, entry, msg):
    return _scrub(await entry.runtime_data.commands.update(msg["item_id"], msg["fields"]))


async def h_cmd_decide(hass, entry, msg):
    mgr = entry.runtime_data.commands
    d = msg["decision"]
    if d == "approve":
        return _scrub(await mgr.approve(msg["item_id"]))
    if d == "reject":
        return _scrub(await mgr.reject(msg["item_id"], msg.get("note")))
    if d in ("enable", "disable"):
        return _scrub(await mgr.set_enabled(msg["item_id"], d == "enable"))
    raise HomeAssistantError(f"Unknown decision {d}")


async def h_cmd_delete(hass, entry, msg):
    await entry.runtime_data.commands.delete(msg["item_id"])
    return {}


async def h_cmd_revert(hass, entry, msg):
    return _scrub(await entry.runtime_data.commands.revert(msg["item_id"], msg["index"]))


async def h_cmd_history(hass, entry, msg):
    return entry.runtime_data.commands.history(msg["item_id"])


async def h_cmd_run_once(hass, entry, msg):
    res = await entry.runtime_data.commands.run_once(msg["item_id"], msg.get("values"))
    return {"ok": res.ok, "error": res.error, "calls": res.calls}


async def h_executions(hass, entry, msg):
    rows = entry.runtime_data.store.executions.values()
    return sorted(rows, key=lambda r: r["time"], reverse=True)[: msg.get("limit", 100)]


async def h_misses(hass, entry, msg):
    rows = sorted(entry.runtime_data.store.misses.values(), key=lambda r: r["timestamp"], reverse=True)
    return [
        {k: r.get(k) for k in ("id", "timestamp", "text", "success", "error", "unalignable", "processed", "actions")}
        for r in rows[: msg.get("limit", 200)]
    ]


async def h_stats(hass, entry, msg):
    s = entry.runtime_data.store
    return {
        "daily": [{"date": d, **v} for d, v in sorted(s.stats.items.items())],
        "learned": [{"id": t["id"], "template": t["template"], "hits": t["hits"]} for t in s.learned.values()],
        "commands": [{"id": c["id"], "name": c["name"], "hits": c["hits"]} for c in s.commands.values()],
    }


async def h_import_discover(hass, entry, msg):
    return await entry.runtime_data.commands.import_discover()


async def h_import_preview(hass, entry, msg):
    return await entry.runtime_data.commands.import_preview()


async def h_import_apply(hass, entry, msg):
    return [_scrub(c) for c in await entry.runtime_data.commands.import_apply(msg["intents"])]


_STR = str
_ID = {vol.Required("item_id"): _STR}  # `id` is reserved by the websocket protocol (message id)

COMMANDS = [
    _cmd("overview", {}, h_overview),
    _cmd("proposals/list", {}, h_proposals_list),
    _cmd("proposals/decide", {vol.Required("proposal_id"): _STR, vol.Required("decision"): vol.In(["approve", "reject"]),
                              vol.Optional("template"): _STR, vol.Optional("note"): _STR}, h_proposals_decide),
    _cmd("run_proposer", {}, h_run_proposer),
    _cmd("proposals/bulk", {vol.Required("proposal_ids"): [_STR], vol.Required("decision"): vol.In(["approve", "reject"]),
                            vol.Optional("note"): _STR}, h_proposals_bulk),
    _cmd("misses/export", {}, h_misses_export),
    _cmd("sentences/list", {vol.Optional("source"): vol.In(["learned", "custom", "external"]),
                            vol.Optional("include_pending"): bool}, h_sentences_list),
    _cmd("sentences/test", {vol.Required("text"): _STR}, h_sentences_test),
    _cmd("learned/update", {**_ID, vol.Optional("template"): _STR, vol.Optional("enabled"): bool}, h_learned_update),
    _cmd("learned/delete", _ID, h_learned_delete),
    _cmd("learned/history", _ID, h_learned_history),
    _cmd("learned/revert", {**_ID, vol.Required("index"): int}, h_learned_revert),
    _cmd("commands/list", {}, h_cmd_list),
    _cmd("commands/get", _ID, h_cmd_get),
    _cmd("commands/create", {vol.Required("name"): _STR, vol.Required("description"): _STR,
                             vol.Required("entities"): {_STR: _STR}, vol.Optional("examples"): [_STR],
                             vol.Optional("cmd_type"): vol.In(["answer", "action", "answer_and_action"])}, h_cmd_create),
    _cmd("commands/payload", {**_ID, vol.Optional("feedback"): _STR}, h_cmd_payload),
    _cmd("commands/generate", {**_ID, vol.Optional("feedback"): _STR}, h_cmd_generate),
    _cmd("commands/regenerate", {**_ID, vol.Required("feedback"): _STR}, h_cmd_generate),
    _cmd("commands/update", {**_ID, vol.Required("fields"): dict}, h_cmd_update),
    _cmd("commands/decide", {**_ID, vol.Required("decision"): vol.In(["approve", "reject", "enable", "disable"]),
                             vol.Optional("note"): _STR}, h_cmd_decide),
    _cmd("commands/delete", _ID, h_cmd_delete),
    _cmd("commands/revert", {**_ID, vol.Required("index"): int}, h_cmd_revert),
    _cmd("commands/history", _ID, h_cmd_history),
    _cmd("commands/run_once", {**_ID, vol.Optional("values"): dict}, h_cmd_run_once),
    _cmd("executions/list", {vol.Optional("limit"): int}, h_executions),
    _cmd("misses/list", {vol.Optional("limit"): int}, h_misses),
    _cmd("stats", {}, h_stats),
    _cmd("import/discover", {}, h_import_discover),
    _cmd("import/preview", {}, h_import_preview),
    _cmd("import/apply", {vol.Required("intents"): [_STR]}, h_import_apply),
]


def async_register(hass: HomeAssistant) -> None:
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
