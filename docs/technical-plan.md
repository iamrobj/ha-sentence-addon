# Assist Learner: Technical Plan

**A Home Assistant custom integration that learns local voice commands from LLM fallbacks**

| | |
|---|---|
| Status | Ready for approval. All design decisions are resolved (see §8) |
| Estimate | About 7 weeks for v1 (36 working days); the learning loop alone could ship as 0.1 after about 3 weeks |
| Target | Home Assistant **2026.11** (current stable, released 4 Nov 2026), minimum supported version 2026.11.0 |
| Distribution | Private GitHub repo, installed via the built-in **Marketplace** (HACS was folded into core in 2026.11) as a custom repository |
| Domain | `assist_learner` |
| First intent family | Music playback through Music Assistant ("play X by Y on Z") |

---

## 1. Goal

Today, any voice request that the strict local sentences don't match goes to Claude Haiku. That works, but it's slower than local handling and depends on the cloud.

Assist Learner will:

1. Sit between Assist and Haiku, and log every request that fell through to the LLM along with the action Haiku took.
2. Every night, turn those logged requests into proposed local sentence templates, or proposed updates to templates it learned earlier.
3. Show the proposals inside Home Assistant so you can approve, edit or reject each one.
4. On approval, write the template to a custom sentences file and register a local handler, so the same kind of request is handled locally from then on.
5. Expose settings (schedule, thresholds, auto-approve, which intents to learn and so on) in the integration's options, so it can grow beyond music later.
6. Provide a **Sentences** page where you can see and edit every sentence the integration manages, and **create new local commands in plain English**. You describe what you want ("what's the weather like outside, using the outdoor temperature sensor"), pick the entities, and the linked LLM drafts the sentences and the spoken response for you to review and approve. Commands can also **do things** (e.g. "good night" turns off chosen lights), with strict safeguards.

### Non-goals for v1

- Learning intents other than music playback. The design supports more, but v1 only ships the music learner.
- **Commands that control high-risk devices** (locks, alarm panels, garage doors and gates, sirens, water valves) and anything that runs shell or Python code. These are blocked outright in v1 with no override (see §3.7.8).
- **Importing sentence-trigger automations.** They're shown read-only. Importing sentence *files* is in v1 (see §3.7.6).
- Languages other than English. The language is a setting, but v1 is only tested in English.
- Replacing the Music Assistant local blueprint. Assist Learner works alongside it and won't propose anything the blueprint already handles.

---

## 2. How it fits into Assist

```
Voice PE / phone / typed text
            │
            ▼
   Assist pipeline  ── "Prefer handling commands locally" = ON
            │
            ├─► Built-in sentences + custom_sentences (incl. learned ones)
            │        │ matched → handled locally (no LLM)  ✅
            │        │
            │        └─ no match
            ▼
   conversation.assist_learner  (proxy agent, this integration)
            │  1. forward request to the target agent
            ▼
   conversation.claude_haiku   (Anthropic integration)
            │  2. Haiku calls a tool, e.g. music_assistant play / script
            ▼
   Music Assistant plays the song
            │
            ▼
   Proxy records: text, language, device, area, tool calls, resolved
   service call(s), success/failure  →  Miss log (.storage)

   Nightly at 03:15 ─► Proposer ─► Proposals ─► Repairs / panel ─► Approve
                                                                 │
                                    custom_sentences/en/assist_learner.yaml
                                    + Python intent handler   ◄──┘
```

The pipeline's "prefer local" step runs before the proxy agent is called. So every request that reaches the proxy is, by definition, one the local sentences missed. The proxy doesn't need to work that out itself.

---

## 3. Components

### 3.1 Proxy conversation agent (`conversation.py`)

- A `ConversationEntity` subclass with `supported_features = ConversationEntityFeature.CONTROL` and `supported_languages` copied from the target agent.
- Implements `_async_handle_message(user_input, chat_log)`, the current entry point. The older `async_process` is still backward compatible but shouldn't be used.
- Forwards to the target agent configured in options (e.g. `conversation.claude_haiku`) with `conversation.async_converse(...)`, passing through `text`, `conversation_id`, `context`, `language`, `device_id` and `satellite_id`, then returns the target's `ConversationResult` unchanged. Follow-up questions ("which speaker?") keep working because the conversation id is passed through.
- Captures what Haiku did in two ways, so one can back up the other:
  1. **Chat log:** reads the `AssistantContent.tool_calls` and `ToolResultContent` entries added during this turn.
  2. **Event bus:** a short-lived listener on `call_service` events whose context descends from `user_input.context`. This catches the final `music_assistant.play_media` call even when Haiku went through a script or `HassMediaSearchAndPlay`.
- Writes one **miss record** per request (see 3.2). Recording happens in a background task, so it never delays the spoken reply.
- If the target agent is missing or errors, the proxy returns that error unchanged and records it as a failed miss. It never swallows the response.

**Pipeline change you make once:** point the Assist pipeline's conversation agent at `conversation.assist_learner` instead of Haiku, and keep "Prefer handling commands locally" turned on.

### 3.2 Storage (`store.py`)

This uses `homeassistant.helpers.storage.Store`, which writes versioned JSON files to `.storage/`, with a migration hook for each schema version. At about 20 requests a day the volume is tiny, so there's no need for SQLite or the recorder.

