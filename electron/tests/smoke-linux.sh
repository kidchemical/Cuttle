#!/usr/bin/env bash
# Run only inside the isolated Xvfb display created by desktop-smoke.yml.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p ../temp
openbox --sm-disable > ../temp/desktop-smoke-window-manager.log 2>&1 &
wm_pid=$!
trap 'kill "$wm_pid" 2>/dev/null || true; wait "$wm_pid" 2>/dev/null || true' EXIT

# Xvfb supplies a display; EWMH visibility/stacking needs a window manager.
wm_ready=0
for ((attempt=0; attempt<100; attempt++)); do
    if ! kill -0 "$wm_pid" 2>/dev/null; then
        cat ../temp/desktop-smoke-window-manager.log
        exit 1
    fi
    if [[ "$(xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null || true)" == *"window id #"* ]]; then
        wm_ready=1
        break
    fi
    sleep 0.1
done
if [[ "$wm_ready" -ne 1 ]]; then
    echo "Desktop smoke window manager did not become ready" >&2
    cat ../temp/desktop-smoke-window-manager.log
    exit 1
fi
npm run smoke:host -- --no-sandbox --ozone-platform=x11
npm run smoke:client -- --no-sandbox --ozone-platform=x11
