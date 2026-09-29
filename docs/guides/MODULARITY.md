# Cuttle modularity — the harness of harnesses

**Status:** Product + architecture thesis (locked 2026-08-17, CH-000162)  
**Canonical strategy:** [`docs/ROADMAP.md`](../ROADMAP.md)  
**Router backlog:** [`.cuttle/docs/agent-router-todo.md`](../../.cuttle/docs/agent-router-todo.md)  
**Router research / economics:** [`docs/ROUTER_RESEARCH_NOTES.md`](../ROUTER_RESEARCH_NOTES.md)

This file is the home for *what Cuttle is*, *how plugins/sockets should feel*, and *what to steal from DeepSeek Harness without becoming it*. Day-to-day how-to for adding a CLI still lives in [`src/api/agent_harness/ADDING_AN_AGENT.md`](../../src/api/agent_harness/ADDING_AN_AGENT.md).

---

## Product goals

1. **Harness of harnesses for a home station / lab.** Cuttle sits above Cursor, Codex, Muse, Claude, Gemini, OpenCode, Hermes, DeepSeek, and whatever comes next. It should also be usable at a small-business / team level when someone wants that, without designing the default experience as a SaaS control plane.
2. **DeepSeek is a tentacle, not a replacement.** Host DeepSeek Harness (`dsh`) as a normal Cuttle agent (`/deepseek`). Steal its *kernel ideas* (seams, event log, compose-via-config). Do not port Cordis or rewrite Cuttle in Node.
3. **Cozy, customizable, cost-honest.** Users should feel at home and rearrange the station as they please. Default policy: deliver **cost efficiency while staying effective**. Self-calibrate from this machine’s outcomes. Notice when a cloud provider (or a harness in front of one) quietly serves worse capacity — “dog food” — and route around it. Surface **signals**, do not claim courtroom proof.
4. **Modular enough that contributing feels obvious.** Adding a tentacle, an LLM, a brain layer, or a router brain should mean filling a folder (or a config row), not forking Flask. If a newcomer cannot see where their plugin goes, the architecture failed.
5. **The router is a socket.** The plug can be a cheap cloud LLM, a local LLM, a programmatic table, or **another agent harness** used as the routing brain. CuttleRouter is the default plug, not the only one.
6. **Same socket idea everywhere it matters.** Knowledge, Brain (context / memory / retrieval), and reasoning / coordination components should be swappable the same way — definition + provider + consumer — so the host does not care which implementation is mounted.
7. **Agent ops CLIs (`python -m api…`) are encouraged; a product `cuttle` shell is not.**
   The home experience remains daemon + web/Electron chat. Discord read/post are
   optional agent-ops, not a default gateway. Do not spend design
   budget on a user-facing `cuttle.exe`. Do ship thin **agent toolkit** modules
   (`python -m api.chat_cli`, `api.device_workers.cli`, `api.cuttle_brain`, …) so agents
   stop inventing SQL. See `.cuttle_global/docs/agent-ops-cli.md`.

---

## What Cuttle is (and is not)

Cuttle is a **Windows-native, cozy control plane** for a personal AI station: pick which harness runs the turn, brief it with Cuttle-owned context, remember what happened, and get cheaper over time without getting dumber.

| Cuttle is | Cuttle is not |
|---|---|
| A harness **of** harnesses (tentacles) | One in-process coding agent competing with Cursor |
| Home / lab first; business-ready if you turn that on | A hosted multi-tenant SaaS by default |
| Cost-efficient + self-calibrating | “Always call the frontier model” |
| Plugin sockets for agents, router brains, LLMs, knowledge, reasoning | A privileged `web_chat_api.py` core you patch for every vendor |
| Cozy and rearrangable | A Creator-IDE for composing TypeScript plugins |

The mascot already says this: each vendor harness is a tentacle; Cuttle is the cuttlefish. [`docs/ROUTER_RESEARCH_NOTES.md`](../ROUTER_RESEARCH_NOTES.md) has the economic version of the same thesis. Flask composition root (routes, reverse imports, extraction seams — **no refactor mandate**): [`WEB_CHAT_API.md`](WEB_CHAT_API.md).

**DeepSeek Harness (dsh)** is a *different* product: an MIT-licensed in-process agent runtime where models, tools, sessions, sandboxes, the loop, and the UI are Cordis plugins. Cuttle **hosts** `dsh` (bundled connector: `src/api/agent_harness/agents/deepseek/`). Cuttle does **not** become dsh.

---

## Sockets (the pattern to copy)

Every swappable capability should have three roles, stolen from dsh’s “capability seams” without stealing Cordis:

