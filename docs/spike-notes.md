# Phase 0 spike notes (HA 2026.11.1, read from installed source)

Method: no Docker available, so APIs were confirmed by reading the installed `homeassistant` 2026.11.1 source.
Anything marked **verify-in-test** is covered by an integration test later rather than confirmed by reading.

| Item | Finding | Verdict |
|---|---|---|
| Entry point | `ConversationEntity.async_process` opens a chat session + chat log, then calls `_async_handle_message(user_input, chat_log)` | OK |
| Nested `async_converse` (risk 1) | `async_converse` handles nesting explicitly. `async_get_chat_log` returns the **already-active** chat log when `conversation_id` matches (uses `current_chat_log` contextvar), so the target agent appends its assistant/tool content to the *same* log the proxy holds. Errors from the target are converted to an error `ConversationResult` | **GO**: read tool calls from `chat_log.content[chat_log.turn_start:]` after the nested call; no need for the direct-handler fallback |
| Chat log busy | A second turn on the same conversation while `turn_active` raises `ConversationBusyError`; only relevant if we spawned a *new* conversation id | Pass the same `conversation_id` through |
| Device id on intents | `async_converse(..., device_id, satellite_id)` and `ConversationInput`/intent handlers carry both | OK (verify-in-test for handler `intent_obj.device_id`) |
| Recognizer adapter (risk 3) | `DefaultAgent.async_recognize_intent(user_input, strict_intents_only=False)` returns `RecognizeResult \| None`; `strict_intents_only=True` skips unexposed-entity matching. Results are cached per (text, language, device, satellite) | Wrap in one adapter module (`recognizer.py`) |
| Custom sentences | Loaded from `<config>/custom_sentences/<lang>/**/*.yaml` (`rglob`), keys `intents`, `lists`, etc. | OK |
| Reload | `conversation.reload` service exists (`services.py`); `DefaultAgent.async_reload(language)` | OK |
| Built-in vs custom priority (risk 10) | Not determinable by reading alone | **verify-in-test** (phase 3) |
| `call_service` context chain (risk 2) | Needs a running instance | **verify-in-test** (phase 1) and manual acceptance |

Decision: proceed to Phase 1 with `async_converse` + shared chat log capture.
