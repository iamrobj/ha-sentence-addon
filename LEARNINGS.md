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

## Spike findings (HA 2026.11.1)
- 2026-11-13 — Nested `conversation.async_converse` from inside `_async_handle_message` is supported: the
  active chat log is reused by conversation_id, so the proxy reads the target's tool calls from the same log.
  Pass the SAME conversation_id. Details in `docs/spike-notes.md`.
- 2026-11-13 — No Docker: API confirmation is by reading installed source + pytest-homeassistant-custom-component.
  Anything needing a live instance is flagged "verify-in-test" in the spike notes.

## Dev env gotchas (read before touching the venv)
- 2026-11-13 — **Dev/test env runs HA 2026.10.4, not 2026.11.1.** `pytest-homeassistant-custom-component`
  0.13.375 (latest) is pinned to 2026.10.4 and breaks on 2026.11.1 (private import `_clear_logger_overwrites`
  from `components.logger`; also httpx/httpx2 aliasing order). Re-check PyPI for a newer plugin release and bump
  `requirements_test.txt` + CI when one exists. Spike notes were read from 2026.11.1 source; APIs used so far are
  identical in 2026.10.4.
- 2026-11-13 — The plugin does NOT install requirements of HA components our integration depends on
  (hassil, home-assistant-intents, ha-ffmpeg, frontend, ...). They are listed in `requirements_test.txt`.
  When adding a dependency component, run the manifest-requirements scan and add what's missing.
- 2026-11-13 — Never `uv pip install pytest-homeassistant-custom-component` unpinned: the resolver backtracks to
  0.2.1 (pytest 6). Keep it pinned. If the venv gets weird, `rm -rf .venv` and rebuild.
- 2026-11-13 — Tests must `async_setup_component(hass, "homeassistant", {})` first, otherwise `conversation`
  fails on `exposed_entities`.

## HA API gotchas
- 2026-11-13 — `ToolResultContent.tool_result` is deprecated (removed 2027.11, logs/errors); use `.result.data`.
- 2026-11-13 — In tests, append content to `chat_log.content` directly for fake tool calls;
  `async_add_assistant_content` raises "No LLM API configured" for non-external tool calls.
- 2026-11-13 — Legacy-agent test double: `conversation.async_set_agent(hass, <obj with entry_id>, agent)` registers
  an agent id usable with `async_converse(agent_id=<entry_id>)`.
- 2026-11-13 — `DefaultAgent.async_recognize_intent` returns a RecognizeResult even for PARTIAL matches (e.g.
  "put on lose control" -> HassTurnOn with `unmatched_entities`). A real local match is
  `result is not None and not result.unmatched_entities` (same test HA uses). Encoded in `recognizer.py`.
- 2026-11-13 — hassil: use `parse_sentence` + `sample_sentence(skip_optionals=True)` to expand templates; fill
  `{slot}` placeholders with plain text BEFORE parsing.
- 2026-11-13 — **Do not name a module `intent.py`** (or `conversation.py` is fine because we ARE a conversation
  platform, `sensor.py`, `button.py` likewise): HA treats `<integration>/intent.py` as the `intent` platform and
  calls `async_setup_intents`. Our runtime handlers live in `handlers.py` (plan §4 said `intent.py`).

## Custom commands (phases 6-7) decisions
- 2026-11-13 — Build order deviates from the plan: backend for custom commands/actions/import first, THEN
  websocket API + panel (so the panel is written once against the full API). Same scope, different order.
- 2026-11-13 — Custom commands support ONLY `number` and `choice` slots (no wildcards at all in v1): simplest
  way to satisfy "no wildcards in action commands" and "wildcards need literal anchors".
- 2026-11-13 — YAML `lists` are global per file, so command slots are scoped as `<cmd_id>_<slot>`
  (`commands/model.py: scope_sentence/slot_scope`). Number slots use hassil `range` lists.
- 2026-11-13 — Unavailable-entity semantics: if a command has an `unavailable_template`, the runtime swaps to it
  when ANY entity is unavailable/unknown, so only that template must render when entities are unavailable. Without
  one, the main template must survive unavailable entities (use `| default`).
- 2026-11-13 — Validation does not check entity supported_features (only existence, domain, service
  allow-list, field names/ranges from live descriptions, climate min/max). Documented limitation.
- 2026-11-13 — `async_at_started` callbacks must be `@callback`-decorated or HA runs them in a thread and
  `hass.async_create_task` raises a thread-safety error.

## Panel / websocket
- 2026-11-13 — Websocket messages reserve `id` (message id) and `type`; use `item_id` for entity ids in our API.
- 2026-11-13 — Lit is vendored as `frontend/vendor/lit.js` (esbuild bundle of lit@3.2.1 + repeat/unsafe-html; no
  CDN at runtime, no build step in the repo). To rebuild: `npm i lit esbuild`, entry exports
  `{LitElement, html, css, nothing, svg}`, `esbuild entry.js --bundle --minify --format=esm`.
- 2026-11-13 — Panel registered with `panel_custom` + static path `/assist_learner_static` (admin only).
