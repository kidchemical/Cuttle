# Agent CLI executable paths

Settings → Agents → expand an agent → **CLI executable or wrapper**.
Available for all eight bundled agents, including ones not yet detected.
This extends existing CLI discovery and ships directly without an experimental
flag; leaving the field empty preserves established discovery behavior.

Enter an absolute executable path on the **Cuttle host**, not the browser or
phone viewing Settings. Click **Save path**. Paths may contain spaces; do not
add shell quotes or arguments. On POSIX the file must be executable (`chmod +x`).
**Use default** removes the saved path and restores environment/PATH discovery.
Saving validates the file without executing it. Changes affect new launches;
a running turn keeps its current process. Refresh Settings to update its CLI
availability status after a path change.

Resolution: saved path → existing environment override → adapter discovery.
The environment overrides are `AGY_CLI_PATH`, `CLAUDE_CLI_PATH`, `CODEX_CLI_PATH`,
`CURSOR_CLI_PATH`, `DSH_CLI_PATH`, `HERMES_CLI_PATH`, `MUSE_CLI_PATH` and
`OPENCODE_CLI_PATH`. Adapter-specific installation discovery is unchanged.
A saved path is used exactly as entered: Windows shim/native-binary discovery
must not substitute a different executable for an explicitly selected wrapper.
If a saved file is deleted or loses execute permission, that agent is unavailable;
Cuttle never silently bypasses the wrapper and launches another installation.
On Windows, use a launchable executable wrapper; PowerShell scripts require an
executable launcher. Automatic Windows discovery continues to prefer native
binaries where required to preserve long and multiline arguments.

## User-owned sandbox wrappers

A path override selects a program; it does not create a sandbox or install a CLI.
A user can select their own wrapper which launches the real agent in a container,
VM or separate account. The wrapper must preserve all arguments, stdin/stdout,
exit status and cancellation. Send wrapper diagnostics to stderr. Support every
CLI mode Cuttle uses, including version/model probes, streaming, session resume,
background helper requests, and app-server/serve transports where applicable.
Keep CLI authentication and session state persistent and expose approved project
paths consistently. Wrappers must call the real executable explicitly to avoid
recursively launching themselves. Host API access and approval policies belong
to the user's sandbox setup.

## Ownership and storage

`api.agent_harness.cli_paths` owns validation, saved path resolution and patches.
Each bundled CLI discovery function calls that owner before its existing logic.
`api.settings_routes` exposes paths through the existing owner-only write route:
`POST /api/settings/agent-adapters` with `{"cli_paths":{"codex":"/absolute/wrapper"}}`.
An empty string clears one agent's saved path. Patches preserve other agents.
GET on the same authenticated route includes `cli_paths` descriptors with saved
path, environment fallback and errors for files that have disappeared.
Host paths are stored under `agent_cli_paths` in the Cuttle home's
`config/machine_settings.json` through the existing settings storage owner.
Third-party drop-in adapters retain their own discovery contracts; their authors
must integrate executable overrides explicitly before exposing a path control.

## Native Linux execution and resource limits

With saved paths empty and no CLI environment overrides, the bundled adapters
discover the installed vendor CLIs. Cuttle does not insert Podman, bwrap, a
separate user, a permission broker or permission notifications into that path.
Vendor CLI protocols, resume/steering and native permission flags still belong
to their existing adapters. Native execution does not mean an interactive TTY.

`api.agent_harness.resource_scope` attaches spawned CLI runs synchronously at
`agent_process.attach_to_chat_run`, before adapters send stdin/RPC prompts.
This uses systemd's existing `StartTransientUnit` D-Bus contract through `busctl`;
no extra Python library or container runtime is required. The native child keeps
its executable, environment, uid, cwd and stdio. Per-run scopes sit under
`cuttle-agents.slice`, outside the hosting Cuttle service; descendants inherit
the cgroup. Scope `OOMPolicy=kill` groups an OOM kill with that run's children.

Defaults: per run `MemoryHigh=6G`, `MemoryMax=8G`, `MemorySwapMax=512M`,
`TasksMax=4096`; all agents together `MemoryHigh=60%`, `MemoryMax=70%` of RAM.
Machine-local `agent_resource_limits` overrides these defaults. Size values
accept bytes, K/M/G/T units or percentages; blank/infinity removes a ceiling.
`enabled: false` or `CUTTLE_AGENT_SCOPES=0` disables attachment. Limits affect
new launches. Existing turns are never migrated by changing settings.

On non-Linux hosts, or if the user manager is unavailable, native execution
continues. Attachment failures log one bounded stderr warning, with no desktop
notifications. This is resource control, not a security sandbox: attachment
happens after process creation, and deliberate cgroup escape is not prevented.
To replace/remove it, change the single registration hook; no session or
transcript data migration is needed.

Host recovery from saved wrappers: run the allowlisted `agents.restore-native`
action (`.cuttle/scripts/restore-native-agents.py --apply`). It clears saved
overrides through the settings owner, enables limits, checks actual cgroup
ceilings and descendant inheritance with a small Python process, and reports
native CLI discovery. It never starts a model turn, stops existing agents, or
restarts Flask. It rejects an active bwrap environment or CLI environment
overrides. If verification fails after clearing settings, it reports failure
and leaves the native settings in place; it does not restore sandbox wrappers.
Restart Flask with the standard restart card to load changed Python code.

References: [systemd scope OOM policy](https://www.freedesktop.org/software/systemd/man/systemd.scope.html),
[systemd D-Bus API](https://www.freedesktop.org/software/systemd/man/org.freedesktop.systemd1.html).
