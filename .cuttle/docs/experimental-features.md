# Runbook — Experimental features & Achievements

How-to for the shipped experimental flag surface and the Achievements feature.
Design record + full teardown checklist:
[experimental-features-plan.md](experimental-features-plan.md).

Achievements is **opt-in and off by default**. The flag-management API is always available.

---

## Turning things on/off

**UI**: Settings → 🧪 **Experimental** tab. Rows are rendered from the registry,
so anything listed there is a real flag on the server.

**Kill switch** (process level, overrides stored settings for every flag): add
`CUTTLE_EXPERIMENTAL=0` to `<home>/.env` and restart Flask.

Use the daemon-owned Flask restart action after changing the process environment.

**Agent CLI**:

```bash
.venv/bin/python -m api.experimental list          # every flag + resolved value
.venv/bin/python -m api.experimental get achievements
.venv/bin/python -m api.experimental set achievements on
.venv/bin/python -m api.experimental set achievements off
.venv/bin/python -m api.experimental reset         # all flags → spec defaults
```

```bash
cd src && ../.venv/bin/python -m api.experimental list   # must run with src on the path
```

---

## Achievements verbs

```bash
cd src && ../.venv/bin/python -m api.achievements list            # catalog + progress
cd src && ../.venv/bin/python -m api.achievements progress --unlocked-only
cd src && ../.venv/bin/python -m api.achievements progress --limit 10
cd src && ../.venv/bin/python -m api.achievements pending          # unseen unlocks
cd src && ../.venv/bin/python -m api.achievements scan             # re-evaluate now
cd src && ../.venv/bin/python -m api.achievements grant one_turn_100m
cd src && ../.venv/bin/python -m api.achievements reset
```

`scan` is the useful one after upgrading a metric definition or backfilling
`router_outcomes` — it is idempotent and never re-unlocks.

**First-enable backfill**: progress is derived from existing `router_outcomes`
rows, so switching the flag on immediately evaluates your whole history and can
unlock a lot at once. The client batches celebrations 4 at a time so it reads as
a roll call, not a wall of toasts. Suppress it once with:

```bash
cd src && ../.venv/bin/python -m api.achievements reset
```

(then re-`scan` when you actually want the ceremony).

---

## HTTP surface

| Method | Path | Auth |
|---|---|---|
| GET | `/api/experimental/flags` | authenticated |
| POST | `/api/experimental/flags/<flag_id>` | owner |
| POST | `/api/experimental/flags/reset` | owner |
| GET | `/achievements_page.html` | authenticated |
| GET | `/api/achievements` | authenticated |
| GET | `/api/achievements/pending` | authenticated |
| POST | `/api/achievements/<id>/ack` | authenticated |
| POST | `/api/achievements/scan` \| `/reset` \| `/<id>/grant` | owner |

Achievements HTTP routes answer `200 {success: false, disabled: true}` while its flag is off. Flag-management routes remain available.

---

## Design practice for new Cuttle features

When adding a new Cuttle capability, prefer shipping it as an opt-in experimental
feature first. This is a default development practice, not a requirement for every
change: bug fixes, established behavior, and capabilities ready for general use
may ship directly. Record the reason when bypassing the experimental stage.

Experimental status governs rollout, not UI placement. Settings → Experimental
is a registry-driven enable/disable surface, with feature descriptions and rollout
metadata. Do not put feature workflows, dashboards, trophy cases, or controls
for operating a feature in that tab.

Choose the surface that fits the feature:

- A standalone tool or content view can register a Cuttle App in the Apps launcher.
- Feature configuration can extend an appropriate Settings tab or introduce one.
- A small enhancement can integrate with its existing surface and need neither
  an App nor a settings tab. Do not create empty surfaces just for consistency.

Keep feature logic in its owned API package and expose library/agent CLI verbs
when agents should operate it. Gate behavior with the registry flag; respect the
process kill switch. A discoverable App may show a disabled state with a link to
Experimental settings, while its functionality remains gated.

## Adding a new experimental feature

Agent-context ownership and migration:
[feature-owned context bundles](experimental-context-bundles.md). Register
`FlagSpec(context_bundle=True)` and ship operational rules/docs/skills/recipes
under `.cuttle_global/features/<flag-id>/`. The shared resolver exposes these
resources only when the existing flag resolver enables the feature. Personal
deltas belong under `<home>/personal/features/<flag-id>/`. Core guidance remains
independent; packaged source presence does not imply operational availability.

1. Add one row in `src/api/experimental/features.py`, normally default-off.
2. Gate behavior with `api.experimental.is_enabled("your_flag")`.
3. Put feature UI in its own appropriate surface, following the design practice
   above. Register an App with the shell's canonical rail catalog if appropriate;
   start it stashed so it appears in Apps and can be pinned by the user.
