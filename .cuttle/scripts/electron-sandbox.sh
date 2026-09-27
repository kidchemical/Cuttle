# Sourced by the Electron launchers. Sets SANDBOX_ARGS for "$ELECTRON", or
# exits with a visible error when Chromium's sandbox cannot start.
#
# The sandbox needs either unprivileged user namespaces (blocked by Ubuntu
# 24.04+ AppArmor) or a root-owned mode-4755 chrome-sandbox helper next to the
# binary. Without either, Electron aborts before opening a window. Launching
# unsandboxed is an explicit opt-in only: CUTTLE_ELECTRON_NO_SANDBOX=1

SANDBOX_ARGS=()

_cuttle_sandbox_helper="$(dirname "$ELECTRON")/chrome-sandbox"

_cuttle_sandbox_usable() {
  if [[ -u "$_cuttle_sandbox_helper" && "$(stat -c %u "$_cuttle_sandbox_helper" 2>/dev/null)" == "0" ]]; then
    return 0
  fi
  if [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" == "1" ]]; then
    return 1
  fi
  if [[ "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null || echo 1)" == "0" ]]; then
    return 1
  fi
  return 0
}

_cuttle_sandbox_error() {
  local msg
  msg="Cuttle cannot start: the Chromium sandbox is unavailable on this system.

This kernel restricts unprivileged user namespaces, and the sandbox helper
is not setuid-root:
  $_cuttle_sandbox_helper

Enable the sandbox (once per Electron install):
  sudo chown root:root '$_cuttle_sandbox_helper'
  sudo chmod 4755 '$_cuttle_sandbox_helper'

Or, for development only, launch without the sandbox:
  CUTTLE_ELECTRON_NO_SANDBOX=1 $0"
  echo "$msg" >&2
  if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    if command -v zenity >/dev/null 2>&1; then
      zenity --error --no-markup --title="Cuttle" --width=560 --text="$msg" 2>/dev/null || true
    elif command -v notify-send >/dev/null 2>&1; then
      notify-send -u critical "Cuttle cannot start" "Chromium sandbox unavailable. Run: sudo chown root:root $_cuttle_sandbox_helper && sudo chmod 4755 $_cuttle_sandbox_helper" || true
    elif command -v xmessage >/dev/null 2>&1; then
      xmessage -center "$msg" 2>/dev/null || true
    fi
  fi
}

if [[ "${CUTTLE_ELECTRON_NO_SANDBOX:-0}" == "1" ]]; then
  echo "cuttle: WARNING: CUTTLE_ELECTRON_NO_SANDBOX=1, launching Electron without the Chromium sandbox" >&2
  export ELECTRON_DISABLE_SANDBOX=1
  SANDBOX_ARGS+=(--no-sandbox)
elif ! _cuttle_sandbox_usable; then
  _cuttle_sandbox_error
  exit 1
fi
unset -f _cuttle_sandbox_usable _cuttle_sandbox_error
unset _cuttle_sandbox_helper
