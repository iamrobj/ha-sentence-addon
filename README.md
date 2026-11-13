# Assist Learner

A Home Assistant custom integration that **learns local voice commands from LLM fallbacks** and lets you
**author new local commands in plain English**.

Today, anything Assist's local sentences don't match goes to your LLM agent (e.g. Claude Haiku): slower, and
cloud-dependent. Assist Learner sits between Assist and the LLM, logs what the LLM did, and every night proposes
local sentence templates so the same kind of request is handled locally next time.

- **Learns** music requests ("put on X by Y in the living room") from what the LLM did via Music Assistant
- **Review inside HA**: Repairs issues + a sidebar panel (approve / edit / reject, bulk actions, test box)
- **Sentences page**: see and edit every learned/custom sentence, with version history and revert
- **Natural-language commands**: describe a command, pick entities, the linked LLM drafts sentences and the spoken
  response; you review and approve. Commands can answer from sensors or **act** (strictly allow-listed)
- **Import** simple custom sentence files + `intent_script` entries as managed commands
- Everything the LLM produces is **validated deterministically** (nothing is trusted) and approved by you

Design: [`docs/technical-plan.md`](docs/technical-plan.md). Contributor/agent rules: [`AGENTS.md`](AGENTS.md),
non-obvious findings: [`LEARNINGS.md`](LEARNINGS.md).

## Requirements
Home Assistant 2026.11+, an LLM conversation agent (e.g. the Anthropic integration), Music Assistant for the music
learner, and an **AI Task** entity for authoring commands.

## Install
1. In HA, add this repository as a **custom repository** in the Marketplace (HACS), category *Integration*, and
   install **Assist Learner**. Restart Home Assistant.
2. Settings -> Devices & services -> Add integration -> **Assist Learner**. Pick the target LLM agent (e.g.
   `conversation.claude_haiku`) and language.
3. In **Settings -> Voice assistants**, point your pipeline's conversation agent at `conversation.assist_learner` and keep
   **Prefer handling commands locally** switched on.
4. Open the integration's **Configure** to set the nightly time, thresholds, the AI Task entity (needed for
   authoring), default/preferred players and safety limits.

## Using it
| What | Where |
|---|---|
| Review proposals learned overnight | Sidebar **Assist Learner -> Pending**, or Settings -> System -> Repairs |
| See/edit/disable everything | **Sentences** tab (filter learned / custom / external) |
| Create a command from plain English | **New command** tab: describe, pick entities, preview the exact LLM payload, Generate |
| Check what a sentence would do | The **test box** at the top of the panel (nothing is executed) |
| Run the learner now | **Run proposer now**, `button.assist_learner_run_now`, or `assist_learner.run_proposer` |
| Script it | `assist_learner.approve/reject/disable_learned/delete_learned/create_command/generate_command/decide_command` (admin) |

Sensors: `sensor.assist_learner_pending_proposals` (list in attributes), `..._llm_fallbacks_today`,
`..._local_hits_today`.

## Safety model
- Proposals and commands are **never active until you approve** (auto-approve is off by default and only ever applies
  to learned music templates, never to custom commands).
- Action commands: only allow-listed services on the entities you picked; **locks, alarms, sirens, valves,
  garage/gate/door covers, shell/python/supervisor/core services are blocked with no override**. No free-text
  wildcards, slot bounds enforced, per-command rate limit, calls attributed to the voice request, full execution log,
  re-validation at every start.
- Response templates are statically analysed: only literal entity ids from the command's own list, allow-listed
  functions/filters. A content hash is checked at runtime.
- Privacy: raw requests are deleted after the nightly run by default (retention is configurable); diagnostics redact
  raw text, prompts, and track/artist data. Only the entities you select are sent to the LLM, and the panel shows
  the exact payload first.

## Development
```bash
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/pytest            # 140+ tests
.venv/bin/ruff check .
cd tests/frontend && npm i jsdom@24 --no-save && node panel_smoke.mjs   # panel smoke test
python -m custom_components.assist_learner.replay misses.json            # offline proposer replay
```
Status: see "Known gaps" in [`LEARNINGS.md`](LEARNINGS.md).
