# LEARNINGS

Running log of rules, decisions and gotchas that aren't obvious from the code. Newest at the bottom.
Format: `- YYYY-MM-DD — <learning> (why / how to apply)`.

## Process rules
- 2026-11-13 — Always commit at sensible intervals and push. (User's first rule for this repo.)
- 2026-11-13 — Record non-obvious findings here in the same commit that discovers them.

## Environment
- 2026-11-13 — HA 2026.11 requires Python >=3.14.2; system Python is 3.13 and Docker is not usable (no group
  access, no sudo). Solution: `uv` (pip install --user uv) -> `uv python install 3.14` -> venv at `.venv`.
- 2026-11-13 — `uv pip install pytest-homeassistant-custom-component` alone resolves HA 2026.10.4; explicitly add
  `homeassistant==2026.11.1` to get 2026.11.

## Decisions (from kickoff)
- 2026-11-13 — Build scope is full v1 (phases 1–9), committed per phase. Licence MIT. Integration lives at repo
  root (`custom_components/assist_learner`, `hacs.json`).
