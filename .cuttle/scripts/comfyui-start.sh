#!/usr/bin/env bash
# Start ComfyUI in low-VRAM mode when a local checkout exists.
set -euo pipefail

ROOT="${COMFYUI_ROOT:-${HOME}/ComfyUI}"
if [[ ! -d "$ROOT" ]]; then
  echo "ComfyUI is not installed at $ROOT. Set COMFYUI_ROOT or run the TRELLIS install docs."
  exit 1
fi

if curl -fsS --max-time 3 http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
  echo "Already running HTTP 200 http://127.0.0.1:8188"
  exit 0
fi

PY="$ROOT/venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/.venv/bin/python"
fi
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

MAIN="$ROOT/main.py"
if [[ ! -f "$MAIN" ]]; then
  echo "ComfyUI main.py not found at $MAIN"
  exit 1
fi

cd "$ROOT"
nohup "$PY" "$MAIN" --lowvram --listen 127.0.0.1 --port 8188 >/dev/null 2>&1 &
echo "Launching ComfyUI from $MAIN — UI at http://127.0.0.1:8188"
