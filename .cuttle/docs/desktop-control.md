# Browser and desktop control from Cuttle

## Choose the tool by what it needs to control

| Method | Use it for | Limits |
|---|---|---|
| Service connector, documented API or app CLI | A supported operation with structured inputs and a result the agent can verify | Requires the service's authorization and may not expose the required operation. It does not reuse an arbitrary Chrome sign-in. |
| Codex Chrome plugin | A signed-in Chrome profile or existing tabs. The bundled client provides page structure, precise controls and tab screenshots, and can work in background tabs. | Covers web content. Uploading a local file through the extension needs Chrome's file URL permission. It cannot operate the native file picker or `chrome://extensions`. |
| OpenAI built-in browser | Local development and public sites in a separate browser profile | Has its own sign-ins. It doesn't inherit the user's regular Chrome session. An app-managed browser host must be available. |
| Playwright in a test profile | Repeatable browser tests with controlled data and explicit login fixtures | Browser only. A fresh profile does not have a user's sign-in and browser automation does not control an OS file picker. |
| OpenAI native Computer Use | Native apps and dialogs on supported macOS and Windows hosts | OpenAI's Linux desktop preview supports browser actions only. Installing the Linux plugin or changing a Cuttle feature flag does not add native desktop input. |
| Wayland desktop CLI through the XDG portal | A native Linux app or file dialog. Can capture the selected display and use pointer and keyboard input after desktop consent. | Shares live pixels and usually uses foreground focus and the real pointer. Needs an unlocked, active session and a compositor that supports RemoteDesktop and ScreenCast. |
| X11 automation such as `xdotool` and PyAutoGUI | A real X11 session or an X11 test VM | Mature and easy to script on X11. Chrome and native dialogs on GNOME Wayland can be outside XWayland, so these tools are not a desktop fallback there. |
| AT-SPI and raw input tools | AT-SPI can read or act on accessible controls. Tools such as `ydotool` can inject input on Linux. | AT-SPI coverage depends on the app and provides no screenshot. Raw input does not give a view of the target and grants broad device-level access, so prefer portal-scoped input on a real workstation. |
| Isolated GUI VM or container | Repeatable, potentially unattended desktop tests | Requires separate setup, files, and a sign-in of its own. It does not inherit a real workstation's Chrome profile. |

