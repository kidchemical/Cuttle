# Monolith baseline (Phase 0)

> **Historical snapshot (2026-10-09, source `62aca76`).** These are point-in-time
> measurements, not current coverage. Current owners:
> [`repository-map.md`](repository-map.md).

Measured state of the three composition roots before the monolith refactor
starts. Later phases compare against these numbers. The plan that uses them is
[`monolith-refactor-plan.md`](monolith-refactor-plan.md).

- **Source:** `62aca76` (`dev`, 2026-10-09), branch `refactor/phase-0-audit`.
- **Method:** regex inventory for the two JS files, `ast` inventory for the
  Python file. Function spans run from one top-level declaration to the next,
  so a span includes any statements that follow it. Counts are accurate
  enough to rank and compare, not exact to the line.
- Facts only. Proposals live in the plan.

## 1. Size and shape

| File | Lines | Top-level functions | ≥100 lines | ≥200 lines | Median fn | Mutable page state |
|---|---:|---:|---:|---:|---:|---:|
| `src/web/js/chat/chat_page.js` | 23,052 | 892 | 20 | 7 | 13 | 131 `let`/collection bindings |
| `src/web/js/shell/app_shell.js` | 8,089 | 305 | 9 | 3 | 15 | 47 |
| `src/api/web_chat_api.py` | 7,741 | 184 (99 route handlers) | — | — | — | 13 module-level containers/singletons |

`chat_page.js` is one IIFE (`(function() { … })()`), with top-level
declarations at four-space indent. `app_shell.js` is a classic script with
globals at column 0.

Growth gate (`src/tests/quality/test_composition_root_growth.py`): ceilings are
23,300 / 8,200 / 7,750 lines, and net growth per change is capped at 150 lines.
Phase 7 lowers the ceilings.

### Largest functions

| File | Function | Lines | Notes |
|---|---|---:|---|
| `web_chat_api.py` | `chat_endpoint` | 1,136 | Every `/api/chat` lane; see §4 |
| `chat_page.js` | `processMessage` | 828 | Send → fetch → SSE → final paint for one turn |
| `chat_page.js` | `formatMessage` | 463 | Markdown, widgets, cards and media in one pass |
| `chat_page.js` | `loadChatSession` | 449 | Session open/restore |
| `chat_page.js` | `applySlashSelectionInner` | 405 | Palette selection effects |
| `chat_page.js` | DOMContentLoaded init block | 394 | Listener wiring, timers, boot |
| `app_shell.js` | `initElectronTitlebar` | 580 | Frameless window chrome |
| `chat_page.js` | `syncSessionMessagesFromServer` | 244 | Server→DOM reconciliation |
| `app_shell.js` | `setupGroupSplitResize` | 233 | Split resize drag |
| `chat_page.js` | `renderSlashChips` / `addMessageToUI` | 215 / 212 | |
| `app_shell.js` | `initMobileAppShell` / `setupPaneReorder` | 212 / 198 | |

## 2. Effects inventory (frontend)

| Effect | `chat_page.js` | `app_shell.js` |
|---|---:|---:|
| `addEventListener` | 138 | 134 |
| `removeEventListener` | 2 | 12 |
| Listeners bound to an `AbortSignal` | 11 | 5 |
| Listeners on the parent/shell window or document | 10 (mostly `parentListenersAbort`-scoped; lightbox and media menu mount on the host document) | n/a |
| `setTimeout` / `setInterval` / `clearInterval` | 39 / 3 / 2 | 38 / 3 / 0 |
| `requestAnimationFrame` | 10 | 5 |
| Resize / Intersection / Mutation observers | 1 / 1 / 0 | 1 / 0 / 0 |
| `EventSource` | 0 (SSE read through `fetch` + `CuttleChatStream`) | 1 (`/api/activity/stream`) |
| `fetch(` call sites | 59 | 23 |
| `postMessage` call sites | 14 | 24 |
| `window.*` exports | 59 | 10 |

`setInterval` owners:
- `chat_page.js`: `startHistoryGeneratingPoll`, `armStreamStatusPoll`, and the
  message-timestamp refresher in the init block.
- `app_shell.js`: the jobs/toast IIFE, `initMobileAppShell`, and
  `initSshApprovalModal`.

