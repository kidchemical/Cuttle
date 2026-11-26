# Studio display mode — plan and gap inventory

Status: **shipped behind the experimental flag `chat_studio_display` (default off), 2026-11-18.**
Plan approved with changes by expert review, reconciled 2026-11-18.
**The "Decisions" section below overrides the gap inventory where they differ.**

**Revised 2026-11-19 (theme and font rework):** Studio no longer has its own palette or
typeface. It is a shape layer (radii, spacing, shadows, motion, the work panel and timeline
layouts) over the active Cuttle theme: every colour derives from theme variables and no
Studio stylesheet sets a font, so themes, wallpapers and Text & fonts prefs apply as in
Classic. The vendored Inter was removed. Where the sections below say "Inter", "lavender
`#7048e8`" or "pastel backdrop", read "the theme's font, accent and page colour".

Deferred to v2: review card, finished-Tasks result snapshot, usage meters, router
notice, progress grid styling, Android parity, and the composer escalation notice.
Feature entry and teardown list: [experimental-features.md](experimental-features.md)
("Studio chat display").

## Goal

A second chat display mode, **Studio**, that looks and behaves like the v4
promo mocks. Classic stays as it is today. Switch: Settings → Appearance → Display
(Classic | Studio), behind one experimental flag `chat_studio_display`
(default off, `.cuttle/docs/experimental-features.md` practice).

The visual spec is the promo scene HTML, not prose. Every surface finishes
with a side-by-side screenshot (real UI vs the promo frame) for user review.

The promo scenes, video, and contact sheet were external design references and
are not included in this repository. Timestamps below refer to the promo video.

## Rules for the work

- **Same data, other drawing.** Studio reuses existing owners, APIs and data
  (fleet, peek, threads, supervision, orchestration, Tasks, agent review).
  No second engine. Backend additions only where the mock shows data Cuttle
  does not have (listed as *data gap* below).
- Mode mechanism mirrors flat messages: `ui_boot.js` sets a class on
  `<html>` (`cuttle-studio`) from localStorage; CSS keys off it; JS owners
  branch only where markup differs, reading one shared helper.
- Owners per AGENTS.md: no domain code in `chat_page.js`, `app_shell.js`,
  `web_chat_api.py` (line ceilings enforced).
- Targeted tests per surface; e2e screenshots for the side-by-side.

## Decisions (after expert review)

### Scope of v1

Keep: foundation, the user's five priorities (in-bubble work panel, push tray,
working pill / bubble agent header, steer receipt + ack in the thread,
supervision timeline), the composer prefix highlight and the Orchestrate chip
in the sent bubble, the playbook view.
Deferred to v2 (stay in Tasks, marked deferred): in-chat review card (a new
surface over `CuttleAgentReview`, not a skin), the finished-Tasks snapshot in the
final bubble (mirror fleet `frozen` snapshots), the live escalation animation,
progress grid / meter / router restyles and side-by-side meter placement,
Android comparison.

### Already exists (do not rebuild)

- Fleet live line: `subagents/fleet.py::fleet_entry()` uses `child.live_status`
  (`store.update_child_live_status`, `service.hydrate_parent_fleet`). No progress
  bar in the mock; never infer percentages.
- Fleet already mounts inside the turn: `chat_subagent_launchers.js`
  `renderSubagentLaunchersHtml()` (reply + live typing bubble). The work panel
  grows from this mount point.
- Peek layout is pluggable: `chat_subagent_peek.js` `layoutFor()` +
  `cuttleSubagentPeekLayout` (`side|center`). Push is a third value, `push`.
- Pause / Stop all / Resume: `chat_orchestration.js` already calls
  `/bindings/<sid>/hold` (`{stop:true}`) and `/resume`. The pill inlines them.
- Supervision history: `checks` (`chat_ops/store.py`) has typed outcomes
  (`stalled`, `nudged`+text, `violation`, `checkpoint`, `note`, `resumed`,
  `paused_alert`, `skipped`, `error`); `chat_supervision_model.js historyRow()`.
  `orchestration_telemetry/{record,store}.py` logs checks, sends, child rounds
  and escalation-tier spawns. The feed is a **projection**, not new storage.
- `subagents/extras.py ensure_parent_tasks()` / `patch_parent_tasks()` already
  create and complete parent Tasks items (but replace the item list wholesale;
  must become incremental).

### Truthful copy (hard rule)

