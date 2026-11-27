"""``comfyui_prompt``: run one API-format ComfyUI graph with node-level progress.

The worker talks to a ComfyUI server on its own loopback (never another host):
it opens the WebSocket ``/ws?clientId=<prompt id>`` first, then queues the graph
with ``POST /prompt`` under a prompt id derived from the mesh job id. Events
(``execution_start``, ``execution_cached``, ``executing``, ``progress``,
``executed``, ``execution_error``, ``execution_interrupted``,
``execution_success``) become store progress: nodes done/total, the running
node as the stage, sampler steps, and the latest preview frame. At each node
boundary the adapter samples GPU VRAM and free RAM, so the result carries
per-node seconds and peak VRAM.

Because the prompt id is a function of the job id, a retry after a worker or
lease loss first asks ComfyUI about that id (``/history``, ``/queue``) and
reattaches or collects instead of running the graph twice. Cancel is
``POST /api/jobs/<id>/cancel`` (interrupts if running, dequeues if pending),
and counts only once ComfyUI no longer lists the prompt as running or queued;
otherwise the job keeps its reservation (``WorkloadStillRunning``) and the
worker retries the stop (:func:`stop`).

Params: ``prompt`` (API-format graph, required), ``server`` (default
``http://127.0.0.1:8188``), ``stages`` (optional ``{node_id: label}``),
``start_recipe`` (allowlisted shell recipe that starts ComfyUI when it is not
reachable), ``hard_timeout_seconds`` / ``idle_timeout_seconds``.
"""

from __future__ import annotations

import json
import struct
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from api.device_workers.executor import JobExecError, WorkloadStillRunning

TYPE = "comfyui_prompt"
DEFAULT_SERVER = "http://127.0.0.1:8188"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_RECV_TIMEOUT = 5.0
_SAMPLE_EVERY = 2.0
_PREVIEW_EVERY = 2.0
CANCEL_CONFIRM_SEC = 30.0


def prompt_id_for(job_id: str) -> str:
    """ComfyUI wants a canonical UUID; mesh ids are uuid4 hex, anything else is hashed."""
    try:
        return str(uuid.UUID(hex=str(job_id)))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cuttle-job:{job_id}"))


def _server(params: Dict[str, Any]) -> str:
    return str(params.get("server") or DEFAULT_SERVER).strip().rstrip("/")


def validate(params: Dict[str, Any]) -> Tuple[bool, str]:
    prompt = params.get("prompt")
    if not isinstance(prompt, dict) or not prompt:
        return False, "comfyui_prompt requires params.prompt (API-format graph object)"
    for node_id, node in prompt.items():
        if not isinstance(node, dict) or not node.get("class_type"):
            return False, f"prompt node {node_id!r} has no class_type (export with 'Save (API)')"
    parsed = urlparse(_server(params))
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _LOOPBACK:
        return False, "params.server must be a loopback ComfyUI URL (the worker's own ComfyUI)"
    return True, ""


