# Assist Learner

A Home Assistant custom integration that **learns local voice commands from LLM fallbacks** and lets you
**author new local commands in plain English**.

Today, anything Assist's local sentences don't match goes to your LLM agent (e.g. Claude Haiku): slower, and
cloud-dependent. Assist Learner sits between Assist and the LLM, logs what the LLM did, and every night proposes
local sentence templates so the same kind of request is handled locally next time.

- **Learns** music requests ("put on X by Y in the living room") from what the LLM did via Music Assistant
- **Review inside HA**: Repairs issues + a sidebar panel (approve / edit / reject, bulk actions, test box)
- **Sentences page**: see and edit every sentence Home Assistant knows about (learned, custom, and your own
  sentence-trigger automations and `custom_sentences` files) with a **Builder** or raw **YAML** editor, version
  history and revert
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
1. Copy `custom_components/assist_learner/` into `<config>/custom_components/` and restart Home Assistant.
   (HACS can install it as a custom repository, but only if the repo is public: HACS cannot read private repos.)
   To update, copy the folder again and restart.
2. Settings -> Devices & services -> Add integration -> **Assist Learner**. Pick the target **LLM** agent (e.g. your
   Claude agent) and language. Home Assistant's own local agent and Assist Learner itself are not offered: forwarding
   to the local agent makes every request fail instantly and nothing is learned. To change the target later use the
   integration's three-dot menu -> **Reconfigure** (learned data is kept). Old installs pointing at the local agent get
   a Repairs issue.
3. In **Settings -> Voice assistants**, point your pipeline's conversation agent at `conversation.assist_learner` and keep
   **Prefer handling commands locally** switched on. Only requests that reach Assist Learner are recorded.
4. Open the integration's **Configure** to set the nightly time, thresholds, the AI Task entity (needed for
   authoring), default/preferred players, retention and safety limits.
5. The **Assist Learner** sidebar entry (admin only) appears once the integration is added. Nothing appears
   until step 2 is done.

**Which Claude model?** Add the Anthropic integration, create a conversation agent (and optionally an AI Task) with the
model you want, then select that agent/entity in Assist Learner. The add-on has no model setting of its own.

## Using it
| What | Where |
|---|---|
| Review proposals learned overnight | Sidebar **Assist Learner -> Pending**, or Settings -> System -> Repairs |
| See/edit/disable everything | **Sentences** tab (filter: learned / custom / home assistant). **Edit** opens a Builder / YAML editor (below) |
| Create a command from plain English | **New command** tab: describe, pick entities, preview the exact LLM payload, Generate |
| Check what a sentence would do | The **test box** at the top of the panel (nothing is executed). Checks automation sentence triggers first, then intents, like the voice pipeline does |
| Run the learner now | **Run proposer now**, `button.assist_learner_run_now`, or `assist_learner.run_proposer`. The result explains what happened to each request (failed, not learnable, waiting for repeats, ...) |
| Script it | `assist_learner.approve/reject/disable_learned/delete_learned/create_command/generate_command/decide_command` (admin) |

### Editing sentences (Builder / YAML)
Every row you can edit opens a **Builder | YAML** switch. Switching converts your unsaved draft either way; a YAML
error keeps you where you are. Saving always re-validates.

| Kind | Builder edits | Notes |
|---|---|---|
| Custom command | name, type, sentences, slots (number / choice), entities, actions, responses | full safety validation on save |
| Learned template | the template (slots shown) | |
| **Home Assistant** automation | alias + the sentences of each conversation trigger | written **in place** to `automations.yaml`, then automations reload. Conditions/actions are untouched. Only automations stored in `automations.yaml` are editable; package/include ones are read-only |
| **Home Assistant** sentence file | the sentences of each block in `custom_sentences/<lang>/*.yaml` | written in place, then Assist reloads; other keys kept. Comments in the edited file are lost (as with HA's own editor) |
| `conversation: intents:` in configuration | read-only | edit it in your configuration |

Every sentence is syntax-checked before anything is written. This is a deliberate exception to "never edit your files".

### Where sentences come from (and what runs first)
The voice pipeline tries, in order: automation **sentence triggers**, then local intents (built-in and
`custom_sentences`), then the conversation agent. A trigger or local match never reaches Assist Learner or the LLM.
Home Assistant only loads `custom_sentences/<language variant>/`: `en-GB` reads `custom_sentences/en/`. Assist Learner
follows this for reading and writing.

Sensors: `sensor.assist_learner_pending_proposals` (list in attributes), `..._llm_fallbacks_today`,
`..._local_hits_today`.

## Playing music locally on the device you spoke to
Learned music templates play on a player chosen by: spoken player, the device's own player, same-area player, default.
For "play <anything>" with Music Assistant, library-first search, areas and failure announcements, use the example
automation and script: **[`docs/play-music-recipe.md`](docs/play-music-recipe.md)**.

Problems? See **[`docs/troubleshooting.md`](docs/troubleshooting.md)**.

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
.venv/bin/pytest            # 170+ tests
.venv/bin/ruff check .
cd tests/frontend && npm i jsdom@24 --no-save && node panel_smoke.mjs   # panel smoke test
python -m custom_components.assist_learner.replay misses.json            # offline proposer replay
```
Status: see "Known gaps" in [`LEARNINGS.md`](LEARNINGS.md).