4. The generic Experimental tab and flag CLI automatically read the registry.
   Do not add a feature-specific toggle route or bespoke block to that tab.
5. Document agent operations, tests, and teardown in the feature owner.

### Compatibility rules

`FlagSpec(exclusive_group="name")` declares alternative experiments that must
not run together. `FlagSpec(conflicts=("other_flag",))` declares individual
incompatibilities; the relation is symmetric, so declare each pair once.
The generic Experimental page shows which enabled experiment blocks a toggle.
Blocked and paused switches are gray and disabled. A warning tooltip next to
the info tooltip names the conflicts. A paused feature has a **Turn off** button
to clear its saved preference while its switch remains disabled.
API and CLI enables reject conflicting combinations; the check runs under the
shared settings lock, including concurrent writers. Existing manually edited
or older conflicting configurations pause every participant until the user
disables the conflicting choice. No preference is silently discarded.

GPT-Live, Realtime and server transcription share `voice_input`. The standalone
voice narrator conflicts with Live and Realtime, which own their spoken output.
Server transcription and the narrator remain compatible. Flags determine
availability; Voice settings determine which available conversation engine runs.

## Achievements App

Open **Apps → Achievements** for the trophy case, unlock summary, and progress
rescan. It starts unpinned; Apps can pin it to the blade bar. When disabled, the
App shows an explanation and a link to Settings → Experimental. Existing progress
is retained. Turning Achievements off does not erase unlocks.

## Gizmos App

Flag `gizmos`. Open **Apps → Gizmos** to add a usage meter (Codex, Claude Code,
Cursor), pick its dock, and edit or remove it. In the shell, drag a gizmo
between the title bar, the blade bar, and anywhere else (floating); click or
right-click it for details and actions. In the desktop app, "Desktop window"
pops it out as an always-on-top window that stays up while Cuttle sits in the
tray. Agent verbs and REST: [gizmos.md](../../.cuttle_global/docs/gizmos.md).
Turning the flag off hides every gizmo and closes pop-outs; rows are kept.
This applies to the experimental shell gizmos; established composer Tasks
remain available independently of this flag.

## Test Lab App

The Test Lab tile and pinned icon are hidden while disabled; its direct page
and operation routes also reject disabled access. Enable the `test_lab`
flag in Experimental settings to use it. Select a registered
project to inspect its recognized test suites and recent run history. Test Lab
does not execute tests during discovery; choose **Run suite** or **Run all
tests** to start them. Runs are bounded to 20 minutes, use the project's local
Python environment when present, and keep their recent output in the Cuttle
home database. Discovery supports pytest files, npm `test` scripts, Go modules,
and Rust Cargo packages. Only the owner can inspect projects or start runs.

## GPT-Live voice

Flag `voice_live` (default off): Settings → Experimental → **Voice: GPT-Live**.
After enabling it, Settings → Voice offers GPT-Live as the recommended conversation
engine, `gpt-live-1`, its supported voice catalog, and an idle timeout (150 seconds
by default; 0 keeps the connection open continuously). Other voice input
experiments and the standalone narrator must be disabled first.

The voice session uses client delegation to the chat's selected Cuttle agent.
The server observes transcripts, claims delegation IDs once, and submits work
through the existing authenticated chat transport. Steerable running turns
receive steering; other turns use the shared durable follow-up queue. A speech
interruption does not cancel agent work. Approvals still require the chat's
confirmation controls. Voice results use short speakable appends; private
reasoning and UI markup are not forwarded as spoken results.

The shell owns the microphone/speaker connection, independently of disposable
chat iframes. Switching Spaces preserves the originating chat and exposes a
compact return/mute/end control. Returning to the chat reattaches its voice
presentation. Backgrounding or locking the app closes voice while agent work
can continue in chat. Muting keeps the duration meter active.

OpenAI bills $0.05 per active minute, by the second, including silence and
agent waiting. WebRTC initialization bills 15 seconds provisionally; running
usage replaces that amount rather than adding it again. The server records
cumulative duration checkpoints and finalization in the existing provider
ledger. Usage appears in the composer, voice view and Cuttle Usage's Voice
filter; the dashboard includes a Voice duration metric. Per-message speech
costs are attributed to their bubble when a message/query identity is available.
Unknown or unconfirmed finalization remains distinct from a verified zero.

