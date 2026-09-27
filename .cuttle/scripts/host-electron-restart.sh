#!/usr/bin/env bash
# Detached Host Electron restart (UI only — Flask/daemon keep running).
set -uo pipefail

ROOT="${CUTTLE_PROJECT_PATH:-}"
if [[ -z "$ROOT" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/cuttle-desktop"
mkdir -p "$STATE"
LOG="${CUTTLE_PARAM_LOG_PATH:-$STATE/host-electron-restart.log}"

log() {
  local line
  line="$(date -Iseconds) $*"
  echo "$line" | tee -a "$LOG"
}

log "host-electron-restart start repo=$ROOT"
sleep 1

while read -r pid; do
  [[ -z "$pid" ]] && continue
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ -z "$cmd" ]] && continue
  if [[ "$cmd" == *cuttle_daemon.py* || "$cmd" == *web_chat_api* || "$cmd" == *cuttle_client_daemon* || "$cmd" == *cuttle_device_worker* ]]; then
    continue
  fi
  if [[ "$cmd" == *"$ROOT/electron"* || "$cmd" == *cuttle-desktop* || "$cmd" == *" --mode=host"* ]]; then
    log "stopping pid=$pid"
    kill "$pid" 2>/dev/null || true
  fi
done < <(pgrep -f 'electron|cuttle-desktop' || true)

sleep 2

if [[ "${CUTTLE_PARAM_NO_PULL:-}" != "true" && "${1:-}" != "--no-pull" ]]; then
  log "git pull --ff-only"
  git -C "$ROOT" pull --ff-only >>"$LOG" 2>&1 || log "WARN git pull failed"
fi

pkg="$ROOT/electron/package.json"
if [[ -f "$pkg" ]]; then
  ver="$(python3 -c "import json; print(json.load(open('$pkg')).get('version',''))" 2>/dev/null || true)"
  if [[ -n "$ver" ]]; then
    export CUTTLE_PACKAGE_VERSION="$ver"
    log "CUTTLE_PACKAGE_VERSION=$CUTTLE_PACKAGE_VERSION"
  fi
fi
export CUTTLE_HOSTED_BY_DAEMON=1

LAUNCH="$ROOT/.cuttle/scripts/launch-cuttle-host.sh"
if [[ -x "$LAUNCH" ]]; then
  log "starting Host via launch-cuttle-host.sh"
  nohup "$LAUNCH" >>"$LOG" 2>&1 &
elif command -v npm >/dev/null 2>&1 && [[ -d "$ROOT/electron" ]]; then
  log "starting Host via npm start"
  (cd "$ROOT/electron" && nohup npm start >>"$LOG" 2>&1 &)
else
  log "ERROR no Host Electron launch path"
  exit 3
fi

log "host-electron-restart done"
exit 0
