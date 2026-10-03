# Design — Experimental Features & Achievements

Plan for two connected subsystems:

1. **Experimental feature flags** — a single, server-owned toggle surface for beta /
   non-default features.
2. **Achievements** — the first consumer of that flag, a Steam-style unlock system
   with toast + sfx + confetti.

Status: plan only (no code committed). Researched against `2bc22c4c`.

Architecture rules followed: owned slices under `src/api/`, never imported by
`web_chat_api` in reverse, settings owned by `settings_manager` + `settings_routes`,
pure-logic JS slices as `Cuttle*` namespaces, imperative idempotent SQLite DDL.

---

## 0. Findings that shape the design

| Question | Answer | Consequence |
|---|---|---|
| Is there an existing feature-flag system? | **No.** Grep for `experimental|feature_flag|beta` finds nothing relevant. Closest precedents: `agent_harness/steer.py:58-73` (`agent_steer` dict + `CUTTLE_AGENT_STEER=0` kill switch) and `chat_tts.py:88-147` (own module + own settings key + own routes + allowlist normalizer). | Build the registry; copy the `chat_tts` shape. |
| Does `SettingsManager` validate keys? | No — permissive both directions (`settings_manager.py:264-287`). Typed getters do their own default merge (`:297-325`). | A new flag needs **its own** allowlist normalizer, or the UI can persist junk. |
| Where do settings routes live? | `settings_routes.py`, `SETTING_FAMILIES` registry at `:48-85`. Module docstring `:1-19` **forbids** touching `web_chat_api.py`. | Add a family row + validator, or register a dedicated blueprint like `chat_tts` does. |
| Settings tab shell? | `settings_page_tabs.js` — tab ids read **from markup**, order-sensitive, contract-tested by `test_settings_page_tabs.py`. | A new tab is a markup-only addition + one `tabs.register()`. |
| Toast system? | `src/web/js/toast.js` — global `showToast(msg, variant, options)`; `VARIANTS` at `:10-17`; iframe→parent postMessage handoff `:344-365`; **no custom icon/duration**; CSS lives in JS only. | Add an `achievement` variant + `icon`/`duration` options, or render a bespoke card. |
| Confetti? | **None.** Only ambient particles on `landing_page.html:1968-2022` (not reusable, not gated). | New module needed. Must respect `uiAnimationsEnabled()` (`app_shell.js:1904-1910`) + `prefers-reduced-motion`. |
| SFX? | Exactly one asset: `src/web/sounds/completion-chirp.wav`, served by `/sounds/<path>` (`web_chat_api.py:3393`). Player `playWebChirp()` at `toast.js:37-51`, escalation ladder at `:66-110`. | New wav is zero-backend-cost. Minimized-window playback needs new Electron IPC. |
| Electron renderer? | **Same web UI**, loaded over HTTP (`electron/main.js:485-492`, `:832`). No renderer bundle. | Toast + confetti are free. SFX-when-obscured is the only Electron work. |
| Token accounting? | `router_outcomes` table (`agent_router/outcomes.py:29-58`) with `total_tokens`, `latency_ms`, `recorded_at`; written live from `runners.py:46-57` and `dispatch.py:231-242`; read via `all_outcomes()`. `metrics_summary()` at `:407-437` is the aggregate precedent. | Achievements read this table; no new telemetry needed for token/duration ones. |
| Long-turn caveat | `pinned_outcomes.py:207` `_MAX_BACKFILL_DURATION_S = 4h` — backfilled turns longer than 4h are treated as *queued*, so `latency_ms` is clamped. | "24hr prompt" only counts for turns measured live via `perf_counter`. Acceptable; note it in the achievement description. |
| Server → client push channel? | `POST /api/toast` → `notify_tray`, drained by `GET /api/ui-toasts`, polled by `pollUiToasts()` (`app_shell.js:7230-7250`). | Reuse as the achievement event bus; no new SSE. |

---

## 1. Experimental feature flags

### 1.1 Decision: where does the UI live?

**Recommended: a tab in the settings page** (`/settings_page.html`), id `experimental`.

Why not a standalone page with a Flask icon:

- The tab shell already exists and is contract-tested; a new tab is markup + one
  `tabs.register()` line (`settings_page.html:1720-1742`).