- Guard: the `reference-transaction` hook refuses local branch updates
  (commit, merge, reset) on protected branches. It does not see pushes. Say
  "Guard blocked a commit to main" only when the forbidden command's tool result
  failed; otherwise "Flagged a forbidden command: …".
- System care observes processes; it ends nothing. Say "Leftover process
  ended" only when a pid+create_time seen at one check is gone at the next, and
  never claim Cuttle ended it.
- Escalation: derived only from a later child on the same task item at a higher
  tier. No "after two tries" claims unless the attempt count is recorded.

### Mode mechanism

- Effective Studio = user pref `cuttleChatDisplay=studio` **and** cached
  effective flag `cuttleChatStudioFlag=1` (written by the flag sync). `ui_boot.js`
  applies `<html class="cuttle-studio">` pre-paint from both; a flag fetch that
  says off, or the kill switch, drops it. The Display select is hidden until the
  flag resolves.
- One helper, new `src/web/js/shared/chat_display.js`:
  `CuttleChatDisplay { mode(), isStudio(), syncFlag(fetch), onChange(cb) }`,
  listening to `cuttle-experimental-flags-changed`, `storage`, and the frame
  appearance broadcast (peek `APPEARANCE` set) so nested frames follow.
  Owners receive `host.isStudio()` through `create(host)`; they never read storage.
- No forked markup: same DOM + additive classes/spans Classic CSS hides.
  Structural branches only in: work-panel mount (launchers), peek `push` layout,
  bubble agent header, supervision panel placement.
- Studio forces bubbles on (ignores `cuttleChatBubbles=0`); user text prefs still
  override Studio fonts. Translate the 1920px film scale to app density; do not
  copy px values. Light first; dark = tokens only.
- Classic stays exactly as today: Classic-visible changes (e.g. slash prefix
  bold) are gated to Studio.
- Inter is vendored (woff2 under `src/web/`), not loaded from Google Fonts.
- Agent identity colour comes from a `color:` field in each
  `agent_harness/agents/*/manifest.yaml`, exposed by `catalog.public_catalog`, read
  by a pure `src/web/js/shared/agent_identity.js`.

### Tasks contract

- Keep `done` canonical. Add optional item fields, omitted when empty:
  `owner: {kind: "chat"|"agent"|"user"|"group", id?: str≤128, label: str≤60, agent?: harness id≤40}`
  and `in_progress: bool` (invariant: `done ⇒ !in_progress`; set_done clears it).
  Colour is never stored.
- Fix `tasks_model._ensure_item_ids()` (it rebuilds items as
  `{id,text,done,children}` and drops unknown fields). New patch ops:
  `set_owner {id: owner|null}`, `set_in_progress [ids]`, `clear_in_progress [ids]`;
  `add` / `items` keep both fields. Shape validation in the pure model; chat
  ownership of `owner.id` checked in `tasks.py`. Item `PATCH /items/<id>` and the
  CLI (`--owner t3=CH-…|user`, `--working t3`) accept them; history records them.
- Assignment is explicit: `ChildSpec.task = "<gizmo>:<item>"` (`spec.py`,
  `--task` in `subagents/cli.py`). On spawn the service sets
  `owner={kind:chat,id:handle,agent,label}` + `in_progress`. Done → `set_done`;
  failed/cancelled/lost → clear `in_progress`, keep owner, leave open. Guard
  updates by assignment identity so an old child cannot finish a reassigned item.
  Linking a list to a chat is visibility, not assignment.
  `ensure_parent_tasks` becomes incremental and sets the same fields.
- Turn binding: `core/agent_cli_env.py operation_actor()` carries
  `CUTTLE_TURN_ID`; the gizmo stores `origin_turn_id` at create. The panel mounts
  only in that turn's bubble (fallback: the turn of the batch that references the
  list), so old conversations are never rewritten.
- Orchestrator brief (`orchestrator/model.py`) says "pass `--task` when spawning
  for a plan item". Runbooks `gizmos.md`, `subagents.md` updated.

### Supervision timeline contract

- Pure projection `src/api/chat_ops/timeline.py`; route
  `GET /api/chat-ops/sessions/<session>/timeline` on the existing `chat_ops`
  blueprint (`web_chat_api.py` has **zero** line headroom); CLI
  `python -m api.chat_cli timeline CH-…`. Strict session ownership; bounded
  cursor pagination; dedupe by source id.
