"""
Cuttle Client Daemon — owns the device-worker loop on Client machines.

Why (CUTTLE_WORKERS.md): Electron alone cannot safely "git pull + restart myself"
when the host asks. The host Flask restart is daemon-owned; Client needs the same
pattern — a process that outlives the UI.

Responsibilities:
- Enroll / register / claim jobs against the host coordinator
- Run allowlisted jobs (ping, file_copy, blender_render, shell recipes, cuttle_self_update)
- On cuttle_self_update: schedule a detached updater (stop Electron → git pull → start Client)

Env / desktop-config.json (Electron userData):
  host, httpsPort, httpPort, workerToken, workerId, workerMode
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


SRC_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _userdata() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "cuttle-desktop"


def status_path() -> Path:
    return _userdata() / "client-daemon-status.json"


def desktop_config_path() -> Path:
    return _userdata() / "desktop-config.json"


def load_desktop_config() -> Dict[str, Any]:
    path = desktop_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_status_lock = threading.Lock()


def write_status(patch: Dict[str, Any]) -> None:
    # The heartbeat thread and main() both write; readers (Electron) must never
    # see a torn file, so merge under a lock and replace atomically.
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _status_lock:
        cur: Dict[str, Any] = {}
        if path.is_file():
            try:
                cur = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(cur, dict):
                    cur = {}
            except Exception:
                cur = {}
        cur.update(patch)
        cur["updated_at"] = time.time()
        cur["pid"] = os.getpid()
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def http_json(
    method: str,
    base: str,
    path: str,
    *,
    token: str = "",
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 20,
) -> Dict[str, Any]:
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json", "User-Agent": "cuttle-client-daemon/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    from api.tls_cert import urlopen as tls_urlopen

    # Remote HTTPS: the desktop app's pinned key (PIN_ENV), never unverified.
    try:
        with tls_urlopen(req, timeout=timeout) as resp:
            raw = resp.read() or b"{}"
            return json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code} {path}: {err}") from e


def _bracket(host: str) -> str:
    host = (host or "").strip()
    if not host or host.startswith("[") or ":" not in host:
        return host
    return f"[{host}]"


def coordinator_bases(cfg: Dict[str, Any], host: str) -> list:
    """Ordered coordinator bases from the persisted endpoint policy.

    A single-endpoint policy yields ONLY the selected endpoint, ignoring
    historical companion metadata. Legacy configs
    without a policy keep the historical https-then-http pair.
    """
    ep = cfg.get("endpoint") if isinstance(cfg.get("endpoint"), dict) else None
    if ep and ep.get("kind") == "single":
        port = ep.get("port")
        if (
            ep.get("scheme") not in ("http", "https")
            or ep.get("host") != host
            or type(port) is not int
            or not 1 <= port <= 65535
        ):
            raise ValueError("Invalid saved endpoint; reconnect with a valid URL.")
        return [f"{ep['scheme']}://{_bracket(host)}:{port}"]
    try:
        https_port = int(cfg.get("httpsPort") or 8080)
    except (TypeError, ValueError):
        https_port = 8080
    try:
        http_port = int(cfg.get("httpPort") or 8000)
    except (TypeError, ValueError):
        http_port = 8000
    return [f"https://{_bracket(host)}:{https_port}", f"http://{_bracket(host)}:{http_port}"]


def _poll_pairing(base: str, request_id: str, pairing_secret: str, code: str) -> Dict[str, Any]:
    """Wait up to 10 min for the host owner to approve a pairing request."""
    print(
        f"[CLIENT-DAEMON] pairing pending — approve on the host "
        f"(Jobs -> Devices) with code {code}",
        flush=True,
    )
    deadline = time.time() + 10 * 60
    last = "pending"
    while time.time() < deadline:
        time.sleep(3)
        try:
            data = http_json(
                "POST",
                base,
                f"/api/workers/enroll/{request_id}/poll",
                token="",
                body={"pairing_secret": pairing_secret},
                timeout=10,
            )
        except Exception as e:
            last = str(e)
            continue
        status = str(data.get("status") or "")
        if status == "approved" and data.get("token"):
            return data
        if status in ("denied", "expired"):
            raise RuntimeError(f"pairing {status} on the host")
        last = status or last
    raise RuntimeError(f"pairing approval timed out (last={last})")


def enroll(cfg: Dict[str, Any], bases: list) -> Dict[str, Any]:
    """Enroll against coordinator bases strictly in the given order.

    Single selections contain one base. Legacy pairs retain their order
    from :func:`coordinator_bases`.

    First pairing (no saved credential) files a pairing request and polls
    until the host owner approves; re-enroll with a saved credential returns
    no credential (never echoed) and keeps the saved one.
    """
    worker_id = str(
        cfg.get("workerId") or socket.gethostname() or "cuttle-client"
    ).lower().replace(" ", "-")
    body = {"worker_id": worker_id, "hostname": socket.gethostname()}
    token = str(cfg.get("workerToken") or "")
    headers_tok = token
    import secrets

    # Always pair-capable: a stale saved bearer (host DB reset) answers 202,
    # and the fresh secret lets this client re-pair without manual steps.
    pairing_secret = secrets.token_hex(16)
    body["pairing_secret"] = pairing_secret
    last_err = None
    for base in bases:
        try:
            data = http_json(
                "POST",
                base,
                "/api/workers/enroll",
                token=headers_tok,
                body=body,
                timeout=10,
            )
            if data.get("status") == "pending" and data.get("request_id"):
                return _poll_pairing(
                    base,
                    str(data["request_id"]),
                    pairing_secret,
                    str(data.get("code") or ""),
                )
            if data.get("success"):
                return data
            last_err = data.get("error") or "enroll failed"
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(last_err or "enroll failed")


def save_desktop_config(patch: Dict[str, Any]) -> None:
    path = desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = load_desktop_config()
    cur.update(patch)
    cur["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path.write_text(json.dumps(cur, indent=2), encoding="utf-8")


def main() -> int:
    cfg = load_desktop_config()
    if cfg.get("workerMode") is False:
        print("[CLIENT-DAEMON] workerMode=false — exiting")
        return 0

    host = str(cfg.get("host") or os.environ.get("CUTTLE_FLASK_HOST") or "").strip()
    if not host:
        print("[CLIENT-DAEMON] No host in desktop-config.json — connect Client once first")
        return 2
    # Enrollment, the worker loop, and status share the selected endpoint.
    # Single policies contain no alternate protocol or listener.
    try:
        bases = coordinator_bases(cfg, host)
    except ValueError as exc:
        print(f"[CLIENT-DAEMON] {exc}")
        return 2
    selected_base = bases[0]
    # Remote HTTPS trust = the key the desktop app pinned for this host:port.
    from api.tls_cert import PIN_ENV, desktop_pin

    for base in bases:
        parts = urllib.parse.urlsplit(base)
        if parts.scheme == "https" and parts.port:
            pin = desktop_pin(cfg.get("tlsPins"), host, parts.port)
            if pin:
                os.environ[PIN_ENV] = pin
            break
    http_base = next(
        (b for b in bases if b.startswith("http://") and not b.startswith("https://")),
        "",
    )

    try:
        enrolled = enroll(cfg, bases)
        # Re-enroll never echoes the saved credential — keep it.
        token = str(enrolled.get("token") or cfg.get("workerToken") or "")
        worker_id = str(enrolled.get("worker_id") or cfg.get("workerId") or "")
        save_desktop_config(
            {
                "workerToken": token,
                "workerId": worker_id,
                "workerEnrolledAt": int(time.time() * 1000),
                "clientDaemon": True,
            }
        )
        print(f"[CLIENT-DAEMON] enrolled as {worker_id}")
    except Exception as e:
        token = str(cfg.get("workerToken") or "")
        worker_id = str(cfg.get("workerId") or socket.gethostname()).lower().replace(" ", "-")
        if not token:
            print(f"[CLIENT-DAEMON] enroll failed and no token: {e}")
            return 1
        print(f"[CLIENT-DAEMON] enroll failed ({e}); using saved token")

    os.environ["CUTTLE_DEVICE_WORKERS_ENABLED"] = "1"
    os.environ["CUTTLE_DEVICE_WORKERS_COORDINATOR_URL"] = selected_base
    os.environ["CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP"] = http_base
    os.environ["CUTTLE_DEVICE_WORKER_TOKEN"] = token
    os.environ["CUTTLE_DEVICE_WORKER_ID"] = worker_id
    os.environ["CUTTLE_CLIENT_DAEMON"] = "1"
    os.environ["CUTTLE_REPO_ROOT"] = str(PROJECT_ROOT)
    os.environ.setdefault(
        "CUTTLE_DEVICE_WORKER_LOG",
        str(_userdata() / "client-daemon-worker.log"),
    )

    write_status(
        {
            "state": "running",
            "worker_id": worker_id,
            "coordinator": selected_base,
            "repo": str(PROJECT_ROOT),
            "owns_worker": True,
        }
    )

    stop = threading.Event()

    def heartbeat_status() -> None:
        while not stop.is_set():
            write_status({"state": "running", "worker_id": worker_id})
            stop.wait(5)

    heartbeat = threading.Thread(target=heartbeat_status, daemon=True)
    heartbeat.start()

    from api.device_workers.worker_loop import run_remote_worker_loop

    print(f"[CLIENT-DAEMON] worker loop → {selected_base} id={worker_id}")
    try:
        run_remote_worker_loop(
            should_continue=lambda: not stop.is_set(),
            base_url=selected_base,
        )
    except KeyboardInterrupt:
        print("[CLIENT-DAEMON] stopped")
    finally:
        stop.set()
        heartbeat.join(timeout=2)
        write_status({"state": "stopped"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
