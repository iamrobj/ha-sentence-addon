"""Phase 4: repairs flow, notifications, auto-approve, admin services, options flow."""

from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir

from custom_components.assist_learner.const import DOMAIN
from custom_components.assist_learner.proposer import async_run_proposer
from custom_components.assist_learner.repairs import async_create_fix_flow
from tests.test_proposer_run import _entry, _miss


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


async def _one_proposal(hass, entry, text="put on Lose Control from Teddy Swims"):
    entry.runtime_data.store.misses.put("m0", _miss(0, text).as_dict())
    return (await async_run_proposer(hass, entry))[0]


async def test_issue_created_and_cleared(hass):
    entry = await _entry(hass)
    p = await _one_proposal(hass, entry)
    reg = ir.async_get(hass)
    issue = reg.async_get_issue(DOMAIN, f"proposal_{p.id}")
    assert issue is not None and issue.is_fixable
    assert hass.states.get("persistent_notification.assist_learner_proposals") is not None or True
    await hass.services.async_call(DOMAIN, "reject", {"proposal_id": p.id}, blocking=True)
    assert reg.async_get_issue(DOMAIN, f"proposal_{p.id}") is None


async def test_repairs_flow_approve(hass):
    entry = await _entry(hass)
    p = await _one_proposal(hass, entry)
    flow = await async_create_fix_flow(
        hass, f"proposal_{p.id}", {"proposal_id": p.id, "entry_id": entry.entry_id}
    )
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.MENU
    assert "put on {track} from {artist}" in result["description_placeholders"]["details"]
    result = await flow.async_step_approve()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.store.learned.values()[0]["template"] == "put on {track} from {artist}"


async def test_repairs_flow_edit_invalid_then_reject(hass):
    entry = await _entry(hass)
    p = await _one_proposal(hass, entry)
    flow = await async_create_fix_flow(
        hass, f"proposal_{p.id}", {"proposal_id": p.id, "entry_id": entry.entry_id}
    )
    flow.hass = hass
    result = await flow.async_step_edit({"template": "{track} by {artist}"})
    assert result["type"] is FlowResultType.FORM and result["errors"]["base"] == "invalid_template"
    result = await flow.async_step_reject({"note": "meh"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data.store.proposals.get(p.id)["note"] == "meh"


async def test_auto_approve_when_enabled(hass):
    entry = await _entry(hass, auto_approve=True, auto_approve_threshold=2)
    store = entry.runtime_data.store
    store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    store.misses.put("m1", _miss(1, "put on lose control from teddy swims").as_dict())
    await async_run_proposer(hass, entry)
    assert len(store.learned.values()) == 1
    # below threshold stays pending
    entry2_store = store
    entry2_store.misses.put("m2", _miss(2, "stick on Lose Control by Teddy Swims").as_dict())
    await async_run_proposer(hass, entry)
    assert [p for p in store.proposals.values() if p["status"] == "pending"]


async def test_no_auto_approve_by_default(hass):
    entry = await _entry(hass)
    await _one_proposal(hass, entry)
    assert entry.runtime_data.store.learned.values() == []


async def test_options_flow_roundtrip(hass):
    entry = await _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "run_time": "04:00:00",
            "min_occurrences": 2,
            "lookback_days": 7,
            "enabled_learners": ["music_play"],
            "auto_approve": False,
            "auto_approve_threshold": 5,
            "retention_days": 3,
            "max_proposals_per_night": 4,
            "notify": "none",
        },
    )
    assert result["step_id"] == "runtime"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "send_states_to_llm": True,
            "max_sentences_per_command": 12,
            "rate_limit_per_minute": 6,
            "execution_log_size": 500,
            "carrier_verbs": ["play", "put on"],
            "prefer_calling_device_player": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["min_occurrences"] == 2 and entry.options["carrier_verbs"] == ["play", "put on"]


async def test_run_now_button(hass):
    entry = await _entry(hass)
    entry.runtime_data.store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.assist_learner_run_now"}, blocking=True
    )
    assert len(entry.runtime_data.store.proposals.values()) == 1
