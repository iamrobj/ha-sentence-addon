"""Optional LLM generalisation of a proposed template (off by default; output is only a *suggestion*)."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import CONF_AI_TASK, CONF_CARRIER_VERBS, CONF_LANGUAGE, CONF_LLM_GENERALISE
from ..options import get_options
from .validate import async_check_template

_LOGGER = logging.getLogger(__name__)
_SLOTS = re.compile(r"\{(\w+)\}")

STRUCTURE = {
    "template": {
        "description": "The improved Home Assistant sentence template",
        "required": True,
        "selector": {"text": {}},
    }
}


async def async_suggest(hass: HomeAssistant, entry, template: str, samples: dict[str, str]) -> str | None:
    """Ask the AI Task entity for a richer template; accept it only if it passes every safety check."""
    opts = get_options(entry)
    if not opts[CONF_LLM_GENERALISE] or not opts[CONF_AI_TASK]:
        return None
    instructions = (
        "Improve this Home Assistant voice sentence template by adding natural synonym alternations "
        "like (play|put on) and optional words like [the] or [please]. Keep exactly the same slots "
        f"({', '.join(sorted(set(_SLOTS.findall(template))))}); do not add slots or wildcards; keep the "
        f"meaning identical. Template: {template}"
    )
    try:
        result = await hass.services.async_call(
            "ai_task",
            "generate_data",
            {"task_name": "assist_learner: generalise", "instructions": instructions,
             "structure": STRUCTURE, "entity_id": opts[CONF_AI_TASK]},
            blocking=True,
            return_response=True,
        )
        suggestion: Any = ((result or {}).get("data") or {}).get("template")
    except Exception as err:
        _LOGGER.debug("LLM generalisation failed: %s", err)
        return None
    if not isinstance(suggestion, str):
        return None
    suggestion = " ".join(suggestion.split())
    if suggestion == template or set(_SLOTS.findall(suggestion)) != set(_SLOTS.findall(template)):
        return None
    check = await async_check_template(
        hass, suggestion, samples, entry.data[CONF_LANGUAGE], opts[CONF_CARRIER_VERBS]
    )
    return suggestion if check.ok else None
