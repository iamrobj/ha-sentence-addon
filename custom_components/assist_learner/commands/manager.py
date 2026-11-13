"""Custom command lifecycle: create, generate, edit, approve, disable, revert, run once."""

from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ..const import CONF_EXEC_LOG_SIZE, CONF_LANGUAGE
from ..options import get_options
from ..sentences import async_write_sentences
from . import generate as gen
from .actions import RateLimiter, async_execute, log_execution
from .model import HASH_FIELDS, content_hash, new_command, push_history
from .validate import Report, async_validate_command

_LOGGER = logging.getLogger(__name__)

EDITABLE = {*HASH_FIELDS, "name", "description", "examples", "accept_collisions", "notes"}


class CommandError(HomeAssistantError):
    """User-facing command error."""


class CommandManager:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.rate_limiter = RateLimiter()

    @property
    def store(self):
        return self.entry.runtime_data.store

    @property
    def language(self) -> str:
        return self.entry.data[CONF_LANGUAGE]

    def _get(self, cmd_id: str) -> dict[str, Any]:
        cmd = self.store.commands.get(cmd_id)
        if cmd is None:
            raise CommandError(f"Unknown command {cmd_id}")
        return cmd

    def _save(self, cmd: dict[str, Any]) -> None:
        cmd["updated"] = dt_util.utcnow().isoformat()
        self.store.commands.put(cmd["id"], cmd)
        self.entry.runtime_data.async_notify_listeners()

    def list(self) -> list[dict[str, Any]]:
        return self.store.commands.values()

    def existing_sentences(self, exclude: str | None = None) -> list[str]:
        out = [t["template"] for t in self.store.learned.values()]
        for c in self.store.commands.values():
            if c["id"] != exclude:
                out.extend(c.get("sentences") or [])
        return out

    # -- create / generate ---------------------------------------------------------------------------
    def create(
        self,
        name: str,
        description: str,
        entities: dict[str, str],
        examples: list[str] | None = None,
        cmd_type: str = "answer",
    ) -> dict[str, Any]:
        if not name.strip() or not description.strip():
            raise CommandError("A name and a description are required")
        cmd = new_command(name.strip(), description.strip(), entities, examples, cmd_type)
        self._save(cmd)
        return cmd

    async def payload(self, cmd_id: str, feedback: str | None = None) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        return await gen.async_build_payload(
            self.hass, self.entry, cmd, feedback=feedback, existing_sentences=self.existing_sentences(cmd_id)
        )

    async def generate(self, cmd_id: str, feedback: str | None = None) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        payload = await self.payload(cmd_id, feedback)
        try:
            result = await gen.async_generate(self.hass, self.entry, payload)
        except gen.GenerationError as err:
            self._log_generation(cmd, payload, None, str(err))
            raise
        push_history(cmd, "generated" if not feedback else f"regenerated: {feedback}")
        for key in ("type", "sentences", "slots", "actions", "response_template", "unavailable_template", "notes"):
            cmd[key] = result[key]
        cmd["llm_warnings"] = result["warnings"]
        if cmd["status"] in ("draft", "disabled"):
            cmd["status"] = "pending"
        report = await self.validate(cmd)
        self._log_generation(cmd, payload, result, "ok" if report.ok else "; ".join(report.errors))
        self._save(cmd)
        return cmd

    def _log_generation(self, cmd, payload, result, outcome) -> None:
        key = f"{dt_util.utcnow().isoformat()}_{cmd['id']}"
        self.store.generation_log.put(
            key,
            {"command_id": cmd["id"], "prompt": payload["instructions"], "result": result, "outcome": outcome},
        )
        for k in sorted(self.store.generation_log.items)[:-20]:
            self.store.generation_log.delete(k)

    # -- validation / editing -----------------------------------------------------------------------
    async def validate(self, cmd: dict[str, Any]) -> Report:
        report = await async_validate_command(self.hass, self.entry, cmd, language=self.language)
        cmd["validation"] = report.as_dict()
        return report

    async def update(self, cmd_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        bad = set(fields) - EDITABLE
        if bad:
            raise CommandError(f"Cannot edit {sorted(bad)}")
        trial = copy.deepcopy(cmd)
        trial.update(fields)
        report = await self.validate(trial)
        if cmd["status"] == "active" and not report.ok:
            raise CommandError("Edit rejected: " + "; ".join(report.errors))
        push_history(cmd, "edited")
        cmd.update({k: trial[k] for k in fields})
        cmd["validation"] = trial["validation"]
        if cmd["status"] == "active":
            cmd["hash"] = content_hash(cmd)
            await async_write_sentences(self.hass, self.entry)
        self._save(cmd)
        return cmd

    async def revert(self, cmd_id: str, index: int) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        try:
            snap = cmd["history"][index]
        except IndexError as err:
            raise CommandError("No such version") from err
        return await self.update(cmd_id, {k: copy.deepcopy(snap[k]) for k in HASH_FIELDS})

    def history(self, cmd_id: str) -> list[dict[str, Any]]:
        return self._get(cmd_id)["history"]

    # -- approval -----------------------------------------------------------------------------------
    async def approve(self, cmd_id: str) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        report = await self.validate(cmd)
        if not report.ok:
            self._save(cmd)
            raise CommandError("Cannot approve: " + "; ".join(report.errors))
        cmd["status"] = "active"
        cmd["hash"] = content_hash(cmd)
        self._save(cmd)
        await async_write_sentences(self.hass, self.entry)
        return cmd

    async def reject(self, cmd_id: str, note: str | None = None) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        cmd["status"] = "draft"
        cmd["notes"] = note or cmd.get("notes", "")
        self._save(cmd)
        await async_write_sentences(self.hass, self.entry)
        return cmd

    async def set_enabled(self, cmd_id: str, enabled: bool) -> dict[str, Any]:
        cmd = self._get(cmd_id)
        if enabled:
            report = await self.validate(cmd)
            if not report.ok:
                self._save(cmd)
                raise CommandError("Cannot enable: " + "; ".join(report.errors))
            cmd["status"] = "active"
            cmd["hash"] = content_hash(cmd)
        else:
            cmd["status"] = "disabled"
        self._save(cmd)
        await async_write_sentences(self.hass, self.entry)
        return cmd

    async def delete(self, cmd_id: str) -> None:
        self._get(cmd_id)
        self.store.commands.delete(cmd_id)
        await async_write_sentences(self.hass, self.entry)
        self.entry.runtime_data.async_notify_listeners()

    # -- runtime ------------------------------------------------------------------------------------
    async def revalidate_all(self) -> None:
        """At every start: disable active commands that no longer validate, and notify."""
        broken: list[str] = []
        for cmd in self.store.commands.values():
            if cmd["status"] != "active":
                continue
            report = await self.validate(cmd)
            if not report.ok or cmd.get("hash") != content_hash(cmd):
                cmd["status"] = "disabled"
                cmd["notes"] = "Disabled: " + ("; ".join(report.errors) or "content changed outside the panel")
                broken.append(cmd["name"])
            self._save(cmd)
        if broken:
            await async_write_sentences(self.hass, self.entry)
            persistent_notification.async_create(
                self.hass,
                "These Assist Learner commands no longer validate and were disabled: " + ", ".join(broken),
                title="Assist Learner",
                notification_id="assist_learner_disabled",
            )

    async def run_once(self, cmd_id: str, values: dict[str, Any] | None = None, context: Context | None = None):
        """Admin 'Run once' test button for action commands."""
        cmd = self._get(cmd_id)
        if cmd["type"] == "answer":
            raise CommandError("Answer commands have nothing to run")
        report = await self.validate(cmd)
        if not report.ok:
            raise CommandError("Cannot run: " + "; ".join(report.errors))
        result = await async_execute(self.hass, cmd, values or {}, context or Context())
        log_execution(
            self.store, cmd, None, result, get_options(self.entry)[CONF_EXEC_LOG_SIZE], source="run_once"
        )
        self.entry.runtime_data.async_notify_listeners()
        return result