| Store | Contents |
|---|---|
| `assist_learner.misses` | Miss records: id, timestamp, raw text, normalised text, language, device/area, target agent, tool calls, resolved actions (`domain.service` + data + targets), success flag, `processed_by_proposal_id` |
| `assist_learner.proposals` | Proposals: id, type (`new` / `update`), intent family, template, slot map, up to 3 example requests (raw text, kept only while the proposal is pending), occurrence count, confidence score, status (`pending` / `approved` / `rejected` / `superseded`), created/decided timestamps, reviewer note |
| `assist_learner.learned` | Active learned templates, with hit counters (how often each was used locally) and an enabled flag |
| `assist_learner.candidates` | Running counts per template fingerprint (template, count, first and last seen, confidence). Holds **no raw text**, so counts survive even when raw requests are deleted |
| `assist_learner.executions` | Audit log of every action-command run: time, command, originating device, service calls made, result. Capped (default 500 entries) |
| `assist_learner.rejected` | Fingerprints of rejected templates, so they're never proposed again |
| `assist_learner.commands` | User-authored custom commands: id, name, your plain-English description, selected entities (with a role/label each), generated sentences, response template, status (`draft` / `pending` / `active` / `disabled`), version history (every edit and regeneration, so you can revert), hit counters |
| `assist_learner.generation_log` | The last N LLM generation requests and responses (prompt, structured result, validation outcome), for debugging. Kept for the raw-text retention period and redacted in diagnostics |

Retention: raw request text is deleted after the nightly run has processed it (default), or kept for a configurable number of days (0 to 365, set in options). A proposal keeps up to 3 example requests only while it is pending, and they're deleted when you decide on it. Learned templates and candidate counts never contain raw text. Proposals, learned templates, custom commands and the execution log are kept until you delete them (the execution log is capped). Because raw text is deleted by default, the replay harness (§6) needs a retention above 0 or an exported file.

### 3.3 Nightly proposer (`proposer/`)

Scheduled with `async_track_time_change` at the configured time (default 03:15 local). It can also be run on demand with the `assist_learner.run_proposer` action.

This is a pluggable design. Each intent family is a **Learner** class:

```python
class Learner(Protocol):
    family: str                                      # "music_play"
    def accepts(self, miss: MissRecord) -> bool: ...
    def extract(self, miss: MissRecord) -> Candidate | None: ...
    def handler(self) -> intent.IntentHandler: ...   # local runtime handler
```

**Music learner pipeline (v1):**

1. **Filter:** keep successful misses whose resolved actions include `music_assistant.play_media` (or `media_player.play_media` on an MA player).
2. **Pull out slot values** from the resolved call: track (`media_id` / `track`), artist (`artist`), media type, and the target player entity → its area and friendly name.
3. **Align them with the text:** normalise the text (lowercase, strip punctuation, unify apostrophes) and find each slot value in it. Speech-to-text often mangles names ("teddy swim's" vs "Teddy Swims"), so fuzzy matching is used (`difflib` ratio ≥ 0.8, token-based). If any slot value can't be found, the miss is skipped and flagged as "unalignable" so you can see it.
4. **Templatise:** replace the matched spans with `{track}`, `{artist}` and `{player}`, which gives a carrier phrase like `put on {track} from {artist} in the {player}`. If the player in the resolved call matches the speaking device's own player or area (i.e. the user didn't name a speaker), the miss yields a template **without** the player part, e.g. `put on {track} from {artist}`. Misses with and without a spoken speaker are proposed as separate templates, and the handler resolves the target as described in 3.4.
5. **Generalise lightly:** strip fillers ("please", "can you", "hey") into optional groups `[please]`, and fold "the" / "my" in front of `{player}` into optionals. This step is deterministic and needs no LLM.
6. **Group and count:** group candidates by template fingerprint and add to the running counts in `assist_learner.candidates`. Propose a template once its count ≥ `min_occurrences` (default **1**, so every distinct phrasing is proposed straight away; raise it in options if it gets noisy). To keep a low threshold manageable, each candidate gets a **confidence score** (alignment quality, how clean the template is, whether it passed all checks), proposals are ranked by it, and at most **10 new proposals per night** are created (a setting), highest confidence first. The rest wait for the next run.
7. **Run safety checks** (any failure means the proposal is dropped or marked ⚠):
   - **Already local?** Fill in sample values and run the sentence through the default agent's recognizer. If it already matches (the built-ins, the MA blueprint, or an earlier learned template), drop it.
   - **Too greedy?** Fill the template with non-music probe values such as "the kitchen lights" or "a timer" and test it against existing intents. If it would capture a command that currently matches something else, block it.
   - **Literal anchor:** the template must contain at least two fixed words, with at least one of them a verb from the allowed list (play, put on, stick on, queue, start…). This stops templates like `{track} by {artist}`.
   - **Wildcard boundary:** two wildcards must be separated by a literal word ("by", "from"). Titles that contain the separator ("Stand by Me by Ben E. King") are flagged in the proposal as a known ambiguity.
8. **Decide new vs update:** if the candidate differs from an existing learned template only by optional words or a synonym alternation, emit an **update** proposal showing the diff, e.g. `(play|put on) {track} by {artist}`, instead of a new template.
9. Emit the proposals, mark the source misses as processed, and fire the `assist_learner_proposals_created` event.

