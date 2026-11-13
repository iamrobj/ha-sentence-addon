"""In-place editing of Home Assistant-owned sentences (automations.yaml + custom_sentences files)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.test_commands import _setup

AUTOMATIONS = """\
- id: '1700000000001'
  alias: Bedtime
  description: keep me
  triggers:
  - trigger: conversation
    command:
    - good night
  - trigger: conversation
    command: bed time
  - trigger: time
    at: '23:00:00'
  conditions: []
  actions:
  - action: light.turn_off
    target:
      entity_id: light.lounge
  mode: single
- alias: No id
  trigger:
    platform: conversation
    command: hello there
  action: []
"""

SENTENCE_FILE = """\
language: en
intents:
  SetScene:
    data:
    - sentences:
      - activate {scene}
      slots:
        mode: x
    - sentences:
      - scene {scene}
lists:
  scene:
    values: [movie, dinner]
"""


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "automations.yaml").write_text(AUTOMATIONS)
    d = tmp_path / "custom_sentences" / "en"
    d.mkdir(parents=True)
    (d / "mine.yaml").write_text(SENTENCE_FILE)


async def call(client, type_, **kw):
    await client.send_json_auto_id({"type": f"assist_learner/{type_}", **kw})
    return await client.receive_json()


async def test_rows_one_per_automation_and_editability(hass, hass_ws_client):
    await _setup(hass)
    client = await hass_ws_client(hass)
    rows = (await call(client, "sentences/list", source="external"))["result"]
    autos = [r for r in rows if r["kind"] == "automation"]
    assert [a["title"] for a in autos] == ["Bedtime", "No id"]
    assert autos[0]["sentences"] == ["good night", "bed time"] and autos[0]["external_editable"]
    assert not autos[1]["external_editable"]
    assert next(r for r in rows if r["kind"] == "file")["external_editable"]


async def test_edit_automation_sentences_only(hass, hass_ws_client, tmp_path):
    await _setup(hass)
    client = await hass_ws_client(hass)
    got = (await call(client, "external/get", kind="automation", item_id="1700000000001"))["result"]
    assert got == {"alias": "Bedtime", "triggers": [{"sentences": ["good night"]}, {"sentences": ["bed time"]}]}

    fields = {"alias": "Bed time", "triggers": [{"sentences": ["good night", "(night|nite) night"]}, {"sentences": ["bed time"]}]}
    resp = await call(client, "external/update", kind="automation", item_id="1700000000001", fields=fields)
    assert resp["success"], resp
    doc = yaml.safe_load((tmp_path / "automations.yaml").read_text())
    auto = doc[0]
    assert auto["alias"] == "Bed time" and auto["description"] == "keep me"
    assert auto["triggers"][0]["command"] == ["good night", "(night|nite) night"]
    assert auto["triggers"][2] == {"trigger": "time", "at": "23:00:00"}
    assert auto["actions"] == [{"action": "light.turn_off", "target": {"entity_id": "light.lounge"}}]
    assert doc[1]["alias"] == "No id"


@pytest.mark.parametrize(
    "fields, msg",
    [
        ({"triggers": [{"sentences": ["a"]}]}, "2 sentence trigger"),
        ({"triggers": [{"sentences": []}, {"sentences": ["b"]}]}, "at least one sentence"),
        ({"triggers": [{"sentences": ["good (night"]}, {"sentences": ["b"]}]}, "invalid sentence syntax"),
    ],
)
async def test_bad_automation_edit_rejected_and_file_untouched(hass, hass_ws_client, tmp_path, fields, msg):
    await _setup(hass)
    client = await hass_ws_client(hass)
    resp = await call(client, "external/update", kind="automation", item_id="1700000000001", fields=fields)
    assert resp["success"] is False and msg in resp["error"]["message"]
    assert (tmp_path / "automations.yaml").read_text() == AUTOMATIONS


async def test_edit_sentence_file_keeps_other_keys(hass, hass_ws_client, tmp_path):
    await _setup(hass)
    client = await hass_ws_client(hass)
    path = tmp_path / "custom_sentences" / "en" / "mine.yaml"
    item = f"{path}#SetScene"
    got = (await call(client, "external/get", kind="file", item_id=item))["result"]
    assert got["data"][0] == {"sentences": ["activate {scene}"], "slots": {"mode": "x"}}

    got["data"][0]["sentences"].append("start [the] {scene} scene")
    resp = await call(client, "external/update", kind="file", item_id=item, fields=got)
    assert resp["success"], resp
    doc = yaml.safe_load(path.read_text())
    assert doc["intents"]["SetScene"]["data"][0] == {"sentences": ["activate {scene}", "start [the] {scene} scene"], "slots": {"mode": "x"}}
    assert doc["lists"]["scene"]["values"] == ["movie", "dinner"] and doc["language"] == "en"


@pytest.mark.parametrize(
    "item",
    ["{root}/../../configuration.yaml#X", "{root}/assist_learner.yaml#X", "/etc/passwd#X"],
)
async def test_sentence_file_path_confined(hass, hass_ws_client, tmp_path, item):
    await _setup(hass)
    client = await hass_ws_client(hass)
    root = Path(tmp_path) / "custom_sentences" / "en"
    resp = await call(client, "external/update", kind="file", item_id=item.format(root=root),
                      fields={"data": [{"sentences": ["x"]}]})
    assert resp["success"] is False and "Not an editable" in resp["error"]["message"]


async def test_yaml_kinds(hass, hass_ws_client):
    await _setup(hass)
    client = await hass_ws_client(hass)
    dumped = (await call(client, "yaml/dump", kind="automation", fields={"alias": "A", "triggers": [{"sentences": ["x"]}], "junk": 1}))["result"]["yaml"]
    assert "junk" not in dumped
    back = (await call(client, "yaml/parse", kind="automation", yaml=dumped))["result"]["fields"]
    assert back == {"alias": "A", "triggers": [{"sentences": ["x"]}]}
    bad = await call(client, "yaml/parse", kind="learned", yaml="template: [1]")
    assert bad["success"] is False and "string" in bad["error"]["message"]
