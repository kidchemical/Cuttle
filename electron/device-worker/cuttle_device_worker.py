#!/usr/bin/env python3
"""
Self-contained Cuttle device-worker sidecar (Electron Client).

Stdlib only — no venv / requests / Flask imports required.
Shipped inside the Electron asar and copied to userData on Client connect.

Env:
  CUTTLE_DEVICE_WORKERS_COORDINATOR_URL  e.g. https://192.168.1.20:8080
  CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP  optional http://host:8000 fallback
  CUTTLE_DEVICE_WORKERS_TOKEN            bearer from auto-enroll
  CUTTLE_DEVICE_WORKER_ID                stable id (default: hostname)
  CUTTLE_DEVICE_WORKER_LOG               optional log path
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def _log_path() -> Path:
    explicit = (os.environ.get("CUTTLE_DEVICE_WORKER_LOG") or "").strip()
    if explicit:
        return Path(explicit)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("HOME") or "."
    return Path(base) / "Cuttle" / "device-worker.log"


LOG_FILE = _log_path()


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _cpu_ram() -> Dict[str, Any]:
    out: Dict[str, Any] = {"cpu_pct": None, "ram_used_gb": None, "ram_total_gb": None}
    try:
        import psutil  # optional

        out["cpu_pct"] = float(psutil.cpu_percent(interval=0.1))
        mem = psutil.virtual_memory()
        out["ram_used_gb"] = round(mem.used / (1024**3), 2)
        out["ram_total_gb"] = round(mem.total / (1024**3), 2)
    except Exception:
        pass
    return out


def _find_blender() -> Optional[str]:
    env = (os.environ.get("CUTTLE_BLENDER_BIN") or os.environ.get("BLENDER_BIN") or "").strip()
    if env and Path(env).is_file():
        return env
    which = shutil.which("blender")
    if which:
        return which
    roots = [
        Path(r"C:\Program Files\Blender Foundation"),
        Path(r"C:\Program Files (x86)\Blender Foundation"),
    ]
    found: List[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for child in root.iterdir():
                exe = child / "blender.exe"
                if exe.is_file():
                    found.append(exe)
        except OSError:
            continue
    if not found:
        return None
    found.sort(key=lambda p: p.parent.name, reverse=True)
    return str(found[0])


def collect_ads(interactive_priority: str = "low") -> Dict[str, Any]:
    load = _cpu_ram()
    blender_bin = _find_blender()
    caps = {
        "shell": True,
        "filesystem": True,
        "python": True,
        "ffmpeg": _which("ffmpeg"),
        "blender": bool(blender_bin),
        "blender_bin": blender_bin,
        "blender_gpu": "optix" if _which("nvidia-smi") else None,
        "unity": bool(
            os.environ.get("UNITY_PATH")
            or Path(r"C:\Program Files\Unity\Hub\Editor").is_dir()
        ),
        "ollama": _which("ollama"),
        "agent_cursor": _which("agent") or _which("cursor-agent"),
        "cuttle_self_update": True,
    }
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "interactive_priority": interactive_priority,
        "ac_power": None,
        "capabilities": caps,
        "storage": {},
        "load": load,
        "cuttle_version": _cuttle_version(),
        "cuttle_git_rev": _cuttle_git_rev(),
    }


def _cuttle_version() -> str:
    """Prefer on-disk package.json over stale CUTTLE_PACKAGE_VERSION after pulls."""
    candidates = []
    repo = (os.environ.get("CUTTLE_REPO_ROOT") or "").strip()
    if repo:
        candidates.append(Path(repo) / "electron" / "package.json")
    candidates.extend(
        [
            Path(__file__).resolve().parent.parent / "package.json",
            Path(__file__).resolve().parents[2] / "electron" / "package.json",
            Path.cwd() / "electron" / "package.json",
            Path.cwd() / "package.json",
        ]
    )
    for pkg in candidates:
        try:
            if pkg.is_file():
                data = json.loads(pkg.read_text(encoding="utf-8"))
                ver = str(data.get("version") or "").strip()
                if ver:
                    return ver
        except Exception:
            continue
    return (os.environ.get("CUTTLE_PACKAGE_VERSION") or "").strip()


def _cuttle_git_rev() -> str:
    """Short HEAD from CUTTLE_REPO_ROOT or nearby checkout."""
    roots = []
    repo = (os.environ.get("CUTTLE_REPO_ROOT") or "").strip()
    if repo:
        roots.append(Path(repo))
    try:
        roots.append(Path(__file__).resolve().parents[2])
    except Exception:
        pass
    roots.append(Path.cwd())
    seen = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                rev = (completed.stdout or "").strip()
                if rev:
                    return rev
        except Exception:
            continue
    return ""


def _worker_payload(wid: str, ads: Dict[str, Any]) -> Dict[str, Any]:
    ver = str(ads.get("cuttle_version") or "").strip()
    rev = str(ads.get("cuttle_git_rev") or "").strip()
    meta: Dict[str, Any] = {}
    if ver:
        meta["cuttle_version"] = ver
    if rev:
        meta["cuttle_git_rev"] = rev
    return {
        "worker_id": wid,
        "hostname": ads.get("hostname"),
        "os": ads.get("os"),
        "capabilities": ads.get("capabilities"),
        "storage": ads.get("storage"),
        "load": ads.get("load"),
        "interactive_priority": ads.get("interactive_priority"),
        "ac_power": ads.get("ac_power"),
        "meta": meta,
    }


def _der_tlv(buf: bytes, pos: int) -> tuple:
    """(tag, content_start, content_end) of the DER element at ``pos``."""
    tag = buf[pos]
    length = buf[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    return tag, pos, pos + length


def spki_sha256(cert_der: bytes) -> str:
    """Base64 SHA-256 of a certificate's DER SubjectPublicKeyInfo.

    Same value as electron/tls-trust.js ``spkiSha256`` (stdlib only: this
    script runs on Client machines without third-party packages).
    """
    _tag, cert_start, _ = _der_tlv(cert_der, 0)
    _tag, pos, _ = _der_tlv(cert_der, cert_start)  # tbsCertificate
    tag, _, end = _der_tlv(cert_der, pos)
    if tag == 0xA0:  # explicit [0] version
        pos = end
    for _field in range(5):  # serial, signature alg, issuer, validity, subject
        _tag, _, pos = _der_tlv(cert_der, pos)
    _tag, _, end = _der_tlv(cert_der, pos)
    spki = cert_der[pos:end]
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


def _is_loopback_url(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def _coordinator_pin() -> str:
    return (os.environ.get("CUTTLE_COORDINATOR_TLS_SPKI_SHA256") or "").strip()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that checks the peer key pin before any request byte."""

    expected_spki = ""

    def connect(self) -> None:
        super().connect()
        der = self.sock.getpeercert(binary_form=True) or b""
        actual = ""
        try:
            actual = spki_sha256(der) if der else ""
        except Exception:
            actual = ""
        if not actual or actual != self.expected_spki:
            self.sock.close()
            raise ssl.SSLError(
                "coordinator certificate key does not match the key pinned by the desktop app"
            )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, expected_spki: str) -> None:
        ctx = ssl.create_default_context()
        # The key pin replaces chain/hostname checks (self-signed LAN host).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        super().__init__(context=ctx)
        self._expected_spki = expected_spki

    def https_open(self, req):
        conn = type("PinnedConn", (_PinnedHTTPSConnection,), {"expected_spki": self._expected_spki})
        return self.do_open(conn, req, context=self._context)


