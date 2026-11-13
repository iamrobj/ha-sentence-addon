"""run_proposer action."""

from __future__ import annotations

from tests.test_proposer_run import _entry, _miss


async def test_run_proposer_service(hass):
    entry = await _entry(hass)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    await hass.services.async_call("assist_learner", "run_proposer", {}, blocking=True)
    assert len(store.proposals.values()) == 1
    assert hass.states.get("sensor.assist_learner_pending_proposals").state == "1"