**Window exports are an interface.** Generated HTML calls the 59 `chat_page`
exports through inline handlers (`chatPageDeleteSession`, `toggleTheme`,
`searchChats`, …), and e2e tests reach `window._e2eChat`. An extraction must
keep each export name working or change its callers in the same commit.

### Cross-frame message contract

The shell and the chat iframes communicate only through `postMessage`. Both
sides must keep these type strings stable.

- **Chat → shell:** `cuttle-chat-session`, `cuttle-chat-url`,
  `cuttle-chat-activity`, `cuttle-pane-activity`, `cuttle-activity-refresh`,
  `cuttle-open-panes-request`, `cuttle-pending-project`,
  `cuttle-flask-restart-linked`, `cuttle-completion-chirp`,
  `cuttle-terminal-delete`, `cuttle-terminal-rename`, `cuttle-theme-change`.
- **Shell → chat:** `cuttle-live-status`, `cuttle-open-panes`,
  `cuttle-pane-focus`, `cuttle-pane-teardown`, `cuttle-pause-streams`,
  `cuttle-pending-changes-data`, `cuttle-sessions-snapshot`, plus visibility,
  theme, animation, video, zoom and identity broadcasts.
- **Handled only by the shell:** `cuttle-apps-*`, `cuttle-auth-changed`,
  `cuttle-jobs-tab`, `cuttle-rail-action`, `cuttle-video-*`,
  `cuttle-shell-visibility-request`, `cuttle-pending-changes-refresh`.

### HTTP endpoints called

- **`chat_page.js`:** 47 distinct `/api/` prefixes. The heaviest are
  `/api/auth/sessions/…` (13) and `/api/agents`. It also calls the per-harness
  `/api/{muse,hermes,opencode,codex,claude}/{models,model,effort}` routes,
  `/api/cursor-agent/models`, `/api/agent-defaults/…`,
  `/api/chat`, `/api/chat-{cancel,steer,live-status,pending-result}`,
  `/api/chat/tts`, `/api/upload` and `/api/agent-context[/compact]`.
- **`app_shell.js`:** 21 prefixes. The main ones are
  `/api/chat-live-status-batch`, `/api/settings/ui-layout`,
  `/api/shell/{panes,workspaces}`, `/api/git/pending-changes`, `/api/workers…`,
  `/api/activity/stream` and `/api/ui-toasts`.

### Browser storage keys

`chat_page.js` uses 21 keys. Hot ones: `chatSessions` (23 sites), draft keys,
`lastChatSessionId`, `theme`, `autoResumeSession`, terminal sessions, pending
attachments, starred project/slash, message pins, history collapse/expand,
prompt history, slash filter, and the supervised jump ack.

`app_shell.js` uses 20 keys: page/rail/layout/splits/last-chat/UI-layout,
viewer identity, animation, background effect, and the
`cuttle_terminal_sessions_v1` and `chatSessions` keys it shares with the chat
page.

## 3. Module dependencies

### Script load order

`chat_page.html` loads 52 scripts. Every owner loads before the page, which
loads last apart from `optional_cdn.js`:

```text
ui_boot, emoticons, toast, completion_notifications, net_debug, video_background,
background_effect_blend, auth, supervised_control, chat_find, tasks_gizmo,
pending_changes_panel, git_push_report, diff_modal, git_commit_viewer,
agent_events/diff, query_changes, query_log_inspector, session_prefs,
chat_attention, chat_mutations, chat_project, chat_slash, chat_action_forms,
chat_activity, chat_action_cards, chat_attachments, chat_composer,
chat_agent_model, chat_usage, chat_messages, chat_activate, chat_markdown,
chat_prompt_history, chat_turn_guard, chat_stop_state, chat_followup_queue,
chat_pending_result, chat_generation, chat_usage_live, chat_stream, chat_vfx,
chat_subagent_fleet, chat_history_archive, chat_history_view,
chat_voice_segments, chat_voice_stars, chat_voice_narrator, chat_voice_recorder,
chat_voice_waveform, chat_voice, chat_page, optional_cdn
```

`app_shell.html` loads 24 scripts: shared boot/auth/toast, five `spaces/*`
modules, `session_prefs`, `activity_broker`, `whats_new`, `account_panel`,
`app_shell`, then `gizmos_model` and `gizmos_shell`.

### Owner namespaces referenced (references per page)