- Sources: `checks` of every schedule whose supervisor is this chat (so "4 chats"
  aggregates), supervisor `send` operations with their **confirmed delivery**
  state (not the attempt), Tasks completion milestones; older rows from
  `orchestration_telemetry`.
- Item: `{schema_version, id, at, kind, chat, headline, detail, refs{pid?,branch?,command?,message_id?}, source}`.
  Kinds: `stalled`, `nudged`, `guard`, `care`, `milestone`, `paused`, `resumed`,
  `error` (`skipped`/`unchanged` hidden by default). Headline/detail built
  server-side (shared by UI and CLI); the client maps kind → icon.
- Guard: `triggers._commands` keeps the tool `failed` flag; the violation detail
  gets `blocked: true` when the forbidden command failed.
- Care: store leftover rows (name, pid, create_time, rss) in the check detail;
  disappearance at the next check → `care` "Leftover process ended".
- Retention: cap `skipped` separately from `HISTORY_KEEP=50`.

### Steer receipt and acknowledgement

- Cursor has no mid-turn channel (steers queue): no ack.
- Spike first on Codex (`codex_app_server_turn.py` echoes each steer as a
  `userMessage` item): correlate `steer_id`, user message id, turn id (captured at
  registration, before `steer.steer()` awaits delivery) and stream item ids;
  distinguish accepted/queued, consumed/echoed, undelivered. Cases: two rapid
  steers, completion during delivery, timeout, Stop/resend, reconnect/history.
  Then Claude (`--replay-user-messages`) and Muse (`muse_serve_turn.py`).
- Receipt = the harness echo. Ack = first agent message item / assistant text
  block **after** the echo, clipped to its first paragraph; never the next
  arbitrary segment. Persist on the steered **user** row:
  `metadata.steer = {delivered_at, reply:{text,at}}` (no new assistant row: CH-…-N
  numbering stays). Live updates via `chat_live_extras`. Main reply stays whole.
- Fallbacks: undelivered → "Not delivered"; no ack text → "Delivered · answered in
  the reply". Optional one-sentence ack hint in `steer.steer_text_for()`; no markers,
  no parsing. If attribution is unreliable, ship the receipt only.

### Lanes

Each lane works in its own registered worktree on its own branch off `dev`,
commits there, never pushes. The orchestrator merges into `dev`.

| Lane | Owns | Depends on |
|---|---|---|
| L0 Foundation | `experimental/features.py` FlagSpec; `shared/ui_boot.js`; new `shared/chat_display.js`, `shared/agent_identity.js`; Settings → Chat display row (`settings/settings_chat_preferences.js`, `settings_page.html`); new `css/studio/studio_tokens.css`; vendored Inter; manifest `color:` + `catalog.public_catalog` | first |
| L1 Tasks data | `gizmos/tasks_model.py`, `tasks.py`, `tasks_routes.py`, `tasks_cli.py`; `core/agent_cli_env.py`; `subagents/spec.py`, `types.py`, `service.py`, `extras.py`, `cli.py`; orchestrator brief; `gizmos.md`, `subagents.md` | parallel with L0 |
| L2 Supervision feed | new `chat_ops/timeline.py`; `chat_ops/routes.py`, `triggers.py`, `worker.py`, `store.py`; `orchestration_telemetry` reads; `chat_cli` | parallel |
| L3 Steer spike → receipt/ack | `codex_app_server_turn.py`, `agents/codex/adapter.py`, then Claude/Muse; `agent_harness/steer.py`; steered-row persist; `AGENT_STEERING.md` | parallel; gates L6 ack |
| L4 Work panel + tray + pills | `chat_subagent_launchers.js`, `chat_subagent_fleet.js`, new pure `chat_work_panel.js`, `gizmos/tasks_gizmo.js` (one row renderer for dock and panel), `chat_subagent_peek.js` (`push` + parent header), `chat_orchestration.js`, bubble agent header + working pill, `css/studio/work_panel.css` | L0, L1 |
| L5 Supervision UI | `chat_supervision.js`, `chat_supervision_model.js`, `css/studio/supervision.css` | L0, L2 |
| L6 Steer thread + thinking | `chat_threads.js`, `chat_thread_model.js`, `chat_live_thinking.js`, `css/studio/threads.css` | L0; L3 for ack |
| L7 Composer | `chat_slash_palette.js`, `chat_orchestrate_chip.js`, user-bubble chip in `chat_messages.js` | L0 |
| L9 Playbook view | `orchestrator/orchestrator_page.js`, `orchestrator_model.js`, css | L0 |
| Integration | every `chat_page.js` call site (≤ ~30 lines total; ceiling 13,096, now 12,993) and every `chat_page.html` / `app_shell.html` tag | orchestrator-owned, serial |

