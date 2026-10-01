# Extension boundaries

Cuttle is a GUI-first, extensible **agent control plane** — a harness of harnesses. It provides a unified environment for interacting with, orchestrating, and managing independently developed agent CLIs.

Repository location is not the architectural boundary. **Dependencies and execution ownership** are. A future surface adapter may live in this repo, a separate package, or an independently deployed service.

Three categories must stay distinct.

## A. Agent harness adapters

Wrap vendor agent CLIs (Cursor, Codex, Claude, Muse, OpenCode, Hermes, …).

**Own:** CLI invocation, capability discovery, model/effort selection, native session resume, normalized results and usage, vendor-specific behavior.

**Do not own:** project context, session lifecycle, cancellation, routing, or execution coordination. Those belong to the shared Cuttle execution kernel (`api.agent_harness` + chat delivery + router).

Adding a vendor is filling a catalog/adapter folder, not a new HTTP trigger per chat platform.

All chat execution funnels through the shared application entry
(`api.chat_coordinator.submit_agent_turn`): route lanes pass their harness
run as `run_harness`, and non-lane ingress (`/api/sessions/send`) goes
through the same entry unclaimed. No surface implements an independent
execution path. Cuttle never installs vendor CLIs (BYO-CLI guidance only,
Phase 6 P6-A). Opted-in project drop-ins (`{project}/.cuttle/agents/`) are
trusted unsandboxed code: manifest identity is validated before import and
siblings load on demand through relative-only namespaced packages — full
contract in `src/api/agent_harness/ADDING_AN_AGENT.md` ("Project drop-in
trust model").

## B. Surface adapters (optional)

Interfaces through which **users** talk to Cuttle: Web, Electron, Android today; Discord, Slack, Telegram, or others later.

**Own:** platform connectivity, platform identity mapping, incoming message normalization, presentation/formatting, progress/event delivery, rendering approvals.

**Must delegate to Cuttle Core:** authentication, authorization, session management, and agent execution.

**Must not:** implement independent Cursor/Claude/etc. execution paths, or platform-specific agent routes such as `pipeline-trigger-discord` (retired) or Telegram/Slack equivalents.

Long-lived gateway processes are allowed when **explicitly enabled and independently managed**. No optional surface may be a mandatory dependency of Cuttle Core. `DISCORD_TOKEN` must not start an inbound Discord gateway.

### Shared ingress (deferred)

A future chat surface should consume one authenticated Cuttle ingress contract that normalizes:

- Platform / user identity
- Cuttle session identity
- Project / workspace context
- Message content and attachments
- Agent / model selection
- Execution request
- Progress and result delivery
- Interactive approvals

Cuttle Core stays **transport-agnostic**. Implementation of that framework is **not** in this cleanup; see deferred work in [`../reviews/discord-cleanup-2026-09.md`](../reviews/discord-cleanup-2026-09.md).

## C. Agent operations / service integrations

Capabilities **agents** invoke through existing CLI and action mechanisms: Discord read/post, Git/Gitea, workers, Flask restart, other APIs.

These are **not** user-interface surfaces. An agent reading a Discord channel is different from a user messaging Cuttle through Discord.

Retained Discord agent-ops live under `src/api/discord_ops/` (token + confirmed REST post) and `src/api/discord_cli/` (read-only). Public contracts: `python -m api.discord_cli`, action id `discord.post`. `project_actions.py` remains a thin dispatcher so action identifiers stay stable.

## What Cuttle is not

Cuttle is not fundamentally a Discord bot. Discord read/post are optional agent operations. External chat platforms are future optional surface adapters, not core execution.
