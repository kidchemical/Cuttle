# Architecture organization and optimization — next phase

**Status:** execution authorized; A1/A2 complete; B1 and S1/S2 integrated and live-verified. Codex Stop/resend ownership fix activated; CH-000885 passed the live Stop/refresh/resend check on 2026-10-02. B2/B3 and C1 are reviewed and integrated; combined candidate passed 2,489 offline tests and 23 browser journeys. Normal Flask activation is pending for B2/B3; C2 and later slices retain their evidence gates.

**Inspected:** 2026-10-01, HEAD `a70dc21b`, initially clean working tree.  
**Predecessor:** [architecture stabilization](ARCHITECTURE_STABILIZATION_PLAN.md), completed Phases 0–7. This is a separate follow-up, not a reopening of completed phases.

## Recommendation

First make the regression gate trustworthy and fix confirmed inconsistencies. Then reduce the working context required for ordinary frontend fixes by moving bounded rendering and transport responsibilities behind the owners already established. Optimize runtime cost only after measuring it. Remove obsolete material only after verifying its consumers.

Execute **one slice at a time** with a manager review of the contributor's evidence and diff before assigning the next slice. Course corrections return to the same contributor session where practical. Later phases remain conditional on their stated evidence gates. A slice that finds no meaningful problem should close with “keep as-is,” without inventing a refactor. Stop for user review if a critical issue requires intervention or activation cannot be recovered safely.

The main goal is that an action-card rendering fix, history recovery fix, or pane-layout fix can be understood through its owner, interface, and tests. Smaller composition files are an expected consequence where justified, not a quota.

## What the inspection establishes

| Finding | Evidence at inspected HEAD | Consequence |
|---|---|---|
| The feedback's sizes are historical | `web_chat_api.py`: **9,856** lines; `chat_page.js`: **23,792**; `app_shell.js`: **7,704** | Record a new baseline; do not judge the next pass against obsolete snapshots. |
| Architecture maps disagree with current ownership | `repository-map.md` still describes removed launchers, settings HTTP in the root, and intermediate reverse imports. `WEB_CHAT_API.md` lists a pre-extraction route/import inventory. Actual settings owner is `settings_routes.py`; shared execution is in `chat_coordinator.py` and `chat_turn_workflow.py`. | Documentation truth is a small, immediate organization fix. Use the map to locate owners, then confirm in code. |
| A persistence policy inconsistency is already documented and tested | `ERR-20261001-001`; stream `_pipeline_save_assistant` in `web_chat_api.py`; `test_oracle_pipeline_stream_cancelled_row_divergence` | Fix as an explicit behavior change, separately from moving code. |
| The regression baseline needs revalidation | Historical closeout: 2,048 passed, 28 failed, 79 skipped, excluding `unit/`. Later commits changed tests and lifecycle behavior. | Neither “all green” nor “the same 28 failures” is established for HEAD. |
| Browser coverage exists but is outside the default collection | `conftest.py` ignores `e2e`; isolated Chromium coverage now exists in `test_chat_terminal_activity.py` and `test_shared_diff_modal.py` | Reuse these fixtures and explicitly run selected browser tests; do not create a competing test harness. |
| Rendering remains a substantial page concern | `formatMessage` still contains balanced JSON parsing, action-card HTML, supervised activity, and widget ingestion; `activateEnhancements` still wires action execution/watch behavior | Rendering and action effects are the strongest first frontend candidates. Existing pure owners should remain pure. |
| Browser byte transport remains intertwined with presentation | `processMessage` contains `getReader`, `TextDecoder`, chunk buffering, read timeouts, session adoption, progress paint, and pending-result recovery | Separate transport mechanics only after pinning the real event/abort contract. Keep lifecycle decisions in existing owners. |
| Configuration is still dependent on process cwd | `core/config.py`: `BotConfig.config_file = Path("bot_config.json")`; live callers include `settings_routes`, `local_llm`, and `query_tracker` | Investigate deterministic path selection. Do not delete this active configuration as Discord debris. |
| Non-wait child CLI execution lacks a durable process owner | This planning run's `spawn` returned two pending children which remained pending. `subagents.service.spawn(wait=False)` starts `run_child_turn_async`; that function creates `threading.Thread(..., daemon=True)`. The CLI returns and exits; CLI `wait` does not start pending parallel children. | Repair the command/process-lifetime contract in the existing subagent owner; no second scheduler or execution route. |
| Existing batching already solves some apparent optimization targets | Shell `pollLiveStatusHub` batches session IDs; `refreshPendingChangesPath` coalesces in-flight work; `syncSessionMessagesFromServer` uses hub status; `chat_usage_live.js` has a shared broker | Measure gaps instead of adding another cache, heartbeat, or poll coordinator. |

