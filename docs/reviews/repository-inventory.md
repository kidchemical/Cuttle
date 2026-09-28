# Repository inventory

**Generated:** 2026-09-27T17:22:30-07:00 (baseline) / inventory written same session.
**HEAD:** `9ecd388`
**Working tree:** clean (`git status --short --untracked-files=all` empty).

## Baseline reconciliation

| Metric | Count | Source |
|---|---|---|
| Tracked files | 914 | `git ls-files \| wc -l` |
| Untracked (not ignored) | 0 | `git ls-files --others --exclude-standard` |
| Ignored others | 72628 | `git ls-files --others --ignored --exclude-standard` |
| Status short lines | 0 | `git status --short --untracked-files=all` |
| Inventory rows below | 914 | same `git ls-files` |

Top-level tracked counts (must sum to 914): `src` 611, `apps` 147, `.cuttle` 93, `docs` 18, `electron` 16, `.cursor` 14, `vendor` 2, `.claude` 2, repo-root files 11.

Ignored 72628 is dominated by `.venv` (~53763), `electron/node_modules`+dist (~8748), `src` caches/output (~6057), `apps` Gradle/build (~3313), `temp/` (~719). Those trees were classified as directories, not file-by-file.

## Git status legend

| Status | Meaning |
|---|---|
| tracked | In Git index |
| ignored | Matches `.gitignore` (Phase 1B) |
| untracked | Would show in `git status` without ignore — **none** at baseline |

## Classification codes (Phase 1 / 1B)

1 Active source · 2 Legitimate local config · 3 Runtime state · 4 Regenerable artifact · 5 Abandoned artifact · 6 Sensitive · 7 Uncertain

Review status for tracked trees: **inventoried**. Line-by-line review of every `.py` is **not** claimed; see `repository-audit.md` coverage.

## Phase 1B — ignored / local (directory classification)

No secrets contents are reproduced. Paths that typically hold secrets are named only.

| Path (this machine) | Git | Class | Notes |
|---|---|---|---|
| `.venv/` | ignored | 4 | pip install from `src/requirements/requirements.txt`; **required** for run, not for clone |
| `src/.env` | ignored | 6 | Copy from `src/.env.example` (tracked) |
| `src/settings.json` | ignored (`*.json`) | 2+3 | Created with defaults by `SettingsManager` if missing; this install has a 13k customized copy |
| `src/data/db/*.db`, `action_hmac_secret` | ignored | 3+6 | Created at runtime |
| `.cuttle/certs/` | ignored | 3+6 | Generated HTTPS certs |
| `.cuttle/personal/` | ignored | 2 | Install overlay; README tracked via negation |
| `.cuttle/keys/*` except `*.pub` | ignored | 6 | Mesh private key local; `cuttle_mesh_lan.pub` tracked |
| `_personal/` | ignored | 2+6 | Home-lab dogfood (Instacart/Expedia docs — credentials files present, not quoted) |
| `electron/node_modules/`, `electron/dist/` | ignored | 4 | `npm install` in `electron/` |
| `apps/mobile/android/.gradle`, `**/build/` | ignored | 4 | Android build |
| `src/output/`, `output/`, `src/cache/` | ignored | 3+4 | Uploads, generated media |
| `temp/` | ignored | 4+5 | Promo shots, Playwright-ish profiles, `WAG-EMS` git backup — **dev machine scratch** |
| Repo-root `cuttle_flask_restart_*.json`, `cuttle_*_queue.jsonl` | ignored | 3 | Daemon/Flask restart + toast queues |
| `vendor/claw-code/` **contents** | ignored by `/vendor/claw-code/` | 4+7 | Git records a gitlink; clone contents not redistributed |
| `vendor/mcp-govee` | submodule | 1 | `.gitmodules`; needs `git submodule update --init` |
| `docs/media/promo/` | ignored | 4 | Regenerable promo renders |
| `cuttle_flask_restart_status.json` at repo root | ignored | 3 | Present on this host |

### Clean-install vs this machine

A fresh clone **does not** need: `_personal/`, `temp/`, this host's `settings.json` contents, DBs, certs, `.venv` (recreated), Electron `node_modules` (if not using desktop), Gradle caches.

A fresh clone **does** need documented steps: venv + requirements, `src/.env` from example, optional `git submodule update --init` for Govee MCP, optional `npm install` in `electron/`. Runtime will mint settings.json, certs, HMAC secret, SQLite.

**Reproducibility issues (not cleanliness):** see audit — README points Flask-alone at `start_api_server.py` which starts **port 5000 time-series**, not 8080; `AGENTS.md` still calls `_execute_remote_agent_tool` the core dispatch.

## Tracked files by prefix

### `apps/mobile` (94)

