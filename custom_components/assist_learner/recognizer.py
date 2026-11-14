"""Single adapter around Home Assistant's internal default-agent recognizer (plan risk 3).

Everything that asks "would this sentence already match locally?" goes through here, so a core API change
only needs fixing in one place. The adapter test in tests/test_recognizer.py fails loudly on upgrade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import language as language_util


def sentences_language(language: str) -> str:
    """The `custom_sentences/<dir>` HA actually loads for this language.

    HA picks a supported language *variant* first (`en-GB` -> `en`, `de-DE` -> `de`) and only reads
    `custom_sentences/<variant>/`. Reading or writing `custom_sentences/<raw language>/` silently misses files.
    """
    from home_assistant_intents import get_languages

    matches = language_util.matches(language, set(get_languages()))
    return matches[0] if matches else language


def config_intents(hass: HomeAssistant) -> dict[str, Any]:
    """Intents defined under `conversation: intents:` (configuration.yaml or packages), as HA loaded them.

    Private HA state (agent manager); isolated here. Returns {} if the internals change.
    """
    try:
        from homeassistant.components.conversation.agent_manager import get_agent_manager
        from homeassistant.components.conversation.const import IntentSource

        intents = get_agent_manager(hass)._intents[IntentSource.CONFIG]["intents"]  # noqa: SLF001
    except Exception:  # internals moved: degrade to "none found"
        return {}
    return intents if isinstance(intents, dict) else {}


def automation_configs(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Raw configs of every loaded automation (automations.yaml, packages, includes), with entity info."""
    try:
        from homeassistant.components.automation import DATA_COMPONENT
    except ImportError:
        return []
    component = hass.data.get(DATA_COMPONENT)
    out = []
    for ent in component.entities if component else []:
        raw = getattr(ent, "raw_config", None)
        if isinstance(raw, dict):
            out.append({"config": raw, "entity_id": ent.entity_id, "name": ent.name, "unique_id": ent.unique_id})
    return out


@dataclass
class Match:
    intent_name: str
    slots: dict[str, str]


def _input(text: str, language: str) -> conversation.ConversationInput:
    return conversation.ConversationInput(
        text=text,
        context=Context(),
        conversation_id=None,
        device_id=None,
        satellite_id=None,
        language=language,
        agent_id=conversation.HOME_ASSISTANT_AGENT,
    )


@dataclass
class TriggerMatch:
    template: str | None
    slots: dict[str, str]


async def async_match_trigger(hass: HomeAssistant, text: str, language: str) -> TriggerMatch | None:
    """Would an automation's sentence trigger catch this text? Matching only; nothing is fired.

    The voice pipeline runs sentence triggers BEFORE any non-default conversation agent (including ours), so
    a trigger match never reaches Assist Learner or the LLM.
    """
    agent = conversation.async_get_agent(hass)
    if agent is None or not hasattr(agent, "async_recognize_sentence_trigger"):
        return None
    result = await agent.async_recognize_sentence_trigger(_input(text, language))
    if result is None:
        return None
    first = next(iter(result.matched_triggers.values()))
    slots = {name: str(s.value) for name, s in first.entities.items()}
    return TriggerMatch(template=result.sentence_template, slots=slots)


async def async_match(hass: HomeAssistant, text: str, language: str) -> Match | None:
    """Return the intent the built-in recognizer (incl. custom sentences) would match, or None."""
    agent = conversation.async_get_agent(hass)
    if agent is None or not hasattr(agent, "async_recognize_intent"):
        return None
    result = await agent.async_recognize_intent(_input(text, language))
    if result is None or result.unmatched_entities:
        # HA returns partial matches (unmatched entities) for error messaging; not a real match
        return None
    slots = {name: str(s.value) for name, s in result.entities.items()}
    return Match(intent_name=result.intent.name, slots=slots)


async def async_reload(hass: HomeAssistant, language: str | None = None) -> None:
    """Reload custom sentences without restart (`conversation.reload`)."""
    data = {"language": language} if language else {}
    await hass.services.async_call("conversation", "reload", data, blocking=True)