Most findings are source observations; the child-CLI pending result was observed during planning. The pending batch was cancelled and the audits were completed directly. No full pytest suite, live-provider smoke, or performance benchmark was run while preparing this plan. Subsequent slice reports must distinguish newly measured results from this planning snapshot.

## Preserve these boundaries

- Keep `chat_coordinator`, `chat_turn_workflow`, `chat_turn_persist`, delivery tokens, producer-owned status, and terminal cancellation. Do not merge sync and SSE merely to remove their intentional transport differences.
- Keep the established `CuttleChat*` decision/state owners. Move effects into explicit view/transport controllers only when that improves the boundary; do not turn pure modules into page-global accessors.
- Keep `CuttleSpaces` state/groups/order/activity/drop decisions, including one canonical drop target for preview and commit. Do not rewrite working Spaces logic because shell LOC barely changed.
- Keep extracted settings/projects/Git/actions/tasks owners; the absence of these routes from the Flask root is progress to retain.
- Keep BYO-CLI guidance, adapter discovery, and project-adapter validation/isolation. No installer revival or harness redesign.
- Leave workers, dashboards, auth policy, daemon lifetime, Android, and Electron outside this initiative unless a selected slice demonstrates a necessary contract change. Electron is excluded from this plan.
- Leave useful wrappers in place when they inject host effects, adapt a public interface, or keep a readable seam. Remove only redundant indirection with verified callers.
- No framework migration, bundler adoption, universal event bus, generic controller framework, alternate chat ingress, or broad renaming exercise.

## Execution order and gates

| Phase | Slices | Priority | Dependency / stop gate |
|---|---|---|---|
| A — Reliable evidence | A1–A3 | Required first | Current gates and maps are trustworthy before risky moves. |
| B — Confirmed defects and state ownership | B1–B3 | B1/B3 confirmed; B2 investigation first | Explicit behavior changes; no frontend extraction bundled in. |
| C — Chat render organization | C1–C3 | First decomposition candidate | Proceed after A and relevant B1 coverage; one card/view responsibility per slice. |
| D — Chat transport and recovery | D1–D2 | Higher risk, separately reviewed | Lifecycle/browser gates pass; C is not technically required. |
| E — Shell organization | E1–E2 | Conditional | Require evidence of pane/layout work needing unrelated shell context. |
| F — Measured optimization | F1–F3 | F1 measurement early; fixes conditional | Change only measured hotspots, one mechanism at a time. |
| G — Clutter and closeout | G1–G2 | G1 can run after A | Consumer evidence before removal; boundary checks before closure. |

Recommended first approval unit: **A1 → A2 → A3 → B1**, with a report after each. Take **B3** next if CLI child delegation is needed. Collect F1 measurements during A2 if convenient. Review those results before authorizing C/D/E implementation. B2 and G1 are independent follow-ups. Do not require every conditional phase to happen before declaring this initiative successful.

**Development-instance sequencing (investigation/recovery):** the startup listener audit (one Flask `app` on :8080 + same-`app` :8000 companion + optional :8888; details in [`development-instance-safety.md`](development-instance-safety.md)) confirms a loopback dev server needs no networking redesign. Only **S1 shadow bootstrap + S2 B1 journey** precede B1 activation (minimum implementation in the CH859 B1 worktree for S1 and CH860 for S2: one loopback HTTP server on the real app, no daemon/main bootstrap); **S3/S4 are deferred followup, not B1 prerequisites**. Final activation still rests on the actual existing external independent recovery, with the plan's remaining work restored after B1 — no permanent staging waits on a new rollback capability.

## Phase A — Make evidence and navigation reliable

### A1. Repair the maintained ownership references

**Owner:** `repository-map.md`, `WEB_CHAT_API.md`, and existing architecture/review documents. **Risk:** low.

1. Reconcile the maintained maps against current registrations, owner interfaces, and actual tracked paths. Remove claims that deleted launchers still exist; replace intermediate reverse-import and coordinator diagrams with current wiring.
2. Separate historical audit snapshots from current guidance. Preserve the stabilization outcome; mark old cleanup candidates completed/obsolete only after checking code.
3. Add a short task-to-owner index for message/card rendering, transport/recovery, pane layout, config, and their tests. Reuse these maps; do not build another exhaustive repository inventory.
4. For the representative tasks repeated in G2, record the files/regions needed, shared bindings touched, and cross-owner interfaces now. Capture these before changes so the later navigability claim has a comparison.

**Done:** each selected task leads to one owner and relevant tests; referenced files exist; guides do not instruct agents to re-extract completed work. Documentation-only diff. No runtime tests required for prose corrections.

### A2. Establish an offline regression gate at current HEAD

**Owner:** `src/tests/conftest.py`, `src/tests/README.md`, existing fixtures and affected suites. **Risk:** medium because tests can touch runtime state.

