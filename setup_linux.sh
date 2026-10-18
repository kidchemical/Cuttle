#!/usr/bin/env bash
# Guided Linux setup: venv, Python deps, desktop deps, then launch the Host.
# Options: --browser (no Node/Electron), --no-launch, --launch-only.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP="$ROOT/src/scripts/setup_cuttle.py"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" "$SETUP" "$@"
fi
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$candidate" "$SETUP" "$@"
  fi
done
echo "Python 3.11+ is required. On Ubuntu: sudo apt install python3 python3-venv (22.04: python3.11 python3.11-venv)." >&2
exit 1
