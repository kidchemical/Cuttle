# Experimental Features & Achievements — shipped design

Two connected subsystems:

1. **Experimental feature flags** — one server-owned toggle surface for beta /
   non-default features.
2. **Achievements** — the first consumer of that flag: a Steam-style unlock
   system with toast + sfx + confetti.

Status: **implemented** (Python + client + Electron + tests). This file is the
design record and the teardown checklist; it replaces the original plan's
"decision requested" section with the decisions actually taken.

Decisions taken (owner calls, 2026-10-03):

| Question | Decision |
|---|---|
| Where does the flags UI live? | **A tab in the settings page** (`/settings_page.html?tab=experimental`) |
| "100M tokens" semantics | **From a single message.** Sub-agent child chats are excluded from every turn metric, so the parent message alone must earn it |
| Default state | **Off.** Experimental features are opt-in |
| Achievements surface | Trophy-case grid lives in the same Experimental tab (no second nav entry) |

Architecture rules held: owned slices under `src/api/`, no reverse import of
`web_chat_api`, settings stored via `settings_manager`, client logic as pure
`Cuttle*` namespaces, imperative idempotent SQLite DDL.

---

## 0. Findings that shaped the design

| Question | Answer | Consequence |
|---|---|---|
| Is there an existing feature-flag system? | **No.** Grep for `experimental|feature_flag|beta` finds nothing relevant. Closest precedents: `agent_harness/steer.py:58-73` (`agent_steer` dict + `CUTTLE_AGENT_STEER=0` kill switch) and `chat_tts.py:88-147` (own module + own settings key + own routes + allowlist normalizer). | Copied the `chat_tts` shape. |
| Does `SettingsManager` validate keys? | No — permissive both directions (`settings_manager.py:264-287`). Typed getters do their own default merge (`:297-325`). | The flag registry is the allowlist; `set_enabled` drops ids that are not registered. |
| Where do settings routes live? | `settings_routes.py`, `SETTING_FAMILIES` at `:48-85`; module docstring `:1-19` forbids touching `web_chat_api.py`. | Self-registering blueprint instead (the `chat_tts` precedent), registered in the same block as the rest. |
| Settings tab shell? | `settings_page_tabs.js` — ids read **from markup**, order-sensitive, contract-tested by `test_settings_page_tabs.py`. | A new tab is a markup-only addition + one `tabs.register()`. |
| Toast system? | `src/web/js/toast.js` — `showToast(msg, variant, options)`; iframe→parent handoff; CSS lives in JS only. | Extended `options` with `achievement` + `duration`; all ~125 existing call sites untouched. |
| Confetti? | **None.** Only ambient particles on `landing_page.html:1968-2022`. | New self-contained particle layer in `celebrate.js` — no CDN dependency. |
| SFX? | One asset (`completion-chirp.wav`), served free at `/sounds/<path>`, escalation ladder at `toast.js:66-110`. | New wav = zero backend cost; Electron IPC added for the obscured-window case. |
| Electron renderer? | **Same web UI** over HTTP (`electron/main.js:485-492`). | Toast + confetti are free; only sound-while-obscured needed IPC. |
| Token/duration telemetry? | `router_outcomes` already carries `total_tokens`, `latency_ms`, `recorded_at`, `target_agent/model`, `attempt_index` (`agent_router/outcomes.py:29-58`). | Achievements read it; no new instrumentation. |
| Long-turn caveat | `pinned_outcomes.py:207` clamps backfilled latency at 4h. | "24hr prompt" can only be earned by turns measured live via `perf_counter`. Documented in the achievement description. |

---

## 1. Experimental feature flags

### 1.1 Owned module

```
src/api/experimental/__init__.py    # public surface (is_enabled, enabled_flags, …)
src/api/experimental/flags.py       # registry + resolver (OWNER)
src/api/experimental/features.py    # THE registry rows (the only feature-aware file)
src/api/experimental/routes.py      # blueprint /api/experimental (transport only)
src/api/experimental/__main__.py    # python -m api.experimental list|get|set|reset
```

Storage: settings key **`experimental_flags`** → `{ "<flag_id>": bool }`.
An absent id falls back to the spec default (no migration for new flags);
an id not in the registry resolves **False**.

### 1.2 Registry = single source of truth

`FlagSpec(id, label, description, *, default, category, risk, since, needs_restart)`
drives the GET response, the Settings tab rendering, `python -m api.experimental list`,
and the runtime gates. Adding a feature = one row in `features.py`.

### 1.3 Resolver precedence

`CUTTLE_EXPERIMENTAL=0` kill switch → stored value → spec default → `False`.
Mirrors `steer.py:58-73`. The kill switch is **never persisted** — it is an
operator escape hatch, so a toggle during a kill switch reports the kill switch
instead of lying.

### 1.4 Runtime gate idiom

```python
from api.experimental import is_enabled
if not is_enabled("my_feature"):
    return  # behaves exactly as if the feature were never built
```

The `chat_tts.py:309-311` pattern. Every HTTP endpoint answers
`200 {success: false, disabled: true}` when off, so clients silently no-op.