1. Check Node, Playwright/Chromium, and collection configuration before running. Retain existing vendor-CLI, temporary-auth-DB, router-store, process-kill, steering-server, and restart-file guards. Verify network/provider seams used by selected suites are fake or deliberately unavailable.
2. Run the focused lifecycle gate and explicitly selected isolated browser suites. Run the broad offline suite with the same exclusions as the historical comparison, recording pass/fail/skip identities and environment. Inspect excluded `unit/` files separately; do not silently enable them.
3. Classify failures as product defect, stale test seam/expectation, host prerequisite, or intentionally gated live test. Fix one cause per diff. A known failure needs an identity, explanation, and follow-up; a skip does not count as behavioral coverage.
4. Ensure required frontend/browser gates report a missing prerequisite as a blocked validation step rather than a completed phase. No live server/paid prompt is needed for this gate.

**Done:** current results can be reproduced; required slice tests pass; there are no unexplained failures in selected paths. Unrelated baseline failures remain visible with owners, rather than blocking all useful work or being dismissed as harmless.

### A3. Pin the product journeys that structural moves could break

**Owner:** existing Node behavioral tests and isolated browser/API fixtures. **Risk:** medium.

Use production assets and fake execution for: send and complete; slow/detached SSE; Stop then resend; switch chat during completion; refresh/history recovery; follow-up edit/drain; project selection; attachments; action-card confirm/cancel/watch; older-message prepend; Spaces drag/drop and split-layout restoration. Include standalone chat and shell iframe contexts, desktop and phone where the UI differs.

Add only missing journey coverage. Assert row counts, exactly-once paint/notifications, current-turn busy ownership, current-session targeting, scroll anchoring, and card locking. A positive control should show critical stale/duplicate assertions fail when the guard is removed. Reuse `test_chat_terminal_activity.py` and `test_shared_diff_modal.py`; avoid broad snapshot suites that pin incidental HTML.

**Done:** each upcoming risky slice names its behavioral/browser fence. Missing manual verification is reported explicitly. New tests do not use the user's live database, provider, restart control files, or installed agent login.

**Phase stop:** report current failures/gaps and correct maps before moving production boundaries.

## Phase B — Fix confirmed inconsistencies without restructuring neighbors

### B1. Give assistant-history persistence one policy

**Owners:** `chat_turn_persist.make_assistant_saver`, `chat_turn_workflow`, pipeline route adapters. **State:** persistent assistant rows; existing turn-scoped cancellation/staleness. **Risk:** medium; intentional behavior change.

1. Extend the row-level lane matrix for successful, empty-failed, cancelled, `ui == 'system'`, superseded, and supervised-owned results across sync/SSE harness, router, and fallback lanes. Reproduce `ERR-20261001-001` using the existing pipeline oracle.
2. Recommended contract: cancellation/system notices do not become assistant-history rows; legitimate nonempty failures and successful replies follow the shared saver policy. Preserve transport completion and user cancellation feedback independently of history persistence.
3. Delegate the pipeline stream saver to the existing policy. Keep action rewrite, captured project/request metadata, title hooks, delivery, and stale-token release order intact. Inspect the sync pipeline adapter for the same policy bypass rather than assuming it is equivalent.
4. Update the deliberately divergent oracle and backlog entry with the explicit before/after row evidence. Do not hide the change inside a refactor commit.

**Verify:** persistence/workflow/coordinator tests, P5-E/P5-F oracles, terminal/detach tests, Stop/resend and refresh browser journey. **Done:** one intentional policy, exactly one appropriate assistant row, no stale write or newer-turn unlock.

### B2. Make active config path selection deterministic

**Owners:** `core/config.py`, `core/runtime_paths.py`, `managers/settings_manager.py`, `settings_routes.py`. **State:** install-local persistent config plus process singleton. **Risk:** medium; compatibility work.

First trace which keys/callers still need `BotConfig`, which store owns each setting, and how repo-root versus `src/` cwd selects a file. Inspect schemas/paths without printing secrets. Choose one canonical owner/path using existing path conventions; do not introduce a third config store.

If inconsistent selection is reproduced, add temporary-directory tests for both cwd forms and absent files, then implement explicit path resolution. Any migration must preserve unknown keys, specify conflict precedence, be repeatable, and keep the original recoverable. Do not overwrite two divergent user files automatically. If consolidation is unnecessary, fix only the path contract and keep the two distinct stores.

**Verify:** settings, runtime paths, local-LLM configuration, query-tracker consumers; temporary stores only. **Done:** active consumers select the same intended configuration regardless of launch cwd, with documented state ownership. No deletion of live config or unrelated settings redesign.

### B3. Make the child CLI's lifetime contract honest