**Optional LLM generalisation (off by default):** if an AI Task entity is selected in options, ask it via `ai_task.generate_data` with a structured output schema to suggest alternations for a proposal. Its output goes through the same safety checks and is never applied without approval. The cost is negligible on Haiku.

### 3.4 Local runtime: sentences and handler (`sentences.py`, `handlers.py`)

Approved templates go into a file the integration owns: **`/config/custom_sentences/<lang>/assist_learner.yaml`**. The file is fully regenerated from `assist_learner.learned` on each change, and starts with a "do not edit" header. Blueprint files are never touched.

```yaml
# Generated by Assist Learner — edits will be overwritten. Manage in Settings → Assist Learner.
language: en
intents:
  AssistLearnerPlayMedia:
    data:
      - sentences:
          - "put on {track} from {artist} [in|on] [the] {player}"
          - "(play|stick on) {track} by {artist} [on|in] [the] {player}"
          - "put on {track} from {artist}"          # no speaker: handler uses the calling device
        slots:
          media_type: track
lists:
  track:  { wildcard: true }
  artist: { wildcard: true }
  player:
    values:            # regenerated from exposed MA players + their areas
      - in: "living room speaker"
        out: media_player.living_room
      - in: "living room"
        out: media_player.living_room
```

- **Handler:** `AssistLearnerPlayMedia` is registered in Python with `intent.async_register`, not as YAML `intent_script`. That means one place handles the logic: resolving the player (see below), calling `music_assistant.play_media` with `media_type`, `media_id` and `artist`, building the spoken reply ("Playing Lose Control by Teddy Swims in the living room"), and incrementing the template's hit counter.
- **Why the handler does its own device targeting:** Home Assistant's built-in "play X" sentences already send playback to the area of the device you spoke to, but that only applies to the built-in intent, which can't express "by artist". Learned commands run through this handler, so it has to implement the same behaviour itself. The intent object carries the originating `device_id` (and satellite, when present), which is what makes this possible.
- **Player resolution order:**
  1. **Spoken player:** if `{player}` was said ("…on the kitchen speaker"), use it. A spoken name always wins.
  2. **Calling device's own player:** if the device you spoke to (e.g. a Voice PE) has a media player that is a Music Assistant player and is exposed to Assist, use it.
  3. **Same-area player:** otherwise, find the calling device's area and use the exposed Music Assistant player in that area. If there are several, use the **preferred player for that area** from the options, and if none is set, reply asking which speaker.
  4. **No device:** for requests with no originating device (typed text from the phone app, API calls), use the **default player** from the options. If none is set, reply asking which speaker.
  - If the device has no area and no player of its own, the handler skips to step 4 and says so in the reply ("I don't know which room you're in").
- **Player list:** built from Music Assistant `media_player` entities that are **exposed to Assist**. It's rebuilt when the entity, area or exposure registries change.
- **Reload:** after writing, call the `conversation.reload` action so the new sentences take effect with no restart.
- **Failure behaviour:** if Music Assistant finds no match, the handler returns an error response ("I couldn't find Lose Control by Teddy Swims"). It does **not** fall back to the LLM, because the pipeline has already committed to the local handler. That limitation is noted in the docs.

### 3.5 Review UI

**v1: Repairs + entities + actions (native, low effort)**

- Each pending proposal appears as a **Repairs issue** (`ir.async_create_issue`, `is_fixable=True`, severity `warning`) under Settings → System → Repairs, titled e.g. *"New local command: put on {track} from {artist} in the {player}"*.
- **Fix flow** (`RepairsFlow`):
  1. Info step: shows the template, the type (new or update, with the diff), the occurrence count, three example raw requests, what Haiku did each time, and any ⚠ warnings.
  2. Action step: a menu with **Approve**, **Edit then approve** (text field, re-validated on submit) or **Reject** (optional note).
- Entities:
  - `sensor.assist_learner_pending_proposals`: the count, with the proposal list in its attributes so you can build a dashboard Markdown card.
  - `sensor.assist_learner_llm_fallbacks_today` and `sensor.assist_learner_local_hits_today`: show whether it's working.
  - `button.assist_learner_run_now`.
- Actions: `assist_learner.approve`, `assist_learner.reject`, `assist_learner.run_proposer`, `assist_learner.disable_learned`, `assist_learner.delete_learned`. All are admin-only.
- Optional persistent notification or mobile push when new proposals are created (an options setting).

**Sidebar panel (v1, required for the Sentences page and authoring)**

Repairs can show a proposal and offer buttons, but it can't show a searchable list, an inline editor, an entity picker or a test box. Since you want to browse and edit every sentence and author new ones, the panel is now part of v1, and Repairs becomes the notification route.

- A custom panel registered with `panel_custom` (a Lit web component bundled with the integration), admin-only, backed by a websocket API.
- Tabs:
  - **Pending:** proposals from the nightly job and generated custom commands waiting for approval, with approve / edit / reject / regenerate, plus bulk approve and reject.
  - **Sentences:** everything the integration manages, in one list (see 3.7.6).
  - **New command:** the natural-language authoring form (see 3.7).
  - **Misses:** the raw fallback log, including "unalignable" ones.
  - **Stats:** local hits vs LLM fallbacks, per template and per command, plus the action execution log.
