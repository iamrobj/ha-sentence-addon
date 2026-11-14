"""LLM generation through the linked AI Task entity (structured output)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_get_all_descriptions

from ..const import CONF_AI_TASK, CONF_MAX_SENTENCES, CONF_SEND_STATES
from ..options import get_options
from .actions import ALLOWED_SERVICES, BLOCKED_DOMAINS

SYNTAX_RULES = """Sentence syntax (Home Assistant hassil):
- Alternatives: (what's|what is). Optional words: [the]. Slots: {slot_name}.
- Use ONLY the slots you define. NEVER use free-text wildcards.
- Number slots: {"type":"number","min":0,"max":100,"step":1}. Choice slots: {"type":"choice","values":["a","b"]}.
- Write natural, casual spoken phrasings. Lowercase, no punctuation except syntax characters."""

TEMPLATE_RULES = """Response template (Jinja):
- You may ONLY read the listed entities, using literal ids: states('sensor.x'), state_attr('sensor.x','attr'),
  is_state(...), is_state_attr(...). Filters allowed: round, float, int, default, title, lower, upper, abs.
- Reference slot values as {{ slots.name }}. No loops, no variables, no other functions, no service calls.
- Keep it short and speakable. Provide unavailable_template for when a sensor can't be read."""

STRUCTURE: dict[str, Any] = {
    "type": {"description": "answer, action or answer_and_action", "required": True, "selector": {"text": {}}},
    "sentences": {
        "description": "Spoken sentences that trigger the command",
        "required": True,
        "selector": {"text": {"multiple": True}},
    },
    # slots/actions are JSON *text*: an `object` selector without declared fields is converted by real AI Task
    # providers into a schema that only allows an empty object (seen with the Anthropic AI Task entity).
    "slots": {
        "description": 'Slot definitions as a JSON object string, e.g. {"level":{"type":"number","min":0,"max":100,"step":1}}. Use "{}" for none.',
        "required": False,
        "selector": {"text": {"multiline": True}},
    },
    "response_template": {"description": "Jinja spoken response", "required": True, "selector": {"text": {}}},
    "unavailable_template": {"description": "Spoken response when entities are unavailable", "required": False, "selector": {"text": {}}},
    "actions": {
        "description": 'Ordered service calls as a JSON array string, e.g. [{"service":"light.turn_on","targets":["lounge"],"data":{"brightness_pct":"{level}"}}]. Use "[]" for none.',
        "required": False,
        "selector": {"text": {"multiline": True}},
    },
    "notes": {"description": "Short explanation of your choices", "required": False, "selector": {"text": {}}},
    "warnings": {"description": "Anything the user should double check", "required": False, "selector": {"text": {"multiple": True}}},
}


class GenerationError(HomeAssistantError):
    """LLM unavailable or returned unusable output."""


def _entity_info(hass: HomeAssistant, roles: dict[str, str], send_states: bool) -> list[dict[str, Any]]:
    info = []
    for role, ent in roles.items():
        st = hass.states.get(ent)
        row: dict[str, Any] = {"role": role, "entity_id": ent, "domain": ent.split(".", 1)[0]}
        if st is not None:
            row["name"] = st.name
            for k in ("unit_of_measurement", "device_class"):
                if st.attributes.get(k):
                    row[k] = st.attributes[k]
            if send_states:
                row["state"] = st.state
        info.append(row)
    return info


async def async_build_payload(
    hass: HomeAssistant,
    entry,
    cmd: dict[str, Any],
    *,
    feedback: str | None = None,
    existing_sentences: list[str] | None = None,
) -> dict[str, Any]:
    """Everything that would be sent to the LLM. The panel shows this before each generation."""
    opts = get_options(entry)
    entities = _entity_info(hass, cmd["entities"], opts[CONF_SEND_STATES])
    domains = {e["domain"] for e in entities}
    services = ""
    if cmd["type"] in ("action", "answer_and_action"):
        descs = await async_get_all_descriptions(hass)
        lines = []
        for d in sorted(domains - BLOCKED_DOMAINS):
            for svc in sorted(ALLOWED_SERVICES.get(d, ())):
                fields = ((descs.get(d) or {}).get(svc) or {}).get("fields") or {}
                desc = ", ".join(f"{k} ({(v.get('description') or '')[:60]})" for k, v in fields.items() if k != "entity_id")
                lines.append(f"- {d}.{svc}: {desc}")
        services = "Allowed services (only these, only on the listed entities):\n" + "\n".join(lines)

    parts = [
        "You create a local Home Assistant voice command. Respond only with the structured result.",
        f"Command type wanted: {cmd['type']} (answer = speaks a response; action = calls services then confirms).",
        f"User's description: {cmd['description']}",
    ]
    if cmd.get("examples"):
        parts.append("Example phrasings from the user: " + "; ".join(cmd["examples"]))
    parts.append("Entities you may use (roles are labels): " + json.dumps(entities))
    parts += [SYNTAX_RULES, TEMPLATE_RULES]
    if services:
        parts.append(services)
    parts.append(f"Provide 2 to {opts[CONF_MAX_SENTENCES]} sentences. They must not collide with these existing ones: "
                 + json.dumps((existing_sentences or [])[:60]))
    if feedback:
        parts.append(f"Previous result: {json.dumps({k: cmd.get(k) for k in ('type', 'sentences', 'slots', 'response_template', 'unavailable_template', 'actions')})}")
        parts.append(f"User feedback to apply: {feedback}")
    return {
        "task_name": f"assist_learner: {cmd['name']}",
        "instructions": "\n\n".join(parts),
        "structure": STRUCTURE,
        "entity_id": opts[CONF_AI_TASK],
    }


def _as_obj(value: Any, problems: list[str] | None = None, name: str = "") -> Any:
    """Parse a JSON string field (tolerating ``` fences); report unparseable text instead of dropping it silently."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        if problems is not None:
            problems.append(f"The LLM returned {name} that isn't valid JSON, so they were ignored: {text[:200]}")
        return None


def normalise_result(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce the structured result into command fields; leaves validation to validate.py."""
    if not isinstance(data, dict):
        raise GenerationError("The LLM returned no structured data")
    sentences = data.get("sentences") or []
    if isinstance(sentences, str):
        sentences = [sentences]
    problems: list[str] = []
    slots = _as_obj(data.get("slots"), problems, "slots") or {}
    actions = _as_obj(data.get("actions"), problems, "actions") or []
    if isinstance(actions, dict):
        actions = [actions]
    return {
        "type": str(data.get("type") or "answer"),
        "sentences": [str(s) for s in sentences],
        "slots": slots if isinstance(slots, dict) else {},
        "actions": [a for a in actions if isinstance(a, dict)] if isinstance(actions, list) else [],
        "response_template": str(data.get("response_template") or ""),
        "unavailable_template": str(data.get("unavailable_template") or ""),
        "notes": str(data.get("notes") or ""),
        "warnings": problems + [str(w) for w in (data.get("warnings") or [])],
    }


async def async_generate(hass: HomeAssistant, entry, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("entity_id"):
        raise GenerationError("Choose an AI Task entity in the Assist Learner options first")
    try:
        result = await hass.services.async_call(
            "ai_task", "generate_data", payload, blocking=True, return_response=True
        )
    except Exception as err:
        raise GenerationError(f"AI Task call failed: {err}") from err
    return normalise_result((result or {}).get("data"))
