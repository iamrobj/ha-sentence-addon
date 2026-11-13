"""Discover external sentence files / sentence-trigger automations and import simple ones.

Import never edits or deletes the original files. Anything that can't be mapped cleanly stays external with a
reason. Imported commands are created as *pending* and go through exactly the same validation as authored ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml

from .model import new_command

OWN_FILE = "assist_learner.yaml"
SIMPLE_CALL_KEYS = {"service", "action", "target", "data", "alias", "metadata", "enabled"}
UNMAPPABLE_STEPS = {"choose", "repeat", "wait_template", "wait_for_trigger", "delay", "if", "parallel", "sequence", "variables", "event", "stop", "condition"}
_VAR_ONLY = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")
_ENTITY_IN_TEMPLATE = re.compile(r"""\b(?:states|state_attr|is_state|is_state_attr)\(\s*['"]([a-z_]+\.[a-z0-9_]+)['"]""")


@dataclass
class ExternalIntent:
    name: str
    file: str
    sentences: list[str]
    slots: dict[str, Any] = field(default_factory=dict)  # data[].slots merged (fixed slots)
    lists: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate:
    intent: ExternalIntent
    command: dict[str, Any] | None = None
    unmappable: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.name,
            "file": self.intent.file,
            "sentences": self.intent.sentences,
            "unmappable": self.unmappable,
            "command": self.command,
        }


def _load(path: Path) -> Any:
    try:
        return load_yaml(str(path))
    except Exception:
        return None


def _read_sentence_files(hass: HomeAssistant, language: str) -> list[ExternalIntent]:
    root = Path(hass.config.path("custom_sentences", language))
    out: list[ExternalIntent] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.yaml")):
        if path.name == OWN_FILE:
            continue
        doc = _load(path)
        if not isinstance(doc, dict):
            continue
        lists = doc.get("lists") or {}
        for name, body in (doc.get("intents") or {}).items():
            sentences: list[str] = []
            fixed: dict[str, Any] = {}
            for block in (body or {}).get("data") or []:
                sentences += [str(s) for s in block.get("sentences") or []]
                fixed.update(block.get("slots") or {})
            out.append(ExternalIntent(name, str(path), sentences, fixed, lists))
    return out


def _read_intent_scripts(hass: HomeAssistant) -> dict[str, Any]:
    doc = _load(Path(hass.config.path("configuration.yaml")))
    scripts = (doc or {}).get("intent_script") if isinstance(doc, dict) else None
    return scripts if isinstance(scripts, dict) else {}


def _sentence_trigger_automations(hass: HomeAssistant) -> list[dict[str, Any]]:
    doc = _load(Path(hass.config.path("automations.yaml")))
    found: list[dict[str, Any]] = []
    for auto in doc if isinstance(doc, list) else []:
        triggers = auto.get("triggers") or auto.get("trigger") or []
        if isinstance(triggers, dict):
            triggers = [triggers]
        for t in triggers:
            if (t.get("trigger") or t.get("platform")) == "conversation":
                cmds = t.get("command") or []
                found.append(
                    {
                        "kind": "automation",
                        "id": auto.get("id"),
                        "alias": auto.get("alias"),
                        "sentences": [cmds] if isinstance(cmds, str) else list(cmds),
                    }
                )
    return found


def discover_sync(hass: HomeAssistant, language: str) -> dict[str, Any]:
    """Read-only view of everything external that we don't manage."""
    return {
        "files": [
            {
                "kind": "file",
                "intent": i.name,
                "file": i.file,
                "sentences": i.sentences,
                "importable": True,
            }
            for i in _read_sentence_files(hass, language)
        ],
        "automations": [
            {**a, "importable": False, "reason": "owned by an automation; importing would fire twice"}
            for a in _sentence_trigger_automations(hass)
        ],
    }


# -------------------------------------------------------------------------- conversion ------------------


def _slot_defs(intent: ExternalIntent, referenced: set[str]) -> tuple[dict[str, Any], str | None]:
    slots: dict[str, Any] = {}
    for name in referenced:
        lst = intent.lists.get(name)
        if not isinstance(lst, dict):
            return {}, f"slot '{name}' has no list definition"
        if lst.get("wildcard"):
            return {}, f"slot '{name}' is a free-text wildcard (not supported for commands)"
        if "range" in lst:
            r = lst["range"]
            slots[name] = {
                "type": "number",
                "min": r.get("from", 0),
                "max": r.get("to", 100),
                "step": r.get("step", 1),
            }
        elif "values" in lst:
            vals: list[str] = []
            for v in lst["values"]:
                if isinstance(v, dict):
                    if v.get("out") not in (None, v.get("in")):
                        return {}, f"slot '{name}' maps spoken values to different outputs"
                    vals.append(str(v["in"]))
                else:
                    vals.append(str(v))
            slots[name] = {"type": "choice", "values": vals}
        else:
            return {}, f"slot '{name}' has an unsupported list type"
    return slots, None