### 1.5 Routes

| Method | Path | Auth |
|---|---|---|
| GET | `/api/experimental/flags` | `@authenticated_required` |
| POST | `/api/experimental/flags/<flag_id>` | `@owner_required` |
| POST | `/api/experimental/flags/reset` | `@owner_required` |

Validator `validate_flag_update()` mirrors `validate_completion_providers_update`
(`settings_routes.py:123-157`).

### 1.6 Settings tab

`data-tab="experimental"` + `#panel-experimental`, inserted between Agents and
Providers in matching order (tab order == panel order is asserted by
`test_settings_page_tabs.py`). Rows render from the registry, so the UI never
drifts from the backend. Toggle handler clones the LAN pattern
(`preventDefault` → optimistic → POST → re-GET → revert-on-error + toast).
The trophy case only appears while achievements are on.

---

## 2. Achievements (the first experimental feature)

### 2.1 Owned module

```
src/api/achievements/__init__.py    # public surface + on_turn_saved() seam
src/api/achievements/catalog.py     # 75 achievements (pure data)
src/api/achievements/evaluator.py   # telemetry → metric numbers (read-only)
src/api/achievements/store.py       # progress/unlock state (SQLite)
src/api/achievements/unlocks.py     # snapshot → progress → diff → events
src/api/achievements/routes.py      # /api/achievements (transport only)
src/api/achievements/__main__.py    # python -m api.achievements list|progress|scan|…
```

Store: `src/data/db/achievements.db` (per `src/data/db/README.md`, resolved via
`core.runtime_paths.data_db_dir()`). Vocabulary in code, state in SQL —
same split as `outcomes.py`.

### 2.2 Sub-agent exclusion (the load-bearing rule)

A sub-agent's turn rolls up into the parent chat's history, so counting children
would let a fan-out earn the single-message achievements. Every turn-based
metric filters `CAST(session_id AS TEXT) NOT IN (<chat_sessions where
parent_session_id IS NOT NULL>)`. Pinned by
`test_snapshot_excludes_subagent_child_sessions`.

### 2.3 The single-message ladder (the headline)

| Metric | Meaning |
|---|---|
| `max_single_turn_tokens` | `MAX(SUM(total_tokens) GROUP BY decision_id)` over non-child sessions — a routing decision is one user message |
| `max_single_turn_input_tokens` | same over `prompt_tokens` |

Ladder: 1M → 10M → 50M → **100M "Kraken"** → 250M → 1B (hidden "Singularity").
Lifetime 100M is a *separate* achievement ("Ocean") so the two never collide.

### 2.4 Catalog (75 achievements)

| Category | Count | Examples |
|---|---|---|
| Milestones | 9 | First Dive → Thousand Tides (Leviathan) |
| Token Ocean | 16 | A Million → Supernova (1B); Kraken (100M in one message) |
| Marathon | 13 | Warm Up → Night Shift (6h) → **Long Haul (24h)** → Never Sleeping (72h, hidden) |
| Variety | 7 | 3 / 5 / 8 harnesses; 10 / 25 / 50 / 100 models |
| The Router | 4 | Patience Pays → Escalation King (500) |
| Swarm | 4 | Spawner → Legion (1,000 sub-agent children) |
| Clockwork | 13 | After Hours (midnight–5am) → Around the Clock (all 24 hours) → Century Streak |
| Many Rooms | 5 | Polyglot Dev → Hall of Chats (100 chats) |
| The Wallet | 4 | Investment → Broke (10k USD, hidden) |

Every family is a visible ladder (first rung `common`, last `legendary`/`mythic`).
Hidden entries ship a `hint` and hide their description until earned.
`catalog.py` raises at import if any row references an unknown metric, so a typo
fails loudly instead of never unlocking.

### 2.5 Unlock flow

1. **Trigger** — `on_turn_saved()` from the `chat_turn_persist` assistant-saver
   tail, fire-and-forget on a daemon thread; never delays reply delivery.
2. **Evaluate** — one read-only `evaluator.snapshot()` pass.
3. **Diff** — progress is monotonic; crossing the threshold with
   `unlocked_at IS NULL` writes `unlocked_at` + an event row (idempotent).
4. **Deliver** — the client polls `GET /api/achievements/pending` on its own
   timer (20s active / 120s hidden). No new SSE, no tray integration, no shell
   heartbeat change — which is also what keeps the feature removable.
5. **Celebrate** — `CuttleAchievements.poll` → `CuttleCelebrate.celebrate`.
6. **Ack** — `POST /api/achievements/<id>/ack`, plus a localStorage set so a
   reload does not re-toast.

Bursts are batched 4 at a time with a 1.4s gap so a first-time backfill reads as
a sequence rather than a wall of toasts.

### 2.6 Client slices

| File | Namespace | Responsibility |
|---|---|---|
| `src/web/js/celebrate.js` | `CuttleCelebrate` | rarity tiers, toast card, confetti layer, sfx escalation |
| `src/web/js/achievements.js` | `CuttleAchievements` | polling, batching, ack bookkeeping, grid grouping, formatters |