def _urlopen(req: urllib.request.Request, url: str, timeout: float):
    """Open ``req``: loopback HTTPS keeps the local self-signed exception;
    remote HTTPS requires the pinned key; plain HTTP is unchanged."""
    if not url.lower().startswith("https://"):
        return urllib.request.urlopen(req, timeout=timeout)
    if _is_loopback_url(url):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    pin = _coordinator_pin()
    if not pin:
        raise RuntimeError(
            "remote HTTPS coordinator has no pinned certificate key "
            "(CUTTLE_COORDINATOR_TLS_SPKI_SHA256); reconnect the desktop app to trust the host"
        )
    return urllib.request.build_opener(_PinnedHTTPSHandler(pin)).open(req, timeout=timeout)


def http_json(
    method: str,
    base: str,
    path: str,
    *,
    token: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    url = base.rstrip("/") + path
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "cuttle-device-worker/0.2",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        raw = json.dumps(body).encode("utf-8")
        data = raw
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with _urlopen(req, url, timeout) as resp:
            raw = resp.read() or b"{}"
            return json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {err_body or e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"{method} {path} via {base}: {e}") from e


def allowed_prefixes() -> List[str]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local"))
    prefixes = [
        str(home / "Desktop"),
        str(home / "Documents"),
        str(home / "Downloads"),
        str(home / "Pictures"),
        str(home / "Dev" / "Cuttle"),
        str(local / "cuttle-desktop"),
        str(Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")),
    ]
    return [p for p in prefixes if p]


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser((path or "").strip()))


def path_allowed(path: str) -> bool:
    expanded = _expand(path)
    if not expanded:
        return False
    norm = expanded.replace("/", "\\")
    for pref in allowed_prefixes():
        p = _expand(pref).replace("/", "\\").rstrip("\\")
        if not p:
            continue
        try:
            target = Path(expanded).resolve()
            base = Path(p).resolve()
            target.relative_to(base)
            return True
        except (OSError, ValueError):
            if norm.lower().startswith(p.lower() + "\\") or norm.lower() == p.lower():
                return True
    return False


def execute_job(job: Dict[str, Any]) -> Dict[str, Any]:
    jtype = str(job.get("type") or "").strip()
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    if jtype == "ping":
        return {
            "ok": True,
            "pong": True,
            "echo": params.get("echo"),
            "worker_time": time.time(),
        }
    if jtype == "file_copy":
        return _file_copy_local(params)
    if jtype == "blender_render":
        return _blender_render_local(params)
    if jtype == "shell":
        return _shell_recipe_local(params)
    if jtype == "cuttle_self_update":
        return _cuttle_self_update_local(params)
    raise RuntimeError(f"unsupported job type: {jtype}")


def _file_copy_local(params: Dict[str, Any]) -> Dict[str, Any]:
    source = str(params.get("source") or "").strip()
    dest = str(params.get("dest") or "").strip()
    if not source or not dest:
        raise RuntimeError("file_copy requires params.source and params.dest")
    if not path_allowed(source):
        raise RuntimeError(f"source path not allowlisted: {source}")
    if not path_allowed(dest):
        raise RuntimeError(f"dest path not allowlisted: {dest}")
    src_path = Path(_expand(source))
    dst_path = Path(_expand(dest))
    if not src_path.exists():
        raise RuntimeError(f"source does not exist: {source}")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if src_path.is_dir():
        if dst_path.exists() and dst_path.is_dir():
            final = dst_path / src_path.name
            if final.exists():
                shutil.rmtree(final)
            shutil.copytree(src_path, final)
            written = str(final)
        else:
            shutil.copytree(src_path, dst_path)
            written = str(dst_path)
        return {"ok": True, "kind": "directory", "source": str(src_path), "dest": written}
    if dst_path.exists() and dst_path.is_dir():
        final = dst_path / src_path.name
        shutil.copy2(src_path, final)
        written = str(final)
    else:
        shutil.copy2(src_path, dst_path)
        written = str(dst_path)
    size = Path(written).stat().st_size
    return {
        "ok": True,
        "kind": "file",
        "source": str(src_path),
        "dest": written,
        "bytes": size,
    }


_SHELL_RECIPES = {
    "git_status": ["git", "-C", "{repo}", "status", "-sb"],
    "git_pull": ["git", "-C", "{repo}", "pull", "--ff-only"],
    "git_fetch": ["git", "-C", "{repo}", "fetch", "--all", "--prune"],
    "git_rev_parse": ["git", "-C", "{repo}", "rev-parse", "HEAD"],
}


def _repo_root(params: Dict[str, Any]) -> Path:
    explicit = str(params.get("repo") or params.get("repo_path") or "").strip()
    if explicit:
        return Path(_expand(explicit))
    env = (os.environ.get("CUTTLE_REPO_ROOT") or "").strip()
    if env:
        return Path(env)
    # Materialized sidecar lives under userData; prefer Desktop/Dev/Cuttle guesses
    home = Path.home()
    for cand in (
        home / "Dev" / "Cuttle",
        Path(r"C:\Users") / os.environ.get("USERNAME", "") / "Dev" / "Cuttle",
    ):
        if (cand / ".git").is_dir():
            return cand
    return home


def _shell_recipe_local(params: Dict[str, Any]) -> Dict[str, Any]:
    import subprocess

    recipe = str(params.get("recipe") or "").strip()
    if not recipe:
        raise RuntimeError("shell requires params.recipe")
    if recipe not in _SHELL_RECIPES:
        raise RuntimeError(f"shell recipe not allowlisted: {recipe}")
    repo = _repo_root(params)
    argv = [p.format(repo=str(repo)) for p in _SHELL_RECIPES[recipe]]
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=int(params.get("timeout_seconds") or 120),
        cwd=str(repo) if repo.is_dir() else None,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise RuntimeError(f"recipe {recipe} exit {completed.returncode}: {(err or out)[-800:]}")
    return {
        "ok": True,
        "kind": "shell",
        "recipe": recipe,
        "argv": argv,
        "stdout": out[-4000:],
        "stderr": err[-1000:],
    }


def _cuttle_self_update_local(params: Dict[str, Any]) -> Dict[str, Any]:
    import subprocess
    import tempfile

    repo = _repo_root(params)
    if os.name == "nt":
        state_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    else:
        state_base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    log_dir = Path(state_base) / "cuttle-desktop"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "client-self-update.log"

    host = str(
        params.get("host")
        or os.environ.get("CUTTLE_FLASK_HOST")
        or ""
    ).strip()
    if not host:
        coord = (os.environ.get("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL") or "").strip()
        if "://" in coord:
            try:
                host = coord.split("://", 1)[1].split("/")[0].split(":")[0]
            except Exception:
                host = ""
    if not host:
        try:
            cfg_path = log_dir / "desktop-config.json"
            if cfg_path.is_file():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    host = str(data.get("host") or "").strip()
        except Exception:
            host = host or ""

    restart_electron = params.get("restart_electron")
    if restart_electron is None:
        restart_electron = True
    restart_daemon = params.get("restart_daemon")
    if restart_daemon is None:
        restart_daemon = True
    restart_electron = bool(restart_electron) and str(restart_electron).lower() not in (
        "0",
        "false",
        "no",
    )
    restart_daemon = bool(restart_daemon) and str(restart_daemon).lower() not in (
        "0",
        "false",
        "no",
    )

    # Stage delivered files outside the checkout; never overwrite local work.
    posix = os.name != "nt"
    name = "client-self-update.sh" if posix else "client-self-update.ps1"
    script_text = str(params.get("script_text_posix" if posix else "script_text") or "")
    if not script_text.strip():
        fallback = str(params.get("updater_script") or params.get("script_text") or "")
        if not posix or fallback.lstrip().startswith("#!") or "BASH_SOURCE" in fallback:
            script_text = fallback
    if script_text.strip():
        stage = Path(tempfile.mkdtemp(prefix="client-update-", dir=str(log_dir)))
        script = stage / name
        script.write_text(script_text, encoding="utf-8")
        helper_text = str(params.get("checkout_helper_text") or "")
        if not helper_text.strip():
            helper_text = (repo / ".cuttle_global" / "scripts" / "client-update-checkout.py").read_text(encoding="utf-8")
        (stage / "client-update-checkout.py").write_text(helper_text, encoding="utf-8")
    else:
        script = repo / ".cuttle_global" / "scripts" / name
    if not script.is_file():
        raise RuntimeError(f"updater missing: {script}")

    # Refuse unsafe checkouts before scheduling any lifecycle.
    pull = _git_update_checkout_local(repo, log_file, script.parent / "client-update-checkout.py")
    if not pull.get("ok"):
        raise RuntimeError(str(pull.get("error") or "checkout update refused"))

    ps_args = [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Repo",
        str(repo),
        "-LogPath",
        str(log_file),
        "-SkipPull",
    ]
    if host:
        ps_args.extend(["-HostName", host])
    if restart_electron:
        ps_args.append("-RestartElectron")
    else:
        ps_args.append("-NoElectron")
    if restart_daemon:
        ps_args.append("-RestartDaemon")
    else:
        ps_args.append("-NoDaemon")
    arg_list = subprocess.list2cmdline(ps_args).replace("'", "''")
    cmd = (
        "Start-Process -FilePath powershell.exe -WindowStyle Hidden "
        f"-ArgumentList '{arg_list}'"
    )
    with log_file.open("a", encoding="utf-8") as lf:
        lf.write(
            f"\n---- self-update scheduled {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"host={host!r} electron={restart_electron} daemon={restart_daemon} "
            f"skip_pull=1 head={pull.get('head')} ----\n"
        )
    argv = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd]
    if posix:
        argv = ["bash", str(script), "--repo", str(repo), "--log", str(log_file), "--skip-pull"]
        if host:
            argv.extend(["--host", host])
        if not restart_electron:
            argv.append("--no-electron")
        if not restart_daemon:
            argv.append("--no-daemon")
    subprocess.Popen(
        argv, cwd=str(repo), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        start_new_session=posix, close_fds=True,
    )
    return {
        "ok": True,
        "kind": "cuttle_self_update",
        "scheduled": True,
        "repo": str(repo),
        "host": host,
        "restart_electron": restart_electron,
        "restart_daemon": restart_daemon,
        "log": str(log_file),
        "spawn": "posix-detached" if posix else "start-process",
        "skip_pull": True,
        "stashed": bool(pull.get("stashed")),
        "head": pull.get("head"),
    }


