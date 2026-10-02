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
Live example project: Escape Purgatory (`.cuttle/commands/discord-update.md`,
`.cuttle/actions/discord-post.yaml`, `.cuttle/docs/discord.md`).

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
