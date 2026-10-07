# Cuttle global — always-on rules (every project)

You are running inside Cuttle, a local agent control-plane harness. These rules apply
no matter which model or CLI you are, and no matter which registered project the chat targets.

These files under `.cuttle_global/rules/` are compiled into the fresh/full context by the
**Context Compiler** (Cuttle Brain) and ride change-deltas on resume. They hold hard invariants and intent→doc pointers only —
how-to lives in `.cuttle_global/docs/`. Rules for the Cuttle repo itself (not guests) live in
`.cuttle/rules/` at the Cuttle project root and load only for Cuttle chats.

## Hard rules (match intent → doc)

Intent → runbook pointers for chat forms, charts, chat history, media, Discord,
headless turns, plans and widgets live in the "Global runbooks" directory at the
top of this context; `python -m api.*` verbs are owned by `02-agent-ops-cli.md`, sub-agents by
`03-subagents.md`. The rules below are the invariants those pointers do not carry.

1. Prefer project `.cuttle/commands`, `.cuttle/actions`, `.cuttle/docs` over inventing parallel paths.
   Install-local overrides live in `personal/` overlays (`<home>/personal/` in the Cuttle home,
   `{project}/.cuttle/personal/`; gitignored). Personal *markdown* appends after the
   tracked file as a delta; other personal files replace by basename.
2. User-visible choices, confirmations, watches, and progress → `cuttle_action_form`
   (`action-forms.md`). Side effects need real allowlisted ids (`discord.post`,
   `flask.restart`, `git.push`, …). **Asking the user a question:** emit one Q&A
   `<cuttle_action_form>` with `"resume": true`, then end the turn. Do not use native
   harness question or input tools. Never treat unavailable input as a user answer
   or cancellation. On preference cards omit `action` entirely; never invent
   `none` / `noop` / `__agent_reply__`. Mesh watch cards need overall + per-worker `bars`.
3. Forge / issue tracker → the enabled integration runbook for the active project.
   Optional integration guidance requires explicit project configuration (`GLOBAL.ini`).
4. Multi-device / LAN workers / render farm / mesh compute → `cuttle-workers.md`.
   **Mesh self-serve:** do device-capable work on the mesh yourself (`file_copy`, shell
   recipes, `workers.self-update`, …) — ask the user only when no capable worker is online,
   HITL/approval needs their click, or the step truly needs interactive UI only they see.
   If Jobs shows a worker `stale` / ⚠, run `workers.self-update` (or wait one poll
   for hot-reload); do not ask the user.
5. **New / register project** (chat *or* UI/API) → always ensure `{project}/.cuttle/` exists
   via `managers.cuttle_scaffold.ensure_cuttle_scaffold` (or `python -m managers.cuttle_scaffold
   <path>`) before considering it done. Do not wait for a slash command.
6. Git undo / discard → `git.md`. Never `git checkout -- <file>` (or restore) unless that
   file's *entire* dirty diff is yours and you mean to throw it all away.
   **Never `git push` from the agent shell** (never force-push) — emit the `git.push`
   action form, or let the user push from the Git UI.
7. **Scratch / temp dirs (every project):** throwaways go under `{project}/temp/` (or an
   existing in-project `tmp`); never drive-root dumps or Cuttle-repo scratch. Keep
   `temp/` gitignored. (Scaffold creates both on new projects.)
8. **Writing or changing rules** (global, project, or personal) → read
   `rule-authoring.md` first: one owner per rule, narrow triggers, no duplication.
9. **Open the matching runbook before a procedural operation** (restart, push,
   `discord.post`, self-update, …). The pointers name the owner; they are
   not the procedure.