**Owners:** `api.subagents.cli`, `service`, and `turns`; shared coordinator remains the execution owner. **State:** persistent child/batch rows; active work belongs to a process that survives long enough to finish it. **Risk:** medium.

Reproduce CLI `spawn` without `--wait` in a subprocess with a temporary database and fake runner; exit the CLI and verify whether children reach a terminal state. Cover parallel and serial collection, timeout, cancellation, and the follow-up `wait` command. Existing in-process thread tests alone cannot establish this contract.

Choose the smallest supported contract: either dispatch non-wait work to an existing durable Cuttle process using existing submission ownership, or explicitly require `--wait` in one-shot CLI contexts until durable dispatch exists. Do not advertise detached survival while relying on daemon threads. The bounded CLI correction is preferable to creating a new background service solely for this feature. Update the global subagent runbook to match; do not silently restart existing stuck jobs.

**Verify:** `test_subagents.py`, isolated CLI subprocess coverage, cancellation/batch status, coordinator submission contract. **Done:** a successful spawn either has a live owner that completes the work, or clearly rejects the unsupported lifetime before leaving misleading pending rows.

**Phase stop:** report each bug fix separately. Existing coordinator ownership remains unchanged.

## Phase C — Make chat rendering independently understandable

### C1. Move action-card render planning to its existing domain

**Owners:** `chat_action_forms.js` for pure model/render planning, `chat_messages.js`/`chat_markdown.js` for message/block composition. **State:** none for pure planning; locks remain with existing card/state owners. **Risk:** medium.

Characterize `formatMessage`'s balanced-JSON recovery, fallback/malformed spec behavior, escaping, locked/selected states, nested previews, and watch snapshots. Move one card HTML/render-planning path out of the page into the existing action-form owner, with explicit primitive inputs. Keep network actions, DOM lookup, polling, and widget writes outside that pure owner. Preserve structured-block ordering and placeholder restoration.

**Verify:** action-form/message/markdown tests plus actual rendered card tests. **Done:** a card-markup fix can be made through the action owner/tests without reading the send lifecycle; page delegates one render operation instead of keeping a large inline renderer. Do not move all of `formatMessage` in this slice.

### C2. Separate action-card activation and lifetime from the page

**Owners:** existing action-form domain and `chat_activate.js` activation boundary; a cohesive action-card view/controller is permissible only if neither owns the DOM/network lifetime today. **State:** per-card wiring, watches/timers, scoped to container/session. **Risk:** medium-high.

Move card-specific DOM wiring and watch activation from `activateEnhancements` through a bounded interface such as mount/update/dispose. Reuse backend action execution and current Q&A resume semantics. Supply a small explicit host interface; do not pass the entire page or a bag of unrelated callbacks. Keep one writer for the answer bubble and one owner for a card's timers. Dispose on removed/replaced containers; define behavior for persisted-card refresh.

**Verify:** repeated activation does not duplicate handlers, requests, answer bubbles, or watches; correct originating session; confirmation/HMAC contract; restart-card recovery; multi-bar rendering. **Done:** ordinary card activation fixes stay in one effect owner and its tests.

### C3. Separate message paint from parsing and ingestion effects

**Owners:** `chat_messages`, `chat_markdown`, `chat_activate`; page composes a message view. **State:** visible message nodes and anchors, per active viewport. **Risk:** medium-high.

First characterize `formatMessage`'s widget-upsert fallback and `CuttleUsageLive` integration. Define when ingestion happens and its existing dedupe/patch behavior before moving anything. Then take one coherent paint path—append/prepend/update or message-navigation wiring—out of the page. Reuse existing record/windowing and activation functions. Keep message IDs/CH numbering, attachments, code/link escaping, scroll pinning, and user attention rules intact.

**Done:** rendering the same record repeatedly has the characterized effects, with no lost widget patches or duplicate actions; history prepend retains its viewport anchor. The page no longer needs the internals of that selected view path. Full render-pipeline rewrite and virtual scrolling are excluded.

**Phase stop:** demonstrate the smaller feature working context, physical code removed from the page, retained adapters, browser results, and any effects still intentionally page-owned.

## Phase D — Separate byte transport from chat recovery decisions

### D1. Isolate the current SSE reader

**Existing owners:** `chat_pending_result` for event classification/recovery, `chat_stop_state` for abort interpretation, turn/generation guards for lifecycle. **New responsibility if justified:** a browser chat transport module owning bytes/request-reader lifetime, not application state. **Risk:** high.

Write tests against the current reader protocol: split UTF-8/code points, split delimiters/JSON, multiple frames per chunk, malformed/unknown frames, EOF, busy response, disconnect, abort, stalled reads, and final response. Resolve any discovered parser defect in a separate behavior-change slice. Preserve existing hold/read timeout semantics during extraction.

