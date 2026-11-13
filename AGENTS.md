# AGENTS.md — Assist Learner

Instructions for any AI agent (Claude Code etc.) working in this repo. `CLAUDE.md` points here.

## What this is
A Home Assistant custom integration (`custom_components/assist_learner`) that sits between Assist and an LLM
conversation agent, learns local sentence templates from LLM fallbacks, and lets the user author local
commands in plain English. Full design: `docs/technical-plan.md` (source of truth for scope and decisions).
Target HA: **2026.11.x**, Python 3.14.

## Rules (non-negotiable)
1. **Commit at sensible intervals and push.** One logical unit per commit (a phase step, a module + its tests).
   Push after every commit or small group of commits. Never leave work uncommitted/unpushed at the end of a turn.
   Commit messages: imperative, explain why. Work on `main` unless told otherwise.
2. **Capture learnings.** Anything non-obvious that isn't visible in the code (HA API quirks, spike findings,
   decisions, gotchas, user preferences) goes in `LEARNINGS.md` in the same commit that discovered it.
3. **Plan is the spec.** Deviating from `docs/technical-plan.md` requires a note in `LEARNINGS.md` (and a plan
   edit if the change is lasting).
4. **Safety first for action commands.** Hard-blocked domains (§3.7.8) have no override. LLM output is never
   trusted: every generated item goes through deterministic validation.
5. **Privacy.** Raw utterances never go in logs at INFO+, diagnostics, fixtures, or commits. Use synthetic data.
6. **Tests accompany code.** Run `.venv/bin/pytest` before committing; don't commit red tests.
7. Keep HA core-API calls in small, isolated modules (see plan risks 3, 7, 12) so upgrades are cheap.

## Dev environment
- `uv venv --python 3.14 .venv` then `uv pip install --python .venv/bin/python -r requirements_test.txt`
- No Docker access on the dev machine; tests use `pytest-homeassistant-custom-component` against pip-installed HA.
- Lint/format: `ruff` (config in `pyproject.toml`).

## Layout
See plan §4. Tests in `tests/`. Spike notes in `docs/spike-notes.md`.

## Module map (differs slightly from plan §4)
`handlers.py` (runtime intent handlers; NOT `intent.py`, see LEARNINGS), `learned.py` (approve/reject/edit),
`players.py`, `sentences.py` (YAML writer), `recognizer.py` (the only touchpoint for the default-agent recognizer),
`external.py` (in-place edits of HA-owned sentences: automations.yaml, custom_sentences files), `websocket.py` + `panel.py` + `frontend/`, `commands/` (model, generate, validate, template_guard, actions,
importer, manager), `proposer/` (music learner, alignment, templating, validate, generalise), `replay.py`.
