# Computer use from any Cuttle project

Use the same shell interface from Codex, Claude Code, Cursor, Hermes, or any
other harness with local command access. Native MCP is optional and belongs to
the agent's configuration. Cuttle does not need a new service, a Flask restart,
or a bundled desktop binary.

| Task | First choice |
| --- | --- |
| Existing signed-in Chrome in Codex | Installed Chrome plugin and its bundled skill. For a local file picker, use the native desktop route if the plugin cannot attach the file. |
| GNOME or KDE Wayland | [wayland-mcp](https://github.com/alex-oleshkevich/wayland-mcp) using the XDG desktop portal. |
| Windows 10/11 or Linux X11 | [Cua Driver](https://cua.ai/docs/cua-driver/concepts/platform-support) in the interactive user session. |
| Public data or disposable browser testing | The task's connector, documented API, or browser test driver. |

The Cuttle checkout's [browser and desktop notes](../../.cuttle/docs/desktop-control.md)
cover the OpenAI browser-host error, how it differs from a Chrome extension
failure, and the local upload fallback.

## Linux Wayland

Install the correct CPU archive and `SHA256SUMS` from the
[wayland-mcp releases](https://github.com/alex-oleshkevich/wayland-mcp/releases).
Verify the matching SHA-256 before installing the binary in a user-owned
directory in `PATH` or recording its absolute path. PipeWire and xkbcommon
are needed. GNOME and KDE also need their RemoteDesktop and ScreenCast
portals. No sudo, compositor extension, or startup service is needed for
the portal route.

In a source checkout, a local install may be under
`.cuttle/personal/tools/wayland-mcp/<version>/wayland-mcp`, relative to the
**Cuttle installation containing this runbook**. That path is shared by
Cuttle chats on this machine. Prefer an installed binary on the target
machine's `PATH` when present. If this Cuttle checkout has the verified
GNOME launcher at `.cuttle/personal/tools/wayland-mcp/v0.5.2-gnome/wayland-mcp`,
use its absolute path for this installation's GNOME desktop. The
[desktop-control notes](../../.cuttle/docs/desktop-control.md#native-drivers-for-linux-and-windows)
describe the fractional-scaling fix and its drag limitation. Pass the
original screenshot coordinates and same-session shot ID to that launcher.

Run diagnostics first. They do not capture the display or prompt for access.

```bash
wayland-mcp --version
wayland-mcp --portal doctor
wayland-mcp --portal tools screenshot --json
wayland-mcp --portal call screenshot '{"max_dimension":1280,"format":"jpeg"}' -o task-preview.jpg
```

Use `--portal` on GNOME or KDE. On wlroots-style compositors, check `doctor`
for native capture and input support. Use a project scratch path for images.
On GNOME or KDE the first screenshot or input request shows an OS consent
dialog. A one-shot CLI call closes its portal session when it exits. For
capture and input over several calls, use a single stdio session so the user
grants access once and screenshots keep their `shot_id`. Each one-shot CLI
call has fresh state, so do not pass a shot ID from a previous shell call.

Example optional stdio registration for an install on `PATH`. An absolute path
is useful when a background harness has a shorter `PATH`. These examples expose
the small tool set needed for basic visual interaction. Read
`wayland-mcp tools --names` and the schema for any additional task-specific tool
before enabling it.

```bash
codex mcp add wayland-desktop -- wayland-mcp --portal --tools doctor,desktop_info,screenshot,click,key,type_text
claude mcp add --scope user wayland-desktop -- wayland-mcp --portal --tools doctor,desktop_info,screenshot,click,key,type_text
```

For a different agent that accepts a stdio MCP command, use the same command
and arguments. Read the new tool list in a fresh agent session after
registering it. Remove the MCP entry in that agent and delete the user-owned
binary to uninstall. Cuttle's shell route works without registering MCP.

## Windows

Install the native Windows build from the
[Cua Driver quickstart](https://cua.ai/docs/cua-driver/quickstart#install-cua-driver-on-its-own).
Use the Windows tab and use the installer in an ordinary interactive Windows
session. Use a Windows executable and desktop session even when a different
machine or WSL hosts the agent. See [permissions](https://cua.ai/docs/cua-driver/guides/permissions)
for restricting access, and the [agent connection guide](https://cua.ai/docs/cua-driver/guides/connect-your-agent)
for supported hosts and plain stdio.

```powershell
cua-driver status
cua-driver doctor
cua-driver call list_apps
cua-driver stop
```

Before using it for real work, drive a disposable local dialog or Calculator
and read the resulting state back from the app. Windows UI Automation
and background input are useful when supported. Elevated windows and some
Chromium gestures have additional limits. The checkout's Linux verification
does not qualify a particular Windows host.

## Use the local session

- Verify the OS and user session that own the window before starting. On
  GNOME, check login state and display power separately. Wake a sleeping
  display and retry. A locked session needs the user's unlock. The portal
  consent dialog is a separate OS control. With explicit authorization to
  operate that dialog and an existing supported input surface, choose the
  approved display and grant only the access the task needs. Leave clipboard
  access off for screenshot, click, and ordinary keyboard input.
- Start with an accessibility tree if the driver can read the target. For
  images, start with a 1280 px long edge and crop for fine text. Use the
  returned coordinate transform or same-session shot ID for input. Capture
  again if the window, scale, or monitor changes. Verify coordinate input in
  a disposable app on the target display before relying on it, especially
  with fractional scaling. The stock wayland-mcp v0.5.2 landed short on
  this host at 125%. The local GNOME launcher passed at both 100% and 125%.
- An authorized UI operation uses a fresh view of the expected target.
  Close the capture and input session after confirming the result. Keep
  captures local and temporary unless the user requests delivery.
- For a Cuttle mesh job, launch the driver in the target machine's desktop
  user session using the configured execution node. Desktop input and capture
  are local. Never publish a raw desktop-control socket on the LAN.