def _git_update_checkout_local(repo: Path, log_path: Path | None = None, helper_path: Path | None = None) -> Dict[str, Any]:
    """Run the shared preservation contract; never stash, reset, or clean."""
    helper = helper_path or repo / ".cuttle_global" / "scripts" / "client-update-checkout.py"
    try:
        result = subprocess.run(
            [sys.executable, str(helper), "--repo", str(repo)],
            capture_output=True, text=True, timeout=240, cwd=str(repo),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or "") + (result.stderr or "")
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n---- preservation update ----\n" + output)
        if result.returncode:
            return {"ok": False, "error": output or "checkout update refused", "log": output}
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
        return {"ok": True, "stashed": False, "head": head, "log": output}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"checkout update refused: {exc}"}

def _blender_render_local(params: Dict[str, Any]) -> Dict[str, Any]:
    import subprocess

    blend = str(params.get("blend_file") or "").strip()
    out_dir = str(params.get("output_dir") or "").strip()
    if not blend or not out_dir:
        raise RuntimeError("blender_render requires blend_file and output_dir")
    if not path_allowed(blend):
        raise RuntimeError(f"blend_file not allowlisted: {blend}")
    if not path_allowed(out_dir):
        raise RuntimeError(f"output_dir not allowlisted: {out_dir}")
    try:
        fs = int(params.get("frame_start") or 1)
        fe = int(params.get("frame_end") or fs)
    except (TypeError, ValueError) as e:
        raise RuntimeError("bad frame range") from e
    blend_path = Path(_expand(blend))
    out_path = Path(_expand(out_dir))
    dry_run = bool(params.get("dry_run"))
    if not dry_run and not blend_path.is_file():
        raise RuntimeError(f"blend missing: {blend}")
    out_path.mkdir(parents=True, exist_ok=True)
    blender = str(params.get("blender_bin") or "").strip() or (_find_blender() or "")
    if not blender:
        raise RuntimeError("blender not found")
    engine = str(params.get("engine") or "").strip()
    cmd = [blender, "-b", str(blend_path)]
    if engine:
        cmd.extend(
            ["--python-expr", f"import bpy; bpy.context.scene.render.engine = {engine!r}"]
        )
    cmd.extend(["-o", str(out_path / "frame_"), "-s", str(fs), "-e", str(fe), "-a"])
    if dry_run:
        return {
            "ok": True,
            "kind": "blender_render",
            "dry_run": True,
            "cmd": cmd,
            "frame_start": fs,
            "frame_end": fe,
            "blender": blender,
        }
    started = time.time()
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=int(params.get("timeout_seconds") or 0) or max(600, (fe - fs + 1) * 120),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"blender exit {completed.returncode}: {(completed.stderr or completed.stdout or '')[-500:]}"
        )
    frames_written = 0
    try:
        frames_written = sum(
            1 for p in out_path.iterdir() if p.is_file() and p.name.lower().startswith("frame_")
        )
    except OSError:
        pass
    return {
        "ok": True,
        "kind": "blender_render",
        "blend_file": str(blend_path),
        "output_dir": str(out_path),
        "frame_start": fs,
        "frame_end": fe,
        "blender": blender,
        "frames_written": frames_written,
        "elapsed_seconds": round(time.time() - started, 2),
    }