- Feature flags are *settings* — they should be in the same place as `sandbox`,
  `discovery`, `chat_tts`, with the same scope badges and save semantics.
- A separate page means new nav plumbing (`shared_navigation.js:167` currently
  hardcodes the settings URL), a second lazy-load surface, and a second place to
  keep in sync.

Compromise worth keeping open: **Achievements** may deserve its own trophy page
(`/achievements_page.html`) for the grid/badges view, while the *flag* lives in
settings. Flags = configuration; achievements = content.

### 1.2 Owned module

```
src/api/experimental/__init__.py      # public surface: is_enabled, enabled_flags, specs
src/api/experimental/flags.py         # registry + resolver  (OWNER)
src/api/experimental/normalize.py     # allowlist merge/normalize (mirrors chat_tts.py:92-126)
src/api/experimental/routes.py        # blueprint /api/experimental
src/api/experimental/__main__.py      # python -m api.experimental list|get|set|reset
```

Storage: settings key **`experimental_flags`** — `{ "<flag_id>": bool }`.

### 1.3 Registry is the single source of truth

```python
FLAG_SPECS: dict[str, FlagSpec] = {
    "achievements": FlagSpec(
        id="achievements",
        label="🏆 Achievements",
        description="Unlock Steam-style achievements as you use Cuttle.",
        default=True,            # opt-out: on for everyone, but hideable
        category="fun",
        since="0.0.0",
        risk="low",              # low | medium | high -> UI styling + warning copy
        needs_restart=False,
    ),
}
```

One row drives: the `GET` response, the settings-tab rendering, the `GET`
progress/unlock polling gate, and `python -m api.experimental list`. Adding a
flag is one dict row plus the code that calls `is_enabled("new_flag")`.

### 1.4 Resolver (mirrors `steer.py:58-73`)

```python
def is_enabled(flag_id: str) -> bool:
    if (os.getenv("CUTTLE_EXPERIMENTAL") or "").strip().lower() in ("0", "false", "off", "no"):
        return False                      # global kill switch
    spec = FLAG_SPECS.get((flag_id or "").strip().lower())
    if spec is None:
        return False                      # unknown flag = off, never on
    try:
        raw = get_settings_manager().get_setting(SETTINGS_KEY, None)
    except Exception:
        raw = None
    if isinstance(raw, dict) and flag_id in raw:
        return bool(raw[flag_id])
    return spec.default
```

`enabled_flags() -> dict[str, bool]` for bulk client delivery.

### 1.5 Runtime gate idiom

```python
from api.experimental import is_enabled

if not is_enabled("achievements"):
    return  # silent no-op; the feature simply does not exist for this install
```

This is the `chat_tts.py:309-311` pattern. Rule: **gated code must behave
exactly as if the feature were never built** — no half-UI, no dead nav entries.
Where the UI can hide itself, the shell fetches `enabled_flags()` and hides.

### 1.6 Routes

Follow the `chat_tts` precedent (self-registering blueprint) rather than adding a
row to `SETTING_FAMILIES`, because achievements is a feature domain, not a settings
family — but keep the same auth contract.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/experimental/flags` | `@authenticated_required` | `{success, flags:[{...spec, enabled}], kill_switch}` |
| POST | `/api/experimental/flags/<flag_id>` | `@owner_required` | body `{enabled: bool}`; rejects unknown ids with 400 |
| POST | `/api/experimental/flags/reset` | `@owner_required` | back to spec defaults |

Validator: `validate_flag_update(flag_id, data) -> (ok, err, patch)` — shape of
`validate_completion_providers_update` (`settings_routes.py:123-157`).

### 1.7 SettingsManager additions

- Add `"experimental_flags": {}` to `_get_default_settings()` (`settings_manager.py:51-144`)
  and to `src/settings.json`.
- `get_experimental_flags()` / `set_experimental_flag(flag_id, value)` /
  `reset_experimental_flags()` in `settings_manager.py`, each delegating to
  `api.experimental.normalize` — but the manager must not import the feature module
  (direction rule). Cleaner: keep normalization inside `api/experimental/normalize.py`
  and have the manager expose only generic `get_setting`/`set_setting`. **Decision needed.**

### 1.8 Settings tab markup

```html
<button type="button" class="settings-tab" role="tab" data-tab="experimental"
        aria-controls="panel-experimental" aria-selected="false" id="tab-experimental">
  🧪 <span>Experimental</span>