Rules: each lane writes its own CSS under `css/studio/`; only L0 edits tokens.
The work panel uses a class `chat_thread_model.js` ignores. Classic golden
screenshots before/after every UI lane, plus the Studio side-by-side.
Acceptance per UI lane: Classic unchanged, flag off / stale pref / kill switch,
active → finished → refresh, multiple turns and lists, narrow split panes (push
tray falls back to overlay below ~900px pane width), keyboard + focus, reduced motion.

## Gap inventory (mock → Cuttle today → work)

### Foundation (applies everywhere)

| Mock | Today | Work |
|---|---|---|
| Inter, large calm type, soft layered shadows, 22–30px radii, lavender accent `#7048e8` (`kit.css`) | Classic tokens in `design_tokens.css` / `themes.css` | Studio token layer under `:root.cuttle-studio`, light and dark |
| Agents named as a colour dot + plain name ("● Codex") | Harness · model · effort chips | One shared agent-identity helper (dot colour per harness, name), used by Tasks, fleet, tray, supervision, meters |
| Camera-steady layout: nothing jumps as content grows | Rows reflow on updates | Height-animated growth (`grid-template-rows` reveal), pinned headers |

### Composer and `/orchestrate` (intro, 0:00–0:10)

| Mock | Today | Work |
|---|---|---|
| Slash menu bolds the typed prefix as you type | Palette filters, no prefix highlight (`chat_slash_palette.js`) | Prefix highlight (Studio styling; harmless in Classic too) |
| "Orchestrate · Release crew" chip inside composer and inside the sent user bubble | Composer chip exists (`chat_orchestrate_chip.js`); sent bubble shows the raw `/orchestrate @id` prefix | Render the prefix as the chip in the user bubble |

### Playbook (0:10–0:16)

| Mock | Today | Work |
|---|---|---|
| Tier org chart: WORKERS → ESCALATION → EXPERT columns, arrows, rule chips (grind limit, escalate on repeat failure, supervisor cadence, pause/stop/resume) | Orchestrator app lists playbooks as cards with ladder dots (`orchestrator_page.js`) | Studio playbook view: tier columns with member identity, rule chips from playbook fields |
| Live escalation moment (Codex "2 tries ✕" → Claude "taking over") | Escalation exists server-side; no visual | Optional: escalation notice in the parent transcript using the identity helper (*verify data*: which event marks an escalation) |

### Fleet / work panel (0:16–0:23) — user callout

| Mock | Today | Work |
|---|---|---|
| Header pill "Orchestrating · Release crew" top-right with Pause / Stop all | "Orchestrating · playbook" chip + popover (`chat_orchestration.js`); pause/stop in `api/orchestrator/control.py` | Studio header pill with live dot, Pause and Stop all buttons calling the existing control routes |
| Two columns **inside the turn's bubble**: Tasks list left, sub-agent cards right | Tasks are a composer dock (`tasks_gizmo.js`); fleet cards a separate block (`chat_subagent_fleet.js`) | Studio work panel mounted in the turn's bubble, composed of Tasks + fleet; dock hidden in Studio while the panel is visible |
| Each task row shows its owner (dot + name); in-progress item has a glowing empty checkbox | Task items carry only `done` | **Data gap:** optional item fields `owner` (agent/chat handle) and `state: in_progress`, through `tasks_model`, CLI, REST, history; sub-agent spawn/finish fills them for linked lists |
| Fleet card: identity, one-line live status, progress | Card: identity chips, outcome, summary | Studio card markup on the same server fleet data |
| Click a card → side tray slides in and **pushes** the thread left; tray streams the child chat | Peek drawer overlays the chat (`chat_subagent_peek.js`, side/center layout) | Studio tray layout: thread shifts, no backdrop; same nested live chat frame |

### Steering (0:23–0:29) — user callout