def pick_base_urls() -> List[str]:
    urls: List[str] = []
    primary = (os.environ.get("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL") or "").strip().rstrip("/")
    http_fb = (
        os.environ.get("CUTTLE_DEVICE_WORKERS_COORDINATOR_URL_HTTP") or ""
    ).strip().rstrip("/")
    # Explicit single-endpoint policy emitted by Electron (CUTTLE_ENDPOINT_SINGLE=1):
    # only explicitly configured endpoints are used — even the documented
    # default pair is NOT inferred for an explicit selection.
    single = (os.environ.get("CUTTLE_ENDPOINT_SINGLE") or "").strip() == "1"
    if primary:
        urls.append(primary)
    # Explicit companion URL only — never derive HTTP from an HTTPS URL by
    # reusing the same port (protocol mismatch), and never guess :8000
    # for a custom primary. The legacy default pair (8080 -> 8000) is
    # inferred only in actual legacy default-mode: not a single selection,
    # primary is exactly the default HTTPS listener, no explicit companion.
    if not single and http_fb and http_fb not in urls:
        urls.append(http_fb)
    if (
        not single
        and not http_fb
        and primary.lower().startswith("https://")
        and _is_default_https_primary(primary)
    ):
        try:
            rest = primary.split("://", 1)[1]
            host = rest.split("/")[0].split(":")[0]
            alt = f"http://{host}:{_DEFAULT_HTTP_PORT}"
            if alt not in urls:
                urls.append(alt)
        except Exception:
            pass
    return urls


