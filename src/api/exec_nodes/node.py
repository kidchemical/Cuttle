"""Node side: run one agent CLI turn for the Cuttle host this machine is paired with.

Runs inside the device-worker claim loop (``api.device_workers.worker_loop``)
on the node, never on the host's own chat path. Off unless this machine opts
in (``device_workers.agent_exec.enabled`` in its settings, or
``CUTTLE_NODE_AGENT_EXEC=1``): pairing as a worker alone never lets the host
start an agent here. The CLI uses this machine's own login; no credential
comes from the host.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from api.exec_nodes import model

JOB_TYPE = model.JOB_TYPE

_POST_SECONDS = 0.5
_KEEPALIVE_SECONDS = 3.0
# The host is unreachable this long: stop the CLI rather than run unobserved.
_HOST_LOST_SECONDS = 120.0
_MAX_BUFFER = 400

_lock = threading.Lock()
_active: Dict[str, threading.Thread] = {}
_agents_cache: Dict[str, Any] = {"at": 0.0, "agents": []}


def enabled() -> bool:
    from api.device_workers.config import agent_exec_enabled

    return agent_exec_enabled()


def running() -> int:
    with _lock:
        for job_id in [j for j, t in _active.items() if not t.is_alive()]:
            _active.pop(job_id, None)
        return len(_active)


def slots_free() -> bool:
    from api.device_workers.config import agent_exec_max_turns

    return running() < agent_exec_max_turns()


def available_agents() -> List[str]:
    """Harness ids whose CLI is installed here (rechecked once a minute)."""
    now = time.monotonic()
    if _agents_cache["at"] and now - _agents_cache["at"] < 60.0:
        return list(_agents_cache["agents"])
    found: List[str] = []
    try:
        from api.agent_harness.catalog import get_agent, list_agents

        for agent_id in list_agents():
            pair = get_agent(agent_id)
            try:
                if pair and pair[1].available():
                    found.append(agent_id)
            except Exception:
                continue
    except Exception:
        found = []
    _agents_cache.update(at=now, agents=found)
    return list(found)


def advertise() -> Dict[str, Any]:
    """Capability keys a worker adds to its heartbeat."""
    on = enabled()
    return {"agent_exec": on, "agents": available_agents() if on else []}


def start(job_id: str, run: Callable[[], None]) -> None:
    """Run an agent turn beside the claim loop, so a long turn never blocks it."""
    thread = threading.Thread(target=run, name=f"agent-turn-{job_id[:8]}", daemon=True)
    with _lock:
        _active[job_id] = thread
    thread.start()


class _Relay:
    """``status_queue`` stand-in that streams the CLI's status to the host."""

    def __init__(self, client: Any, job_id: str, cancel: threading.Event) -> None:
        self._client = client
        self._job_id = job_id
        self._cancel = cancel
        self._buffer: List[Any] = []
        self._buffer_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="agent-turn-relay", daemon=True)
        self._thread.start()

    def put(self, item: Any, *_args: Any, **_kwargs: Any) -> None:
        line = model.status_line(item)
        if line is None:
            return
        with self._buffer_lock:
            if len(self._buffer) < _MAX_BUFFER:
                self._buffer.append(line)

    put_nowait = put
    put_preview = put

    def _post(self, items: List[Any], result: Optional[Dict[str, Any]] = None) -> bool:
        if self._client.post_agent_turn(self._job_id, items, result):
            self._cancel.set()
        return True

    def _pump(self) -> None:
        last_ok = time.monotonic()
        last_post = 0.0
        while not self._stop.wait(_POST_SECONDS):
            with self._buffer_lock:
                items, self._buffer = self._buffer, []
            now = time.monotonic()
            if not items and now - last_post < _KEEPALIVE_SECONDS:
                continue
            try:
                self._post(items)
                last_ok = last_post = time.monotonic()
            except Exception as exc:
                with self._buffer_lock:
                    self._buffer = (items + self._buffer)[:_MAX_BUFFER]
                # The host dropped this turn (Stop, timeout): end the CLI.
                if "HTTP 410" in str(exc) or now - last_ok > _HOST_LOST_SECONDS:
                    self._cancel.set()
                last_post = now

    def finish(self, result: Dict[str, Any]) -> None:
        """Flush what is left and deliver the result (retried: it is the reply)."""
        self._stop.set()
        self._thread.join(timeout=25)
        with self._buffer_lock:
            items, self._buffer = self._buffer, []
        last: Optional[Exception] = None
        for attempt in range(8):
            try:
                self._post(items, result)
                return
            except Exception as exc:
                last = exc
                if "HTTP 410" in str(exc):
                    break
                time.sleep(min(2.0 * (attempt + 1), 10.0))
        raise RuntimeError(f"could not deliver the turn result to the host: {last}")