- A **test box** on every page: type a sentence and see which intent or command would match it, with the slots it extracts, without running anything.
- Repairs issues are kept as the notification route and link into the panel.
- The panel uses Home Assistant's own entity picker where it's available and falls back to a plain searchable list built from `hass.states` if the frontend component changes. This keeps the panel from breaking on a frontend update.

### 3.6 Settings (config flow + options flow)

**Config flow (setup):** choose the target conversation agent and the language. The setup checks that the target agent supports CONTROL and that Music Assistant is loaded, and warns if it isn't.

**Options flow, set up for future families:**

| Setting | Default | Notes |
|---|---|---|
| Target conversation agent | — | The agent misses are forwarded to |
| Nightly run time | 03:15 | Local time |
| Minimum occurrences before proposing | 1 | 1–10 |
| Look-back window | 14 days | Only misses newer than this are considered |
| Auto-approve | Off | When on, learned music templates that pass all checks with ≥ N occurrences are approved automatically and you're notified. **Never applies to custom commands** |
| Auto-approve threshold | 5 | |
| Enabled learners | `music_play` | Multi-select; future: `light_control`, `timer`, `scene` |
| Raw request retention | 0 (delete after the nightly run) | 0–365 days. 0 deletes each request as soon as the nightly run has processed it |
| Max new proposals per night | 10 | Highest confidence first |
| Notify on new proposals | Persistent notification | None / persistent / `notify.*` target |
| AI Task entity (the linked LLM) | None | **Required** for custom-command authoring. Also used for the optional generalisation step in the nightly job |
| Send current entity states to the LLM | On | Off sends only names, units and device classes, which gives slightly less natural responses |
| Max sentences per custom command | 12 | |
| Panel admin only | On | Locked on in v1 |
| Action services allow-list | Default list in §3.7.8 | Can be narrowed, and extended only within the non-blocked set |
| Max action runs per minute, per command | 6 | Stops runaway loops or repeated mishearings |
| Execution log size | 500 entries | |
| Allowed carrier verbs | play, put on, stick on, queue, start | Editable list |
| Default player | None | Used when a request has no originating device and no spoken speaker |
| Preferred player per area | None | Used when several Music Assistant players share an area |
| Prefer calling device's own player | On | Step 2 of player resolution; turn off to always use an area player |

The options are stored in `entry.options`, and a version on the config entry allows migrations (`async_migrate_entry`).

### 3.7 Sentences page and natural-language custom commands

This adds two things: a single place to see and edit sentences, and a way to create new local commands by describing them.

#### 3.7.1 The idea, with your example

1. On the **New command** tab you type what you want in plain English: *"When I ask what the weather's like outside, answer with the outdoor temperature."*
2. You optionally add one or more example phrasings (*"what's the weather like outside"*) and **pick the entities** the answer should use, e.g. `sensor.outdoor_temperature`. You can add several (humidity, a `weather.*` entity) and give each a short label.
3. You press **Generate**. The linked LLM returns proposed sentences and a spoken response template that uses only your chosen entities.
4. The proposal appears in **Pending** with a live preview using real current values (*"It's 7 degrees outside"*). You can approve, edit by hand, **regenerate with feedback** ("also mention humidity", "shorter"), or reject.
5. On approval, the command becomes a local sentence. It's handled locally from then on, with no LLM call.

For a command that *does* something ("when I say good night, turn off these lights"), you describe what should happen, and the proposal also shows the exact service calls it would make (see 3.7.8).

#### 3.7.2 What the command is made of

| Part | Details |
|---|---|
| Name and description | Your words, kept so the LLM can be re-asked later and so you remember why it exists |
| Type | `answer` (reads entities and speaks a response), `action` (calls services, then speaks a confirmation), or `answer_and_action` |
| Entities | An explicit list of entity ids, each with a label (e.g. `outdoor_temp`). This is also the **allow list**: the response can only read these entities, and actions can only target them |
| Slots | Optional typed slots used in sentences: `number` (with min, max and step, e.g. brightness 0–100) or `choice` (a fixed list of values, e.g. scene names). No free-text wildcards in action commands |
| Actions | For `action` types: an ordered list of service calls. Each has a service name, targets chosen only from the entity list, and data that is a fixed value or a slot reference |
| Sentences | Home Assistant sentence syntax: alternations `(a\|b)`, optionals `[the]`, and slots. No wildcards unless you add one yourself |
| Response template | A Jinja template that reads only the allowed entities, e.g. `It's {{ states('sensor.outdoor_temperature') \| round(0) }} degrees outside.` Optional second variant for when an entity is `unavailable` |
| Language | From the integration setting |

#### 3.7.3 LLM generation (`commands/generate.py`)

- **Which LLM:** the single **AI Task entity** chosen in the integration's options. It's the same one the nightly job can use for generalisation, so there's one LLM setting to manage. Calls go through `ai_task.generate_data` with a structured-output schema.
- **What is sent to the LLM:** your description and example phrases, the selected entities' friendly names, domains, units, device classes and (by default) current states, the HA language, the sentence-syntax rules, the list of existing sentences it must not collide with, and, for action commands, the allowed services for the selected entities' domains with their field descriptions (so it can only propose valid calls). Nothing else about your home is sent. The panel shows exactly what will be sent before you press Generate, and a setting turns off sending current states (names and units only).
- **What comes back** (structured result):