| Mock | Today | Work |
|---|---|---|
| Live bubble shows a THINKING box with streaming reasoning | Collapsed "Thinking" disclosure (`chat_live_thinking.js`) | Studio: open-by-default compact reasoning box with fade-out tail |
| Steer goes into a reply thread labelled "↳ Steered into the live turn · no restart" | Reply threads exist (`chat_threads.js`) with "N replies" bar | Studio thread styling and label |
| **The agent's acknowledgement appears in the reply thread** | The answer continues in the main live bubble; no separate ack | **Data gap / spike:** a short ack per steer, captured as a thread reply. Options: (a) steer injection asks the agent to start with a one-line ack that the stream splitter attributes to the steer; (b) Cuttle posts its own delivery receipt and the agent's next text segment after the steer joins the thread. Spike on Codex first (`agent_harness/steer.py`, `docs/guides/AGENT_STEERING.md`), then Claude Code and Muse |
| Status line updates after the steer ("Fix done · writing CHANGELOG.md…") | Status line exists | Styling only |

### Review (0:29–0:35)

| Mock | Today | Work |
|---|---|---|
| Diff card with LIVE badge, +/− counts, per-hunk Keep / Undo, "✓ Kept", "↺ Undone · 2 lines restored" collapsing the hunk | Agent review exists (`api/agent_review/`, IDE review, pending changes panel) | Studio skin for the review card in chat and IDE; undone hunk collapses with restored-line count (*verify* the count is in the hunk API) |

### Supervision (0:35–0:42) — user callout

| Mock | Today | Work |
|---|---|---|
| Panel "Supervising <goal>", "N chats · check-in every 15 min", active badge | Supervision chip + popover with check history (`chat_supervision.js`) | Studio supervision panel in the supervisor chat (and from the worker's "Supervised by" badge) |
| One timeline of events, time + icon + headline + detail: stuck detected, nudge sent (with its text), system care ended a process (name, PID, memory), guard blocked a push to a protected branch, milestone (all tests green) | Checks in `chat_ops/store.py`; system care in `chat_ops/system_care.py`; guard is a git hook that never calls Cuttle (`chat_ops/git_hook.py`, violation wake in `worker.py`) | **Data gap:** one supervision event feed (kind, time, headline, detail, chat) merged from checks, nudges, system care actions and guard violation wakes; REST + `chat_cli` verb. Guard blocks reach it through the existing violation wake, not from the hook |
| "Supervised. Even at 2 AM." clock | n/a (marketing) | none |

### Machines / progress grid (0:42–0:48)

| Mock | Today | Work |
|---|---|---|
| Frame grid filling per machine | Progress grid feature exists | Studio styling only (*verify* against `feature/progress_grid`) |

### Result + router (0:48–0:55)

| Mock | Today | Work |
|---|---|---|
| Final reply bubble: summary text, Tasks list checked except the user's item ("Ship it · You"), attached video card with play button and caption | Render results attach (`render_result_attachments`); Tasks in dock | Work panel's finished state inside the final bubble; Studio media card. Owner "You" needs the `owner` field to accept the user |
| Usage meters beside the thread (5-hour / weekly / monthly windows), one turning red "Out of usage · resets 4:00 AM" | Usage meter gizmos exist (dock) | Studio meter styling |
| ROUTER card: "Claude is out of usage. Codex writes … instead." | Route notices in `chat_messages.js` (reroute/fallback labels) | Studio route notice card, plain sentence from the existing route metadata |

### Phone (0:55–1:01)

| Mock | Today | Work |
|---|---|---|
| Android notification "v0.8 is ready to ship", tap → voice call strip with meters, voice bubble, reply | Android app + completion notifications + Realtime voice exist (separate app repo) | Out of web scope; list as a follow-up comparison for the Android app |

### More tiles / close (1:01–1:14)

Marketing only; no product gap. Tiles name existing features (Tasks app,
IDE, worktrees, secret vault, auto-compact, reply threads, local models,
dashboards).

## Phases

1. Wave 1 (parallel): L0 foundation, L1 Tasks data, L2 supervision feed, L3 steer spike.
2. Wave 2: L4, L5, L6, L7, L9 on merged wave 1; integration after each.
3. Side-by-side review per surface with the user.
4. Expert review of the merged diff; Top Expert final assessment.
5. Docs (`experimental-features.md` entry + teardown), commit, `git.push` form.

## Teardown

Remove the flag row, the `cuttle-studio` class branch in `ui_boot.js`, the
Studio CSS layer and each owner's Studio branch. Data additions (Tasks fields,
supervision feed, steer ack) are useful to Classic too and stay.
