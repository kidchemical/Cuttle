# `.cuttle_global/` — shared global Cuttle configuration

This tree supplies shared rules, runbooks, actions, scripts, skills and agent
configuration for every registered project. A project's `.cuttle/GLOBAL.ini`
selects which global layers apply; the safety core remains non-severable.
See [GLOBAL.ini](../.cuttle/GLOBAL.ini) for the selection keys.

```text
.cuttle_global/
  rules/    docs/    actions/    scripts/    skills/    agents/
  personal/   # install-local global overlay (gitignored)
```

Each project owns its own `.cuttle/` commands, rules, actions, docs and scripts;
it does not inherit another project's commands or actions. See the
[project reference layout](../.cuttle/README.md) and
[commands and actions guide](docs/commands-and-actions.md).

`<home>` is the per-user Cuttle home (`~/.local/share/cuttle`, `%LOCALAPPDATA%\Cuttle`, or `$CUTTLE_HOME`; `core.runtime_paths.cuttle_home()`).

Install-local facts belong under `<home>/personal/` for shared configuration
or `{project}/.cuttle/personal/` for a project. Rule/doc Markdown merges by
appending a personal delta to tracked text; supported non-Markdown overlays such
as action YAML replace by basename. This is not a universal replacement promise
for every loader or literal script path. See the [overlay contract](../docs/architecture/cuttle-home.md#personal-overlays).

Scratch belongs under `{project}/temp/`; see the scratch invariant in
`rules/00-core.md`. New projects are scaffolded through
`managers.cuttle_scaffold.ensure_cuttle_scaffold`; see the project reference.

Commands/actions/skills replace whole units by their normalized command/action
name or skill directory id. `disabled: true` in the winning unit hides lower
copies. List/get use the same winning source; rules/docs still append deltas.
Script recipes resolve literal paths: a personal script does not redirect a
tracked recipe unless its action/command explicitly names that script.

`[global]` also supports `skills=off` and `commands=off`, including global personal
units. Optional integration guidance defaults off; enable it for a project in
`.cuttle/GLOBAL.ini` or its personal twin:

```ini
[integrations]
<integration-id> = on
```

Optional skills can declare `integration: <integration-id>` in frontmatter to
use the same gate. The flag does not start a service or grant authorization.
Core currently ships no optional forge integration or forge runbook; install
integration guidance in the owning project's `.cuttle/`.
