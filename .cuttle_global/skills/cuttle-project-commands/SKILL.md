---
name: cuttle-project-commands
description: >-
  Create and maintain per-project Cuttle slash commands as markdown files under
  .cuttle/commands/ (YAML frontmatter + body), plus confirm-gated / multi-choice
  project actions under .cuttle/actions/ via cuttle_action_form and cuttle_confirm.
  Use when adding /build-style commands, Discord confirm posts, action-form
  pickers, palette entries, teaching agents how to author project commands,
  OR when the user asks to create / register / set up a new Cuttle project
  (scaffold .cuttle/ even if they never type a slash command).
---

# Cuttle project commands

## New project (chat ask — no slash required)

When the user asks you to **create, register, or set up a Cuttle project**
(prose like “make a new project for …”, not only `/something`):

1. Create or locate the project root on disk.
2. Register it with Cuttle when appropriate (`POST /api/projects` or
   `ProjectManager.add_local_project`) — registration **auto-scaffolds**
   `.cuttle/` via `managers.cuttle_scaffold.ensure_cuttle_scaffold`.
3. If you only create folders and skip the API, still run the scaffold yourself:
   ```text
   ..\.venv\Scripts\python.exe -m managers.cuttle_scaffold "E:\path\to\project" --name "Display Name"
   ```
   (cwd = `/path/to/Cuttle/src`), or call `ensure_cuttle_scaffold(path, project_name=…)` in Python.
4. Fill `rules/00-core.md` with real layout notes for that project; leave stubs only as a starting point.
5. Do **not** treat “project created” as done until `{root}/.cuttle/rules/00-core.md` exists.

## Where they live

**Convention:** every Cuttle-registered project owns its own `.cuttle/` tree.
Cuttle’s repo (`/path/to/Cuttle/.cuttle/`, shared config in `/path/to/Cuttle/.cuttle_global/`) is the **master layout + docs** —
other projects (Escape Purgatory, etc.) mirror that shape with their own commands,
actions, and runbooks. They do **not** inherit Cuttle’s actions at runtime.

For any registered project root:

```text
{project_root}/.cuttle/commands/*.md
{project_root}/.cuttle/rules/*.md     # always-on (Context Compiler)
{project_root}/.cuttle/actions/*.yaml
{project_root}/.cuttle/docs/          # optional runbooks
{project_root}/.cuttle/scripts/       # optional shell helpers
{project_root}/.cuttle/GLOBAL.ini     # which global layers apply (rules/docs/actions)
```

Global reference: `/path/to/Cuttle/.cuttle_global/README.md` and
`/path/to/Cuttle/.cuttle_global/docs/commands-and-actions.md`.

Unity-style layouts (git under `source/`) may also use:

```text
{project_root}/source/.cuttle/commands/*.md
{project_root}/source/.cuttle/actions/*.yaml
```

Cuttle prefers the registered root’s `.cuttle/…`, then the nested `source/` copy.

## Command file format

Same idea as Cursor skills — YAML frontmatter + markdown body:

```markdown
---
name: build
title: build
description: Build the Windows player
aliases: []
execute: shell
run: pwsh -File .cuttle/scripts/build.ps1 -Kick
watch:
  id: my-job
  title: Windows build
timeout: 90
---
```

Long OS jobs: **`execute: shell` + `run:` + `watch:`**. Flask runs the recipe (no LLM) and attaches the progress card. Do not make `/build` an agent prompt.

Judgment-only commands (Discord drafts) stay `execute: prompt`.

| Field | Purpose |
|---|---|
| `name` | Slash id (`/build`). Defaults to filename stem. |
| `title` | Palette label (defaults to name). |
| `description` | Palette hint under the label. |
| `aliases` | Extra names that resolve to this command. |
| `execute` | `shell` runs `run:` in Flask; `prompt` expands into an agent. Prefer shell for automation. |
| `run` | Shell recipe (project cwd / `workdir`). |
| `watch` | `{id, title?, url?, resume_message?}`. Flask posts the native progress card after a successful kick. |
| `timeout` | Seconds for `run` (default 120 if `watch` is set, else 3600). |
| `workdir` | Optional relative/absolute cwd for `run`. |

Reserved names (`cursor`, `help`, `pipeline`, …) are exposed only as `/cmd <name>`.

## Confirm-gated actions (no LLM on click)

For side effects that should ask the human first (Discord posts, deploys, etc.):

1. Define an allowlisted action in `.cuttle/actions/my-action.yaml`.
2. Prefer a **`<cuttle_action_form>`** when the user should pick among options / multi-select / fill fields. Use **`<cuttle_confirm>`** for a single Confirm/Cancel.
3. Cuttle renders an inline card; clicks run via Flask — **no second agent turn**, and action forms default to **no chat reply** (toast + lock).

### Action YAML

```yaml
name: discord.post
title: Post to Discord
description: Post to an allowlisted channel
type: discord.post          # builtin; or "shell" with run:
guild_id: "…"               # optional (message URLs)
channels:                   # alias → id (for discord.post)
  feature-updates: "…"
# type: shell
# run: python .cuttle/scripts/do_thing.py
# workdir: .
```

Shell actions receive:

- stdin = confirm body (`content`)
- env `CUTTLE_ACTION`, `CUTTLE_ACTION_CONTENT`, `CUTTLE_ACTION_PARAMS_JSON`, `CUTTLE_PROJECT_PATH`
- env `CUTTLE_SESSION_ID` (the chat the **card** was rendered in, not whatever chat
  the client had open) and one `CUTTLE_PARAM_<KEY>` per scalar
  param — `{"params":{"mode":"when-idle"}}` becomes `CUTTLE_PARAM_MODE=when-idle`
  (key uppercased, non-alphanumerics → `_`, booleans → `true`/`false`; `content`
  is skipped since it already arrives on stdin)

### Action form (preferred for A/B/C / multi / fields)

```xml
<cuttle_action_form>
{
  "title": "Post Discord update",
  "mode": "choice",
  "lock": "form",
  "silent": true,
  "content": "**🐛 Title**\n\n• Bullet",
  "options": [
    { "id": "a", "label": "Feature Updates", "action": "discord.post", "params": { "channel": "feature-updates" } },
    { "id": "b", "label": "Prompt Lab", "action": "discord.post", "params": { "channel": "prompt-lab" } },
    { "id": "cancel", "label": "Cancel", "action": null }
  ]
}
</cuttle_action_form>
```

| Field | Purpose |
|---|---|
| `mode` | `choice` (one click), `multi` (checkboxes + Run), `form` (fields + submit) |
| `lock` | `form` (lock whole card), `field` (lock used control), `none` |
| `silent` | `true` (default): toast + lock, no assistant bubble |
| `options[].action` | Allowlisted `.cuttle/actions` name. Omit for Q&A picks (`Selected: …`). Use `id: "cancel"` (action null/omitted) for Cancel. |
| `resume` | `true` = the answer starts the agent's next turn. **One resume card per reply** — several questions go in one `form` (`radio` / `checkboxes` fields); separate resume cards orphan each other (server merges them as a fallback). |
| `content` | Shared body merged into option `params.content` when omitted |
| `submit` / `paramMap` | For `mode: form` — map field ids → action params. Checkbox fields with `line` are joined into `content` (checked only). |

**`line` is required on every checkbox/toggle field.** `label` is the card's UI
summary and is **never** posted; a checked field with no `line` blocks the whole
submission ("No post text for: …") instead of posting the label. Write `line` as
the finished Discord text, not a description of it:

```json
{ "id": "cause", "type": "checkbox", "value": true,
  "label": "Cause",
  "line": "• The 2024 halo never wrote to Steam stats, so it vanished on relaunch." }
```

Also: `POST /api/action-form/run` executes submissions without going through the chat LLM path.

### Background job + progress (native — any project)

Do **not** copy this into every project command. The Context Compiler UI contract
already tells every agent to emit a watch card when an OS job will run a long time.
Project commands only need a **kick** recipe and a **job id** if they write a
non-default status file.

Status JSON lives at Cuttle `src/output/<id>-status.json` (`GET /output/<id>-status.json`):

```text
python -m api.job_watch write --id my-job --state running --percent 40 --label "Compiling"
```

Poll URL must be same-origin `/output/…` or `/api/…` (arbitrary URLs are ignored).

```xml
<cuttle_action_form>
{
  "title": "Job",
  "mode": "choice",
  "lock": "form",
  "silent": true,
  "watch": {
    "id": "my-job",
    "url": "/output/my-job-status.json",
    "interval_ms": 4000,
    "done_states": ["done"],
    "fail_states": ["failed"],
    "resume_message": "Job my-job finished. Read the status JSON and summarize. Do not restart it."
  },
  "options": [
    { "id": "continue", "label": "Continue once it's finished", "action": "__watch_resume__" },
    { "id": "later", "label": "I'll reply once it's done", "action": "__watch_park__" },
    { "id": "stop", "label": "Stop job", "action": "__watch_cancel__" }
  ]
}
</cuttle_action_form>
```

- Progress bar updates as soon as the card renders.
- **Continue** locks the buttons and, when `state` is in `done_states`, sends `resume_message` as a new user turn in that same chat.
- **I'll reply** locks the buttons and does not send a turn.
- **Stop job** (and the composer Stop button while the job is running) kills the process tree recorded in the status JSON (`pid`, `unity_pid`).

### Confirm tag (single action)

```xml
<cuttle_confirm
  action="discord.post"
  channel="feature-updates"
  confirm_label="Post to Feature Updates"
  cancel_label="Cancel">
**🐛 Title**

• Bullet
</cuttle_confirm>
```

Any attribute other than `action` / labels is stored as a param (`channel`, custom fields, …). The confirm body becomes `content`.

## How Cuttle uses commands

1. Chat project chip / `/project` sets cwd.
2. `/` palette loads `GET /api/project-commands?path=…` and shows **Project cmd** entries.
3. Sending `/{name}` or `/cmd {name}` (optionally after `/cursor ` / `/claude ` / …) expands the markdown into the agent prompt, or runs `run` when `execute: shell`.

## How to add commands (any project)

1. Create `{project}/.cuttle/commands/` if missing.
2. Add `my-command.md` with `name`, `description`, and a clear body.
3. Hard-refresh Cuttle chat; set the chat project; type `/` — the command should appear.
4. Prefer **agent instructions** in the body when the procedure needs judgment. Use `run:` for deterministic scripts.
5. For human-gated side effects, add `.cuttle/actions/*.yaml` and teach the command body to emit `<cuttle_action_form>` or `<cuttle_confirm>`.