- **`chat_page.js` (42 namespaces):**
  - 20 or more: CuttleChatSlash 64, CuttleAuth 61, CuttleChatActivity 30,
    CuttleChatAgentModel 26, CuttleNetDebug 20.
  - 10–19: CuttleChatGeneration 19, CuttleChatMessages 19,
    CuttleSupervised 18, CuttleChatActionForms 18, CuttleFollowupQueue 17,
    CuttleStopState 12, CuttleChatAttachments 12, CuttleChatProject 11,
    CuttleChatPendingResult 11, CuttlePromptHistory 11,
    CuttleCompletionNotifications 10.
  - The remaining 26 appear fewer than 10 times. Several are used through a
    created instance, so their real use is higher than the count shows:
    `chatVoice`, the action-cards controller, `pendingChangesCtl`, and the
    usage-live broker.
- **`app_shell.js` (10 namespaces):** CuttleSpaces 50, CuttleAuth 9,
  CuttleShellNative 9, CuttleVideoBackground 8, then CuttleUiBoot,
  CuttleSessionPrefs, CuttleUsageLive, CuttleChatFind, CuttleAccountPanel and
  CuttleActivityBroker.

### Extracted chat owners (line counts)

These already exist and must not be duplicated:

| Module | Lines | | Module | Lines |
|---|---:|---|---|---:|
| `chat_action_cards.js` | 1,743 | | `chat_project.js` | 359 |
| `chat_slash.js` | 1,136 | | `chat_usage_live.js` | 334 |
| `chat_voice.js` (+ 5 voice modules, 1,106) | 1,017 | | `chat_markdown.js` | 299 |
| `chat_messages.js` | 1,005 | | `chat_generation.js` | 241 |
| `chat_action_forms.js` | 683 | | `chat_followup_queue.js` | 213 |
| `chat_pending_result.js` | 639 | | `chat_stream.js` / `chat_composer.js` | 199 / 199 |
| `chat_activity.js` | 615 | | `chat_usage.js` | 190 |
| `chat_agent_model.js` | 467 | | `chat_attachments.js` / `chat_history_archive.js` | 174 / 171 |
| `supervised_control.js` | 437 | | `chat_activate.js` / `chat_subagent_fleet.js` | 160 / 138 |
| `chat_find.js` | 371 | | `chat_prompt_history.js` | 135 |
| `chat_stop_state.js` / `chat_turn_guard.js` | 100 / 72 | | `chat_attention.js` / `chat_history_view.js` / `chat_mutations.js` | 64 / 39 / 29 |

Shell side: `spaces/*` (five modules, 1,048), `shared/activity_broker.js`,
`shared/session_prefs.js`, `shell/account_panel.js`, `shell/apps_page.js`,
`shell/whats_new.js`, `shell/desktop_update_policy.js`.

### Backend fan-out

`web_chat_api.py` imports 104 distinct internal modules: 36 at module level,
68 only lazily inside functions (261 lazy import statements). The largest
groups are `agent_router` (18), `agent_harness` (9) and
`scripts.utilities` (8). It registers 21 blueprints.

Reverse imports are limited to `api.doctor` and the shadow child bootstrap.
`test_architecture_boundaries.py` enforces this.

## 4. Backend route families

There are 99 `@app.route` handlers totalling 4,234 lines, plus 2,428 lines of
helpers. The remaining ~1,080 lines are imports, blueprint registration,
startup and `__main__`.

| Family | Routes | Lines | Existing owner the logic could live with |
|---|---:|---:|---|
| Chat turn + live turn (`/api/chat`, `-cancel`, `-steer`, `-live-status[-batch]`, `-pending-result`, `query-live-status`, `query-log`, `upload`) | 9 | 1,617 | `chat_turn*`, `chat_coordinator`, `chat_delivery`, `chat_live_status` |
| Harness model/effort (`/api/{muse,hermes,codex,opencode}/…`, `/api/cursor-agent/models`) | 17 | 677 | `agent_harness/agents/<id>/`; Claude already has `agents/claude/routes.py` (`claude_bp`) |
| Infra/diagnostics (status, health, doctor, LAN, network, jobs, toasts, debug, warnings, project commands) | 15 | 406 | mostly app-level; keep |
| Router (`/api/router/*`, `/api/agent-router/options`, `/api/turn-feedback`) | 7 | 264 | `agent_router` |
| Agent catalog/defaults/context | 5 | 249 | `agent_harness` |
| API keys (test/save/load) | 3 | 188 (+~200 in `test_*_key` helpers) | settings/providers |
| Shell panes/workspaces | 4 | 171 | shell state owner (none yet) |
| Static HTML/assets | 19 | 155 | app-level; keep |
| Supervised | 2 | 130 | `agent_router.supervised` |
| Flask restart | 3 | 107 | `api.flask_restart` |
| Mobile events | 3 | 81 | `api.mobile_companion` |
| Legacy sessions (`/api/sessions*`, `clear-session`) | 5 | 81 | compat; `process_message_with_bot` |
| Shared media | 4 | 61 | `api.shared_media` |
| User pairing | 3 | 47 | `auth_api` |

