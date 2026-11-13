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


async def async_match(hass: HomeAssistant, text: str, language: str) -> Match | None:
    """Return the intent the built-in recognizer (incl. custom sentences) would match, or None."""
    agent = conversation.async_get_agent(hass)
    if agent is None or not hasattr(agent, "async_recognize_intent"):
        return None
    user_input = conversation.ConversationInput(
        text=text,
        context=Context(),
        conversation_id=None,
        device_id=None,
        satellite_id=None,
        language=language,
        agent_id=conversation.HOME_ASSISTANT_AGENT,
    )
    result = await agent.async_recognize_intent(user_input)
    if result is None or result.unmatched_entities:
        # HA returns partial matches (unmatched entities) for error messaging; not a real match
        return None
    slots = {name: str(s.value) for name, s in result.entities.items()}
    return Match(intent_name=result.intent.name, slots=slots)


async def async_reload(hass: HomeAssistant, language: str | None = None) -> None:
    """Reload custom sentences without restart (`conversation.reload`)."""
    data = {"language": language} if language else {}
    await hass.services.async_call("conversation", "reload", data, blocking=True)