def _convert_action(step: Any, slots: dict[str, Any], roles: dict[str, str]) -> tuple[dict | None, str | None]:
    if not isinstance(step, dict):
        return None, "action step is not a service call"
    bad = set(step) & UNMAPPABLE_STEPS
    if bad:
        return None, f"uses '{sorted(bad)[0]}', which can't be mapped"
    extra = set(step) - SIMPLE_CALL_KEYS
    if extra:
        return None, f"has unsupported keys {sorted(extra)}"
    service = step.get("service") or step.get("action")
    if not isinstance(service, str) or "{{" in service or "." not in service:
        return None, "service name is missing or templated"
    target = step.get("target") or {}
    ents = target.get("entity_id") or (step.get("data") or {}).get("entity_id")
    if isinstance(ents, str):
        ents = [e.strip() for e in ents.split(",")]
    if not ents or not all(isinstance(e, str) and "{{" not in e for e in ents):
        return None, "targets are missing or templated"
    if set(target) - {"entity_id"}:
        return None, "targets other than entity_id (area/device) can't be mapped"
    targets = [_role(e, roles) for e in ents]
    data: dict[str, Any] = {}
    for k, v in (step.get("data") or {}).items():
        if k == "entity_id":
            continue
        if isinstance(v, str) and "{{" in v:
            m = _VAR_ONLY.match(v.strip())
            if not m or m.group(1) not in slots:
                return None, f"data '{k}' uses a template that is not a plain slot reference"
            data[k] = "{" + m.group(1) + "}"
        elif isinstance(v, (str, int, float, bool)):
            data[k] = v
        else:
            return None, f"data '{k}' is not a literal"
    return {"service": service, "targets": targets, "data": data}, None


def _role(entity_id: str, roles: dict[str, str]) -> str:
    for role, ent in roles.items():
        if ent == entity_id:
            return role
    base = entity_id.split(".", 1)[1]
    role, n = base, 2
    while role in roles:
        role, n = f"{base}_{n}", n + 1
    roles[role] = entity_id
    return role


def _convert_speech(text: str, slots: dict[str, Any], roles: dict[str, str]) -> str:
    """intent_script exposes slots as bare variables; our templates use `slots.<name>`."""

    def fix(block: re.Match) -> str:
        inner = block.group(0)
        for name in slots:
            inner = re.sub(rf"(?<![\w.'\"]){re.escape(name)}(?![\w'\"(])", f"slots.{name}", inner)
        return inner

    out = re.sub(r"\{\{.*?\}\}", fix, text, flags=re.S)
    for ent in _ENTITY_IN_TEMPLATE.findall(out):
        _role(ent, roles)
    return out


def convert(intent: ExternalIntent, script: dict[str, Any] | None) -> Candidate:
    cand = Candidate(intent)
    if script is None:
        cand.unmappable = "no matching intent_script entry in configuration.yaml"
        return cand
    referenced: set[str] = set()
    for s in intent.sentences:
        referenced |= set(re.findall(r"\{(\w+)\}", s))
    slots, err = _slot_defs(intent, referenced)
    if err:
        cand.unmappable = err
        return cand
    roles: dict[str, str] = {}
    actions: list[dict[str, Any]] = []
    steps = script.get("action") or []
    if isinstance(steps, dict):
        steps = [steps]
    for i, step in enumerate(steps, 1):
        converted, err = _convert_action(step, slots, roles)
        if err:
            cand.unmappable = f"action {i}: {err}"
            return cand
        actions.append(converted)
    speech = (script.get("speech") or {}).get("text")
    if speech is None and not actions:
        cand.unmappable = "intent has neither actions nor speech"
        return cand
    template = _convert_speech(str(speech), slots, roles) if speech else ""
    ctype = "answer_and_action" if (actions and template) else ("action" if actions else "answer")
    cmd = new_command(
        name=f"Imported: {intent.name}",
        description=f"Imported from {Path(intent.file).name} ({intent.name})",
        entities=roles,
        cmd_type=ctype,
    )
    cmd.update(
        sentences=intent.sentences,
        slots=slots,
        actions=actions,
        response_template=template,
        status="pending",
        imported_from={"file": intent.file, "intent": intent.name},
    )
    cand.command = cmd
    return cand


def preview_sync(hass: HomeAssistant, language: str) -> list[Candidate]:
    scripts = _read_intent_scripts(hass)
    return [convert(i, scripts.get(i.name)) for i in _read_sentence_files(hass, language)]