| Role | Job |
|---|---|
| **Definition** | A small Python protocol / dataclass. Stable. |
| **Provider** | One folder (or config row) that implements it. |
| **Consumer** | Chat UI / future surface adapters, router — talks only to the definition. |

A **socket** is that definition plus discovery (“what is plugged in?”) plus a default plug so a fresh home station works.

### Sockets we already have, or mean to have

| Socket | Today | Target |
|---|---|---|
| **Agent / tentacle** | Folder-per-CLI: `manifest.yaml` + `adapter.py`; catalog + shared kernel | Done for chat slash. Keep inference-mode lists on the catalog so they cannot bypass it. |
| **Router** | `api` / `local` / `agent` modes; runners still late-bind Flask | A socket: task + available tentacles in; chosen tentacle + reason out. Plug = cheap LLM, local LLM, preference table, or another harness as brain. See [`.cuttle/docs/agent-router-todo.md`](../../.cuttle/docs/agent-router-todo.md). |
| **LLM (API calling)** | `api.llm_complete.complete` for titles/commits/enhance/router brain; Jev separate; TTS/vision own clients | Same shape as agents: `llm_providers/<id>/`. Router brain and cheap jobs consume the helper. |
| **Brain / context** | Context Compiler layers are a function with a fixed order | Layers become registered plugs (contract, rules, profile, inventory, handoff, later retrieval). Kernel stays the consumer. |
| **Knowledge / memory** | Chat transcript (SQLite) + Context Compiler; guest CLIs own native resume | Future Brain retrieval plug (`.cuttle/memory/`). Not a separate OpenClaw memory store. |
| **Reasoning / coordination** | Supervised coordinator as a routing *strategy*, not a fake agent | Keep it a strategy plug on the router socket. Do not invent a second router. |

**Rule:** if adding a vendor still requires an `elif` in `web_chat_api.py` or `_execute_remote_agent_tool`, that socket is not real yet.

---

## What DeepSeek Harness is doing right (steal this)

