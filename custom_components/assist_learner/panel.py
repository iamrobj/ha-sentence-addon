"""Sidebar panel registration (admin only)."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

URL_BASE = "/assist_learner_static"
PANEL_PATH = "assist-learner"
COMPONENT = "assist-learner-panel"


async def async_register_panel(hass: HomeAssistant) -> None:
    if hass.data.get(f"{DOMAIN}_panel"):
        return
    hass.data[f"{DOMAIN}_panel"] = True
    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_BASE, str(frontend_dir), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=COMPONENT,
        frontend_url_path=PANEL_PATH,
        module_url=f"{URL_BASE}/panel.js",
        sidebar_title="Assist Learner",
        sidebar_icon="mdi:microphone-message",
        require_admin=True,
        config={},
    )