Owners: `api.voice_live` (sessions/delegation/transcript receipts),
`model_providers.openai.live` (provider transport), `voice_usage` and provider
usage (pricing/ledger), `shell_voice.js` (connection lifetime),
`chat_voice_live.js` (Live protocol), `chat_voice.js` (presentation), and
`chat_session_usage.js` (durable usage presentation). Settings are under
`/api/settings/voice-chat`; session routes are `/api/voice-stt/live/sessions`.
Owner auth and chat ownership are required; credentials remain on the server.
Provider audio recording storage is off. Reconnects seed bounded saved text
history. No ambiguous paid create/delegation request is blindly retried.

Secure browsers use WebRTC. The updated Capacitor Android app can stream native
24 kHz mono PCM through its authenticated host socket when LAN HTTP cannot use
`getUserMedia`. Native capture/playback owns permissions, audio focus, echo
handling, bounded queues and shutdown. Older APKs do not expose that bridge;
they need the updated app for Live on HTTP. Realtime still requires WebRTC
microphone support; an unavailable engine is reported explicitly.

Tests use fake providers and private state; real microphone/model-access and
physical-device audio quality require a deliberate smoke test.

## OpenAI Realtime voice

Flag `voice_realtime` (default off), in Settings → Experimental →
**Voice: OpenAI Realtime**. It enhances the existing voice overlay. When enabled,
it becomes available in the Voice conversation-engine selector. It conflicts
with Live, server transcription and the standalone narrator. The process kill switch
`CUTTLE_EXPERIMENTAL=0` also disables it.

Tap the microphone to connect, speak naturally, and tap again to disconnect.
Semantic voice activity detection sends turns automatically and supports
interrupting spoken replies. The shell owns the live connection across Space
switches; ending voice or backgrounding the shell closes it. An open connection rechecks the flag every
10 seconds and closes when disabled. Failed connections show an error; tap to
retry. Unsupported WebRTC clients retain the established voice input engine.

OpenAI `gpt-realtime-2.1` handles live input/output audio over WebRTC. The
owner-authenticated `POST /api/voice-stt/realtime/session` mints a 60-second
ephemeral credential using the existing OpenAI provider key. No long-lived key
is sent to the browser. The response has `Cache-Control: no-store`; secrets
are not persisted or logged. Custom `OPENAI_BASE_URL` proxies are rejected for
this feature. Input transcription uses `gpt-4o-mini-transcribe`; spoken output
uses Realtime's `marin` voice, independently of the existing chat TTS preference.

The live companion can converse directly. Its `ask_cuttle_agent` function hands
project work and context questions to the ordinary chat submitter, preserving
the sticky agent, router, chat persistence and execution ownership. Completed
agent replies return as tool outputs and are spoken by Realtime. A running
steerable agent receives steering; its eventual reply is fed back to the live
session. Other running agents receive a queued follow-up; the server correlates
the queued request and its eventual reply before returning the result. Closing
voice does not cancel already submitted chat work.

Live small-talk transcripts are displayed only in the voice overlay; agent
handoffs are persisted through the existing chat path. A new live session does
not automatically receive the full chat history; it can ask the chat agent for
context. Audio usage is priced with its frozen model/transcription settings and
recorded using idempotent provider event receipts. It contributes to durable
chat totals and the dashboard. This is a
WebRTC feature; the Android `NativeMic` bridge is not a WebRTC transport.

Owners: `api.voice_realtime` (session config/credential issuance),
`chat_voice_realtime.js` (connection, audio, events and tool lifecycle),
`chat_voice.js` (overlay and chat handoff composition). Credential routes are
mounted as a child blueprint of the existing voice API. No Electron code or
new execution lane is required.

Protocol references: [OpenAI WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc),
[Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations).
Validation uses mocked providers, WebRTC and microphone devices; a real account
and microphone smoke test is required to establish live model access and audio
quality. Tests: `src/tests/chat/test_voice_realtime.py` and
`src/tests/chat/test_chat_voice_overlay.py`.

## Voice narrator

Flag `voice_narrator`. Enhances voice mode in place (no App or settings tab).
After a voice send, Cuttle speaks a one-line acknowledgment, in the first person
as Cuttle (never naming the harness or saying "the agent"), then progress lines
paraphrasing the live status lines: the first after
about 6 s, then at most one every 10 s, with a "still working" heartbeat every
25 s while the agent is quiet (12 lines per turn).
The sticky agent or Cuttle Router still runs the turn; the narrator never answers
or decides anything. A reply, a mic tap, or leaving voice mode before a turn
starts silences it. Lines come from `api.llm_complete` (gpt-4o-mini by default)
voiced with the Settings → chat speech voice; `POST /api/voice-narrator/narrate`
answers `{disabled: true}` while the flag is off. Owner `src/api/voice_narrator/`,
client `src/web/js/chat/chat_voice_narrator.js`; tests
`src/tests/chat/test_voice_narrator.py`, `test_chat_voice_overlay.py`.
Teardown: drop the flag row, the package, its blueprint registration, the
narrator script and the `narrate*` calls in `chat_voice.js`.

