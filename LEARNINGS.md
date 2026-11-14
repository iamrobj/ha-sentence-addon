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
- 2026-11-13 — Panel is verified only by a jsdom smoke test (`tests/frontend/panel_smoke.mjs`, renders every tab with a
  mocked `hass.callWS`) — NOT in a real browser against a live HA. Do a manual look in HA before release.
  jsdom must be v24 on Node 20 (newer jsdom needs a newer Node).

## Sentence editor (builder / YAML)
- 2026-11-13 — Custom-command editor in the Sentences tab has a Builder and a raw YAML mode. No JS YAML parser is vendored, so
  switching modes round-trips the unsaved draft through stateless websocket calls `commands/to_yaml` / `commands/from_yaml`
  (HA's own yaml util). A YAML parse/shape error keeps you in the current mode so edits aren't lost. Save always goes through
  `commands/update` (full re-validation). Learned templates keep the single-line editor (a template string has no structure
  worth a builder). Action data values in the builder are JSON-parsed when possible ("5" becomes 5; `{level}` stays a string).
- 2026-11-13 — The builder/YAML editor covers every kind (KINDS table in panel.js): custom commands, learned templates
  (template only), and Home Assistant-owned sentences. YAML conversion is `yaml/dump` / `yaml/parse` with a `kind`.
- 2026-11-13 — **User decision: HA-owned sentences are edited in place** (deviates from plan's "never edit your files";
  plan updated). `external.py`: automations in `automations.yaml` (by `id`; only alias + conversation-trigger `command`
  lists change, trigger count is fixed, conditions/actions untouched) then `automation.reload`; intents in
  `custom_sentences/<lang>/*.yaml` (path confined to that dir, never our own file; whole `data` list replaced, other
  keys kept) then `conversation.reload`. Every sentence is hassil-parsed first. Round-trip uses HA's yaml loader/dumper
  like HA's own automation editor, so comments in those files are lost on save. Automations without an `id` (or in
  packages / other files) are read-only. UI label for `source: external` is "home assistant".
- Builder/YAML editor has only had the jsdom smoke test, not a real browser.

## Routing facts
- 2026-11-14 — The voice pipeline (`assist_pipeline/default_pipeline.py`) runs automation **sentence triggers before
  any non-default conversation agent**, ours included; then, if `prefer_local_intents`, local intents; only then our
  agent. So a sentence-trigger match never reaches Assist Learner or the LLM. The test box mirrors this order using
  `DefaultAgent.async_recognize_sentence_trigger` (matches only; despite its docstring it does not fire callbacks).
  Before this fix the test box ignored triggers and wrongly said "would go to the LLM".

## Sentence sources (fixed 2026-11-14, found on a live en-GB install)
- 2026-11-14 — **HA only loads `custom_sentences/<language variant>/`**: `language_util.matches(lang, get_languages())[0]`,
  so `en-GB` -> `en`, `de-DE` -> `de`. We used the raw language, so on en-GB we (a) never saw the user's own files and
  (b) wrote OUR sentence file to `custom_sentences/en-GB/` where HA never loaded it (learned templates and commands
  silently stayed LLM-only). Always use `recognizer.sentences_language()`. The writer deletes the stale raw-language copy.
- 2026-11-14 — HA-owned sentences come from: custom_sentences files (variant dir), `conversation: intents:` (read from the
  agent manager's private CONFIG intents, isolated in `recognizer.config_intents`), and automations from ANY file
  (automation entities' `raw_config`, `recognizer.automation_configs`). Only automations whose id is in
  automations.yaml are editable; config intents are read-only. Fallback when automation isn't loaded: read
  automations.yaml.
- 2026-11-14 — **AI Task: never use a field-less `object` selector.** The real Anthropic AI Task entity turns it into a
  schema that only allows `{}`, so generated slots and actions were always empty ("service must look like
  domain.service"). Slots/actions are now JSON text fields; `normalise_result` parses them (tolerates ``` fences) and
  reports unparseable JSON as a warning.

## Live-use findings (2026-11-14)
- 2026-11-14 — Users can (and did) pick `conversation.home_assistant` as the target: every miss then fails in ms with
  `no_intent_match` and nothing is learned. The picker now excludes it and us; Reconfigure changes the target without
  losing data; a Repairs issue flags old entries.
- 2026-11-14 — With retention 0 (default), processed misses vanish from the Misses tab immediately, which looks like a
  bug. The proposer now records outcome counts (`runtime_data.last_run`, no raw text) and the panel explains a run.

## Hardening
- 2026-11-13 — Diagnostics must redact miss `actions`/`tool_calls` too: they carry track/artist names (listening
  habits), not just the utterance. Command `actions` are config, not user data, so they are NOT redacted there.
- 2026-11-13 — hassil can raise bare `AssertionError` on degenerate templates (e.g. "( )"). Template sampling and
  per-miss extraction are wrapped so one bad record never aborts the nightly run.
- 2026-11-13 — Update proposals (template differs from a learned one only by optionals) must be allowed to match our
  OWN learned intent in the "already local"/greediness checks (`allow_learned`), otherwise they are always dropped.
- 2026-11-13 — Replay harness: `python -m custom_components.assist_learner.replay misses.json` (static checks only).

## Known gaps / unverified (be honest in reviews)
- Dev/CI runs HA 2026.10.4 (see Dev env gotchas); nothing has been run on 2026.11.x or a live instance.
- Not yet verified on a live HA: which sentence wins when a custom and a built-in sentence both match (plan risk 10).
  The collision check therefore blocks approval by default; `accept_collisions` lets the user accept overlap.
- The `ai_task.generate_data` structure is exercised only against a fake AI Task entity. The object-selector problem
  was found live and fixed (JSON text fields); re-verify generation of a slot + action command on the real entity.
- Panel verified only with the jsdom smoke test, not visually in a browser.
- Entity `supported_features` are not checked during action validation.
- Sentence-trigger automations are discovered only from `automations.yaml`; intent_script only from `configuration.yaml`.
