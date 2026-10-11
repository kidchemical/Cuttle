# Commands & actions (master guide)

Authoritative short guide for the `{project}/.cuttle/` pattern. Cuttle’s own
`.cuttle/` is the **reference layout** (project config; shared global config lives
in `.cuttle_global/`); other projects mirror the project shape with their own
content (channels, scripts, runbooks).

## Layout

| Path | Role |
|---|---|
| `{project}/.cuttle/commands/*.md` | Slash palette (`/name` or `/cmd name`). On-demand expand — not dumped every turn. |
| `{project}/.cuttle/rules/*.md` | Always-on project guidelines compiled by the Context Compiler for every agent. |
| `{project}/.cuttle/actions/*.yaml` | Allowlisted recipes (`discord.post`, `shell`, …). Only these run on form/confirm click. |
| `{project}/.cuttle/docs/` | Human/agent runbooks (e.g. Discord emoji, deploy notes). Inventory only in context. |
| `{project}/.cuttle/scripts/` | Optional scripts referenced by `type: shell` actions. |
| `{project}/.cuttle/agents/<id>/` | Optional drop-in harness agents (manifest + adapter). |
| `{project}/.cuttle/memory/` | Reserved for future Brain memory retrieval. |

Global-owned equivalents (every project) live under `.cuttle_global/` in the Cuttle checkout.

## Command frontmatter (minimum)

```markdown
---
name: my-command
title: My command
description: Shown in the / palette
---

# Instructions the agent should follow
```

Optional: `execute: shell` + `run:` + `watch:` for deterministic jobs with a progress card
(Flask attaches the card — no LLM). Use `execute: prompt` only when the agent must draft
or judge (e.g. Discord copy).

## Action YAML (minimum)

```yaml
name: my.action          # id used in forms / confirms
title: Human label
description: Short hint
type: discord.post       # or shell
# channels: { alias: "discord-channel-id" }
# run: python .cuttle/scripts/do_thing.py
```

## Native `/branch` (project git branch)

A global native control command (registered in
`src/web/js/chat/chat_slash.js`, intercepted in `api/web_chat_api.py` and
handled by `api/branch_slash.py`; git behavior stays in `api/git_service.py`).
It never becomes a model turn.

- `/branch` — show the current branch of the chat's project.
- `/branch list` — list branches with the current one marked.
- `/branch <name>` — switch the working branch (owner-only; refuses a dirty
  worktree, tracks a remote branch when needed).

The composer project chip is segmented: star status | project name | branch
(branch read from `GET /api/git/repos?path=…`).

## Chip config modal

Clicking a composer chip opens a centered config modal (reuses the shared
`.auth-modal` account-modal skin) with one dropdown per chip part — no model
turn:

- **Project chip** → **Project** + **Branch** dropdowns. Changing the project
  updates the branch list; changing the branch calls `POST /api/git/branch`
  (`action: switch`).
- **Agent chip** → **Agent** + **Model** + **Effort** dropdowns. Changing the
  agent swaps the sticky chip and reloads model/effort; model/effort picks use
  the same setters as the `/` palette.

`GET /api/git/branches` and `POST /api/git/branch` accept optional
`path`/`project_id` so the branch list/switch targets the chat's project, not
the global active project. Close with Done, Escape, or a backdrop click.
Wiring lives in `src/web/js/chat/chat_page.js` (`bindChipClickMenus`,
`CHIP_AGENT_MENU_SPECS`); skin in `.chip-config-modal` (`chat_page.css`).

## Live usage reports

With a Cursor, Codex, Muse, Hermes, or OpenCode agent badge selected, `/usage`
shows a fixed snapshot and `/usage-live` shows a report that refreshes every
minute while visible. Explicit forms such as `/codex /usage-live` also work.
Hermes, Muse, and OpenCode retain their optional day range (`/usage-live 7d`).

Chat panes share one shell polling scheduler and one request per agent/day range.
`GET /api/usage-live?agent=codex&days=30` requires owner authentication and uses
a 60-second provider cache with per-key request coalescing across windows and
devices. Hidden/offscreen reports stop polling; scrolling back or restoring the
page resumes updates. Refreshes change the displayed report without adding chat
messages or starting agent turns. No background polling runs without a viewer.

## Chat UI tags (side effects)

Emit from the agent reply — Cuttle rewrites to cards; clicks hit Flask with **no LLM**:

- `<cuttle_action_form>{JSON}</cuttle_action_form>` — choice / multi / fields; include `watch` for long OS jobs
- `<cuttle_confirm action="my.action" …>body</cuttle_confirm>` — one-shot Confirm/Cancel

**Full form contract** (Q&A vs side effects vs watch/progress, Flask restart card):
[action-forms.md](action-forms.md). Agents should open that before emitting a form.

Also: Cursor skill `cuttle-project-commands`.
Project commands, actions and runbooks belong to the registered project's own
`.cuttle/` tree; install-local workflows belong in its personal overlay.

### Codex saved usage resets

With a Codex badge, `/usage` and `/usage-live` list saved usage-limit resets,
with expiration dates in the viewer's local timezone and a **Use reset** button
for each eligible credit. The button asks for confirmation before spending the
selected reset. The owner-authenticated endpoint refreshes the usage report
after redemption; retries reuse the same idempotency key.

Cuttle uses the installed Codex CLI's supported app-server methods
`account/rateLimits/read` and `account/rateLimitResetCredit/consume` with the
existing Codex login. No model turn is started. Older CLIs fall back to the
existing account usage report; if only a reset count is available, expiration
and redemption controls remain unavailable. Codex may return fewer detail rows
than the total count; the report explains when that happens.