Move `getReader`/`TextDecoder`/buffering into one transport owner that emits current event data and supports cancellation/disposal. It must not adopt sessions, paint DOM, persist rows, infer idle, or release busy locks. Trace ownership of pending reads and cancellation so detached readers do not leak; do not mask a real defect as a harmless move.

**Done:** page consumes events through one reader interface; transport tests need no page-global state. Existing pending-result classification and backend producer/subscriber lifetime stay authoritative.

### D2. Bound message synchronization/recovery effects

**Owners:** `chat_generation` cadence/lock decisions, `chat_pending_result` reconciliation, `chat_messages` record/windowing decisions. **Effect owner if needed:** a session-scoped history-sync controller around existing decisions. **Risk:** high.

Start with `scheduleNextMessageSync`/`startMessageSync`/`stopMessageSync` and `syncSessionMessagesFromServer`, not all of `sendMessage`. Capture session and turn generation at request start; specify who owns the timer, in-flight request, cursor, and teardown. Preserve shell-hub use, standalone fallback, hidden-page cadence, title/model/project hydration, optimistic-row claiming, and exactly-once notifications.

**Verify:** stale/out-of-order fetch after New Chat or session switch, hung-fetch recovery, detach/slow subscriber, Stop/resend, follow-up draining, idle-to-new-remote-turn transition, history refresh. **Done:** one selected sync effect lifetime; no duplicate logical busy or history state. Keep session-adoption rules with existing guards instead of reproducing them in transport.

**Phase stop:** review D1 before D2. Do not collapse the remaining composer/send workflow merely to hit a file-size target.

## Phase E — Shell decomposition only where navigation work is blocked

### E1. Prove the pane-layout boundary before moving it

**Owner today:** shell pane/layout functions in `app_shell.js`; backend pane/workspace stores through their current interfaces. `CuttleSpaces` owns Spaces decisions. **Risk:** low investigation, medium implementation.

Trace `snapshotLayoutTree`, `flatLayoutToTree`, leaf/group normalization, restoration, and persistence. Select a real layout regression or a representative pane task; record which unrelated shell regions it requires. If the boundary already supports a local fix, make that fix and stop.

If pure tree normalization/serialization is mixed with DOM reads, characterize it and create one pane-tree owner receiving plain snapshots. Preserve saved-layout shape and restore compatibility; no generic tree framework and no new server-side layout model.

**Verify:** existing pane/workspace tests plus nested split restore, close/collapse, active space, focused pane, and reload browser cases. **Done:** tree decisions have one owner, and Spaces state remains single-owned.

### E2. Move one shell effect cluster with explicit teardown

**Conditional owner:** pane view/frame controller for a proven frame-lifetime problem, or Spaces view for a proven rendering problem; choose one. **State:** DOM/frame handles and timers scoped to live panes or rail, shell retains top-level composition. **Risk:** high.

Move the selected rendering/lifetime cluster only after E1/characterization. Pass existing state/decisions rather than copying `spacesState`, pane maps, or activity caches. Preserve frame/session/project registration, focus broadcasts, history navigation, disposal, and refresh behavior.

**Done:** a concrete pane/view fix avoids unrelated shell context and no discarded frame retains active handlers/timers. If no such problem is demonstrated, skip E2. Working drag/drop stays untouched.

## Phase F — Optimize measured costs

### F1. Record a repeatable workload and cost baseline

**Owners:** existing isolated browser fixtures and the measured UI/backend subsystem. **Risk:** low.

Use deterministic fixtures for short/long history, one/four panes, repeated project panes, visible/hidden tabs, pending diffs, and a streamed turn with recovery. Record request counts/payload bytes, changed nodes/repaints, long tasks, render and recovery latency, and active timers/listeners after navigation. Report median and tail latency across repeated runs with environment and workload; startup/provider latency is a separate measurement.

Inspect both selected frontend cost and any corresponding backend query cost before choosing a fix. Batching, incremental paging, hidden-page backoff, and live-usage coalescing already exist. Do not infer a problem from LOC.

**Done:** a repeatable cost profile with named hotspots or a documented “no optimization warranted.” Set a measurable acceptance target for each selected hotspot before implementing it.

### F2. Fix one confirmed polling or backend-query hotspot

Trace request ownership across shell and standalone pages; fix redundant requests or polling lifetime only where F1 proves it. Reuse existing batching/coalescing owners and current authorization boundaries. For a measured database hotspot, inspect the real query plan and indexing in the existing store; no speculative cache, ORM migration, or blanket index additions.

**Done:** the selected request/query/latency metric improves against the same workload; completion/recovery latency and cross-device activity remain within the agreed baseline. Stale-cache or missed-new-turn behavior is a rejection.

### F3. Fix one confirmed paint/activation hotspot

