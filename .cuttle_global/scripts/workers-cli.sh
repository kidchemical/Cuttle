#!/usr/bin/env bash
# Agent-agnostic workers platform CLI bridge for .cuttle_global/actions (POSIX).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
verb="${CUTTLE_PARAM_VERB:-}"
forward=("$@")
if [[ -z "$verb" && ${#forward[@]} -gt 0 ]]; then
  verb="${forward[0]}"
  forward=("${forward[@]:1}")
fi
if [[ -z "$verb" ]]; then
  echo '{"success":false,"error":"missing verb (list|submit|status|cancel|wait|plan|blender-shard|batch-status|batch-watch|self-update)"}'
  exit 1
fi
exec "$PY" -m api.device_workers.cli "$verb" "${forward[@]}"