_DEFAULT_HTTPS_PORT = 8080
_DEFAULT_HTTP_PORT = 8000


def _is_default_https_primary(primary: str) -> bool:
    """True only for the documented default-mode https://host:8080 URL."""
    try:
        rest = primary.split("://", 1)[1]
        authority = rest.split("/")[0]
        if ":" not in authority:
            # No explicit port (protocol-default 443) is not default-mode.
            return False
        return int(authority.rsplit(":", 1)[1]) == _DEFAULT_HTTPS_PORT
    except Exception:
        return False


def worker_id() -> str:
    return (
        (os.environ.get("CUTTLE_DEVICE_WORKER_ID") or "").strip()
        or socket.gethostname().lower().replace(" ", "-")
    )


def worker_token() -> str:
    return (os.environ.get("CUTTLE_DEVICE_WORKERS_TOKEN") or "").strip()


class Client:
    def __init__(self, bases: List[str], token: str, wid: str) -> None:
        self.bases = bases
        self.token = token
        self.worker_id = wid
        self.active_base = bases[0] if bases else ""

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        errors: List[str] = []
        # Prefer last working base first
        ordered = [self.active_base] + [b for b in self.bases if b != self.active_base]
        ordered = [b for b in ordered if b]
        for base in ordered:
            try:
                data = http_json(method, base, path, token=self.token, body=body)
                self.active_base = base
                return data
            except Exception as e:
                errors.append(f"{base}: {e}")
        raise RuntimeError("; ".join(errors) or "no coordinator URL")

    def register(self, payload: Dict[str, Any]) -> None:
        body = dict(payload)
        body.setdefault("worker_id", self.worker_id)
        self._request("POST", "/api/workers/register", body)

    def claim(self, capabilities: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = self._request(
            "POST",
            "/api/workers/jobs/claim",
            {
                "worker_id": self.worker_id,
                "limit": 1,
                "capabilities": capabilities or {},
            },
        )
        jobs = data.get("jobs") or []
        return list(jobs) if isinstance(jobs, list) else []

    def complete(self, job_id: str, result: Dict[str, Any]) -> None:
        self._request(
            "POST",
            f"/api/workers/jobs/{job_id}/complete",
            {"worker_id": self.worker_id, "result": result or {}},
        )

    def fail(self, job_id: str, error: str, retry: bool = False) -> None:
        self._request(
            "POST",
            f"/api/workers/jobs/{job_id}/fail",
            {
                "worker_id": self.worker_id,
                "error": (error or "")[:1000],
                "retry": bool(retry),
            },
        )

    def heartbeat(self, job_id: str) -> None:
        self._request(
            "POST",
            f"/api/workers/jobs/{job_id}/heartbeat",
            {"worker_id": self.worker_id},
        )


def run_loop() -> int:
    bases = pick_base_urls()
    if not bases:
        log("ERROR: set CUTTLE_DEVICE_WORKERS_COORDINATOR_URL")
        return 2
    token = worker_token()
    if not token:
        log("ERROR: missing CUTTLE_DEVICE_WORKERS_TOKEN (auto-enroll failed?)")
        return 2
    wid = worker_id()
    client = Client(bases, token, wid)
    poll = 5
    try:
        poll = max(2, min(int(os.environ.get("CUTTLE_DEVICE_WORKERS_POLL_SECONDS") or "5"), 120))
    except ValueError:
        pass

    log(f"starting worker_id={wid} bases={bases} log={LOG_FILE}")
    # Prove connectivity once before entering the loop
    ads = collect_ads()
    payload = _worker_payload(wid, ads)
    try:
        client.register(payload)
        log(f"registered via {client.active_base} version={ads.get('cuttle_version') or '?'}")
    except Exception as e:
        log(f"ERROR initial register failed: {e}")
        return 1

    while True:
        try:
            ads = collect_ads()
            payload = _worker_payload(wid, ads)
            client.register(payload)
            caps = dict(ads.get("capabilities") or {})
            jobs = client.claim(caps)
            for job in jobs:
                jid = str(job.get("id") or "")
                try:
                    client.heartbeat(jid)
                    result = execute_job(job)
                    client.complete(jid, result)
                    log(f"job {jid} ok type={job.get('type')}")
                except Exception as e:
                    try:
                        client.fail(jid, str(e), retry=True)
                    except Exception:
                        pass
                    log(f"job {jid} fail: {e}")
        except Exception as e:
            log(f"tick error: {e}")
        time.sleep(poll)


def main() -> int:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    log(f"boot python={sys.version.split()[0]} exe={sys.executable}")
    if (os.environ.get("CUTTLE_DEVICE_WORKERS_ENABLED") or "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        log("disabled via CUTTLE_DEVICE_WORKERS_ENABLED")
        return 0
    try:
        return run_loop()
    except KeyboardInterrupt:
        log("stopped")
        return 0
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
