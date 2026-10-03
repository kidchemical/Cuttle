# Runbook — Experimental features & Achievements

How-to for the shipped experimental flag surface and the Achievements feature.
Design record + full teardown checklist:
[experimental-features-plan.md](experimental-features-plan.md).

Both are **opt-in and off by default**.

---

## Turning things on/off

**UI**: Settings → 🧪 **Experimental** tab. Rows are rendered from the registry,
so anything listed there is a real flag on the server.

**Kill switch** (process level, overrides stored settings for every flag): add
`CUTTLE_EXPERIMENTAL=0` to `src/.env` and restart Flask.

```bash
.venv/bin/python -m src.scripts.cuttle_daemon.py   # or your normal start
```

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
| GET | `/api/achievements` | authenticated |
| GET | `/api/achievements/pending` | authenticated |
| POST | `/api/achievements/<id>/ack` | authenticated |
| POST | `/api/achievements/scan` \| `/reset` \| `/<id>/grant` | owner |

Both surfaces answer `200 {success: false, disabled: true}` while the flag is
off — clients silently skip rather than erroring.

---

## Adding a new experimental feature

1. Add one row in `src/api/experimental/features.py`.
2. Gate the code: `if not is_enabled("your_flag"): return …`.
3. Nothing else — the Settings tab, the CLI, and `flags_payload()` all read the
   registry.

Never add a flag-specific settings route or UI block. That is the whole point of
the registry.

---

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

## Where things live

| Thing | Path |
|---|---|
| Flag registry rows | `src/api/experimental/features.py` |
| Flag resolver | `src/api/experimental/flags.py` |
| Achievement catalog | `src/api/achievements/catalog.py` |
| Metric math (incl. sub-agent exclusion) | `src/api/achievements/evaluator.py` |
| Progress/unlock state | `src/data/db/achievements.db` via `api/achievements/store.py` |
| Turn seam | `on_turn_saved()` in `src/api/chat_turn_persist.py` |
| Client slices | `src/web/js/achievements.js`, `src/web/js/celebrate.js` |
| Trophy grid | `settings_page.html` → `#panel-experimental` → `#achievementsGrid` |
| Settings storage | `experimental_flags` key in `src/settings.json` |