def stage_labels(prompt: Dict[str, Any], stages: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Node id → readable label: explicit stage map, then the node title, then its class."""
    out: Dict[str, str] = {}
    for node_id, node in prompt.items():
        meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
        out[str(node_id)] = str((stages or {}).get(str(node_id)) or meta.get("title") or node.get("class_type"))
    return out


class Tracker:
    """Folds one prompt's ComfyUI events into progress, node timings and an outcome."""

    def __init__(self, prompt_id: str, labels: Dict[str, str], *, now: Callable[[], float] = time.time):
        self.prompt_id = prompt_id
        self.labels = labels
        self.now = now
        self.done_nodes: set = set()
        self.cached: set = set()
        self.node: Optional[str] = None
        self.node_started = 0.0
        self.node_peak_vram = 0
        self.step: Optional[int] = None
        self.steps: Optional[int] = None
        self.outputs: Dict[str, Any] = {}
        self.timings: List[Dict[str, Any]] = []
        self.outcome: Optional[str] = None  # "success" | "error" | "interrupted"
        self.error = ""
        self.started = False
        self.preview_url = ""

    def _close_node(self, sample: Optional[Dict[str, Any]]) -> None:
        if self.node is None:
            return
        self.done_nodes.add(self.node)
        entry: Dict[str, Any] = {"node": self.node, "label": self.labels.get(self.node, self.node),
                                 "seconds": round(self.now() - self.node_started, 3)}
        if sample:
            entry["peak_vram_mb"] = max(self.node_peak_vram, int(sample.get("vram_used_mb") or 0))
            if sample.get("ram_available_mb") is not None:
                entry["ram_available_mb"] = sample["ram_available_mb"]
        self.timings.append(entry)
        self.node = None

    def observe(self, sample: Optional[Dict[str, Any]]) -> None:
        if sample and self.node is not None:
            self.node_peak_vram = max(self.node_peak_vram, int(sample.get("vram_used_mb") or 0))

    def handle(self, event: Dict[str, Any], sample: Optional[Dict[str, Any]] = None) -> bool:
        """Apply one JSON event; True when it belonged to this prompt."""
        kind = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if data.get("prompt_id") != self.prompt_id:
            return False
        if kind == "execution_start":
            self.started = True
        elif kind == "execution_cached":
            self.started = True
            self.cached.update(str(n) for n in data.get("nodes") or [])
        elif kind == "executing":
            self.started = True
            self._close_node(sample)
            node = data.get("node")
            if node is None:
                self.outcome = self.outcome or "success"
            else:
                self.node = str(node)
                self.node_started = self.now()
                self.node_peak_vram = int((sample or {}).get("vram_used_mb") or 0)
                self.step = self.steps = None
        elif kind == "progress":
            # After a reattach the prompt's ``executing`` event is long gone; the
            # sampler's own events still name its node.
            self.started = True
            if self.node is None and data.get("node") is not None:
                self.node, self.node_started = str(data["node"]), self.now()
            self.step, self.steps = int(data.get("value") or 0), int(data.get("max") or 0)
        elif kind == "executed":
            if data.get("node") is not None:
                self.outputs[str(data["node"])] = data.get("output")
        elif kind == "execution_success":
            self._close_node(sample)
            self.outcome = "success"
        elif kind == "execution_error":
            self._close_node(sample)
            self.outcome = "error"
            node_type = data.get("node_type") or self.labels.get(str(data.get("node_id")), "node")
            self.error = f"{node_type}: {data.get('exception_message') or 'execution error'}".strip()
        elif kind == "execution_interrupted":
            self._close_node(sample)
            self.outcome = "interrupted"
        return True

    def progress(self, *, queue_position: Optional[int] = None) -> Dict[str, Any]:
        total = len(self.labels)
        done = len(self.done_nodes | self.cached)
        label = self.labels.get(self.node or "", "")
        if not self.started:
            message = (f"queued in ComfyUI ({queue_position} ahead)" if queue_position
                       else "waiting for ComfyUI")
        elif label and self.steps:
            message = f"{label} {self.step}/{self.steps}"
        else:
            message = label or "running"
        out: Dict[str, Any] = {"units_done": min(done, total), "units_total": total,
                               "message": message, "updated_at": self.now()}
        if label:
            out["stage"] = label
            out["last_unit_id"] = self.node
        if self.steps:
            out["step"], out["steps"] = self.step, self.steps
        if self.preview_url:
            out["preview_url"] = self.preview_url
        return out


def decode_preview(frame: bytes) -> Optional[Tuple[str, bytes]]:
    """Binary WS frame → ``(extension, image bytes)`` for preview events; else None."""
    if len(frame) < 8:
        return None
    event = struct.unpack(">I", frame[:4])[0]
    if event == 1:  # PREVIEW_IMAGE: >I image type (1 jpeg, 2 png) + bytes
        kind = struct.unpack(">I", frame[4:8])[0]
        return ("png" if kind == 2 else "jpg"), frame[8:]
    if event == 4:  # PREVIEW_IMAGE_WITH_METADATA: >I json length + json + bytes
        size = struct.unpack(">I", frame[4:8])[0]
        try:
            meta = json.loads(frame[8:8 + size].decode("utf-8"))
        except ValueError:
            meta = {}
        return ("png" if "png" in str(meta.get("image_type") or "") else "jpg"), frame[8 + size:]
    return None


def _sample() -> Dict[str, Any]:
    """GPU VRAM in use, free RAM, and the ComfyUI server's own RSS and VRAM."""
    from api.device_workers import capabilities

    gpus, apps = capabilities._gpus()
    out: Dict[str, Any] = {"vram_used_mb": max((g["vram_used_mb"] for g in gpus), default=0)}
    try:
        import psutil

        out["ram_available_mb"] = int(psutil.virtual_memory().available / (1024**2))
    except ImportError:
        pass
    service = capabilities._processes(apps, gpus)[1].get("comfyui") or {}
    if service:
        out["service_rss_mb"] = int(service.get("rss_mb") or 0)
        out["service_vram_mb"] = sum(int(v) for v in (service.get("vram_mb") or {}).values())
    return out


def _cancel(base: str, pid: str) -> bool:
    """Cancel a prompt; True once ComfyUI lists it neither running nor queued."""
    deadline = time.time() + CANCEL_CONFIRM_SEC
    while True:
        try:
            requests.post(f"{base}/api/jobs/{pid}/cancel", timeout=15)
            entry, position = _known(base, pid)
            if position is None:
                return True
        except requests.ConnectionError:
            return True  # nothing listens: the server (and the prompt) is gone
        except requests.RequestException:
            pass  # a hung server may still be running it
        if time.time() >= deadline:
            return False
        time.sleep(2)


def stop(params: Dict[str, Any]) -> bool:
    return _cancel(_server(params), prompt_id_for(str(params.get("_job_id") or "")))


def _preview_target(job_id: str):
    """Where previews go, and their URL — only on the host, whose /output is served."""
    from api.device_workers.config import coordinator_base_url
    from core.runtime_paths import output_dir

    if coordinator_base_url():
        return None, ""
    folder = output_dir() / "jobs" / job_id
    return folder, f"/output/jobs/{job_id}"


def _reachable(base: str) -> bool:
    try:
        return requests.get(f"{base}/system_stats", timeout=5).ok
    except requests.RequestException:
        return False


def _ensure_server(base: str, params: Dict[str, Any]) -> None:
    if _reachable(base):
        return
    recipe = str(params.get("start_recipe") or "").strip()
    if not recipe:
        raise JobExecError(f"ComfyUI not reachable at {base} (start it, or pass params.start_recipe)")
    from api.device_workers.executor import _shell_recipe

    _shell_recipe({"recipe": recipe, "_cancelled": params.get("_cancelled")})
    deadline = time.time() + 300
    while time.time() < deadline:
        if _reachable(base):
            return
        time.sleep(3)
    raise JobExecError(f"ComfyUI still not reachable at {base} after recipe {recipe}")


def _known(base: str, pid: str) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """``(history entry or None, queue position or None)`` for a prompt id."""
    hist = requests.get(f"{base}/history/{pid}", timeout=15).json() or {}
    if pid in hist:
        return hist[pid], None
    queue = requests.get(f"{base}/queue", timeout=15).json() or {}
    for item in queue.get("queue_running") or []:
        if len(item) > 1 and item[1] == pid:
            return None, 0
    for pos, item in enumerate(sorted(queue.get("queue_pending") or [], key=lambda i: i[0])):
        if len(item) > 1 and item[1] == pid:
            return None, pos + 1
    return None, None


def _history_result(entry: Dict[str, Any]) -> Tuple[str, Dict[str, Any], str]:
    status = entry.get("status") or {}
    outputs = entry.get("outputs") or {}
    if status.get("status_str") == "error":
        for name, data in status.get("messages") or []:
            if name == "execution_error":
                return "error", outputs, f"{data.get('node_type')}: {data.get('exception_message')}"
        return "error", outputs, "execution error"
    return "success", outputs, ""


def _files(outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
    files = []
    for node_id, out in (outputs or {}).items():
        for key, items in (out or {}).items():
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("filename"):
                    files.append({"node": node_id, "kind": key, "filename": item["filename"],
                                  "subfolder": item.get("subfolder") or "", "type": item.get("type") or ""})
    return files


def run(params: Dict[str, Any]) -> Dict[str, Any]:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    from api.device_workers.job_policy import resolve_timeouts

    ok, err = validate(params)
    if not ok:
        raise JobExecError(err)
    job_id = str(params.get("_job_id") or uuid.uuid4().hex)
    pid = prompt_id_for(job_id)
    base = _server(params)
    on_progress = params.get("_on_progress") if callable(params.get("_on_progress")) else (lambda _p: None)
    cancelled = params.get("_cancelled") if callable(params.get("_cancelled")) else (lambda: False)
    idle_s, hard_s, _mode = resolve_timeouts(job_type=TYPE, params=params)
    prompt = params["prompt"]
    tracker = Tracker(pid, stage_labels(prompt, params.get("stages")))
    started = time.time()

    _ensure_server(base, params)
    ws = connect(base.replace("http", "ws", 1) + f"/ws?clientId={pid}", open_timeout=15, max_size=None)
    try:
        entry, position = _known(base, pid)
        tracker.started = position == 0  # reattached to a prompt ComfyUI is running
        if entry is None and position is None:
            resp = requests.post(f"{base}/prompt", json={"prompt": prompt, "client_id": pid, "prompt_id": pid},
                                 timeout=60)
            if not resp.ok:
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                detail = (body.get("error") or {}).get("message") or resp.text[:300]
                node_errors = "; ".join(
                    f"{nid}: {(e.get('errors') or [{}])[0].get('message', '')}"
                    for nid, e in (body.get("node_errors") or {}).items())
                raise JobExecError(f"ComfyUI rejected the prompt: {detail} {node_errors}".strip())
        preview_dir, preview_url = _preview_target(job_id)
        last_event = last_sample_at = last_preview_at = last_beat = time.time()
        sample: Optional[Dict[str, Any]] = None
        peak_ram = peak_vram = 0
        while entry is None and tracker.outcome is None:
            now = time.time()
            if cancelled():
                if not _cancel(base, pid):
                    raise WorkloadStillRunning("cancelled, but ComfyUI did not confirm the prompt stopped")
                raise JobExecError("cancelled: ComfyUI prompt cancelled")
            if now - started > hard_s:
                if not _cancel(base, pid):
                    raise WorkloadStillRunning(f"timed out after {int(hard_s)}s; ComfyUI did not confirm the cancel")
                raise JobExecError(f"ComfyUI prompt timed out after {int(hard_s)}s")
            if idle_s and now - last_event > idle_s:
                if not _cancel(base, pid):
                    raise WorkloadStillRunning(f"no ComfyUI progress for {int(idle_s)}s; cancel not confirmed")
                raise JobExecError(f"no ComfyUI progress for {int(idle_s)}s")
            if now - last_sample_at >= _SAMPLE_EVERY:
                sample, last_sample_at = _sample(), now
                tracker.observe(sample)
                peak_ram = max(peak_ram, int(sample.get("service_rss_mb") or 0))
                peak_vram = max(peak_vram, int(sample.get("service_vram_mb") or 0))
            try:
                frame = ws.recv(timeout=_RECV_TIMEOUT)
            except TimeoutError:
                # Quiet socket: a long node, a queue wait, or events we missed.
                entry, position = _known(base, pid)
                if position is not None and not tracker.started:
                    last_event = now  # moving through ComfyUI's own queue counts as progress
                if now - last_beat >= 10:
                    on_progress(tracker.progress(queue_position=position))
                    last_beat = now
                continue
            if isinstance(frame, (bytes, bytearray)):
                preview = decode_preview(bytes(frame))
                if preview and preview_dir is not None and now - last_preview_at >= _PREVIEW_EVERY:
                    ext, data = preview
                    preview_dir.mkdir(parents=True, exist_ok=True)
                    (preview_dir / f"preview.{ext}").write_bytes(data)
                    tracker.preview_url = f"{preview_url}/preview.{ext}?t={int(now)}"
                    last_preview_at = now
                    on_progress(tracker.progress())
                continue
            try:
                event = json.loads(frame)
            except ValueError:
                continue
            if tracker.handle(event, sample):
                last_event = now
                on_progress(tracker.progress())
                last_beat = now
    except (requests.RequestException, OSError, ConnectionClosed) as exc:
        # Lost the server mid-prompt. Unless it is provably gone (nothing listens) or no
        # longer has the prompt, keep the hold: a hung server may still be running it,
        # and the retry, pinned to this worker, reattaches to the same prompt id.
        try:
            live = _known(base, pid)[1] is not None
        except requests.ConnectionError:
            live = False
        except requests.RequestException:
            live = True
        if live:
            raise WorkloadStillRunning(f"lost ComfyUI mid-prompt ({exc})", retry=True) from exc
        raise
    finally:
        ws.close()

    if entry is not None:  # finished while we were away, or before this attempt
        outcome, outputs, error = _history_result(entry)
        tracker.outputs = outputs or tracker.outputs
    else:
        outcome, error = tracker.outcome, tracker.error
        if outcome == "success":
            # executed events carry outputs; history is authoritative for the full set
            hist = (requests.get(f"{base}/history/{pid}", timeout=15).json() or {}).get(pid) or {}
            tracker.outputs = hist.get("outputs") or tracker.outputs
    if outcome == "error":
        raise JobExecError(f"ComfyUI execution error: {error}")
    if outcome == "interrupted":
        raise JobExecError("ComfyUI prompt was interrupted")
    return {
        "ok": True,
        "kind": TYPE,
        "prompt_id": pid,
        "server": base,
        "files": _files(tracker.outputs),
        "outputs": tracker.outputs,
        "node_timings": tracker.timings,
        "cached_nodes": sorted(tracker.cached),
        "elapsed_seconds": round(time.time() - started, 2),
        **({"peak_ram_mb": peak_ram} if peak_ram else {}),
        **({"peak_vram_mb": peak_vram} if peak_vram else {}),
    }