Released 13 Aug 2026, MIT, developer preview. Docs: [architecture](https://raw.githubusercontent.com/deepseek-ai/DeepSeek-Harness/master/docs/architecture.md), [site](https://deepseek.com/harness/en/).

1. **No privileged core.** Models, tools, skills, sessions, sandboxes, storage, the **loop**, scheduling, and UI are plugins. You mount something beside the others; you do not patch a god file.
2. **Capability seams.** One provider swap (filesystem / subprocess / sandbox) moves every consumer. Three roles: definition, provider, consumer.
3. **Compose with configuration.** Bundles stacked into a profile, then user patches. Standard / Code / Minimal / Creator are the same plugins recomposed. Minimal mode is how they benchmark models — scaffolding is explicit.
4. **“Model-visible means logged.”** Append-only session event log is the source of truth. LLM history is *derived*. Resume, fork, search, replay, and Trajectory share one stream. A runtime invariant: anything that reached a model can be reconstructed.
5. **Events as extension points.** Durable session events vs live waterfalls (`agent/pre-step`, `tools/pre-execute`). Intercept without forking the loop.
6. **Code mode.** Tools as an SDK so the model writes one program instead of dozens of round-trips. Interesting later for a Cuttle-native agent; not a near-term host feature (Cursor/Codex already own their loops).

Do **not** steal: Cordis, a TypeScript rewrite, UI-as-plugin of the agent loop, Creator mode as a Cuttle product surface, or pinning production to dsh while it is preview (breaking changes are promised).

---

## What Cuttle already got right

The agent harness rewrite is the correct **hub** slice:

- Folder + manifest + thin CLI adapter; kernel owns status, resume, Context Compiler, hot-swap handoff, error shaping.
- Neutral SQLite chat transcript + **per-tentacle** native resume. Same-agent resume sends only the user prompt; a sticky switch injects a short handoff delta.
- Context Compiler is Cuttle-owned briefing — not a copy of each vendor’s skill registry.
- Drop-in roots (`src/data/harness_agents/`, `{project}/.cuttle/agents/`, `CUTTLE_AGENTS_DIR`). Bundled ids cannot be shadowed.
- Windows `.cmd` traps, lossless prompt transport, auth sync without the LLM seeing keys, contract tests + gated live smoke.

`/deepseek` is already a bundled tentacle (`dsh --profile headless`, Flash by default, `DEEPSEEK_API_KEY`). Headless preview has **no native per-chat resume**. Treat that as a connector limitation, not a reason to wait on the socket work.

---

## What is still a privileged core

Agents are plugins. Almost nothing else is.

| Gap | Why it hurts the goals |
|---|---|
| `inference_mode.py` hardcodes cloud slashes (partially catalog-aware) | Local-mode blocking drifts from the catalog. |
| Router `_default_runners` imports `web_chat_api` | The router socket is not extractable. Goal 5 stalls. |
| No Cuttle-owned event log | Query JSON sidecars, `chat_messages`, and compiler envelopes are three stories. You cannot replay what the model saw. Self-calibration and anti-dogfood (goal 3) stay blind. |
| LLM providers are not a catalog | Gemini-as-API vs Gemini-as-CLI stays a Flask special case. |
| Brain layers are hardcoded | Knowledge/retrieval cannot plug in without editing the compiler (goal 6). |

---

## Contribution bar (goal 4)

A newcomer should be able to:

1. Copy `agents/<id>/` (or the future `llm_providers/<id>/`, `brain/layers/<id>/`).
2. Fill a manifest + a small adapter.
3. Pass the contract test **and** a gated live smoke on the cheap model they chose.
4. Never edit `web_chat_api.py`.

If step 4 is required, log it in `.cuttle/learnings/ERRORS.md` — that is a process failure, same as shipping an agent without a test.

Drop-ins under `{project}/.cuttle/agents/` and `src/data/harness_agents/` are the on-ramp for experiments. Bundled connectors are for tentacles we are willing to smoke and support.

---

## First steps (do these, in order)

Security in [`docs/ROADMAP.md`](../ROADMAP.md) still comes first when it conflicts. This list is the **modularity spine** on the current branch.

**Close the tentacle socket**

1. Drive Local-mode cloud blocking from `manifest.requires_cloud`, not a hardcoded slash list.
2. Point router runners at `kernel.run_agent_web_command` directly — no Flask import.

**Then copy the pattern**

3. Keep this file as the seam map. Add sockets only when a second provider is real (do not invent empty registries).
4. Cuttle-owned session events next to chat messages: `user/message`, `context/inject`, `handoff`, `assistant/message`, `tool/call`, `tool/result`, `router/decision`. Compiler layers become events, not a blob that vanishes into the CLI. Query reports become a projection. **Model-visible means logged.**
5. `src/api/llm_providers/<id>/` for API calling. `/api/llm-request` and the router brain consume it.
6. Register Context Compiler layers the same way. Knowledge retrieval is a new layer provider.

**Router after the catalog is honest**

7. Phase 0 preference table + CuttleRouter as a pin-able harness agent ([`.cuttle/docs/agent-router-todo.md`](../../.cuttle/docs/agent-router-todo.md)). Include DeepSeek Flash as a cheap tentacle in OOB discovery once the CLI is installed and keyed.
8. Do not extract a standalone router package until runners do not touch Flask.
9. Prefer growing **agent ops** `python -m api.<module>` verbs (chat, workers, brain) over a product `cuttle` shell. See `.cuttle_global/docs/agent-ops-cli.md`.

---

## DeepSeek as a tentacle (goal 2)

| Item | State |
|---|---|
| Bundled adapter | `src/api/agent_harness/agents/deepseek/` — `/deepseek`, auto-install `@deepseek-ai/dsh` |
| Default / smoke model | `deepseek-v4-flash` (do not smoke Pro by accident) |
| Auth | `DEEPSEEK_API_KEY` in `src/.env`; optional `DEEPSEEK_BASE_URL` |
| Resume | **No** — headless one-shot; Cuttle handoff still applies on sticky switch |
| Router | Not yet a first-class OOB / preference-table target — that is remaining work |
| Kernel ideas | This file — independent of the adapter |

When dsh grows native resume or a stable non-preview CLI, upgrade the adapter in place. Do not block the socket work on preview churn.

---

## Related docs

| Need | Where |
|---|---|
| Phases, security, OpenClaw, what not to build | `docs/ROADMAP.md` (public); `.cuttle_global/personal/docs/roadmap-2026.md` (this install) |
| Router work items, CuttleRouter-as-agent, authority order | `.cuttle/docs/agent-router-todo.md` |
| Router economics, backends-as-black-boxes, eval notes | `docs/ROUTER_RESEARCH_NOTES.md` |
| How routing works in chat | `docs/guides/AGENT_ROUTER.md` |
| How to add a tentacle | `src/api/agent_harness/ADDING_AN_AGENT.md` |
| Context Compiler / Brain | `src/api/cuttle_brain/CONTEXT_COMPILER.md` |
| Discrete user feature ideas | `.cuttle/learnings/FEATURE_REQUESTS.md` |
