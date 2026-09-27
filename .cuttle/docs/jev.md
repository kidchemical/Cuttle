# Jev (TypeSafe System One)

Cheap typed judgments for Cuttle’s control plane — **not** a tentacle. You assemble
`state`, ask Choice / Score / Noul questions, and keep policy in Python.

Auth (first match wins):

1. `TYPESAFE_API_KEY` → `https://api.typesafe.ai`
2. `OPENROUTER_API_KEY` → `https://openrouter.ai/api` (same System One body; billed to OpenRouter)

Override host with `CUTTLE_JEV_BASE_URL`. Optional `settings.json → jev`
(`enabled`, `model`, `rank_context`, `label_turns`, `regress.interval_s`).

## Agent CLI

```bash
.venv/bin/python -m api.jev status
.venv/bin/python -m api.jev route --prompt "fix the scarecrow spawn"
.venv/bin/python -m api.jev rank --prompt "post this to discord" --project .
.venv/bin/python -m api.jev label --limit 40
.venv/bin/python -m api.jev regress --no-pytest
.venv/bin/python -m api.jev eval --state "still not fixed" --noul urgent:"Is this a complaint about a prior attempt?"
```

Flags after the verb. `--json` is implied: stdout is JSON.

## Where it is plugged in

| Surface | How |
|---|---|
| Agent router brain | `/router api model jev` (keeps the use-case table + demotions in code) |
| Frustration investigator | Default `agent_router.rage.investigator.agent` is `jev`; falls back to Cursor Auto if no key |
| Context Compiler | Optional ranked runbook/skill snippets when Jev is available |
| Pipeline skillsets | `dynamicStrategy: "jev"` (keyword fallback) |
| My Cuttle Performance | Cached miss labels (`python -m api.jev label` or dashboard **Label with Jev**) |
| Hourly regress | Flask daemon thread: scan recent query logs + router outcomes → maybe run a pytest subset → tray/toast if tests fail |

Disable the watcher with `CUTTLE_JEV_WATCH=0`. Disable all Jev with `CUTTLE_JEV_DISABLED=1`.

Thresholds live in `src/api/jev/thresholds.py` (the numbers humans should review).
