"""Approve / reject / edit / disable / delete learned templates."""

from __future__ import annotations

import uuid
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CARRIER_VERBS,
    CONF_LANGUAGE,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_SUPERSEDED,
)
from .options import get_options
from .proposer.templating import fingerprint
from .proposer.validate import async_check_template
from .sentences import async_write_sentences


class ProposalError(HomeAssistantError):
    """Raised for invalid proposal operations (shown to the user)."""


def _proposal(store, proposal_id: str) -> dict[str, Any]:
    p = store.proposals.get(proposal_id)
    if p is None or p["status"] != "pending":
        raise ProposalError(f"Proposal {proposal_id} is not pending")
    return p


async def async_approve(
    hass: HomeAssistant,
    entry: ConfigEntry,
    proposal_id: str,
    template: str | None = None,
    auto: bool = False,
) -> dict[str, Any]:
    """Approve a pending proposal (optionally with an edited template) and activate it."""
    store = entry.runtime_data.store
    p = _proposal(store, proposal_id)
    final = " ".join((template or p["template"]).split())
    opts = get_options(entry)

    if template and final != p["template"]:
        check = await async_check_template(
            hass,
            final,
            {"track": "lose control", "artist": "teddy swims", "player": "living room"},
            entry.data[CONF_LANGUAGE],
            opts[CONF_CARRIER_VERBS],
        )
        if not check.ok:
            raise ProposalError(check.drop_reason or check.block_reason or "invalid template")

    now = dt_util.utcnow().isoformat()
    learned_id = p.get("replaces") or f"lt_{uuid.uuid4().hex[:8]}"
    existing = store.learned.get(learned_id)
    history = (existing or {}).get("history", [])
    if existing:
        history = [*history, {"at": now, "template": existing["template"]}]
        p_status_old = STATUS_SUPERSEDED
    else:
        p_status_old = None
    row = {
        "id": learned_id,
        "family": p["family"],
        "template": final,
        "fingerprint": fingerprint(final),
        "enabled": True,
        "hits": (existing or {}).get("hits", 0),
        "last_used": (existing or {}).get("last_used"),
        "created": (existing or {}).get("created", now),
        "slots": p.get("slots", {}),
        "history": history,
    }
    store.learned.put(learned_id, row)
    _decide(store, p, STATUS_APPROVED, None)
    if p_status_old:
        p["note"] = "replaced earlier template"
    await async_write_sentences(hass, entry)
    entry.runtime_data.async_notify_listeners()
    return row


async def async_reject(
    hass: HomeAssistant, entry: ConfigEntry, proposal_id: str, note: str | None = None
) -> None:
    store = entry.runtime_data.store
    p = _proposal(store, proposal_id)
    store.rejected.put(p["fingerprint"], {"at": dt_util.utcnow().isoformat(), "note": note})
    _decide(store, p, STATUS_REJECTED, note)
    entry.runtime_data.async_notify_listeners()


def _decide(store, p: dict[str, Any], status: str, note: str | None) -> None:
    p["status"] = status
    p["decided"] = dt_util.utcnow().isoformat()
    p["note"] = note
    p["examples"] = []  # raw examples are deleted when you decide
    store.proposals.put(p["id"], p)


async def async_set_enabled(
    hass: HomeAssistant, entry: ConfigEntry, learned_id: str, enabled: bool
) -> None:
    store = entry.runtime_data.store
    row = store.learned.get(learned_id)
    if row is None:
        raise ProposalError(f"Unknown learned template {learned_id}")
    row["enabled"] = enabled
    store.learned.put(learned_id, row)
    await async_write_sentences(hass, entry)
    entry.runtime_data.async_notify_listeners()


async def async_delete(hass: HomeAssistant, entry: ConfigEntry, learned_id: str) -> None:
    store = entry.runtime_data.store
    if store.learned.get(learned_id) is None:
        raise ProposalError(f"Unknown learned template {learned_id}")
    store.learned.delete(learned_id)
    await async_write_sentences(hass, entry)
    entry.runtime_data.async_notify_listeners()


def record_hit(entry: ConfigEntry, learned_id: str) -> None:
    store = entry.runtime_data.store
    row = store.learned.get(learned_id)
    if row is None:
        return
    row["hits"] = row.get("hits", 0) + 1
    row["last_used"] = dt_util.utcnow().isoformat()
    store.learned.put(learned_id, row)
    store.bump("local_hits")
    entry.runtime_data.async_notify_listeners()


async def async_edit(hass: HomeAssistant, entry: ConfigEntry, learned_id: str, template: str) -> dict[str, Any]:
    """Edit an active learned template (re-validated; the old version goes to history)."""
    store = entry.runtime_data.store
    row = store.learned.get(learned_id)
    if row is None:
        raise ProposalError(f"Unknown learned template {learned_id}")
    final = " ".join(template.split())
    check = await async_check_template(
        hass,
        final,
        {"track": "lose control", "artist": "teddy swims", "player": "living room"},
        entry.data[CONF_LANGUAGE],
        get_options(entry)[CONF_CARRIER_VERBS],
    )
    # an already-active template matches itself, so only structural failures matter here
    if check.block_reason:
        raise ProposalError(check.block_reason)
    row["history"] = [*row.get("history", []), {"at": dt_util.utcnow().isoformat(), "template": row["template"]}]
    row["template"] = final
    row["fingerprint"] = fingerprint(final)
    store.learned.put(learned_id, row)
    await async_write_sentences(hass, entry)
    entry.runtime_data.async_notify_listeners()
    return row


async def async_revert(hass: HomeAssistant, entry: ConfigEntry, learned_id: str, index: int) -> dict[str, Any]:
    row = entry.runtime_data.store.learned.get(learned_id)
    if row is None or not (0 <= index < len(row.get("history", []))):
        raise ProposalError("No such version")
    return await async_edit(hass, entry, learned_id, row["history"][index]["template"])
