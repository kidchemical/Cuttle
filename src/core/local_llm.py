"""
Local LLM backend abstraction for Cuttle.

Cuttle talks to local models through an OpenAI-compatible HTTP API. Two
interchangeable backends are supported, selected via the LOCAL_LLM_BACKEND
env var:

  - "ollama"   (default) : Ollama server. OpenAI API at http://localhost:11434/v1,
                            native API (model listing) at http://localhost:11434/api/tags
  - "llamacpp"           : llama.cpp `llama-server --jinja`. OpenAI API at
                            e.g. http://127.0.0.1:8081/v1

Everything is read from the environment at call time, so editing src/.env and
reloading the daemon switches backends without code changes.

Relevant env vars:
  LOCAL_LLM_BACKEND     ollama | llamacpp                     (default: ollama)
  LLAMACPP_BASE_URL     OpenAI base URL of llama-server        (default: http://127.0.0.1:8081/v1)
  LLAMACPP_MODEL        model id/alias to request (optional)   (default: auto-detect / "default")
  LLAMACPP_API_KEY      dummy key for the OpenAI client        (default: "llamacpp")
  OLLAMA_BASE_URL       OpenAI base URL of Ollama              (default: http://localhost:11434/v1)
  OLLAMA_MODEL          fallback Ollama model                  (default: llama3)
  OLLAMA_API_KEY        dummy key for the OpenAI client        (default: "ollama")
"""

import os
import math
import threading
import time
from contextlib import contextmanager

_LLAMACPP_ALIASES = {
    'llamacpp', 'llama.cpp', 'llama_cpp', 'llama-cpp', 'llama', 'llama-server', 'llamaserver',
}


def get_local_backend() -> str:
    """Return the active local backend id: 'ollama' or 'llamacpp'."""
    val = (os.getenv('LOCAL_LLM_BACKEND') or 'ollama').strip().lower()
    if val in _LLAMACPP_ALIASES:
        return 'llamacpp'
    return 'ollama'


def is_llamacpp() -> bool:
    return get_local_backend() == 'llamacpp'


def get_local_label() -> str:
    """Human-facing name for status strings and query-report notes."""
    return 'llama.cpp' if is_llamacpp() else 'Ollama'


def get_local_base_url() -> str:
    """OpenAI-compatible base URL (no trailing slash) used for chat completions."""
    if is_llamacpp():
        base = os.getenv('LLAMACPP_BASE_URL', 'http://127.0.0.1:8081/v1')
    else:
        base = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1')
    return base.rstrip('/')


def get_local_native_base_url() -> str:
    """Server root without the trailing /v1 (used for Ollama's native /api/tags)."""
    base = get_local_base_url()
    if base.endswith('/v1'):
        base = base[:-len('/v1')]
    return base.rstrip('/')


def get_local_api_key() -> str:
    """Dummy API key for the OpenAI client (local servers ignore it but the SDK requires one)."""
    if is_llamacpp():
        return os.getenv('LLAMACPP_API_KEY', 'llamacpp')
    return os.getenv('OLLAMA_API_KEY', 'ollama')


def list_local_models() -> list:
    """List available model ids from the active backend's OpenAI /v1/models endpoint."""
    try:
        import requests as _requests
        resp = _requests.get(f"{get_local_base_url()}/models", timeout=5)
        if resp.ok:
            data = resp.json().get('data', []) or []
            return [m.get('id') for m in data if m.get('id')]
    except Exception:
        return []
    return []


_DEFAULT_MODEL_SENTINELS = {'', 'default', 'local-default', 'local_default', 'auto'}


def resolve_local_model(requested, with_tools: bool = False) -> str:
    """Resolve which model id to request.

    Order: explicit (non-sentinel) request -> backend-specific default.
    The node editor's "Default Local Model" option sends the literal
    "local-default"; treat that (and "default"/"auto") as "use the backend
    default" rather than a real model id. llama.cpp uses ``LLAMACPP_MODEL`` or
    its single loaded model. Ollama uses ``OLLAMA_MODEL``, then Settings ->
    completion models -> Local (the setting cheap completions use), then a
    default that suits the call.
    """
    req_norm = str(requested).strip().lower() if requested is not None else ''
    if req_norm and req_norm not in _DEFAULT_MODEL_SENTINELS:
        return str(requested).strip()

    if is_llamacpp():
        env_model = (os.getenv('LLAMACPP_MODEL') or '').strip()
        if env_model:
            return env_model
        models = list_local_models()
        if models:
            return models[0]
        # llama-server with a single loaded model ignores the model field.
        return 'default'

    env_model = (os.getenv('OLLAMA_MODEL') or '').strip()
    if env_model:
        return env_model
    configured = _settings_local_model()
    if configured:
        return configured
    # Tool rounds need a model that emits OpenAI-style tool_calls reliably.
    return 'qwen2.5:latest' if with_tools else 'llama3'


def _settings_local_model() -> str:
    """Settings ``completion_models.local`` (owned by api.completion_providers)."""
    try:
        from managers.settings_manager import get_settings_manager

        models = get_settings_manager().get_setting('completion_models') or {}
    except Exception:
        return ''
    if not isinstance(models, dict):
        return ''
    return str(models.get('local') or '').strip()