Both follow the `spaces_*.js` IIFE tail (`window` + `module.exports`) and are
loaded in `app_shell.html` **before** `app_shell.js` with the `?v=` bust.
`achievements.js` self-schedules its timer, so `app_shell.js` contains zero
achievement code. The settings page sets `__CUTTLE_ACHIEVEMENTS_MANUAL = true`
so it renders the grid itself without also polling.

**Confetti** — self-contained fixed/pointer-events-none particle layer, scaled by
rarity (0 / 0 / 60 / 120 / 200), self-removing, gated on `animationsEnabled()`
(`prefers-reduced-motion` + `cuttleUiAnimations` + `notificationsEnabled`).

**SFX** — `src/web/sounds/achievement-unlock.wav` (1.6s C6→E6→G6→C7 chime,
generated by `.cuttle/scripts/make_achievement_chime.py`, stdlib only). Played
in-page when focused; `electron.playSfx('achievement-unlock')` when obscured —
without that escalation a background unlock is silently dropped by autoplay
policy, which is exactly when a long-turn achievement fires.

**Electron** — `resolveSfxWavPath(name)` replaces `resolveChirpWavPath()` behind
an allowlist (`NATIVE_SFX_FILES`); `ipcMain.on('play-sfx')` + `preload.playSfx`.
The renderer passes a **name**, never a path. `electron/assets/*.wav` is checked
in, so the new wav is duplicated there (asserted byte-identical by a test).

### 2.7 Routes

| Method | Path | Auth |
|---|---|---|
| GET | `/api/achievements` | `@authenticated_required` |
| GET | `/api/achievements/pending` | `@authenticated_required` |
| POST | `/api/achievements/<id>/ack` | `@authenticated_required` |
| POST | `/api/achievements/scan` | `@owner_required` |
| POST | `/api/achievements/<id>/grant` | `@owner_required` |
| POST | `/api/achievements/reset` | `@owner_required` |

---

## 3. Teardown (the reason the feature is split the way it is)

Removing achievements completely, leaving the experimental system intact:

1. delete the `register_flag(FlagSpec(id="achievements", …))` block in
   `src/api/experimental/features.py`;
2. `rm -rf src/api/achievements/ src/tests/test_achievements.py
   src/tests/test_achievements_js.py`;
3. delete the achievements blueprint block in `src/api/web_chat_api.py`;
4. delete the `on_turn_saved()` tail in `src/api/chat_turn_persist.py`;
5. delete `src/web/js/achievements.js` + `src/web/js/celebrate.js` and their two
   `<script>` lines in `src/web/app_shell.html`;
6. delete the `#experimentalAchievementsGroup` + trophy-case markup, the
   `__CUTTLE_ACHIEVEMENTS_MANUAL` script line, and the
   `loadAchievementsSummary` / `renderAchievementsGrid` / `rescanAchievements`
   functions + `loadExperimentalFlags` call in `settings_page.html`;
7. delete the `.achievements-*` CSS blocks in `settings_page.css` and the
   achievement card CSS in `toast.js`;
8. `rm src/web/sounds/achievement-unlock.wav electron/assets/achievement-unlock.wav
   .cuttle/scripts/make_achievement_chime.py`; drop the `achievement-unlock`
   entry from `NATIVE_SFX_FILES` (`electron/main.js`);
9. optionally delete `src/data/db/achievements.db`.

Adding a *second* experimental feature is cheaper: one `FlagSpec` row + the
package + the gated call sites. No new UI, no new settings route, no new tab.

---

## 4. Tests

| Suite | Covers |
|---|---|
| `test_experimental_flags.py` (22) | unknown-id-off, precedence, kill-switch values, duplicate rejection, redundant-default pruning, HTTP auth matrix, validation, CLI |
| `test_achievements.py` (39) | catalog integrity (≥40, unique ids, known metrics, hidden hints), evaluator math + sub-agent exclusion, monotonic progress, unlock idempotency, pending/ack, flag gating, HTTP contract |
| `test_achievements_js.py` (7) | node harness for both slices' pure helpers, shell load order, settings-page poller suppression, wav/electron-asset sync, Electron SFX allowlist |

Existing suites updated: none needed — the settings tab satisfied
`test_settings_page_tabs.py` for free, and `test_settings_routes.py` does not
enumerate non-`/api/settings` blueprints.

---

## 5. Known limitations

1. **4h backfill clamp** — `pinned_outcomes._MAX_BACKFILL_DURATION_S` means
   turns longer than 4h only count when measured live (`perf_counter`).
2. **Token semantics** — `router_outcomes` records *billed* tokens per attempt;
   Cursor usage blobs are cumulative billing. "100M in one message" therefore
   means 100M billed to a single routing decision, not 100M of raw text.
3. **`turns_total` is per-decision** — a fallback chain is one message, so the
   count is of messages, not harness invocations.
4. **`escalations` counts attempts with `attempt_index > 0`**, which is the
   escalation/fallback-chain signal.
5. **Autoplay policy** — in-browser unlocks can still be silent on a cold page;
   only the Electron path guarantees sound while obscured.
