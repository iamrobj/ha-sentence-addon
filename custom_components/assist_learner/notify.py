"""Notify the user about new proposals."""

from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_NOTIFY, NOTIFY_NONE, NOTIFY_PERSISTENT
from .models import Proposal
from .options import get_options


async def async_notify_proposals(
    hass: HomeAssistant, entry: ConfigEntry, created: list[Proposal], auto_approved: int = 0
) -> None:
    if not created and not auto_approved:
        return
    mode = get_options(entry)[CONF_NOTIFY]
    if mode == NOTIFY_NONE:
        return
    parts = []
    if created:
        parts.append(f"{len(created)} new proposed local command(s) to review in Settings -> Repairs.")
    if auto_approved:
        parts.append(f"{auto_approved} learned command(s) were auto-approved.")
    message = " ".join(parts)
    if mode == NOTIFY_PERSISTENT:
        persistent_notification.async_create(
            hass, message, title="Assist Learner", notification_id="assist_learner_proposals"
        )
    elif "." in mode:  # notify.<target>
        domain, service = mode.split(".", 1)
        await hass.services.async_call(
            domain, service, {"title": "Assist Learner", "message": message}, blocking=False
        )
