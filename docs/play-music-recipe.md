# Recipe: "play <anything>" locally, on the device you spoke to

Home Assistant's built-in "play {search_query}" needs a device with an area, and an `intent_script` cannot know which
voice device heard you. A **sentence-trigger automation** can (`trigger.device_id`, `trigger.satellite_id`), and it runs
before any conversation agent, so it never touches the LLM. This recipe was built and verified on a live instance
(Waveshare ESP32-S3 satellite, Music Assistant with a Jellyfin-synced library plus Tidal).

Files (paste each via *Edit in YAML*):
- [`examples/play-music-search-script.yaml`](examples/play-music-search-script.yaml) -> script, entity id `script.play_music_search`
- [`examples/play-music-on-calling-device.yaml`](examples/play-music-on-calling-device.yaml) -> automation

Create the **script first** (the automation calls it).

## What you can say
All take an optional "in the kitchen" / "on the kitchen speaker" (an area or a Music Assistant player). Without one the
device you spoke to is used.

| Say | Plays |
|---|---|
| "play teddy swims" | the artist's tracks in your library; else the best provider match |
| "play love songs" | a playlist (exact name, or whose name contains it: "christmas music" -> playlist "Christmas") |
| "play lose control by teddy swims" | that song (or album) by that artist |
| "play stand by me" | tries "stand" by "me", then the whole phrase as one title |
| "play music" | resumes the player |

"in the X" only counts as a room if X resolves to an area or player, so "play songs in the key of life" works.

## How it behaves
1. The automation parses `trigger.sentence`, picks the Music Assistant player and **replies immediately**.
2. It starts `script.play_music_search` without waiting (`script.turn_on`), which searches, plays, waits up to 20 s for the
   player to reach `playing`, and **announces on the satellite** if nothing was found or playback did not start.
3. Search order: your Music Assistant library (Jellyfin content shows up as `library://`), then every provider (Tidal...).
   With an artist: album, then song, named exactly that by that artist. Without: exact name (artist > playlist > album >
   song), then a playlist containing the name, then all that artist's library songs.

## Setup checklist
1. The speaker must be a **Music Assistant player** (Music Assistant -> Settings -> Players; Home Assistant provider for ESPHome
   speakers). The automation uses MA's own `media_player` entity, not the bare ESPHome one.
2. That MA player usually has **no area**; the automation finds it by matching the friendly name of the satellite's own
   media player. If names differ, give both the same name, or put the MA player in the same area as the satellite.
3. Optional: set `default_player` (top of the automation) for requests that resolve no player.
4. Speech-to-text must transcribe free text. Vosk in limited/corrected-sentence mode cannot (it dropped "play"); use
   **Whisper** (faster-whisper) or Home Assistant Cloud.
5. Remove old competing setups: an `intent_script: PlayMusic` replies "Playing" without checking anything, and a second
   conversation-trigger automation would fire too. Pipeline conversation agent: Assist Learner (or your LLM agent): triggers
   run first either way.

## Limits / gotchas
- **Genres cannot be matched.** The Home Assistant Music Assistant integration exposes no genre data. Make a playlist named
  after the genre (it is matched by name, "contains").
- **Generic phrases prefer playlists/albums over a provider artist**: "christmas music" exactly matches a huge Tidal *artist*
  called "Christmas Music"; `music_assistant.play_media` on it never returned (queue stuck at ~7000 items, speaker idle after
  saying "Playing"). The script now only treats a name as exact after dropping a trailing "music/songs/playlist", and puts
  playlists and albums ahead of artists for such phrases.
- `play_media` can hang, so a **watchdog** branch runs in parallel: if the player is not `playing` after 20 s it announces on
  the satellite. (The reply "Playing X" is spoken before that, by design.)
- Anything not in your library falls back to the provider's best guess, even for nonsense phrases.
- A variable template that renders JSON-looking text is auto-parsed to a dict inside automations; the automation handles both.
- Typed tests (`/api/conversation/process`, Assist dialog) have no device, so they need a spoken room or `default_player`.
- The search script was verified against real library/provider data; the "not found" announcement path was not triggered.

## Testing without speaking
Type the sentence in the Assist Learner **test box** (shows whether an automation trigger, intent or the LLM would handle it,
and the extracted slots; nothing is played). To exercise the whole chain:
`POST /api/conversation/process {"text": "play teddy swims in the kitchen", "agent_id": "conversation.home_assistant"}`.
Traces: Automations / Scripts -> the item -> Traces.
