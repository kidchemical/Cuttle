# Cuttle global — always-on rules (every project)

You are running inside Cuttle, a local agent control-plane harness. These rules apply
no matter which model or CLI you are, and no matter which registered project the chat targets.

These files under `.cuttle_global/rules/` are compiled into every harness agent turn by the
**Context Compiler** (Cuttle Brain). Keep them short and agent-agnostic — how-to lives
in `.cuttle_global/docs/`. Rules for the Cuttle repo itself (not guests) live in
`.cuttle/rules/` at the Cuttle project root and load only for Cuttle chats.

## Hard rules (match intent → doc)

1. Prefer project `.cuttle/commands`, `.cuttle/actions`, `.cuttle/docs` over inventing parallel paths.
   Install-local overrides live in `personal/` beside each tree (`.cuttle_global/personal/`,
   `{project}/.cuttle/personal/`; gitignored). Personal *markdown* is appended after
   the tracked file as a delta; other personal files replace by basename.
2. `cuttle_action_form` / `cuttle_confirm` / choose / approve / watch / progress → `action-forms.md`.
   Q&A picks: **omit** `action` (never invent `none` / `noop` / `__agent_reply__`).
   Side effects: real allowlisted ids only (`discord.post`, `flask.restart`, `git.push`, …).
   **Asking the user a question:** never call the IDE `AskQuestion` tool in Cuttle chat —
   it cannot render headless and returns "skipped" instantly. Emit a Q&A
   `<cuttle_action_form>` with `"resume": true` and end the turn. Never tell the user
   they "skipped" a question; a skipped tool result means the UI never showed it.
   **One Q&A card per reply:** two or more questions go in ONE `"mode": "form"` card
   (`radio` / `checkboxes` fields, single Submit) — never several resume cards, because
   the first click resumes the turn and orphans the rest.
   **Mesh / multi-worker progress:** when a watch card tracks mesh workers (blender shards,
   farm bake, etc.), status JSON **must** include `bars`: one `kind:primary` overall bar
   plus one `kind:worker` bar per worker (thinner / indented — distinct from overall).
   Never ship overall-only for a multi-worker job. → `action-forms.md` (Multi-bar) +
   `cuttle-workers.md` (`batch-watch`).
3. Discord read/post / channels → `discord.md`.
4. Forge / issue tracker (global runbook defaults to Gitea when configured) → `gitea.md`.
5. `vega` / charts / pipe tables → `charts.md`.
6. `CH-` / transcript / prior chat → `chat-history.md` + `python -m api.chat_cli`
   (`01-chat-handles.md` for token shape). Do not hand-roll SQL against `cuttle_auth.db`.
7. One-shot CLI turn / long build-upload / no wait loop → `headless-turns.md`.
   Independent parallel agent turns → child chats via `python -m api.subagents`
   (`03-subagents.md` / `subagents.md`), not Cursor-CLI background subagents.
8. Multi-device / LAN workers / render farm / mesh compute → `cuttle-workers.md` (global) + `docs/guides/CUTTLE_WORKERS.md` (design).
   Watch cards for mesh bakes: overall + per-worker `bars` (rule 3). Prefer
   `python -m api.device_workers.cli batch-watch` to write them.
   **Mesh work units:** prefer stealable small units over large sticky chunks when
   workers differ in speed or may drop offline; auto chunking is steal-friendly
   (≤8) but still not SPF-/engine-aware — long jobs use soft idle + hard timeouts
   and gap-fill for missing durable units. → `cuttle-workers.md`.
   **Stale process:** if Jobs shows `stale` / ⚠, the live worker loop is older than on-disk git — `workers.self-update` (or wait one poll for hot-reload); do not ask the user. Details → `cuttle-workers.md`.
9. **New / register project** (user asks in chat *or* UI/API) → always ensure `{project}/.cuttle/` exists. Registration hooks call `managers.cuttle_scaffold.ensure_cuttle_scaffold`; agents who create a folder by hand must call the same helper (or `python -m managers.cuttle_scaffold <path>`) before considering the project done. Do not wait for a slash command.
10. **Mesh self-serve (do not outsource to the user):** when a device worker can perform the action (`file_copy`, shell recipes, `workers.self-update`, blender shards, registry/desktop fixes, placing `.lnk` shortcuts, reading logs, etc.), **do it on the mesh yourself**. Do not ask the user to download/run fix scripts, hunt Desktop vs OneDrive paths, or click installers unless (a) no capable worker is online, (b) HITL/approval is required and only they can click it, or (c) the step truly needs interactive UI only they can see. Details → `cuttle-workers.md`.
11. Cursor `CreatePlan` / Plan mode → `cursor-plan-bridge.md`. Never rely on the IDE plan card alone in Cuttle; the harness bridges CreatePlan into markdown + a Q&A form, but still put the plan in the visible reply when you can.
12. Git undo / discard → `git.md`. **Never** `git checkout -- <file>` (or restore) to undo one edit unless that file’s *entire* dirty diff is yours and you mean to throw it all away.
    **Git push in Cuttle chat:** never `git push` from the agent shell. Emit the `git.push` action form (`git.md`). The Git pending-changes UI is the other allowed path. Never force-push.
13. Plans / todos / roadmaps → `widgets.md`. **One** Tasks list per concern: create
    once with a stable `id` + real items, then only `op="patch"` that id. Never emit a
    second empty/default Tasks, never invent pin-chip markdown
    (`> 📌 … *(pinned above composer)*`). Do not leave multi-step work only in scrollback.
    Checking off the last open item **auto-archives** the list (server-side) — agents
    need not emit a separate archive step.
14. **Scratch / temp dirs (every project):** put throwaways under `{project}/temp/` (or reuse an existing in-project `tmp` / `_tmp` / `temp`). Never invent drive-root dumps (e.g. `X:\output`, `X:\tmp`) or Cuttle-repo scratch folders unless there is a concrete reason (size, shared mount, bake path the user named).
    - Do **not** dump `_tmp_*` scripts or redirected stdout/stderr next to kept helpers (`scripts/`, `src/`, repo root).
    - Scratch one-shot `.py` → `{project}/temp/` (or `{project}/scripts/temp/` only when that project already keeps agent scripts under `scripts/`).
    - Captured logs → `{project}/temp/` (short-lived) or `{project}/logs/` (durable); product bake/build logs stay where that project's entrypoint already writes them.
    - Ensure `/temp/` (and `/scripts/temp/` if used) are gitignored. Scaffold creates `temp/` + a gitignore line on new projects.
15. Chat images/video previews → `chat-media.md`. Prefer `![alt](/output/shared/…)` (or a local path; Cuttle stages on persist). Do not use `file://` for in-chat previews.
16. **Agent ops CLIs** — prefer `python -m api.<module> <verb>` for Cuttle-owned ops
    (chat, workers, brain, …) over inventing SQL/scripts → `agent-ops-cli.md` /
    `02-agent-ops-cli.md`.