Use the existing message/view/activation boundary to limit repeated parsing, highlighting, DOM scans, or unnecessary node replacement. Reuse already-windowed history; do not start with virtualization. Preserve code-copy, Vega, action forms, media, widget patches, selection, and scroll anchoring.

**Done:** measured render/long-task improvement with unchanged journey results. No change, or a readability/regression cost larger than the gain, means keep the existing implementation.

## Phase G — Remove proven clutter and retain the gains

### G1. Delete only verified obsolete material

**Owners:** existing cleanup ledger, actual script/UI owner, and its tests. **Risk:** low-medium.

Reconcile the old cleanup plan before selecting candidates: several launchers and graph routes are already absent. Inspect the still-present `landing_page_backup.html`, `start_api_server.py`/time-series stack, retired graph references, legacy test runners, and misleading comments. For each candidate record runtime/import/URL consumers, supported workflows, and replacement; absence from one `rg` is not a deletion proof.

Remove one verified unused cluster per diff, or keep it with an accurate purpose. Check platform scripts and externally invoked entry points; leave uncertain install-local shortcuts alone. Preserve required 410 compatibility responses until their contract is intentionally retired. “Pipeline” still names the active no-LLM fallback: do not delete it by keyword. Keep Discord REST operations, active execution tracking, query tracking, and live BotConfig consumers.

**Done:** fewer obsolete paths/false instructions; no broken supported boot/navigation/ops path. Historical review documents can retain old facts clearly labeled as historical.

### G2. Prove navigability and close the plan

Repeat representative tasks: action-card render change, history recovery change, pane-layout change, config fix. Record files/regions required, shared mutable state touched, cross-owner callbacks, and focused gate commands. Compare with A's baseline. Add only lightweight checks for new demonstrated boundaries—script load order, forbidden upward dependencies, coordinator bypass—not arbitrary LOC caps or source-text copies of implementation.

Update the maintained maps and tests guide, close addressed backlog entries, and publish outcomes/skipped candidates. Keep one maintained owner index rather than multiple competing architecture reports.

**Done:** selected ordinary fixes can be reasoned about locally; no duplicate state/decision owner; regression evidence is reproducible; measured optimization gains are real. Remaining large composition regions have an explicit reason to stay or a named deferred problem.

## Slice protocol and scorecard

### Execution record

- **A1 — complete (2026-10-01):** maintained maps reconciled at `4845233c`; removed-launcher references, extracted route registrations, reverse imports, execution/status ownership, and frontend decision/effect distinctions corrected. Added a task/symbol/state/test navigability baseline. The manager checked the contributor's claims against source and reviewed corrections before acceptance. Documentation only; link and whitespace checks passed. No runtime code, historical audit, or live process changed.
- **A2 — complete (2026-10-01):** test-only fixes developed in a separate worktree at `4845233c` and reviewed before integration. Browser requests now stay within the fixture origin; isolation startup fails closed. Corrected three stale fake/catalog expectations and split the generated Android prerequisite from its tracked-source assertion. Focused gate: **92 passed**. Chromium: **14 passed, no skips**. Broad historical-comparison command: **2,125 passed, 62 skipped, no failures** before the final Android test split; final targeted mobile file: **21 passed, 1 skipped**. The 62 skips are 41 deliberate live/scope opt-ins and 21 platform/fixture gaps, not passing coverage. Initial 29 failures were diagnosed before changes: 25 cleared through proper temp/Git/path/venv isolation, three needed fixture corrections, and one required an explicit generated-asset prerequisite. See `src/tests/README.md` for reproduction; no production source or live process changed.
- **A2 — startup follow-up reviewed:** log inspection found a failed, unintended Gradle APK build attempt during API import, before pytest's per-test guard existed. `pytest_configure` now disables automatic mobile rebuilding before collection. Added inherited-flag and true collection-state controls (no `PYTEST_CURRENT_TEST`, forced missing APK, fake thread constructor). Focused neighbors: **52 passed, 1 generated-asset skip**; final six hook tests: **6 passed**. No builder ran in the corrected verification. The older broad count is historical evidence, not proof that the original run was fully isolated.
- **A3 — B1 fences reviewed:** a temporary-database HTTP matrix measured six lanes and nine outcomes (54 cells plus an execution-guard control). Both pipeline lanes persist cancellation/system text and duplicate supervised-owned rows. All sync lanes drop useful failed replies; superseded harness/router sync turns can save stale replies while correctly retaining the newer turn's busy slot. These are intentional policy/lifecycle fixes for B1, not extraction work. The isolated Chromium Stop/resend/refresh fence now passes on desktop and phone, with an ordinary-reply positive control (**3 passed**), and is integrated as a test-only change. Buffered fake SSE does not prove chunk/detach timing or internal turn-guard rejection; fake history is checked separately by the backend matrix. Other journey fences remain prerequisites of their respective slices.
- **B1 — integrated, activation pending:** pipeline callbacks now use the existing assistant saver; sync lanes retain useful failure text, and superseded claimed sync turns skip post-run effects while preserving the newer busy slot. Stream construction captures the request before worker execution. Current-session action-card project precedence is preserved, with the ingress project used when the session project is absent. A proposed precedence change was rejected and reversed during review. Original scoped gate: **262 passed, 1 unmounted-project skip**; original manager broad gate: **2,194 passed, 62 skipped, no failures**. Production scope is two files, **+65/-59**, including a **14-line root reduction**; tests/backlog bring the selective B1 patch to **+862/-73**. After the shadow gates below, the manager selectively integrated the reviewed patch, preserved all unrelated tracked bytes, and verified that the resulting production source matches the tested combined candidate. The two-file runtime recovery patch passes its reverse check. No runtime activation has occurred; independent recovery and the daemon-owned restart choice remain required.
- **S1/S2 — implemented and integrated (Linux verified):** `api.dev_instance` snapshots working tracked source into private state/runtime, launches the real Flask app on an OS-assigned loopback port, verifies nonce/PID/source identity, and owns only its child process. The development composition bootstrap blocks vendor execution and external effects before importing the app; deterministic executor seams leave routing, coordination, streaming, auth, and SQLite persistence real. S1 isolation/negative gates: **37 passed**. Real HTTP policy/lifecycle gates: **34 passed**, **66 executions**, zero guard denials. The pre-B1 source control reproduces the unwanted assistant row. Manager verification against current main source plus B1: broad **2,255 passed, 61 skipped, no failures**; final focused shadow/HTTP/boundary **71 passed**; actual desktop/phone Stop-resend-reload and ordinary-reply browser journeys **3 passed, no skips**. Browser optional probes remain explicit real 403s; external fonts/CDN assets are aborted. The durable Stop SYSTEM notice is preserved independently of assistant-history policy. S3/S4 remain deferred follow-ups and are not activation prerequisites. Resume B2/B3 and the original conditional slices after B1 activation is verified.

