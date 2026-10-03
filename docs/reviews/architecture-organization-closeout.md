# Architecture organization closeout — 2026-10-03

The selected scope of the [organization plan](../architecture/ARCHITECTURE_ORGANIZATION_PLAN.md)
is complete. This closes the current initiative, not every known defect or
all possible decomposition. The last round was reviewed, corrected, tested,
and integrated directly, without new contributor turns. Its candidate was
isolated at `14e512ea`; concurrent visual-effects work is preserved and is
outside this review's validation claims.

## Final ownership and behavior

- **D1:** the retained-read correction remains intact. `CuttleChatStream.readEvents`
  owns native reads, timeout observations, decoding/framing and cleanup.
  Its explicit `holdMs`, `readTimeoutMs`, optional signal and synchronous
  `onEvents(batch)` interface contain no page/session/domain state. The page
  retains requests, event classification through `CuttleChatPendingResult`,
  session adoption, DOM effects, Stop/busy ownership and parked/history recovery.
  Whole batches are applied before stopping; the last final frame still wins.
  Existing LF-double-newline framing and hold/tick overshoot are preserved.
- **D2:** `CuttleChatGeneration` owns one page-held sync-claim state through
  `createSyncState`, `claimSync`, `isSyncCurrent`, `finishSync`. Existing page
  navigation sequence plus session and claim token fence response effects.
  Three reproduced defects are fixed: stale malformed live status painting a
  different chat, an earlier A→B→A visit reverting the current title, and an
  old request's cleanup releasing a replacement request. Fresh claims still
  coalesce; replacement remains strictly after 20 seconds. No new navigation
  counter, duplicated state or cancellation/persistence policy was introduced.
- **F2:** pending-change periodic scans use the existing per-project shell hub.
  Reconciled project selection now reports its path to the shell. Embedded
  panels do not also start standalone periodic timers. Standalone cadence and
  explicit focus, visibility, manual and post-operation refreshes remain.
  `app_shell.js`, Git routes, cache policy and polling intervals are unchanged.
- **G2:** maintained owner maps, extension guidance and test instructions now
  describe the implemented boundaries. Historical reviews remain snapshots.

The prior Stop review keeps red activity/query access while excluding activity
from saved-record logic; its tested correction remains intact. Earlier B/C
checkpoints retain shared execution/persistence ownership, canonical config
paths, supervised child lifetime, pure card rendering and explicit card effects.

## Navigability and retained code

| Representative task | A1 working context | Current owner and retained seam |
|---|---|---|
| Card HTML / effects | Large `formatMessage` and `activateEnhancements` regions | `chat_action_forms.js` pure render interface; `chat_action_cards.js` explicit root/host lifetime; page preview/session adapters |
| Stream bytes / history races | Inline native reads plus page sync variables and effects | `chat_stream.js` 199-line byte owner; `chat_generation.js` sync transitions; page fetch/navigation/paint and existing recovery owner |
| Pane restore / layout | Existing shell layout functions and frame maps | Kept after E1 task-local review and six real layout fences; E2 skipped without benefit |
| Config read / save | Cwd-dependent `BotConfig` path | Existing config owner calls `core.runtime_paths.bot_config_path`; root copy untouched |
| Pending-change polling | Existing shell hub plus redundant panel clocks | Same shell hub, restored project registration, standalone-only panel clock |

D1b/D2 reduce the page by **48 lines**, while total frontend source grows by
**214 lines**, including the new reader. F2 adds **3** total source lines
(page +1, panel +2). Compared with the direct candidate base, the final page
is **47 lines smaller** and total runtime source **217 lines larger**.
These are ownership and correctness changes, not a LOC optimization claim.
The candidate page is 22,494 lines; shell remains 7,704. The A1 page baseline
was 23,792. Other feature work makes checkout-wide size changes unsuitable
for attributing this round's improvement.

## F1 measurements and F2 result

Linux, Chromium 154, 1280×800; three fresh contexts per browser workload,
three fresh SQLite databases for query measurement. Native timers and
observer-only diagnostics; guarded same-origin fake APIs, no paid execution
or live state. Normal restored shell layout uses distinct chat IDs sharing
one project. Initial newest-ten readiness and explicit paging are separate.

| Workload | Before scans / 12 s (three runs) | After scans / 12 s (three runs) |
|---|---|---|
| Standalone, 20 rows | 1, 1, 1 | 1, 1, 1 |
| Shell, one pane | 1, 1, 1 | 1, 1, 1 |
| Shell, four panes / one project | 4, 4, 4 | 1, 1, 1 |

Request counts are exact per run, not server CPU/cache or latency measurements.
The four-pane reduction is **75% fewer periodic pending-change requests**;
the deterministic response body falls from 1,152 to 288 bytes per 12-second
window. A separate native-timer regression proves distinct projects each get
one scan and standalone retains one. The old-source control produces four
same-project requests and fails the desired regression.

Other baseline observations:

- Initial readiness medians (range): standalone 20 rows **477 ms (462–488)**;
  standalone 400 **468 ms (460–474)**; shell one pane **505 ms (492–518)**;
  shell four panes **766 ms (746–770)**. These include boot/network/scheduling;
  they are not isolated render CPU. Full 400-row paging needs 39 older-page
  requests, with deliberate 250 ms driver pacing and a 750 ms settle.
