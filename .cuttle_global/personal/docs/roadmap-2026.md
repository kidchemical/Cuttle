# Cuttle — 2026-08 architecture dump (install-local)

**Not public.** Snapshot from 2026-08-17 (OpenClaw comparison, pipeline-era phases). Public truth: `docs/ROADMAP.md`.

This file is a **historical briefing** so local agents can see why some code still exists (executor, node editor). Do not treat the “Current State” table or “Critical Security” list as HEAD.

## What aged out

| Then (2026-08) | Now |
|---|---|
| Windows-only daemon | Windows **and** Linux |
| Auth = unsalted SHA-256 | **bcrypt** |
| CORS `*` + credentials | Origin allowlist (`api.lan_access`) |
| No login rate limits | Flask-Limiter on auth + pairing + OAuth |
| `secure=False` cookies always | Secure on HTTPS; HTTP LAN keeps Secure=False |
| Web sessions all `is_owner: True` | `/api/chat` uses `OWNER_USER_EMAIL` |
| 16 shipped pipeline graphs | **Deprecated** — empty `src/pipelines/`, chat is slash agents + router |
| Node Editor as differentiator | Parked for a future rebuild |
| “Memory poisoning via Discord → MEMORY.md” | **Removed.** Pipeline-era `workspace_memory.py` / per-identity `MEMORY.md` is gone. CLI harnesses use Cuttle Brain. |

## Prompt injection (item 6) — what it actually was

Not Cuttle Brain. It was an old OpenClaw-style per-identity `MEMORY.md` injected into **pipeline LLM node** system prompts. That module and its Flask routes were deleted. CLI turns use Context Compiler (`.cuttle/`), not that file.

If you revive pipeline LLM nodes for Discord, do not reintroduce unsanitized DM → memory writes.

## Still useful (guardrails)

- No Docker-as-the-product sandbox; no public ClawHub-style marketplace; no 20-platform parity
- Do not port DeepSeek Cordis / rewrite Cuttle in Node
- Do not become an in-process coding agent — host vendor CLIs
- No product `cuttle` human CLI; grow `python -m api.*`
- Workers: jobs not live GPU session migration; don’t rebuild Flamenco

## Phases (archive labels)

Phase 2–4 rows (Telegram/Slack bots, skills marketplace, visual debugger, etc.) were written when pipelines were the product. Re-litigate against `docs/ROADMAP.md` + `.cuttle/learnings/FEATURE_REQUESTS.md` before building them.

Workers W0–W2c are largely **code done**; W3 intent scheduling is partial.

## Verification leftovers

CORS: foreign-origin `fetch` to `/api/chat` with credentials should fail. Login: HTTP 429 after five attempts from one IP.