```json
{
  "type": "answer",
  "sentences": ["(what's|what is) the weather [like] outside", "how is it outside", "..."],
  "slots": {},
  "response_template": "It's {{ states('sensor.outdoor_temperature') | round(0) }} degrees outside.",
  "unavailable_template": "I can't read the outdoor sensor right now.",
  "entity_roles": { "outdoor_temp": "sensor.outdoor_temperature" },
  "actions": [],
  "notes": "Short explanation of the choices, shown in the proposal.",
  "warnings": []
}
```

An action command comes back in the same shape, for example:

```json
{
  "type": "action",
  "sentences": ["turn the lounge lights to {level} percent"],
  "slots": { "level": { "type": "number", "min": 0, "max": 100 } },
  "entity_roles": { "lounge_lights": "light.lounge" },
  "actions": [
    { "service": "light.turn_on", "targets": ["lounge_lights"], "data": { "brightness_pct": "{level}" } }
  ],
  "response_template": "Lounge lights set to {{ slots.level }} percent."
}
```

- **Regenerate with feedback:** your feedback and the previous result are sent back, so you can steer it ("also say the humidity", "make the sentences more casual").
- **Cost:** a few hundred input tokens per generation, so a fraction of a cent on Haiku.

#### 3.7.4 Deterministic validation (nothing from the LLM is trusted)

Every generated or hand-edited command goes through the same checks, and any failure blocks approval and is shown in the panel:

- **Sentences parse** with `hassil`, and there are at least 2 and at most a configurable maximum.
- **Collision check:** each sentence is filled with sample values and tested against the recognizer. If it already matches a built-in or custom intent (the built-in weather sentences, for example), the collision is shown with what it matches. You can narrow the phrasing, or accept the overlap if the Phase 0 spike confirms which one wins.
- **Greediness probes:** no sentence may match a probe set of unrelated commands ("turn on the kitchen lights", "set a timer"). Wildcards are rejected unless literal anchor words surround them.
- **Template safety (static analysis of the Jinja):** the template is parsed to an AST, and only an allow-listed set of functions is permitted (`states`, `state_attr`, `is_state`, `is_state_attr`, filters like `round`, `float`, `int`, `default`, `title`). Every entity id must be a literal and must be in the command's entity list. Anything else (other entities, `now()` side effects, `states` with a variable id, service calls, loops over all states) fails validation.
- **Action validation (action commands):**
  - The service must be on the allow-list and not on the hard-blocked list (see 3.7.8).
  - Every target must be a role from the command's entity list, and its domain must match the service's domain.
  - The data is validated against the live service description (field names, types, ranges, supported features of the entity). Values must be literals or a `{slot}` reference, never templates, and slot values are bounded by the slot's min and max.
  - Action commands can't use wildcard slots.
  - **Dry run only:** validation checks the schema and that the entities exist and support the call. Nothing runs during approval.
- **Render test:** the template is rendered with live values; it must produce non-empty text without errors. A second render with the entities set to `unavailable` checks the fallback.
- A content hash of the validated version is stored. If the stored template changes outside the panel, the command is disabled until it's re-validated.

#### 3.7.5 Runtime

- Approved commands go into the same generated sentences file as learned templates, under one intent, `AssistLearnerCustomCommand`, with a `command_id` slot in the YAML so each sentence points at its command:

```yaml
intents:
  AssistLearnerCustomCommand:
    data:
      - sentences:
          - "(what's|what is) the weather [like] outside"
          - "how is it outside"
        slots:
          command_id: "cmd_7f3a"
```

- The handler (`handlers.py`) looks up the command, re-checks that it's enabled and its hash matches, renders the response template through Home Assistant's template helper, and returns it as the spoken reply. It increments the hit counter. For action commands it first validates slot values against their bounds, then makes each service call in order with `blocking=True` and the request's own context (so the logbook attributes the change to the voice request), stops on the first failure and says so, and enforces the per-command rate limit. The spoken reply is rendered only after the calls succeed. Every run is written to the execution log.
- The command's entities are **not** required to be exposed to Assist. Selecting them in the panel is the explicit grant, shown on the approval screen. (Answer commands can only read. Action commands can only target the entities in their list.)

#### 3.7.6 Sentences tab: see and edit everything

One list, filterable by source:

| Source | What it shows | Editable? |
|---|---|---|
| **Learned** | Music templates approved from the nightly job | Yes: edit the template, enable/disable, delete |
| **Custom** | Commands you authored | Yes: edit sentences, response, entities; regenerate; enable/disable; delete |
| **External** (read-only) | Sentence files found in `/config/custom_sentences/` that this integration doesn't manage, and automations that use sentence triggers | No. Shown so you can see collisions, with a link to the file or automation. Sentence files can be **imported** (below); sentence-trigger automations can't |