Same-shaped handlers: comparing the provider route families with provider names
normalized, setters are 0.84–0.98 similar to each other, and the
`*_effort`/`*_models` readers are 0.35–0.96.

### `chat_endpoint` structure (lines 3972–5107)

One `try` holds these lanes in order:
1. Native restart (52)
2. Supervised control (263)
3. `/help` text (48)
4. Project-action and action-form launch gates (~70)
5. Shell action expansion (56)
6. Command normalization (56)
7. Router-config lane (28)
8. Route/retry lane (103, with nested savers)
9. Harness lane (119, with nested savers)
10. Pipeline/auth fallback lane (117)

### Process-lifetime backend state

- `chat_sessions` + `session_counter`: legacy in-memory sessions, written by
  `get_or_create_session`.
- `_shell_pane_layout`: written by `api_shell_panes`. It is lost on restart and
  restored when the shell next POSTs its layout.
- The rest are constants and compiled regexes.

## 5. State lifetimes (chat page)

`chat_page.js` runs once per pane iframe. Its "process" is the iframe document.

| Lifetime | State (examples) | Writers today |
|---|---|---|
| Turn | `activeRequestController`, `activeEventSource`, `inFlightUserMessage`, `sendDispatchGuard`, `steeringText`, `inFlightControlRequestId`, `connectingWatchdogTimer`, `streamStatusPollTimer`, `_restartRecoverInFlight` | `activeRequestController`: 6 functions; `inFlightUserMessage`: 7 (`processMessage`, `sendMessage`, `sendWelcomeMessage`, `stopGenerating`, `submitVoiceTurn`, `detachLocalGenerationForNavigation`, init) |
| Chat session | `currentSessionId`, `lastSeenServerMessageId`, `chatHistory*` window, `messageSyncTimer`, `followupQueue`, `pendingAttachments`, `currentProject`, attention flags, `chatPinnedToBottom`, `_agentContextStatus`, `pendingMessageJump` | `currentSessionId`: 6 functions (`loadChatSession`, `createNewChat`, `adoptChatSessionId`, `saveChatSession`, `showWelcomeSplash`, init); read by 22 of 27 domain buckets. `currentProject`: 7 writers. `lastSeenServerMessageId`: 6. |
| Pane (iframe document) | lightbox/media-menu state (17 bindings), `runningSessionIds`, `formAwaitingSessionIds`, history search/filter/rename/delete state, archive caches, TTS audio, voice audio context, `hubLiveStatusCache`, `shellPaneFocused`, `localOpenPanes`, `projects`, `slashPaletteSupplement`, `turnBadgeBySession`, `sharedComposerState`, `_actionCardsController`, `_loadSessionSeq` | |
| Persistent (device) | the 21 storage keys in §2; `CuttleSessionPrefs` per-session preferences | |
| Persistent (server) | composer selection, attention, follow-ups, history (SQLite under the Cuttle home) | owned server-side |

Shell lifetimes: `columnState`, `paneSessionByColumn`, `paneProjectByColumn`,
`focusedColumnIdx` (pane map); `shellBackStack`/`currentPage` (navigation);
`lastUILayout` and the rail flags (layout); and the Spaces state through
`CuttleSpaces`.

The chat's identity is held in two places: `currentSessionId` in each iframe,
and `paneSessionByColumn`/`lastChatByColumn` in the shell. The
`cuttle-chat-session`/`cuttle-chat-url` messages keep them in sync.

## 6. Domain map and coupling