- **Codex ownership handoff — integrated (2026-10-02):** CH-000878 took over the blocker from CH-000856. Muse implementation/review ran through supervised Cuttle children CH-000880 and CH-000879. A concurrent fix landed in main; integration hashes rejected overwriting it. The manager preserved that implementation and its 19 tests, then added cancellation-before-acquisition, task-cancellation cleanup, bounded probe reaping, owned-only resume publication, and heartbeat lifetime corrections. The shared process owner now offloads the registry sweep while preserving Windows fallback and descendant cleanup. Final main gate: **132 passed, 1 obsolete unsupported-agent case skipped**; neighboring Muse/harness/process gate: **88 passed, 41 platform/live/scope skips**. Only the current parent remained active at the runtime handoff check. Thread ownership is process-local; the earlier incident's exact competing writer remains unproven. The fix has not been activated by this manager, and the live Stop → refresh → resend test remains pending. Recovery for these manager corrections is checksum-guarded under `temp/codex-handoff-final-backup/`; it restores the preceding integrated fix without discarding unrelated changes. Resume B2/B3 after activation/live validation; C/D/E remain conditional on their evidence gates. Commentary/final concatenation is recorded separately as ERR-20261002-002.

- **B2 — integrated, normal activation pending (2026-10-02):** Reproduced cwd-dependent file selection with isolated copied production source. Existing daemon selection is now canonical: `core.runtime_paths.bot_config_path()` resolves checkout `src/bot_config.json`; the root copy is retained untouched, without merging divergent values. Missing-file reads hold defaults in memory until an explicit save. The two settings stores and singleton/setter contracts stay intact. Manager gate: **108 passed**, including config consumers, runtime paths, architecture boundaries, and real shadow-app HTTP/state journeys. Three regression tests fail against the pre-change source. Four reviewed files integrated; no live config bytes changed and no service restarted. Child CH-000862; evidence in `temp/architecture-resume/architecture-b2-gate.log` and `b2-prechange-control.log`.

- **B3 — integrated, normal activation pending (2026-10-02):** CLI `spawn`/`message` now reject missing `--wait` before database access or worker creation. A supervised spawn propagates polling-timeout evidence and cancels its own unfinished batch through the existing cancellation owner before exiting nonzero. CLI `wait` observes with serial advancement disabled; in-process service defaults remain compatible. No scheduler, new execution route, or production fake-runner switch was added. Manager gate: **90 passed**, including real subprocess/temporary-DB completion, serial/parallel/first collection, follow-up, timeout, cancellation, stale pending observation, fail-closed test bootstrap, coordinator, restart, and architecture neighbors. Four reviewed files integrated. Synchronous serial turns/message execution retain their documented runtime deadline limits; hard process-death recovery remains deferred. Child CH-000860; manager evidence: `temp/architecture-resume/architecture-b3-gate.log`.