**Import (v1) for sentence files:**
- Import works on custom sentence files whose intents are backed by `intent_script` entries in your configuration. It reads the sentences and lists, plus the matching `intent_script` actions and speech. If the actions are plain service calls and the speech is a template over the selected entities, they become a managed `answer`, `action` or `answer_and_action` command that goes through the same validation as anything you author (blocked services, the entity allow-list and so on).
- Anything that can't be mapped cleanly (`choose`, `repeat`, `wait`, templated service names, calls to blocked services) is listed with the reason and stays external.
- Import never edits or deletes your files. (Separately, and only when you press Save in the Sentences tab, Home Assistant-owned sentences can be edited in place: the sentences and alias of a sentence-trigger automation in `automations.yaml`, or an intent's blocks in a hand-written `custom_sentences` file. Decision 2026-11-13.) After an import, the original and the managed copy both match, so the panel flags a collision until you remove the original, with a link to the exact file and block.
- Sentence-trigger automations (`conversation` triggers) are shown read-only with their sentences for collision checks. They can't be imported because the automation owns the trigger, and importing would make the same sentence fire twice.

Each row shows its sentences, response or action, hit count and last-used time, enabled toggle, and collision warnings. Editing a managed item re-runs validation and opens a test box. Every change is saved to the item's **version history** so you can see what changed and revert.

#### 3.7.7 Websocket API additions

`assist_learner/sentences/list`, `.../test`, `assist_learner/commands/create`, `.../generate`, `.../regenerate`, `.../update`, `.../decide`, `.../delete`, `.../revert`, `.../history`, `.../run_once`, `assist_learner/executions/list`, `assist_learner/import/discover`, `.../import/preview` and `.../import/apply`. All require admin. Actions mirror these (`assist_learner.generate_command`, `assist_learner.decide_command`, and so on) so they can be scripted.

#### 3.7.8 Action commands (in v1, with strict safeguards)

An `action` command lets the LLM propose service calls on the entities you selected (e.g. "good night" turns off these lights, "set the lounge lights to 40 percent"). Because a misheard sentence could trigger one, the safeguards are layered:

**Allowed by default** (and only on entities in the command's own list): `light`, `switch`, `fan`, `input_boolean`, `media_player` (transport, volume and power, not arbitrary `play_media` URLs), `climate` (within the entity's own min and max), `vacuum`, `cover` (excluding garage doors, gates and doors), `scene.turn_on`, `script.turn_on` and `automation.trigger`. Scripts and automations run with full power, so the approval screen flags them with the script's name and a link to it. The LLM can't create or edit them.

**Hard-blocked in v1, with no override:** `lock`, `alarm_control_panel`, `siren`, `valve`, `cover` entities with a garage, gate or door device class, `shell_command`, `python_script`, `hassio`, `homeassistant` (restart, stop, reload) and `persistent_notification`. A command that touches these fails validation.

**Approval screen:** shows every service call exactly as it would run (service, target entity, data), the slot ranges, the sentences that would trigger it, any collisions, and a ⚠ for scripts and automations. You approve the calls themselves, not just a description. After approval, a **Run once** button (admin only, with a confirm) lets you test it.

**Runtime safeguards:** approved sentences only (no free-text wildcards), slot bounds enforced, per-command rate limit (default 6 a minute), calls attributed to the voice request, stop on first failure with a spoken error, a per-command enable toggle, and an execution log in the panel. Auto-approve never applies to custom commands, so every one is approved by you.

**Re-validation:** commands are validated against the live service descriptions when you approve them and again at each Home Assistant start. If a service or entity changes so a command no longer validates, it's disabled and you get a notification.

---

## 4. Repository layout

```
custom_components/assist_learner/
├── __init__.py            # setup/unload, scheduler, registry listeners
├── manifest.json
├── config_flow.py         # config + options flow
├── const.py
├── conversation.py        # proxy ConversationEntity
├── capture.py             # chat-log + call_service capture
├── store.py               # Store wrappers + migrations
├── models.py              # dataclasses: MissRecord, Proposal, LearnedTemplate
├── proposer/
│   ├── __init__.py        # run_proposer orchestration
│   ├── base.py            # Learner protocol
│   ├── music.py           # music_play learner
│   ├── align.py           # normalise + fuzzy slot alignment
│   └── validate.py        # recognizer checks, greediness probes
├── sentences.py           # YAML writer + conversation.reload
├── intent.py              # AssistLearnerPlayMedia handler
├── repairs.py             # RepairsFlow for proposals
├── sensor.py
├── button.py
├── services.yaml
├── strings.json
├── translations/en.json
├── commands/
│   ├── generate.py        # ai_task.generate_data prompts + schema
│   ├── validate.py        # hassil parse, collision/greediness checks
│   ├── template_guard.py  # Jinja AST allow-list for response templates
│   ├── actions.py         # service allow/block lists, schema validation, executor, rate limit
│   └── importer.py        # discover + convert external sentence files / intent_script
├── websocket.py           # websocket API for the panel
└── frontend/              # panel bundle (Lit)
tests/
hacs.json                  # Marketplace metadata, homeassistant: "2026.11.0"
```

**manifest.json**

```json
{
  "domain": "assist_learner",
  "name": "Assist Learner",
  "version": "0.1.0",
  "config_flow": true,
  "dependencies": ["conversation", "assist_pipeline", "repairs"],
  "after_dependencies": ["music_assistant", "ai_task"],
  "iot_class": "calculated",
  "integration_type": "service",
  "requirements": [],
  "codeowners": [],
  "documentation": "https://github.com/<you>/assist-learner"
}
```

There are no third-party requirements. Fuzzy matching uses the standard library's `difflib`, and sentence parsing uses `hassil`, which ships with core.

---

## 5. Delivery plan

| Phase | Scope | Estimate | Exit criteria |
|---|---|---|---|
| **0. Spike** | Confirm the 2026.11 APIs this design relies on (see §7), including the originating device id on intents, and which sentence wins when a custom and a built-in sentence both match, against a dev container running 2026.11.x | 1–2 days | Short spike notes, plus go/no-go on proxy capture |
| **1. Capture** | Config flow, proxy agent, chat-log + event capture, miss store, fallback sensor | 3 days | Pipeline runs through the proxy with no change in behaviour, and misses are logged with the resolved `play_media` call |
| **2. Proposer** | Alignment, templating, grouping, safety checks, proposal store, nightly schedule, run-now | 4 days | Replaying a fixture set of 50 real misses produces the expected proposals, and greedy templates are blocked |
| **3. Local runtime** | YAML writer, player list, Python intent handler, reload, hit counters | 3 days | An approved template plays the right song on the right player with no LLM call (verified in the pipeline debug view) |
| **4. Repairs + options** | Repairs fix flow, notification, actions, options flow | 2 days | A pending proposal shows in Repairs and can be approved from there |
| **5. Panel** | Sidebar panel, websocket API, Pending / Sentences / Misses / Stats tabs, test box, version history | 5 days | You can browse, edit, enable/disable and revert every managed sentence, and the test box shows what would match |
| **6. Custom commands (answers)** | New command tab, entity picker, LLM generation + regenerate-with-feedback, validation, template guard, runtime handler | 5 days | "What's the weather like outside" using your outdoor temp sensor is generated, previewed with live values, approved, and answered locally with no LLM call |
| **7. Action commands** | Action schema and slots, service allow/block lists, validation against live service descriptions, executor, rate limit, execution log, approval preview, Run once | 5 days | "Turn the lounge lights to 40 percent" is generated, shown as exact service calls, approved, and runs locally; a command targeting a blocked domain is rejected; the rate limit trips on rapid repeats |
| **8. Import** | Discover external sentence files and `intent_script` entries, mapping to managed commands, import preview, collision flagging, read-only automation view | 3 days | An existing sentence file with a simple `intent_script` imports as a managed command and passes validation; unmappable ones are listed with reasons |
| **9. Hardening** | Tests to 90%+ on proposer/runtime/validation/actions, migrations, diagnostics download (`diagnostics.py`), docs, Marketplace packaging | 4 days | CI green against `pytest-homeassistant-custom-component` pinned to 2026.11 |

**v1 total: about 7 weeks of focused work** (36 working days). Phases 1–4 (about 14 working days) could be released first as 0.1, so the learning loop is running while the panel, custom commands, action commands and import are built.

---

## 6. Testing

- **Unit:** normalisation and alignment (including STT errors such as "teddy swim's"), template building, fingerprinting, update-vs-new detection, and greediness probes.
- **Integration (pytest-homeassistant-custom-component 2026.11):** a fake target conversation agent that emits a scripted tool call and a `music_assistant.play_media` call. Tests cover player resolution (spoken, own player, area, preferred, default, no device), the proxy passing results through unchanged, capture, the nightly run, the Repairs flow, YAML output, and conversation reload followed by local recognition of the new sentence.
- **Custom command generation:** a fake AI Task entity that returns fixed structured results, including bad ones (template reading an entity that wasn't selected, a variable entity id, a sentence colliding with a built-in, a greedy wildcard sentence). Each bad case must be rejected with a clear message. Plus a live-render test with `unavailable` entities.
- **Action commands:** a fake AI Task entity returning bad actions (blocked domain, entity not in the list, out-of-range slot, wildcard slot, templated data, unknown service field). Each must be rejected. Runtime tests cover slot bounds, stop-on-first-failure, rate limiting, context attribution, the hash check, and disabling on re-validation after a service description changes.
- **Import:** fixtures of real-looking sentence files and `intent_script` blocks, including ones that can't be mapped, checking the reasons shown and that original files are never modified.
- **Replay harness:** a CLI (`python -m assist_learner.replay misses.json`) that runs the proposer offline on exported misses, so you can tune thresholds without waiting for nights to pass.
- **Manual acceptance on your instance:** say "put on Lose Control from Teddy Swims in the living room" twice → a proposal appears the next morning → approve → say it again → the pipeline debug view shows it was handled locally and Haiku wasn't called.

---

## 7. Risks and what the Phase 0 spike must confirm

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Nested agent call:** calling `async_converse` to another agent from inside `_async_handle_message` may conflict with the chat log / chat session context the outer call has open | Spike first. Fallback: get the target entity from the conversation agent manager and call its handler directly with the same `ChatLog`, so tool calls land in a log we already hold |
| 2 | **Capturing the real action:** Haiku may go through `HassMediaSearchAndPlay`, a script, or the MA blueprint's LLM script, so the tool call alone may not contain track/artist | The `call_service` context-descendant listener catches the final `music_assistant.play_media` call whichever route was taken. Spike confirms the context chain is preserved |
| 3 | **Recognizer API for safety checks** is internal to the default agent and could change between releases | Wrap it in one adapter module, pin a minimum HA version, and cover it with a test that fails loudly on upgrade |
| 4 | **Wildcard ambiguity** (titles containing "by"/"from") | The literal separator rule, ⚠ warnings in the proposal, and the MA search result decides. A wrong guess just plays the wrong track, which is easy to notice |
| 5 | **Learned sentences stealing other commands** | Greediness probes, a required verb anchor, every proposal needs approval (auto-approve off by default), and each learned template has a one-tap disable |
| 6 | **Local miss with no LLM fallback** (local handler matched but MA found nothing) | A clear spoken error. A later version could add a retry action ("ask Claude instead") |
| 7 | **HA core changes** (conversation APIs have changed several times since 2024) | Pin to 2026.11 at minimum, CI against each new beta, keep core-API calls in a small number of modules |
| 8 | **Privacy:** raw voice transcripts stored on disk | Kept on the local instance only, with retention limits, the raw-text toggle, and redaction in the diagnostics download |
| 9 | **LLM-generated Jinja reading things it shouldn't** | Static AST allow-list (literal entity ids from the command's list only, allow-listed functions), re-validation on every edit, content hash checked at runtime, and answer commands can't call services |
| 10 | **Generated sentences colliding with built-in sentences** (e.g. a built-in weather command), with unclear priority between built-in and custom sentences | Collision check in the panel with the matching intent shown. Phase 0 spike confirms which wins on 2026.11 and the panel explains it |
| 11 | **Entity states and names sent to a cloud LLM** | Only the selected entities are sent, the panel shows the exact payload before each generation, and a setting limits it to names and units |
| 12 | **Panel depends on frontend components** that can change between HA releases | Use the entity picker if present, otherwise a plain searchable list, and keep the panel's HA-specific calls in one small module |
| 13 | **A misheard sentence triggers an action** | Approved fixed sentences only, no wildcards in action commands, hard-blocked high-risk domains, slot bounds, per-command rate limit, calls attributed to the voice request, execution log, per-command disable, and auto-approve never applies to custom commands |
| 14 | **Service or entity changes between Home Assistant releases break an approved action** | Validated against live service descriptions at approval and at every start; failing commands are disabled with a notification rather than run |
| 15 | **Import mis-converts an existing intent** | Import preview before anything is created, the same validation as authored commands, original files never modified, and unmappable items stay external with a reason |
| 16 | **Minimum occurrences of 1 creates noisy proposals** | Confidence ranking, a nightly cap (default 10), rejected fingerprints never return, and auto-approve off by default |

Note: this plan is based on the published developer docs for the conversation entity (`_async_handle_message` with `ChatLog`) and the 2026.11 release notes. Behaviour specific to 2026.11 still needs to be confirmed in Phase 0 before Phase 1 starts.

---

## 8. Decisions (all resolved)

There are no open questions. Everything below is decided; any of it can still be changed before development starts.

### Decided by you

| # | Question | Decision |
|---|---|---|
| 1 | Sidebar panel in v1? | **Yes.** The Sentences page and authoring are in v1, which makes it about 7 weeks in total |
| 2 | Can custom commands control devices in v1? | **Yes**, with the safeguards in §3.7.8 |
| 3 | Send current entity states to the LLM? | **Yes**, for the selected entities only. A setting turns it off |
| 4 | External sentences? | **Read-only view plus Import** of sentence files in v1 (§3.7.6) |
| 5 | Minimum occurrences before proposing | **1**, configurable (1–10) |
| 6 | Auto-approve | **Off by default**, available in options for learned templates |
| 7 | Raw text retention | **Delete after the nightly run by default**, adjustable in settings (0–365 days) |
| 8 | Install method | **Private GitHub repo installed through the Marketplace** as a custom repository |

### Defaults I've chosen (change any you disagree with)

| # | Topic | Default | Why |
|---|---|---|---|
| 9 | Auto-approve scope | Learned music templates only; never custom commands | Anything that can act on your home is always approved by you |
| 10 | Blocked services | Locks, alarms, sirens, valves, garage/gate/door covers, shell/Python/Supervisor/core services are blocked with no override in v1 | A misheard sentence shouldn't be able to unlock a door or open a garage |
| 11 | Scripts, scenes and automations as action targets | Allowed only when you select them explicitly, flagged on the approval screen | They're your own, but they run with full power |
| 12 | Nightly proposal cap | 10 per night, highest confidence first | Keeps a minimum of 1 manageable |
| 13 | Action rate limit | 6 runs a minute per command | Stops loops and repeated mishearings |
| 14 | Sentence-trigger automations | Shown read-only, not importable | Importing would make the same sentence fire twice |
| 15 | The linked LLM | One AI Task entity in options (Haiku via the Anthropic integration), used for authoring and the optional nightly generalisation | One setting to manage |
| 16 | Panel access | Admin only | Approving commands changes what your home does |
| 17 | Language | English for v1 (the language is a setting) | Keeps testing manageable |
| 18 | Release plan | Phases 1–4 can ship first as 0.1 | The learning loop starts collecting data sooner |
