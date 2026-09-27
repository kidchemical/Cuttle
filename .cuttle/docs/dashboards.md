# Dashboards

Hub page for tracked landscapes (rail: **Dashboards**). First live tile is **Model Benchmarks**.

## User UI

- `/dashboards_page.html` — catalog
- `/dashboards_page.html?d=model-benchmarks` — 3D Plotly landscape (turntable or Unity-style free cam), source selector, chip filters, optional sweet-spot zone

Model Benchmarks supports DeepSWE, SWE-bench Verified, and Aider Polyglot. The Aggregate view groups identical model names and averages each model's best listed score per available benchmark with equal weight. Its chart shows benchmark count, mean score, and cross-benchmark score spread. This is a rough summary across different tasks and harnesses, not a controlled ranking. Cost and duration are only populated where a source reports them.

Each source has its own score definition: DeepSWE pass@1, SWE-bench Verified resolved percentage, and Aider Polyglot pass@1. Filters include provider, reasoning effort, and harness. **NEW** marks DeepSWE configs first seen by Cuttle in the last 48 hours.

**My Cuttle Performance** (`?d=cuttle-performance`) uses the same 3D widget, one point per agent / model / reasoning effort, built from *your* turns in `src/data/router_outcomes.db`:

- **Router** turns are recorded by `agent_router.dispatch` (one row per attempt).
- **Pinned** turns (starred agent, chip, `/cursor`, `/codex`, …) are recorded by `_run_pinned_harness_turn` in `web_chat_api.py` (`source=pinned`). Native controls (`/cost`, `/muse model …`, session clear) carry `meta_command` and are skipped.
- Turns from before pinned logging existed come from `backfill-performance` (idempotent, keyed by message id and `query_id`).
- **Accept score** = 👍/👎 on the reply (chat footer → `POST /api/turn-feedback`) → Jev label → finished without error. With no feedback it barely separates configs, because most runs finish.
- Axes: accept score, finished %, median turn time, cost / turn (Cursor reports none), output / total tokens per turn, turns (log). Filters: provider, reasoning, agent, plus a Turns picker (all / pinned / router) and a window (7 / 30 / 90 days / all time).
- **Jev labels** (`api/jev/labels.py`) run as a cheap batched classifier. Thumbs, errors, cancels and transport failures are labeled by rule without a call. Other turns go 10 per System One call with the ask, a trimmed reply, and your next message ("did the user push back?"). Accept scores between 0.4 and 0.6 count as unsure. Unlabeled turns are labeled in the background on page load and hourly (`jev.label_turns`). Manual runs: menu **Label with Jev**, or `python -m api.jev label --all`. Cache: `src/output/dashboards/jev_turn_labels.json` (override with `CUTTLE_JEV_LABEL_CACHE`).

**Cuttle Usage** (`?d=cuttle-usage`) charts totals from the same outcomes store (`src/api/dashboards/usage.py`): total cost, total tokens, turns, input and output tokens, and agent time.

- Range pills: 7d / 14d / 30d / 90d / 1y / all time. You can also pick custom start and end dates, which override the pill (`range=custom`).
- **Group by** model, harness, or none. Model grouping drops the reasoning effort and labels ambiguous names with their agent (`cursor · auto`). Groups beyond the top 9 by turns roll into **Other**.
- **Interval**: auto picks day for 45 days or fewer, week for 210 days or fewer, and month beyond that. Days are bucketed in the browser's time zone (`tz` = `getTimezoneOffset()`).
- **Chart**: stacked columns over time, or a breakdown of totals by group. Legend chips hide a group in every chart and in the table.
- Every attempt counts, including fallbacks and cancelled turns. Cost only includes harnesses that report it (not Cursor).

## Agent CLI

```bash
.venv/bin/python -m api.dashboards list
.venv/bin/python -m api.dashboards get model-benchmarks --source aggregate
.venv/bin/python -m api.dashboards get model-benchmarks --source aider --refresh
.venv/bin/python -m api.dashboards get model-benchmarks --refresh --no-discovery
.venv/bin/python -m api.dashboards get cuttle-performance --source pinned --days 0
.venv/bin/python -m api.dashboards get cuttle-usage --range 7d --group harness
.venv/bin/python -m api.dashboards get cuttle-usage --start 2026-09-01 --end 2026-09-15 --interval week
.venv/bin/python -m api.dashboards backfill-performance [--dry-run]
```

(Flags after the verb. `--json` is implied: stdout is JSON.)

## HTTP

| Method | Path | Notes |
|---|---|---|
| GET | `/api/dashboards` | Catalog |
| GET | `/api/dashboards/model-benchmarks` | `?source=deepswe\|swebench\|aider\|aggregate`; `?refresh=1` bypasses source caches |
| GET | `/api/dashboards/cuttle-performance` | Local outcomes. `?source=all\|pinned\|router`, `?days=7\|30\|90\|0`; `?label=1` / `?refresh=1` runs a Jev label pass |
| GET | `/api/dashboards/cuttle-usage` | `?range=7d\|14d\|30d\|90d\|1y\|all` or `?start=&end=` (YYYY-MM-DD); `?group=model\|harness\|none`, `?interval=auto\|day\|week\|month`, `?source=all\|pinned\|router`, `?tz=<minutes>` |
| POST | `/api/turn-feedback` | `{session_id, query_id, feedback: "good"\|"bad"\|null}` — thumbs on a reply; stored on the outcome row and message metadata |

Caches: `src/output/dashboards/` (gitignored with the rest of `src/output/`); each feed has a 6h cache.

## Sources (v1)

| Source | Role |
|---|---|
| [DeepSWE live JSON](https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json) | Score + cost + duration backbone |
| [SWE-bench official leaderboard JSON](https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json) | Verified resolved rate and reported cost |
| [Aider Polyglot leaderboard](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml) | pass@1, cost, and seconds per case |
| [BenchmarkList recent updates](https://benchmarklist.com/api/v1/operations/recent-benchmark-updates.json) | Discovery panel (no API key) |

Not integrated: OpenRouter benchmarks and Artificial Analysis (commercial).

## Adding a dashboard

1. Register a card in `src/api/dashboards/catalog.py`
2. Return a payload from `service.py` + a branch in `routes.py`
3. Handle `?d=<id>` in `src/web/js/dashboards_page.js`
