# Supervision App

Open **Apps → Supervision** (flag `chat_supervision`; starts stashed, can be
pinned). It is the general view of chat supervision; one supervision's own
settings are still edited from its chat (the **Supervising** badge, or
**☰ → Supervise this chat…** when starting one).

## What it does

- **Running now**: every supervision of the signed-in user still in force,
  paused ones included: the supervised chat, its supervisor and agent, what the
  chat was doing at the last check, the next check, the System care it has on
  and anything that needs attention (rule violations, display off, memory low).
  **Open chat** / **Open supervisor**, **Pause…** (asks for how long) /
  **Resume now**, **Stop**. The list refreshes every 8 s while visible. A line
  above it says when the host is keeping the display awake, or why it cannot.
- **Defaults for new supervisions**: check interval, supervisor agent, when to
  involve the user, how an idle chat is nudged, System care (processes, memory,
  display) and the forgotten-pause alert. Install-wide (`settings.json` →
  `supervision_defaults`, plus the existing `supervision_paused_alert_hours`),
  so only the owner saves them.

## Precedence

Most specific first: the value given for that supervision (dialog field or
`chat_cli supervise` flag) → the orchestrator playbook's supervisor settings →
these defaults → the built-ins (`api.chat_ops.defaults.BUILTIN`). A default only
fills a value a **new** supervision did not give: schedules already running keep
what they stored, and the agent default only preselects the dialog (a CLI
`supervise` without `--agent` still follows the supervisor chat's own agent).

## Display care

System care → **Keep the display awake** (`--system display`, off by default):
`api.chat_ops.display_care` holds one idle inhibitor (`wakepy`) while a live
supervision with the setting on has an unfinished chat, and tells the
supervisor once per episode when the display is off or locked anyway, or when
no inhibitor could be taken. It prevents idle blanking and auto-lock; it does
not wake a display and cannot see a monitor switched off at its power button.
Reading the display state works on Linux desktops (screen saver; GNOME monitor
power); elsewhere only the inhibitor runs.

## Owner

`src/api/chat_ops/`: `defaults` (pure `normalize`, `load` / `save`),
`service.supervisions` (the list), `display_care`, routes
`GET /api/chat-ops/supervisions`, `GET|PUT /api/chat-ops/defaults`,
`/supervision_page.html`. Client: `js/supervision/supervision_model.js`
(`CuttleSupervisionApp`, pure; option lists come from
`CuttleChatSupervisionModel`) + `supervision_page.js` on the shared
Orchestrator page skin. Tests: `tests/chat_ops/test_supervision_defaults.py`,
`test_supervision_app_js.py`, `test_display_care.py`, `e2e/test_supervision_app.py`.

## Removal

Delete `src/web/supervision_page.html`, `src/web/js/supervision/`,
`src/web/css/supervision_page.css`, the three routes above and the
`nav-supervision` rail entry (keep `RAIL_LAYOUT_VERSION`; stale ids are filtered
on restore). Without `api.chat_ops.defaults`, `service.supervise` goes back to
its built-in values; a stored `supervision_defaults` key is then ignored.
Display care is independent of the App: drop the `display` key from
`brief.SYSTEM`, `display_care.py`, its two call sites (`system_care`, the worker
loop) and the `wakepy` requirement; schedules that stored `display` read as off.

## Themes

Status and accent text on the shared skin uses `--orch-good-text`, `--orch-warn-text`,
`--orch-bad-text` and `--orch-accent-text` (`orchestrator_page.css`), not the raw
`--orch-good` / `--orch-bad` tones: those are the themes' border colours, greys on
Midnight. `test_text_is_readable_in_each_theme` audits the page with
`control_helpers.low_contrast_text`; use the same helper for another page.
