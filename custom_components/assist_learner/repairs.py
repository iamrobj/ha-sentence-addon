"""Repairs: one fixable issue per pending proposal (notification route into approve/edit/reject)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from . import learned
from .const import DOMAIN, STATUS_PENDING

PREFIX = "proposal_"


def sync_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create issues for pending proposals; delete issues for decided ones."""
    store = entry.runtime_data.store
    pending = {p["id"]: p for p in store.proposals.values() if p["status"] == STATUS_PENDING}
    registry = ir.async_get(hass)
    for (domain, issue_id), _ in list(registry.issues.items()):
        if domain == DOMAIN and issue_id.startswith(PREFIX) and issue_id[len(PREFIX) :] not in pending:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for pid, p in pending.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            PREFIX + pid,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="proposal",
            translation_placeholders={"template": p["template"], "type": p["type"]},
            data={"proposal_id": pid, "entry_id": entry.entry_id},
        )


def describe(p: dict[str, Any]) -> str:
    lines = [f"Template: {p['template']}", f"Type: {p['type']}", f"Seen {p['count']} time(s)"]
    if p.get("diff"):
        lines.append(f"Change:\n{p['diff']}")
    if p.get("examples"):
        lines.append("Examples: " + "; ".join(p["examples"]))
    if p.get("suggestion"):
        lines.append(f"LLM suggestion (not applied unless you edit it in): {p['suggestion']}")
    if p.get("warnings"):
        lines.append("Warnings: " + "; ".join(p["warnings"]))
    return "\n".join(lines)


class ProposalRepairFlow(RepairsFlow):
    def __init__(self, entry: ConfigEntry, proposal_id: str) -> None:
        self._entry = entry
        self._pid = proposal_id

    def _proposal(self) -> dict[str, Any] | None:
        return self._entry.runtime_data.store.proposals.get(self._pid)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        p = self._proposal()
        if p is None or p["status"] != STATUS_PENDING:
            return self.async_create_entry(data={})
        return self.async_show_menu(
            step_id="init",
            menu_options=["approve", "edit", "reject"],
            description_placeholders={"details": describe(p)},
        )

    async def async_step_approve(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        await learned.async_approve(self.hass, self._entry, self._pid)
        return self.async_create_entry(data={})

    async def async_step_edit(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        p = self._proposal()
        if user_input is not None:
            try:
                await learned.async_approve(
                    self.hass, self._entry, self._pid, template=user_input["template"]
                )
            except learned.ProposalError as err:
                errors["base"] = "invalid_template"
                return self.async_show_form(
                    step_id="edit",
                    data_schema=vol.Schema({vol.Required("template", default=user_input["template"]): str}),
                    errors=errors,
                    description_placeholders={"reason": str(err)},
                )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="edit",
            data_schema=vol.Schema({vol.Required("template", default=p["template"]): str}),
            description_placeholders={"reason": ""},
        )

    async def async_step_reject(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            await learned.async_reject(self.hass, self._entry, self._pid, user_input.get("note"))
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="reject", data_schema=vol.Schema({vol.Optional("note"): str})
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    entry = hass.config_entries.async_get_entry((data or {})["entry_id"])
    return ProposalRepairFlow(entry, (data or {})["proposal_id"])