Each top-level block was assigned to a domain by name and position. Coupling
counts page-scope names (functions and state) crossing domain boundaries:
**out** = foreign names a domain uses, **in** = uses of its names from other
domains. Low totals mean an easy extraction.

### `chat_page.js`

| Domain (plan slice) | Lines | Fns | Out | In |
|---|---:|---:|---:|---:|
| History panel/search/archive/rename/delete (3A) | 3,485 | 172 | 83 | 98 |
| Model/effort controls (2A) | 2,777 | 89 | 26 | 108 |
| Slash palette + chips (2B) | 2,418 | 93 | 115 | 76 |
| Turn lifecycle: send/stop/generation (4A) | 1,560 | 27 | 91 | 57 |
| Message presentation (1B) | 1,408 | 48 | 51 | 63 |
| Media/lightbox (1A) | 1,129 | 53 | **4** | **6** |
| Session lifecycle (3B) | 1,100 | 31 | 110 | 107 |
| Project/cwd + pending changes + git menu (*no slice*) | 1,014 | 42 | 19 | 48 |
| Message sync + windowing (3C) | 753 | 28 | 61 | 44 |
| Composer: drafts/attachments/prompt history/enhance (*no slice*) | 751 | 40 | 32 | 48 |
| Init block + exports + wiring | 737 | 73 | 140 | 2 |
| Stream/status transport (4B) | 711 | 25 | 50 | 41 |
| Follow-up queue + steer (4D) | 584 | 29 | 27 | 25 |
| Transcript nav: pins/scroll/jump/copy/share (*no slice*) | 564 | 26 | 14 | 40 |
| Attention/unread markers (3A) | 450 | 24 | 33 | 42 |
| Sticky agent + shared composer selection (2C) | 418 | 29 | 35 | 36 |
| Context gauge (*no slice*) | 402 | 15 | 6 | 3 |
| Supervised activity cards (*no slice*) | 369 | 9 | 11 | 6 |
| Recovery (4C) | 333 | 11 | 33 | 17 |
| Action-card adapters (owner exists) | 285 | 23 | 14 | 13 |
| Theme/chrome menu/misc | 271 | 14 | 5 | 21 |
| TTS bubble speech (1C) | 247 | 9 | 1 | 9 |
| Subagent launchers/orbs (*no slice*) | 196 | 10 | 4 | 8 |
| Voice integration (4E) | 191 | 6 | 26 | 0 |
| Cross-pane/shell bridge (3D) | 116 | 9 | 9 | 20 |
| Terminal sessions (*no slice*) | 101 | 6 | 2 | 6 |
| Unassigned small helpers | 246 | 24 | — | — |

Most-shared page state, by number of domains touching it:
`currentSessionId` 22, `currentProject` 11, `slashPaletteSupplement` 8,
`projects` 7, `slashCtx` 7, then `followupQueue`, `runningSessionIds`,
`chatPinnedToBottom`, `shellPaneFocused` at 5 each.

Notable edges:
- Voice reaches into TTS private state (`_chatTtsAudio`, `_chatTtsActiveBtn`,
  `stopChatTtsPlayback`, `applyTtsTranscriptLayout`).
- The lightbox and media menu mount on the **host (shell) document** and rely
  on `parentListenersAbort` for teardown.
- Message presentation feeds media through `buildMediaThumbHtml` and
  `mediaKindFromUrl`.

### `app_shell.js`

| Domain (plan slice) | Lines | Fns | Out | In |
|---|---:|---:|---:|---:|
| Pane layout: split tree, sizing, resize, collapse (5A) | 2,405 | 75 | 49 | 70 |
| Spaces UI (5C) | 1,495 | 73 | 45 | 21 |
| Electron chrome / zoom / mobile shell / SSH approval (*no slice*) | 1,180 | 17 | 8 | 13 |
| Pane lifecycle: frames, navigation, history (5B) | 950 | 49 | 39 | 77 |
| Rail customization / apps (*no slice*) | 566 | 29 | 11 | 41 |
| Init + top-level statements (incl. 176-line message router) | 530 | 46 | 88 | 2 |
| Shell services: account, notifications, jobs, toasts, heartbeat, theme (5E) | 375 | 30 | 12 | 18 |
| Cross-pane hubs: live status, pending changes, open panes | 227 | 15 | 12 | 13 |
| Workspace persistence (5D) | 62 | 5 | 6 | 9 |