- Four 400-row sessions' newest-ten reads plus page metadata take median
  **about 1.8 ms per four-session sample**, across 25 samples per private DB.
  Each session performs three SELECTs; traced SQL and EXPLAIN show existing
  `idx_chat_messages_session` use (including a covering COUNT). No index,
  store consolidation or metadata cache is justified by this small fixture.
- Synthetic hidden input produces **zero history reads in 12 seconds**;
  foreground request observation is about 53 ms including a 50 ms driver
  observation interval. This tests policy, not OS/Electron minimization.
- Three native stream hold/detach journeys recover one parked reply without
  assistant-history rescue, about **4.08 seconds** after result availability,
  including polling and 250 ms driver observation. This is fixture recovery,
  not vendor latency or full backend performance.
- Shared live-status batching already works. Auxiliary approval/toast/jobs
  requests were counted but lack a demonstrated cost/UX budget for changes.
  Longtasks vary by run; no repeatable render hotspot was identified.

**F3 skipped:** no demonstrated paint bottleneck justifies changing working
render paths. Fonts/highlight/Vega CDN resources are intentionally blocked;
real media, action-card CPU, production-scale route enrichment, global live
listener/timer counts, real OS visibility and vendor behavior are not measured.
Those omissions remain explicit; the profile is not an exhaustive performance
claim, timing-threshold gate or new benchmark framework.

## Validation

- Final isolated broad offline gate: **2,650 passed, 61 deliberate skips,
  no failures**, with `unit/` excluded as in the historical comparison and
  `e2e/` collected separately. Skips are explicit live/scope/platform/fixture
  prerequisites; Windows behavior is not claimed by Linux checks.
- Final selected browser suites: **60 passing unaffected cases**, then all
  **3 polling regressions pass** after correcting fixture endpoint logging.
  Zero selected skips. Coverage includes native SSE, held native sync bodies,
  Stop/resend/reload, real shadow Flask/auth/SQLite persistence, terminal
  activity, card rendering/effects, pane layout and shared Git/diff behavior.
  One layout suite case is a URL helper; these are 63 suite cases, not 63
  independent end-to-end journeys.
- F1 full baseline: **8 passed, zero skips**. F2 measured follow-up:
  **3 passed, zero skips** (nine fresh contexts). Eleven public reader-owner cases and sync transitions are
  included in the broad gate. Browser prerequisites were available.

Rejected profile attempts (driver pacing, invalid running-state fixture) and
the initially misrecorded polling fixture were corrected, then rerun; their
results are not accepted measurements. No production workaround or shadow
allowlist relaxation was used to make a gate green. Install-local raw evidence,
JUnit, source patches and recovery proof are under ignored
`temp/architecture-resume/direct-closeout/` and the direct candidate's `temp/`.
The repeatable tests and these bounded results are tracked public artifacts.

## Activation, recovery and remaining work

Runtime commits: `7fa80648` (D1b/D2) and `edeb5b16` (F1/F2).
Four static frontend assets served by the running HTTP companion matched
the integrated file hashes; this is asset availability, not a live vendor/UI
journey.

This closeout changes frontend runtime assets only. Use normal hard refresh;
no Flask/daemon restart, second daemon or live provider smoke is needed.
Integration preserves another chat's visual-effects edits; those changes have
not been covered by this candidate's gates.

The install-local `temp/architecture-resume/direct-closeout/recover.py` first
checks **all five runtime files and backups** and refuses partial recovery
on conflicts. Run from an independent terminal at the checkout root:

```bash
.venv/bin/python temp/architecture-resume/direct-closeout/recover.py
.venv/bin/python temp/architecture-resume/direct-closeout/recover.py --apply
```

It restores the pre-closeout page, generation module, HTML and pending panel,
removes the new stream asset, and retains the captured visual-effects edits.
It never touches databases/config/history, processes or services. Conflict
refusal and complete restoration were proven in a disposable copy. Later
source edits require a new reviewed recovery, not bypassing the checksum guard.

Deferred follow-ups retain owners and evidence:

- **S3/S4:** configurable normal listeners and explicit daemon instance
  ownership; immutable-runtime rollback. Not prerequisites of completed B1
  or this initiative. Port 8000 is a **current same-app HTTP companion** used
  by Electron/Android, not a tombstone; see the listener audit in
  [development safety](../architecture/development-instance-safety.md).
- **ERR-20261002-002:** Codex commentary/final assembly in its harness owner.
- **ERR-20261002-003:** share prepared signed-card identities across workflow
  display and persistence, with cancellation/project/security contracts pinned.
- **ERR-20261002-005:** raw widget fallback identity/replay semantics before
  changing suppression or DOM ingestion.
- **ERR-20261003-001:** supported Jobs insight destination before pruning its
  remaining graph-era consumer.

These are separate behavior/product work, not unfinished extraction hidden
by this closeout. No new scheduler, framework, ingress, global policy payload,
or broad networking redesign was added.