def local_reachable(timeout: float = 0.6) -> bool:
    """Return True if the active local backend answers on its OpenAI /v1/models endpoint."""
    try:
        import requests as _requests
        resp = _requests.get(f"{get_local_base_url()}/models", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# On-demand llama-server lifecycle (llamacpp backend only)
# ---------------------------------------------------------------------------

def get_llamacpp_start_script() -> str:
    """Path to the PowerShell script that starts llama-server (LLAMACPP_START_SCRIPT)."""
    return (os.getenv('LLAMACPP_START_SCRIPT') or r'F:\llama.cpp\start-qwen-coder.ps1').strip()


def _llamacpp_log_path():
    from pathlib import Path
    log_dir = Path.home() / 'cuttle_logs'
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return log_dir / 'llamacpp.log'


def launch_llamacpp_detached() -> tuple:
    """Start llama-server fully detached from the calling process.

    Uses a short-lived intermediary (PowerShell Start-Process) so the server is
    NOT part of Flask's process tree — the daemon restarts Flask with
    `taskkill /T`, and a directly-spawned child would be killed with it. The
    server stays up until the user stops it (or asks Cuttle to).

    Returns (ok, error_message). ok=True when the server is already reachable
    or the launch was kicked off.
    """
    import subprocess
    import sys
    from datetime import datetime
    from pathlib import Path

    if not is_llamacpp():
        return False, 'LOCAL_LLM_BACKEND is not llamacpp'
    if local_reachable(timeout=1.5):
        return True, ''

    script_path = Path(get_llamacpp_start_script())
    if not script_path.is_file():
        return False, (
            f'llama-server start script not found: {script_path} '
            '(set LLAMACPP_START_SCRIPT in src/.env)'
        )

    log_path = _llamacpp_log_path()
    try:
        with open(log_path, 'a', encoding='utf-8') as logf:
            logf.write(f"\n--- llama-server launched on demand {datetime.now().isoformat()} ---\n")
    except Exception:
        pass

    try:
        if sys.platform == 'win32':
            # Inner PS runs the start script appending all output to the log.
            # Start-Process detaches it: once this outer PS exits, the server
            # is orphaned from Flask's tree and survives Flask restarts.
            inner = f"& ''{script_path}'' 2>&1 | Out-File -FilePath ''{log_path}'' -Append -Encoding utf8"
            outer = (
                "Start-Process powershell -WindowStyle Hidden "
                f"-WorkingDirectory '{script_path.parent}' "
                "-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',"
                f"'{inner}'"
            )
            subprocess.run(
                ['powershell', '-NoProfile', '-Command', outer],
                capture_output=True,
                timeout=30,
            )
        else:
            with open(log_path, 'a', encoding='utf-8') as logf:
                subprocess.Popen(
                    ['bash', str(script_path)],
                    cwd=str(script_path.parent),
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
    except Exception as e:
        return False, f'Failed to launch llama-server: {e}'
    return True, ''


def stop_llamacpp() -> bool:
    """Force-stop any running llama-server processes. Returns True if the stop command ran."""
    import subprocess
    import sys
    if sys.platform != 'win32':
        try:
            subprocess.run(['pkill', '-f', 'llama-server'], capture_output=True, timeout=15)
            return True
        except Exception:
            return False
    try:
        subprocess.run(
            [
                'powershell', '-NoProfile', '-Command',
                "Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue | Stop-Process -Force",
            ],
            capture_output=True,
            timeout=15,
        )
        return True
    except Exception:
        return False


# One local inference lane, with bounded/cancellable waiting (F10).

_request_lock = threading.Lock()


class LocalRequestCancelled(RuntimeError):
    """A stopped or superseded chat must not start/retry local inference."""


class LocalQueueTimeout(TimeoutError):
    """The local inference queue did not become available in time."""


def check_local_cancelled(cancelled):
    if cancelled():
        raise LocalRequestCancelled("Local inference cancelled.")


@contextmanager
def local_request_slot(*, cancelled=lambda: False, on_wait=lambda: None, timeout=None):
    """Serialize inference; poll Stop while queued and release only owned locks."""
    wait_limit = float(timeout if timeout is not None else os.getenv('LOCAL_LLM_QUEUE_TIMEOUT_SEC', '120'))
    if not math.isfinite(wait_limit) or wait_limit <= 0:
        raise ValueError("LOCAL_LLM_QUEUE_TIMEOUT_SEC must be finite and positive")
    check_local_cancelled(cancelled)
    acquired = _request_lock.acquire(blocking=False)
    waited = not acquired
    try:
        if waited:
            on_wait()
            deadline = time.monotonic() + wait_limit
            while not acquired:
                check_local_cancelled(cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalQueueTimeout("Local inference queue timed out; retry when the active request finishes.")
                acquired = _request_lock.acquire(timeout=min(0.1, remaining))
        check_local_cancelled(cancelled)
        yield waited
    finally:
        if acquired:
            _request_lock.release()


def chat_completion_with_fallback(client, base_kwargs, preferred_model, *, cancelled=lambda: False):
    """Own local model selection/retries, including empty-response fallback.

    Cancellation never falls through to another model. The caller holds the
    request slot across tool rounds, and supplies only the completion transport.
    """
    check_local_cancelled(cancelled)
    models = list_local_models()
    candidates = list(dict.fromkeys([preferred_model] + [m for m in models if m]))
    max_attempts = max(1, int(os.getenv('OLLAMA_FALLBACK_MAX_ATTEMPTS', '5')))
    last_err = None
    for model in candidates[:max_attempts]:
        check_local_cancelled(cancelled)
        try:
            response = client.chat.completions.create(**{**base_kwargs, 'model': model})
            check_local_cancelled(cancelled)
            message = response.choices[0].message
            if not getattr(message, 'tool_calls', None) and not str(getattr(message, 'content', None) or '').strip():
                last_err = RuntimeError("Empty content from local model")
                continue
            return response
        except LocalRequestCancelled:
            raise
        except Exception as exc:
            last_err = exc
    raise last_err or RuntimeError("Local model call failed")
