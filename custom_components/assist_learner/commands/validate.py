"""Deterministic validation of generated or hand-edited commands (nothing from the LLM is trusted)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hassil import Intents, recognize_all
from hassil.parse_expression import parse_sentence
from hassil.sample import sample_sentence
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template

from .. import recognizer
from ..const import CONF_MAX_SENTENCES
from ..options import get_options
from ..sentences import COMMAND_INTENT, MUSIC_INTENT
from .actions import async_validate_actions
from .model import DEFAULT_CONFIRMATION, TYPES
from .template_guard import validate_template

PROBES = [
    "turn on the kitchen lights",
    "turn off the living room light",
    "set a timer for five minutes",
    "what time is it",
    "play some music",
    "set the thermostat to twenty degrees",
]
_SLOT_RE = re.compile(r"\{(\w+)\}")
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_SAMPLES = 20


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    collisions: list[dict[str, str]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    preview: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "flags": self.flags,
            "collisions": self.collisions,
            "calls": self.calls,
            "preview": self.preview,
        }


def _slot_samples(slots: dict[str, dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, s in slots.items():
        out[name] = str(int(s["min"])) if s["type"] == "number" else str(s["values"][0])
    return out


def _check_slots(slots: Any, rep: Report) -> dict[str, dict[str, Any]]:
    clean: dict[str, dict[str, Any]] = {}
    if not isinstance(slots, dict):
        rep.errors.append("slots must be an object")
        return clean
    for name, s in slots.items():
        if not _IDENT.match(name):
            rep.errors.append(f"slot name '{name}' must be lowercase letters, digits and underscores")
            continue
        if not isinstance(s, dict) or s.get("type") not in ("number", "choice"):
            rep.errors.append(f"slot '{name}' must have type 'number' or 'choice' (free-text slots are not allowed)")
            continue
        if s["type"] == "number":
            try:
                lo, hi, step = float(s["min"]), float(s["max"]), float(s.get("step", 1))
            except (KeyError, TypeError, ValueError):
                rep.errors.append(f"number slot '{name}' needs numeric min and max")
                continue
            if not lo < hi or step <= 0:
                rep.errors.append(f"number slot '{name}' needs min < max and step > 0")
                continue
            clean[name] = {"type": "number", "min": lo, "max": hi, "step": step}
        else:
            vals = s.get("values")
            if not isinstance(vals, list) or not vals or not all(isinstance(v, str) and v for v in vals):
                rep.errors.append(f"choice slot '{name}' needs a non-empty list of values")
                continue
            clean[name] = {"type": "choice", "values": vals}
    return clean


def _hassil_lists(slots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lists: dict[str, Any] = {}
    for name, s in slots.items():
        if s["type"] == "number":
            lists[name] = {"range": {"from": int(s["min"]), "to": int(s["max"]), "step": int(s["step"])}}
        else:
            lists[name] = {"values": list(s["values"])}
    return lists


def would_match(sentences: list[str], slots: dict[str, dict[str, Any]], text: str, language: str) -> bool:
    """Would these sentences (alone) recognise `text`? Used for greediness probes."""
    intents = Intents.from_dict(
        {
            "language": language,
            "intents": {"Probe": {"data": [{"sentences": sentences}]}},
            "lists": _hassil_lists(slots),
        }
    )
    return any(True for _ in recognize_all(text, intents))


async def async_validate_command(
    hass: HomeAssistant, entry, cmd: dict[str, Any], *, language: str
) -> Report:
    rep = Report()
    opts = get_options(entry)
    ctype = cmd.get("type")
    if ctype not in TYPES:
        rep.errors.append(f"type must be one of {TYPES}")
        return rep
    entities: dict[str, str] = cmd.get("entities") or {}
    for role, ent in entities.items():
        if hass.states.get(ent) is None:
            rep.errors.append(f"entity {ent} ({role}) does not exist")
    slots = _check_slots(cmd.get("slots") or {}, rep)
    if ctype == "answer" and cmd.get("actions"):
        rep.errors.append("an answer command cannot have actions")

    # --- sentences ------------------------------------------------------------------------------------
    sentences = [s.strip() for s in cmd.get("sentences") or [] if s and s.strip()]
    max_s = opts[CONF_MAX_SENTENCES]
    if len(sentences) < 2:
        rep.errors.append("at least 2 sentences are required")
    if len(sentences) > max_s:
        rep.errors.append(f"at most {max_s} sentences are allowed")
    parsed_ok: list[str] = []
    for s in sentences:
        refs = set(_SLOT_RE.findall(s))
        unknown = refs - set(slots)
        if unknown:
            rep.errors.append(f"sentence '{s}' uses undefined slot(s) {sorted(unknown)} (wildcards are not allowed)")
            continue
        try:
            parse_sentence(_SLOT_RE.sub("x", s))
        except Exception as err:
            rep.errors.append(f"sentence '{s}' does not parse: {err}")
            continue
        parsed_ok.append(s)
    if len(set(sentences)) != len(sentences):
        rep.warnings.append("duplicate sentences")

    samples = _slot_samples(slots)
    if parsed_ok and not rep.errors:
        # greediness probes: these sentences alone must not match unrelated commands
        for probe in PROBES:
            if would_match(parsed_ok, slots, probe, language):
                rep.errors.append(f"too greedy: matches the unrelated request '{probe}'")
                break
        # collision with built-ins / other custom intents
        seen: set[str] = set()
        for s in parsed_ok:
            filled = _SLOT_RE.sub(lambda m: samples[m.group(1)], s)
            for text in _expand(filled):
                if text in seen:
                    continue
                seen.add(text)
                m = await recognizer.async_match(hass, text, language)
                if m is None:
                    continue
                own = m.intent_name == COMMAND_INTENT and m.slots.get("command_id") == cmd.get("id")
                if own:
                    continue
                what = (
                    "another Assist Learner item"
                    if m.intent_name in (COMMAND_INTENT, MUSIC_INTENT)
                    else f"the existing intent {m.intent_name}"
                )
                rep.collisions.append({"sentence": text, "matches": m.intent_name})
                if not cmd.get("accept_collisions"):
                    rep.errors.append(f"'{text}' already matches {what}")
                    break
            if rep.collisions and not cmd.get("accept_collisions"):
                break
        if rep.collisions and cmd.get("accept_collisions"):
            rep.warnings.append("collisions accepted: priority between built-in and custom sentences applies")

    # --- templates ----------------------------------------------------------------------------------
    allowed = set(entities.values())
    template = cmd.get("response_template") or ""
    if ctype in ("answer", "answer_and_action") and not template.strip():
        rep.errors.append("a response template is required")
    texts = {"response_template": template, "unavailable_template": cmd.get("unavailable_template") or ""}
    guarded = True
    for key, src in texts.items():
        if not src.strip():
            continue
        g = validate_template(src, allowed, set(slots))
        if not g.ok:
            guarded = False
            rep.errors.extend(f"{key}: {e}" for e in g.errors)
    if guarded and template.strip():
        rep.preview = await _render_checks(hass, rep, template, texts["unavailable_template"], allowed, slots, samples)

    # --- actions ------------------------------------------------------------------------------------
    if ctype in ("action", "answer_and_action"):
        ar = await async_validate_actions(hass, cmd.get("actions") or [], entities, slots)
        rep.errors.extend(ar.errors)
        rep.flags.extend(ar.flags)
        rep.calls = ar.calls
        if ctype == "action" and not template.strip():
            rep.preview = DEFAULT_CONFIRMATION
    return rep


def _expand(text: str) -> list[str]:
    try:
        sentence = parse_sentence(text)
        return list(_limit(sample_sentence(sentence), MAX_SAMPLES))
    except Exception:
        return []


def _limit(it, n):
    for i, x in enumerate(it):
        if i >= n:
            return
        yield x


async def _render_checks(hass, rep, template, unavailable_template, allowed, slots, samples) -> str | None:
    """Render with live values, then with every entity unavailable (fallback check)."""
    values = {k: (float(v) if slots[k]["type"] == "number" else v) for k, v in samples.items()}
    try:
        live = Template(template, hass).async_render({"slots": values}, parse_result=False).strip()
    except Exception as err:
        rep.errors.append(f"response template failed to render: {err}")
        return None
    if not live:
        rep.errors.append("response template rendered empty text")

    def _unavailable_states(entity_id=None, *a, **k):
        return "unavailable"

    shadow = {
        "slots": values,
        "states": _unavailable_states,
        "state_attr": lambda *a, **k: None,
        "is_state": lambda *a, **k: False,
        "is_state_attr": lambda *a, **k: False,
    }
    unavailable_src = unavailable_template.strip()
    # With an unavailable_template the runtime swaps to it when any entity is unavailable, so only that
    # must render; without one the main template itself has to survive unavailable entities.
    to_check = (("unavailable template", unavailable_template),) if unavailable_src else (
        ("response template", template),
    )
    for label, src in to_check:
        try:
            out = Template(src, hass).async_render(shadow, parse_result=False).strip()
        except Exception as err:
            hint = "" if unavailable_src else " (add an unavailable_template or use | default filters)"
            rep.errors.append(f"{label} failed to render with unavailable entities: {err}{hint}")
            continue
        if not out:
            rep.errors.append(f"{label} rendered empty text with unavailable entities")
    return live or None
