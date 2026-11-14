# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No "Assist Learner" in the sidebar | The integration is not added (Settings -> Devices & services -> Add integration), you are not an admin, or the browser cached the sidebar (hard refresh). Check Logs for `assist_learner`. |
| Your custom sentence files are not listed, or learned sentences never match | Home Assistant reads `custom_sentences/<language variant>/` (`en-GB` -> `en`). Fixed in Assist Learner; approve/save one item so its file is rewritten in the right folder. |
| Every request answers "Sorry, I couldn't understand that" in ~7 ms | The target agent is Home Assistant's local agent. Reconfigure it to your LLM agent (three-dot menu -> Reconfigure). A Repairs issue flags this. |
| Run proposer says "0 new proposal(s)" and the request vanished from Misses | Processed requests are deleted by default (retention 0 days). The run summary says why each request produced nothing: failed (LLM error), not a music request, waiting for repeats (needs 2 sightings), already known, dropped by safety checks. Raise retention in Configure to keep them. |
| Nothing learned for "play X" | Requests only reach Assist Learner if the pipeline agent is `conversation.assist_learner`, no sentence trigger / local sentence handled them first, and the LLM called a media-player play action (an `intent_script` tool call is not learnable). |
| The test box says "would go to the LLM" but an automation handles it | Fixed: the test box now checks sentence triggers first. |
| "Generate" produces no slots/actions ("service must look like domain.service") | Fixed: slots/actions are sent to AI Task as JSON text (field-less `object` selectors become empty-object schemas on the real Anthropic AI Task). Re-verify with a slot + action command. |
| Custom command asks for a wildcard / `play_media` | Not allowed in v1: only number/choice slots, and no arbitrary `play_media`. Use the music recipe (`docs/play-music-recipe.md`) or learned music templates. |
| "Playing" spoken but no music | An `intent_script` always says its fixed reply. Use the automation + script recipe, check the traces, and confirm the speaker is a Music Assistant player. |
| Player is "Kitchen Jarvis" twice | One is the ESPHome entity, the other is Music Assistant's; target the Music Assistant one. |
| HA test suite fails to import with HA 2026.11 | The test plugin on PyPI pins HA 2026.10.4; see `LEARNINGS.md` (Dev env gotchas). |
