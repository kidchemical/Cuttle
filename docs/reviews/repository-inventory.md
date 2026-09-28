# Repository inventory

**HEAD:** `4f2880c` (this pass). **Baseline session HEAD:** `9ecd388` had **914** tracked files; this pass recounts **918** (audit docs committed).

## Baseline reconciliation (this pass)

| Metric | Count | Source |
|---|---|---|
| Tracked files | 918 | `git ls-files` |
| Untracked (not ignored) | 0 | `git ls-files --others --exclude-standard` |
| Inventory rows | 918 | must equal tracked |

Prior ignored-path count **72628** was recorded 2026-09-27 at `9ecd388`; not re-walked file-by-file this pass. Directory classification in Phase 1B is unchanged.

## Git status legend

| Status | Meaning |
|---|---|
| tracked | In Git index (every row below) |
| ignored | See Phase 1B (directory class only) |
| untracked | None at this working tree for source |

## Classification codes

1 Active source · 2 Legitimate local config · 3 Runtime state · 4 Regenerable artifact · 5 Abandoned artifact · 6 Sensitive · 7 Uncertain

Tracked files are almost all **1**. Class **5** is used only where this pass showed a broken path or unserved backup. Class **5** still means **do not delete until consumers are product-confirmed**; it is not a delete ticket.

## Review status (inventoried ≠ reviewed)

| Code | Meaning |
|---|---|
| inventoried | Path classified; not treated as inspected source |
| structure | Python AST parse + top-level defs, or JS file header read |
| execution | Callers/boot path traced in this investigation |
| partial-execution | Oversized; sampled routes/callers, not every line |
| structure-failed | Could not parse |

**Python AST coverage:** 540 of 540 `.py`+`.js` files. Parse failures: 0.


## Phase 1B — ignored / local (directory classification)

No secrets contents are reproduced. Paths that typically hold secrets are named only.

| Path (this machine) | Git | Class | Notes |
|---|---|---|---|
| `.venv/` | ignored | 4 | pip install from `src/requirements/requirements.txt`; **required** for run, not for clone |
| `src/.env` | ignored | 6 | Copy from `src/.env.example` (tracked) |
| `src/settings.json` | ignored (`*.json`) | 2+3 | Created with defaults by `SettingsManager` if missing |
| `src/data/db/*.db`, `action_hmac_secret` | ignored | 3+6 | Created at runtime |
| `.cuttle/certs/` | ignored | 3+6 | Generated HTTPS certs |
| `.cuttle/personal/` | ignored | 2 | Install overlay |
| `.cuttle/keys/*` except `*.pub` | ignored | 6 | Mesh private key local |
| `_personal/` | ignored | 2+6 | Home-lab dogfood |
| `electron/node_modules/`, `electron/dist/` | ignored | 4 | `npm install` in `electron/` |
| `apps/mobile/android/.gradle`, `**/build/` | ignored | 4 | Android build |
| `src/output/`, `output/`, `src/cache/` | ignored | 3+4 | Uploads, generated media |
| `temp/` | ignored | 4+5 | Dev-machine scratch |
| Repo-root `cuttle_flask_restart_*.json` | ignored | 3 | Daemon/Flask restart |
| `vendor/claw-code/` **contents** | ignored | 4+7 | Gitlink; contents not redistributed |
| `vendor/mcp-govee` | submodule | 1 | `.gitmodules` |
| `docs/media/promo/` | ignored | 4 | Regenerable promo renders |

### Clean-install vs this machine

A fresh clone **does not** need this host’s `settings.json`, DBs, certs, `_personal/`, or `temp/`.

A fresh clone **does** need: venv + requirements, `src/.env` from example, optional submodule + Electron `npm install`. Runtime mints settings, certs, HMAC, SQLite.

**Reproducibility (not cleanliness):** README Flask-alone still names `start_api_server.py` (:5000). Canonical Flask-alone is `python src/api/web_chat_api.py` from repo root (daemon does this). `start_web_chat.py` is **not** a working alternative.

**Clean-install run:** still **not executed** in a throwaway clone this pass — boot claim for daemon path is from code + README steps, not an observed empty-dir install.

## Files potentially missing from version control

None at this working tree (`git ls-files --others --exclude-standard` empty). New JSON templates can still be accidentally gitignored (`*.json` + allowlist) — that is a **future** miss risk, not a current untracked file.

## Files potentially in version control unnecessarily

| Path | Why suspected | Confidence |
|---|---|---|
| `src/web/landing_page_backup.html` | No route | H unused; keep until product confirms |
| `src/scripts/launchers/*` (4 files) | Broken cwd/imports | H broken as launchers; may still be referenced in old docs |
| Duplicate `bot_config.json` copies | cwd-relative load | M which copy is canonical depends on cwd |
| Graph leftover **bodies** after `return` in `web_chat_api.py` | Unreachable, still tracked inside one file | H dead statements, not extra files |

## Local dependencies that could break a clean install

| Item | Kind |
|---|---|
| Following README Flask-alone | **Docs** start wrong process |
| `src/.env` | Must copy example (documented) |
| Govee MCP | Optional submodule |
| Electron sandbox helper on Ubuntu | Documented chmod |
| `settings.json` | Auto-created; **not** a silent missing file |

## Candidates for archival or removal (investigation only)

See audit A/B/D. No deletions in this pass.

## Recommended `.gitignore` corrections (do not apply here)

Comment the `*.json` allowlist; do not track `settings.json` to “help clones.”

---

## Tracked files (one row per path)

Columns: **class** · **owner** · **purpose** · **review**. Git status for every row: **tracked**.

