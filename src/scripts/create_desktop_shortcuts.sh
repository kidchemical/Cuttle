#!/usr/bin/env bash
# Cuttle desktop entries — two primary launchers + a daemon-only launcher:
#   Cuttle Host    (red mascot)
#   Cuttle Client  (mascot)
#   Cuttle Daemon  (mascot)
#
# Idempotent. Electron runs this on startup so installs self-heal.
set -euo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$APPS" "$DESKTOP"

HOST_LAUNCH="$ROOT/.cuttle/scripts/launch-cuttle-host.sh"
CLIENT_LAUNCH="$ROOT/.cuttle/scripts/launch-cuttle-client.sh"
PY="$ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$ROOT/.venv/bin/python"
DAEMON="$ROOT/src/scripts/cuttle_daemon.py"
ICON_HOST="$ROOT/src/img/cuttle-mascot_square_host.png"
ICON_CLIENT="$ROOT/src/img/cuttle-mascot_square.png"
[[ -f "$ICON_HOST" ]] || ICON_HOST="$ICON_CLIENT"

write_entry() {
    local file="$1" name="$2" exec="$3" icon="$4" comment="$5"
    cat > "$file" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$name
Comment=$comment
Exec=$exec
Icon=$icon
Terminal=false
Categories=Utility;
StartupNotify=true
EOF
    chmod +x "$file"
}

write_entry "$APPS/cuttle-host.desktop"   "Cuttle Host"   "$HOST_LAUNCH"   "$ICON_HOST"   "Start Cuttle Host (daemon + app on this PC)"
write_entry "$APPS/cuttle-client.desktop" "Cuttle Client" "$CLIENT_LAUNCH" "$ICON_CLIENT" "Start Cuttle Client (connect to a Host)"
write_entry "$APPS/cuttle-daemon.desktop" "Cuttle Daemon" "\"$PY\" \"$DAEMON\"" "$ICON_CLIENT" "Start the Cuttle daemon only (no app)"

for name in cuttle-host cuttle-client cuttle-daemon; do
    cp -f "$APPS/$name.desktop" "$DESKTOP/$name.desktop"
    chmod +x "$DESKTOP/$name.desktop"
    # Best-effort trust so GNOME/Nautilus shows the icon and allows launch.
    command -v gio >/dev/null 2>&1 && gio set "$DESKTOP/$name.desktop" metadata::trusted true 2>/dev/null || true
done

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true

if [[ "$QUIET" -eq 0 ]]; then
    echo "Desktop entries: Cuttle Host / Cuttle Client / Cuttle Daemon -> $DESKTOP"
fi
