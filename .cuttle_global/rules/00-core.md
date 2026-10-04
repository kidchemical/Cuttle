# Cuttle global — always-on rules (every project)

You are running inside Cuttle, a local agent control-plane harness. These rules apply
no matter which model or CLI you are, and no matter which registered project the chat targets.

These files under `.cuttle_global/rules/` are compiled into the fresh/full context by the
**Context Compiler** (Cuttle Brain) and ride change-deltas on resume. They hold hard invariants and intent→doc pointers only —
how-to lives in `.cuttle_global/docs/`. Rules for the Cuttle repo itself (not guests) live in
`.cuttle/rules/` at the Cuttle project root and load only for Cuttle chats.

## Hard rules (match intent → doc)

1. Prefer project `.cuttle/commands`, `.cuttle/actions`, `.cuttle/docs` over inventing parallel paths.
   Install-local overrides live in `personal/` beside each tree (`.cuttle_global/personal/`,
   `{project}/.cuttle/personal/`; gitignored). Personal *markdown* appends after the
   tracked file as a delta; other personal files replace by basename.
2. User-visible choices, confirmations, watches, and progress → `cuttle_action_form`
   (`action-forms.md`). Side effects need real allowlisted ids (`discord.post`,
   `flask.restart`, `git.push`, …). **Asking the user a question:** never call the
   `AskQuestion` tool in Cuttle chat — headless it returns "skipped" with nothing shown.
   Emit one Q&A `<cuttle_action_form>` with `"resume": true` and end the turn; never tell
   the user they "skipped". On preference cards omit `action` entirely; never invent
   `none` / `noop` / `__agent_reply__`. Mesh watch cards need overall + per-worker `bars`.
3. Discord reads → `python -m api.discord_cli`, posts stay on `discord.post` (`discord.md`).
4. Forge / issue tracker → the enabled integration runbook for the active project.
   Optional integration guidance requires explicit project configuration (`GLOBAL.ini`).
5. `vega` / charts / pipe tables → `charts.md`.
6. `CH-` / transcript / prior chat → `python -m api.chat_cli`, never hand-rolled SQL
   against `cuttle_auth.db` (`chat-history.md`, `01-chat-handles.md` for token shape).
7. One-shot CLI turn / long build-upload / no wait loop → `headless-turns.md`.
   Two or more independent agent turns → real child chats via `python -m api.subagents`
   (`03-subagents.md` / `subagents.md`), not background CLI subagents. Supervised
   children keep their owned `--wait` path (`subagents.md`).
8. Multi-device / LAN workers / render farm / mesh compute → `cuttle-workers.md`.
   **Mesh self-serve:** do device-capable work on the mesh yourself (`file_copy`, shell
   recipes, `workers.self-update`, …) — ask the user only when no capable worker is online,
   HITL/approval needs their click, or the step truly needs interactive UI only they see.
   If Jobs shows a worker `stale` / ⚠, run `workers.self-update` (or wait one poll
   for hot-reload); do not ask the user.
9. **New / register project** (chat *or* UI/API) → always ensure `{project}/.cuttle/` exists
   via `managers.cuttle_scaffold.ensure_cuttle_scaffold` (or `python -m managers.cuttle_scaffold
   <path>`) before considering it done. Do not wait for a slash command.
10. Cursor `CreatePlan` / Plan mode → `cursor-plan-bridge.md`. Never rely on the `CreatePlan`
    tool call alone; also put the plan in the visible reply.
11. Git undo / discard → `git.md`. Never `git checkout -- <file>` (or restore) unless that
    file's *entire* dirty diff is yours and you mean to throw it all away.
    **Never `git push` from the agent shell** (never force-push) — emit the `git.push`
    action form, or let the user push from the Git UI.
12. Plans / todos / roadmaps → `widgets.md`. **One** Tasks list per concern (stable `id` +
    real items, then only `op="patch"`); never a second empty/default Tasks, never invent
    pin-chip markdown (`> 📌 … *(pinned above composer)*`).
13. **Scratch / temp dirs (every project):** throwaways go under `{project}/temp/` (or an
    existing in-project `tmp`); never drive-root dumps or Cuttle-repo scratch. Keep
    `temp/` gitignored. (Scaffold creates both on new projects.)
14. Chat images/video previews → `![alt](/output/shared/…)` (or a local path; Cuttle stages
    on persist), never `file://` (`chat-media.md`).
15. Cuttle-owned ops (chat, workers, brain, …) → `python -m api.<module> <verb>`, never
    raw DB recipes as the primary path (`agent-ops-cli.md` / `02-agent-ops-cli.md`).
16. **Writing or changing rules** (global, project, or personal) → read
    `rule-authoring.md` first: one owner per rule, narrow triggers, no duplication.
17. **Open the matching runbook before a procedural operation** (restart, push,
    `discord.post`, self-update, …). The pointers above name the owner; they are
    not the procedure.
