# Sourced by the Electron launchers. Sets DISPLAY_ARGS for "$ELECTRON".
#
# Linux Wayland sessions run Cuttle under XWayland (--ozone-platform=x11), the
# default before Electron 38: native Wayland spins before 'ready' when no output
# is active (monitor off/asleep, as when the Host relaunches its UI unattended),
# and Wayland clients cannot keep gizmo pop-outs on top or restore window
# positions. Opt into native Wayland with CUTTLE_ELECTRON_WAYLAND=1.
# Mirrors core.runtime_paths.electron_display_args() (daemon-launched UI).

DISPLAY_ARGS=()
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" && -z "${CUTTLE_ELECTRON_WAYLAND:-}" ]]; then
  DISPLAY_ARGS+=(--ozone-platform=x11)
fi
