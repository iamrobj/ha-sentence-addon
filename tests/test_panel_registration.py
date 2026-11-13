"""The sidebar panel must be registered and its static files served."""

from __future__ import annotations

import pytest

from tests.test_commands import _setup


@pytest.fixture(autouse=True)
def config_dir(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)


async def test_panel_registered_and_served(hass, hass_client):
    await _setup(hass)
    panel = hass.data["frontend_panels"]["assist-learner"]
    assert panel.sidebar_title == "Assist Learner"
    assert panel.require_admin is True
    client = await hass_client()
    for path in ("panel.js", "vendor/lit.js"):
        resp = await client.get(f"/assist_learner_static/{path}")
        assert resp.status == 200, path
