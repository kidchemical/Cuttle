# Chat widgets (Tasks)

Durable panels above the composer (above queued prompts and pending changes).
Agents pin plans/todos here so you do not re-scroll chat history.

## When to use

- Multi-step plans, roadmaps, or todo lists → create/update a **Tasks** widget.
- Prefer the widget over (or in addition to) markdown checklists in the bubble.
- Mark progress with `op="patch"` as you complete items — do not only narrate.
- Set a **description** when the list has intent beyond the title (why it exists,
  what “done” means). Users see it on an ⓘ tooltip; agents get it in the context digest.

## One list per concern (do not double-pin)

Agents often “fulfill” the pin rule twice in one turn: a real widget early, then a
second empty **Tasks · 0/0** (or fake pin markdown) at the end. **Don’t.**

- **Create once** for a given plan/concern — pick a stable `id` (e.g. `vine-tip-plan`)
  and include real `items` on first emit.
- **After that, only patch** that same `id` (`op="patch"`). Progress = patch, not a
  new tag.
- If the context digest already lists Active chat widgets, **those are the lists** —
  patch them; do not open another default `Tasks`.
- **Never** write the post-rewrite chip by hand:
  `> 📌 **…** · N/M *(pinned above composer)*` — that is display-only, not a pin.
- **Never** emit an empty stub (`items: []` / no body) “to satisfy the rule.”
- Separate concerns may use separate ids; the same concern must not get a second list.

## Create / replace

```xml
<cuttle_widget type="tasks" id="auth-refactor" scope="session" title="Tasks" edit="agent"
  description="Track the auth refactor for Cuttle chat. Prefer patching this list over restating it.">
{"items":[
  {"id":"1","text":"Scaffold API","done":false,"children":[
    {"id":"1a","text":"auth_db table","done":true,"children":[]}
  ]}
]}
</cuttle_widget>
```

Longer blurbs can live in the JSON body instead of (or in addition to) the attr:

```xml
<cuttle_widget type="tasks" id="auth-refactor" title="Auth refactor">
{"description":"Track the auth refactor for Cuttle chat. Prefer patching this list.",
 "items":[{"id":"1","text":"Scaffold API","done":false}]}
</cuttle_widget>
```

- `title` — display alias (`Tasks`, `To-do`, `Goals`, or a plan name). Default **Tasks**.
- `description` / `summary` — optional intent blurb (tooltip + agent digest). Prefer body JSON for long text.
- `scope="session"` (default) — this chat only.
- `scope="project"` — every chat with the same project chip.
- `edit="agent"` (default) — agent-managed; checkboxes disabled for you.
- `edit="shared"` — you can toggle checkboxes too.
- Header buttons flip scope / edit mode anytime.

## Patch (preferred for progress)

```xml
<cuttle_widget type="tasks" id="auth-refactor" op="patch">
{"set_done":["1","1a"],"add":[{"parent":"1","item":{"id":"1b","text":"tests","done":false}}]}
</cuttle_widget>
```

Update the blurb without touching items:

```xml
<cuttle_widget type="tasks" id="auth-refactor" op="patch">
{"description":"Now focused on write-path tests only."}
</cuttle_widget>
```

Ops: `set_done`, `set_undone`, `set_text`, `add`, `remove`, replace `items`, or set `description` / `summary` / `set_description`. Omitting description keeps the prior value; `""` clears it.

## Auto-archive when complete

When **every** item (including nested children) is `done`, the store **auto-archives**
the widget — it leaves the strip and drops out of the agent digest. No separate
archive tag or ✕ click is required. This is especially for agent-authored lists
(`edit="agent"`); shared lists behave the same when the last checkbox is ticked.

- Empty lists (`0/0`) stay active (do not create empty stubs).
- Marking an item undone or adding a new open item on a later patch **reactivates**
  the list.
- Manual ✕ archive still works anytime; `status="archived"` on a tag also works.

Agents: just `set_done` the last open id(s). Do not leave a finished  N/N list pinned.

## Agent ops CLI

In a **chat reply**, keep using `<cuttle_widget>` so the bubble shows the pin.
From the **shell** (inspect, or patch without a tag):

```bash
PYTHONPATH=src .venv/bin/python -m api.widgets_cli list --session CH-000465 --json
PYTHONPATH=src .venv/bin/python -m api.widgets_cli get auth-refactor --json
PYTHONPATH=src .venv/bin/python -m api.widgets_cli patch auth-refactor --set-done 1,1a --json
PYTHONPATH=src .venv/bin/python -m api.widgets_cli patch auth-refactor --ops "{\"set_done\":[\"1\"],\"description\":\"…\"}" --json
```

Flags go **after** the verb. `--session` accepts `CH-…` or a numeric id. Patch JSON matches the tag body (`set_done`, `add`, `remove`, `description`, …).

## REST

- `GET /api/widgets?session_id=`
- `PUT|PATCH /api/widgets/<id>` (accepts `description`)
- `PATCH /api/widgets/<id>/items/<item_id>` `{done:true}`

Multi-device: clients poll `widgets_revision` on live-status / refresh the strip.

## Context

Active tasks (including description as `intent:`) are injected into the Context
Compiler runtime digest so mid-work turns can patch by id without re-reading CH history.
When that digest is present, treat listed ids as authoritative — patch only; do not
create a second Tasks list for the same work.

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell, set
the path and use the Windows interpreter; for example:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.widgets_cli list --session CH-000465 --json
```

Use PowerShell backticks for multiline continuation and single-quoted JSON
arguments; POSIX examples use backslashes for continuation.
