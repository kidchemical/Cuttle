# Monolith refactor plan

> **Status: largely executed / working plan.** Many Phase 6 route-family moves are
> already extracted; current ownership is described in
> [`repository-map.md`](repository-map.md) and `AGENTS.md`. Treat this as plan +
> history, not a description of current state.

Plan for moving the remaining domain logic out of `chat_page.js`,
`app_shell.js` and `web_chat_api.py` into named owners. The refactor
preserves behavior; it does not redesign anything. Measured inputs:
[`monolith-baseline.md`](monolith-baseline.md) (source `62aca76`). Rules:
[`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md). Current owners:
[`repository-map.md`](repository-map.md).

Line targets are directional (about 5–8k for `chat_page.js`, 2.5–4k each for
`app_shell.js` and `web_chat_api.py`). Correct ownership matters more than
reaching a number.

## 1. Working rules

**Branches**
- One branch per phase, cut from the latest `dev` (`refactor/phase-N-<topic>`).
- One isolated commit per slice, so each slice can be reverted alone.
- Merge into `dev` when the phase is reviewed. The next phase starts from the
  updated `dev`.
- If a high-risk phase gets too large to review, split it into per-slice
  branches.
- Concurrent agents get separate worktrees and branches. Never two agents in
  one monolith in a shared working directory.

**Scope**
- No feature work, no behavior change, no opportunistic cleanup. Record
  unrelated findings instead of fixing them.
- One owner per behavior and state machine. Before creating a module, check
  §3 and the repository map for an existing owner.
- Owners never read page globals. They receive capabilities as explicit
  arguments or a `host`/`deps` object, the same pattern as
  `CuttleChatActionCards.mountCards(root, host)` and the `escapeHtmlInline`
  injected into `chat_messages`.
- Prefer a pure decision module plus a controller module (for example
  `chat_action_forms` + `chat_action_cards`) over one mixed file.
- New frontend modules load before `chat_page.js` / `app_shell.js` in the
  page HTML and follow the existing `chat_*.js` / `Cuttle*` naming.
- Window exports (`baseline §2`) and `postMessage` type strings are
  interfaces. Keep them, or change every caller in the same commit.
- Python changes reach the running app only through the `flask.restart`
  action form. JS/CSS-only slices need a hard refresh, never a restart.

## 2. Per-slice protocol

Each slice follows these five steps, in order:

1. **Characterize.** If the code to be moved has no behavioral test (the
   lightbox, bubble TTS, context gauge and message nav have almost none
   today), first add tests against the current page code. Commit them with
   the slice, or before it.
2. **Move.** Move the code to its owner. The page keeps a thin adapter or a
   call site and no private state for the moved feature.
3. **Migrate the tests that pin the old location.** At the baseline, 34 test
   files sliced 139 monolith functions out of the source (appendix A).
   - Point each one at the owner module's public interface.
   - Don't re-slice it from the new file by name.
   - Source-text assertions that only pin *where* code lives are rewritten to
     pin the owner (namespace exists, page delegates). Assertions about
     behavior must stay behavioral.
4. **Run the gate:**
   - **Offline suite:** `CUTTLE_API_URL=http://127.0.0.1:9 .venv/bin/python -m pytest src/tests/`.
     Baseline: 3,837 passed / 0 failed / 55 deliberate skips. Any new failure
     or skip blocks the slice.
   - **The slice's browser suites, named explicitly.** `src/tests/e2e/` is in
     `collect_ignore`, so the offline run above includes no browser cases.
   - **Quality gates:** `test_architecture_boundaries.py`,
     `test_composition_root_growth.py`, `test_frontend_assets.py`,
     `test_chat_page_js_syntax.py`.
   - **Run the gate on the commit, not the working tree.** A suite reads
     source files as it goes, so editing during a run mixes states. Gate
     each slice commit in a throwaway worktree:
     `git worktree add --detach temp/worktrees/<name>/Cuttle <rev>`.
     - The checkout directory must be named `Cuttle`; two path-rewrite
       tests expect it.
     - Symlink `.venv` into it, because the restart-script test looks for
       it.
     - Run pytest with the main checkout's interpreter by its real path,
       not through the symlink. The shadow-instance read guard otherwise
       denies the venv prefix and 35 shadow tests fail to boot.
   - Run gates one at a time. `test_atomic_claim_across_connections` is
     load-sensitive when two suites run at once.
   - Browser checks assert `not errors` (page errors). That catches a page
     reference to a function that moved, which `node --check` cannot.
5. **Smoke-test the real UI.** The user hard-refreshes for JS slices, or
   restarts Flask through the action form for Python slices, and exercises
   the slice's listed journeys. No paid or live vendor runs unless the user
   explicitly authorizes one.

## 3. Gaps resolved: domains the original plan didn't assign

These domains have no slice in the master plan. Each now has an owner, so the
Phase 0 exit gate ("every planned extraction has a destination owner") holds.

| Domain | Lines | Coupling out/in | Destination | Phase |
|---|---:|---|---|---|
| Transcript nav: pins, scroll, jump, copy, share | 564 | 14 / 40 | new `chat_transcript_nav.js` (`CuttleChatTranscriptNav`) | 1D |
| Subagent launchers/orbs | 196 | 4 / 8 | new `chat_subagent_launchers.js` over the pure `chat_subagent_fleet.js` | 1E |
| Composer state: drafts, attachments, prompt history, enhance | 751 | 32 / 48 | existing `chat_composer.js`, `chat_attachments.js`, `chat_prompt_history.js` (finish half-done delegation) | 2D |
| Context gauge | 402 | 6 / 3 | new `chat_context_gauge.js` (`CuttleChatContextGauge`) | 2E |
| Chat project binding: cwd, pending changes, git push menu | 1,014 | 19 / 48 | existing `chat_project.js`; git push menu → existing `git/git_push_report.js` family | 3E |
| Supervised activity cards | 369 | 11 / 6 | existing `supervised_control.js` (`CuttleSupervised`) | 4F |
| Electron chrome, zoom HUD, mobile shell, SSH approval (shell) | 1,180 | 8 / 13 | new `shell/shell_electron.js` and `shell/shell_mobile.js` | 5F |
| Rail customization / apps list (shell) | 566 | 11 / 41 | new `shell/shell_rail.js` | 5E |
| Cross-pane hubs: live status, pending changes, open panes (shell) | 227 | 12 / 13 | stays with pane lifecycle (5B) and existing `shared/activity_broker.js` | 5B |
| Terminal session list | 101 | 2 / 6 | **stays in the page.** Too small and too entangled with the history list to own separately; revisit in Phase 7 | — |

## 4. Phases

Each slice lists its destination, the code that moves, what the page keeps,
its interface, the tests to migrate (by count of sliced functions), and the
journeys to verify. "Out/in" are the coupling counts from baseline §6.

### Phase 0: Baseline and dependency audit — this branch

Deliverables are this plan and `monolith-baseline.md`. No production change.

**Exit:**
- Every domain in baseline §6 has a destination (§3 above).
- The gates are recorded.
- The test-location coupling is listed (appendix A).

### Phase 1: Chat media and presentation (low to medium risk)

Order: 1A → 1C → 1E → 1D → 1B. 1B is more coupled than the original
estimate, so it goes last.

**1A — Media and lightbox** (1,129 lines, out 4 / in 6, 2 sliced functions).
The cleanest first extraction.
- **Destination:** new `chat_media.js` (`CuttleChatMedia`). The master plan
  proposed `chat_media_controller.js`; repo convention is `chat_<domain>.js`
  even for controllers (`chat_action_cards.js`).
- **What moves:**
  - 53 functions (`mediaKindFromUrl` … `bindCuttleMediaLightbox`, plus
    `isSafeMediaSrc` and `onMediaContextMenu`).
  - 17 lightbox and media-menu state bindings.
  - The `window.CuttleMediaThumbFallback` export.
- **The page keeps:**
  - `formatMessage` calling `CuttleChatMedia.buildMediaThumbHtml` /
    `mediaKindFromUrl`.
  - One `bind(root)` call in init.
  - The `mediaDownloadUrl` use in attachments, through the namespace.
- **Interface:** pure helpers plus `create({ escapeHtmlInline, clamp, signal,
  hostWindow, toast })`.
- **Constraint:** the lightbox and media menu mount on the **host (shell)
  document**. Every host listener must stay bound to the page's
  `parentListenersAbort` signal.
- **Verify:**
  - Characterize first: thumb HTML, lightbox open/close/keyboard, zoom
    clamp math, and caption/meta.
  - `test_chat_frame_listener_teardown.py`, `test_chat_messages.py`.
  - Journeys: zoom/pan/pinch, copy/save image, video thumbs, gallery
    navigation, repeated pane switches without leaked host listeners.

**1C — Bubble TTS playback** (247 lines, out 1 / in 9).
- **Destination:** new `chat_tts.js` (`CuttleChatTts`), owning playback state
  (`_chatTtsAudio`, active button, prefs) and `/api/chat/tts` fetch/playback.
- **Relationship to voice:** this is the single owner of TTS *playback*.
  `chat_voice.js` keeps voice mode and continues to receive playback as a
  host capability (it already does). Voice currently reaches into five TTS
  internals; those become calls on the `CuttleChatTts` instance. No second
  voice subsystem is created.
- **Verify:**
  - Characterize play/stop/replace and button reset.
  - Voice suites `test_chat_voice_overlay.py`, `test_chat_voice_waveform.py`.
  - Journeys: speaker button, voice mode reply playback, stop on navigation.

**1E — Subagent launchers and orbs** (196 lines, out 4 / in 8).
- **Destination (revised in 1E):** new controller `chat_subagent_launchers.js`.
  `chat_subagent_fleet.js` is a documented pure markup module (no DOM), and
  the launchers mount DOM and hold the live list. So they follow the
  pure-module-plus-controller pattern and call the fleet module for cards.
- Verify with e2e `test_subagent_fleet_cards.py`.

**1D — Transcript navigation** (564 lines, out 14 / in 40).
- **Destination:** new `chat_transcript_nav.js`, covering follow-latest
  scroll state, the message nav rail and pins, jump-to-bottom,
  jump-to-message, `?msg=` deep links and message refs.
- **Revised in 1D:** message copy/speak text and the clipboard fallback are
  message *actions*, not navigation. They stay in the page for 1B.
- **The page keeps:** a narrow API instead of shared variables
  (`isFollowingLatest`, `holdScrollPin`, `settleAfterLoad`, `resetNav`).
  The two identical "settle at bottom after load" blocks are now one owner
  method.
- **Verify:**
  - Characterize pin storage and ref parsing.
  - e2e `test_verify_nav_history.py`, `test_chat_history_scroll.py`.

**1B — Message presentation** (1,408 lines, out 51 / in 63, 12 sliced functions).
- **Destination:** existing `chat_markdown.js` (md link chips, chat-handle
  linkify, Vega wrap, code-copy text) and `chat_messages.js` (footer and
  button HTML, usage/query-log footers, pricing HTML). No new renderer.
- **`formatMessage` (463 lines)** stays the page's orchestration in this
  phase, but delegates block-by-block. Splitting it further belongs to Phase 7
  review.
- **Verify:**
  - `test_chat_messages.py`, `test_chat_markdown.py`,
    `test_agent_cost_slash.py`, `test_assistant_badge_all_harnesses.py`,
    `test_chat_handle_links.py`.
  - e2e `test_chat_usage_footer.py`, `test_chat_action_card_render.py`.

**Exit:** the page delegates media, TTS, nav, launchers and presentation, and
holds none of their private state.

### Phase 2: Agent selection and composer (medium risk)

**2A — Model/effort controls** (2,777 lines, out 26 / in 108, 16 sliced
functions).
- **Destination:** new controller `chat_agent_controls.js`
  (`CuttleChatAgentControls`) over the pure `chat_agent_model.js`.
- **What moves:**
  - The six provider families (Cursor, Muse, Hermes, OpenCode, Codex,
    Claude). Each is a copy of the same steps:
    `load*ModelsForPalette`, `persist*ModelSelection`,
    `build*ModelPaletteItems`, `seed*Supplement…`, `load/persist/build*Effort…`,
    `pretty*ModelLabel`, `repaint*UserBadges` and `enrichSlashCommandWith*`.
  - With provider names normalized, the copies are 0.8–0.99 similar, except
    Claude's loaders and Codex/Claude effort builders (0.06–0.44).
- **Collapsing the copies:**
  - Collapse into one descriptor-driven path only after characterizing every
    provider's differences. Keep a provider-specific branch wherever behavior
    really differs.
  - Collapsing must not change which endpoints are called, the payloads, or
    the label text.
- **The page keeps:** palette integration through the controller.
  `slashPaletteSupplement` state moves to the controller.
- **Coordination:** pairs with 6C (server routes moving to
  `agents/<id>/routes.py`). Keep the `/api/<id>/{models,model,effort}`
  contract unchanged in both phases.
- **Verify:**
  - `test_chat_agent_model.py`, `test_slash_palette_selection_consistency.py`,
    `test_first_turn_agent_pins.py`, `test_codex_starred_effort.py`,
    `test_agent_router_palette.py`, and the provider pin suites.
  - e2e `test_chat_badge_identity.py`, `test_chat_badge_starring.py`,
    `test_chat_draft_agent.py`.

**2B — Slash palette controller** (2,418 lines, out 115 / in 76, 20 sliced
functions; the largest test migration).
- **Destination:** new `chat_slash_palette.js` (DOM, keyboard, filtering
  effects, lifecycle) over pure `chat_slash.js`.
- `applySlashSelectionInner` (405 lines) and `renderSlashChips` /
  `renderSlashMenu` move in stages.
- **Verify:**
  - `test_slash_palette_*.py`, `test_composer_chip_removal.py`,
    `test_agent_usage_slash.py`.
  - e2e `test_chat_draft_controls.py`.

**2C — Sticky agent and shared composer selection** (418 lines, 7 sliced
functions).
- **Destination:** existing `chat_composer.js`, which already owns
  `draftWithSharedAgent`.
- Server `composer_selection` stays the owner of persisted selection.
- Precedence rules stay unchanged (star seeds new chats only; chip ✕ sends
  `sticky_agent: "none"`).
- **Verify:** `test_starred_agent_removal.py`, `test_bare_sticky_agent_send.py`,
  e2e `test_chat_badge_identity.py`.

**2D — Composer state** (751 lines). Finish moving drafts, pending
attachments, prompt history and prompt enhance into their existing owners.
- **Verify:** `test_composer_draft_session.py`, `test_chat_attachments*.py`,
  `test_chat_prompt_history.py`, `test_prompt_enhancer.py`, e2e
  `test_chat_draft_controls.py`.

**2E — Context gauge** (402 lines, out 6 / in 3).
- **Destination:** new `chat_context_gauge.js`.
- There are no tests today, so characterize first.

**Exit:** agent selection, palette and composer state each have one named
owner, with no parallel model-selection implementations.

### Phase 3: History and session lifecycle (high risk)

**3B — Session lifecycle first.** The other Phase 3 slices observe it.
- **Destination:** new `chat_session.js` (`CuttleChatSession`). It becomes the
  only writer of `currentSessionId`; today six functions write it and 22 of
  27 domains read it.
- **Step 1 is one mechanical commit:** reads go through an accessor, and
  writers call `session.adopt/open/create`, which keeps `_loadSessionSeq`
  navigation-sequence semantics.
- **Step 2:** move `loadChatSession` (449 lines), `createNewChat`,
  `bootRestoreSession`, `adoptChatSessionId` and `saveChatSession`.
  Observers subscribe; no other code sets session identity.
- **Verify:**
  - `test_chat_session_routing.py`, `test_chat_followup_heal.py` (9 sliced),
    `test_working_bubble_badge_chat_switch.py`, `test_project_chip_persistence.py`.
  - e2e `test_chat_sync_lifetime.py`, `test_app_shell_navigation.py`,
    `test_welcome_ui_sanity.py`.

**3A — History panel** (3,485 lines, out 83 / in 98, 14 sliced functions).
- **Split into four commits:**
  1. Search and filters (`searchChats`, filter chips, history slash palette).
  2. Item rendering, menus, and rename/delete modals.
  3. Project grouping and subagent nesting.
  4. Attention/unread (450 lines) into existing `chat_attention.js`.
- **Destinations:** extend `chat_history_view.js` (pure) and
  `chat_history_archive.js`, plus a new `chat_history_panel.js` controller.
- **Verify:**
  - `test_chat_history_*.py`, `test_chat_rename.py`,
    `test_chat_archived_badge.py`, `test_chat_attention*.py`.
  - e2e `test_chat_history_search_panel.py`, `test_chat_history_scroll.py`,
    `test_shadow_chat_status_dots.py`.

**3C — Message sync** (753 lines, 9 sliced functions).
- This reopens a decision recorded as "page-side by design" (sync timer
  mechanics; baseline §8).
- Do it only if 3B leaves the timer as the last thing tying the page to
  session identity. Otherwise record "kept" with evidence.
- If moved, sync claim and replacement stay in `chat_generation.js` and
  reconciliation goes to `chat_messages.js`.
- **Verify:** e2e `test_chat_sync_lifetime.py`, `test_chat_stream_reader.py`,
  `test_chat_message_pagination.py`.

**3D — Cross-pane identity.**
- One owner on each side (the page's `CuttleChatSession`, the shell's pane
  map), with the `cuttle-chat-session`/`cuttle-chat-url` contract unchanged.
- **Verify:** `test_shell_panes.py`, e2e `test_app_shell_navigation.py`.

**3E — Chat project binding** (1,014 lines).
- Moves into `chat_project.js` and the git push report family.
- **Verify:** `test_chat_project.py`, `test_stamp_auth_session_project.py`,
  e2e `test_pending_changes_polling.py`, `test_git_push_report.py`.

**Exit:** a single documented session lifecycle, and no competing session
state machines.

### Phase 4: Turn execution and recovery (highest risk)

Lesson from P5-E (baseline §8): subscriber lifetime is not turn lifetime.
Every 4A–4D slice characterizes detached and slow SSE subscribers, Stop →
resend, and reload before it moves anything.

- **4A — Turn lifecycle** (1,560 lines). `processMessage` (828 lines) splits
  by stage (prepare → submit → stream → finalize) behind
  `chat_generation.js` / `chat_turn_guard.js` / `chat_stop_state.js`. Nine
  functions are sliced by tests.
- **4B — Stream transport** (711 lines). This covers the status poll,
  connecting watchdog, pool-stall detection and typing indicator.
  `chat_stream.js` stays the only SSE decoder.
- **4C — Recovery** (333 lines). Moves into `chat_pending_result.js`
  (detached watch, parked results, post-restart recovery).
- **4D — Follow-ups and steering** (584 lines). Moves into
  `chat_followup_queue.js` with explicit transitions. Enqueue, steer, pause,
  resume and drain behavior are preserved.
- **4E — Voice.** Voice submits through the same turn interface. Today it
  writes `inFlightUserMessage` directly in `submitVoiceTurn`.
- **4F — Supervised activity cards** (369 lines). Moves into
  `supervised_control.js`.
- **Verify:**
  - The lifecycle regression gate in `src/tests/README.md`.
  - `test_p5e_stream_oracles.py`, `test_p5f_pipeline_oracles.py`,
    `test_stream_detach_status.py`, `test_chat_terminal_status.py`.
  - e2e `test_chat_stop_resend.py`, `test_shadow_chat_stop_resend.py`,
    `test_chat_terminal_activity.py`, `test_chat_stream_reader.py`.

**Exit:** every turn follows one observable lifecycle, and stale callbacks
cannot affect newer turns.

### Phase 5: Shell, panes and Spaces (high risk)

- **5F — Electron chrome, zoom, mobile shell, SSH approval** (1,180 lines,
  out 8 / in 13). This is low-coupling and can run first in the phase.
  `initElectronTitlebar` alone is 580 lines.
- **5A — Pane layout** (2,405 lines). This reopens the E1/E2 decision
  (baseline §8). Proceed only with a demonstrated benefit, such as a pure
  pane-tree module that the six existing layout fences exercise directly.
  Otherwise record "kept".
- **5B — Pane lifecycle** (950 lines, in 77). Covers frame creation,
  navigation, history, teardown, and the cross-pane hubs.
- **5C — Spaces UI** (1,495 lines, 10 sliced tests in
  `test_space_tab_groups.py`). Tab rendering, drag/drop and group menus move
  into a controller over the five existing `CuttleSpaces.*` modules.
- **5D — Workspace persistence** (62 lines). Small; fold it into 5B if no
  separate owner is justified.
- **5E — Shell services and rail.** Account, notifications, jobs badge, toasts
  and heartbeat go into their existing modules (`account_panel.js`, …). Rail
  customization moves to `shell_rail.js`. No generic "shell services" module.
- **Verify:**
  - `src/tests/shell/*`.
  - e2e `test_app_shell_layout.py` (the six E1 fences),
    `test_app_shell_navigation.py`, `test_shadow_chat_status_dots.py`,
    `test_video_background.py`.
  - Electron journeys by hand, since the packaged-host CI covers boot only.

**Exit:** `app_shell.js` initializes controllers, coordinates navigation and
wires shared events.

### Phase 6: Flask API (medium-high risk)

Python changes need a Flask restart (through the action form) for the smoke
test.

**6A — Route families.** Move each family to its existing owner as a
blueprint:

| Family | Destination |
|---|---|
| Flask restart | `api.flask_restart` |
| Shared media | `api.shared_media` |
| Mobile events | `api.mobile_companion` |
| Supervised | `api.agent_router.supervised` |
| User pairing | `api.auth_api` |
| Router | `api.agent_router` |
| Agents catalog/defaults/context | `api.agent_harness` |
| API keys | the settings/provider owner |

Static pages, assets, `/api/status`, `/api/health` and the other infra
endpoints stay on the root. Shell panes/workspaces have no backend owner yet;
decide in this phase whether `settings_routes` (which owns `ui_state.json`)
takes them.

**6B — `chat_endpoint`** (1,136 lines). Move one lane per commit, in this
order:
1. Supervised control (263 lines; it already ends in a terminal return).
2. Launch gates.
3. Shell action expansion.
4. `/help` text.
5. Router-config lane.
6. Route/retry lane.
7. Harness lane.
8. Pipeline lane.

Each lane moves into `chat_turn_workflow` or its existing owner. The route
keeps request capture, auth, SSE framing and `jsonify`.

**6C — Provider routes.** Muse, Hermes, Codex and OpenCode (16 routes) move to
`agents/<id>/routes.py`, following the existing `agents/claude/routes.py`
(`claude_bp`). The contract stays unchanged (pairs with 2A). Cursor models
move to `agents/cursor/`.

**6D — Dependencies.** Reduce the 261 lazy imports as their callers move.
`process_message_with_bot` stays as the `/api/sessions/send` compat entry.

**Verify:**
- `src/tests/chat/test_chat_turn_*.py`, `test_chat_coordinator_acceptance.py`,
  the P5-E/P5-F oracles, `test_architecture_boundaries.py`, and the harness
  and router suites.
- e2e `test_shadow_chat_stop_resend.py` (real child Flask).

**Exit:** `web_chat_api.py` is a composition root and transport boundary.

### Phase 7: Enforcement and final verification

- **Gates:**
  - Lower the `test_composition_root_growth.py` ceilings to the new line
    counts.
  - Add the new owners to the boundary test's acyclic and no-reverse-import
    rules.
  - Add one test per owner asserting the page delegates.
- **Audit:**
  - Re-run this audit's measurements and compare against
    `monolith-baseline.md`.
  - Confirm one owner per state machine.
  - Confirm no dead or duplicated implementations (in particular the six
    provider families and the history/session state).
- **Test runs:**
  - Full offline suite plus **every** `src/tests/e2e/` file.
  - Packaged-host on Linux and Windows.
  - Opt-in live agent, voice and mesh smoke runs that the user authorizes.
- **Docs:** update `repository-map.md` (frontend ownership table),
  `WEB_CHAT_API.md` and `AGENTS.md` ownership rows.
- **Deferred items:** revisit `formatMessage` decomposition, the terminal
  session list, and anything recorded as "kept".

## 5. Decisions

Decided at the Phase 0 review (2026-10-09):

1. **Module names follow repo convention** (`chat_<domain>.js`, `Cuttle*`
   namespace), so 1A is `chat_media.js`. The implementing agent picks names
   case by case, favoring what a newcomer would search for.
2. **Bubble TTS gets its own owner, `chat_tts.js`.** Playback is shared by
   bubble speaker buttons and voice mode; voice mode stays in `chat_voice.js`.
   The guiding preference: components independent and obvious to future
   agents and humans.
3. **Reopened decisions (3C sync timers, 5A pane tree) may proceed** when the
   evidence shows a performance or readability/ownership benefit. Performance
   comes first, readability and layout close behind. Record "kept" otherwise.

Open:

4. **Browser suites in CI.** The CI browser job runs 3 of 36 e2e files. Phase 7
   could widen it to the suites named here.
5. **Shell navigation coverage.** `test_app_shell_navigation.py` (14 cases)
   runs only against a live server, so it skips in every offline gate. 3B,
   3D and Phase 5 either point it at the shadow instance (`api.dev_instance`)
   or add an isolated equivalent first.

## 6. Findings log

Pre-existing issues found while moving code. Each is recorded here and left
unchanged (no behavior change during the refactor), unless the user schedules
a fix.

| Found in | Finding |
|---|---|
| 1A | With the image menu open over the lightbox, one Escape closes both. The menu's capture-phase dismiss listener clears the menu before the lightbox keydown handler checks for it. Pinned by `test_chat_media_lightbox.py`. |
| 1A | The lightbox element's own listeners (click, pointer, wheel, contextmenu) live on an element in the host body and close over the frame that built it. They are not signal-bound, so an unloaded chat frame stays reachable until another pane rebuilds the lightbox (`ensureCuttleLightbox` removes stale ones). Host window/document listeners are correctly torn down. |
| 1A | `parseMdImageDest` had no callers anywhere; it was dropped instead of moved. |
| 1C | Two near-duplicate `/api/chat/tts` request paths: `speakMessageFromButton` (bubble button) and `voiceSpeechForMessage` (voice mode). Both now live in `chat_tts.js`; they differ in their spoken-text fallback and voice-usage capture, so they were moved as-is rather than merged. |

## Appendix A: Tests that slice monolith functions by name

Each row is a test file that extracts these functions from `chat_page.js` /
`app_shell.js` source, either by name (`_extract(src, "name")`) or between
`span('    function name(', …)` markers. A slice that moves a listed function
migrates that test to the owner's interface in the same commit. A slice that
moves a function used as a span *end* marker must also update that marker.
The table lists what remains; slices remove rows as they migrate them.

| Test file | Monolith functions sliced |
|---|---|
| `test_achievements_app.py` | `migrateUILayout` |
| `test_agent_badge_segments.py` | `escapeHtmlInline` |
| `test_agent_cost_slash.py` | `isSendableComposerMessage`, `renderCuttlePricingHtml` |
| `test_agent_router_palette.py` | `buildCursorModelPaletteItems`, `cursorAgentSlashCommandsForPalette`, `filterSlashPaletteItems`, `harnessUsageSlashCommandsForPalette` |
| `test_agent_usage_slash.py` | `composerChipAgentId`, `filterSlashPaletteItems`, `harnessUsageSlashCommandsForPalette`, `isSendableComposerMessage` |
| `test_apps_list_footer.py` | `getAppsList` |
| `test_assistant_badge_all_harnesses.py` | `chatTitleParts`, `escapeHtml`, `escapeHtmlInline`, `isBareAgentChipLabel`, `normalizeAgentSlashChips`, `parseProjectOrGenericSlashHead`, `parseStoredSlashCommandHead`, `parseTitleSlashChips`, `sessionPrefsForHistory`, `stickyTitleChipsForSession`, `titleChipKey` |
| `test_bare_sticky_agent_send.py` | `applyStickySlashAfterComposerSend`, `clearAllSlashChips`, `composeMessageWithSlashChip`, `isNativeControlCommand`, `isSendableComposerMessage`, `sendMessage`, `sendWelcomeMessage` |
| `test_chat_agent_model.py` | `attachAgentIdentityToRequest`, `buildClaudeEffortPaletteItems`, `buildClaudeModelPaletteItems`, `claudeModelLabel`, `ensureCodexEffortForSend`, `isAgentDefaultStarred`, `loadClaudeEffortForPalette`, `loadClaudeModelsForPalette`, `persistClaudeModelSelection`, `prettyClaudeModelLabel`, `seedClaudeSupplementFromSessionData`, `sessionPin`, `starredAgentEffort`, `starredAgentModel` |
| `test_chat_archived_badge.py` | `refreshArchivedSessions`, `updateChatSessionArchivedBadge`, `updateChatSessionTitle` |
| `test_chat_attention_dots.py` | `notifyAssistantResponseReady` |
| `test_chat_composer.py` | `claimUnmarkedMessageEl`, `clearPendingAttachments`, `isImageAttachment`, `normalizeMessageContentForMatch`, `sendMessage`, `userAvatarInnerHtmlForChat` |
| `test_chat_followup_heal.py` | `createNewChat`, `healStaleGeneratingState`, `isSessionGenerating`, `loadChatSession`, `pageIsBackgrounded`, `setWelcomeComposerEnabled`, `thisTurnHasAssistantReply`, `turnAlreadyShowsAssistantReply` |
| `test_chat_history_delete_modal.py` | `openHistoryDeleteModal`, `requestDeleteChatSession` |
| `test_chat_history_search.py` | `historySearchQueryActive`, `loadChatHistory`, `loadProjects`, `reloadHistoryListKeepingSearch`, `softRefreshHistorySearchIndicators` |
| `test_chat_messages.py` | `annotateMessageProjects`, `applyChatHistoryPageMeta`, `authMessageOptsFromServer`, `availableSlashCommands`, `buildMessageAttachmentsHtml`, `buildVegaWrapHtml`, `clamp`, `cleanCodeCopyText`, `countVisibleChatMessages`, `escapeHtmlInline`, `formatFormReplyHtml`, `formatMessage`, `formatMessageWithAttachments`, `formatUserMessageForDisplay`, `isSafeMdHref`, `isTouchComposer`, `mdLinkChipLabel`, `normalizeMdHref`, `openFileInDefaultApp`, `parseButtonClickFromContent`, `parseButtonClickFromMessage`, `preferredModelFromMessages`, `projectFromMessageRecord`, `renderMdLinkChip`, `safeJsonParse`, `transcriptEndsWithAssistant`, `windowChatHistoryMessages`, `windowsPathToFileUrl` |
| `test_chat_page_js_syntax.py` | `_historyActionIcons` |
| `test_chat_terminal_status.py` | `remoteLiveStatusLooksActive` |
| `test_chat_widgets.py` | `applyHubLiveStatusPayload`, `createNewChat` |
| `test_codex_starred_effort.py` | `ensureCodexEffortForSend`, `sendMessage`, `sendWelcomeMessage` |
| `test_composer_draft_session.py` | `restoreComposerDraft` |
| `test_first_turn_agent_pins.py` | `attachAgentIdentityToRequest` |
| `test_gizmos_js.py` | `migrateUILayout` |
| `test_history_agent_badge_styling.py` | `escapeHtmlInline`, `slashCommandChipHistoryHtml` |
| `test_pane_space_drag.py` | `appendLayoutLeafToEnd`, `exchangeCarriedPaneWithLive`, `moveDraggedPaneToSpace` |
| `test_project_chip_persistence.py` | `canonicalizeChatSessionId`, `clearSessionPrefs`, `findAuthServerSession`, `handleSlashKeyDown`, `isHistoryProjectSectionExpanded`, `persistProjectForCurrentSession`, `resolveSessionProjectInfo`, `sessionHasStoredProject`, `sessionIdsEqual`, `switchChatProject`, `toAuthDbSessionId` |
| `test_rail_edit_mode.py` | `setRailEditing` |
| `test_restart_composer_js.py` | `composeMessageWithSlashChip`, `isNativeControlCommand` |
| `test_retired_pages.py` | `canonicalizeShellPage` |
| `test_slash_palette_selection_consistency.py` | `applySlashSelection`, `applySlashSelectionInner`, `buildCursorModelPaletteItems`, `buildMuseModelPaletteItems`, `composeMessageWithSlashChip`, `composerChipAgentId`, `getStickySlashCommandFromMessage`, `isAgentHeaderChip`, `isCursorNestedCommandChip`, `isCursorRelatedSlashChip`, `isHarnessNestedCommandChip`, `isNativeControlCommand`, `isSendableComposerMessage`, `isStickyAgentChip`, `isStickyCursorAgentChip` |
| `test_space_activity_dots.py` | `collectChatActivitySnapshot`, `scheduleChatActivityBroadcast` |
| `test_space_tab_groups.py` | `deleteSpaceGroupWithSpaces`, `dissolveSpaceGroup`, `isSpaceCtxOpen`, `measureSpaceDropLanes`, `openSpaceGroupBubbleFor`, `openSpaceTabMenu`, `placeDraggedSpaceTab`, `spaceGroupPillHtml`, `toggleSpaceGroupCollapsed` |
| `test_split_agent_feed.py` | `applyAgentFeedAvailability`, `mountNewLeafFrame` |
| `test_starred_agent_removal.py` | `getStickySlashCommandFromMessage`, `persistProjectForCurrentSession`, `restoreComposerDraftControls`, `saveComposerDraft` |
