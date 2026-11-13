"""Phase 5 backend: websocket API (admin only) covering the panel's needs."""

from __future__ import annotations

import pytest

from tests.test_commands import GOOD_ACTION, GOOD_ANSWER, _setup
from tests.test_proposer_run import _miss


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


async def call(client, type_, **kw):
    await client.send_json_auto_id({"type": f"assist_learner/{type_}", **kw})
    return await client.receive_json()


async def test_non_admin_rejected(hass, hass_ws_client, hass_read_only_access_token):
    await _setup(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    resp = await call(client, "overview")
    assert resp["success"] is False and resp["error"]["code"] == "unauthorized"


async def test_learning_flow_over_websocket(hass, hass_ws_client):
    entry, mgr, ai = await _setup(hass)
    client = await hass_ws_client(hass)
    entry.runtime_data.store.misses.put("m0", _miss(0, "put on Lose Control from Teddy Swims").as_dict())

    assert (await call(client, "run_proposer"))["result"] == {"created": 1}
    pending = (await call(client, "proposals/list"))["result"]
    assert pending[0]["template"] == "put on {track} from {artist}"
    assert (await call(client, "overview"))["result"]["pending_proposals"] == 1

    row = (await call(client, "proposals/decide", proposal_id=pending[0]["id"], decision="approve"))["result"]
    sents = (await call(client, "sentences/list", source="learned"))["result"]
    assert sents[0]["title"] == "put on {track} from {artist}" and sents[0]["editable"]

    # edit with history + revert
    upd = (await call(client, "learned/update", item_id=row["id"], template="(play|put on) {track} from {artist}"))["result"]
    assert upd["versions"] == 1
    hist = (await call(client, "learned/history", item_id=row["id"]))["result"]
    assert hist[0]["template"] == "put on {track} from {artist}"
    rev = (await call(client, "learned/revert", item_id=row["id"], index=0))["result"]
    assert rev["template"] == "put on {track} from {artist}"

    # test box: nothing executes, shows what would match
    t = (await call(client, "sentences/test", text="put on despacito from luis fonsi"))["result"]
    assert t["matched"] and t["source"] == "learned" and t["id"] == row["id"]
    assert (await call(client, "sentences/test", text="gibberish words here"))["result"] == {"matched": False}

    await call(client, "learned/update", item_id=row["id"], enabled=False)
    assert (await call(client, "sentences/list", source="learned"))["result"][0]["enabled"] is False
    assert (await call(client, "learned/delete", item_id=row["id"]))["success"]


async def test_custom_command_flow_over_websocket(hass, hass_ws_client):
    entry, mgr, ai = await _setup(hass)
    client = await hass_ws_client(hass)
    ai.results += [GOOD_ANSWER, {**GOOD_ANSWER, "response_template": "Outside: {{ states('sensor.outdoor_temperature') | int }}."}]

    created = (await call(client, "commands/create", name="Outside", description="how warm outside",
                          entities={"outdoor_temp": "sensor.outdoor_temperature"}))["result"]
    cid = created["id"]
    payload = (await call(client, "commands/payload", item_id=cid))["result"]
    assert "sensor.outdoor_temperature" in payload["instructions"]

    gen = (await call(client, "commands/generate", item_id=cid))["result"]
    assert gen["validation"]["ok"] and gen["validation"]["preview"] == "It's 7 degrees outside."
    regen = (await call(client, "commands/regenerate", item_id=cid, feedback="shorter"))["result"]
    assert regen["response_template"].startswith("Outside")

    bad = await call(client, "commands/update", item_id=cid, fields={"hash": "x"})
    assert bad["success"] is False and "Cannot edit" in bad["error"]["message"]

    ok = (await call(client, "commands/decide", item_id=cid, decision="approve"))["result"]
    assert ok["status"] == "active"
    listed = (await call(client, "sentences/list", source="custom"))["result"]
    assert listed[0]["title"] == "Outside" and listed[0]["enabled"]
    t = (await call(client, "sentences/test", text="how warm is it outside"))["result"]
    assert t["source"] == "custom" and t["id"] == cid

    hist = (await call(client, "commands/history", item_id=cid))["result"]
    assert len(hist) >= 2
    rv = (await call(client, "commands/revert", item_id=cid, index=1))["result"]
    assert rv["response_template"] == GOOD_ANSWER["response_template"]
    assert (await call(client, "commands/decide", item_id=cid, decision="disable"))["result"]["status"] == "disabled"
    assert (await call(client, "commands/delete", item_id=cid))["success"]


async def test_action_run_once_and_executions(hass, hass_ws_client):
    from pytest_homeassistant_custom_component.common import async_mock_service

    entry, mgr, ai = await _setup(hass)
    client = await hass_ws_client(hass)
    calls = async_mock_service(hass, "light", "turn_on")
    ai.results.append(GOOD_ACTION)
    cid = (await call(client, "commands/create", name="Dim", description="dim lounge", cmd_type="action",
                      entities={"lounge": "light.lounge"}))["result"]["id"]
    await call(client, "commands/generate", item_id=cid)
    await call(client, "commands/decide", item_id=cid, decision="approve")
    run = (await call(client, "commands/run_once", item_id=cid, values={"level": 55}))["result"]
    assert run["ok"] and calls[0].data["brightness_pct"] == 55
    execs = (await call(client, "executions/list"))["result"]
    assert execs[0]["source"] == "run_once"
    stats = (await call(client, "stats"))["result"]
    assert stats["commands"][0]["id"] == cid


async def test_misses_and_import_endpoints(hass, hass_ws_client):
    entry, mgr, ai = await _setup(hass)
    client = await hass_ws_client(hass)
    entry.runtime_data.store.misses.put("m0", _miss(0, "play something").as_dict())
    misses = (await call(client, "misses/list"))["result"]
    assert misses[0]["text"] == "play something"
    assert (await call(client, "import/discover"))["result"] == {"files": [], "automations": []}
    assert (await call(client, "import/preview"))["result"] == []
    r = await call(client, "import/apply", intents=["Nope"])
    assert r["success"] and r["result"] == []

