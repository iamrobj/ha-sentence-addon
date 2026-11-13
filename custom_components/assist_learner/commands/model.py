"""Custom command records (plain dicts in the `commands` store)."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from homeassistant.util import dt as dt_util

TYPES = ("answer", "action", "answer_and_action")
STATUSES = ("draft", "pending", "active", "disabled")
HASH_FIELDS = (
    "type",
    "entities",
    "slots",
    "actions",
    "sentences",
    "response_template",
    "unavailable_template",
)
DEFAULT_CONFIRMATION = "Done."


def new_id() -> str:
    return f"cmd_{uuid.uuid4().hex[:6]}"


def snapshot(cmd: dict[str, Any]) -> dict[str, Any]:
    return {k: json.loads(json.dumps(cmd.get(k))) for k in HASH_FIELDS}


def content_hash(cmd: dict[str, Any]) -> str:
    blob = json.dumps(snapshot(cmd), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def new_command(
    name: str,
    description: str,
    entities: dict[str, str],
    examples: list[str] | None = None,
    cmd_type: str = "answer",
) -> dict[str, Any]:
    now = dt_util.utcnow().isoformat()
    return {
        "id": new_id(),
        "name": name,
        "description": description,
        "examples": examples or [],
        "type": cmd_type,
        "entities": entities,
        "slots": {},
        "actions": [],
        "sentences": [],
        "response_template": "",
        "unavailable_template": "",
        "status": "draft",
        "hash": None,
        "notes": "",
        "validation": None,
        "accept_collisions": False,
        "history": [],
        "hits": 0,
        "last_used": None,
        "created": now,
        "updated": now,
    }


def push_history(cmd: dict[str, Any], reason: str) -> None:
    cmd["history"].append({"at": dt_util.utcnow().isoformat(), "reason": reason, **snapshot(cmd)})
    cmd["history"] = cmd["history"][-50:]


def slot_scope(cmd_id: str, slot: str) -> str:
    """Name of the YAML list/slot for a command-scoped slot (lists are global in the sentences file)."""
    return f"{cmd_id}_{slot}"


def scope_sentence(sentence: str, cmd_id: str, slot_names: set[str]) -> str:
    out = sentence
    for s in slot_names:
        out = out.replace("{" + s + "}", "{" + slot_scope(cmd_id, s) + "}")
    return out
