"""Nightly proposer orchestration."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_AUTO_APPROVE,
    CONF_AUTO_APPROVE_THRESHOLD,
    CONF_CARRIER_VERBS,
    CONF_ENABLED_LEARNERS,
    CONF_LANGUAGE,
    CONF_LOOKBACK_DAYS,
    CONF_MAX_PROPOSALS,
    CONF_MIN_OCCURRENCES,
    CONF_RETENTION_DAYS,
    EVENT_PROPOSALS_CREATED,
    STATUS_PENDING,
)
from ..models import MissRecord, Proposal
from ..options import get_options
from .base import Candidate, Learner
from .music import FAMILY as MUSIC_FAMILY
from .music import MusicLearner
from .validate import async_check_template

_LOGGER = logging.getLogger(__name__)

_OPT = re.compile(r"\[[^\]]*\]")
_ALT = re.compile(r"\(([^|()]*)\|[^()]*\)")


def core_of(template: str) -> str:
    """Template with optional words dropped and alternations reduced to their first option."""
    t = _OPT.sub("", template)
    t = _ALT.sub(r"\1", t)
    return " ".join(t.split())


def player_phrases_for(hass: HomeAssistant):
    """(entity_id, device_id) -> phrases a user might say for this player (name, area, name+area)."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    def phrases(entity_id: str, _device_id: str | None) -> list[str]:
        out: list[str] = []
        entry = ent_reg.async_get(entity_id)
        state = hass.states.get(entity_id)
        if state and state.name:
            out.append(state.name)
        if entry:
            for n in (entry.name, entry.original_name):
                if n:
                    out.append(n)
            area_id = entry.area_id
            if not area_id and entry.device_id and (dev := dev_reg.async_get(entry.device_id)):
                area_id = dev.area_id
            if area_id and (area := area_reg.async_get_area(area_id)):
                out.append(area.name)
        return list(dict.fromkeys(p for p in out if p))

    return phrases


def build_learners(hass: HomeAssistant, enabled: list[str]) -> list[Learner]:
    learners: list[Learner] = []
    if MUSIC_FAMILY in enabled:
        learners.append(
            MusicLearner(
                player_phrases=player_phrases_for(hass),
                is_ma_player=lambda e: (
                    (r := er.async_get(hass).async_get(e)) is not None
                    and r.platform == "music_assistant"
                ),
            )
        )
    return learners


