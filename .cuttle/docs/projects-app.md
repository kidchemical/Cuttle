# Projects App

Open **Apps → Projects**. It starts stashed and can be pinned to the blade bar.
This expands established project management and ships directly (no experimental
toggle): registering/editing projects already exists, and broken locations need
to remain repairable.

## Locations

Each project has an ordered list of absolute folder paths. Drag a grip or use the
up/down buttons, then **Save paths**. **Test on host** checks the draft without
saving it. The first accessible directory wins, on the Cuttle host only.
Windows drive/UNC paths are preserved on Linux and marked Different OS.
Linux network paths must already be mounted; Cuttle does not mount shares.
A leading `~/` is expanded using the host's home directory.

The UI keeps registered paths separate from the resolved path. Resolution uses
only the saved list in its exact order (or the registered path for a record
without a list). It does not silently substitute machine path aliases.
Missing paths can be saved for a future host or mount. If every location is
unavailable, turns bound to that project are rejected before execution. Resolution
is pinned for the current request; edits apply to subsequent turns. Project IDs
and chat history remain intact when paths or project names change.

## Workflows

- **Add project** registers an existing host folder and initializes `.cuttle/`.
- **Overview** edits name, description, tags, and an optional forge URL.
- **Configuration** inventories shared/project layers and their personal overlays.
  It does not edit arbitrary config files or claim that every shared layer is enabled;
  the project's `GLOBAL.ini` still owns participation.
- **Activity** shows only the signed-in user's user/assistant messages, newest first,
  with pagination and chat links. Totals are also user scoped. ID association survives
  path/name changes; legacy records without IDs match registered names/paths.
- **Repository** reads local Git branch/origin details with timeouts; **Open Git**
  opens the established Git app for the selected project.
- **Archive** hides a project from normal pickers; Show archived allows restoration.
- **Remove from Cuttle** requires the exact project name in a confirmation dialog.
  It unregisters the project and keeps its files, chat history, and registry audit log.
  Old chats referencing a removed project must select another project before execution.

Changes require owner privileges. Authenticated non-owners can inspect projects,
read their own activity, and start chats in available projects. Existing API contracts
remain available; the App uses a dedicated confirmed-removal endpoint.

Deep analytics and arbitrary configuration-file editing remain follow-up work.

## Agent interface

From the Cuttle root, with the project venv:

```bash
PYTHONPATH=src .venv/bin/python -m api.projects_cli list
PYTHONPATH=src .venv/bin/python -m api.projects_cli get 7
PYTHONPATH=src .venv/bin/python -m api.projects_cli check 'D:/Projects/Game' '/home/me/Projects/Game'
PYTHONPATH=src .venv/bin/python -m api.projects_cli update 7 --json '{"paths":["/home/me/Projects/Game","D:/Projects/Game"]}'
PYTHONPATH=src .venv/bin/python -m api.projects_cli register --name Demo --path /home/me/Projects/Demo
PYTHONPATH=src .venv/bin/python -m api.projects_cli remove 7 --confirm-name Demo
```

The CLI uses `ProjectManager`; no independent registry or path resolver.
Normal agent confirmation etiquette still applies to writes outside the active project.

## Ownership and validation

Registry and atomic config/history updates: `managers.project_manager`.
Ordered host checks: `managers.project_locations`. Read-only Git/config summaries:
`managers.project_details`. User-scoped activity SQL: `api.auth_db`.
HTTP and App page: `api.project_routes`; UI: `projects_page.js`/`.css`/`.html`.
The existing chat ingress pins the owned resolver result before execution.

Tests: `test_projects_app.py`, existing projects/chat/architecture suites, and
`e2e/test_projects_app.py` (Chromium, private static server, mocked API; no live host).
Python changes require the daemon-owned Flask restart; then refresh the shell.
