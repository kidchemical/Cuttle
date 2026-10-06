# Gizmos (experimental)

**Widgets** render inside an agent's chat bubble (charts, forms). **Gizmos**
live outside the transcript, in the shell: docked on the Electron title bar or
the blade bar, floating over every Space, or popped out as an always-on-top
desktop window. They persist install-wide and stay put across chats.

Gated by the `gizmos` experimental flag (default off). When it is off, every
verb and route answers `disabled` and the shell shows nothing.

The first type is **`usage_meter`**: plan budget left for one agent (Codex,
Claude Code, Cursor) plus when the tightest window resets, or when a
blocked account unblocks. It is the same data as `/usage`, normalized.

## Placement

| Dock | Where | Fallback |
|---|---|---|
| `titlebar` | Electron title bar, right side | blade bar in a browser |
| `rail` | leftmost blade bar, above the footer | — |
| `float` | over the shell, every Space; `x`/`y` are 0..1 viewport fractions | — |
| `popout` | always-on-top desktop window (one per gizmo) | `float` in a browser |

`order` sorts gizmos within `titlebar`/`rail`. Users drag gizmos between
docks, or click/right-click one for details, agent, move, refresh, remove.
Apps → **Gizmos** creates and edits them without an agent.

## Agent ops CLI

```bash
PYTHONPATH=src .venv/bin/python -m api.gizmos types
PYTHONPATH=src .venv/bin/python -m api.gizmos list
PYTHONPATH=src .venv/bin/python -m api.gizmos create usage_meter --agent claude --dock titlebar
PYTHONPATH=src .venv/bin/python -m api.gizmos create usage_meter --agent codex --id codex-meter --dock float --x 0.8 --y 0.05
PYTHONPATH=src .venv/bin/python -m api.gizmos update codex-meter --show used --window weekly --title "Codex weekly"
PYTHONPATH=src .venv/bin/python -m api.gizmos move codex-meter --dock popout
PYTHONPATH=src .venv/bin/python -m api.gizmos data codex-meter --refresh
PYTHONPATH=src .venv/bin/python -m api.gizmos usage codex
PYTHONPATH=src .venv/bin/python -m api.gizmos remove codex-meter
```

JSON out; exit `0` ok, `1` flag off, `2` invalid/not found. Writes bump a
store revision the shell polls every few seconds, so open windows update with
no restart and no chat message. Check `list` before creating a meter; do not
create a second meter for an agent that already has one unless asked.

`usage_meter` config: `agent` (`codex` | `claude` | `cursor`), `window`
(`tightest` or a window id from `usage <agent>`, e.g. `five_hour`, `weekly`),
`show` (`remaining` | `used`). Changing the agent keeps a default title in step;
a custom title sticks.

## REST (owner session)

- `GET /api/gizmos` → `{revision, gizmos, types}`
- `POST /api/gizmos` `{type, config, placement, title?, id?}`
- `GET|PATCH|DELETE /api/gizmos/<id>` (`PATCH` takes partial `title`/`config`/`placement`)
- `GET /api/gizmos/<id>/data[?refresh=1]` — live data (usage cached 60 s; forced refresh at most every 15 s)

## Extending

- New usage agent: `api.gizmos.usage.register_provider(agent, label, fetch)`
  where `fetch()` returns `{windows, blocked, plan, extras, error}` (see the
  module docstring). It shows up in every agent picker automatically.
- New gizmo type: `register_type(GizmoType(...))` in `api/gizmos/catalog.py`
  plus a renderer branch in `src/web/js/gizmos/gizmos_model.js`.

The Tasks strip above the composer (`widgets.md`) predates this split and is
still called a widget. It is a gizmo by this definition; it has not moved.

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.gizmos list
```