OpenAI documents the [Chrome extension and file upload permission](https://learn.chatgpt.com/docs/chrome-extension#upload-files), the [built-in browser](https://learn.chatgpt.com/docs/browser), and [native platform support](https://learn.chatgpt.com/use-cases/use-your-computer-with-codex). The [xdotool README](https://github.com/jordansissel/xdotool) describes its Wayland limitations.

## What the browser-host error means

Reproduced on Ubuntu 26.04.1, GNOME Wayland 50.1, Codex CLI 0.171.0 and OpenAI's
bundled `unified-computer-use` plugin 26.1117.31558. The unified worker reports
`The browser access host is unavailable` before listing tabs. Its launcher
starts it with `BROWSER_USE_ACCESS_EXECUTOR_ID` present but empty. A presence-only
check of that worker found both `BROWSER_USE_ACCESS_PIPE_PATH` and
`BROWSER_USE_ACCESS_VERIFYING_IDENTITY` absent. Its configured surface is
`browser`, so this worker offers no desktop app control either. These are two
separate limits.

The unified browser path expects OpenAI's app-managed access host for the
current session. That host is a local service that authorizes browser requests
for a specific chat and turn. Its connection details tell the worker where to
ask and how to verify the reply. Cuttle starts its own Codex app-server over
stdio. A ChatGPT sign-in or a working Chrome extension does not register that
app-server's turns with the desktop service. Cuttle's child environment filter
preserves those names when present, as verified with synthetic values.

The strongest version evidence is the October 2026 desktop/plugin change.
[OpenAI Codex issue #57641](https://github.com/openai/codex/issues/57641)
reports a working Windows setup on desktop `26.1021.3705.0` with plugin
`26.1021.31345` and failure after desktop `26.1023.6123.0` with plugin
`26.1023.50603`. A macOS reporter restored browser control by rolling back the
desktop package alone and restarting the affected worker, with the CLI and
Chrome extension held constant. The older package did not preload the new
access check. On this Linux host, the package log shows an upgrade from
`26.928.31416` to `26.1117.31558` on November 19. There is no local before/after
browser test for that upgrade. [Linux issue #62786](https://github.com/openai/codex/issues/62786)
reproduces the error in a fresh CLI on the same installed version as Cuttle.
[Issue #63724](https://github.com/openai/codex/issues/63724) reports that CLI
`0.172.0` still fails on macOS.

The immediate condition is verified locally. OpenAI's
[public app-server protocol](https://learn.chatgpt.com/docs/app-server) does not
document how a third-party client registers a session with this browser host.
The upstream reports remain open. A durable fix to the unified tool needs a
supported registration path or an updated upstream runtime. Once available,
verify it by listing, opening and closing a disposable tab through `cua_repl`
from a fresh Cuttle turn with the normal authorization checks enabled.

The dedicated Chrome plugin connected in the same setup and verified the
signed-in Studio channel. `chrome@openai-bundled` is already enabled in this
installation. It is the right integration for “use my Chrome” and
an alternative to the broken unified route, not a repair of that route. Keep
its local quick start as the first step. Do not copy another session's access
context, patch vendor authorization, or switch tools to get around a denied URL
or site action. A normal native file picker can select a single authorized file
without granting the extension additional file access.

## Native drivers for Linux and Windows

Use [wayland-mcp](https://github.com/alex-oleshkevich/wayland-mcp) on GNOME or
KDE Wayland. Its upstream v0.5.2 Linux archive is checksum-pinned on this host.
It offers a plain CLI and stdio MCP, screenshot crop and resize, screenshot IDs
and coordinate transforms, accessibility inspection, and a filter for the
tools each agent sees. It opens the XDG portal on demand and needs no GNOME
Shell extension or installed daemon. The local health check sees two Wayland
outputs and the portal's keyboard, pointer and screenshot capabilities. The
stdio handshake and tool filter passed. A live GNOME test used the portal's
normal Share control with the user's explicit authorization, one display per
session, and clipboard access off. A disposable GTK window confirmed a
screenshot-based click, typing, and Enter at 100% display scaling. On the
125% output, the stock driver landed 20% short on both axes despite correct
screenshot geometry. Mutter 50.1 divides absolute motion by the display
scale after the portal forwards it. The portal also rejects absolute values
beyond the stream's logical bounds. [Mutter monitor coordinate conversion](https://github.com/GNOME/mutter/blob/50.1/src/backends/meta-screen-cast-monitor-stream.c#L196-L220)

On this installation, use the launcher at
`.cuttle/personal/tools/wayland-mcp/v0.5.2-gnome/wayland-mcp`, relative to the
Cuttle checkout. It runs a local v0.5.2 build with a GNOME pointer correction
and leaves the upstream binary available at `v0.5.2/wayland-mcp`. Pass the
original screenshot coordinates and same-session `shot_id` to the launcher.
The build multiplies the offset from the selected monitor's origin by its
scale. Near the right or bottom edge it uses a valid absolute anchor and
finishes with relative motion in the same portal session. It rejects the
two-step move while a mouse button is held, so use another input method for
drags into those areas. The adjacent `gnome-pointer.patch` records the source
change against upstream tag `v0.5.2`.

The corrected build hit the center and four corner targets at 125% on
`HDMI-1` and at 100% on `DP-1`. Each test used the portal's normal Share
control, one display, and clipboard access off. Both sessions were closed.
Follow the global [computer-use runbook](../../.cuttle_global/docs/computer-use.md)
to use the launcher from other projects and agent CLIs.

Use [Cua Driver](https://cua.ai/docs/cua-driver/concepts/platform-support)
on Windows. It publishes Windows UI Automation and capture support, with a
shell CLI and plain stdio MCP. Use the same local-host test before relying on
it from Cuttle. Windows was not exercised on this Linux machine.

Alternatives worth keeping:

- [gnome-wayland-agent (`gwa`)](https://github.com/Bloodcrane/gnome-wayland-agent)
  is a small Python portal client with a shell CLI, screen size limit, and
  stdio MCP. Its daemon's per-user socket remains reachable to other processes
  under the same user while it runs. Current upstream GNOME evidence is for
  51.1. It has fewer accessibility and screenshot-targeting features than
  wayland-mcp.
- [waytool](https://github.com/mx-shift/waytool) documents GNOME 50 and uses
  portal capture and libei. It takes clicks in screenshot pixels and supports
  AT-SPI queries. Its GNOME extension is needed for window targeting and text
  outside the keyboard layout. It is useful for CLI-only scripting with one
  daemon and one grant.
- Cua Driver also supports Linux. Its GNOME Wayland path needs a Shell helper
  and a Shell restart. Use wayland-mcp for the basic GNOME workflow to avoid
  that setup and keep the driver task-scoped.
- A short Python client can use the same portal and PipeWire if neither small
  driver meets the need. It still has to handle consent, lock state, monitor
  scale and focus. A local probe reached the GNOME portal, but the compositor
  returned `Session creation inhibited` while the display was asleep. GNOME's
  screensaver reported active and logind reported `LockedHint=no`. Check the
  display power state and lock state separately. Wake a sleeping display and
  retry before asking the user to unlock. The portal reached its consent prompt
  after the display was awake. This is separate from OpenAI's missing host.

For the Cuttle promo upload, the user's active Linux seat already had access to
`/dev/uinput`. A temporary Python `evdev` keyboard brought the signed-in Chrome
tab forward and used the normal GNOME file picker for the video and thumbnail.
GNOME's built-in window screenshot verified the picker and selected filename.
The dedicated plugin handled the rest of Studio and confirmed the saved video.
The picker needed one Enter in its location field to select the file and a
second Enter to submit the selection. This was a local foreground fallback
using an existing seat permission. It did not require sudo, an installed
service, or a change to Chrome's file permissions. Use an app-scoped portal
driver for a reusable integration.

Acceptance before adopting a driver: open a non-sensitive native dialog, grant
only that test session, capture one frame, click a known control on a scaled
display, enter a test file path, and verify the app received exactly that file.
Then close the session, confirm control stops on revoke/lock and reject input
on stale screenshots or a changed target. Try the same CLI from Codex and one
other harness before adding a Cuttle UI.

## If Cuttle needs a wrapper

Keep the external driver replaceable. Start with a local, versioned JSON CLI
contract (`status`, `capture`, `click`, `key`, `close`) under the Cuttle agent-ops
conventions. No Cuttle-hosted MCP server. Guest harnesses can configure the
driver's MCP themselves. Consider a default-off app only after the CLI proves
useful, for session status, scoped approval and stop. Keep raw desktop input off
the network. Cuttle would own the chat/user binding, target, expiry, audit of
actions and final side-effect approval. The compositor owns consent and the
driver owns capture and input. Remove the local adapter/config to uninstall
without changing agent dispatch or persistence.

Budget screenshot input as well as disk space. Prefer an accessibility tree
or a compact list of controls when available. Use a downscaled overview (start
at a 1280 px long edge), then a native-resolution crop for small text. Ship only
changed frames needed for the decision. Preserve frame ID, stream ID, crop
origin and both size pairs so preview coordinates can be mapped back and stale
clicks rejected. JPEG quality mostly changes bytes. Image input cost depends on
the model's resolution/detail rules, so benchmark actual usage and task success
instead of equating a smaller file with fewer tokens. Store captures temporarily
and locally unless the user requests sharing.
[OpenAI's image input guide](https://developers.openai.com/api/docs/guides/images-vision#calculating-costs)
shows why rates and image sizing should be checked for each model.

[Jev](../../.cuttle_global/docs/jev.md) can optionally score a structured
before/after result, such as whether the expected filename appeared. It is a
text judgment service, not a screen driver, vision model or approval authority.
Start with deterministic checks and no Jev calls. If a text judgment is useful,
provide only the minimal state needed and account for its external provider.