- `apps/mobile/.gitignore`
- `apps/mobile/README.md`
- `apps/mobile/android/.gitignore`
- `apps/mobile/android/app/.gitignore`
- `apps/mobile/android/app/build.gradle`
- `apps/mobile/android/app/capacitor.build.gradle`
- `apps/mobile/android/app/proguard-rules.pro`
- `apps/mobile/android/app/src/androidTest/java/com/getcapacitor/myapp/ExampleInstrumentedTest.java`
- `apps/mobile/android/app/src/main/AndroidManifest.xml`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/MainActivity.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/ActionReceiver.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/BootReceiver.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/CuttleApi.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/CuttleNotifications.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/EventStreamService.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/NetworkUtil.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/Notifier.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/NotifyController.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/NotifyPrefs.java`
- `apps/mobile/android/app/src/main/java/com/cuttle/mobile/notify/ShellUpdate.java`
- `apps/mobile/android/app/src/main/res/drawable-land-hdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-land-mdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-land-xhdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-land-xxhdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-land-xxxhdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-port-hdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-port-mdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-port-xhdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-port-xxhdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-port-xxxhdpi/splash.png`
- `apps/mobile/android/app/src/main/res/drawable-v24/ic_launcher_foreground.xml`
- `apps/mobile/android/app/src/main/res/drawable/ic_launcher_background.xml`
- `apps/mobile/android/app/src/main/res/drawable/splash.png`
- `apps/mobile/android/app/src/main/res/layout/activity_main.xml`
- `apps/mobile/android/app/src/main/res/mipmap-anydpi-v26/ic_launcher.xml`
- `apps/mobile/android/app/src/main/res/mipmap-anydpi-v26/ic_launcher_round.xml`
- `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher.png`
- `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher_foreground.png`
- `apps/mobile/android/app/src/main/res/mipmap-hdpi/ic_launcher_round.png`
- `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher.png`
- `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher_foreground.png`
- `apps/mobile/android/app/src/main/res/mipmap-mdpi/ic_launcher_round.png`
- `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher.png`
- `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher_foreground.png`
- `apps/mobile/android/app/src/main/res/mipmap-xhdpi/ic_launcher_round.png`
- `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher.png`
- `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher_foreground.png`
- `apps/mobile/android/app/src/main/res/mipmap-xxhdpi/ic_launcher_round.png`
- `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher.png`
- `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_foreground.png`
- `apps/mobile/android/app/src/main/res/mipmap-xxxhdpi/ic_launcher_round.png`
- `apps/mobile/android/app/src/main/res/values/ic_launcher_background.xml`
- `apps/mobile/android/app/src/main/res/values/strings.xml`
- `apps/mobile/android/app/src/main/res/values/styles.xml`
- `apps/mobile/android/app/src/main/res/xml/file_paths.xml`
- `apps/mobile/android/app/src/main/res/xml/network_security_config.xml`
- `apps/mobile/android/app/src/test/java/com/getcapacitor/myapp/ExampleUnitTest.java`
- `apps/mobile/android/build.gradle`
- `apps/mobile/android/capacitor.settings.gradle`
- `apps/mobile/android/gradle.properties`
- `apps/mobile/android/gradle/wrapper/gradle-wrapper.jar`
- `apps/mobile/android/gradle/wrapper/gradle-wrapper.properties`
- `apps/mobile/android/gradlew`
- `apps/mobile/android/gradlew.bat`
- `apps/mobile/android/local.properties.example`
- `apps/mobile/android/settings.gradle`
- `apps/mobile/android/variables.gradle`
- `apps/mobile/assets/icon/chat-avatar.png`
- `apps/mobile/assets/icon/foreground-432.png`
- `apps/mobile/assets/icon/icon-1024.png`
- `apps/mobile/assets/icon/mascot.png`
- `apps/mobile/build-android-debug.bat`
- `apps/mobile/capacitor.config.ts`
- `apps/mobile/index.html`
- `apps/mobile/ios/.gitignore`
- `apps/mobile/ios/App/App.xcodeproj/project.pbxproj`
- `apps/mobile/ios/App/App.xcworkspace/xcshareddata/IDEWorkspaceChecks.plist`
- `apps/mobile/ios/App/App/AppDelegate.swift`
- `apps/mobile/ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png`
- `apps/mobile/ios/App/App/Assets.xcassets/Splash.imageset/splash-2732x2732-1.png`
- `apps/mobile/ios/App/App/Assets.xcassets/Splash.imageset/splash-2732x2732-2.png`
- `apps/mobile/ios/App/App/Assets.xcassets/Splash.imageset/splash-2732x2732.png`
- `apps/mobile/ios/App/App/Base.lproj/LaunchScreen.storyboard`
- `apps/mobile/ios/App/App/Base.lproj/Main.storyboard`
- `apps/mobile/ios/App/App/CuttleApi.swift`
- `apps/mobile/ios/App/App/CuttleEventStream.swift`
- `apps/mobile/ios/App/App/CuttleNotifications.swift`
- `apps/mobile/ios/App/App/CuttleNotifyPrefs.swift`
- `apps/mobile/ios/App/App/Info.plist`
- `apps/mobile/ios/App/Podfile`
- `apps/mobile/scripts/generate_icons.py`
- `apps/mobile/src/main.js`
- `apps/mobile/src/setup.css`
- `apps/mobile/vite.config.js`

### `src/api/agent_router` (47)

- `src/api/agent_router/__init__.py`
- `src/api/agent_router/commands.py`
- `src/api/agent_router/config.py`
- `src/api/agent_router/dispatch.py`
- `src/api/agent_router/drift.py`
- `src/api/agent_router/engine.py`
- `src/api/agent_router/eval/__init__.py`
- `src/api/agent_router/eval/report.py`
- `src/api/agent_router/eval/runner.py`
- `src/api/agent_router/eval/scoring.py`
- `src/api/agent_router/eval/stability.py`
- `src/api/agent_router/eval/suites.py`
- `src/api/agent_router/frustration.py`
- `src/api/agent_router/integration.py`
- `src/api/agent_router/logging_events.py`
- `src/api/agent_router/outcomes.py`
- `src/api/agent_router/pinned_outcomes.py`
- `src/api/agent_router/policy.py`
- `src/api/agent_router/providers/__init__.py`
- `src/api/agent_router/providers/agent.py`
- `src/api/agent_router/providers/api_openai.py`
- `src/api/agent_router/providers/base.py`
- `src/api/agent_router/providers/jev.py`
- `src/api/agent_router/providers/local.py`
- `src/api/agent_router/rage_investigator.py`
- `src/api/agent_router/registry.py`
- `src/api/agent_router/repeats.py`
- `src/api/agent_router/suites/baseline.json`
- `src/api/agent_router/supervised/__init__.py`
- `src/api/agent_router/supervised/adapters.py`
- `src/api/agent_router/supervised/bubble.py`
- `src/api/agent_router/supervised/commands.py`
- `src/api/agent_router/supervised/control.py`
- `src/api/agent_router/supervised/delivery.py`
- `src/api/agent_router/supervised/events.py`
- `src/api/agent_router/supervised/evidence.py`
- `src/api/agent_router/supervised/helpers.py`
- `src/api/agent_router/supervised/orchestrator.py`
- `src/api/agent_router/supervised/packet.py`
- `src/api/agent_router/supervised/policy_hooks.py`
- `src/api/agent_router/supervised/profiles.py`
- `src/api/agent_router/supervised/store.py`
- `src/api/agent_router/supervised/test_isolation.py`
- `src/api/agent_router/supervised/types.py`
- `src/api/agent_router/supervised/verification.py`
- `src/api/agent_router/types.py`
- `src/api/agent_router/use_cases.py`

### `src/api/agent_harness` (43)

- `src/api/agent_harness/ADDING_AN_AGENT.md`
- `src/api/agent_harness/__init__.py`
- `src/api/agent_harness/activity.py`
- `src/api/agent_harness/agent_defaults.py`
- `src/api/agent_harness/agents/__init__.py`
- `src/api/agent_harness/agents/antigravity/__init__.py`
- `src/api/agent_harness/agents/antigravity/adapter.py`
- `src/api/agent_harness/agents/antigravity/manifest.yaml`
- `src/api/agent_harness/agents/antigravity/session_store.py`
- `src/api/agent_harness/agents/claude/__init__.py`
- `src/api/agent_harness/agents/claude/adapter.py`
- `src/api/agent_harness/agents/claude/manifest.yaml`
- `src/api/agent_harness/agents/codex/__init__.py`
- `src/api/agent_harness/agents/codex/adapter.py`
- `src/api/agent_harness/agents/codex/manifest.yaml`
- `src/api/agent_harness/agents/codex/model_catalog.py`
- `src/api/agent_harness/agents/cursor/__init__.py`
- `src/api/agent_harness/agents/cursor/adapter.py`
- `src/api/agent_harness/agents/cursor/manifest.yaml`
- `src/api/agent_harness/agents/deepseek/__init__.py`
- `src/api/agent_harness/agents/deepseek/adapter.py`
- `src/api/agent_harness/agents/deepseek/manifest.yaml`
- `src/api/agent_harness/agents/hermes/__init__.py`
- `src/api/agent_harness/agents/hermes/adapter.py`
- `src/api/agent_harness/agents/hermes/manifest.yaml`
- `src/api/agent_harness/agents/muse/__init__.py`
- `src/api/agent_harness/agents/muse/adapter.py`
- `src/api/agent_harness/agents/muse/manifest.yaml`
- `src/api/agent_harness/agents/opencode/__init__.py`
- `src/api/agent_harness/agents/opencode/adapter.py`
- `src/api/agent_harness/agents/opencode/manifest.yaml`
- `src/api/agent_harness/agents/opencode/model_catalog.py`
- `src/api/agent_harness/agents/opencode/session_store.py`
- `src/api/agent_harness/catalog.py`
- `src/api/agent_harness/cwd.py`
- `src/api/agent_harness/installer.py`
- `src/api/agent_harness/kernel.py`
- `src/api/agent_harness/model_capabilities.py`
- `src/api/agent_harness/smoke_policy.py`
- `src/api/agent_harness/steer.py`
- `src/api/agent_harness/timeouts.py`
- `src/api/agent_harness/types.py`
- `src/api/agent_harness/win_cli.py`

### `.cuttle/scripts` (37)

- `.cuttle/scripts/.gitkeep`
- `.cuttle/scripts/bump-cuttle-version.ps1`
- `.cuttle/scripts/cleanup-idle-sessions.ps1`
- `.cuttle/scripts/cleanup-idle-sessions.py`
- `.cuttle/scripts/client-self-update.ps1`
- `.cuttle/scripts/client-self-update.sh`
- `.cuttle/scripts/comfyui-start.ps1`
- `.cuttle/scripts/comfyui-start.sh`
- `.cuttle/scripts/download-trellis2-models.ps1`
- `.cuttle/scripts/electron-sandbox.sh`
- `.cuttle/scripts/flask-health.ps1`
- `.cuttle/scripts/flask-health.py`
- `.cuttle/scripts/git-push.ps1`
- `.cuttle/scripts/git-push.py`
- `.cuttle/scripts/gitea_cli.py`
- `.cuttle/scripts/host-electron-restart.ps1`
- `.cuttle/scripts/host-electron-restart.sh`
- `.cuttle/scripts/install-comfyui-trellis-py311.ps1`
- `.cuttle/scripts/install-comfyui-trellis2.ps1`
- `.cuttle/scripts/install-cuttle-mesh-lan-key.ps1`
- `.cuttle/scripts/launch-cuttle-client.sh`
- `.cuttle/scripts/launch-cuttle-host.sh`
- `.cuttle/scripts/muse.cmd`
- `.cuttle/scripts/opencode_sync_auth.py`
- `.cuttle/scripts/playwright-mcp.cmd`
- `.cuttle/scripts/publish-trellis-download-status.ps1`
- `.cuttle/scripts/restart-daemon.ps1`
- `.cuttle/scripts/restart-daemon.sh`
- `.cuttle/scripts/restart-flask-worker.ps1`
- `.cuttle/scripts/restart-flask.ps1`
- `.cuttle/scripts/restart-flask.py`
- `.cuttle/scripts/setup-tailscale-ssh-verify.ps1`
- `.cuttle/scripts/setup-tailscale-ssh.ps1`
- `.cuttle/scripts/start-cuttle.cmd`
- `.cuttle/scripts/trellis-download-status.ps1`
- `.cuttle/scripts/workers-cli.ps1`
- `.cuttle/scripts/workers-cli.sh`

### `apps/android_bt_voice` (37)

- `apps/android_bt_voice/.gradle/7.5/checksums/checksums.lock`
- `apps/android_bt_voice/.gradle/7.5/checksums/md5-checksums.bin`
- `apps/android_bt_voice/.gradle/7.5/checksums/sha1-checksums.bin`
- `apps/android_bt_voice/.gradle/7.5/dependencies-accessors/dependencies-accessors.lock`
- `apps/android_bt_voice/.gradle/7.5/dependencies-accessors/gc.properties`
- `apps/android_bt_voice/.gradle/7.5/fileChanges/last-build.bin`
- `apps/android_bt_voice/.gradle/7.5/fileHashes/fileHashes.lock`
- `apps/android_bt_voice/.gradle/7.5/gc.properties`
- `apps/android_bt_voice/.gradle/8.2/checksums/checksums.lock`
- `apps/android_bt_voice/.gradle/8.2/checksums/md5-checksums.bin`
- `apps/android_bt_voice/.gradle/8.2/checksums/sha1-checksums.bin`
- `apps/android_bt_voice/.gradle/8.2/dependencies-accessors/dependencies-accessors.lock`
- `apps/android_bt_voice/.gradle/8.2/dependencies-accessors/gc.properties`
- `apps/android_bt_voice/.gradle/8.2/fileChanges/last-build.bin`
- `apps/android_bt_voice/.gradle/8.2/fileHashes/fileHashes.lock`
- `apps/android_bt_voice/.gradle/8.2/gc.properties`
- `apps/android_bt_voice/.gradle/buildOutputCleanup/buildOutputCleanup.lock`
- `apps/android_bt_voice/.gradle/buildOutputCleanup/cache.properties`
- `apps/android_bt_voice/.gradle/buildOutputCleanup/outputFiles.bin`
- `apps/android_bt_voice/.gradle/vcs-1/gc.properties`
- `apps/android_bt_voice/README.md`
- `apps/android_bt_voice/app/build.gradle.kts`
- `apps/android_bt_voice/app/proguard-rules.pro`
- `apps/android_bt_voice/app/src/main/AndroidManifest.xml`
- `apps/android_bt_voice/app/src/main/java/com/cuttle/androidbtvoice/MainActivity.kt`
- `apps/android_bt_voice/app/src/main/res/drawable/ic_launcher.xml`
- `apps/android_bt_voice/app/src/main/res/layout/activity_main.xml`
- `apps/android_bt_voice/app/src/main/res/values/colors.xml`
- `apps/android_bt_voice/app/src/main/res/values/strings.xml`
- `apps/android_bt_voice/app/src/main/res/values/themes.xml`
- `apps/android_bt_voice/build.gradle.kts`
- `apps/android_bt_voice/gradle.properties`
- `apps/android_bt_voice/gradle/wrapper/gradle-wrapper.jar`
- `apps/android_bt_voice/gradle/wrapper/gradle-wrapper.properties`
- `apps/android_bt_voice/gradlew`
- `apps/android_bt_voice/gradlew.bat`
- `apps/android_bt_voice/settings.gradle.kts`

### `src/scripts/utilities` (33)

- `src/scripts/utilities/README.md`
- `src/scripts/utilities/agent_process.py`
- `src/scripts/utilities/claude_cli_session_store.py`
- `src/scripts/utilities/claude_cli_tool.py`
- `src/scripts/utilities/claude_code_tool.py`
- `src/scripts/utilities/claw_code_harness.py`
- `src/scripts/utilities/codex_app_server.py`
- `src/scripts/utilities/codex_app_server_turn.py`
- `src/scripts/utilities/codex_cli_session_store.py`
- `src/scripts/utilities/codex_cli_tool.py`
- `src/scripts/utilities/cursor_cli_session_store.py`
- `src/scripts/utilities/cursor_cli_tool.py`
- `src/scripts/utilities/debug_cursor_location.py`
- `src/scripts/utilities/example_project_setup.py`
- `src/scripts/utilities/fix_project_paths.py`
- `src/scripts/utilities/git_credential_helper.py`
- `src/scripts/utilities/git_graph.py`
- `src/scripts/utilities/git_pending_changes.py`
- `src/scripts/utilities/hello_world.py`
- `src/scripts/utilities/hermes_cli_session_store.py`
- `src/scripts/utilities/hermes_cli_tool.py`
- `src/scripts/utilities/kill_bots.py`
- `src/scripts/utilities/migrate_project_paths.py`
- `src/scripts/utilities/muse_cli_session_store.py`
- `src/scripts/utilities/muse_cli_tool.py`
- `src/scripts/utilities/muse_serve_turn.py`
- `src/scripts/utilities/promo_sculpture.py`
- `src/scripts/utilities/promo_video.py`
- `src/scripts/utilities/readme_gifs.py`
- `src/scripts/utilities/readme_promo.py`
- `src/scripts/utilities/readme_screenshots.py`
- `src/scripts/utilities/stdio_rpc.py`
- `src/scripts/utilities/update_project_paths.py`

### `src/web/js` (29)

- `src/web/js/app_shell.js`
- `src/web/js/apps_page.js`
- `src/web/js/auth.js`
- `src/web/js/background_effect_blend.js`
- `src/web/js/chat_find.js`
- `src/web/js/chat_page.js`
- `src/web/js/chat_widgets.js`
- `src/web/js/cron_countdown.js`
- `src/web/js/dashboards_page.js`
- `src/web/js/desktop_update_policy.js`
- `src/web/js/emoticons.js`
- `src/web/js/force_reload.js`
- `src/web/js/git_commit_viewer.js`
- `src/web/js/git_graph_page.js`
- `src/web/js/git_ui.js`
- `src/web/js/media_player.js`
- `src/web/js/net_debug.js`
- `src/web/js/optional_cdn.js`
- `src/web/js/pending_changes_panel.js`
- `src/web/js/query_log_inspector.js`
- `src/web/js/router_editor.js`
- `src/web/js/shared_navigation.js`
- `src/web/js/supervised_control.js`
- `src/web/js/task_management.js`
- `src/web/js/terminal_page.js`
- `src/web/js/toast.js`
- `src/web/js/ui_boot.js`
- `src/web/js/video_background.js`
- `src/web/js/youtube_id.js`

### `.cuttle/docs` (21)

- `.cuttle/docs/.gitkeep`
- `.cuttle/docs/action-forms.md`
- `.cuttle/docs/agent-context.md`
- `.cuttle/docs/agent-ops-cli.md`
- `.cuttle/docs/agent-router-todo.md`
- `.cuttle/docs/charts.md`
- `.cuttle/docs/chat-history.md`
- `.cuttle/docs/chat-media.md`
- `.cuttle/docs/comfyui-trellis2.md`
- `.cuttle/docs/commands-and-actions.md`
- `.cuttle/docs/cursor-plan-bridge.md`
- `.cuttle/docs/cuttle-jobs.md`
- `.cuttle/docs/cuttle-workers.md`
- `.cuttle/docs/dashboards.md`
- `.cuttle/docs/discord.md`
- `.cuttle/docs/git.md`
- `.cuttle/docs/gitea.md`
- `.cuttle/docs/headless-turns.md`
- `.cuttle/docs/jev.md`
- `.cuttle/docs/subagents.md`
- `.cuttle/docs/widgets.md`

### `src/web/css` (21)

- `src/web/css/app_shell.css`
- `src/web/css/apps_page.css`
- `src/web/css/auth_modal.css`
- `src/web/css/chat_lightbox.css`
- `src/web/css/chat_page.css`
- `src/web/css/compact_page.css`
- `src/web/css/dashboards_page.css`
- `src/web/css/git_commit_viewer.css`
- `src/web/css/git_graph_page.css`
- `src/web/css/landing_page.css`
- `src/web/css/media_player.css`
- `src/web/css/pending_changes.css`
- `src/web/css/query_log_inspector.css`
- `src/web/css/router_editor.css`
- `src/web/css/safe_area.css`
- `src/web/css/shared_navigation.css`
- `src/web/css/task_management.css`
- `src/web/css/terminal_page.css`
- `src/web/css/themes.css`
- `src/web/css/ui_boot.css`
- `src/web/css/video_background.css`

### `.cuttle/actions` (17)

- `.cuttle/actions/.gitkeep`
- `.cuttle/actions/comfyui-start.yaml`
- `.cuttle/actions/electron-host-restart.yaml`
- `.cuttle/actions/flask-health.yaml`
- `.cuttle/actions/flask-restart.yaml`
- `.cuttle/actions/git-push.yaml`
- `.cuttle/actions/opencode-sync-auth.yaml`
- `.cuttle/actions/sessions-cleanup-idle.yaml`
- `.cuttle/actions/workers-blender-shard.yaml`
- `.cuttle/actions/workers-cancel.yaml`
- `.cuttle/actions/workers-list.yaml`
- `.cuttle/actions/workers-plan.yaml`
- `.cuttle/actions/workers-self-update.yaml`
- `.cuttle/actions/workers-shell.yaml`
- `.cuttle/actions/workers-status.yaml`
- `.cuttle/actions/workers-submit.yaml`
- `.cuttle/actions/workers-wait.yaml`

### `src/api/device_workers` (17)

- `src/api/device_workers/__init__.py`
- `src/api/device_workers/__main__.py`
- `src/api/device_workers/auth.py`
- `src/api/device_workers/capabilities.py`
- `src/api/device_workers/cli.py`
- `src/api/device_workers/client.py`
- `src/api/device_workers/config.py`
- `src/api/device_workers/executor.py`
- `src/api/device_workers/intent.py`
- `src/api/device_workers/job_policy.py`
- `src/api/device_workers/long_run.py`
- `src/api/device_workers/platform.py`
- `src/api/device_workers/profiles.py`
- `src/api/device_workers/routes.py`
- `src/api/device_workers/ssh_approval.py`
- `src/api/device_workers/store.py`
- `src/api/device_workers/worker_loop.py`

### `apps/android_companion` (16)

- `apps/android_companion/README.md`
- `apps/android_companion/app/build.gradle.kts`
- `apps/android_companion/app/proguard-rules.pro`
- `apps/android_companion/app/src/main/AndroidManifest.xml`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/core/Prefs.kt`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/notify/ActionReceiver.kt`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/notify/Notifier.kt`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/sse/CuttleApi.kt`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/sse/EventStreamService.kt`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/sse/Notifications.kt`
- `apps/android_companion/app/src/main/java/com/cuttle/companion/ui/MainActivity.kt`
- `apps/android_companion/app/src/main/res/layout/activity_main.xml`
- `apps/android_companion/app/src/main/res/values/strings.xml`
- `apps/android_companion/app/src/main/res/values/themes.xml`
- `apps/android_companion/build.gradle.kts`
- `apps/android_companion/settings.gradle.kts`

### `src/tests/unit` (14)

- `src/tests/unit/__init__.py`
- `src/tests/unit/bot_config.json`
- `src/tests/unit/test_api_key.py`
- `src/tests/unit/test_claude_code.py`
- `src/tests/unit/test_discord_integration.py`
- `src/tests/unit/test_markdown_skills.py`
- `src/tests/unit/test_openai_connection.py`
- `src/tests/unit/test_remote_agent.py`
- `src/tests/unit/test_sandbox_policy.py`
- `src/tests/unit/test_security.py`
- `src/tests/unit/test_security_standalone.py`
- `src/tests/unit/test_username_case.py`
- `src/tests/unit/test_username_case_standalone.py`
- `src/tests/unit/test_window_focus.py`

### `src/api/jev` (13)

- `src/api/jev/__init__.py`
- `src/api/jev/__main__.py`
- `src/api/jev/cli.py`
- `src/api/jev/client.py`
- `src/api/jev/config.py`
- `src/api/jev/labels.py`
- `src/api/jev/rage.py`
- `src/api/jev/rank.py`
- `src/api/jev/regress.py`
- `src/api/jev/routing.py`
- `src/api/jev/thresholds.py`
- `src/api/jev/types.py`
- `src/api/jev/watch.py`

### `(repo root)` (11)

- `.gitattributes`
- `.gitignore`
- `.gitmodules`
- `AGENTS.md`
- `LICENSE.txt`
- `README.md`
- `bot_config.json`
- `build_electron.bat`
- `pytest.ini`
- `start_cuttle.sh`
- `start_electron.bat`

### `src/api/dashboards` (11)

- `src/api/dashboards/__init__.py`
- `src/api/dashboards/__main__.py`
- `src/api/dashboards/benchmarklist.py`
- `src/api/dashboards/catalog.py`
- `src/api/dashboards/cli.py`
- `src/api/dashboards/deepswe.py`
- `src/api/dashboards/http_fetch.py`
- `src/api/dashboards/integrations.py`
- `src/api/dashboards/routes.py`
- `src/api/dashboards/service.py`
- `src/api/dashboards/usage.py`

### `src/api/subagents` (11)

- `src/api/subagents/__init__.py`
- `src/api/subagents/__main__.py`
- `src/api/subagents/cli.py`
- `src/api/subagents/extras.py`
- `src/api/subagents/identity.py`
- `src/api/subagents/profiles.py`
- `src/api/subagents/service.py`
- `src/api/subagents/spec.py`
- `src/api/subagents/store.py`
- `src/api/subagents/turns.py`
- `src/api/subagents/types.py`

### `docs/guides` (8)

- `docs/guides/AGENT_ROUTER.md`
- `docs/guides/CUTTLE_WORKERS.md`
- `docs/guides/MODULARITY.md`
- `docs/guides/PAIRING_AND_ALLOWLIST.md`
- `docs/guides/REMOTE_ACCESS.md`
- `docs/guides/SESSIONS_API.md`
- `docs/guides/SUPERVISED_COORDINATOR.md`
- `docs/guides/WEB_CHAT_API.md`

### `src/api/cuttle_brain` (8)

- `src/api/cuttle_brain/CONTEXT_COMPILER.md`
- `src/api/cuttle_brain/__init__.py`
- `src/api/cuttle_brain/__main__.py`
- `src/api/cuttle_brain/cli.py`
- `src/api/cuttle_brain/context_compiler.py`
- `src/api/cuttle_brain/context_delta.py`
- `src/api/cuttle_brain/handoff.py`
- `src/api/cuttle_brain/personal_overlay.py`

### `src/api/cuttle_jobs` (8)

- `src/api/cuttle_jobs/__init__.py`
- `src/api/cuttle_jobs/client.py`
- `src/api/cuttle_jobs/commands.py`
- `src/api/cuttle_jobs/executor.py`
- `src/api/cuttle_jobs/formatting.py`
- `src/api/cuttle_jobs/status_store.py`
- `src/api/cuttle_jobs/worker.py`
- `src/api/cuttle_jobs/workspace.py`

### `.cursor/skills` (7)

- `.cursor/skills/comfyui-trellis2/SKILL.md`
- `.cursor/skills/cuttle-cleanup-sessions/SKILL.md`
- `.cursor/skills/cuttle-electron-debug/SKILL.md`
- `.cursor/skills/cuttle-project-commands/SKILL.md`
- `.cursor/skills/cuttle-roadmap/SKILL.md`
- `.cursor/skills/govee/SKILL.md`
- `.cursor/skills/local-plan-act-ollama/SKILL.md`

### `src/api/home_automation_socket` (7)

- `src/api/home_automation_socket/README.md`
- `src/api/home_automation_socket/__init__.py`
- `src/api/home_automation_socket/providers/__init__.py`
- `src/api/home_automation_socket/providers/govee.py`
- `src/api/home_automation_socket/providers/nest.py`
- `src/api/home_automation_socket/registry.py`
- `src/api/home_automation_socket/types.py`

### `docs/media` (6)

- `docs/media/chat-charts.webp`
- `docs/media/chat-hero.webp`
- `docs/media/customize.webp`
- `docs/media/dashboards.webp`
- `docs/media/hero.webp`
- `docs/media/multiplex.webp`

### `.cursor/rules` (5)

- `.cursor/rules/comfyui-trellis2.mdc`
- `.cursor/rules/cuttle-electron-debug.mdc`
- `.cursor/rules/multi-project-awareness.mdc`
- `.cursor/rules/project-guidelines.mdc`
- `.cursor/rules/restart-cuttle.mdc`

### `src/tests/e2e` (5)

- `src/tests/e2e/__init__.py`
- `src/tests/e2e/test_app_shell_navigation.py`
- `src/tests/e2e/test_chat_badge_starring.py`
- `src/tests/e2e/test_chat_history_search_panel.py`
- `src/tests/e2e/test_verify_nav_history.py`

### `.cuttle/commands` (4)

- `.cuttle/commands/README.md`
- `.cuttle/commands/cleanup-sessions.md`
- `.cuttle/commands/generate-3d.md`
- `.cuttle/commands/scaffold-cuttle.md`

### `.cuttle/learnings` (4)

- `.cuttle/learnings/ERRORS.md`
- `.cuttle/learnings/FEATURE_REQUESTS.md`
- `.cuttle/learnings/LEARNINGS.md`
- `.cuttle/learnings/README.md`

### `.cuttle/rules` (4)

- `.cuttle/rules/00-core.md`
- `.cuttle/rules/01-chat-handles.md`
- `.cuttle/rules/02-agent-ops-cli.md`
- `.cuttle/rules/03-subagents.md`

### `src/scripts/launchers` (4)

- `src/scripts/launchers/launcher_debug.py`
- `src/scripts/launchers/start_router_editor.py`
- `src/scripts/launchers/start_ungit.py`
- `src/scripts/launchers/start_web_chat.py`

### `src/tools/govee` (4)

- `src/tools/govee/__init__.py`
- `src/tools/govee/govee_api.py`
- `src/tools/govee/govee_screen_sync.py`
- `src/tools/govee/govee_v2.py`

### `src/api/chat_cli` (3)

- `src/api/chat_cli/__init__.py`
- `src/api/chat_cli/__main__.py`
- `src/api/chat_cli/cli.py`

### `src/api/discord_cli` (3)

- `src/api/discord_cli/__init__.py`
- `src/api/discord_cli/__main__.py`
- `src/api/discord_cli/cli.py`

### `src/api/edit_attribution` (3)

- `src/api/edit_attribution/__init__.py`
- `src/api/edit_attribution/journal.py`
- `src/api/edit_attribution/recorder.py`

### `src/api/gitea` (3)

- `src/api/gitea/__init__.py`
- `src/api/gitea/__main__.py`
- `src/api/gitea/cli.py`

### `src/api/panes_cli` (3)

- `src/api/panes_cli/__init__.py`
- `src/api/panes_cli/__main__.py`
- `src/api/panes_cli/cli.py`

### `src/api/widgets_cli` (3)

- `src/api/widgets_cli/__init__.py`
- `src/api/widgets_cli/__main__.py`
- `src/api/widgets_cli/cli.py`

### `src/scripts/setup` (3)

- `src/scripts/setup/fix_venv.py`
- `src/scripts/setup/install_deps_step_by_step.py`
- `src/scripts/setup/setup_env.py`

### `src/tests/integration` (3)

- `src/tests/integration/__init__.py`
- `src/tests/integration/test_discord_api_e2e.py`
- `src/tests/integration/test_discord_remote_execution.py`

### `src/tools/comfyui` (3)

- `src/tools/comfyui/__init__.py`
- `src/tools/comfyui/comfyui_client.py`
- `src/tools/comfyui/comfyui_mcp.py`

### `.claude/agents` (2)

- `.claude/agents/code-reviewer.md`
- `.claude/agents/researcher.md`

### `.cuttle/keys` (2)

- `.cuttle/keys/.gitignore`
- `.cuttle/keys/cuttle_mesh_lan.pub`

### `src/tests/fixtures` (2)

- `src/tests/fixtures/supervised_truncated_worker_report.md`
- `src/tests/fixtures/wr_d49c8d97962e.raw.txt`

### `src/tools/ocr` (2)

- `src/tools/ocr/__init__.py`
- `src/tools/ocr/ocr_manager.py`

### `.cursor/hooks` (1)

- `.cursor/hooks/block_cuttle_managed_kill.py`

### `.cursor/mcp.json` (1)

- `.cursor/mcp.json`

### `.cuttle/README.md` (1)

- `.cuttle/README.md`

### `.cuttle/agents` (1)

- `.cuttle/agents/.gitkeep`

### `.cuttle/memory` (1)

- `.cuttle/memory/.gitkeep`

### `.cuttle/personal` (1)

- `.cuttle/personal/README.md`

### `docs/README.md` (1)

- `docs/README.md`

### `docs/ROADMAP.md` (1)

- `docs/ROADMAP.md`

### `docs/ROUTER_RESEARCH_NOTES.md` (1)

- `docs/ROUTER_RESEARCH_NOTES.md`

### `docs/reviews` (1)

- `docs/reviews/security-hardening-2026-09.md`

### `electron/.gitignore` (1)

- `electron/.gitignore`

### `electron/.npmrc` (1)

- `electron/.npmrc`

### `electron/BUILD_TROUBLESHOOTING.md` (1)

- `electron/BUILD_TROUBLESHOOTING.md`

### `electron/ELECTRON_LAUNCH_GUIDE.md` (1)

- `electron/ELECTRON_LAUNCH_GUIDE.md`

### `electron/QUICK_START.txt` (1)

- `electron/QUICK_START.txt`

### `electron/README.md` (1)

- `electron/README.md`

### `electron/assets` (1)

- `electron/assets/completion-chirp.wav`

### `electron/bot_config.json` (1)

- `electron/bot_config.json`

### `electron/connect.html` (1)

- `electron/connect.html`

### `electron/device-worker` (1)

- `electron/device-worker/cuttle_device_worker.py`

### `electron/main.js` (1)

- `electron/main.js`

### `electron/pack-desktop-update.js` (1)

- `electron/pack-desktop-update.js`

### `electron/pack_asar.js` (1)

- `electron/pack_asar.js`

### `electron/package-lock.json` (1)

- `electron/package-lock.json`

### `electron/package.json` (1)

- `electron/package.json`

### `electron/preload.js` (1)

- `electron/preload.js`

### `src/.env.example` (1)

- `src/.env.example`

### `src/api/__init__.py` (1)

- `src/api/__init__.py`

### `src/api/action_forms.py` (1)

- `src/api/action_forms.py`

### `src/api/active_executions.py` (1)

- `src/api/active_executions.py`

### `src/api/agent_context.py` (1)

- `src/api/agent_context.py`

### `src/api/agent_cost.py` (1)

- `src/api/agent_cost.py`

### `src/api/agent_usage.py` (1)

- `src/api/agent_usage.py`

### `src/api/auth_api.py` (1)

- `src/api/auth_api.py`

### `src/api/auth_db.py` (1)

- `src/api/auth_db.py`

### `src/api/auth_session.py` (1)

- `src/api/auth_session.py`

### `src/api/bundled_llm_tools.py` (1)

- `src/api/bundled_llm_tools.py`

### `src/api/chat_delivery.py` (1)

- `src/api/chat_delivery.py`

### `src/api/chat_run_registry.py` (1)

- `src/api/chat_run_registry.py`

### `src/api/chat_status_phases.py` (1)

- `src/api/chat_status_phases.py`

### `src/api/chat_titler.py` (1)

- `src/api/chat_titler.py`

### `src/api/chat_tts.py` (1)

- `src/api/chat_tts.py`

### `src/api/chat_turn_idempotency.py` (1)

- `src/api/chat_turn_idempotency.py`

### `src/api/chat_warnings.py` (1)

- `src/api/chat_warnings.py`

### `src/api/chat_widgets.py` (1)

- `src/api/chat_widgets.py`

### `src/api/commit_message_suggester.py` (1)

- `src/api/commit_message_suggester.py`

### `src/api/cursor_agent_commands.py` (1)

- `src/api/cursor_agent_commands.py`

### `src/api/cursor_plan_bridge.py` (1)

- `src/api/cursor_plan_bridge.py`

### `src/api/cursor_question_bridge.py` (1)

- `src/api/cursor_question_bridge.py`

### `src/api/cuttle_managed_process_guard.py` (1)

- `src/api/cuttle_managed_process_guard.py`

### `src/api/cuttle_ui_capabilities.py` (1)

- `src/api/cuttle_ui_capabilities.py`

### `src/api/desktop_electron.py` (1)

- `src/api/desktop_electron.py`

### `src/api/discord_chat_bridge.py` (1)

- `src/api/discord_chat_bridge.py`

### `src/api/discovery_mdns.py` (1)

- `src/api/discovery_mdns.py`

### `src/api/doctor.py` (1)

- `src/api/doctor.py`

### `src/api/flask_restart.py` (1)

- `src/api/flask_restart.py`

### `src/api/fs_reveal.py` (1)

- `src/api/fs_reveal.py`

### `src/api/gitea_client.py` (1)

- `src/api/gitea_client.py`

### `src/api/http_authz.py` (1)

- `src/api/http_authz.py`

### `src/api/inference_mode.py` (1)

- `src/api/inference_mode.py`

### `src/api/internal_http.py` (1)

- `src/api/internal_http.py`

### `src/api/job_watch.py` (1)

- `src/api/job_watch.py`

### `src/api/lan_access.py` (1)

- `src/api/lan_access.py`

### `src/api/limiter.py` (1)

- `src/api/limiter.py`

### `src/api/llm_complete.py` (1)

- `src/api/llm_complete.py`

### `src/api/markdown_skills.py` (1)

- `src/api/markdown_skills.py`

### `src/api/mobile_android_update.py` (1)

- `src/api/mobile_android_update.py`

### `src/api/mobile_companion.py` (1)

- `src/api/mobile_companion.py`

### `src/api/model_pricing.py` (1)

- `src/api/model_pricing.py`

### `src/api/pairing_manager.py` (1)

- `src/api/pairing_manager.py`

### `src/api/process_kill_safety.py` (1)

- `src/api/process_kill_safety.py`

### `src/api/project_actions.py` (1)

- `src/api/project_actions.py`

### `src/api/project_commands.py` (1)

- `src/api/project_commands.py`

### `src/api/prompt_enhancer.py` (1)

- `src/api/prompt_enhancer.py`

### `src/api/query_events.py` (1)

- `src/api/query_events.py`

### `src/api/query_tracker.py` (1)

- `src/api/query_tracker.py`

### `src/api/restart_safety_policy.py` (1)

- `src/api/restart_safety_policy.py`

### `src/api/sandbox_policy.py` (1)

- `src/api/sandbox_policy.py`

### `src/api/session_keys.py` (1)

- `src/api/session_keys.py`

### `src/api/shared_media.py` (1)

- `src/api/shared_media.py`

### `src/api/starred_project.py` (1)

- `src/api/starred_project.py`

### `src/api/starred_slash.py` (1)

- `src/api/starred_slash.py`

### `src/api/task_benchmarks.py` (1)

- `src/api/task_benchmarks.py`

### `src/api/video_playlists.py` (1)

- `src/api/video_playlists.py`

### `src/api/vision_prepass.py` (1)

- `src/api/vision_prepass.py`

### `src/api/web_chat_api.py` (1)

- `src/api/web_chat_api.py`

### `src/api/web_terminal.py` (1)

- `src/api/web_terminal.py`

### `src/bot.py` (1)

- `src/bot.py`

### `src/bot_config.json` (1)

- `src/bot_config.json`

### `src/bots/__init__.py` (1)

- `src/bots/__init__.py`

### `src/bots/discord_bot.py` (1)

- `src/bots/discord_bot.py`

### `src/core/__init__.py` (1)

- `src/core/__init__.py`

### `src/core/config.py` (1)

- `src/core/config.py`

### `src/core/local_llm.py` (1)

- `src/core/local_llm.py`

### `src/core/mcp_tool_coaching.py` (1)

- `src/core/mcp_tool_coaching.py`

### `src/core/runtime_paths.py` (1)

- `src/core/runtime_paths.py`

### `src/data/README.md` (1)

- `src/data/README.md`

### `src/data/db` (1)

- `src/data/db/README.md`

### `src/data/harness_agents` (1)

- `src/data/harness_agents/README.md`

### `src/data/home_automation_devices.example.json` (1)

- `src/data/home_automation_devices.example.json`

### `src/img/JamBit Logo White Font.png` (1)

- `src/img/JamBit Logo White Font.png`

### `src/img/cuttle-avatar.png` (1)

- `src/img/cuttle-avatar.png`

### `src/img/cuttle-logo.png` (1)

- `src/img/cuttle-logo.png`

### `src/img/cuttle-mascot.png` (1)

- `src/img/cuttle-mascot.png`

### `src/img/cuttle-mascot_square.png` (1)

- `src/img/cuttle-mascot_square.png`

### `src/img/cuttle-mascot_square_host.png` (1)

- `src/img/cuttle-mascot_square_host.png`

### `src/img/cuttle_logo.ico` (1)

- `src/img/cuttle_logo.ico`

### `src/img/sattelite.PNG` (1)

- `src/img/sattelite.PNG`

### `src/launcher.py` (1)

- `src/launcher.py`

### `src/managers/__init__.py` (1)

- `src/managers/__init__.py`

### `src/managers/cuttle_scaffold.py` (1)

- `src/managers/cuttle_scaffold.py`

### `src/managers/home_automation.py` (1)

- `src/managers/home_automation.py`

### `src/managers/project_manager.py` (1)

- `src/managers/project_manager.py`

### `src/managers/settings_manager.py` (1)

- `src/managers/settings_manager.py`

### `src/managers/task_manager.py` (1)

- `src/managers/task_manager.py`

### `src/pytest.ini` (1)

- `src/pytest.ini`

### `src/requirements/README.md` (1)

- `src/requirements/README.md`

### `src/requirements/requirements.txt` (1)

- `src/requirements/requirements.txt`

### `src/scripts/cert_manager.py` (1)

- `src/scripts/cert_manager.py`

### `src/scripts/create_desktop_shortcuts.ps1` (1)

- `src/scripts/create_desktop_shortcuts.ps1`

### `src/scripts/create_test_execution.py` (1)

- `src/scripts/create_test_execution.py`

### `src/scripts/cuttle_client_daemon.py` (1)

- `src/scripts/cuttle_client_daemon.py`

### `src/scripts/cuttle_daemon.py` (1)

- `src/scripts/cuttle_daemon.py`

### `src/scripts/cuttle_device_worker.py` (1)

- `src/scripts/cuttle_device_worker.py`

### `src/scripts/diagnose_lan.bat` (1)

- `src/scripts/diagnose_lan.bat`

### `src/scripts/dogfood_blender_mesh.py` (1)

- `src/scripts/dogfood_blender_mesh.py`

### `src/scripts/enable_lan_firewall.bat` (1)

- `src/scripts/enable_lan_firewall.bat`

### `src/scripts/enable_lan_firewall.ps1` (1)

- `src/scripts/enable_lan_firewall.ps1`

### `src/scripts/generate_test_report.py` (1)

- `src/scripts/generate_test_report.py`

### `src/scripts/run_tests_with_logging.py` (1)

- `src/scripts/run_tests_with_logging.py`

### `src/scripts/start_api_server.py` (1)

- `src/scripts/start_api_server.py`

### `src/scripts/test_history_manager.py` (1)

- `src/scripts/test_history_manager.py`

### `src/scripts/test_phone_isolation.bat` (1)

- `src/scripts/test_phone_isolation.bat`

### `src/scripts/test_phone_isolation.ps1` (1)

- `src/scripts/test_phone_isolation.ps1`

### `src/scripts/time_series_api.py` (1)

- `src/scripts/time_series_api.py`

### `src/tests/README.md` (1)

- `src/tests/README.md`

### `src/tests/__init__.py` (1)

- `src/tests/__init__.py`

### `src/tests/bot_config.json` (1)

- `src/tests/bot_config.json`

### `src/tests/conftest.py` (1)

- `src/tests/conftest.py`

### `src/tests/html_reporter.py` (1)

- `src/tests/html_reporter.py`

### `src/tests/run_all_tests.py` (1)

- `src/tests/run_all_tests.py`

### `src/tests/run_all_tests_simple.py` (1)

- `src/tests/run_all_tests_simple.py`

### `src/tests/run_safe_tests.py` (1)

- `src/tests/run_safe_tests.py`

### `src/tests/run_tests_wsl_compatible.py` (1)

- `src/tests/run_tests_wsl_compatible.py`

### `src/tests/spend_guard.py` (1)

- `src/tests/spend_guard.py`

### `src/tests/test_action_form_process_restart.py` (1)

- `src/tests/test_action_form_process_restart.py`

### `src/tests/test_action_forms.py` (1)

- `src/tests/test_action_forms.py`

### `src/tests/test_agent_badge_segments.py` (1)

- `src/tests/test_agent_badge_segments.py`

### `src/tests/test_agent_context.py` (1)

- `src/tests/test_agent_context.py`

### `src/tests/test_agent_context_all_clis.py` (1)

- `src/tests/test_agent_context_all_clis.py`

### `src/tests/test_agent_cost_slash.py` (1)

- `src/tests/test_agent_cost_slash.py`

### `src/tests/test_agent_defaults.py` (1)

- `src/tests/test_agent_defaults.py`

### `src/tests/test_agent_harness.py` (1)

- `src/tests/test_agent_harness.py`

### `src/tests/test_agent_harness_smoke.py` (1)

- `src/tests/test_agent_harness_smoke.py`

### `src/tests/test_agent_harness_timeouts.py` (1)

- `src/tests/test_agent_harness_timeouts.py`

### `src/tests/test_agent_resume_contract.py` (1)

- `src/tests/test_agent_resume_contract.py`

### `src/tests/test_agent_router.py` (1)

- `src/tests/test_agent_router.py`

### `src/tests/test_agent_router_drift.py` (1)

- `src/tests/test_agent_router_drift.py`

### `src/tests/test_agent_router_eval.py` (1)

- `src/tests/test_agent_router_eval.py`

### `src/tests/test_agent_router_frustration.py` (1)

- `src/tests/test_agent_router_frustration.py`

### `src/tests/test_agent_router_outcomes.py` (1)

- `src/tests/test_agent_router_outcomes.py`

### `src/tests/test_agent_router_palette.py` (1)

- `src/tests/test_agent_router_palette.py`

### `src/tests/test_agent_router_repeats.py` (1)

- `src/tests/test_agent_router_repeats.py`

### `src/tests/test_agent_router_use_cases.py` (1)

- `src/tests/test_agent_router_use_cases.py`

### `src/tests/test_agent_steer.py` (1)

- `src/tests/test_agent_steer.py`

### `src/tests/test_agent_stop_then_followup.py` (1)

- `src/tests/test_agent_stop_then_followup.py`

### `src/tests/test_agent_usage_slash.py` (1)

- `src/tests/test_agent_usage_slash.py`

### `src/tests/test_assistant_badge_all_harnesses.py` (1)

- `src/tests/test_assistant_badge_all_harnesses.py`

### `src/tests/test_auth_guest.py` (1)

- `src/tests/test_auth_guest.py`

### `src/tests/test_auth_rate_limit.py` (1)

- `src/tests/test_auth_rate_limit.py`

### `src/tests/test_auth_session_token.py` (1)

- `src/tests/test_auth_session_token.py`

### `src/tests/test_bare_sticky_agent_send.py` (1)

- `src/tests/test_bare_sticky_agent_send.py`

### `src/tests/test_chat_attachments.py` (1)

- `src/tests/test_chat_attachments.py`

### `src/tests/test_chat_attention_dots.py` (1)

- `src/tests/test_chat_attention_dots.py`

### `src/tests/test_chat_busy_zombie.py` (1)

- `src/tests/test_chat_busy_zombie.py`

### `src/tests/test_chat_cli.py` (1)

- `src/tests/test_chat_cli.py`

### `src/tests/test_chat_cross_session_activity.py` (1)

- `src/tests/test_chat_cross_session_activity.py`

### `src/tests/test_chat_false_reply_ready.py` (1)

- `src/tests/test_chat_false_reply_ready.py`

### `src/tests/test_chat_find.py` (1)

- `src/tests/test_chat_find.py`

### `src/tests/test_chat_followup_heal.py` (1)

- `src/tests/test_chat_followup_heal.py`

### `src/tests/test_chat_followup_share.py` (1)

- `src/tests/test_chat_followup_share.py`

### `src/tests/test_chat_handle_links.py` (1)

- `src/tests/test_chat_handle_links.py`

### `src/tests/test_chat_history_delete_modal.py` (1)

- `src/tests/test_chat_history_delete_modal.py`

### `src/tests/test_chat_history_search.py` (1)

- `src/tests/test_chat_history_search.py`

### `src/tests/test_chat_message_pagination.py` (1)

- `src/tests/test_chat_message_pagination.py`

### `src/tests/test_chat_message_project_meta.py` (1)

- `src/tests/test_chat_message_project_meta.py`

### `src/tests/test_chat_message_share_index.py` (1)

- `src/tests/test_chat_message_share_index.py`

### `src/tests/test_chat_page_js_syntax.py` (1)

- `src/tests/test_chat_page_js_syntax.py`

### `src/tests/test_chat_pause_not_stop.py` (1)

- `src/tests/test_chat_pause_not_stop.py`

### `src/tests/test_chat_rename.py` (1)

- `src/tests/test_chat_rename.py`

### `src/tests/test_chat_session_routing.py` (1)

- `src/tests/test_chat_session_routing.py`

### `src/tests/test_chat_store_session_scope.py` (1)

- `src/tests/test_chat_store_session_scope.py`

### `src/tests/test_chat_switch_running_spinner.py` (1)

- `src/tests/test_chat_switch_running_spinner.py`

### `src/tests/test_chat_titler.py` (1)

- `src/tests/test_chat_titler.py`

### `src/tests/test_chat_tts.py` (1)

- `src/tests/test_chat_tts.py`

### `src/tests/test_chat_widgets.py` (1)

- `src/tests/test_chat_widgets.py`

### `src/tests/test_claude_cli_tool.py` (1)

- `src/tests/test_claude_cli_tool.py`

### `src/tests/test_claude_usage.py` (1)

- `src/tests/test_claude_usage.py`

### `src/tests/test_codex_app_server.py` (1)

- `src/tests/test_codex_app_server.py`

### `src/tests/test_codex_cli.py` (1)

- `src/tests/test_codex_cli.py`

### `src/tests/test_codex_starred_effort.py` (1)

- `src/tests/test_codex_starred_effort.py`

### `src/tests/test_command_detection.py` (1)

- `src/tests/test_command_detection.py`

### `src/tests/test_commit_message_suggester.py` (1)

- `src/tests/test_commit_message_suggester.py`

### `src/tests/test_composer_chip_removal.py` (1)

- `src/tests/test_composer_chip_removal.py`

### `src/tests/test_composer_draft_session.py` (1)

- `src/tests/test_composer_draft_session.py`

### `src/tests/test_concurrent_query_tracking.py` (1)

- `src/tests/test_concurrent_query_tracking.py`

### `src/tests/test_context_compiler.py` (1)

- `src/tests/test_context_compiler.py`

### `src/tests/test_context_delta.py` (1)

- `src/tests/test_context_delta.py`

### `src/tests/test_cursor_agent_incomplete_finish.py` (1)

- `src/tests/test_cursor_agent_incomplete_finish.py`

### `src/tests/test_cursor_agent_model_honesty.py` (1)

- `src/tests/test_cursor_agent_model_honesty.py`

### `src/tests/test_cursor_agent_multiline_prompt.py` (1)

- `src/tests/test_cursor_agent_multiline_prompt.py`

### `src/tests/test_cursor_agent_reply_assemble.py` (1)

- `src/tests/test_cursor_agent_reply_assemble.py`

### `src/tests/test_cursor_agent_slash_commands.py` (1)

- `src/tests/test_cursor_agent_slash_commands.py`

### `src/tests/test_cursor_agent_timeout_continue.py` (1)

- `src/tests/test_cursor_agent_timeout_continue.py`

### `src/tests/test_cursor_chip_labels.py` (1)

- `src/tests/test_cursor_chip_labels.py`

### `src/tests/test_cursor_plan_bridge.py` (1)

- `src/tests/test_cursor_plan_bridge.py`

### `src/tests/test_cursor_question_bridge.py` (1)

- `src/tests/test_cursor_question_bridge.py`

### `src/tests/test_cuttle_jobs.py` (1)

- `src/tests/test_cuttle_jobs.py`

### `src/tests/test_cuttle_jobs_pr.py` (1)

- `src/tests/test_cuttle_jobs_pr.py`

### `src/tests/test_cuttle_jobs_workspace.py` (1)

- `src/tests/test_cuttle_jobs_workspace.py`

### `src/tests/test_cuttle_managed_process_guard.py` (1)

- `src/tests/test_cuttle_managed_process_guard.py`

### `src/tests/test_cuttle_mcp_retired.py` (1)

- `src/tests/test_cuttle_mcp_retired.py`

### `src/tests/test_cuttle_performance.py` (1)

- `src/tests/test_cuttle_performance.py`

### `src/tests/test_cuttle_scaffold.py` (1)

- `src/tests/test_cuttle_scaffold.py`

### `src/tests/test_cuttle_ui_capabilities.py` (1)

- `src/tests/test_cuttle_ui_capabilities.py`

### `src/tests/test_daemon_startup_ui.py` (1)

- `src/tests/test_daemon_startup_ui.py`

### `src/tests/test_dashboards.py` (1)

- `src/tests/test_dashboards.py`

### `src/tests/test_dashboards_usage.py` (1)

- `src/tests/test_dashboards_usage.py`

### `src/tests/test_desktop_electron.py` (1)

- `src/tests/test_desktop_electron.py`

### `src/tests/test_device_workers.py` (1)

- `src/tests/test_device_workers.py`

### `src/tests/test_discord_chat_bridge.py` (1)

- `src/tests/test_discord_chat_bridge.py`

### `src/tests/test_discord_cli.py` (1)

- `src/tests/test_discord_cli.py`

### `src/tests/test_edit_attribution.py` (1)

- `src/tests/test_edit_attribution.py`

### `src/tests/test_filesystem_shell.py` (1)

- `src/tests/test_filesystem_shell.py`

### `src/tests/test_first_turn_agent_pins.py` (1)

- `src/tests/test_first_turn_agent_pins.py`

### `src/tests/test_flask_restart.py` (1)

- `src/tests/test_flask_restart.py`

### `src/tests/test_fs_reveal.py` (1)

- `src/tests/test_fs_reveal.py`

### `src/tests/test_git_commit_chat.py` (1)

- `src/tests/test_git_commit_chat.py`

### `src/tests/test_git_graph.py` (1)

- `src/tests/test_git_graph.py`

### `src/tests/test_git_pending_changes.py` (1)

- `src/tests/test_git_pending_changes.py`

### `src/tests/test_gitea_cli_module.py` (1)

- `src/tests/test_gitea_cli_module.py`

### `src/tests/test_hermes_runtime_config.py` (1)

- `src/tests/test_hermes_runtime_config.py`

### `src/tests/test_hermes_session_pins.py` (1)

- `src/tests/test_hermes_session_pins.py`

### `src/tests/test_hermes_usage.py` (1)

- `src/tests/test_hermes_usage.py`

### `src/tests/test_history_agent_badge_styling.py` (1)

- `src/tests/test_history_agent_badge_styling.py`

### `src/tests/test_home_automation_socket.py` (1)

- `src/tests/test_home_automation_socket.py`

### `src/tests/test_http_authz.py` (1)

- `src/tests/test_http_authz.py`

### `src/tests/test_inference_mode.py` (1)

- `src/tests/test_inference_mode.py`

### `src/tests/test_interruptible_run.py` (1)

- `src/tests/test_interruptible_run.py`

### `src/tests/test_jev.py` (1)

- `src/tests/test_jev.py`

### `src/tests/test_job_watch.py` (1)

- `src/tests/test_job_watch.py`

### `src/tests/test_kernel_usage_cache.py` (1)

- `src/tests/test_kernel_usage_cache.py`

### `src/tests/test_kill_pid_tree_safety.py` (1)

- `src/tests/test_kill_pid_tree_safety.py`

### `src/tests/test_llm_calls_vs_executed_nodes.py` (1)

- `src/tests/test_llm_calls_vs_executed_nodes.py`

### `src/tests/test_llm_complete.py` (1)

- `src/tests/test_llm_complete.py`

### `src/tests/test_local_llm_launch_gate.py` (1)

- `src/tests/test_local_llm_launch_gate.py`

### `src/tests/test_mobile_android_update.py` (1)

- `src/tests/test_mobile_android_update.py`

### `src/tests/test_mobile_companion.py` (1)

- `src/tests/test_mobile_companion.py`

### `src/tests/test_mobile_webview_hardening.py` (1)

- `src/tests/test_mobile_webview_hardening.py`

### `src/tests/test_model_pricing.py` (1)

- `src/tests/test_model_pricing.py`

### `src/tests/test_muse_chat_discovery.py` (1)

- `src/tests/test_muse_chat_discovery.py`

### `src/tests/test_muse_cli.py` (1)

- `src/tests/test_muse_cli.py`

### `src/tests/test_muse_msp_context.py` (1)

- `src/tests/test_muse_msp_context.py`

### `src/tests/test_oauth_link_account.py` (1)

- `src/tests/test_oauth_link_account.py`

### `src/tests/test_opencode_model_catalog.py` (1)

- `src/tests/test_opencode_model_catalog.py`

### `src/tests/test_opencode_sync_auth.py` (1)

- `src/tests/test_opencode_sync_auth.py`

### `src/tests/test_p0_p1_restart_session.py` (1)

- `src/tests/test_p0_p1_restart_session.py`

### `src/tests/test_panes_cli.py` (1)

- `src/tests/test_panes_cli.py`

### `src/tests/test_personal_overlay.py` (1)

- `src/tests/test_personal_overlay.py`

### `src/tests/test_project_actions.py` (1)

- `src/tests/test_project_actions.py`

### `src/tests/test_project_chip_persistence.py` (1)

- `src/tests/test_project_chip_persistence.py`

### `src/tests/test_project_commands.py` (1)

- `src/tests/test_project_commands.py`

### `src/tests/test_prompt_enhancer.py` (1)

- `src/tests/test_prompt_enhancer.py`

### `src/tests/test_query_events.py` (1)

- `src/tests/test_query_events.py`

### `src/tests/test_remaining_harness_context.py` (1)

- `src/tests/test_remaining_harness_context.py`

### `src/tests/test_restart_composer_js.py` (1)

- `src/tests/test_restart_composer_js.py`

### `src/tests/test_restart_daemon_script.py` (1)

- `src/tests/test_restart_daemon_script.py`

### `src/tests/test_restart_daemon_sh.py` (1)

- `src/tests/test_restart_daemon_sh.py`

### `src/tests/test_restart_native_command.py` (1)

- `src/tests/test_restart_native_command.py`

### `src/tests/test_runtime_paths.py` (1)

- `src/tests/test_runtime_paths.py`

### `src/tests/test_session_keys.py` (1)

- `src/tests/test_session_keys.py`

### `src/tests/test_shared_media.py` (1)

- `src/tests/test_shared_media.py`

### `src/tests/test_shell_panes.py` (1)

- `src/tests/test_shell_panes.py`

### `src/tests/test_shell_workspaces.py` (1)

- `src/tests/test_shell_workspaces.py`

### `src/tests/test_slash_palette_selection_consistency.py` (1)

- `src/tests/test_slash_palette_selection_consistency.py`

### `src/tests/test_stamp_auth_session_project.py` (1)

- `src/tests/test_stamp_auth_session_project.py`

### `src/tests/test_starred_agent_removal.py` (1)

- `src/tests/test_starred_agent_removal.py`

### `src/tests/test_starred_project.py` (1)

- `src/tests/test_starred_project.py`

### `src/tests/test_starred_slash.py` (1)

- `src/tests/test_starred_slash.py`

### `src/tests/test_stop_refresh_live_status.py` (1)

- `src/tests/test_stop_refresh_live_status.py`

### `src/tests/test_subagents.py` (1)

- `src/tests/test_subagents.py`

### `src/tests/test_subagents_ui.py` (1)

- `src/tests/test_subagents_ui.py`

### `src/tests/test_supervised_coordinator.py` (1)

- `src/tests/test_supervised_coordinator.py`

### `src/tests/test_supervised_forensics.py` (1)

- `src/tests/test_supervised_forensics.py`

### `src/tests/test_supervised_hardening.py` (1)

- `src/tests/test_supervised_hardening.py`

### `src/tests/test_supervised_presentation.py` (1)

- `src/tests/test_supervised_presentation.py`

### `src/tests/test_system_chat_notices.py` (1)

- `src/tests/test_system_chat_notices.py`

### `src/tests/test_token_tracking.py` (1)

- `src/tests/test_token_tracking.py`

### `src/tests/test_ui_layout_apps.py` (1)

- `src/tests/test_ui_layout_apps.py`

### `src/tests/test_user_badge_snapshot.py` (1)

- `src/tests/test_user_badge_snapshot.py`

### `src/tests/test_user_parallelization_scenario.py` (1)

- `src/tests/test_user_parallelization_scenario.py`

### `src/tests/test_video_playlists.py` (1)

- `src/tests/test_video_playlists.py`

### `src/tests/test_web_terminal_access.py` (1)

- `src/tests/test_web_terminal_access.py`

### `src/tests/test_widgets_cli.py` (1)

- `src/tests/test_widgets_cli.py`

### `src/tests/test_working_bubble_badge_chat_switch.py` (1)

- `src/tests/test_working_bubble_badge_chat_switch.py`

### `src/tools/TOOLS_README.md` (1)

- `src/tools/TOOLS_README.md`

### `src/tools/__init__.py` (1)

- `src/tools/__init__.py`

### `src/tools/web_search.py` (1)

- `src/tools/web_search.py`

### `src/web/about_page.html` (1)

- `src/web/about_page.html`

### `src/web/app_shell.html` (1)

- `src/web/app_shell.html`

### `src/web/apps_page.html` (1)

- `src/web/apps_page.html`

### `src/web/chat_page.html` (1)

- `src/web/chat_page.html`

### `src/web/control_panel.html` (1)

- `src/web/control_panel.html`

### `src/web/dashboards_page.html` (1)

- `src/web/dashboards_page.html`

### `src/web/git_graph_page.html` (1)

- `src/web/git_graph_page.html`

### `src/web/git_ui.html` (1)

- `src/web/git_ui.html`

### `src/web/home_automation.html` (1)

- `src/web/home_automation.html`

### `src/web/job_insight.html` (1)

- `src/web/job_insight.html`

### `src/web/jobs_page.html` (1)

- `src/web/jobs_page.html`

### `src/web/landing_page.html` (1)

- `src/web/landing_page.html`

### `src/web/landing_page_backup.html` (1)

- `src/web/landing_page_backup.html`

### `src/web/media_player.html` (1)

- `src/web/media_player.html`

### `src/web/query_log.html` (1)

- `src/web/query_log.html`

### `src/web/router_editor.html` (1)

- `src/web/router_editor.html`

### `src/web/settings_page.html` (1)

- `src/web/settings_page.html`

### `src/web/sounds` (1)

- `src/web/sounds/completion-chirp.wav`

### `src/web/task_management.html` (1)

- `src/web/task_management.html`

### `src/web/terminal_page.html` (1)

- `src/web/terminal_page.html`

### `src/web/test_reports.html` (1)

- `src/web/test_reports.html`

### `src/web/tools_page.html` (1)

- `src/web/tools_page.html`

### `src/web/wizard_page.html` (1)

- `src/web/wizard_page.html`

### `vendor/claw-code` (1)

- `vendor/claw-code`

### `vendor/mcp-govee` (1)

- `vendor/mcp-govee`