async def async_run_proposer(hass: HomeAssistant, entry: ConfigEntry) -> list[Proposal]:
    """Process unprocessed misses into proposals. Returns newly created proposals."""
    store = entry.runtime_data.store
    opts = get_options(entry)
    language = entry.data.get(CONF_LANGUAGE, hass.config.language)
    now = dt_util.utcnow()
    cutoff = (now - timedelta(days=opts[CONF_LOOKBACK_DAYS])).isoformat()
    learners = build_learners(hass, opts[CONF_ENABLED_LEARNERS])

    misses = [
        MissRecord.from_dict(m)
        for m in store.misses.values()
        if not m.get("processed") and m.get("timestamp", "") >= cutoff
    ]

    # fingerprint -> (candidate, [misses that produced it])
    grouped: dict[str, tuple[Candidate, list[MissRecord]]] = {}
    for miss in misses:
        for learner in learners:
            if not learner.accepts(miss):
                continue
            cand = learner.extract(miss)
            if cand is None:
                miss.unalignable = True
                miss.processed = True
                store.misses.put(miss.id, miss.as_dict())
                break
            fp = cand.fingerprint
            if fp in grouped:
                grouped[fp][1].append(miss)
            else:
                grouped[fp] = (cand, [miss])
            break
        else:
            # no learner cares about this miss: nothing to learn, mark done
            miss.processed = True
            store.misses.put(miss.id, miss.as_dict())

    learned = store.learned.values()
    learned_fps = {lv["fingerprint"] for lv in learned}
    learned_cores = {core_of(lv["template"]): lv for lv in learned}
    rejected = set(store.rejected.items)
    pending_by_fp = {
        p["fingerprint"]: p for p in store.proposals.values() if p["status"] == STATUS_PENDING
    }

    ready: list[tuple[Candidate, list[MissRecord], int, dict | None]] = []
    for fp, (cand, group) in grouped.items():
        if fp in rejected or fp in learned_fps:
            _finish(store, group, None)
            continue
        row = store.candidates.get(fp) or {
            "fingerprint": fp,
            "template": cand.template,
            "family": cand.family,
            "count": 0,
            "first_seen": now.isoformat(),
        }
        total = row["count"] + len(group)
        row.update(count=total, last_seen=now.isoformat(), confidence=cand.confidence)
        if fp in pending_by_fp:
            # more evidence for an existing pending proposal
            p = pending_by_fp[fp]
            p["count"] = total
            for m in group:
                if len(p["examples"]) < 3 and m.text not in p["examples"]:
                    p["examples"].append(m.text)
            store.proposals.put(p["id"], p)
            store.candidates.put(fp, row)
            _finish(store, group, p["id"])
            continue
        if total < opts[CONF_MIN_OCCURRENCES]:
            store.candidates.put(fp, row)
            _finish(store, group, None)
            continue
        replaces = learned_cores.get(core_of(cand.template))
        ready.append((cand, group, total, replaces))
        store.candidates.put(fp, row)

    created: list[Proposal] = []
    # highest confidence first; capped per night. Capped groups stay unprocessed (and uncounted next run)
    ready.sort(key=lambda r: (-r[0].confidence, -r[2]))
    for cand, group, total, replaces in ready:
        samples = cand.slots.get("samples", {})
        check = await async_check_template(
            hass, cand.template, samples, language, opts[CONF_CARRIER_VERBS], allow_learned=replaces is not None
        )
        if not check.ok:
            _LOGGER.debug("Dropped template (%s)", check.drop_reason or check.block_reason)
            _finish(store, group, None)
            if check.block_reason:
                store.candidates.put(
                    cand.fingerprint,
                    {**store.candidates.get(cand.fingerprint), "blocked": check.block_reason},
                )
            continue
        if len(created) >= opts[CONF_MAX_PROPOSALS]:
            # un-count so the next run counts these misses once, not twice
            row = store.candidates.get(cand.fingerprint)
            row["count"] -= len(group)
            store.candidates.put(cand.fingerprint, row)
            continue
        proposal = Proposal(
            id=uuid.uuid4().hex[:12],
            type="update" if replaces else "new",
            family=cand.family,
            template=cand.template,
            slots=cand.slots,
            examples=[m.text for m in group][:3],
            count=total,
            confidence=cand.confidence,
            status=STATUS_PENDING,
            warnings=cand.warnings + check.warnings,
            fingerprint=cand.fingerprint,
            replaces=replaces["id"] if replaces else None,
            diff=f"- {replaces['template']}\n+ {cand.template}" if replaces else None,
            created=now.isoformat(),
        )
        proposal.slots = {k: v for k, v in proposal.slots.items() if k != "samples"}
        store.proposals.put(proposal.id, proposal.as_dict())
        _finish(store, group, proposal.id)
        created.append(proposal)

    _apply_retention(store, opts[CONF_RETENTION_DAYS], now)
    auto = 0
    if opts[CONF_AUTO_APPROVE]:
        from ..learned import async_approve

        for p in created:
            if (
                p.family == MUSIC_FAMILY
                and p.count >= opts[CONF_AUTO_APPROVE_THRESHOLD]
                and not p.warnings
            ):
                await async_approve(hass, entry, p.id, auto=True)
                auto += 1
    from ..notify import async_notify_proposals

    still_pending = [p for p in created if store.proposals.get(p.id)["status"] == STATUS_PENDING]
    await async_notify_proposals(hass, entry, still_pending, auto)
    if created:
        hass.bus.async_fire(
            EVENT_PROPOSALS_CREATED, {"count": len(created), "ids": [p.id for p in created]}
        )
    entry.runtime_data.async_notify_listeners()
    return created


def _finish(store, group: list[MissRecord], proposal_id: str | None) -> None:
    for m in group:
        m.processed = True
        m.processed_by_proposal_id = proposal_id
        store.misses.put(m.id, m.as_dict())


def _apply_retention(store, retention_days: int, now) -> None:
    """Delete processed raw requests: immediately when retention is 0, else after N days."""
    if retention_days <= 0:
        store.misses.prune(lambda m: m.get("processed"))
        return
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    store.misses.prune(lambda m: m.get("processed") and m.get("timestamp", "") < cutoff)
    # pending proposal examples are kept until a decision; nothing else holds raw text

