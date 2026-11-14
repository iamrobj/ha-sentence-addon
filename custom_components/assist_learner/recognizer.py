"""Single adapter around Home Assistant's internal default-agent recognizer (plan risk 3).

Everything that asks "would this sentence already match locally?" goes through here, so a core API change
only needs fixing in one place. The adapter test in tests/test_recognizer.py fails loudly on upgrade.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant


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
