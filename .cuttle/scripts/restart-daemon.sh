#!/usr/bin/env bash
# Restart Cuttle daemon on POSIX (Linux / macOS).
# Run from a standalone terminal — NOT from a Cursor agent hosted by Cuttle chat.
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python3"
if [[ ! -x "$VENV_PYTHON" ]]; then
  VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
fi
DAEMON_REL="src/scripts/cuttle_daemon.py"
DAEMON_ABS="$PROJECT_ROOT/$DAEMON_REL"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing venv python: $VENV_PYTHON" >&2
  exit 1
fi
if [[ ! -f "$DAEMON_ABS" ]]; then
  echo "Missing daemon script: $DAEMON_ABS" >&2
  exit 1
fi

# Headless Host (cuttle-service.sh install): let systemd own the restart so the
# new daemon stays in the unit instead of an orphan nohup copy.
if command -v systemctl >/dev/null 2>&1 \
   && systemctl --user is-active --quiet cuttle.service 2>/dev/null; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "=== DRY RUN ==="
    echo "Would run: systemctl --user restart cuttle.service"
    exit 0
  fi
  echo "Restarting systemd user unit cuttle.service..."
  systemctl --user restart cuttle.service
  systemctl --user --no-pager status cuttle.service | head -n 5 || true
  exit 0
fi

mapfile -t PIDS < <(pgrep -f 'cuttle_daemon\.py' || true)

echo "=== BEFORE ==="
echo "ProjectRoot=$PROJECT_ROOT"
echo "VenvPython=$VENV_PYTHON"
if [[ ${#PIDS[@]} -eq 0 ]]; then
  echo "No cuttle_daemon process found (already stopped?)."
else
  echo "Daemon PID(s): ${PIDS[*]}"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "=== DRY RUN ==="
  echo "Start: $VENV_PYTHON $DAEMON_REL (cwd=$PROJECT_ROOT)"
  echo "kill_loop_survived_missing_pids=True"
  echo "DryRun complete — no processes stopped or started."
  exit 0
fi

if [[ ${#PIDS[@]} -gt 0 ]]; then
  echo "Stopping daemon PID(s): ${PIDS[*]}"
  kill "${PIDS[@]}" 2>/dev/null || true
  sleep 1
  kill -9 "${PIDS[@]}" 2>/dev/null || true
fi

echo "Starting daemon..."
cd "$PROJECT_ROOT"
nohup "$VENV_PYTHON" "$DAEMON_ABS" >/dev/null 2>&1 &
echo "Started PID $!"
