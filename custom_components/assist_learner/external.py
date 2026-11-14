"""Edit Home Assistant-owned sentences in place (user decision 2026-11-13, see LEARNINGS).

Two sources:
- automations in `automations.yaml` with conversation (sentence) triggers: only the alias and the triggers'
  `command` sentences are changed; everything else in the automation is left exactly as it was.
- hand-written `custom_sentences/<lang>/*.yaml` files (not our own): one intent's `data` blocks are replaced.

Writes use HA's own YAML loader/dumper (the same round trip HA's automation editor does, so comments in the
edited file are lost, as with the UI editor), are atomic, and are followed by the matching reload. Every sentence
is syntax-checked with hassil first so a bad edit never breaks Assist.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from hassil.parse_expression import parse_sentence
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import dump, load_yaml

from . import recognizer
from .commands.importer import OWN_FILE

_LOCK = "assist_learner_external_lock"


class ExternalEditError(HomeAssistantError):
    """User-facing error editing a Home Assistant sentence source."""


# ------------------------------------------------------------------------------- helpers ----------------
def _lock(hass: HomeAssistant) -> asyncio.Lock:
    return hass.data.setdefault(_LOCK, asyncio.Lock())


def _load(path: Path) -> Any:
    try:
        return load_yaml(str(path))
    except FileNotFoundError as err:
        raise ExternalEditError(f"{path.name} not found") from err
    except HomeAssistantError as err:
        raise ExternalEditError(f"Can't read {path.name}: {err}") from err


def _write(path: Path, doc: Any) -> None:
    write_utf8_file_atomic(str(path), dump(doc))


def check_sentences(sentences: Any, where: str) -> list[str]:
    if not isinstance(sentences, list) or not sentences:
        raise ExternalEditError(f"{where}: needs at least one sentence")
    out = []
    for s in sentences:
        text = " ".join(str(s).split())
        if not text:
            raise ExternalEditError(f"{where}: empty sentence")
        try:
            parse_sentence(text)
        except Exception as err:  # hassil raises assorted errors (incl. bare AssertionError)
            raise ExternalEditError(f"{where}: invalid sentence syntax in '{text}': {err}") from err
        out.append(text)
    return out


# ---------------------------------------------------------------------------- automations ---------------
def _automations_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("automations.yaml"))


def _triggers(auto: dict[str, Any]) -> list[dict[str, Any]]:
    key = "triggers" if "triggers" in auto else "trigger"
    trig = auto.get(key) or []
    if isinstance(trig, dict):
        trig = [trig]
        auto[key] = trig
    return [t for t in trig if isinstance(t, dict) and (t.get("trigger") or t.get("platform")) == "conversation"]


def _find_automation(doc: Any, auto_id: str) -> dict[str, Any]:
    for auto in doc if isinstance(doc, list) else []:
        if isinstance(auto, dict) and str(auto.get("id")) == auto_id:
            return auto
    raise ExternalEditError("Automation not found in automations.yaml (only UI/automations.yaml ones can be edited)")


def get_automation_sync(hass: HomeAssistant, auto_id: str) -> dict[str, Any]:
    auto = _find_automation(_load(_automations_path(hass)), auto_id)
    return {
        "alias": str(auto.get("alias") or ""),
        "triggers": [
            {"sentences": [str(c) for c in ([t["command"]] if isinstance(t.get("command"), str) else t.get("command") or [])]}
            for t in _triggers(auto)
        ],
    }


def update_automation_sync(hass: HomeAssistant, auto_id: str, fields: dict[str, Any]) -> None:
    path = _automations_path(hass)
    doc = _load(path)
    auto = _find_automation(doc, auto_id)
    conv = _triggers(auto)
    new = fields.get("triggers")
    if not isinstance(new, list) or len(new) != len(conv):
        raise ExternalEditError(
            f"This automation has {len(conv)} sentence trigger(s); edit their sentences, add/remove triggers in HA"
        )
    checked = [check_sentences((t or {}).get("sentences"), f"Trigger {i + 1}") for i, t in enumerate(new)]
    for trig, sentences in zip(conv, checked, strict=True):
        trig["command"] = sentences
    if alias := str(fields.get("alias") or "").strip():
        auto["alias"] = alias
    _write(path, doc)


# ------------------------------------------------------------------------------ sentence files ----------
def _sentence_file(hass: HomeAssistant, language: str, file: str) -> Path:
    """Resolve a client-supplied path, refusing anything outside custom_sentences/<lang> or our own file."""
    root = Path(hass.config.path("custom_sentences", recognizer.sentences_language(language))).resolve()
    path = Path(file).resolve()
    if not path.is_relative_to(root) or path.suffix != ".yaml" or path.name == OWN_FILE:
        raise ExternalEditError("Not an editable custom sentences file")
    return path


def _intent_body(doc: Any, intent: str) -> dict[str, Any]:
    body = (doc.get("intents") or {}).get(intent) if isinstance(doc, dict) else None
    if not isinstance(body, dict):
        raise ExternalEditError(f"Intent {intent} not found in the file")
    return body


def get_intent_sync(hass: HomeAssistant, language: str, file: str, intent: str) -> dict[str, Any]:
    body = _intent_body(_load(_sentence_file(hass, language, file)), intent)
    return {"data": [dict(b) for b in body.get("data") or [] if isinstance(b, dict)]}


def update_intent_sync(hass: HomeAssistant, language: str, file: str, intent: str, fields: dict[str, Any]) -> None:
    path = _sentence_file(hass, language, file)
    doc = _load(path)
    body = _intent_body(doc, intent)
    data = fields.get("data")
    if not isinstance(data, list) or not data:
        raise ExternalEditError("'data' must be a non-empty list of blocks")
    blocks = []
    for i, block in enumerate(data):
        if not isinstance(block, dict):
            raise ExternalEditError(f"Block {i + 1} must be a mapping")
        blocks.append({**block, "sentences": check_sentences(block.get("sentences"), f"Block {i + 1}")})
    body["data"] = blocks
    _write(path, doc)


# --------------------------------------------------------------------------------- async API ------------
async def async_get(hass: HomeAssistant, language: str, kind: str, item_id: str) -> dict[str, Any]:
    if kind == "automation":
        return await hass.async_add_executor_job(get_automation_sync, hass, item_id)
    file, _, intent = item_id.rpartition("#")
    return await hass.async_add_executor_job(get_intent_sync, hass, language, file, intent)


async def async_update(hass: HomeAssistant, language: str, kind: str, item_id: str, fields: dict[str, Any]) -> None:
    async with _lock(hass):
        if kind == "automation":
            await hass.async_add_executor_job(update_automation_sync, hass, item_id, fields)
            if hass.services.has_service("automation", "reload"):
                await hass.services.async_call("automation", "reload", blocking=True)
            return
        file, _, intent = item_id.rpartition("#")
        await hass.async_add_executor_job(update_intent_sync, hass, language, file, intent, fields)
    await recognizer.async_reload(hass, language)
