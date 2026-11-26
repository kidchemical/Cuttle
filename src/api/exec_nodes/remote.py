"""Host side of a turn that executes on a node.

``RemoteAdapter`` stands in for an agent's adapter in the harness kernel: the
kernel keeps compiling context, tracking the query and saving the reply on the
host, and only ``execute()`` leaves the machine. The turn rides one targeted
``agent_turn`` mesh job; status lines and the result come back through the
relay rows in ``store`` (see ``model`` for the contract).
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from api.agent_harness.types import AgentResult
from api.exec_nodes import model as wire
from api.exec_nodes import service, store

# A queued turn the node has not claimed (offline, or busy with other work).
START_TIMEOUT_SECONDS = 90.0
# A running turn whose node went silent (it reports at least every few seconds).
SILENCE_TIMEOUT_SECONDS = 75.0
# After Stop, how long the node gets to end its CLI and report back.
CANCEL_GRACE_SECONDS = 25.0
_POLL_SECONDS = 0.3


def _put(status_queue: Any, item: Any) -> None:
    if status_queue is None:
        return
    try:
        status_queue.put(item)
    except Exception:
        pass


def effective_pins(agent_id: str, chat_session_id: Any, model_id: Optional[str],
                   effort: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The model/effort this chat would run with on the host.

    A node knows nothing of the chat's pins or the starred defaults (they are
    host state), so the host resolves them (through the same badge the chat
    shows) and sends them as explicit overrides.
    """
    model_id = (str(model_id).strip() if model_id else "") or None
    effort = (str(effort).strip() if effort else "") or None
    if model_id and effort:
        return model_id, effort
    try:
        from api.chat_metadata import user_badge_metadata

        badge = user_badge_metadata("/" + agent_id, chat_session_id) or {}
        chips = (badge.get("slash_command") or {}).get("chips") or []
        meta = str(chips[0].get("meta") or "") if chips else ""
    except Exception:
        meta = ""
    if not model_id:
        found = re.search(r"\bmodel\s+(\S+)", meta)
        model_id = found.group(1) if found else None
    if not effort:
        found = re.search(r"\beffort\s+(\S+)", meta)
        effort = found.group(1) if found else None
    return model_id, effort