- **C1 — complete and integrated (2026-10-02):** Card HTML/render planning and watch-bar markup now live in existing `CuttleChatActionForms`; page adapters retain preview recursion, balanced JSON extraction, placeholders, session stamping, activation, and IO. One explicit pure escaper is injected; no page-global/DOM/network dependency was added to the owner. Manager source verification confirms `chat_page.js` **23,848 → 23,597 lines (net −251)**. Nineteen baseline cases are byte-identical (including preserved malformed-input failure); Node rendering tests pass, and the browser card fence passed before/after the move. Reviewed readability and map corrections prevent future activation effects from entering the pure owner. Child CH-000862.
- **Combined B2/B3/C1 checkpoint:** isolated current-source candidate: **2,489 passed / 61 deliberate live/platform/fixture skips / zero failures**; separately selected Chromium suites: **23 passed / zero skips** (card paint, Stop/resend/refresh, real shadow Flask/persistence, shared diff and terminal behavior). First combined attempt had one SQLite fixture failure because manager broad/browser invocations shared a basetemp; corrected to distinct directories and reran broad successfully. No product workaround or guard relaxation. Logs and JUnit: `temp/architecture-resume/`, `temp/architecture-resume-candidate/temp/manager-browser.xml`. Six-file checksum-guarded recovery prepared and proven in a disposable copy (conflicts reject all writes; matching source restores only checkpoint runtime files). No live config/database edits, provider smoke, daemon/Electron changes, push, or Flask restart. Normal activation remains user-selected per AGENTS.md. Next slice after verification: C2 activation/lifetime characterization, then bounded implementation only when its fence is established.

### Development and activation safety

Documentation-only changes can use the active checkout with one writer per file. Test-harness changes and risky runtime slices use separate worktrees with explicitly assigned paths. Do not let parallel contributors edit the same files; integrate only after the manager reviews each result. Keep test databases, restart files, provider fakes, and captured logs isolated from the running install. A second checkout alone does not isolate process side effects or shared user-level stores.

Existing imports usually retain Python code until process replacement, but lazy imports, new CLI processes, configuration reads, and frontend reloads may observe changes earlier. Treat development, integration, and activation as separate steps. Retain a known-good checkpoint and consistent backups before persistent-data/config changes. The daemon restart protocol checks liveness; it does not roll back code or prove the chat journey works.

Before activating changes to startup, chat execution/recovery, persistent config, or the restart path, establish a recovery path independent of Cuttle chat. Complete offline verification first, report the concrete change, and use the appropriate restart choice card when required. If independent recovery is unavailable, keep the verified work isolated and stop before activation. Do not autonomously replace the active Flask/daemon to test a risky slice.

### Independent recovery and shadow instances

The Linux-verified shadow-app capability is now implemented; its runbook and
listener audit live in [development-instance-safety.md](development-instance-safety.md).
It starts no second daemon and does not activate code in the running host.
Daemon process-name ownership and fixed-port readiness remain separate S3
follow-ups; immutable-runtime rollback remains S4. Neither blocks B1 after
the candidate gates pass. Final activation still requires independent recovery
and the existing daemon-owned restart choice, followed by live verification.

### Review record

Before editing: recheck HEAD/working tree; read AGENTS, principles, and maintained map; identify behavior owner, state lifetime/reset, input/output interface, and neighboring contracts. Preserve pre-existing user changes. Characterize risky behavior before moving it. Commit a completed slice separately when authorized; do not push from the agent shell.

Each slice report must include:

- The concrete problem and evidence; whether behavior changed or only ownership changed.
- Files/symbols moved or removed; owner/interface before and after; callers and state affected.
- Focused tests, neighboring gates, explicit browser checks, pass/fail/skip identities, and gaps.
- Page/root LOC delta **and** total code delta including new modules, retained wrappers, and tests. Moving lines without reducing feature working context is not a success.
- For performance slices: workload, before/after metric, variability, and regression budget.
- Deferred issues, rollback path, current Git status, and the next proposed slice. The manager reviews before dispatching more work; escalate critical issues to the user.

**Reject or split a slice** if it introduces generic dependency bags, copies mutable state, imports upward, adds a parallel executor, changes persistence/event ordering accidentally, needs several unrelated owners, or cannot demonstrate the proposed benefit. Do not compensate for a weak boundary with another thousand-line controller.

Frontend-only changes use the ordinary hard refresh for manual verification. Python changes loaded by Flask may require the approved restart workflow; use the `flask.restart` choice card, not autonomous restart or process termination. Planning and offline verification do not require a service restart.

The user may stop after any accepted slice. Successful completion means the actual defects and selected navigation/cost problems are resolved; it does not mean every file is small or every candidate was refactored.