</button>
...
<section class="settings-panel" id="panel-experimental" hidden>
  <div class="settings-group">
    <h3 class="settings-title"><span class="emoji">🧪</span> Experimental Features
      <span class="settings-scope" data-scope="server"
            title="Stored on the server — affects every device and client.">All devices</span>
    </h3>
    <!-- one .setting-item per flag, id="expFlag-<id>" -->
  </div>
</section>
```

Tab + panel must be inserted **in matching positions** (`test_settings_page_tabs.py`
asserts tab order == panel order). Placement: after `appearance`, before `agents`.

Toggle handler = clone of `toggleLanAccess` (`settings_page.html:1394-1423`):
`preventDefault()` → optimistic `.active` → `POST` → re-`GET` → revert-on-error +
`showToast(..., 'error')`. Register the loader: `tabs.register('experimental', loadExperimentalFlags)`.

Add a **"Restart required"** hint on flags with `needs_restart=True` (mirrors the
existing hint pattern; reload ladder in `.cuttle/rules/00-core.md` §3).

---

## 2. Achievements (first experimental feature)

### 2.1 Owned module

```
src/api/achievements/__init__.py      # progress(), unlock_count(), check_and_unlock()
src/api/achievements/catalog.py       # ACHIEVEMENTS defs (pure data + pure evaluators)
src/api/achievements/store.py         # SQLite store (OWNER of schema + reads)
src/api/achievements/evaluator.py     # aggregate source data -> metric values
src/api/achievements/unlocks.py       # diff progress -> newly unlocked -> enqueue event
src/api/achievements/routes.py        # /api/achievements
src/api/achievements/__main__.py      # python -m api.achievements list|progress|scan|grant
```

Store: new DB `src/data/db/achievements.db` (per `src/data/db/README.md` convention,
`data_db_dir()` from `core/runtime_paths.py`).

Schema (idempotent DDL in `store._connect()`, mirroring `outcomes.py:22-76`):

```sql
CREATE TABLE IF NOT EXISTS achievement_state (
    achievement_id TEXT PRIMARY KEY,
    progress      REAL NOT NULL DEFAULT 0,
    target        REAL,
    unlocked_at   REAL,                -- unix epoch, NULL = locked
    seen_at       REAL,                -- client acked the celebration
    detail        TEXT                 -- JSON: e.g. {"query_id": "...", "session_id": 922}
);
CREATE TABLE IF NOT EXISTS achievement_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    achievement_id TEXT NOT NULL,
    unlocked_at   REAL NOT NULL,
    delivered     INTEGER NOT NULL DEFAULT 0
);
```

Separation follows the `outcomes.py` precedent: **vocabulary in code, state in SQL.**

### 2.2 Catalog + rarity (Steam vocabulary)

```python
ACHIEVEMENTS = [
    Achievement(id="tokens_100m", title="Ocean", icon="🌊", rarity="legendary",
                description="Push 100,000,000 tokens through Cuttle.",
                threshold=100_000_000, metric="lifetime_total_tokens"),
    Achievement(id="turn_24h", title="Long Haul", icon="⏳", rarity="epic",
                description="Run a single agent turn for 24 hours.",
                threshold=86_400_000, metric="max_turn_latency_ms"),
    Achievement(id="first_turn", title="First Dive", icon="🐙", rarity="common", ...),
]
```

Rarity → toast styling + confetti intensity: `common` (toast only), `rare`
(toast + sfx), `epic` (+ confetti), `legendary` (+ full-screen + sfx).

### 2.3 Metrics — sourced from existing telemetry

| Metric | Source | Notes |
|---|---|---|
| `lifetime_total_tokens` | `SUM(total_tokens)` over `router_outcomes` | precedent: `outcomes.metrics_summary()` `:407-437`; also `dashboards/usage.py:_row_metrics` `:203-213` |
| `max_turn_latency_ms` | `MAX(latency_ms)` over `router_outcomes` | live-only above 4h (see `_MAX_BACKFILL_DURATION_S`) |
| `turns_completed`, `agent_hours`, `distinct_harnesses`, `distinct_models` | same table | `SUM(latency_ms)/3.6e6` |
| `night_owl` / `weekend_warrior` | `recorded_at` bucketed local time | `_bucket_start` pattern from `dashboards/usage.py:66-79` |
| `streak_days` | distinct local days with ≥1 turn | same |

`evaluator.py` reads one `all_outcomes()` snapshot and returns
`{metric: value}` — one query per evaluation, not per achievement.

### 2.4 Unlock flow

1. **Trigger** — after a turn completes. Hook where the result is already final:
   `api/chat_turn_workflow` or `chat_turn_persist.make_assistant_saver` tail, guarded
   by `is_enabled("achievements")` and fire-and-forget (never block reply delivery).
2. **Evaluate** — `evaluator.snapshot()` → per-achievement progress → `store.upsert`.
3. **Diff** — a `progress` value crossing `threshold` with `unlocked_at IS NULL`
   writes `unlocked_at` + an `achievement_events` row.
4. **Deliver** — `notify_tray`-style event onto the existing tray channel so the
   shell picks it up on its heartbeat (`pollUiToasts`, `app_shell.js:7230`).
   Add `variant: 'achievement'` carrying `{achievement_id, title, icon, rarity, sfx}`.
5. **Celebrate (client)** — `achievements.js` listener → `celebrate.js`.
6. **Ack** — `POST /api/achievements/<id>/ack` sets `seen_at`; missed celebrations
   are shown as a quiet badge (the notification-history pattern in `toast.js:189-212`
   already models unread state).

Also expose `POST /api/achievements/scan` + `python -m api.achievements scan` so
historical `router_outcomes` can be backfilled into unlocks on first enable
(same spirit as `dashboards/cli.py backfill-performance`).

### 2.5 Routes

| Method | Path | Auth |
|---|---|---|
| GET | `/api/achievements` | `@authenticated_required` — full catalog + progress + unlocked/seen |
| GET | `/api/achievements/pending` | `@authenticated_required` — unacked unlocks |
| POST | `/api/achievements/<id>/ack` | `@authenticated_required` |
| POST | `/api/achievements/scan` | `@owner_required` |
| POST | `/api/achievements/<id>/grant` | `@owner_required` (manual unlock for testing) |

All return 200 with `{success: false, error}` (never 403 from the flag) when the
flag is off, so the client can silently no-op.

### 2.6 Client slices (vanilla, `Cuttle*` namespaces)

New files, loaded in `app_shell.html` **before** `app_shell.js` with the `?v=` bust:

| File | Namespace | Responsibility |
|---|---|---|
| `src/web/js/achievements.js` | `CuttleAchievements` | registry + progress math + reduced/unlock filtering (pure logic, node-testable) |
| `src/web/js/celebrate.js` | `CuttleCelebrate` | toast card, confetti, sfx, rarity tiers |

Follow the `spaces_activity.js:130-136` tail exactly:

```js
const ns = (root.CuttleAchievements = root.CuttleAchievements || {});
Object.assign(ns, api);
if (typeof module !== 'undefined' && module.exports) Object.assign(module.exports, api);
```

**Confetti** (new; ~60 lines): DOM/canvas particles injected into a fixed,
`pointer-events:none` layer in the shell document. Gate on
`uiAnimationsEnabled()` (`app_shell.js:1904-1910`) + `prefers-reduced-motion`;
skip entirely for `common` rarity; scale count by rarity. Do **not** pull in
canvas-confetti via CDN — `optional_cdn.js` exists but an offline-safe local
implementation avoids a new failure mode.

**SFX**: `new Audio('/sounds/achievement-unlock.wav')`, lazily constructed, `.play()`
rejection swallowed (`toast.js:46`). Add `electron/assets/achievement-unlock.wav`
(check-in, must stay in sync) and generalize Electron playback:
- `preload.js` → `playSfx: (name) => ipcRenderer.send('play-sfx', name)` (allowlisted names)
- `main.js:1209-1225` → `resolveSfxWavPath(name)` (chirp path stays the default)
- `main.js:1267` → `ipcMain.on('play-sfx', …)` reusing the obscured-window logic at `:1270-1274`

Escalation ladder mirrors `playCuttleCompletionChirp` (`toast.js:66-110`): in-page
when focused, Electron-native when hidden/minimized/in-tray. Without this the
sound is dropped by autoplay policy whenever the unlock happens while the user is
away — which is exactly when a long-running-turn unlock fires.

**Toast**: extend `showToast` options with `icon` + `duration` (currently hardcoded
`TOAST_DURATION` at `toast.js:283`, icon from `VARIANTS`), or render the achievement
as its own card component. Prefer extending options — 125 existing call sites stay valid.

### 2.7 Achievements page (optional, phase 5)

`/achievements_page.html` + `src/web/js/achievements_page.js`: Steam-style grid,
grouped by rarity, locked ones showing hint text (unless `hidden: true`).
Nav entry via `shared_navigation.js` + a rail trophy button with an unread badge
patterned on `.rail-badge-notifications` (`app_shell.css:1838-2007`).

---

## 3. Phasing

| Phase | Work | Gate |
|---|---|---|
| 0 | `api/experimental` registry + normalize + resolver + CLI. Tests only. | `is_enabled` unit tests incl. kill switch + unknown-id |
| 1 | Routes + `SettingsManager` key + settings tab markup + toggle handler | `test_settings_routes.py` READS/WRITES matrices; `test_settings_page_tabs.py` markup contract |
| 2 | `api/achievements` store + catalog + evaluator + unlock diff + CLI | pytest with seeded `router_outcomes` db (pattern: `test_dashboards_usage.py:11-16`) |
| 3 | Event delivery on the tray channel + `GET/ack` routes + flag gate | end-to-end unlock → `GET /api/achievements/pending` |
| 4 | `achievements.js` + `celebrate.js` (toast card, confetti, sfx) + Electron `play-sfx` | node-harness slice tests; load-order test like `test_space_tab_groups.py:179-187` |
| 5 | Achievements page + backfill scan on first enable | manual UX pass |

Each phase is independently shippable; 0–2 are invisible to users.

---

## 4. Tests

- `test_experimental_flags.py` — resolver precedence (spec default < stored value < env kill
  switch), unknown id → off, normalization drops junk keys.
- `test_settings_routes.py` — add the new read/write rows to the `READS`/`WRITES`
  matrices (`:13-35`); auth enforced by decorator only (module docstring `:10-15`).
- `test_settings_page_tabs.py` — new tab satisfies the existing markup contract for free
  if ids/order/aria are right.
- `test_achievements.py` — catalog uniqueness, threshold crossing idempotency (re-scan
  does not re-unlock), progress monotonicity, metrics against a seeded DB.
- Node harness tests for `achievements.js` / `celebrate.js` pure logic, guarded by
  `shutil.which("node")` (pattern: `test_spaces_order_activity.py:16-56`).
- `test_architecture_boundaries.py` must stay green: `api/experimental` and
  `api/achievements` must not import `web_chat_api`.

---

## 5. Risks / open questions

1. **Autoplay policy** — a sound fired as a side effect of a background event is silently
   dropped. The obscured-window Electron escalation is required, not optional.
2. **Reduced motion** — confetti must respect `uiAnimationsEnabled()`.
3. **Electron asset duplication** — `electron/assets/*.wav` is check-in and hand-synced;
   a new sound adds a manual step to the release checklist.
4. **4h backfill clamp** — `pinned_outcomes._MAX_BACKFILL_DURATION_S` means "24hr prompt"
   can only be earned by turns measured live. Either accept it or relax the clamp.
5. **Token double-counting** — `router_outcomes` records per *attempt* (fallback chain
   included) and Cursor usage blobs are cumulative billing (`cursor_cli_tool.py:508-524`).
   The 100M number is therefore "tokens billed through Cuttle", not "tokens in one prompt".
   Pick the wording deliberately — the user asked for "a 100M token *prompt*", which is a
   different metric (`SUM(prompt_tokens)` over a single `query_id`, groupable via
   `dashboards/usage.py:_prompt_handles`).
6. **Settings permissiveness** — `SettingsManager` accepts arbitrary keys, so the
   normalizer is the only guard on flag shape.
7. **Manager↔feature import direction** — decide whether `settings_manager` knows about
   `api.experimental` (suggested: it does not; keep the feature module owning its own key).

---

## 6. Decisions requested before implementation

1. Settings tab (recommended) vs standalone page for flags?
2. Lifetime totals vs single-prompt tokens for the first achievement?
3. Should achievements default **on** (opt-out) or **off** (opt-in)? Given they are the
   poster child for the experimental surface, default-on + easy opt-out showcases the
   system; default-off is more honest about "experimental".