class RemoteAdapter:
    """An agent's adapter whose CLI runs on execution node ``node_id``."""

    def __init__(self, local: Any, manifest: Any, node_id: str, *, chat_cwd: str = "") -> None:
        self._local = local
        self._manifest = manifest
        self.node_id = node_id
        self._chat_cwd = chat_cwd

    def __getattr__(self, name: str) -> Any:
        # Meta commands (/model, /effort, …) and helpers stay host-side.
        return getattr(self._local, name)

    def available(self) -> bool:
        # The node's CLI decides; an unavailable node answers in execute().
        return True

    def resolve_cwd(self, project_path: str) -> str:
        return self._local.resolve_cwd(project_path)

    def load_resume(self, cwd: str, chat_session_id: Optional[str]) -> Optional[str]:
        if not chat_session_id:
            return None
        return store.load_resume(str(chat_session_id), self._manifest.id, self.node_id, cwd)

    def save_resume(self, cwd: str, chat_session_id: Optional[str], cli_session_id: Optional[str]) -> None:
        if chat_session_id:
            store.save_resume(str(chat_session_id), self._manifest.id, self.node_id, cwd, cli_session_id)

    def clear_resume(self, cwd: str, chat_session_id: Optional[str]) -> None:
        self.save_resume(cwd, chat_session_id, None)

    def _refused(self, message: str) -> AgentResult:
        return AgentResult(success=False, output=message, error=message[:300])

    async def execute(
        self,
        prompt: str,
        *,
        cwd: str,
        resume: Optional[str],
        model: Optional[str],
        status_queue: Any = None,
        chat_session_id: Optional[str] = None,
        timeout: float = 3600.0,
        cancel_event: Any = None,
        reasoning_effort: Optional[str] = None,
        **_host_only: Any,
    ) -> AgentResult:
        agent_id = self._manifest.id
        info = service.node(self.node_id, agent_id)
        label = (info or {}).get("label") or self.node_id
        if info is None or info["state"] == wire.STATE_UNAVAILABLE:
            reason = (info or {}).get("reason") or "no longer paired with this Cuttle host"
            return self._refused(
                f"⚠️ **Execution node `{label}` is unavailable** ({reason}). This turn did not run. "
                "Bring the machine online or pick another node for this chat, then send again."
            )
        node_dir = wire.node_cwd(chat_cwd=self._chat_cwd, host_cwd=cwd, prefs=info)
        if not node_dir:
            return self._refused(
                f"⚠️ **No working directory on `{label}` for this chat.** This turn did not run. "
                "Set one in the chat's node selector, or give the node a default directory "
                "(`python -m api.exec_nodes node`)."
            )
        from api.cuttle_brain.context_compiler import _USER_REQUEST_HEADER

        text = wire.rewrite_envelope(prompt, info.get("paths"), _USER_REQUEST_HEADER)
        if not resume:
            note = wire.node_note(label=label, os_name=info.get("os") or "", cwd=node_dir)
            text = f"{note}\n\n{text}"
        model_id, effort = effective_pins(agent_id, chat_session_id, model, reasoning_effort)
        sid = service.session_number(chat_session_id)
        payload: Dict[str, Any] = {
            "contract": wire.CONTRACT,
            "agent_id": agent_id,
            "prompt": text,
            "cwd": node_dir,
            "resume": resume or None,
            "model": model_id,
            "effort": effort,
            "timeout": float(timeout or 3600.0),
            "chat": f"CH-{sid:06d}" if sid is not None else "",
        }
        job_id = uuid.uuid4().hex
        store.open_turn(job_id, self.node_id, str(chat_session_id or ""), payload)
        try:
            from api.device_workers.store import get_store

            mesh = get_store()
            mesh.submit_job(
                job_type=wire.JOB_TYPE,
                job_id=job_id,
                target_worker_id=self.node_id,
                requirements={"agent_exec": True},
                # Metadata only: the prompt and the reply never enter the mesh job.
                params={"agent_id": agent_id, "chat": payload["chat"], "cwd": node_dir},
                submitted_by=payload["chat"] or "agent-turn",
                priority=80,
                ttl_seconds=START_TIMEOUT_SECONDS + 30,
                max_attempts=1,
            )
            _put(status_queue, ("status", f"Running on {label}…"))
            return self._wait(mesh, job_id, label, status_queue, cancel_event, float(timeout or 3600.0))
        finally:
            store.close_turn(job_id)

    def _wait(self, mesh: Any, job_id: str, label: str, status_queue: Any,
              cancel_event: Any, timeout: float) -> AgentResult:
        started = time.monotonic()
        cursor = 0
        claimed = False
        cancel_at: Optional[float] = None
        next_job_check = 0.0
        while True:
            events, turn = store.read_turn(job_id, after=cursor)
            for event_id, item in events:
                cursor = event_id
                if isinstance(item, str):  # status lines only: a node never steers the turn pump
                    _put(status_queue, ("status", item[:8000]))
            if turn and turn["result"] is not None:
                return AgentResult(**wire.result_from_wire(turn["result"]))
            now = time.monotonic()
            claimed = claimed or bool(turn and turn["node_seen"])

            if cancel_event is not None and cancel_event.is_set() and cancel_at is None:
                cancel_at = now
                store.request_cancel(job_id)
                if not claimed:
                    mesh.cancel_job(job_id, reason="cancelled before the node started it")
                    return AgentResult(success=False, error="cancelled")
            if cancel_at is not None and now - cancel_at > CANCEL_GRACE_SECONDS:
                mesh.cancel_job(job_id, reason="cancelled")
                return AgentResult(success=False, error="cancelled")

            if now >= next_job_check:
                next_job_check = now + 1.0
                job = mesh.get_job(job_id) or {}
                status = str(job.get("status") or "")
                if status in ("failed", "cancelled", "succeeded"):
                    # The node posts its result before it finishes the job.
                    _events, turn = store.read_turn(job_id, after=cursor)
                    if turn and turn["result"] is not None:
                        continue
                    if cancel_at is not None:
                        return AgentResult(success=False, error="cancelled")
                    why = str(job.get("error") or "the node ended the turn without a result")
                    return self._refused(f"⚠️ **Turn on `{label}` failed:** {why}")
                if not claimed and now - started > START_TIMEOUT_SECONDS:
                    mesh.cancel_job(job_id, reason="node did not start the turn in time")
                    return self._refused(
                        f"⚠️ **`{label}` did not start this turn within {int(START_TIMEOUT_SECONDS)}s** "
                        "(offline, or busy with other work). It did not run; send again or pick another node."
                    )
                seen = turn["node_seen"] if turn else None
                if claimed and seen and time.time() - float(seen) > SILENCE_TIMEOUT_SECONDS:
                    mesh.cancel_job(job_id, reason="lost contact with the node")
                    return self._refused(
                        f"⚠️ **Lost contact with `{label}` during this turn.** Whatever the agent "
                        "finished there stays on that machine; its reply did not arrive. Send again once "
                        "the node is back online to continue."
                    )
                if now - started > timeout + 180:
                    mesh.cancel_job(job_id, reason="turn budget exceeded")
                    return self._refused(f"⚠️ **Turn on `{label}` exceeded its time budget.**")
            time.sleep(_POLL_SECONDS)
