"""Offline replay harness: run the proposer logic over exported misses to tune thresholds.

    python -m custom_components.assist_learner.replay misses.json [--min-occurrences 2] [--players players.json]

`misses.json` is a list of records ({"text", "actions": [...]}, as in the `assist_learner.misses` store or the
panel's export; raw text needs a retention > 0). `players.json` maps player entity ids to spoken phrases.
Only the static checks run here (no recognizer), so already-local / greediness checks are NOT applied.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from .models import MissRecord
from .proposer.music import MusicLearner
from .proposer.templating import has_verb_anchor, wildcards_separated

DEFAULT_VERBS = ["play", "put on", "stick on", "queue", "start"]


def replay(
    misses: list[dict[str, Any]],
    *,
    min_occurrences: int = 1,
    players: dict[str, list[str]] | None = None,
    verbs: list[str] | None = None,
) -> dict[str, Any]:
    players = players or {}
    verbs = verbs or DEFAULT_VERBS
    learner = MusicLearner(player_phrases=lambda e, _d: players.get(e, []))
    counts: Counter[str] = Counter()
    best: dict[str, float] = {}
    rejected: dict[str, str] = {}
    unalignable: list[str] = []
    skipped = 0
    for i, raw in enumerate(misses):
        miss = MissRecord.from_dict(
            {
                "id": raw.get("id", str(i)),
                "timestamp": raw.get("timestamp", ""),
                "text": raw.get("text", ""),
                "normalised": "",
                "language": raw.get("language", "en"),
                "device_id": raw.get("device_id"),
                "area_id": raw.get("area_id"),
                "target_agent": raw.get("target_agent", ""),
                "actions": raw.get("actions", []),
                "success": raw.get("success", True),
            }
        )
        if not learner.accepts(miss):
            skipped += 1
            continue
        cand = learner.extract(miss)
        if cand is None:
            unalignable.append(miss.text)
            continue
        if not has_verb_anchor(cand.template, verbs):
            rejected[cand.template] = "needs two fixed words including a carrier verb"
            continue
        if not wildcards_separated(cand.template):
            rejected[cand.template] = "wildcards not separated by a literal word"
            continue
        counts[cand.template] += 1
        best[cand.template] = max(best.get(cand.template, 0), cand.confidence)
    proposals = [
        {"template": t, "count": n, "confidence": best[t]}
        for t, n in counts.most_common()
        if n >= min_occurrences
    ]
    below = [{"template": t, "count": n} for t, n in counts.items() if n < min_occurrences]
    return {
        "proposals": sorted(proposals, key=lambda p: (-p["confidence"], -p["count"])),
        "below_threshold": below,
        "rejected": rejected,
        "unalignable": unalignable,
        "skipped_non_music": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("misses")
    ap.add_argument("--min-occurrences", type=int, default=1)
    ap.add_argument("--players")
    args = ap.parse_args(argv)
    with open(args.misses, encoding="utf-8") as fh:
        misses = json.load(fh)
    players = None
    if args.players:
        with open(args.players, encoding="utf-8") as fh:
            players = json.load(fh)
    json.dump(replay(misses, min_occurrences=args.min_occurrences, players=players), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
