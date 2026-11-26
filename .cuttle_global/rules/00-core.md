# Cuttle global — always-on rules (every project)

You are running inside Cuttle, a local agent control-plane harness. These rules apply
to every model, CLI and registered project. Runbooks are named in the "Global
runbooks" directory above; the rules below are the invariants it does not carry.

## Hard rules (match intent → doc)

1. Prefer project `.cuttle/commands`, `.cuttle/actions`, `.cuttle/docs` over inventing parallel paths.
   Install-local overrides live in gitignored `personal/` overlays (`<home>/personal/` in the
   Cuttle home, `{project}/.cuttle/personal/`).
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
   Do device-capable work on the mesh yourself; ask the user only when no capable
   worker is online or a step needs their click. A `stale` / ⚠ worker → run
   `workers.self-update`, do not ask.
5. **New / registered project** → ensure `{project}/.cuttle/` exists
   (`python -m managers.cuttle_scaffold <path>`) before calling it done.
6. Git undo / discard → `git.md`. Never `git checkout -- <file>` (or restore) unless that
   file's *entire* dirty diff is yours and you mean to throw it all away.
   **Never `git push` from the agent shell** (never force-push) — emit the `git.push`
   action form, or let the user push from the Git UI.
7. **Scratch / temp files** → `{project}/temp/` (gitignored), or an existing
   in-project `tmp`; never drive-root dumps or scratch inside the install tree.
8. **Writing or changing rules** (global, project, or personal) → read
   `rule-authoring.md` first: one owner per rule, narrow triggers, no duplication.
9. **Open the matching runbook before a procedural operation** (restart, push,
   `discord.post`, self-update, …). The pointers name the owner; they are
   not the procedure.
