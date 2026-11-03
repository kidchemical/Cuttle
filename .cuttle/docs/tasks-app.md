# Tasks App

Open **Apps → Tasks**. It starts stashed and can be pinned to the blade bar.
It manages the established Tasks gizmo lists, so it ships directly (no
experimental toggle) — the same reasoning as the Projects App: it is a manager
over existing, always-available behavior, and lists created by agents must stay
reachable without a flag. Agent workflow and HTTP surface: `.cuttle_global/docs/gizmos.md`.

## What it does

- **Sidebar** — every list the signed-in user owns, filtered Active / Archived /
  All, searchable by title, description, task text and chat name/handle.
- **Tasks tab** — check, edit, nest (Tab / Shift+Tab), reorder (Alt+↑/↓) and
  delete tasks. Title, description and tree edits are a draft saved from the
  save bar (Ctrl+S); the save carries `expected_revision`, so if an agent wrote
  meanwhile the bar offers **Reload** or **Overwrite** instead of clobbering it.
  Checkbox toggles apply immediately while the tree has no unsaved edits.
  "Agents only" lists stay read-only until **Let me edit tasks** switches them
  to shared editing (the same rule the composer strip enforces).
- **Chats tab** — home chat or project, linked chats (link/unlink), moving a
  chat list to its whole project or a project list to one chat, and
  **Same orchestration** suggestions: sub-agent parent/children/siblings and
  live supervisor/supervised chats of the home chat, with **Link all**.
- **History tab** — the attributed audit log (who, which agent/model, what
  changed); agent reads are hidden unless toggled on.
- **New list** — pick a home chat, scope, editing mode, optional linked chats,
  and initial tasks. Deep links: `/tasks_page.html?list=<id>[&tab=chats]` opens
  a list; `?chat=<id>` starts a new list for that chat. The composer strip's ↗
  button opens its list here.
- **Archive / Restore / Delete** — delete is permanent (confirmed) and is
  recorded in the audit log; archive keeps the list.

The page polls `GET /api/gizmos/tasks?all=1` every 8 s while visible and
refreshes the open list unless it has unsaved edits.

## Removal

Delete `src/web/tasks_page.html`, `src/web/js/tasks/`, `src/web/css/tasks_page.css`,
the `/tasks_page.html` route in `api.gizmos.routes`, `api.gizmos.tasks_manager`
and the `?all=1` branch / `related` route in `tasks_routes`, and the `nav-tasks`
rail entry (keep `RAIL_LAYOUT_VERSION`; stale ids are filtered on restore).
Linked chats are part of the Tasks owner and stay.
