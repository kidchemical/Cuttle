---
name: cuttle-electron-debug
description: >-
  Debug and reproduce Cuttle desktop (Electron) UI bugs by attaching Chrome
  DevTools Protocol (CDP) directly to the running app — click rails, evaluate
  JS, force navigations, measure freezes. Use when Electron freezes, Chat→Git
  (or other shell nav) wedges, UI works in browser but not desktop, SSE /
  connection-pool hangs, or the user reports a Cuttle.exe / Electron-only bug.
  Prefer this over browser-only Playwright when the bug is desktop-specific.
---

# Cuttle Electron — debug via CDP (do this first)

**Hard rule:** If the bug is in **Cuttle.exe / Electron** (freeze, nav, SSE, shell),
**attach to the desktop app and interact with it**. Do not silently debug only
against a browser tab and assume it matches Electron. Tell the user when you
are driving their Electron window via CDP.

Browser Playwright is fine for shared HTML/JS issues. Electron has a **single
renderer**, shared HTTP/1.1 pool (~6 slots/origin), YouTube wallpaper iframes,
and multi-pane chat SSE — browser will not reproduce those freezes.

## When to use

- User: “Electron freezes”, “desktop locks”, Chat→Git, shell nav, tray app
- Works on LAN/browser, fails in `Cuttle.exe`
- Suspected SSE / connection-pool / iframe teardown issues

## Attach (preferred workflow)

### 1. Launch Electron with CDP

**Dev (picks up live `electron/main.js` + `preload.js`):**

```powershell
Get-Process electron,Cuttle -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -FilePath "/path/to/Cuttle/electron\node_modules\electron\dist\electron.exe" `
  -ArgumentList ".","--remote-debugging-port=9333" `
  -WorkingDirectory "/path/to/Cuttle/electron"
```

**Packaged** (`dist\win-unpacked\Cuttle.exe`): same `--remote-debugging-port=9333`
arg if the asar `main.js` does not already enable it. Packaged **preload/main**
come from `resources\app.asar` — Flask UI is still live from `:8000`, but IPC
fixes need an asar rebuild/repack.

### 2. Confirm CDP

```powershell
Invoke-RestMethod http://127.0.0.1:9333/json/list
```

Look for `type: page` with `app_shell.html` (UI prefers `http://127.0.0.1:8000`).

### 3. Drive the app

Use a short Python + `websockets` CDP client (or equivalent):

- `Runtime.evaluate` — inspect shell scripts (`?v=` cache bust), iframe `src`,
  `window.electron.openGitPage`, call `navigate(0, …)` / click `#nav-git`
- `Page.navigate` / `Page.reload` — **browser-level** nav still works when
  in-page `location.href` is wedged under SSE
- After top-level nav, **re-fetch** `/json/list` (page target id changes)
- If `Runtime.evaluate` times out after a click that should unload the shell,
  poll `/json/list` for the new URL — that often means nav **succeeded**

Do **not** leave `--remote-debugging-port` enabled in committed production
`main.js` unless the user wants dogfood debugging on by default.

## Known Electron failure modes (Cuttle)

| Symptom | Likely cause | What works |
|---|---|---|
| Chat→Git freezes with live SSE | In-page nav blocked while EventSources hold HTTP/1.1 slots | Pause streams across panes before nav; Git paint must not infinite-loop |
| Opens Git then locks (CPU pegged) | Git SVG paint sized to `viewH` → scroll/resize feedback loop | `git_graph_page.js` paint key + never size SVG to viewport; debounce resize |
| Browser OK, Electron wedged | Shared pool / single renderer | Reproduce on Electron via CDP, not only Playwright |
| Packaged app ignores IPC/preload fix | Stale `app.asar` | Repack asar or run Electron from `electron/` source |

**Do not “fix” freezes by crippling UX:** never strip restored multi-pane layouts on
boot, and never leave split iframes on `about:blank` as a substitute for fixing
the real bug (usually Git paint or connection pressure).

Git top-level page uses `?shell=1` and a **← Shell** link back to `app_shell.html`.

## Source vs packaged

| | UI HTML/JS/CSS | `main.js` / `preload.js` |
|---|---|---|
| Flask `:8000` | Live (hard-refresh / cache-bust `?v=`) | — |
| `npm start` / `electron .` | Flask | Live from `electron/` |
| `dist\win-unpacked\Cuttle.exe` | Flask | **asar only** — extract, copy, `npx asar pack` |

## Don'ts

- Don't claim an Electron freeze is fixed after only testing in Chrome/Playwright
- Don't keep hammering iframe teardown if CDP shows `location.href` itself is stuck under SSE — escalate to main-process `loadURL` / IPC
- Don't kill Flask without asking (see restart-cuttle rule); restarting Electron is OK for desktop repro, but say so
- Don't leave a wedged Electron + open CDP session without telling the user

## Quick checklist

```
- [ ] Bug is Electron/desktop-specific? → CDP attach (this skill)
- [ ] Listed targets on :9333
- [ ] Reproduced on the real shell (rails / iframes / electron.*)
- [ ] Distinguished in-page nav failure vs main-process loadURL success
- [ ] Told user you were controlling their Electron window
```
