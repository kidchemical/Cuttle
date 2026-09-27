#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ELECTRON="$ROOT/electron/node_modules/electron/dist/electron"
if [[ ! -x "$ELECTRON" ]]; then
  ELECTRON="$ROOT/electron/dist/linux-unpacked/cuttle-desktop"
fi
export ELECTRON_DISABLE_SANDBOX=1
export PATH="$HOME/.local/opt/node/bin:$ROOT/.venv/bin:${PATH:-}"
cd "$ROOT/electron"
exec "$ELECTRON" --no-sandbox --mode=host --class=CuttleHost "$ROOT/electron"