| Path | Class | Owner | Purpose | Review |
|---|---|---|---|---|
| `.claude/agents/code-reviewer.md` | 1 | claude-ide | Markdown (code-reviewer.md) | inventoried |
| `.claude/agents/researcher.md` | 1 | claude-ide | Markdown (researcher.md) | inventoried |
| `.cursor/hooks/block_cuttle_managed_kill.py` | 1 | cursor-ide | Python module (hooks/block_cuttle_managed_kill.py) | structure |
| `.cursor/mcp.json` | 1 | cursor-ide | JSON config/fixture (mcp.json) | inventoried |
| `.cursor/rules/comfyui-trellis2.mdc` | 1 | cursor-ide | Cursor rule | inventoried |
| `.cursor/rules/cuttle-electron-debug.mdc` | 1 | cursor-ide | Cursor rule | inventoried |
| `.cursor/rules/multi-project-awareness.mdc` | 1 | cursor-ide | Cursor rule | inventoried |
| `.cursor/rules/project-guidelines.mdc` | 1 | cursor-ide | Cursor rule | inventoried |
| `.cursor/rules/restart-cuttle.mdc` | 1 | cursor-ide | Cursor rule | inventoried |
| `.cursor/skills/comfyui-trellis2/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cursor/skills/cuttle-cleanup-sessions/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cursor/skills/cuttle-electron-debug/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cursor/skills/cuttle-project-commands/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cursor/skills/cuttle-roadmap/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cursor/skills/govee/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cursor/skills/local-plan-act-ollama/SKILL.md` | 1 | cursor-ide | Agent skill | inventoried |
| `.cuttle/README.md` | 1 | hub-config | Readme (.cuttle) | inventoried |
| `.cuttle/actions/.gitkeep` | 1 | hub-actions | Tracked file (.gitkeep) | inventoried |
| `.cuttle/actions/comfyui-start.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/electron-host-restart.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/flask-health.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/flask-restart.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/git-push.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/opencode-sync-auth.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/sessions-cleanup-idle.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-blender-shard.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-cancel.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-list.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-plan.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-self-update.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-shell.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-status.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-submit.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/actions/workers-wait.yaml` | 1 | hub-actions | Hub action form | inventoried |
| `.cuttle/agents/.gitkeep` | 1 | hub-config | Tracked file (.gitkeep) | inventoried |
| `.cuttle/commands/README.md` | 1 | hub-commands | Hub slash command | inventoried |
| `.cuttle/commands/cleanup-sessions.md` | 1 | hub-commands | Hub slash command | inventoried |
| `.cuttle/commands/generate-3d.md` | 1 | hub-commands | Hub slash command | inventoried |
| `.cuttle/commands/scaffold-cuttle.md` | 1 | hub-commands | Hub slash command | inventoried |
| `.cuttle/docs/.gitkeep` | 1 | hub-docs | Tracked file (.gitkeep) | inventoried |
| `.cuttle/docs/action-forms.md` | 1 | hub-docs | Markdown (action-forms.md) | inventoried |
| `.cuttle/docs/agent-context.md` | 1 | hub-docs | Markdown (agent-context.md) | inventoried |
| `.cuttle/docs/agent-ops-cli.md` | 1 | hub-docs | Markdown (agent-ops-cli.md) | inventoried |
| `.cuttle/docs/agent-router-todo.md` | 1 | hub-docs | Markdown (agent-router-todo.md) | inventoried |
| `.cuttle/docs/charts.md` | 1 | hub-docs | Markdown (charts.md) | inventoried |
| `.cuttle/docs/chat-history.md` | 1 | hub-docs | Markdown (chat-history.md) | inventoried |
| `.cuttle/docs/chat-media.md` | 1 | hub-docs | Markdown (chat-media.md) | inventoried |
| `.cuttle/docs/comfyui-trellis2.md` | 1 | hub-docs | Markdown (comfyui-trellis2.md) | inventoried |
| `.cuttle/docs/commands-and-actions.md` | 1 | hub-docs | Markdown (commands-and-actions.md) | inventoried |
| `.cuttle/docs/cursor-plan-bridge.md` | 1 | hub-docs | Markdown (cursor-plan-bridge.md) | inventoried |
| `.cuttle/docs/cuttle-jobs.md` | 1 | hub-docs | Markdown (cuttle-jobs.md) | inventoried |
| `.cuttle/docs/cuttle-workers.md` | 1 | hub-docs | Markdown (cuttle-workers.md) | inventoried |
| `.cuttle/docs/dashboards.md` | 1 | hub-docs | Markdown (dashboards.md) | inventoried |
| `.cuttle/docs/discord.md` | 1 | hub-docs | Markdown (discord.md) | inventoried |
| `.cuttle/docs/git.md` | 1 | hub-docs | Markdown (git.md) | inventoried |
| `.cuttle/docs/gitea.md` | 1 | hub-docs | Markdown (gitea.md) | inventoried |
| `.cuttle/docs/headless-turns.md` | 1 | hub-docs | Markdown (headless-turns.md) | inventoried |
| `.cuttle/docs/jev.md` | 1 | hub-docs | Markdown (jev.md) | inventoried |
| `.cuttle/docs/subagents.md` | 1 | hub-docs | Markdown (subagents.md) | inventoried |
| `.cuttle/docs/widgets.md` | 1 | hub-docs | Markdown (widgets.md) | inventoried |
| `.cuttle/keys/.gitignore` | 1 | hub-config | Git ignore/attributes | inventoried |
| `.cuttle/keys/cuttle_mesh_lan.pub` | 1 | hub-config | Tracked file (cuttle_mesh_lan.pub) | inventoried |
| `.cuttle/learnings/ERRORS.md` | 1 | hub-config | Markdown (ERRORS.md) | inventoried |
| `.cuttle/learnings/FEATURE_REQUESTS.md` | 1 | hub-config | Markdown (FEATURE_REQUESTS.md) | inventoried |
| `.cuttle/learnings/LEARNINGS.md` | 1 | hub-config | Markdown (LEARNINGS.md) | inventoried |
| `.cuttle/learnings/README.md` | 1 | hub-config | Readme (learnings) | inventoried |
| `.cuttle/memory/.gitkeep` | 1 | hub-config | Tracked file (.gitkeep) | inventoried |
| `.cuttle/personal/README.md` | 1 | hub-config | Readme (personal) | inventoried |
| `.cuttle/rules/00-core.md` | 1 | hub-rules | Markdown (00-core.md) | inventoried |
| `.cuttle/rules/01-chat-handles.md` | 1 | hub-rules | Markdown (01-chat-handles.md) | inventoried |
| `.cuttle/rules/02-agent-ops-cli.md` | 1 | hub-rules | Markdown (02-agent-ops-cli.md) | inventoried |
| `.cuttle/rules/03-subagents.md` | 1 | hub-rules | Markdown (03-subagents.md) | inventoried |
| `.cuttle/scripts/.gitkeep` | 1 | hub-scripts | Tracked file (.gitkeep) | inventoried |
| `.cuttle/scripts/bump-cuttle-version.ps1` | 1 | hub-scripts | Launcher/script (bump-cuttle-version.ps1) | inventoried |
| `.cuttle/scripts/cleanup-idle-sessions.ps1` | 1 | hub-scripts | Launcher/script (cleanup-idle-sessions.ps1) | inventoried |
| `.cuttle/scripts/cleanup-idle-sessions.py` | 1 | hub-scripts | Python module (scripts/cleanup-idle-sessions.py) | structure |
| `.cuttle/scripts/client-self-update.ps1` | 1 | hub-scripts | Launcher/script (client-self-update.ps1) | inventoried |
| `.cuttle/scripts/client-self-update.sh` | 1 | hub-scripts | Launcher/script (client-self-update.sh) | inventoried |
| `.cuttle/scripts/comfyui-start.ps1` | 1 | hub-scripts | Launcher/script (comfyui-start.ps1) | inventoried |
| `.cuttle/scripts/comfyui-start.sh` | 1 | hub-scripts | Launcher/script (comfyui-start.sh) | inventoried |
| `.cuttle/scripts/download-trellis2-models.ps1` | 1 | hub-scripts | Launcher/script (download-trellis2-models.ps1) | inventoried |
| `.cuttle/scripts/electron-sandbox.sh` | 1 | hub-scripts | Launcher/script (electron-sandbox.sh) | inventoried |
| `.cuttle/scripts/flask-health.ps1` | 1 | hub-scripts | Launcher/script (flask-health.ps1) | inventoried |
| `.cuttle/scripts/flask-health.py` | 1 | hub-scripts | Python module (scripts/flask-health.py) | structure |
| `.cuttle/scripts/git-push.ps1` | 1 | hub-scripts | Launcher/script (git-push.ps1) | inventoried |
| `.cuttle/scripts/git-push.py` | 1 | hub-scripts | Python module (scripts/git-push.py) | structure |
| `.cuttle/scripts/gitea_cli.py` | 1 | hub-scripts | Python module (scripts/gitea_cli.py) | structure |
| `.cuttle/scripts/host-electron-restart.ps1` | 1 | hub-scripts | Launcher/script (host-electron-restart.ps1) | inventoried |
| `.cuttle/scripts/host-electron-restart.sh` | 1 | hub-scripts | Launcher/script (host-electron-restart.sh) | inventoried |
| `.cuttle/scripts/install-comfyui-trellis-py311.ps1` | 1 | hub-scripts | Launcher/script (install-comfyui-trellis-py311.ps1) | inventoried |
| `.cuttle/scripts/install-comfyui-trellis2.ps1` | 1 | hub-scripts | Launcher/script (install-comfyui-trellis2.ps1) | inventoried |
| `.cuttle/scripts/install-cuttle-mesh-lan-key.ps1` | 1 | hub-scripts | Launcher/script (install-cuttle-mesh-lan-key.ps1) | inventoried |
| `.cuttle/scripts/launch-cuttle-client.sh` | 1 | hub-scripts | Launcher/script (launch-cuttle-client.sh) | inventoried |
| `.cuttle/scripts/launch-cuttle-host.sh` | 1 | hub-scripts | Launcher/script (launch-cuttle-host.sh) | inventoried |
| `.cuttle/scripts/muse.cmd` | 1 | hub-scripts | Tracked file (muse.cmd) | inventoried |
| `.cuttle/scripts/opencode_sync_auth.py` | 1 | hub-scripts | Python module (scripts/opencode_sync_auth.py) | structure |
| `.cuttle/scripts/playwright-mcp.cmd` | 1 | hub-scripts | Tracked file (playwright-mcp.cmd) | inventoried |
| `.cuttle/scripts/publish-trellis-download-status.ps1` | 1 | hub-scripts | Launcher/script (publish-trellis-download-status.ps1) | inventoried |
| `.cuttle/scripts/restart-daemon.ps1` | 1 | hub-scripts | Launcher/script (restart-daemon.ps1) | inventoried |
| `.cuttle/scripts/restart-daemon.sh` | 1 | hub-scripts | Launcher/script (restart-daemon.sh) | inventoried |
| `.cuttle/scripts/restart-flask-worker.ps1` | 1 | hub-scripts | Launcher/script (restart-flask-worker.ps1) | inventoried |
| `.cuttle/scripts/restart-flask.ps1` | 1 | hub-scripts | Launcher/script (restart-flask.ps1) | inventoried |
| `.cuttle/scripts/restart-flask.py` | 1 | hub-scripts | Python module (scripts/restart-flask.py) | structure |
| `.cuttle/scripts/setup-tailscale-ssh-verify.ps1` | 1 | hub-scripts | Launcher/script (setup-tailscale-ssh-verify.ps1) | inventoried |
| `.cuttle/scripts/setup-tailscale-ssh.ps1` | 1 | hub-scripts | Launcher/script (setup-tailscale-ssh.ps1) | inventoried |
| `.cuttle/scripts/start-cuttle.cmd` | 1 | hub-scripts | Tracked file (start-cuttle.cmd) | inventoried |
| `.cuttle/scripts/trellis-download-status.ps1` | 1 | hub-scripts | Launcher/script (trellis-download-status.ps1) | inventoried |
| `.cuttle/scripts/workers-cli.ps1` | 1 | hub-scripts | Launcher/script (workers-cli.ps1) | inventoried |
| `.cuttle/scripts/workers-cli.sh` | 1 | hub-scripts | Launcher/script (workers-cli.sh) | inventoried |
| `.gitattributes` | 1 | repo-root | Git ignore/attributes | inventoried |
| `.gitignore` | 1 | repo-root | Git ignore/attributes | inventoried |
| `.gitmodules` | 1 | repo-root | Tracked file (.gitmodules) | inventoried |
| `AGENTS.md` | 1 | repo-root | Multi-agent brief; dispatch sentence stale | inventoried |
| `LICENSE.txt` | 1 | repo-root | Tracked file (LICENSE.txt) | inventoried |
| `README.md` | 1 | repo-root | Install/run docs (Flask-alone line currently wrong) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/checksums/checksums.lock` | 1 | android-bt-voice | Tracked file (checksums.lock) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/checksums/md5-checksums.bin` | 1 | android-bt-voice | Tracked file (md5-checksums.bin) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/checksums/sha1-checksums.bin` | 1 | android-bt-voice | Tracked file (sha1-checksums.bin) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/dependencies-accessors/dependencies-accessors.lock` | 1 | android-bt-voice | Tracked file (dependencies-accessors.lock) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/dependencies-accessors/gc.properties` | 1 | android-bt-voice | Android build/source (gc.properties) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/fileChanges/last-build.bin` | 1 | android-bt-voice | Tracked file (last-build.bin) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/fileHashes/fileHashes.lock` | 1 | android-bt-voice | Tracked file (fileHashes.lock) | inventoried |
| `apps/android_bt_voice/.gradle/7.5/gc.properties` | 1 | android-bt-voice | Android build/source (gc.properties) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/checksums/checksums.lock` | 1 | android-bt-voice | Tracked file (checksums.lock) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/checksums/md5-checksums.bin` | 1 | android-bt-voice | Tracked file (md5-checksums.bin) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/checksums/sha1-checksums.bin` | 1 | android-bt-voice | Tracked file (sha1-checksums.bin) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/dependencies-accessors/dependencies-accessors.lock` | 1 | android-bt-voice | Tracked file (dependencies-accessors.lock) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/dependencies-accessors/gc.properties` | 1 | android-bt-voice | Android build/source (gc.properties) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/fileChanges/last-build.bin` | 1 | android-bt-voice | Tracked file (last-build.bin) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/fileHashes/fileHashes.lock` | 1 | android-bt-voice | Tracked file (fileHashes.lock) | inventoried |
| `apps/android_bt_voice/.gradle/8.2/gc.properties` | 1 | android-bt-voice | Android build/source (gc.properties) | inventoried |
| `apps/android_bt_voice/.gradle/buildOutputCleanup/buildOutputCleanup.lock` | 1 | android-bt-voice | Tracked file (buildOutputCleanup.lock) | inventoried |
| `apps/android_bt_voice/.gradle/buildOutputCleanup/cache.properties` | 1 | android-bt-voice | Android build/source (cache.properties) | inventoried |
| `apps/android_bt_voice/.gradle/buildOutputCleanup/outputFiles.bin` | 1 | android-bt-voice | Tracked file (outputFiles.bin) | inventoried |
| `apps/android_bt_voice/.gradle/vcs-1/gc.properties` | 1 | android-bt-voice | Android build/source (gc.properties) | inventoried |
| `apps/android_bt_voice/README.md` | 1 | android-bt-voice | Readme (android_bt_voice) | inventoried |
| `apps/android_bt_voice/app/build.gradle.kts` | 1 | android-bt-voice | Tracked file (build.gradle.kts) | inventoried |
| `apps/android_bt_voice/app/proguard-rules.pro` | 1 | android-bt-voice | Android build/source (proguard-rules.pro) | inventoried |
| `apps/android_bt_voice/app/src/main/AndroidManifest.xml` | 1 | android-bt-voice | Android build/source (AndroidManifest.xml) | inventoried |
| `apps/android_bt_voice/app/src/main/java/com/cuttle/androidbtvoice/MainActivity.kt` | 1 | android-bt-voice | Android build/source (MainActivity.kt) | inventoried |
| `apps/android_bt_voice/app/src/main/res/drawable/ic_launcher.xml` | 1 | android-bt-voice | Android build/source (ic_launcher.xml) | inventoried |
| `apps/android_bt_voice/app/src/main/res/layout/activity_main.xml` | 1 | android-bt-voice | Android build/source (activity_main.xml) | inventoried |
| `apps/android_bt_voice/app/src/main/res/values/colors.xml` | 1 | android-bt-voice | Android build/source (colors.xml) | inventoried |
| `apps/android_bt_voice/app/src/main/res/values/strings.xml` | 1 | android-bt-voice | Android build/source (strings.xml) | inventoried |
| `apps/android_bt_voice/app/src/main/res/values/themes.xml` | 1 | android-bt-voice | Android build/source (themes.xml) | inventoried |
| `apps/android_bt_voice/build.gradle.kts` | 1 | android-bt-voice | Tracked file (build.gradle.kts) | inventoried |
| `apps/android_bt_voice/gradle.properties` | 1 | android-bt-voice | Android build/source (gradle.properties) | inventoried |
| `apps/android_bt_voice/gradle/wrapper/gradle-wrapper.jar` | 1 | android-bt-voice | Tracked file (gradle-wrapper.jar) | inventoried |
| `apps/android_bt_voice/gradle/wrapper/gradle-wrapper.properties` | 1 | android-bt-voice | Android build/source (gradle-wrapper.properties) | inventoried |
| `apps/android_bt_voice/gradlew` | 1 | android-bt-voice | Tracked file (gradlew) | inventoried |
| `apps/android_bt_voice/gradlew.bat` | 1 | android-bt-voice | Launcher/script (gradlew.bat) | inventoried |
| `apps/android_bt_voice/settings.gradle.kts` | 1 | android-bt-voice | Tracked file (settings.gradle.kts) | inventoried |
| `apps/android_companion/README.md` | 1 | android-companion | Readme (android_companion) | inventoried |
| `apps/android_companion/app/build.gradle.kts` | 1 | android-companion | Tracked file (build.gradle.kts) | inventoried |
| `apps/android_companion/app/proguard-rules.pro` | 1 | android-companion | Android build/source (proguard-rules.pro) | inventoried |
| `apps/android_companion/app/src/main/AndroidManifest.xml` | 1 | android-companion | Android build/source (AndroidManifest.xml) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/core/Prefs.kt` | 1 | android-companion | Android build/source (Prefs.kt) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/notify/ActionReceiver.kt` | 1 | android-companion | Android build/source (ActionReceiver.kt) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/notify/Notifier.kt` | 1 | android-companion | Android build/source (Notifier.kt) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/sse/CuttleApi.kt` | 1 | android-companion | Android build/source (CuttleApi.kt) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/sse/EventStreamService.kt` | 1 | android-companion | Android build/source (EventStreamService.kt) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/sse/Notifications.kt` | 1 | android-companion | Android build/source (Notifications.kt) | inventoried |
| `apps/android_companion/app/src/main/java/com/cuttle/companion/ui/MainActivity.kt` | 1 | android-companion | Android build/source (MainActivity.kt) | inventoried |
| `apps/android_companion/app/src/main/res/layout/activity_main.xml` | 1 | android-companion | Android build/source (activity_main.xml) | inventoried |
| `apps/android_companion/app/src/main/res/values/strings.xml` | 1 | android-companion | Android build/source (strings.xml) | inventoried |
| `apps/android_companion/app/src/main/res/values/themes.xml` | 1 | android-companion | Android build/source (themes.xml) | inventoried |
| `apps/android_companion/build.gradle.kts` | 1 | android-companion | Tracked file (build.gradle.kts) | inventoried |
| `apps/android_companion/settings.gradle.kts` | 1 | android-companion | Tracked file (settings.gradle.kts) | inventoried |
| `apps/mobile/.gitignore` | 1 | android-mobile | Git ignore/attributes | inventoried |
| `apps/mobile/README.md` | 1 | android-mobile | Readme (mobile) | inventoried |
| `apps/mobile/android/.gitignore` | 1 | android-mobile | Git ignore/attributes | inventoried |
| `apps/mobile/android/app/.gitignore` | 1 | android-mobile | Git ignore/attributes | inventoried |
| `apps/mobile/android/app/build.gradle` | 1 | android-mobile | Android build/source (build.gradle) | inventoried |
| `apps/mobile/android/app/capacitor.build.gradle` | 1 | android-mobile | Android build/source (capacitor.build.gradle) | inventoried |
| `apps/mobile/android/app/proguard-rules.pro` | 1 | android-mobile | Android build/source (proguard-rules.pro) | inventoried |
| `apps/mobile/android/app/src/androidTest/java/com/getcapacitor/myapp/ExampleInstrumentedTest.java` | 1 | android-mobile | Android build/source (ExampleInstrumentedTest.java) | inventoried |
| `apps/mobile/android/app/src/main/AndroidManifest.xml` | 1 | android-mobile | Android build/source (AndroidManifest.xml) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/MainActivity.java` | 1 | android-mobile | Android build/source (MainActivity.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/ActionReceiver.java` | 1 | android-mobile | Android build/source (ActionReceiver.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/BootReceiver.java` | 1 | android-mobile | Android build/source (BootReceiver.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/CuttleApi.java` | 1 | android-mobile | Android build/source (CuttleApi.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/CuttleNotifications.java` | 1 | android-mobile | Android build/source (CuttleNotifications.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/EventStreamService.java` | 1 | android-mobile | Android build/source (EventStreamService.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/NetworkUtil.java` | 1 | android-mobile | Android build/source (NetworkUtil.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/Notifier.java` | 1 | android-mobile | Android build/source (Notifier.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/NotifyController.java` | 1 | android-mobile | Android build/source (NotifyController.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/NotifyPrefs.java` | 1 | android-mobile | Android build/source (NotifyPrefs.java) | inventoried |
| `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/ShellUpdate.java` | 1 | android-mobile | Android build/source (ShellUpdate.java) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-land-hdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-land-mdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-land-xhdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-land-xxhdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-land-xxxhdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-port-hdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-port-mdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-port-xhdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-port-xxhdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-port-xxxhdpi/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable-v24/ic_launcher_foreground.xml` | 1 | android-mobile | Android build/source (ic_launcher_foreground.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable/ic_launcher_background.xml` | 1 | android-mobile | Android build/source (ic_launcher_background.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/drawable/splash.png` | 1 | android-mobile | Asset (splash.png) | inventoried |
| `apps/mobile/android/app/src/main/res/layout/activity_main.xml` | 1 | android-mobile | Android build/source (activity_main.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml` | 1 | android-mobile | Android build/source (ic_launcher.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml` | 1 | android-mobile | Android build/source (ic_launcher_round.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher.png` | 1 | android-mobile | Asset (ic_launcher.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher_foreground.png` | 1 | android-mobile | Asset (ic_launcher_foreground.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher_round.png` | 1 | android-mobile | Asset (ic_launcher_round.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher.png` | 1 | android-mobile | Asset (ic_launcher.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher_foreground.png` | 1 | android-mobile | Asset (ic_launcher_foreground.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher_round.png` | 1 | android-mobile | Asset (ic_launcher_round.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher.png` | 1 | android-mobile | Asset (ic_launcher.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher_foreground.png` | 1 | android-mobile | Asset (ic_launcher_foreground.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher_round.png` | 1 | android-mobile | Asset (ic_launcher_round.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher.png` | 1 | android-mobile | Asset (ic_launcher.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher_foreground.png` | 1 | android-mobile | Asset (ic_launcher_foreground.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher_round.png` | 1 | android-mobile | Asset (ic_launcher_round.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher.png` | 1 | android-mobile | Asset (ic_launcher.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_foreground.png` | 1 | android-mobile | Asset (ic_launcher_foreground.png) | inventoried |
| `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_round.png` | 1 | android-mobile | Asset (ic_launcher_round.png) | inventoried |
| `apps/mobile/android/app/src/main/res/values/ic_launcher_background.xml` | 1 | android-mobile | Android build/source (ic_launcher_background.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/values/strings.xml` | 1 | android-mobile | Android build/source (strings.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/values/styles.xml` | 1 | android-mobile | Android build/source (styles.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/xml/file_paths.xml` | 1 | android-mobile | Android build/source (file_paths.xml) | inventoried |
| `apps/mobile/android/app/src/main/res/xml/network_security_config.xml` | 1 | android-mobile | Android build/source (network_security_config.xml) | inventoried |
| `apps/mobile/android/app/src/test/java/com/getcapacitor/myapp/ExampleUnitTest.java` | 1 | android-mobile | Android build/source (ExampleUnitTest.java) | inventoried |
| `apps/mobile/android/build.gradle` | 1 | android-mobile | Android build/source (build.gradle) | inventoried |
| `apps/mobile/android/capacitor.settings.gradle` | 1 | android-mobile | Android build/source (capacitor.settings.gradle) | inventoried |
| `apps/mobile/android/gradle.properties` | 1 | android-mobile | Android build/source (gradle.properties) | inventoried |
| `apps/mobile/android/gradle/wrapper/gradle-wrapper.jar` | 1 | android-mobile | Tracked file (gradle-wrapper.jar) | inventoried |
| `apps/mobile/android/gradle/wrapper/gradle-wrapper.properties` | 1 | android-mobile | Android build/source (gradle-wrapper.properties) | inventoried |
| `apps/mobile/android/gradlew` | 1 | android-mobile | Tracked file (gradlew) | inventoried |
| `apps/mobile/android/gradlew.bat` | 1 | android-mobile | Launcher/script (gradlew.bat) | inventoried |
| `apps/mobile/android/local.properties.example` | 1 | android-mobile | Tracked file (local.properties.example) | inventoried |
| `apps/mobile/android/settings.gradle` | 1 | android-mobile | Android build/source (settings.gradle) | inventoried |
| `apps/mobile/android/variables.gradle` | 1 | android-mobile | Android build/source (variables.gradle) | inventoried |
| `apps/mobile/assets/icon/chat-avatar.png` | 1 | android-mobile | Asset (chat-avatar.png) | inventoried |
| `apps/mobile/assets/icon/foreground-432.png` | 1 | android-mobile | Asset (foreground-432.png) | inventoried |
| `apps/mobile/assets/icon/icon-1024.png` | 1 | android-mobile | Asset (icon-1024.png) | inventoried |
| `apps/mobile/assets/icon/mascot.png` | 1 | android-mobile | Asset (mascot.png) | inventoried |
| `apps/mobile/build-android-debug.bat` | 1 | android-mobile | Launcher/script (build-android-debug.bat) | inventoried |
| `apps/mobile/capacitor.config.ts` | 1 | android-mobile | Tracked file (capacitor.config.ts) | inventoried |
| `apps/mobile/index.html` | 1 | android-mobile | HTML page (index.html) | inventoried |
| `apps/mobile/ios/.gitignore` | 1 | android-mobile | Git ignore/attributes | inventoried |
| `apps/mobile/ios/App/App.xcodeproj/project.pbxproj` | 1 | android-mobile | Tracked file (project.pbxproj) | inventoried |
| `apps/mobile/ios/App/App.xcworkspace/xcshareddata/IDEWorkspaceChecks.plist` | 1 | android-mobile | Tracked file (IDEWorkspaceChecks.plist) | inventoried |
| `apps/mobile/ios/App/App/AppDelegate.swift` | 1 | android-mobile | Tracked file (AppDelegate.swift) | inventoried |
| `apps/mobile/ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png` | 1 | android-mobile | Asset (AppIcon-512@2x.png) | inventoried |
| `apps/mobile/ios/App/App/Assets.xcassets/Splash.imageset/splash-2732x2732-1.png` | 1 | android-mobile | Asset (splash-2732x2732-1.png) | inventoried |
| `apps/mobile/ios/App/App/Assets.xcassets/Splash.imageset/splash-2732x2732-2.png` | 1 | android-mobile | Asset (splash-2732x2732-2.png) | inventoried |
| `apps/mobile/ios/App/App/Assets.xcassets/Splash.imageset/splash-2732x2732.png` | 1 | android-mobile | Asset (splash-2732x2732.png) | inventoried |
| `apps/mobile/ios/App/App/Base.lproj/LaunchScreen.storyboard` | 1 | android-mobile | Tracked file (LaunchScreen.storyboard) | inventoried |
| `apps/mobile/ios/App/App/Base.lproj/Main.storyboard` | 1 | android-mobile | Tracked file (Main.storyboard) | inventoried |
| `apps/mobile/ios/App/App/CuttleApi.swift` | 1 | android-mobile | Tracked file (CuttleApi.swift) | inventoried |
| `apps/mobile/ios/App/App/CuttleEventStream.swift` | 1 | android-mobile | Tracked file (CuttleEventStream.swift) | inventoried |
| `apps/mobile/ios/App/App/CuttleNotifications.swift` | 1 | android-mobile | Tracked file (CuttleNotifications.swift) | inventoried |
| `apps/mobile/ios/App/App/CuttleNotifyPrefs.swift` | 1 | android-mobile | Tracked file (CuttleNotifyPrefs.swift) | inventoried |
| `apps/mobile/ios/App/App/Info.plist` | 1 | android-mobile | Tracked file (Info.plist) | inventoried |
| `apps/mobile/ios/App/Podfile` | 1 | android-mobile | Tracked file (Podfile) | inventoried |
| `apps/mobile/scripts/generate_icons.py` | 1 | android-mobile | Python module (scripts/generate_icons.py) | structure |
| `apps/mobile/src/main.js` | 1 | android-mobile | Browser JS (main.js) | structure |
| `apps/mobile/src/setup.css` | 1 | android-mobile | Stylesheet (setup.css) | inventoried |
| `apps/mobile/vite.config.js` | 1 | android-mobile | Browser JS (vite.config.js) | structure |
| `bot_config.json` | 1 | repo-root | Repo-root BotConfig copy (cwd-relative load) | inventoried |
| `build_electron.bat` | 1 | repo-root | Launcher/script (build_electron.bat) | inventoried |
| `docs/README.md` | 1 | docs | Readme (docs) | inventoried |
| `docs/ROADMAP.md` | 1 | docs | Markdown (ROADMAP.md) | inventoried |
| `docs/ROUTER_RESEARCH_NOTES.md` | 1 | docs | Markdown (ROUTER_RESEARCH_NOTES.md) | inventoried |
| `docs/architecture/repository-map.md` | 1 | docs | Markdown (repository-map.md) | inventoried |
| `docs/guides/AGENT_ROUTER.md` | 1 | docs | Markdown (AGENT_ROUTER.md) | inventoried |
| `docs/guides/CUTTLE_WORKERS.md` | 1 | docs | Markdown (CUTTLE_WORKERS.md) | inventoried |
| `docs/guides/MODULARITY.md` | 1 | docs | Markdown (MODULARITY.md) | inventoried |
| `docs/guides/PAIRING_AND_ALLOWLIST.md` | 1 | docs | Markdown (PAIRING_AND_ALLOWLIST.md) | inventoried |
| `docs/guides/REMOTE_ACCESS.md` | 1 | docs | Markdown (REMOTE_ACCESS.md) | inventoried |
| `docs/guides/SESSIONS_API.md` | 1 | docs | Markdown (SESSIONS_API.md) | inventoried |
| `docs/guides/SUPERVISED_COORDINATOR.md` | 1 | docs | Markdown (SUPERVISED_COORDINATOR.md) | inventoried |
| `docs/guides/WEB_CHAT_API.md` | 1 | docs | Markdown (WEB_CHAT_API.md) | inventoried |
| `docs/media/chat-charts.webp` | 1 | docs | Asset (chat-charts.webp) | inventoried |
| `docs/media/chat-hero.webp` | 1 | docs | Asset (chat-hero.webp) | inventoried |
| `docs/media/customize.webp` | 1 | docs | Asset (customize.webp) | inventoried |
| `docs/media/dashboards.webp` | 1 | docs | Asset (dashboards.webp) | inventoried |
| `docs/media/hero.webp` | 1 | docs | Asset (hero.webp) | inventoried |
| `docs/media/multiplex.webp` | 1 | docs | Asset (multiplex.webp) | inventoried |
| `docs/reviews/cleanup-plan.md` | 1 | docs | Markdown (cleanup-plan.md) | inventoried |
| `docs/reviews/repository-audit.md` | 1 | docs | Markdown (repository-audit.md) | inventoried |
| `docs/reviews/repository-inventory.md` | 1 | docs | Markdown (repository-inventory.md) | inventoried |
| `docs/reviews/security-hardening-2026-09.md` | 1 | docs | Markdown (security-hardening-2026-09.md) | inventoried |
| `electron/.gitignore` | 1 | electron | Git ignore/attributes | inventoried |
| `electron/.npmrc` | 1 | electron | Tracked file (.npmrc) | inventoried |
| `electron/BUILD_TROUBLESHOOTING.md` | 1 | electron | Markdown (BUILD_TROUBLESHOOTING.md) | inventoried |
| `electron/ELECTRON_LAUNCH_GUIDE.md` | 1 | electron | Markdown (ELECTRON_LAUNCH_GUIDE.md) | inventoried |
| `electron/QUICK_START.txt` | 1 | electron | Tracked file (QUICK_START.txt) | inventoried |
| `electron/README.md` | 1 | electron | Readme (electron) | inventoried |
| `electron/assets/completion-chirp.wav` | 1 | electron | Tracked file (completion-chirp.wav) | inventoried |
| `electron/bot_config.json` | 1 | electron | Electron-packaged BotConfig copy | inventoried |
| `electron/connect.html` | 1 | electron | HTML page (connect.html) | inventoried |
| `electron/device-worker/cuttle_device_worker.py` | 1 | electron | Python module (device-worker/cuttle_device_worker.py) | structure |
| `electron/main.js` | 1 | electron | Browser JS (main.js) | execution |
| `electron/pack-desktop-update.js` | 1 | electron | Browser JS (pack-desktop-update.js) | structure |
| `electron/pack_asar.js` | 1 | electron | Browser JS (pack_asar.js) | structure |
| `electron/package-lock.json` | 1 | electron | JSON config/fixture (package-lock.json) | inventoried |
| `electron/package.json` | 1 | electron | JSON config/fixture (package.json) | inventoried |
| `electron/preload.js` | 1 | electron | Browser JS (preload.js) | structure |
| `pytest.ini` | 1 | repo-root | Tracked file (pytest.ini) | inventoried |
| `src/.env.example` | 1 | src-other | Tracked file (.env.example) | inventoried |
| `src/api/__init__.py` | 1 | flask-api | Package init (api) | structure |
| `src/api/action_forms.py` | 1 | flask-api | Python module (api/action_forms.py) | structure |
| `src/api/active_executions.py` | 1 | flask-api | Live job registry used by agent_harness.kernel (not graph-only) | execution |
| `src/api/agent_context.py` | 1 | flask-api | Python module (api/agent_context.py) | structure |
| `src/api/agent_cost.py` | 1 | flask-api | Python module (api/agent_cost.py) | structure |
| `src/api/agent_harness/ADDING_AN_AGENT.md` | 1 | agent-harness | Markdown (ADDING_AN_AGENT.md) | inventoried |
| `src/api/agent_harness/__init__.py` | 1 | agent-harness | Package init (agent_harness) | structure |
| `src/api/agent_harness/activity.py` | 1 | agent-harness | Python module (agent_harness/activity.py) | structure |
| `src/api/agent_harness/agent_defaults.py` | 1 | agent-harness | Python module (agent_harness/agent_defaults.py) | structure |
| `src/api/agent_harness/agents/__init__.py` | 1 | agent-harness | Package init (agents) | structure |
| `src/api/agent_harness/agents/antigravity/__init__.py` | 1 | agent-harness | Package init (antigravity) | structure |
| `src/api/agent_harness/agents/antigravity/adapter.py` | 1 | agent-harness | Python module (antigravity/adapter.py) | structure |
| `src/api/agent_harness/agents/antigravity/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/antigravity/session_store.py` | 1 | agent-harness | Python module (antigravity/session_store.py) | structure |
| `src/api/agent_harness/agents/claude/__init__.py` | 1 | agent-harness | Package init (claude) | structure |
| `src/api/agent_harness/agents/claude/adapter.py` | 1 | agent-harness | Python module (claude/adapter.py) | structure |
| `src/api/agent_harness/agents/claude/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/codex/__init__.py` | 1 | agent-harness | Package init (codex) | structure |
| `src/api/agent_harness/agents/codex/adapter.py` | 1 | agent-harness | Python module (codex/adapter.py) | structure |
| `src/api/agent_harness/agents/codex/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/codex/model_catalog.py` | 1 | agent-harness | Python module (codex/model_catalog.py) | structure |
| `src/api/agent_harness/agents/cursor/__init__.py` | 1 | agent-harness | Package init (cursor) | structure |
| `src/api/agent_harness/agents/cursor/adapter.py` | 1 | agent-harness | Python module (cursor/adapter.py) | structure |
| `src/api/agent_harness/agents/cursor/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/deepseek/__init__.py` | 1 | agent-harness | Package init (deepseek) | structure |
| `src/api/agent_harness/agents/deepseek/adapter.py` | 1 | agent-harness | Python module (deepseek/adapter.py) | structure |
| `src/api/agent_harness/agents/deepseek/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/hermes/__init__.py` | 1 | agent-harness | Package init (hermes) | structure |
| `src/api/agent_harness/agents/hermes/adapter.py` | 1 | agent-harness | Python module (hermes/adapter.py) | structure |
| `src/api/agent_harness/agents/hermes/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/muse/__init__.py` | 1 | agent-harness | Package init (muse) | structure |
| `src/api/agent_harness/agents/muse/adapter.py` | 1 | agent-harness | Python module (muse/adapter.py) | structure |
| `src/api/agent_harness/agents/muse/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/opencode/__init__.py` | 1 | agent-harness | Package init (opencode) | structure |
| `src/api/agent_harness/agents/opencode/adapter.py` | 1 | agent-harness | Python module (opencode/adapter.py) | structure |
| `src/api/agent_harness/agents/opencode/manifest.yaml` | 1 | agent-harness | Tracked file (manifest.yaml) | inventoried |
| `src/api/agent_harness/agents/opencode/model_catalog.py` | 1 | agent-harness | Python module (opencode/model_catalog.py) | structure |
| `src/api/agent_harness/agents/opencode/session_store.py` | 1 | agent-harness | Python module (opencode/session_store.py) | structure |
| `src/api/agent_harness/catalog.py` | 1 | agent-harness | Python module (agent_harness/catalog.py) | structure |
| `src/api/agent_harness/cwd.py` | 1 | agent-harness | Python module (agent_harness/cwd.py) | structure |
| `src/api/agent_harness/installer.py` | 1 | agent-harness | Python module (agent_harness/installer.py) | structure |
| `src/api/agent_harness/kernel.py` | 1 | agent-harness | Python module (agent_harness/kernel.py) | structure |
| `src/api/agent_harness/model_capabilities.py` | 1 | agent-harness | Python module (agent_harness/model_capabilities.py) | structure |
| `src/api/agent_harness/smoke_policy.py` | 1 | agent-harness | Python module (agent_harness/smoke_policy.py) | structure |
| `src/api/agent_harness/steer.py` | 1 | agent-harness | Python module (agent_harness/steer.py) | structure |
| `src/api/agent_harness/timeouts.py` | 1 | agent-harness | Python module (agent_harness/timeouts.py) | structure |
| `src/api/agent_harness/types.py` | 1 | agent-harness | Python module (agent_harness/types.py) | structure |
| `src/api/agent_harness/win_cli.py` | 1 | agent-harness | Python module (agent_harness/win_cli.py) | structure |
| `src/api/agent_router/__init__.py` | 1 | agent-router | Package init (agent_router) | structure |
| `src/api/agent_router/commands.py` | 1 | agent-router | Python module (agent_router/commands.py) | structure |
| `src/api/agent_router/config.py` | 1 | agent-router | Python module (agent_router/config.py) | structure |
| `src/api/agent_router/dispatch.py` | 1 | agent-router | Python module (agent_router/dispatch.py) | structure |
| `src/api/agent_router/drift.py` | 1 | agent-router | Python module (agent_router/drift.py) | structure |
| `src/api/agent_router/engine.py` | 1 | agent-router | Python module (agent_router/engine.py) | structure |
| `src/api/agent_router/eval/__init__.py` | 1 | agent-router | Package init (eval) | structure |
| `src/api/agent_router/eval/report.py` | 1 | agent-router | Python module (eval/report.py) | structure |
| `src/api/agent_router/eval/runner.py` | 1 | agent-router | Python module (eval/runner.py) | structure |
| `src/api/agent_router/eval/scoring.py` | 1 | agent-router | Python module (eval/scoring.py) | structure |
| `src/api/agent_router/eval/stability.py` | 1 | agent-router | Python module (eval/stability.py) | structure |
| `src/api/agent_router/eval/suites.py` | 1 | agent-router | Python module (eval/suites.py) | structure |
| `src/api/agent_router/frustration.py` | 1 | agent-router | Python module (agent_router/frustration.py) | structure |
| `src/api/agent_router/integration.py` | 1 | agent-router | Python module (agent_router/integration.py) | structure |
| `src/api/agent_router/logging_events.py` | 1 | agent-router | Python module (agent_router/logging_events.py) | structure |
| `src/api/agent_router/outcomes.py` | 1 | agent-router | Python module (agent_router/outcomes.py) | structure |
| `src/api/agent_router/pinned_outcomes.py` | 1 | agent-router | Python module (agent_router/pinned_outcomes.py) | structure |
| `src/api/agent_router/policy.py` | 1 | agent-router | Python module (agent_router/policy.py) | structure |
| `src/api/agent_router/providers/__init__.py` | 1 | agent-router | Package init (providers) | structure |
| `src/api/agent_router/providers/agent.py` | 1 | agent-router | Python module (providers/agent.py) | structure |
| `src/api/agent_router/providers/api_openai.py` | 1 | agent-router | Python module (providers/api_openai.py) | structure |
| `src/api/agent_router/providers/base.py` | 1 | agent-router | Python module (providers/base.py) | structure |
| `src/api/agent_router/providers/jev.py` | 1 | agent-router | Python module (providers/jev.py) | structure |
| `src/api/agent_router/providers/local.py` | 1 | agent-router | Python module (providers/local.py) | structure |
| `src/api/agent_router/rage_investigator.py` | 1 | agent-router | Python module (agent_router/rage_investigator.py) | structure |
| `src/api/agent_router/registry.py` | 1 | agent-router | Python module (agent_router/registry.py) | structure |
| `src/api/agent_router/repeats.py` | 1 | agent-router | Python module (agent_router/repeats.py) | structure |
| `src/api/agent_router/suites/baseline.json` | 1 | agent-router | JSON config/fixture (baseline.json) | inventoried |
| `src/api/agent_router/supervised/__init__.py` | 1 | agent-router | Package init (supervised) | structure |
| `src/api/agent_router/supervised/adapters.py` | 1 | agent-router | Python module (supervised/adapters.py) | structure |
| `src/api/agent_router/supervised/bubble.py` | 1 | agent-router | Python module (supervised/bubble.py) | structure |
| `src/api/agent_router/supervised/commands.py` | 1 | agent-router | Python module (supervised/commands.py) | structure |
| `src/api/agent_router/supervised/control.py` | 1 | agent-router | Python module (supervised/control.py) | structure |
| `src/api/agent_router/supervised/delivery.py` | 1 | agent-router | Python module (supervised/delivery.py) | structure |
| `src/api/agent_router/supervised/events.py` | 1 | agent-router | Python module (supervised/events.py) | structure |
| `src/api/agent_router/supervised/evidence.py` | 1 | agent-router | Python module (supervised/evidence.py) | structure |
| `src/api/agent_router/supervised/helpers.py` | 1 | agent-router | Python module (supervised/helpers.py) | structure |
| `src/api/agent_router/supervised/orchestrator.py` | 1 | agent-router | Python module (supervised/orchestrator.py) | structure |
| `src/api/agent_router/supervised/packet.py` | 1 | agent-router | Python module (supervised/packet.py) | structure |
| `src/api/agent_router/supervised/policy_hooks.py` | 1 | agent-router | Python module (supervised/policy_hooks.py) | structure |
| `src/api/agent_router/supervised/profiles.py` | 1 | agent-router | Python module (supervised/profiles.py) | structure |
| `src/api/agent_router/supervised/store.py` | 1 | agent-router | Python module (supervised/store.py) | structure |
| `src/api/agent_router/supervised/test_isolation.py` | 1 | agent-router | Python module (supervised/test_isolation.py) | structure |
| `src/api/agent_router/supervised/types.py` | 1 | agent-router | Python module (supervised/types.py) | structure |
| `src/api/agent_router/supervised/verification.py` | 1 | agent-router | Python module (supervised/verification.py) | structure |
| `src/api/agent_router/types.py` | 1 | agent-router | Python module (agent_router/types.py) | structure |
| `src/api/agent_router/use_cases.py` | 1 | agent-router | Python module (agent_router/use_cases.py) | structure |
| `src/api/agent_usage.py` | 1 | flask-api | Python module (api/agent_usage.py) | structure |
| `src/api/auth_api.py` | 1 | flask-api | Python module (api/auth_api.py) | structure |
| `src/api/auth_db.py` | 1 | flask-api | Python module (api/auth_db.py) | structure |
| `src/api/auth_session.py` | 1 | flask-api | Python module (api/auth_session.py) | structure |
| `src/api/bundled_llm_tools.py` | 1 | flask-api | Python module (api/bundled_llm_tools.py) | structure |
| `src/api/chat_cli/__init__.py` | 1 | flask-api | Package init (chat_cli) | structure |
| `src/api/chat_cli/__main__.py` | 1 | flask-api | Python module (chat_cli/__main__.py) | structure |
| `src/api/chat_cli/cli.py` | 1 | flask-api | Python module (chat_cli/cli.py) | structure |
| `src/api/chat_delivery.py` | 1 | flask-api | Python module (api/chat_delivery.py) | structure |
| `src/api/chat_run_registry.py` | 1 | flask-api | Python module (api/chat_run_registry.py) | structure |
| `src/api/chat_status_phases.py` | 1 | flask-api | Python module (api/chat_status_phases.py) | structure |
| `src/api/chat_titler.py` | 1 | flask-api | Python module (api/chat_titler.py) | structure |
| `src/api/chat_tts.py` | 1 | flask-api | Python module (api/chat_tts.py) | structure |
| `src/api/chat_turn_idempotency.py` | 1 | flask-api | Python module (api/chat_turn_idempotency.py) | structure |
| `src/api/chat_warnings.py` | 1 | flask-api | Python module (api/chat_warnings.py) | structure |
| `src/api/chat_widgets.py` | 1 | flask-api | Python module (api/chat_widgets.py) | structure |
| `src/api/commit_message_suggester.py` | 1 | flask-api | Python module (api/commit_message_suggester.py) | structure |
| `src/api/cursor_agent_commands.py` | 1 | flask-api | Python module (api/cursor_agent_commands.py) | structure |
| `src/api/cursor_plan_bridge.py` | 1 | flask-api | Python module (api/cursor_plan_bridge.py) | structure |
| `src/api/cursor_question_bridge.py` | 1 | flask-api | Python module (api/cursor_question_bridge.py) | structure |
| `src/api/cuttle_brain/CONTEXT_COMPILER.md` | 1 | cuttle-brain | Markdown (CONTEXT_COMPILER.md) | inventoried |
| `src/api/cuttle_brain/__init__.py` | 1 | cuttle-brain | Package init (cuttle_brain) | structure |
| `src/api/cuttle_brain/__main__.py` | 1 | cuttle-brain | Python module (cuttle_brain/__main__.py) | structure |
| `src/api/cuttle_brain/cli.py` | 1 | cuttle-brain | Python module (cuttle_brain/cli.py) | structure |
| `src/api/cuttle_brain/context_compiler.py` | 1 | cuttle-brain | Python module (cuttle_brain/context_compiler.py) | structure |
| `src/api/cuttle_brain/context_delta.py` | 1 | cuttle-brain | Python module (cuttle_brain/context_delta.py) | structure |
| `src/api/cuttle_brain/handoff.py` | 1 | cuttle-brain | Python module (cuttle_brain/handoff.py) | structure |
| `src/api/cuttle_brain/personal_overlay.py` | 1 | cuttle-brain | Python module (cuttle_brain/personal_overlay.py) | structure |
| `src/api/cuttle_jobs/__init__.py` | 1 | flask-api | Package init (cuttle_jobs) | structure |
| `src/api/cuttle_jobs/client.py` | 1 | flask-api | Python module (cuttle_jobs/client.py) | structure |
| `src/api/cuttle_jobs/commands.py` | 1 | flask-api | Python module (cuttle_jobs/commands.py) | structure |
| `src/api/cuttle_jobs/executor.py` | 1 | flask-api | Python module (cuttle_jobs/executor.py) | structure |
| `src/api/cuttle_jobs/formatting.py` | 1 | flask-api | Python module (cuttle_jobs/formatting.py) | structure |
| `src/api/cuttle_jobs/status_store.py` | 1 | flask-api | Python module (cuttle_jobs/status_store.py) | structure |
| `src/api/cuttle_jobs/worker.py` | 1 | flask-api | Python module (cuttle_jobs/worker.py) | structure |
| `src/api/cuttle_jobs/workspace.py` | 1 | flask-api | Python module (cuttle_jobs/workspace.py) | structure |
| `src/api/cuttle_managed_process_guard.py` | 1 | flask-api | Python module (api/cuttle_managed_process_guard.py) | structure |
| `src/api/cuttle_ui_capabilities.py` | 1 | flask-api | Python module (api/cuttle_ui_capabilities.py) | structure |
| `src/api/dashboards/__init__.py` | 1 | dashboards | Package init (dashboards) | structure |
| `src/api/dashboards/__main__.py` | 1 | dashboards | Python module (dashboards/__main__.py) | structure |
| `src/api/dashboards/benchmarklist.py` | 1 | dashboards | Python module (dashboards/benchmarklist.py) | structure |
| `src/api/dashboards/catalog.py` | 1 | dashboards | Python module (dashboards/catalog.py) | structure |
| `src/api/dashboards/cli.py` | 1 | dashboards | Python module (dashboards/cli.py) | structure |
| `src/api/dashboards/deepswe.py` | 1 | dashboards | Python module (dashboards/deepswe.py) | structure |
| `src/api/dashboards/http_fetch.py` | 1 | dashboards | Python module (dashboards/http_fetch.py) | structure |
| `src/api/dashboards/integrations.py` | 1 | dashboards | Python module (dashboards/integrations.py) | structure |
| `src/api/dashboards/routes.py` | 1 | dashboards | Python module (dashboards/routes.py) | structure |
| `src/api/dashboards/service.py` | 1 | dashboards | Python module (dashboards/service.py) | structure |
| `src/api/dashboards/usage.py` | 1 | dashboards | Python module (dashboards/usage.py) | structure |
| `src/api/desktop_electron.py` | 1 | flask-api | Python module (api/desktop_electron.py) | structure |
| `src/api/device_workers/__init__.py` | 1 | workers | Package init (device_workers) | structure |
| `src/api/device_workers/__main__.py` | 1 | workers | Python module (device_workers/__main__.py) | structure |
| `src/api/device_workers/auth.py` | 1 | workers | Python module (device_workers/auth.py) | structure |
| `src/api/device_workers/capabilities.py` | 1 | workers | Python module (device_workers/capabilities.py) | structure |
| `src/api/device_workers/cli.py` | 1 | workers | Python module (device_workers/cli.py) | structure |
| `src/api/device_workers/client.py` | 1 | workers | Python module (device_workers/client.py) | structure |
| `src/api/device_workers/config.py` | 1 | workers | Python module (device_workers/config.py) | structure |
| `src/api/device_workers/executor.py` | 1 | workers | Python module (device_workers/executor.py) | structure |
| `src/api/device_workers/intent.py` | 1 | workers | Python module (device_workers/intent.py) | structure |
| `src/api/device_workers/job_policy.py` | 1 | workers | Python module (device_workers/job_policy.py) | structure |
| `src/api/device_workers/long_run.py` | 1 | workers | Python module (device_workers/long_run.py) | structure |
| `src/api/device_workers/platform.py` | 1 | workers | Python module (device_workers/platform.py) | structure |
| `src/api/device_workers/profiles.py` | 1 | workers | Python module (device_workers/profiles.py) | structure |
| `src/api/device_workers/routes.py` | 1 | workers | Python module (device_workers/routes.py) | structure |
| `src/api/device_workers/ssh_approval.py` | 1 | workers | Python module (device_workers/ssh_approval.py) | structure |
| `src/api/device_workers/store.py` | 1 | workers | Python module (device_workers/store.py) | structure |
| `src/api/device_workers/worker_loop.py` | 1 | workers | Python module (device_workers/worker_loop.py) | structure |
| `src/api/discord_chat_bridge.py` | 1 | flask-api | Python module (api/discord_chat_bridge.py) | structure |
| `src/api/discord_cli/__init__.py` | 1 | flask-api | Package init (discord_cli) | structure |
| `src/api/discord_cli/__main__.py` | 1 | flask-api | Python module (discord_cli/__main__.py) | structure |
| `src/api/discord_cli/cli.py` | 1 | flask-api | Python module (discord_cli/cli.py) | structure |
| `src/api/discovery_mdns.py` | 1 | flask-api | Python module (api/discovery_mdns.py) | structure |
| `src/api/doctor.py` | 1 | flask-api | Python module (api/doctor.py) | structure |
| `src/api/edit_attribution/__init__.py` | 1 | flask-api | Package init (edit_attribution) | structure |
| `src/api/edit_attribution/journal.py` | 1 | flask-api | Python module (edit_attribution/journal.py) | structure |
| `src/api/edit_attribution/recorder.py` | 1 | flask-api | Python module (edit_attribution/recorder.py) | structure |
| `src/api/flask_restart.py` | 1 | flask-api | Python module (api/flask_restart.py) | structure |
| `src/api/fs_reveal.py` | 1 | flask-api | Python module (api/fs_reveal.py) | structure |
| `src/api/gitea/__init__.py` | 1 | flask-api | Package init (gitea) | structure |
| `src/api/gitea/__main__.py` | 1 | flask-api | Python module (gitea/__main__.py) | structure |
| `src/api/gitea/cli.py` | 1 | flask-api | Python module (gitea/cli.py) | structure |
| `src/api/gitea_client.py` | 1 | flask-api | Python module (api/gitea_client.py) | structure |
| `src/api/home_automation_socket/README.md` | 1 | flask-api | Readme (home_automation_socket) | inventoried |
| `src/api/home_automation_socket/__init__.py` | 1 | flask-api | Package init (home_automation_socket) | structure |
| `src/api/home_automation_socket/providers/__init__.py` | 1 | flask-api | Package init (providers) | structure |
| `src/api/home_automation_socket/providers/govee.py` | 1 | flask-api | Python module (providers/govee.py) | structure |
| `src/api/home_automation_socket/providers/nest.py` | 1 | flask-api | Python module (providers/nest.py) | structure |
| `src/api/home_automation_socket/registry.py` | 1 | flask-api | Python module (home_automation_socket/registry.py) | structure |
| `src/api/home_automation_socket/types.py` | 1 | flask-api | Python module (home_automation_socket/types.py) | structure |
| `src/api/http_authz.py` | 1 | flask-api | Python module (api/http_authz.py) | structure |
| `src/api/inference_mode.py` | 1 | flask-api | Python module (api/inference_mode.py) | structure |
| `src/api/internal_http.py` | 1 | flask-api | In-process Flask POST helpers; imported by web_chat_api only to set PIPELINE_AVAILABLE — functions unused | execution |
| `src/api/jev/__init__.py` | 1 | flask-api | Package init (jev) | structure |
| `src/api/jev/__main__.py` | 1 | flask-api | Python module (jev/__main__.py) | structure |
| `src/api/jev/cli.py` | 1 | flask-api | Python module (jev/cli.py) | structure |
| `src/api/jev/client.py` | 1 | flask-api | Python module (jev/client.py) | structure |
| `src/api/jev/config.py` | 1 | flask-api | Python module (jev/config.py) | structure |
| `src/api/jev/labels.py` | 1 | flask-api | Python module (jev/labels.py) | structure |
| `src/api/jev/rage.py` | 1 | flask-api | Python module (jev/rage.py) | structure |
| `src/api/jev/rank.py` | 1 | flask-api | Python module (jev/rank.py) | structure |
| `src/api/jev/regress.py` | 1 | flask-api | Python module (jev/regress.py) | structure |
| `src/api/jev/routing.py` | 1 | flask-api | Python module (jev/routing.py) | structure |
| `src/api/jev/thresholds.py` | 1 | flask-api | Python module (jev/thresholds.py) | structure |
| `src/api/jev/types.py` | 1 | flask-api | Python module (jev/types.py) | structure |
| `src/api/jev/watch.py` | 1 | flask-api | Python module (jev/watch.py) | structure |
| `src/api/job_watch.py` | 1 | flask-api | Python module (api/job_watch.py) | structure |
| `src/api/lan_access.py` | 1 | flask-api | Python module (api/lan_access.py) | structure |
| `src/api/limiter.py` | 1 | flask-api | Python module (api/limiter.py) | structure |
| `src/api/llm_complete.py` | 1 | flask-api | Python module (api/llm_complete.py) | structure |
| `src/api/markdown_skills.py` | 1 | flask-api | Python module (api/markdown_skills.py) | structure |
| `src/api/mobile_android_update.py` | 1 | flask-api | Python module (api/mobile_android_update.py) | structure |
| `src/api/mobile_companion.py` | 1 | flask-api | Python module (api/mobile_companion.py) | structure |
| `src/api/model_pricing.py` | 1 | flask-api | Python module (api/model_pricing.py) | structure |
| `src/api/pairing_manager.py` | 1 | flask-api | Python module (api/pairing_manager.py) | structure |
| `src/api/panes_cli/__init__.py` | 1 | flask-api | Package init (panes_cli) | structure |
| `src/api/panes_cli/__main__.py` | 1 | flask-api | Python module (panes_cli/__main__.py) | structure |
| `src/api/panes_cli/cli.py` | 1 | flask-api | Python module (panes_cli/cli.py) | structure |
| `src/api/process_kill_safety.py` | 1 | flask-api | Python module (api/process_kill_safety.py) | structure |
| `src/api/project_actions.py` | 1 | flask-api | Python module (api/project_actions.py) | structure |
| `src/api/project_commands.py` | 1 | flask-api | Python module (api/project_commands.py) | structure |
| `src/api/prompt_enhancer.py` | 1 | flask-api | Python module (api/prompt_enhancer.py) | structure |
| `src/api/query_events.py` | 1 | flask-api | Python module (api/query_events.py) | structure |
| `src/api/query_tracker.py` | 1 | flask-api | Python module (api/query_tracker.py) | structure |
| `src/api/restart_safety_policy.py` | 1 | flask-api | Python module (api/restart_safety_policy.py) | structure |
| `src/api/sandbox_policy.py` | 1 | flask-api | Python module (api/sandbox_policy.py) | structure |
| `src/api/session_keys.py` | 1 | flask-api | Python module (api/session_keys.py) | structure |
| `src/api/shared_media.py` | 1 | flask-api | Python module (api/shared_media.py) | structure |
| `src/api/starred_project.py` | 1 | flask-api | Python module (api/starred_project.py) | structure |
| `src/api/starred_slash.py` | 1 | flask-api | Python module (api/starred_slash.py) | structure |
| `src/api/subagents/__init__.py` | 1 | flask-api | Package init (subagents) | structure |
| `src/api/subagents/__main__.py` | 1 | flask-api | Python module (subagents/__main__.py) | structure |
| `src/api/subagents/cli.py` | 1 | flask-api | Python module (subagents/cli.py) | structure |
| `src/api/subagents/extras.py` | 1 | flask-api | Python module (subagents/extras.py) | structure |
| `src/api/subagents/identity.py` | 1 | flask-api | Python module (subagents/identity.py) | structure |
| `src/api/subagents/profiles.py` | 1 | flask-api | Python module (subagents/profiles.py) | structure |
| `src/api/subagents/service.py` | 1 | flask-api | Python module (subagents/service.py) | structure |
| `src/api/subagents/spec.py` | 1 | flask-api | Python module (subagents/spec.py) | structure |
| `src/api/subagents/store.py` | 1 | flask-api | Python module (subagents/store.py) | structure |
| `src/api/subagents/turns.py` | 1 | flask-api | Python module (subagents/turns.py) | structure |
| `src/api/subagents/types.py` | 1 | flask-api | Python module (subagents/types.py) | structure |
| `src/api/task_benchmarks.py` | 1 | flask-api | Python module (api/task_benchmarks.py) | structure |
| `src/api/video_playlists.py` | 1 | flask-api | Python module (api/video_playlists.py) | structure |
| `src/api/vision_prepass.py` | 1 | flask-api | Python module (api/vision_prepass.py) | structure |
| `src/api/web_chat_api.py` | 1 | flask-api | Flask composition root (HTTPS 8080) | partial-execution |
| `src/api/web_terminal.py` | 1 | flask-api | Python module (api/web_terminal.py) | structure |
| `src/api/widgets_cli/__init__.py` | 1 | flask-api | Package init (widgets_cli) | structure |
| `src/api/widgets_cli/__main__.py` | 1 | flask-api | Python module (widgets_cli/__main__.py) | structure |
| `src/api/widgets_cli/cli.py` | 1 | flask-api | Python module (widgets_cli/cli.py) | structure |
| `src/bot.py` | 1 | src-other | Python module (src/bot.py) | execution |
| `src/bot_config.json` | 1 | src-other | src/ BotConfig copy | inventoried |
| `src/bots/__init__.py` | 1 | discord | Package init (bots) | structure |
| `src/bots/discord_bot.py` | 1 | discord | Python module (bots/discord_bot.py) | partial-execution |
| `src/core/__init__.py` | 1 | core | Package init (core) | structure |
| `src/core/config.py` | 1 | core | Python module (core/config.py) | execution |
| `src/core/local_llm.py` | 1 | core | Python module (core/local_llm.py) | structure |
| `src/core/mcp_tool_coaching.py` | 1 | core | Prompt suffix for bundled tools; called from web_chat_api local-LLM path | execution |
| `src/core/runtime_paths.py` | 1 | core | Python module (core/runtime_paths.py) | structure |
| `src/data/README.md` | 1 | data-templates | Readme (data) | inventoried |
| `src/data/db/README.md` | 1 | data-templates | Readme (db) | inventoried |
| `src/data/harness_agents/README.md` | 1 | data-templates | Readme (harness_agents) | inventoried |
| `src/data/home_automation_devices.example.json` | 1 | data-templates | JSON config/fixture (home_automation_devices.example.json) | inventoried |
| `src/img/JamBit Logo White Font.png` | 1 | src-other | Asset (JamBit Logo White Font.png) | inventoried |
| `src/img/cuttle-avatar.png` | 1 | src-other | Asset (cuttle-avatar.png) | inventoried |
| `src/img/cuttle-logo.png` | 1 | src-other | Asset (cuttle-logo.png) | inventoried |
| `src/img/cuttle-mascot.png` | 1 | src-other | Asset (cuttle-mascot.png) | inventoried |
| `src/img/cuttle-mascot_square.png` | 1 | src-other | Asset (cuttle-mascot_square.png) | inventoried |
| `src/img/cuttle-mascot_square_host.png` | 1 | src-other | Asset (cuttle-mascot_square_host.png) | inventoried |
| `src/img/cuttle_logo.ico` | 1 | src-other | Asset (cuttle_logo.ico) | inventoried |
| `src/img/sattelite.PNG` | 1 | src-other | Asset (sattelite.PNG) | inventoried |
| `src/launcher.py` | 1 | src-other | Pre-daemon process supervisor (cwd src/) | partial-execution |
| `src/managers/__init__.py` | 1 | managers | Package init (managers) | structure |
| `src/managers/cuttle_scaffold.py` | 1 | managers | Python module (managers/cuttle_scaffold.py) | structure |
| `src/managers/home_automation.py` | 1 | managers | Python module (managers/home_automation.py) | structure |
| `src/managers/project_manager.py` | 1 | managers | Python module (managers/project_manager.py) | structure |
| `src/managers/settings_manager.py` | 1 | managers | Python module (managers/settings_manager.py) | execution |
| `src/managers/task_manager.py` | 1 | managers | Python module (managers/task_manager.py) | structure |
| `src/pytest.ini` | 1 | src-other | Tracked file (pytest.ini) | inventoried |
| `src/requirements/README.md` | 1 | python-deps | Readme (requirements) | inventoried |
| `src/requirements/requirements.txt` | 1 | python-deps | Tracked file (requirements.txt) | inventoried |
| `src/scripts/cert_manager.py` | 1 | process-scripts | Python module (scripts/cert_manager.py) | structure |
| `src/scripts/create_desktop_shortcuts.ps1` | 1 | process-scripts | Launcher/script (create_desktop_shortcuts.ps1) | inventoried |
| `src/scripts/create_test_execution.py` | 1 | process-scripts | Python module (scripts/create_test_execution.py) | structure |
| `src/scripts/cuttle_client_daemon.py` | 1 | process-scripts | Python module (scripts/cuttle_client_daemon.py) | structure |
| `src/scripts/cuttle_daemon.py` | 1 | process-scripts | Daemon: spawn Flask, Discord, tray, restart | partial-execution |
| `src/scripts/cuttle_device_worker.py` | 1 | process-scripts | Python module (scripts/cuttle_device_worker.py) | structure |
| `src/scripts/diagnose_lan.bat` | 1 | process-scripts | Launcher/script (diagnose_lan.bat) | inventoried |
| `src/scripts/dogfood_blender_mesh.py` | 1 | process-scripts | Python module (scripts/dogfood_blender_mesh.py) | structure |
| `src/scripts/enable_lan_firewall.bat` | 1 | process-scripts | Launcher/script (enable_lan_firewall.bat) | inventoried |
| `src/scripts/enable_lan_firewall.ps1` | 1 | process-scripts | Launcher/script (enable_lan_firewall.ps1) | inventoried |
| `src/scripts/generate_test_report.py` | 1 | process-scripts | Python module (scripts/generate_test_report.py) | structure |
| `src/scripts/launchers/launcher_debug.py` | 5 | legacy-launchers | Broken debug launcher: looks for src/web_chat_api.py under launchers/ | execution |
| `src/scripts/launchers/start_router_editor.py` | 5 | legacy-launchers | Broken: imports web_chat_api from launchers/ cwd | execution |
| `src/scripts/launchers/start_ungit.py` | 5 | legacy-launchers | Broken: imports project_manager from launchers/; real module is managers/ | execution |
| `src/scripts/launchers/start_web_chat.py` | 5 | legacy-launchers | Broken Flask-alone helper: chdir launchers/, runs web_chat_api.py by basename | execution |
| `src/scripts/run_tests_with_logging.py` | 1 | process-scripts | Python module (scripts/run_tests_with_logging.py) | structure |
| `src/scripts/setup/fix_venv.py` | 1 | process-scripts | Python module (setup/fix_venv.py) | structure |
| `src/scripts/setup/install_deps_step_by_step.py` | 1 | process-scripts | Python module (setup/install_deps_step_by_step.py) | structure |
| `src/scripts/setup/setup_env.py` | 1 | process-scripts | Python module (setup/setup_env.py) | structure |
| `src/scripts/start_api_server.py` | 1 | process-scripts | Starts port-5000 time_series_api (not Cuttle :8080) | execution |
| `src/scripts/test_history_manager.py` | 1 | process-scripts | Python module (scripts/test_history_manager.py) | structure |
| `src/scripts/test_phone_isolation.bat` | 1 | process-scripts | Launcher/script (test_phone_isolation.bat) | inventoried |
| `src/scripts/test_phone_isolation.ps1` | 1 | process-scripts | Launcher/script (test_phone_isolation.ps1) | inventoried |
| `src/scripts/time_series_api.py` | 1 | process-scripts | Legacy chart Flask app on :5000 | execution |
| `src/scripts/utilities/README.md` | 1 | cli-utilities | Readme (utilities) | inventoried |
| `src/scripts/utilities/agent_process.py` | 1 | cli-utilities | Python module (utilities/agent_process.py) | structure |
| `src/scripts/utilities/claude_cli_session_store.py` | 1 | cli-utilities | Python module (utilities/claude_cli_session_store.py) | structure |
| `src/scripts/utilities/claude_cli_tool.py` | 1 | cli-utilities | Python module (utilities/claude_cli_tool.py) | structure |
| `src/scripts/utilities/claude_code_tool.py` | 1 | cli-utilities | Python module (utilities/claude_code_tool.py) | structure |
| `src/scripts/utilities/claw_code_harness.py` | 1 | cli-utilities | Python module (utilities/claw_code_harness.py) | structure |
| `src/scripts/utilities/codex_app_server.py` | 1 | cli-utilities | Python module (utilities/codex_app_server.py) | structure |
| `src/scripts/utilities/codex_app_server_turn.py` | 1 | cli-utilities | Python module (utilities/codex_app_server_turn.py) | structure |
| `src/scripts/utilities/codex_cli_session_store.py` | 1 | cli-utilities | Python module (utilities/codex_cli_session_store.py) | structure |
| `src/scripts/utilities/codex_cli_tool.py` | 1 | cli-utilities | Python module (utilities/codex_cli_tool.py) | structure |
| `src/scripts/utilities/cursor_cli_session_store.py` | 1 | cli-utilities | Python module (utilities/cursor_cli_session_store.py) | structure |
| `src/scripts/utilities/cursor_cli_tool.py` | 1 | cli-utilities | Python module (utilities/cursor_cli_tool.py) | structure |
| `src/scripts/utilities/debug_cursor_location.py` | 5 | cli-utilities | Debug helper; imports tool_manager from utilities/ (module lives elsewhere) | structure |
| `src/scripts/utilities/example_project_setup.py` | 1 | cli-utilities | Python module (utilities/example_project_setup.py) | structure |
| `src/scripts/utilities/fix_project_paths.py` | 1 | cli-utilities | Python module (utilities/fix_project_paths.py) | structure |
| `src/scripts/utilities/git_credential_helper.py` | 1 | cli-utilities | Python module (utilities/git_credential_helper.py) | structure |
| `src/scripts/utilities/git_graph.py` | 1 | cli-utilities | Python module (utilities/git_graph.py) | structure |
| `src/scripts/utilities/git_pending_changes.py` | 1 | cli-utilities | Python module (utilities/git_pending_changes.py) | structure |
| `src/scripts/utilities/hello_world.py` | 5 | cli-utilities | Sample print script; no product callers | structure |
| `src/scripts/utilities/hermes_cli_session_store.py` | 1 | cli-utilities | Python module (utilities/hermes_cli_session_store.py) | structure |
| `src/scripts/utilities/hermes_cli_tool.py` | 1 | cli-utilities | Python module (utilities/hermes_cli_tool.py) | structure |
| `src/scripts/utilities/kill_bots.py` | 5 | cli-utilities | Imports launcher from utilities/; Electron README still points at src/kill_bots.py | execution |
| `src/scripts/utilities/migrate_project_paths.py` | 1 | cli-utilities | Python module (utilities/migrate_project_paths.py) | structure |
| `src/scripts/utilities/muse_cli_session_store.py` | 1 | cli-utilities | Python module (utilities/muse_cli_session_store.py) | structure |
| `src/scripts/utilities/muse_cli_tool.py` | 1 | cli-utilities | Python module (utilities/muse_cli_tool.py) | structure |
| `src/scripts/utilities/muse_serve_turn.py` | 1 | cli-utilities | Python module (utilities/muse_serve_turn.py) | structure |
| `src/scripts/utilities/promo_sculpture.py` | 1 | cli-utilities | Python module (utilities/promo_sculpture.py) | structure |
| `src/scripts/utilities/promo_video.py` | 1 | cli-utilities | Python module (utilities/promo_video.py) | structure |
| `src/scripts/utilities/readme_gifs.py` | 1 | cli-utilities | Python module (utilities/readme_gifs.py) | structure |
| `src/scripts/utilities/readme_promo.py` | 1 | cli-utilities | Python module (utilities/readme_promo.py) | structure |
| `src/scripts/utilities/readme_screenshots.py` | 1 | cli-utilities | Python module (utilities/readme_screenshots.py) | structure |
| `src/scripts/utilities/stdio_rpc.py` | 1 | cli-utilities | Python module (utilities/stdio_rpc.py) | structure |
| `src/scripts/utilities/update_project_paths.py` | 1 | cli-utilities | Python module (utilities/update_project_paths.py) | structure |
| `src/tests/README.md` | 1 | tests | Test: README.md | inventoried |
| `src/tests/__init__.py` | 1 | tests | Test: __init__.py | structure |
| `src/tests/bot_config.json` | 1 | tests | Test: bot_config.json | inventoried |
| `src/tests/conftest.py` | 1 | tests | Test: conftest.py | structure |
| `src/tests/e2e/__init__.py` | 1 | tests | Test: __init__.py | structure |
| `src/tests/e2e/test_app_shell_navigation.py` | 1 | tests | Test: test_app_shell_navigation.py | structure |
| `src/tests/e2e/test_chat_badge_starring.py` | 1 | tests | Test: test_chat_badge_starring.py | structure |
| `src/tests/e2e/test_chat_history_search_panel.py` | 1 | tests | Test: test_chat_history_search_panel.py | structure |
| `src/tests/e2e/test_verify_nav_history.py` | 1 | tests | Test: test_verify_nav_history.py | structure |
| `src/tests/fixtures/supervised_truncated_worker_report.md` | 1 | tests | Test: supervised_truncated_worker_report.md | inventoried |
| `src/tests/fixtures/wr_d49c8d97962e.raw.txt` | 1 | tests | Test: wr_d49c8d97962e.raw.txt | inventoried |
| `src/tests/html_reporter.py` | 1 | tests | Test: html_reporter.py | structure |
| `src/tests/integration/__init__.py` | 1 | tests | Test: __init__.py | structure |
| `src/tests/integration/test_discord_api_e2e.py` | 1 | tests | Test: test_discord_api_e2e.py | structure |
| `src/tests/integration/test_discord_remote_execution.py` | 1 | tests | Test: test_discord_remote_execution.py | structure |
| `src/tests/run_all_tests.py` | 1 | tests | Test: run_all_tests.py | structure |
| `src/tests/run_all_tests_simple.py` | 1 | tests | Test: run_all_tests_simple.py | structure |
| `src/tests/run_safe_tests.py` | 1 | tests | Test: run_safe_tests.py | structure |
| `src/tests/run_tests_wsl_compatible.py` | 1 | tests | Test: run_tests_wsl_compatible.py | structure |
| `src/tests/spend_guard.py` | 1 | tests | Test: spend_guard.py | structure |
| `src/tests/test_action_form_process_restart.py` | 1 | tests | Test: test_action_form_process_restart.py | structure |
| `src/tests/test_action_forms.py` | 1 | tests | Test: test_action_forms.py | structure |
| `src/tests/test_agent_badge_segments.py` | 1 | tests | Test: test_agent_badge_segments.py | structure |
| `src/tests/test_agent_context.py` | 1 | tests | Test: test_agent_context.py | structure |
| `src/tests/test_agent_context_all_clis.py` | 1 | tests | Test: test_agent_context_all_clis.py | structure |
| `src/tests/test_agent_cost_slash.py` | 1 | tests | Test: test_agent_cost_slash.py | structure |
| `src/tests/test_agent_defaults.py` | 1 | tests | Test: test_agent_defaults.py | structure |
| `src/tests/test_agent_harness.py` | 1 | tests | Test: test_agent_harness.py | structure |
| `src/tests/test_agent_harness_smoke.py` | 1 | tests | Test: test_agent_harness_smoke.py | structure |
| `src/tests/test_agent_harness_timeouts.py` | 1 | tests | Test: test_agent_harness_timeouts.py | structure |
| `src/tests/test_agent_resume_contract.py` | 1 | tests | Test: test_agent_resume_contract.py | structure |
| `src/tests/test_agent_router.py` | 1 | tests | Test: test_agent_router.py | structure |
| `src/tests/test_agent_router_drift.py` | 1 | tests | Test: test_agent_router_drift.py | structure |
| `src/tests/test_agent_router_eval.py` | 1 | tests | Test: test_agent_router_eval.py | structure |
| `src/tests/test_agent_router_frustration.py` | 1 | tests | Test: test_agent_router_frustration.py | structure |
| `src/tests/test_agent_router_outcomes.py` | 1 | tests | Test: test_agent_router_outcomes.py | structure |
| `src/tests/test_agent_router_palette.py` | 1 | tests | Test: test_agent_router_palette.py | structure |
| `src/tests/test_agent_router_repeats.py` | 1 | tests | Test: test_agent_router_repeats.py | structure |
| `src/tests/test_agent_router_use_cases.py` | 1 | tests | Test: test_agent_router_use_cases.py | structure |
| `src/tests/test_agent_steer.py` | 1 | tests | Test: test_agent_steer.py | structure |
| `src/tests/test_agent_stop_then_followup.py` | 1 | tests | Test: test_agent_stop_then_followup.py | structure |
| `src/tests/test_agent_usage_slash.py` | 1 | tests | Test: test_agent_usage_slash.py | structure |
| `src/tests/test_assistant_badge_all_harnesses.py` | 1 | tests | Test: test_assistant_badge_all_harnesses.py | structure |
| `src/tests/test_auth_guest.py` | 1 | tests | Test: test_auth_guest.py | structure |
| `src/tests/test_auth_rate_limit.py` | 1 | tests | Test: test_auth_rate_limit.py | structure |
| `src/tests/test_auth_session_token.py` | 1 | tests | Test: test_auth_session_token.py | structure |
| `src/tests/test_bare_sticky_agent_send.py` | 1 | tests | Test: test_bare_sticky_agent_send.py | structure |
| `src/tests/test_chat_attachments.py` | 1 | tests | Test: test_chat_attachments.py | structure |
| `src/tests/test_chat_attention_dots.py` | 1 | tests | Test: test_chat_attention_dots.py | structure |
| `src/tests/test_chat_busy_zombie.py` | 1 | tests | Test: test_chat_busy_zombie.py | structure |
| `src/tests/test_chat_cli.py` | 1 | tests | Test: test_chat_cli.py | structure |
| `src/tests/test_chat_cross_session_activity.py` | 1 | tests | Test: test_chat_cross_session_activity.py | structure |
| `src/tests/test_chat_false_reply_ready.py` | 1 | tests | Test: test_chat_false_reply_ready.py | structure |
| `src/tests/test_chat_find.py` | 1 | tests | Test: test_chat_find.py | structure |
| `src/tests/test_chat_followup_heal.py` | 1 | tests | Test: test_chat_followup_heal.py | structure |
| `src/tests/test_chat_followup_share.py` | 1 | tests | Test: test_chat_followup_share.py | structure |
| `src/tests/test_chat_handle_links.py` | 1 | tests | Test: test_chat_handle_links.py | structure |
| `src/tests/test_chat_history_delete_modal.py` | 1 | tests | Test: test_chat_history_delete_modal.py | structure |
| `src/tests/test_chat_history_search.py` | 1 | tests | Test: test_chat_history_search.py | structure |
| `src/tests/test_chat_message_pagination.py` | 1 | tests | Test: test_chat_message_pagination.py | structure |
| `src/tests/test_chat_message_project_meta.py` | 1 | tests | Test: test_chat_message_project_meta.py | structure |
| `src/tests/test_chat_message_share_index.py` | 1 | tests | Test: test_chat_message_share_index.py | structure |
| `src/tests/test_chat_page_js_syntax.py` | 1 | tests | Test: test_chat_page_js_syntax.py | structure |
| `src/tests/test_chat_pause_not_stop.py` | 1 | tests | Test: test_chat_pause_not_stop.py | structure |
| `src/tests/test_chat_rename.py` | 1 | tests | Test: test_chat_rename.py | structure |
| `src/tests/test_chat_session_routing.py` | 1 | tests | Test: test_chat_session_routing.py | structure |
| `src/tests/test_chat_store_session_scope.py` | 1 | tests | Test: test_chat_store_session_scope.py | structure |
| `src/tests/test_chat_switch_running_spinner.py` | 1 | tests | Test: test_chat_switch_running_spinner.py | structure |
| `src/tests/test_chat_titler.py` | 1 | tests | Test: test_chat_titler.py | structure |
| `src/tests/test_chat_tts.py` | 1 | tests | Test: test_chat_tts.py | structure |
| `src/tests/test_chat_widgets.py` | 1 | tests | Test: test_chat_widgets.py | structure |
| `src/tests/test_claude_cli_tool.py` | 1 | tests | Test: test_claude_cli_tool.py | structure |
| `src/tests/test_claude_usage.py` | 1 | tests | Test: test_claude_usage.py | structure |
| `src/tests/test_codex_app_server.py` | 1 | tests | Test: test_codex_app_server.py | structure |
| `src/tests/test_codex_cli.py` | 1 | tests | Test: test_codex_cli.py | structure |
| `src/tests/test_codex_starred_effort.py` | 1 | tests | Test: test_codex_starred_effort.py | structure |
| `src/tests/test_command_detection.py` | 1 | tests | Test: test_command_detection.py | structure |
| `src/tests/test_commit_message_suggester.py` | 1 | tests | Test: test_commit_message_suggester.py | structure |
| `src/tests/test_composer_chip_removal.py` | 1 | tests | Test: test_composer_chip_removal.py | structure |
| `src/tests/test_composer_draft_session.py` | 1 | tests | Test: test_composer_draft_session.py | structure |
| `src/tests/test_concurrent_query_tracking.py` | 1 | tests | Test: test_concurrent_query_tracking.py | structure |
| `src/tests/test_context_compiler.py` | 1 | tests | Test: test_context_compiler.py | structure |
| `src/tests/test_context_delta.py` | 1 | tests | Test: test_context_delta.py | structure |
| `src/tests/test_cursor_agent_incomplete_finish.py` | 1 | tests | Test: test_cursor_agent_incomplete_finish.py | structure |
| `src/tests/test_cursor_agent_model_honesty.py` | 1 | tests | Test: test_cursor_agent_model_honesty.py | structure |
| `src/tests/test_cursor_agent_multiline_prompt.py` | 1 | tests | Test: test_cursor_agent_multiline_prompt.py | structure |
| `src/tests/test_cursor_agent_reply_assemble.py` | 1 | tests | Test: test_cursor_agent_reply_assemble.py | structure |
| `src/tests/test_cursor_agent_slash_commands.py` | 1 | tests | Test: test_cursor_agent_slash_commands.py | structure |
| `src/tests/test_cursor_agent_timeout_continue.py` | 1 | tests | Test: test_cursor_agent_timeout_continue.py | structure |
| `src/tests/test_cursor_chip_labels.py` | 1 | tests | Test: test_cursor_chip_labels.py | structure |
| `src/tests/test_cursor_plan_bridge.py` | 1 | tests | Test: test_cursor_plan_bridge.py | structure |
| `src/tests/test_cursor_question_bridge.py` | 1 | tests | Test: test_cursor_question_bridge.py | structure |
| `src/tests/test_cuttle_jobs.py` | 1 | tests | Test: test_cuttle_jobs.py | structure |
| `src/tests/test_cuttle_jobs_pr.py` | 1 | tests | Test: test_cuttle_jobs_pr.py | structure |
| `src/tests/test_cuttle_jobs_workspace.py` | 1 | tests | Test: test_cuttle_jobs_workspace.py | structure |
| `src/tests/test_cuttle_managed_process_guard.py` | 1 | tests | Test: test_cuttle_managed_process_guard.py | structure |
| `src/tests/test_cuttle_mcp_retired.py` | 1 | tests | Test: test_cuttle_mcp_retired.py | structure |
| `src/tests/test_cuttle_performance.py` | 1 | tests | Test: test_cuttle_performance.py | structure |
| `src/tests/test_cuttle_scaffold.py` | 1 | tests | Test: test_cuttle_scaffold.py | structure |
| `src/tests/test_cuttle_ui_capabilities.py` | 1 | tests | Test: test_cuttle_ui_capabilities.py | structure |
| `src/tests/test_daemon_startup_ui.py` | 1 | tests | Test: test_daemon_startup_ui.py | structure |
| `src/tests/test_dashboards.py` | 1 | tests | Test: test_dashboards.py | structure |
| `src/tests/test_dashboards_usage.py` | 1 | tests | Test: test_dashboards_usage.py | structure |
| `src/tests/test_desktop_electron.py` | 1 | tests | Test: test_desktop_electron.py | structure |
| `src/tests/test_device_workers.py` | 1 | tests | Test: test_device_workers.py | structure |
| `src/tests/test_discord_chat_bridge.py` | 1 | tests | Test: test_discord_chat_bridge.py | structure |
| `src/tests/test_discord_cli.py` | 1 | tests | Test: test_discord_cli.py | structure |
| `src/tests/test_edit_attribution.py` | 1 | tests | Test: test_edit_attribution.py | structure |
| `src/tests/test_filesystem_shell.py` | 1 | tests | Test: test_filesystem_shell.py | structure |
| `src/tests/test_first_turn_agent_pins.py` | 1 | tests | Test: test_first_turn_agent_pins.py | structure |
| `src/tests/test_flask_restart.py` | 1 | tests | Test: test_flask_restart.py | structure |
| `src/tests/test_fs_reveal.py` | 1 | tests | Test: test_fs_reveal.py | structure |
| `src/tests/test_git_commit_chat.py` | 1 | tests | Test: test_git_commit_chat.py | structure |
| `src/tests/test_git_graph.py` | 1 | tests | Test: test_git_graph.py | structure |
| `src/tests/test_git_pending_changes.py` | 1 | tests | Test: test_git_pending_changes.py | structure |
| `src/tests/test_gitea_cli_module.py` | 1 | tests | Test: test_gitea_cli_module.py | structure |
| `src/tests/test_hermes_runtime_config.py` | 1 | tests | Test: test_hermes_runtime_config.py | structure |
| `src/tests/test_hermes_session_pins.py` | 1 | tests | Test: test_hermes_session_pins.py | structure |
| `src/tests/test_hermes_usage.py` | 1 | tests | Test: test_hermes_usage.py | structure |
| `src/tests/test_history_agent_badge_styling.py` | 1 | tests | Test: test_history_agent_badge_styling.py | structure |
| `src/tests/test_home_automation_socket.py` | 1 | tests | Test: test_home_automation_socket.py | structure |
| `src/tests/test_http_authz.py` | 1 | tests | Test: test_http_authz.py | structure |
| `src/tests/test_inference_mode.py` | 1 | tests | Test: test_inference_mode.py | structure |
| `src/tests/test_interruptible_run.py` | 1 | tests | Test: test_interruptible_run.py | structure |
| `src/tests/test_jev.py` | 1 | tests | Test: test_jev.py | structure |
| `src/tests/test_job_watch.py` | 1 | tests | Test: test_job_watch.py | structure |
| `src/tests/test_kernel_usage_cache.py` | 1 | tests | Test: test_kernel_usage_cache.py | structure |
| `src/tests/test_kill_pid_tree_safety.py` | 1 | tests | Test: test_kill_pid_tree_safety.py | structure |
| `src/tests/test_llm_calls_vs_executed_nodes.py` | 1 | tests | Test: test_llm_calls_vs_executed_nodes.py | structure |
| `src/tests/test_llm_complete.py` | 1 | tests | Test: test_llm_complete.py | structure |
| `src/tests/test_local_llm_launch_gate.py` | 1 | tests | Test: test_local_llm_launch_gate.py | structure |
| `src/tests/test_mobile_android_update.py` | 1 | tests | Test: test_mobile_android_update.py | structure |
| `src/tests/test_mobile_companion.py` | 1 | tests | Test: test_mobile_companion.py | structure |
| `src/tests/test_mobile_webview_hardening.py` | 1 | tests | Test: test_mobile_webview_hardening.py | structure |
| `src/tests/test_model_pricing.py` | 1 | tests | Test: test_model_pricing.py | structure |
| `src/tests/test_muse_chat_discovery.py` | 1 | tests | Test: test_muse_chat_discovery.py | structure |
| `src/tests/test_muse_cli.py` | 1 | tests | Test: test_muse_cli.py | structure |
| `src/tests/test_muse_msp_context.py` | 1 | tests | Test: test_muse_msp_context.py | structure |
| `src/tests/test_oauth_link_account.py` | 1 | tests | Test: test_oauth_link_account.py | structure |
| `src/tests/test_opencode_model_catalog.py` | 1 | tests | Test: test_opencode_model_catalog.py | structure |
| `src/tests/test_opencode_sync_auth.py` | 1 | tests | Test: test_opencode_sync_auth.py | structure |
| `src/tests/test_p0_p1_restart_session.py` | 1 | tests | Test: test_p0_p1_restart_session.py | structure |
| `src/tests/test_panes_cli.py` | 1 | tests | Test: test_panes_cli.py | structure |
| `src/tests/test_personal_overlay.py` | 1 | tests | Test: test_personal_overlay.py | structure |
| `src/tests/test_project_actions.py` | 1 | tests | Test: test_project_actions.py | structure |
| `src/tests/test_project_chip_persistence.py` | 1 | tests | Test: test_project_chip_persistence.py | structure |
| `src/tests/test_project_commands.py` | 1 | tests | Test: test_project_commands.py | structure |
| `src/tests/test_prompt_enhancer.py` | 1 | tests | Test: test_prompt_enhancer.py | structure |
| `src/tests/test_query_events.py` | 1 | tests | Test: test_query_events.py | structure |
| `src/tests/test_remaining_harness_context.py` | 1 | tests | Test: test_remaining_harness_context.py | structure |
| `src/tests/test_restart_composer_js.py` | 1 | tests | Test: test_restart_composer_js.py | structure |
| `src/tests/test_restart_daemon_script.py` | 1 | tests | Test: test_restart_daemon_script.py | structure |
| `src/tests/test_restart_daemon_sh.py` | 1 | tests | Test: test_restart_daemon_sh.py | structure |
| `src/tests/test_restart_native_command.py` | 1 | tests | Test: test_restart_native_command.py | structure |
| `src/tests/test_runtime_paths.py` | 1 | tests | Test: test_runtime_paths.py | structure |
| `src/tests/test_session_keys.py` | 1 | tests | Test: test_session_keys.py | structure |
| `src/tests/test_shared_media.py` | 1 | tests | Test: test_shared_media.py | structure |
| `src/tests/test_shell_panes.py` | 1 | tests | Test: test_shell_panes.py | structure |
| `src/tests/test_shell_workspaces.py` | 1 | tests | Test: test_shell_workspaces.py | structure |
| `src/tests/test_slash_palette_selection_consistency.py` | 1 | tests | Test: test_slash_palette_selection_consistency.py | structure |
| `src/tests/test_stamp_auth_session_project.py` | 1 | tests | Test: test_stamp_auth_session_project.py | structure |
| `src/tests/test_starred_agent_removal.py` | 1 | tests | Test: test_starred_agent_removal.py | structure |
| `src/tests/test_starred_project.py` | 1 | tests | Test: test_starred_project.py | structure |
| `src/tests/test_starred_slash.py` | 1 | tests | Test: test_starred_slash.py | structure |
| `src/tests/test_stop_refresh_live_status.py` | 1 | tests | Test: test_stop_refresh_live_status.py | structure |
| `src/tests/test_subagents.py` | 1 | tests | Test: test_subagents.py | structure |
| `src/tests/test_subagents_ui.py` | 1 | tests | Test: test_subagents_ui.py | structure |
| `src/tests/test_supervised_coordinator.py` | 1 | tests | Test: test_supervised_coordinator.py | structure |
| `src/tests/test_supervised_forensics.py` | 1 | tests | Test: test_supervised_forensics.py | structure |
| `src/tests/test_supervised_hardening.py` | 1 | tests | Test: test_supervised_hardening.py | structure |
| `src/tests/test_supervised_presentation.py` | 1 | tests | Test: test_supervised_presentation.py | structure |
| `src/tests/test_system_chat_notices.py` | 1 | tests | Test: test_system_chat_notices.py | structure |
| `src/tests/test_token_tracking.py` | 1 | tests | Test: test_token_tracking.py | structure |
| `src/tests/test_ui_layout_apps.py` | 1 | tests | Test: test_ui_layout_apps.py | structure |
| `src/tests/test_user_badge_snapshot.py` | 1 | tests | Test: test_user_badge_snapshot.py | structure |
| `src/tests/test_user_parallelization_scenario.py` | 1 | tests | Test: test_user_parallelization_scenario.py | structure |
| `src/tests/test_video_playlists.py` | 1 | tests | Test: test_video_playlists.py | structure |
| `src/tests/test_web_terminal_access.py` | 1 | tests | Test: test_web_terminal_access.py | structure |
| `src/tests/test_widgets_cli.py` | 1 | tests | Test: test_widgets_cli.py | structure |
| `src/tests/test_working_bubble_badge_chat_switch.py` | 1 | tests | Test: test_working_bubble_badge_chat_switch.py | structure |
| `src/tests/unit/__init__.py` | 1 | tests | Test: __init__.py | structure |
| `src/tests/unit/bot_config.json` | 1 | tests | Test: bot_config.json | inventoried |
| `src/tests/unit/test_api_key.py` | 1 | tests | Test: test_api_key.py | structure |
| `src/tests/unit/test_claude_code.py` | 1 | tests | Test: test_claude_code.py | structure |
| `src/tests/unit/test_discord_integration.py` | 1 | tests | Test: test_discord_integration.py | structure |
| `src/tests/unit/test_markdown_skills.py` | 1 | tests | Test: test_markdown_skills.py | structure |
| `src/tests/unit/test_openai_connection.py` | 1 | tests | Test: test_openai_connection.py | structure |
| `src/tests/unit/test_remote_agent.py` | 1 | tests | Test: test_remote_agent.py | structure |
| `src/tests/unit/test_sandbox_policy.py` | 1 | tests | Test: test_sandbox_policy.py | structure |
| `src/tests/unit/test_security.py` | 1 | tests | Test: test_security.py | structure |
| `src/tests/unit/test_security_standalone.py` | 1 | tests | Test: test_security_standalone.py | structure |
| `src/tests/unit/test_username_case.py` | 1 | tests | Test: test_username_case.py | structure |
| `src/tests/unit/test_username_case_standalone.py` | 1 | tests | Test: test_username_case_standalone.py | structure |
| `src/tests/unit/test_window_focus.py` | 1 | tests | Test: test_window_focus.py | structure |
| `src/tools/TOOLS_README.md` | 1 | tools | Markdown (TOOLS_README.md) | inventoried |
| `src/tools/__init__.py` | 1 | tools | Package init (tools) | structure |
| `src/tools/comfyui/__init__.py` | 1 | tools | Package init (comfyui) | structure |
| `src/tools/comfyui/comfyui_client.py` | 1 | tools | Python module (comfyui/comfyui_client.py) | structure |
| `src/tools/comfyui/comfyui_mcp.py` | 1 | tools | Python module (comfyui/comfyui_mcp.py) | structure |
| `src/tools/govee/__init__.py` | 1 | tools | Package init (govee) | structure |
| `src/tools/govee/govee_api.py` | 1 | tools | Python module (govee/govee_api.py) | structure |
| `src/tools/govee/govee_screen_sync.py` | 1 | tools | Python module (govee/govee_screen_sync.py) | structure |
| `src/tools/govee/govee_v2.py` | 1 | tools | Python module (govee/govee_v2.py) | structure |
| `src/tools/ocr/__init__.py` | 1 | tools | Package init (ocr) | structure |
| `src/tools/ocr/ocr_manager.py` | 1 | tools | Python module (ocr/ocr_manager.py) | structure |
| `src/tools/web_search.py` | 1 | tools | Python module (tools/web_search.py) | structure |
| `src/web/about_page.html` | 1 | web-html | HTML page (about_page.html) | inventoried |
| `src/web/app_shell.html` | 1 | web-html | HTML page (app_shell.html) | inventoried |
| `src/web/apps_page.html` | 1 | web-html | HTML page (apps_page.html) | inventoried |
| `src/web/chat_page.html` | 1 | web-html | HTML page (chat_page.html) | inventoried |
| `src/web/control_panel.html` | 1 | web-html | HTML page (control_panel.html) | inventoried |
| `src/web/css/app_shell.css` | 1 | web-html | Stylesheet (app_shell.css) | inventoried |
| `src/web/css/apps_page.css` | 1 | web-html | Stylesheet (apps_page.css) | inventoried |
| `src/web/css/auth_modal.css` | 1 | web-html | Stylesheet (auth_modal.css) | inventoried |
| `src/web/css/chat_lightbox.css` | 1 | web-html | Stylesheet (chat_lightbox.css) | inventoried |
| `src/web/css/chat_page.css` | 1 | web-html | Stylesheet (chat_page.css) | inventoried |
| `src/web/css/compact_page.css` | 1 | web-html | Stylesheet (compact_page.css) | inventoried |
| `src/web/css/dashboards_page.css` | 1 | web-html | Stylesheet (dashboards_page.css) | inventoried |
| `src/web/css/git_commit_viewer.css` | 1 | web-html | Stylesheet (git_commit_viewer.css) | inventoried |
| `src/web/css/git_graph_page.css` | 1 | web-html | Stylesheet (git_graph_page.css) | inventoried |
| `src/web/css/landing_page.css` | 1 | web-html | Stylesheet (landing_page.css) | inventoried |
| `src/web/css/media_player.css` | 1 | web-html | Stylesheet (media_player.css) | inventoried |
| `src/web/css/pending_changes.css` | 1 | web-html | Stylesheet (pending_changes.css) | inventoried |
| `src/web/css/query_log_inspector.css` | 1 | web-html | Stylesheet (query_log_inspector.css) | inventoried |
| `src/web/css/router_editor.css` | 1 | web-html | Stylesheet (router_editor.css) | inventoried |
| `src/web/css/safe_area.css` | 1 | web-html | Stylesheet (safe_area.css) | inventoried |
| `src/web/css/shared_navigation.css` | 1 | web-html | Stylesheet (shared_navigation.css) | inventoried |
| `src/web/css/task_management.css` | 1 | web-html | Stylesheet (task_management.css) | inventoried |
| `src/web/css/terminal_page.css` | 1 | web-html | Stylesheet (terminal_page.css) | inventoried |
| `src/web/css/themes.css` | 1 | web-html | Stylesheet (themes.css) | inventoried |
| `src/web/css/ui_boot.css` | 1 | web-html | Stylesheet (ui_boot.css) | inventoried |
| `src/web/css/video_background.css` | 1 | web-html | Stylesheet (video_background.css) | inventoried |
| `src/web/dashboards_page.html` | 1 | web-html | HTML page (dashboards_page.html) | inventoried |
| `src/web/git_graph_page.html` | 1 | web-html | HTML page (git_graph_page.html) | inventoried |
| `src/web/git_ui.html` | 1 | web-html | HTML page (git_ui.html) | inventoried |
| `src/web/home_automation.html` | 1 | web-html | HTML page (home_automation.html) | inventoried |
| `src/web/job_insight.html` | 1 | web-html | HTML page (job_insight.html) | execution |
| `src/web/jobs_page.html` | 1 | web-html | HTML page (jobs_page.html) | execution |
| `src/web/js/app_shell.js` | 1 | web-js | Browser JS (app_shell.js) | partial-execution |
| `src/web/js/apps_page.js` | 1 | web-js | Browser JS (apps_page.js) | structure |
| `src/web/js/auth.js` | 1 | web-js | Browser JS (auth.js) | structure |
| `src/web/js/background_effect_blend.js` | 1 | web-js | Browser JS (background_effect_blend.js) | structure |
| `src/web/js/chat_find.js` | 1 | web-js | Browser JS (chat_find.js) | structure |
| `src/web/js/chat_page.js` | 1 | web-js | Browser JS (chat_page.js) | structure |
| `src/web/js/chat_widgets.js` | 1 | web-js | Browser JS (chat_widgets.js) | structure |
| `src/web/js/cron_countdown.js` | 1 | web-js | Browser JS (cron_countdown.js) | structure |
| `src/web/js/dashboards_page.js` | 1 | web-js | Browser JS (dashboards_page.js) | structure |
| `src/web/js/desktop_update_policy.js` | 1 | web-js | Browser JS (desktop_update_policy.js) | structure |
| `src/web/js/emoticons.js` | 1 | web-js | Browser JS (emoticons.js) | structure |
| `src/web/js/force_reload.js` | 1 | web-js | Browser JS (force_reload.js) | structure |
| `src/web/js/git_commit_viewer.js` | 1 | web-js | Browser JS (git_commit_viewer.js) | structure |
| `src/web/js/git_graph_page.js` | 1 | web-js | Browser JS (git_graph_page.js) | structure |
| `src/web/js/git_ui.js` | 1 | web-js | Browser JS (git_ui.js) | structure |
| `src/web/js/media_player.js` | 1 | web-js | Browser JS (media_player.js) | structure |
| `src/web/js/net_debug.js` | 1 | web-js | Browser JS (net_debug.js) | structure |
| `src/web/js/optional_cdn.js` | 1 | web-js | Browser JS (optional_cdn.js) | structure |
| `src/web/js/pending_changes_panel.js` | 1 | web-js | Browser JS (pending_changes_panel.js) | structure |
| `src/web/js/query_log_inspector.js` | 1 | web-js | Browser JS (query_log_inspector.js) | structure |
| `src/web/js/router_editor.js` | 1 | web-js | Browser JS (router_editor.js) | structure |
| `src/web/js/shared_navigation.js` | 1 | web-js | Browser JS (shared_navigation.js) | structure |
| `src/web/js/supervised_control.js` | 1 | web-js | Browser JS (supervised_control.js) | structure |
| `src/web/js/task_management.js` | 1 | web-js | Browser JS (task_management.js) | structure |
| `src/web/js/terminal_page.js` | 1 | web-js | Browser JS (terminal_page.js) | structure |
| `src/web/js/toast.js` | 1 | web-js | Browser JS (toast.js) | structure |
| `src/web/js/ui_boot.js` | 1 | web-js | Browser JS (ui_boot.js) | structure |
| `src/web/js/video_background.js` | 1 | web-js | Browser JS (video_background.js) | structure |
| `src/web/js/youtube_id.js` | 1 | web-js | Browser JS (youtube_id.js) | structure |
| `src/web/landing_page.html` | 1 | web-html | HTML page (landing_page.html) | inventoried |
| `src/web/landing_page_backup.html` | 5 | web-html | Unserved HTML backup of landing; no Flask route | inventoried |
| `src/web/media_player.html` | 1 | web-html | HTML page (media_player.html) | inventoried |
| `src/web/query_log.html` | 1 | web-html | HTML page (query_log.html) | inventoried |
| `src/web/router_editor.html` | 1 | web-html | HTML page (router_editor.html) | inventoried |
| `src/web/settings_page.html` | 1 | web-html | HTML page (settings_page.html) | inventoried |
| `src/web/sounds/completion-chirp.wav` | 1 | web-html | Tracked file (completion-chirp.wav) | inventoried |
| `src/web/task_management.html` | 1 | web-html | HTML page (task_management.html) | inventoried |
| `src/web/terminal_page.html` | 1 | web-html | HTML page (terminal_page.html) | inventoried |
| `src/web/test_reports.html` | 1 | web-html | HTML page (test_reports.html) | inventoried |
| `src/web/tools_page.html` | 1 | web-html | HTML page (tools_page.html) | inventoried |
| `src/web/wizard_page.html` | 1 | web-html | HTML page (wizard_page.html) | inventoried |
| `start_cuttle.sh` | 1 | repo-root | Launcher/script (start_cuttle.sh) | execution |
| `start_electron.bat` | 1 | repo-root | Launcher/script (start_electron.bat) | inventoried |
| `vendor/claw-code` | 1 | vendor | Tracked file (claw-code) | inventoried |
| `vendor/mcp-govee` | 1 | vendor | Tracked file (mcp-govee) | inventoried |