## 7. Test baseline

### Full offline suite

Command:
`CUTTLE_API_URL=http://127.0.0.1:9 .venv/bin/python -m pytest src/tests/ -q`

**Result: 3,837 passed, 0 failed, 55 skipped (4 min 53 s).** Node and
Playwright Chromium are present. Every skip is deliberate:

| Skips | Reason |
|---:|---|
| 41 | Live vendor smoke that spends tokens (`CUTTLE_AGENT_SMOKE`), plus opt-in smoke scopes |
| 8 | Windows-only helpers/paths |
| 6 | One each: no kotlinc/JDK; `CUTTLE_GROWTH_BASE` unset (CI sets it on PRs); missing models.dev fixture; no local chat DB; dogfood projects not mounted; one retired gauge case |

There are no known failures to carry forward.

**`src/tests/e2e/` is never collected by a directory run.** It is listed in
`collect_ignore` in `src/tests/conftest.py`, so the run above included zero
browser cases. Browser suites run only when their files are named:

- **CI browser gate** (`quality.yml`: action-card render, action-card
  controller, badge identity): **20 passed** with `CUTTLE_REQUIRE_BROWSER=1`.
- **All of `src/tests/e2e/test_*.py`**: see §7a.

### 7a. Browser suites

Command: `CUTTLE_API_URL=http://127.0.0.1:9 pytest src/tests/e2e/test_*.py`

**Result: 194 passed, 0 failed, 22 skipped across 36 files (8 min 11 s).**

| Skips | Reason |
|---:|---|
| 14 | `test_app_shell_navigation.py` needs a live Cuttle server; it always skips offline |
| 8 | `test_chat_cost_profile.py` is opt-in (`CUTTLE_COST_PROFILE=1`) |

The log contains one traceback printed by a background thread; no test
failed.

**Gap:** `test_app_shell_navigation.py` never runs in an offline gate, so shell
navigation has no offline browser coverage beyond `test_app_shell_layout.py`.

### Coupling between tests and source

These numbers set the cost of moving code:

- **80 test files** name `chat_page.js` and **15** name `app_shell.js`.
- 43 chat-page test files run page code in Node, 3 in a browser, and 30 only
  assert on source text. Including the Node/browser files that also inspect
  source, there are about 500 source-text assertions.
- **34 test files slice 139 named monolith functions** out of the source and
  execute them. They use either a name (`_extract(src, "name")`, `_fn_body`,
  …) or a `span('    function name(', '    function next(')` marker pair.
  Moving one of those functions, or a function used as a span end marker,
  breaks its test even when behavior is unchanged.
  - By slice: 2B 20, 2A 16, 3A 14, 1B 12, Spaces UI 10, 3C 9, composer 9,
    4A 9, 2C 7, 3B 7, project binding 5, 1A 2, other 19.
  - The remaining list is in the plan's appendix.
  - *Correction:* Phase 0 first reported 31 files / 91 names because it
    missed the `span(` marker style. Phase 1A found the gap.

Existing architecture gates:
- `test_architecture_boundaries.py`: reverse imports, acyclic `src/api`,
  coordinator funnel, SSE pump, manifest schema.
- `test_composition_root_growth.py`: ceilings and net growth.
- `test_state_outside_install_tree.py`, `test_frontend_assets.py`,
  `test_page_header_standard.py`.

## 8. Prior decisions this baseline inherits

The 2026-10-01→03 stabilization initiative (summarized in
`repository-map.md`) left these choices on record. A later phase that reopens
one must justify it with evidence:

- **Pane tree (E1/E2):** `snapshotLayoutTree`/`flattenLayoutLeaves` stayed in
  `app_shell.js`. E2 was skipped "without a demonstrated extraction benefit".
  Six browser fences guard the layout.
- **Deferred page-side by design:** message-sync/history timer mechanics,
  session-adopt navigation guards, and the running-flag store/paint.
- **Regression lesson:** P5-E moved stream execution into a worker but left
  status release in the SSE subscriber's `finally`. A subscriber detaching
  then released the busy lock while the worker still ran. Subscriber lifetime
  is not turn lifetime, and detached/slow subscribers must be part of any
  turn-lifecycle characterization (`src/tests/README.md`).
