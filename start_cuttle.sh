#!/usr/bin/env bash
# Start the Cuttle daemon (Flask + tray + optional Discord) using the project venv.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/.venv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  echo "Create the venv first: python3 -m venv .venv && .venv/bin/pip install -r src/requirements/requirements.txt" >&2
  exit 1
fi
exec "$PY" "$ROOT/src/scripts/cuttle_daemon.py" "$@"
