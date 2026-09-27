# Commands & actions (master guide)

Authoritative short guide for the `{project}/.cuttle/` pattern. Cuttle’s own
`.cuttle/` is the **reference layout**; other projects mirror it with their own
content (channels, scripts, runbooks).

## Layout

| Path | Role |
|---|---|
| `.cuttle/commands/*.md` | Slash palette (`/name` or `/cmd name`). On-demand expand — not dumped every turn. |
| `.cuttle/rules/*.md` | Always-on project guidelines compiled by the Context Compiler for every agent. |
| `.cuttle/actions/*.yaml` | Allowlisted recipes (`discord.post`, `shell`, …). Only these run on form/confirm click. |
| `.cuttle/docs/` | Human/agent runbooks (e.g. Discord emoji, deploy notes). Inventory only in context. |
| `.cuttle/scripts/` | Optional scripts referenced by `type: shell` actions. |
| `.cuttle/agents/<id>/` | Optional drop-in harness agents (manifest + adapter). |
| `.cuttle/memory/` | Reserved for future Brain memory retrieval. |

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

## Chat UI tags (side effects)

Emit from the agent reply — Cuttle rewrites to cards; clicks hit Flask with **no LLM**:

- `<cuttle_action_form>{JSON}</cuttle_action_form>` — choice / multi / fields; include `watch` for long OS jobs
- `<cuttle_confirm action="my.action" …>body</cuttle_confirm>` — one-shot Confirm/Cancel

**Full form contract** (Q&A vs side effects vs watch/progress, Flask restart card):
[action-forms.md](action-forms.md). Agents should open that before emitting a form.

Also: Cursor skill `cuttle-project-commands`.
Live example project: Escape Purgatory (`.cuttle/commands/discord-update.md`,
`.cuttle/actions/discord-post.yaml`, `.cuttle/docs/discord.md`).