def run_agent_turn(job: Dict[str, Any], auth_token: Optional[str] = None) -> Dict[str, Any]:
    """Executor entry for an ``agent_turn`` mesh job (see ``model`` for the contract)."""
    from api.device_workers.client import DeviceWorkerClient
    from api.device_workers.executor import JobExecError

    if not enabled():
        raise JobExecError(
            "agent execution is not enabled on this machine "
            "(set device_workers.agent_exec.enabled or CUTTLE_NODE_AGENT_EXEC=1 here)"
        )
    job_id = str(job.get("id") or "")
    client = DeviceWorkerClient(token=auth_token) if auth_token else DeviceWorkerClient()
    if not client.base_url:
        raise JobExecError("agent turns need a remote coordinator (this is the host's own worker)")
    try:
        payload = client.fetch_agent_turn(job_id)
    except Exception as exc:
        raise JobExecError(f"could not fetch the turn from the host: {exc}") from exc
    if int(payload.get("contract") or 0) != model.CONTRACT:
        raise JobExecError(
            f"turn contract v{payload.get('contract')} is not supported here "
            f"(this node speaks v{model.CONTRACT}; update Cuttle on the older side)"
        )

    cancel = threading.Event()
    relay = _Relay(client, job_id, cancel)
    started = time.time()
    try:
        result = _execute(payload, relay, cancel, job_id)
    except Exception as exc:
        result = {"success": False, "output": f"⚠️ **Node error:** {exc}", "error": str(exc)[:300]}
    if cancel.is_set():
        _end_turn_processes(job_id)
    wire = model.result_to_wire(result)
    relay.finish(wire)
    return {"ok": wire["success"], "agent_id": payload.get("agent_id"), "model": wire["model"],
            "seconds": round(time.time() - started, 1)}


def _end_turn_processes(job_id: str) -> None:
    """Stop means stop: end what this turn started here and left running.

    Nobody watches a node's processes from the host, so a stopped turn does
    not leave work behind. The turn's run marker finds even detached children
    (owner: ``api.jobs.processes``); earlier turns' processes are not touched.
    """
    try:
        from api.jobs import processes

        mine = [r for r in processes.scan(max_age=0) if r.get("run_id") == job_id]
        for rec in sorted(mine, key=lambda r: r.get("create_time") or 0):
            try:
                processes.end_process(rec["pid"], rec["create_time"])
            except (LookupError, PermissionError):
                continue
    except Exception:
        pass


def _execute(payload: Dict[str, Any], relay: _Relay, cancel: threading.Event, job_id: str) -> Any:
    from api.agent_harness.catalog import get_agent
    from api.agent_harness.kernel import _adapter_execute_kwargs
    from core.agent_cli_env import agent_operation_context

    agent_id = str(payload.get("agent_id") or "")
    pair = get_agent(agent_id)
    if not pair:
        return {"success": False, "output": f"⚠️ Unknown agent `{agent_id}` on this node."}
    manifest, adapter = pair
    label = manifest.label or manifest.id
    if not adapter.available():
        hint = manifest.missing_cli_hint or manifest.install_hint or f"{label} CLI not found on PATH."
        return {"success": False, "output": f"❌ **{label} is not installed on this node.** {hint}"}
    cwd = str(payload.get("cwd") or "").strip()
    if not cwd or not os.path.isdir(cwd):
        return {"success": False,
                "output": f"⚠️ **Working directory not found on this node:** `{cwd or '(none)'}`. "
                          "Set the chat's directory in its node selector."}
    kwargs = _adapter_execute_kwargs(
        adapter,
        cwd=cwd,
        resume=payload.get("resume") or None,
        model=payload.get("model") or None,
        status_queue=relay,
        # The chat lives on the host: nothing here is keyed by its id.
        chat_session_id=None,
        timeout=float(payload.get("timeout") or 3600.0),
        cancel_event=cancel,
        reasoning_effort=payload.get("effort") or None,
    )
    # Run markers on the CLI's processes: the host chat's number and this turn.
    chat_no = "".join(ch for ch in str(payload.get("chat") or "") if ch.isdigit()).lstrip("0")
    with agent_operation_context(session_id=chat_no or job_id, agent_id=manifest.id,
                                 model=payload.get("model"), run_id=job_id, project_path=cwd):
        return asyncio.run(adapter.execute(str(payload.get("prompt") or ""), **kwargs))