## Voice server transcription

Flag `voice_server_stt`. Voice mode records with the microphone instead of the
browser speech recognizer, so Android stops chiming at every pause. Browsers
only allow mic recording on a secure origin, so the audio source is: the Cuttle
Android app's native recorder (`NativeMic`, works over LAN HTTP, no setup), else
the browser (HTTPS or localhost only), else the old recognizer — the voice hint
names which one is live. Rule of thumb: in Cuttle apps voice just works; in a
plain browser it needs HTTPS. Nobody installs certificates for voice. A local
energy detector cuts a phrase at each pause (about 0.9 s) and uploads it to
`POST /api/voice-stt/transcribe` (OpenAI `gpt-4o-mini-transcribe`, falling back
to `whisper-1`; needs `OPENAI_API_KEY`). Each phrase becomes its own removable
bubble, in spoken order. Silence is never uploaded. The route answers
`{disabled: true}` while the flag is off. Owner `src/api/voice_stt/`, client
`src/web/js/chat/chat_voice_recorder.js`, Android
`apps/mobile/.../voice/NativeMic.java` + `PcmClip.java` (bridge `cuttleMobile.mic*`);
tests `src/tests/chat/test_voice_stt.py`, `test_chat_voice_overlay.py`,
`PcmClipTest.java`. Teardown: drop the flag row, the package, its blueprint
registration, the recorder script and the `engine === 'server'` branches in
`chat_voice.js` (the native bridge is inert without them).

## Adding an achievement

1. Add the row to `_ACHIEVEMENTS` in `src/api/achievements/catalog.py`.
2. If it needs a number that does not exist yet, add the metric to
   `evaluator.METRIC_NAMES` **and** to `REQUIRED_METRICS` in `catalog.py`
   (the catalog raises at import on an unknown metric).
3. Pick a rarity — it drives celebration intensity
   (`common` toast → `rare` +sound → `epic` +confetti → `legendary` → `mythic`
   +shimmer) and the trophy grid.

Ladders matter: add the rung below an existing achievement rather than an
isolated entry.

---

## Release checklist

- [ ] New SFX? regenerate with
      `.venv/bin/python .cuttle/scripts/make_achievement_chime.py`, then copy to
      `electron/assets/` — **that directory is checked in and not generated**,
      and the byte-identical copy is asserted by `test_achievements_js.py`.
- [ ] New SFX? add the name to `NATIVE_SFX_FILES` in `electron/main.js`.
- [ ] Changed client JS? bump the `?v=` cache-bust on the `<script>` lines in
      `src/web/app_shell.html` (and `settings_page.html`).
- [ ] Reload ladder: HTML/JS/CSS only → hard-refresh the shell. No Python change
      → **no Flask restart needed** for flag toggles; a restart *is* needed if
      you added a new route to `web_chat_api.py`'s registration block.

---

## Previewing the celebration

`/achievements_preview.html` (owner-only) fires the real toast + confetti + sfx
path with sample payloads for all five rarity tiers, plus burst buttons. No flag,
no restart, no real unlocks. The file also opens directly from disk
(`file://`) since its script paths are relative.

---

## Where things live

| Thing | Path |
|---|---|
| Flag registry rows | `src/api/experimental/features.py` |
| Flag resolver | `src/api/experimental/flags.py` |
| Achievement catalog | `src/api/achievements/catalog.py` |
| Metric math (incl. sub-agent exclusion) | `src/api/achievements/evaluator.py` |
| Progress/unlock state | `<home>/db/achievements.db` via `api/achievements/store.py` |
| Turn seam | `on_turn_saved()` in `src/api/chat_turn_persist.py` |
| Client slices | `src/web/js/achievements/achievements.js`, `src/web/js/achievements/celebrate.js` |
| Trophy grid | `achievements_page.html` + `js/achievements/achievements_page.js` + `css/achievements_page.css` |
| Settings storage | `experimental_flags` key in `<home>/config/settings.json` |

Flag CLI commands emit JSON and return a nonzero exit code on errors. Toggles made by a separate local agent process become visible to Flask without a restart. Overrides persist across Flask restart and app reopen. Single-flag writes merge under the settings store lock and preserve other overrides, including flags unknown to an older process. Test settings are private, and writes to the live install are rejected by the shared test fixture. The kill switch still overrides stored toggles.

Generic confetti and toast triggers belong to `api.chat_vfx`; see [chat-vfx.md](../../.cuttle_global/docs/chat-vfx.md). They do not grant achievements or change experimental flags. Removing achievements leaves the generic VFX owner intact.
