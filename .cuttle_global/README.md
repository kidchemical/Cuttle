# `.cuttle/` — per-project Cuttle config

This folder is the **canonical layout** every Cuttle-registered project should mirror.
Cuttle (this repo) owns the pattern; Escape Purgatory and any future project copy the
same shape under *their* project root — they do **not** inherit actions/commands from here.

```text
{project_root}/.cuttle/
  commands/*.md     # slash commands (/name) — on-demand expand (YAML frontmatter + body)
  rules/*.md        # always-on project guidelines (Context Compiler / Cuttle Brain)
  actions/*.yaml    # allowlisted side effects for <cuttle_action_form> / <cuttle_confirm>
  docs/             # runbooks agents should open when doing project-specific work
  scripts/          # optional helpers invoked by shell actions
  agents/<id>/      # optional drop-in harness agents (manifest + adapter)
  memory/           # reserved (future Brain memory retrieval — do not dump wholesale)
  learnings/        # repo-only FEAT/ERR/LRN backlog (not Brain-injected; not scaffolded)
```

Shared context for *every* agent is assembled by the **Context Compiler**
(`src/api/cuttle_brain/`). See `src/api/cuttle_brain/CONTEXT_COMPILER.md`.

## Auto-scaffold on register

`ProjectManager.add_local_project` / `add_github_project` / `add_gitlab_project`
call `managers.cuttle_scaffold.ensure_cuttle_scaffold` after a successful insert
(idempotent — never overwrites existing files).

Agents who create a project **in chat without** hitting that API must still run:

```text
..\.venv\Scripts\python.exe -m managers.cuttle_scaffold "E:\path\to\project" --name "Display Name"
```

(from `/path/to/Cuttle/src`).

Unity-style repos may also keep a nested copy under `source/.cuttle/…`. Cuttle prefers
the registered project root’s `.cuttle/`, then the nested `source/` copy.

## Rules of thumb

1. **Per project** — Discord guilds, deploy scripts, and channels live in *that* project’s
   `.cuttle/actions` and `.cuttle/docs`, not in Cuttle’s global copy.
2. **Commands teach; actions execute** — command markdown tells the agent *what to draft*;
   action YAML is the only allowlist Flask will run on Confirm / form submit (no LLM).
3. **Prefer forms for choices** — `<cuttle_action_form>` for A/B/C or multi; `<cuttle_confirm>`
   for a single Confirm/Cancel.
4. **Scratch goes in `{project}/temp/`** — never drive-root dumps or `_tmp_*` next to kept
   helpers. Scaffold creates `temp/` + a `/temp/` gitignore line. Details: global
   `.cuttle_global/rules/00-core.md` rule 14.
5. **Install-local overlay** — `.cuttle_global/personal/` mirrors tracked `.cuttle_global/` (gitignored).
   Same-relative-path files win over tracked copies. Keep LAN hosts / absolute paths there.
6. **Details** — authoring guide: Cursor skill `cuttle-project-commands`, or
   `.cuttle_global/docs/commands-and-actions.md` in this repo.
