#!/usr/bin/env bash
# Client deploy updater (POSIX). ASCII-safe; used by cuttle_self_update jobs.
set -uo pipefail

REPO=""
HOST_NAME=""
DO_ELECTRON=1
DO_DAEMON=1
SKIP_PULL=0
LOG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --host|--hostname) HOST_NAME="$2"; shift 2 ;;
    --log|--log-path) LOG="$2"; shift 2 ;;
    --skip-pull) SKIP_PULL=1; shift ;;
    --no-electron) DO_ELECTRON=0; shift ;;
    --no-daemon) DO_DAEMON=0; shift ;;
    --restart-electron) DO_ELECTRON=1; shift ;;
    --restart-daemon) DO_DAEMON=1; shift ;;
    *) shift ;;
  esac
done

if [[ -z "$REPO" ]]; then
  echo "ERROR --repo is required"
  exit 2
fi

STATE="${XDG_STATE_HOME:-$HOME/.local/state}/cuttle-desktop"
mkdir -p "$STATE"
if [[ -z "$LOG" ]]; then
  LOG="$STATE/client-self-update.log"
fi

log() {
  local line
  line="$(date -Iseconds) $*"
  echo "$line" | tee -a "$LOG"
}

log "self-update start repo=$REPO host=$HOST_NAME electron=$DO_ELECTRON daemon=$DO_DAEMON skipPull=$SKIP_PULL"
sleep 2

if [[ ! -d "$REPO" ]]; then
  log "ERROR repo missing: $REPO"
  exit 2
fi

PY="$REPO/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

# SkipPull skips the completed merge, never the lifecycle safety recheck.
UPDATE_HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/client-update-checkout.py"
CHECK_ARGS=()
[[ "$SKIP_PULL" -eq 0 ]] || CHECK_ARGS+=(--check-only)
"$PY" "$UPDATE_HELPER" --repo "$REPO" "${CHECK_ARGS[@]}" >>"$LOG" 2>&1 || { log "ERROR checkout update refused; see $LOG"; exit 1; }

while read -r pid; do
  [[ -z "$pid" ]] && continue
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ -z "$cmd" ]] && continue
  if [[ "$cmd" == *cuttle_daemon.py* || "$cmd" == *web_chat_api* ]]; then
    continue
  fi
  if [[ "$DO_DAEMON" -eq 1 && ( "$cmd" == *"$REPO/src/scripts/cuttle_client_daemon.py"* || "$cmd" == *"$REPO/src/scripts/cuttle_device_worker.py"* ) ]]; then
    log "stopping pid=$pid"
    kill "$pid" 2>/dev/null || true
    continue
  fi
  if [[ "$DO_ELECTRON" -eq 1 && "$cmd" == *"$REPO/electron/"* ]]; then
    log "stopping pid=$pid"
    kill "$pid" 2>/dev/null || true
  fi
done < <(pgrep -f 'electron|cuttle-desktop|cuttle_client_daemon|cuttle_device_worker' || true)

sleep 3


pkg="$REPO/electron/package.json"
if [[ -f "$pkg" ]]; then
  ver="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$pkg" 2>/dev/null || true)"
  if [[ -n "$ver" ]]; then
    export CUTTLE_PACKAGE_VERSION="$ver"
    log "CUTTLE_PACKAGE_VERSION=$CUTTLE_PACKAGE_VERSION"
  fi
fi

if [[ "$DO_DAEMON" -eq 1 ]]; then
  daemon="$REPO/src/scripts/cuttle_client_daemon.py"
  if [[ -f "$daemon" ]]; then
    log "starting client-daemon"
    nohup "$PY" "$daemon" >>"$LOG" 2>&1 &
  fi
fi

if [[ "$DO_ELECTRON" -eq 1 ]]; then
  LAUNCH="$REPO/.cuttle/scripts/launch-cuttle-client.sh"
  if [[ -x "$LAUNCH" ]]; then
    log "starting client via launch-cuttle-client.sh"
    if [[ -n "$HOST_NAME" ]]; then
      nohup "$LAUNCH" --host="$HOST_NAME" >>"$LOG" 2>&1 &
    else
      nohup "$LAUNCH" >>"$LOG" 2>&1 &
    fi
  elif command -v npm >/dev/null 2>&1 && [[ -d "$REPO/electron" ]]; then
    log "starting via npm start"
    extra=()
    if [[ -n "$HOST_NAME" ]]; then
      extra=(-- --host="$HOST_NAME")
    fi
    (cd "$REPO/electron" && nohup npm start "${extra[@]}" >>"$LOG" 2>&1 &)
  else
    log "ERROR no Client Electron launch path"
    exit 3
  fi
fi

log "self-update done"
exit 0
