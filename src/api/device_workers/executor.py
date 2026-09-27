"""Execute allowlisted device-worker job types on the local machine."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from api.device_workers.config import allowed_path_prefixes


ALLOWED_TYPES = frozenset(
    {
        "ping",
        "file_copy",
        "blender_render",
        "shell",  # named recipes only (safe default)
        "execute_shell_unsafe",  # free-form local cmdline — opt-in, dangerous
        "execute_shell",  # legacy alias → execute_shell_unsafe
        "execute_shell_ssh",  # same intent over SSH — opt-in transport
        "cuttle_self_update",
    }
)


def _normalize_job_type(jtype: str) -> str:
    t = (jtype or "").strip()
    if t == "execute_shell":
        return "execute_shell_unsafe"
    return t

# Named shell recipes only — never arbitrary argv from the coordinator.
_BUILTIN_SHELL_RECIPES = {
    "git_status": ["git", "-C", "{repo}", "status", "-sb"],
    "git_pull": ["git", "-C", "{repo}", "pull", "--ff-only"],
    "git_fetch": ["git", "-C", "{repo}", "fetch", "--all", "--prune"],
    "git_rev_parse": ["git", "-C", "{repo}", "rev-parse", "HEAD"],
    # Diagnose Client version stickiness / updater failures (stdout only).
    "cuttle_version": [
        "{python}",
        "-c",
        (
            "import json,os,sys; from pathlib import Path; "
            "root=Path(r'{repo}'); sys.path.insert(0,str(root/'src')); "
            "pkg=json.loads((root/'electron'/'package.json').read_text(encoding='utf-8')); "
            "env=os.environ.get('CUTTLE_PACKAGE_VERSION',''); "
            "from api.device_workers.capabilities import cuttle_version as cv; "
            "print('package.json='+str(pkg.get('version'))); "
            "print('env='+env); print('cuttle_version()='+cv()); "
            "base=os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_STATE_HOME') or str(Path.home()/'.local'/'state'); "
            "log=Path(base)/'cuttle-desktop'/'client-self-update.log'; "
            "print('--- log tail ---'); "
            "print('\\n'.join(log.read_text(encoding='utf-8',errors='replace').splitlines()[-40:]) if log.is_file() else 'no log')"
        ),
    ],
}


def _relaunch_electron_client_recipe() -> List[str]:
    host = (os.environ.get("CUTTLE_ELECTRON_HOST") or "127.0.0.1").strip()
    if not re.fullmatch(r"[0-9A-Za-z.:-]+", host):
        host = "127.0.0.1"
    if os.name == "nt":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "Start-Process -FilePath npm.cmd -WorkingDirectory '{repo}\\electron' "
            f"-ArgumentList @('start','--','--host={host}')",
        ]
    return [
        "bash",
        "-lc",
        f"cd '{{repo}}/electron' && nohup npm start -- --host={host} >/dev/null 2>&1 &",
    ]


class JobExecError(RuntimeError):
    """Permanent failure (do not retry unless caller overrides)."""


class PartialRetryError(JobExecError):
    """Durable partial progress — shrink params and requeue when attempts remain."""

    def __init__(
        self,
        message: str,
        *,
        params_update: Dict[str, Any],
        partial_result: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.params_update = dict(params_update or {})
        self.partial_result = dict(partial_result or {})


def _is_unc(path: str) -> bool:
    s = (path or "").replace("/", "\\")
    return s.startswith("\\\\")


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser((path or "").strip()))


def path_allowed(path: str, prefixes: List[str] | None = None) -> bool:
    prefixes = prefixes if prefixes is not None else allowed_path_prefixes()
    expanded = _expand(path)
    if not expanded:
        return False
    if os.name != "nt":
        try:
            target = Path(expanded).resolve()
        except OSError:
            return False
        for pref in prefixes:
            p = _expand(pref)
            if not p:
                continue
            try:
                base = Path(p).resolve()
                target.relative_to(base)
                return True
            except (OSError, ValueError):
                continue
        return False
    norm = expanded.replace("/", "\\")
    if "\\\\*" in prefixes or any(p.strip() == "\\\\*" for p in prefixes):
        if _is_unc(norm):
            return True
    for pref in prefixes:
        p = _expand(pref).replace("/", "\\").rstrip("\\")
        if not p:
            continue
        if _is_unc(norm):
            if p.startswith("\\\\") and norm.lower().startswith(p.lower()):
                return True
            continue
        if _is_unc(p):
            continue
        try:
            target = Path(expanded).resolve()
            base = Path(p).resolve()
            target.relative_to(base)
            return True
        except (OSError, ValueError):
            continue
    return False


def execute_job(job: Dict[str, Any]) -> Dict[str, Any]:
    raw_type = str(job.get("type") or "").strip()
    jtype = _normalize_job_type(raw_type)
    if jtype not in {
        "ping",
        "file_copy",
        "blender_render",
        "shell",
        "execute_shell_unsafe",
        "execute_shell_ssh",
        "cuttle_self_update",
    }:
        raise JobExecError(f"unsupported job type: {raw_type or jtype}")
    params = job.get("params") if isinstance(job.get("params"), dict) else {}
    # Thread job id for HITL prompts
    if job.get("id") and "_job_id" not in params:
        params = {**params, "_job_id": str(job.get("id"))}
    if jtype == "ping":
        return {
            "ok": True,
            "pong": True,
            "echo": params.get("echo"),
            "worker_time": time.time(),
        }
    if jtype == "file_copy":
        return _file_copy(params)
    if jtype == "blender_render":
        return _blender_render(params)
    if jtype == "shell":
        return _shell_recipe(params)
    if jtype == "execute_shell_unsafe":
        return _execute_shell_unsafe(params)
    if jtype == "execute_shell_ssh":
        return _execute_shell_ssh(params)
    if jtype == "cuttle_self_update":
        return _cuttle_self_update(params)
    raise JobExecError(f"unsupported job type: {raw_type or jtype}")


def _dw_settings() -> Dict[str, Any]:
    """Live device_workers block from settings.json (disk), then settings manager."""
    try:
        # Prefer disk so enabling SSH/prefixes applies without relying on a stale singleton.
        path = Path(__file__).resolve().parents[2] / "settings.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            block = data.get("device_workers") if isinstance(data, dict) else None
            if isinstance(block, dict):
                return block
    except Exception:
        pass
    try:
        from managers.settings_manager import get_settings_manager

        sm = get_settings_manager()
        try:
            sm.reload()
        except Exception:
            pass
        block = sm.get_setting("device_workers") or {}
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def _execute_shell_unsafe_enabled() -> bool:
    for key in (
        "CUTTLE_DEVICE_WORKERS_EXECUTE_SHELL_UNSAFE",
        "CUTTLE_DEVICE_WORKERS_EXECUTE_SHELL",  # legacy env
    ):
        env = (os.environ.get(key) or "").strip().lower()
        if env in ("0", "false", "no", "off"):
            return False
        if env in ("1", "true", "yes", "on"):
            return True
    block = _dw_settings()
    if "execute_shell_unsafe_enabled" in block:
        return bool(block.get("execute_shell_unsafe_enabled"))
    return bool(block.get("execute_shell_enabled"))  # legacy settings key


def _execute_shell_ssh_enabled() -> bool:
    env = (os.environ.get("CUTTLE_DEVICE_WORKERS_EXECUTE_SHELL_SSH") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return bool(_dw_settings().get("execute_shell_ssh_enabled"))


def _command_allowed(command: str) -> bool:
    """Optional prefix allowlist when execute_shell_unsafe / ssh is on (empty = any)."""
    prefixes = _dw_settings().get("execute_shell_prefixes")
    if not isinstance(prefixes, list) or not prefixes:
        return True
    cmd = (command or "").strip()
    for pref in prefixes:
        p = str(pref or "").strip()
        if p and cmd.startswith(p):
            return True
    return False


def _execute_shell_unsafe(params: Dict[str, Any]) -> Dict[str, Any]:
    if not _execute_shell_unsafe_enabled():
        raise JobExecError(
            "execute_shell_unsafe disabled — set device_workers.execute_shell_unsafe_enabled=true "
            "(dangerous; prefer shell recipes or cuttle_self_update)"
        )
    command = str(params.get("command") or params.get("cmd") or "").strip()
    if not command:
        raise JobExecError("execute_shell_unsafe requires params.command")
    if not _command_allowed(command):
        raise JobExecError(
            "execute_shell_unsafe command rejected by execute_shell_prefixes allowlist"
        )
    _ensure_unsafe_shell_hitl(
        job_kind="execute_shell_unsafe",
        target="local",
        command=command,
        job_id=str(params.get("_job_id") or ""),
    )
    cwd = str(params.get("cwd") or "").strip()
    if cwd:
        if not path_allowed(cwd) and not Path(_expand(cwd)).exists():
            raise JobExecError(f"cwd not allowlisted: {cwd}")
        workdir = str(Path(_expand(cwd)).resolve())
    else:
        workdir = str(_repo_root(params))

    from api.device_workers.job_policy import resolve_timeouts
    from api.device_workers.long_run import HardTimeoutError, IdleTimeoutError, run_long_process

    idle_s, hard_s, prog_mode = resolve_timeouts(
        job_type="execute_shell_unsafe", params=params
    )
    on_prog = params.get("_on_progress")
    try:
        completed = run_long_process(
            command,
            hard_timeout_seconds=hard_s,
            idle_timeout_seconds=idle_s,
            progress_mode=prog_mode,
            on_progress=on_prog if callable(on_prog) else None,
            cwd=workdir,
            shell=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except IdleTimeoutError as e:
        raise JobExecError(
            f"execute_shell_unsafe idle timeout ({int(e.idle)}s)"
        ) from e
    except HardTimeoutError as e:
        raise JobExecError(
            f"execute_shell_unsafe timed out ({int(e.hard)}s)"
        ) from e
    except OSError as e:
        raise JobExecError(f"execute_shell_unsafe failed: {e}") from e
    out = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise JobExecError(
            f"execute_shell_unsafe exit {completed.returncode}: {(err or out)[-800:]}"
        )
    return {
        "ok": True,
        "kind": "execute_shell_unsafe",
        "transport": "local",
        "cwd": workdir,
        "stdout": out[-4000:],
        "stderr": err[-1000:],
        "elapsed_seconds": float(completed.elapsed_seconds),
    }


def _execute_shell_ssh(params: Dict[str, Any]) -> Dict[str, Any]:
    """Run a command on this machine via local OpenSSH client to a configured target.

    Intended for cases where pull-based recipes are wrong and you already trust SSH
    into the worker (or a sibling host). Still opt-in and never the enroll/claim plane.
    """
    if not _execute_shell_ssh_enabled():
        raise JobExecError(
            "execute_shell_ssh disabled — set device_workers.execute_shell_ssh_enabled=true "
            "and configure ssh_host (params or settings)"
        )
    command = str(params.get("command") or params.get("cmd") or "").strip()
    if not command:
        raise JobExecError("execute_shell_ssh requires params.command")
    if not _command_allowed(command):
        raise JobExecError("execute_shell_ssh command rejected by execute_shell_prefixes allowlist")

    block = _dw_settings()
    ssh_host = str(params.get("ssh_host") or block.get("ssh_host") or "").strip()
    ssh_user = str(params.get("ssh_user") or block.get("ssh_user") or "").strip()
    ssh_port = int(params.get("ssh_port") or block.get("ssh_port") or 22)
    identity = str(params.get("ssh_identity") or block.get("ssh_identity") or "").strip()
    if not ssh_host:
        raise JobExecError("execute_shell_ssh requires ssh_host (params or device_workers.ssh_host)")

    target = f"{ssh_user}@{ssh_host}" if ssh_user else ssh_host
    _ensure_unsafe_shell_hitl(
        job_kind="execute_shell_ssh",
        target=target,
        command=command,
        job_id=str(params.get("_job_id") or ""),
    )

    argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-p", str(ssh_port)]
    if identity:
        argv.extend(["-i", identity, "-o", "IdentitiesOnly=yes"])
    argv.extend([target, command])

    from api.device_workers.job_policy import resolve_timeouts
    from api.device_workers.long_run import HardTimeoutError, IdleTimeoutError, run_long_process

    idle_s, hard_s, prog_mode = resolve_timeouts(
        job_type="execute_shell_ssh", params=params
    )
    on_prog = params.get("_on_progress")
    try:
        completed = run_long_process(
            argv,
            hard_timeout_seconds=hard_s,
            idle_timeout_seconds=idle_s,
            progress_mode=prog_mode,
            on_progress=on_prog if callable(on_prog) else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as e:
        raise JobExecError("ssh client not found on PATH (install OpenSSH Client)") from e
    except IdleTimeoutError as e:
        raise JobExecError(f"execute_shell_ssh idle timeout ({int(e.idle)}s)") from e
    except HardTimeoutError as e:
        raise JobExecError(f"execute_shell_ssh timed out ({int(e.hard)}s)") from e
    except OSError as e:
        raise JobExecError(f"execute_shell_ssh failed: {e}") from e
    out = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise JobExecError(
            f"execute_shell_ssh exit {completed.returncode}: {(err or out)[-800:]}"
        )
    return {
        "ok": True,
        "kind": "execute_shell_ssh",
        "transport": "ssh",
        "target": target,
        "stdout": out[-4000:],
        "stderr": err[-1000:],
        "elapsed_seconds": float(completed.elapsed_seconds),
    }


def _ensure_unsafe_shell_hitl(
    *,
    job_kind: str,
    target: str = "",
    command: str = "",
    job_id: str = "",
) -> None:
    """Block until user approves first unsafe shell in this worker process (or session grant)."""
    from api.device_workers import ssh_approval as sa
    from api.device_workers.config import worker_id as local_worker_id

    if not sa.unsafe_shell_approval_required():
        return
    if sa.session_granted():
        return
    # Bypass only for explicit tests
    if os.environ.get("CUTTLE_SSH_APPROVAL_BYPASS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return

    wid = local_worker_id()
    block = _dw_settings()
    timeout = float(
        block.get("unsafe_shell_approval_timeout_seconds")
        or block.get("ssh_approval_timeout_seconds")
        or 300
    )
    decision = sa.request_via_coordinator(
        worker_id=wid,
        target=target,
        command_preview=command,
        job_id=job_id,
        job_kind=job_kind,
        timeout_seconds=timeout,
    )
    if decision == "session":
        sa.grant_session()
        return
    if decision == "once":
        return
    if decision == "timeout":
        raise JobExecError("Unsafe shell approval timed out — no response in Cuttle UI")
    raise JobExecError("Unsafe shell denied by user in Cuttle UI")


def _repo_root(params: Dict[str, Any] | None = None) -> Path:
    params = params or {}
    explicit = str(params.get("repo") or params.get("repo_path") or "").strip()
    if explicit:
        return Path(_expand(explicit)).resolve()
    env = (os.environ.get("CUTTLE_REPO_ROOT") or "").strip()
    if env:
        return Path(env).resolve()
    # src/api/device_workers/executor.py → repo root
    return Path(__file__).resolve().parents[3]


def _shell_recipes() -> Dict[str, List[str]]:
    recipes = dict(_BUILTIN_SHELL_RECIPES)
    recipes["relaunch_electron_client"] = _relaunch_electron_client_recipe()
    try:
        from managers.settings_manager import get_settings_manager

        block = get_settings_manager().get_setting("device_workers") or {}
        extra = block.get("shell_recipes") if isinstance(block, dict) else None
        if isinstance(extra, dict):
            for name, argv in extra.items():
                key = str(name).strip()
                if not key or not isinstance(argv, list) or not argv:
                    continue
                recipes[key] = [str(x) for x in argv]
    except Exception:
        pass
    return recipes


def _shell_recipe(params: Dict[str, Any]) -> Dict[str, Any]:
    recipe = str(params.get("recipe") or "").strip()
    if not recipe:
        raise JobExecError("shell requires params.recipe (named allowlist only)")
    recipes = _shell_recipes()
    if recipe not in recipes:
        raise JobExecError(
            f"shell recipe not allowlisted: {recipe} (allowed: {', '.join(sorted(recipes))})"
        )
    repo = _repo_root(params)
    if not path_allowed(str(repo)) and not (repo / ".git").is_dir():
        # Allow Cuttle repo itself even if outside Desktop allowlist when it is a git checkout
        if not (repo / ".git").is_dir():
            raise JobExecError(f"repo not allowlisted and not a git checkout: {repo}")
    fmt = {"repo": str(repo), "python": sys.executable}
    try:
        argv = [part.format(**fmt) for part in recipes[recipe]]
    except KeyError as e:
        raise JobExecError(f"recipe format error: missing {e}") from e

    from api.device_workers.job_policy import resolve_timeouts
    from api.device_workers.long_run import HardTimeoutError, IdleTimeoutError, run_long_process

    idle_s, hard_s, prog_mode = resolve_timeouts(job_type="shell", params=params)
    on_prog = params.get("_on_progress")
    try:
        completed = run_long_process(
            argv,
            hard_timeout_seconds=hard_s,
            idle_timeout_seconds=idle_s,
            progress_mode=prog_mode,
            on_progress=on_prog if callable(on_prog) else None,
            cwd=str(repo),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except IdleTimeoutError as e:
        raise JobExecError(f"shell recipe idle timeout: {recipe} ({int(e.idle)}s)") from e
    except HardTimeoutError as e:
        raise JobExecError(f"shell recipe timed out: {recipe} ({int(e.hard)}s)") from e
    except OSError as e:
        raise JobExecError(f"shell launch failed: {e}") from e
    out = (completed.stdout or "").strip()
    err = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise JobExecError(
            f"recipe {recipe} exit {completed.returncode}: {(err or out)[-800:]}"
        )
    return {
        "ok": True,
        "kind": "shell",
        "recipe": recipe,
        "argv": argv,
        "stdout": out[-4000:],
        "stderr": err[-1000:],
        "elapsed_seconds": float(completed.elapsed_seconds),
    }


def _cuttle_self_update(params: Dict[str, Any]) -> Dict[str, Any]:
    """Stash+pull synchronously (visible failure), then detach kill/relaunch only."""
    repo = _repo_root(params)
    if not (repo / ".git").is_dir():
        raise JobExecError(f"not a git repo: {repo}")

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
            cfg_candidates = []
            local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or ""
            if local:
                cfg_candidates.append(Path(local) / "cuttle-desktop" / "desktop-config.json")
            xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
            cfg_candidates.append(Path(xdg) / "cuttle-desktop" / "desktop-config.json")
            for cfg_path in cfg_candidates:
                if not cfg_path.is_file():
                    continue
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and str(data.get("host") or "").strip():
                    host = str(data.get("host") or "").strip()
                    break
        except Exception:
            host = host or ""

    restart_electron = params.get("restart_electron")
    if restart_electron is None:
        restart_electron = True
    restart_daemon = bool(params.get("restart_daemon", True))

    try:
        from core.runtime_paths import desktop_state_dir

        log_path = desktop_state_dir() / "client-self-update.log"
    except Exception:
        log_path = Path.home() / ".local" / "state" / "cuttle-desktop" / "client-self-update.log"

    # Prefer Host-embedded script_text so dirty/old Clients get the latest updater.
    script_path = _materialize_updater_script(params, repo, log_path)

    pull = _git_stash_and_pull(repo, log_path)
    if not pull.get("ok"):
        raise JobExecError(str(pull.get("error") or "git pull failed"))

    spawn_kind = "posix-detached"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(
                f"\n---- self-update scheduled {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"host={host!r} electron={bool(restart_electron)} "
                f"daemon={bool(restart_daemon)} skip_pull=1 "
                f"stash={pull.get('stashed')} rev={pull.get('head')} "
                f"spawn={spawn_kind} ----\n"
            )
    except OSError:
        pass

    try:
        if os.name == "nt":
            spawn_kind = "start-process"
            ps_args = [
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-Repo",
                str(repo),
                "-LogPath",
                str(log_path),
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
            arg_list = ", ".join("'{0}'".format(a.replace("'", "''")) for a in ps_args)
            cmd = (
                "Start-Process -FilePath powershell.exe -WindowStyle Hidden "
                f"-ArgumentList @({arg_list})"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                cwd=str(repo),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        else:
            argv = [
                "bash",
                str(script_path),
                "--repo",
                str(repo),
                "--log",
                str(log_path),
                "--skip-pull",
            ]
            if host:
                argv.extend(["--host", host])
            if not restart_electron:
                argv.append("--no-electron")
            if not restart_daemon:
                argv.append("--no-daemon")
            subprocess.Popen(
                argv,
                cwd=str(repo),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as e:
        raise JobExecError(f"failed to spawn updater: {e}") from e

    return {
        "ok": True,
        "kind": "cuttle_self_update",
        "scheduled": True,
        "repo": str(repo),
        "host": host,
        "restart_electron": bool(restart_electron),
        "restart_daemon": restart_daemon,
        "log": str(log_path),
        "spawn": spawn_kind,
        "skip_pull": True,
        "stashed": bool(pull.get("stashed")),
        "head": pull.get("head"),
        "pull_log": (pull.get("log") or "")[-1500:],
        "note": "Stash+pull succeeded; detached updater will stop Electron and relaunch.",
    }


def _materialize_updater_script(
    params: Dict[str, Any], repo: Path, log_path: Path
) -> Path:
    """Write Host-provided script_text to temp, else use repo script."""
    posix = os.name != "nt"
    if posix:
        text = str(params.get("script_text_posix") or "")
        if not text.strip():
            fallback = str(params.get("script_text") or params.get("updater_script") or "")
            if fallback.lstrip().startswith("#!") or "BASH_SOURCE" in fallback:
                text = fallback
    else:
        text = str(params.get("script_text") or params.get("updater_script") or "")
    suffix = ".sh" if posix else ".ps1"
    name = "client-self-update.sh" if posix else "client-self-update.ps1"
    if text.strip():
        dest_dir = log_path.parent if log_path else Path(tempfile.gettempdir())
        dest = dest_dir / f"client-self-update-embedded-{int(time.time())}{suffix}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            repo_script = repo / ".cuttle" / "scripts" / name
            try:
                repo_script.parent.mkdir(parents=True, exist_ok=True)
                repo_script.write_text(text, encoding="utf-8")
            except OSError:
                pass
            return dest
        except OSError as e:
            raise JobExecError(f"failed to write embedded updater: {e}") from e
    script = repo / ".cuttle" / "scripts" / name
    if not script.is_file():
        raise JobExecError(f"updater script missing: {script}")
    return script


def _git_stash_and_pull(repo: Path, log_path: Path | None = None) -> Dict[str, Any]:
    """Fetch + hard-reset to upstream + clean. Clients are deploy-only checkouts."""
    lines: List[str] = []

    def run(argv: List[str], timeout: int = 180) -> Tuple[int, str, str]:
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(repo),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout running {' '.join(argv)}"
        except OSError as e:
            return 1, "", str(e)
        out = (completed.stdout or "").strip()
        err = (completed.stderr or "").strip()
        lines.append(f"$ {' '.join(argv)} → {completed.returncode}")
        if out:
            lines.append(out)
        if err:
            lines.append(err)
        return completed.returncode, out, err

    def flush_log() -> None:
        if not log_path:
            return
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as lf:
                lf.write("\n---- sync reset-to-upstream ----\n")
                lf.write("\n".join(lines) + "\n")
        except OSError:
            pass

    rc, _, err = run(["git", "fetch", "--all", "--prune"])
    if rc != 0:
        flush_log()
        return {"ok": False, "error": f"git fetch failed: {err or rc}", "log": "\n".join(lines)}

    rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = (branch or "master").strip() or "master"
    rc_u, upstream, _ = run(["git", "rev-parse", "--abbrev-ref", "@{u}"])
    upstream = (upstream or "").strip() if rc_u == 0 else ""
    if not upstream:
        upstream = f"origin/{branch}"

    rc, _, err = run(["git", "reset", "--hard", upstream])
    if rc != 0:
        flush_log()
        return {
            "ok": False,
            "error": f"git reset --hard {upstream} failed: {err or rc}",
            "log": "\n".join(lines),
        }

    run(["git", "clean", "-fd"])
    rc, head, _ = run(["git", "rev-parse", "--short", "HEAD"])
    flush_log()
    return {
        "ok": True,
        "stashed": False,
        "reset": True,
        "upstream": upstream,
        "head": (head or "").strip(),
        "log": "\n".join(lines),
    }

def _as_path(path: str) -> Path:
    expanded = _expand(path)
    if _is_unc(expanded):
        return Path(expanded)
    return Path(expanded).resolve()


def _file_copy(params: Dict[str, Any]) -> Dict[str, Any]:
    source = str(params.get("source") or "").strip()
    dest = str(params.get("dest") or "").strip()
    if not source or not dest:
        raise JobExecError("file_copy requires params.source and params.dest")

    if not path_allowed(source):
        raise JobExecError(f"source path not allowlisted: {source}")
    if not path_allowed(dest):
        raise JobExecError(f"dest path not allowlisted: {dest}")

    src_path = _as_path(source)
    dst_path = _as_path(dest)

    if not src_path.exists():
        raise JobExecError(f"source does not exist: {source}")

    if not _is_unc(str(dst_path)):
        dst_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    if src_path.is_dir():
        if dst_path.exists() and dst_path.is_dir():
            final = dst_path / src_path.name
            if final.exists():
                shutil.rmtree(final)
            shutil.copytree(src_path, final)
            written = str(final)
        else:
            if dst_path.exists() and dst_path.is_file():
                raise JobExecError("cannot copy directory onto a file")
            shutil.copytree(src_path, dst_path)
            written = str(dst_path)
        kind = "directory"
    else:
        if dst_path.exists() and dst_path.is_dir():
            final = dst_path / src_path.name
            shutil.copy2(src_path, final)
            written = str(final)
        else:
            shutil.copy2(src_path, dst_path)
            written = str(dst_path)
        kind = "file"

    size = None
    try:
        if kind == "file":
            size = Path(written).stat().st_size
    except OSError:
        pass

    return {
        "ok": True,
        "kind": kind,
        "source": str(src_path),
        "dest": written,
        "bytes": size,
    }


def _resolve_blender(bin_hint: str = "") -> str:
    from api.device_workers.capabilities import find_blender_executable

    found = find_blender_executable(bin_hint)
    if found:
        return found
    raise JobExecError(
        "blender executable not found (install Blender, add to PATH, "
        "set CUTTLE_BLENDER_BIN, or params.blender_bin)"
    )


_FRAME_NAME_RE = re.compile(r"^frame_(\d+)", re.I)


def _list_frames_in_range(out_path: Path, frame_start: int, frame_end: int) -> List[int]:
    """Return sorted frame numbers present as frame_* files in [start, end]."""
    found: List[int] = []
    try:
        for p in out_path.iterdir():
            if not p.is_file():
                continue
            m = _FRAME_NAME_RE.match(p.name)
            if not m:
                continue
            fr = int(m.group(1))
            if frame_start <= fr <= frame_end:
                found.append(fr)
    except OSError:
        return []
    return sorted(set(found))


def _missing_frames(frame_start: int, frame_end: int, present: Sequence[int]) -> List[int]:
    have = set(int(x) for x in present)
    return [f for f in range(int(frame_start), int(frame_end) + 1) if f not in have]


def _count_frames_in_range(out_path: Path, frame_start: int, frame_end: int) -> int:
    """Count frame_* files whose frame number falls in [start, end] only."""
    return len(_list_frames_in_range(out_path, frame_start, frame_end))


def _contiguous_missing_span(missing: Sequence[int]) -> Optional[Tuple[int, int]]:
    """First contiguous run of missing frames → (start, end), or None."""
    if not missing:
        return None
    start = int(missing[0])
    end = start
    for f in missing[1:]:
        if int(f) == end + 1:
            end = int(f)
        else:
            break
    return start, end


def _write_frame_timer_script(log_path: Path) -> Path:
    """Temp Blender --python hook that logs per-frame render seconds as JSONL."""
    log_literal = json.dumps(str(log_path))
    body = f"""# auto-generated by Cuttle device_workers — do not edit
import bpy
import json
import time
from pathlib import Path

_LOG = Path({log_literal})
_state = {{"t0": None, "frame": None}}

def _pre(scene, *_args):
    _state["t0"] = time.time()
    _state["frame"] = int(scene.frame_current)

def _write(scene, *_args):
    t0 = _state.get("t0")
    fr = _state.get("frame")
    if t0 is None or fr is None:
        return
    rec = {{
        "frame": int(fr),
        "seconds": round(time.time() - float(t0), 4),
        "t": time.time(),
    }}
    try:
        with _LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\\n")
    except OSError:
        pass

bpy.app.handlers.render_pre.append(_pre)
bpy.app.handlers.render_write.append(_write)
"""
    fd, name = tempfile.mkstemp(prefix="cuttle_frame_timer_", suffix=".py")
    os.close(fd)
    path = Path(name)
    path.write_text(body, encoding="utf-8")
    return path


def _read_frame_times(log_path: Path) -> List[Dict[str, Any]]:
    if not log_path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and "frame" in rec and "seconds" in rec:
                out.append(rec)
    except OSError:
        return []
    return out


def _summarize_frame_times(
    frame_times: List[Dict[str, Any]], *, elapsed: float, frame_start: int, frame_end: int
) -> Dict[str, Any]:
    secs = [
        float(t["seconds"])
        for t in frame_times
        if isinstance(t.get("seconds"), (int, float))
    ]
    n = max(1, int(frame_end) - int(frame_start) + 1)
    if secs:
        secs_sorted = sorted(secs)
        mid = secs_sorted[len(secs_sorted) // 2]
        return {
            "sec_per_frame": round(sum(secs) / len(secs), 4),
            "sec_per_frame_p50": round(mid, 4),
            "sec_per_frame_max": round(max(secs), 4),
            "frames_timed": len(secs),
        }
    return {
        "sec_per_frame": round(float(elapsed) / n, 4) if elapsed > 0 else None,
        "sec_per_frame_p50": None,
        "sec_per_frame_max": None,
        "frames_timed": 0,
    }


def _blender_render(params: Dict[str, Any]) -> Dict[str, Any]:
    blend = str(params.get("blend_file") or "").strip()
    out_dir = str(params.get("output_dir") or "").strip()
    if not blend or not out_dir:
        raise JobExecError("blender_render requires params.blend_file and params.output_dir")
    if not path_allowed(blend):
        raise JobExecError(f"blend_file path not allowlisted: {blend}")
    if not path_allowed(out_dir):
        raise JobExecError(f"output_dir path not allowlisted: {out_dir}")

    try:
        frame_start = int(params.get("frame_start") or 1)
        frame_end = int(params.get("frame_end") or frame_start)
    except (TypeError, ValueError) as e:
        raise JobExecError("frame_start/frame_end must be ints") from e
    if frame_end < frame_start:
        raise JobExecError("frame_end must be >= frame_start")

    blend_path = _as_path(blend)
    dry_run = bool(params.get("dry_run"))
    if not dry_run and not blend_path.is_file():
        raise JobExecError(f"blend_file not found: {blend}")

    out_path = _as_path(out_dir)
    try:
        out_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise JobExecError(f"cannot create output_dir: {e}") from e

    # Blender -o path is a prefix; ensure trailing separator + frame stem
    out_prefix = str(out_path / "frame_")
    blender = _resolve_blender(str(params.get("blender_bin") or ""))
    engine = str(params.get("engine") or "").strip()
    # Optional one-liner / multi-line setup (runs after load, before -a).
    # Used e.g. to install project tip handlers when blend auto-scripts are disabled.
    extra_expr = str(params.get("python_expr") or "").strip()
    setup_parts = []
    if engine:
        setup_parts.append(f"bpy.context.scene.render.engine = {engine!r}")
    if extra_expr:
        setup_parts.append(extra_expr)
    # If bake_render.py sits next to the blend, install vein tip handler
    # (Blender often skips blend text auto-run — tips would stay blunt).
    setup_parts.append(
        "import os,sys\n"
        "root=os.path.dirname(bpy.data.filepath or '')\n"
        "br=os.path.join(root,'bake_render.py')\n"
        "if root and os.path.isfile(br):\n"
        " sys.path.insert(0,root)\n"
        " import importlib\n"
        " import bake_render\n"
        " importlib.reload(bake_render)\n"
        " bake_render._install_vein_tip_handler()\n"
        " for _o in bpy.data.objects:\n"
        "  if _o.name.startswith('CamTendril_plug'):\n"
        "   _o.hide_render=True"
    )
    setup_expr = "import bpy\n" + "\n".join(setup_parts)

    def _cmd_prefix() -> list:
        c = [blender, "-b", str(blend_path), "--python-expr", setup_expr]
        return c

    cmd = _cmd_prefix()
    cmd.extend(
        [
            "-o",
            out_prefix,
            "-s",
            str(frame_start),
            "-e",
            str(frame_end),
            "-a",
        ]
    )

    if dry_run:
        return {
            "ok": True,
            "kind": "blender_render",
            "dry_run": True,
            "blend_file": str(blend_path),
            "output_dir": str(out_path),
            "frame_start": frame_start,
            "frame_end": frame_end,
            "engine": engine or None,
            "batch_id": str(params.get("batch_id") or "") or None,
            "chunk_index": params.get("chunk_index"),
            "blender": blender,
            "cmd": cmd,
            "elapsed_seconds": 0,
        }

    from api.device_workers.job_policy import resolve_timeouts
    from api.device_workers.long_run import (
        HardTimeoutError,
        IdleTimeoutError,
        ProgressSnapshot,
        run_long_process,
    )

    units = frame_end - frame_start + 1
    idle_s, hard_s, prog_mode = resolve_timeouts(
        job_type="blender_render", params=params, units=units
    )

    timer_script: Optional[Path] = None
    frame_log: Optional[Path] = None
    # Insert frame timer before -a so handlers register, then animate.
    try:
        fd, log_name = tempfile.mkstemp(prefix="cuttle_frames_", suffix=".jsonl")
        os.close(fd)
        frame_log = Path(log_name)
        timer_script = _write_frame_timer_script(frame_log)
        cmd = _cmd_prefix()
        cmd.extend(["--python", str(timer_script)])
        cmd.extend(
            [
                "-o",
                out_prefix,
                "-s",
                str(frame_start),
                "-e",
                str(frame_end),
                "-a",
            ]
        )
    except OSError:
        timer_script = None
        frame_log = None

    def _poll_progress() -> ProgressSnapshot:
        present = _list_frames_in_range(out_path, frame_start, frame_end)
        last = str(present[-1]) if present else None
        return ProgressSnapshot(
            units_done=len(present),
            units_total=units,
            last_unit_id=last,
            message=f"frames {len(present)}/{units}",
        )

    on_prog = params.get("_on_progress")
    progress_cb = on_prog if callable(on_prog) else None

    def _raise_partial(reason: str) -> None:
        present = _list_frames_in_range(out_path, frame_start, frame_end)
        missing = _missing_frames(frame_start, frame_end, present)
        partial = {
            "kind": "blender_render",
            "blend_file": str(blend_path),
            "output_dir": str(out_path),
            "frame_start": frame_start,
            "frame_end": frame_end,
            "frames_written": len(present),
            "frames_expected": units,
            "frames_present": present[:128],
            "frames_missing": missing[:128],
            "batch_id": str(params.get("batch_id") or "") or None,
            "chunk_index": params.get("chunk_index"),
        }
        if not missing:
            # All durable units landed — treat as success path via caller.
            return
        span = _contiguous_missing_span(missing)
        if span is None:
            raise JobExecError(f"{reason}; no missing span computed")
        fs, fe = span
        raise PartialRetryError(
            f"{reason}; shrinking to frames {fs}-{fe} "
            f"({len(present)}/{units} durable frames kept)",
            params_update={"frame_start": fs, "frame_end": fe},
            partial_result=partial,
        )

    started = time.time()
    try:
        try:
            completed = run_long_process(
                cmd,
                hard_timeout_seconds=hard_s,
                idle_timeout_seconds=idle_s,
                progress_mode=prog_mode,
                progress_poll=_poll_progress,
                on_progress=progress_cb,
                poll_interval=5.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except IdleTimeoutError as e:
            _raise_partial(f"blender idle timeout after {int(e.idle)}s")
            # If no missing frames, fall through as soft success.
            completed = None  # type: ignore
        except HardTimeoutError as e:
            _raise_partial(f"blender hard timeout after {int(e.hard)}s")
            completed = None  # type: ignore
    except PartialRetryError:
        raise
    except OSError as e:
        raise JobExecError(f"failed to launch blender: {e}") from e
    finally:
        if timer_script is not None:
            try:
                timer_script.unlink(missing_ok=True)
            except OSError:
                pass

    if completed is None:
        # Idle/hard timeout but every frame in range is on disk.
        present = _list_frames_in_range(out_path, frame_start, frame_end)
        missing = _missing_frames(frame_start, frame_end, present)
        if missing:
            span = _contiguous_missing_span(missing)
            fs, fe = span if span else (missing[0], missing[-1])
            raise PartialRetryError(
                f"blender timed out with missing frames; shrinking to {fs}-{fe}",
                params_update={"frame_start": int(fs), "frame_end": int(fe)},
                partial_result={
                    "frames_written": len(present),
                    "frames_expected": units,
                    "frames_missing": missing[:128],
                },
            )
        elapsed = round(time.time() - started, 2)
        frame_times = _read_frame_times(frame_log) if frame_log else []
        if frame_log is not None:
            try:
                frame_log.unlink(missing_ok=True)
            except OSError:
                pass
        stats = _summarize_frame_times(
            frame_times, elapsed=elapsed, frame_start=frame_start, frame_end=frame_end
        )
        return {
            "ok": True,
            "kind": "blender_render",
            "blend_file": str(blend_path),
            "output_dir": str(out_path),
            "frame_start": frame_start,
            "frame_end": frame_end,
            "engine": engine or None,
            "batch_id": str(params.get("batch_id") or "") or None,
            "chunk_index": params.get("chunk_index"),
            "blender": blender,
            "frames_written": len(present),
            "frames_expected": units,
            "elapsed_seconds": elapsed,
            "recovered_after_timeout": True,
            "frame_times": frame_times[:64],
            **stats,
        }

    if completed.returncode != 0:
        present = _list_frames_in_range(out_path, frame_start, frame_end)
        missing = _missing_frames(frame_start, frame_end, present)
        if present and missing:
            span = _contiguous_missing_span(missing)
            if span:
                fs, fe = span
                tail = ((completed.stderr or completed.stdout or "")[-400:]).strip()
                raise PartialRetryError(
                    f"blender exit {completed.returncode} after partial progress; "
                    f"shrinking to {fs}-{fe}"
                    + (f": {tail}" if tail else ""),
                    params_update={"frame_start": fs, "frame_end": fe},
                    partial_result={
                        "frames_written": len(present),
                        "frames_expected": units,
                        "frames_missing": missing[:128],
                        "exit_code": completed.returncode,
                    },
                )
        tail = ((completed.stderr or completed.stdout or "")[-800:]).strip()
        raise JobExecError(
            f"blender exit {completed.returncode}: {tail or 'no output'}"
        )

    elapsed = float(completed.elapsed_seconds)
    present = _list_frames_in_range(out_path, frame_start, frame_end)
    missing = _missing_frames(frame_start, frame_end, present)
    frame_times = _read_frame_times(frame_log) if frame_log else []
    if frame_log is not None:
        try:
            frame_log.unlink(missing_ok=True)
        except OSError:
            pass

    if missing:
        span = _contiguous_missing_span(missing)
        if span:
            fs, fe = span
            raise PartialRetryError(
                f"blender exited 0 but missing {len(missing)} frame(s); "
                f"shrinking to {fs}-{fe}",
                params_update={"frame_start": fs, "frame_end": fe},
                partial_result={
                    "frames_written": len(present),
                    "frames_expected": units,
                    "frames_missing": missing[:128],
                },
            )

    stats = _summarize_frame_times(
        frame_times, elapsed=elapsed, frame_start=frame_start, frame_end=frame_end
    )
    sample = frame_times if len(frame_times) <= 64 else (
        frame_times[:8] + frame_times[-56:]
    )

    return {
        "ok": True,
        "kind": "blender_render",
        "blend_file": str(blend_path),
        "output_dir": str(out_path),
        "frame_start": frame_start,
        "frame_end": frame_end,
        "engine": engine or None,
        "batch_id": str(params.get("batch_id") or "") or None,
        "chunk_index": params.get("chunk_index"),
        "blender": blender,
        "frames_written": len(present),
        "frames_expected": units,
        "elapsed_seconds": elapsed,
        "frame_times": sample,
        **stats,
    }


def validate_job_submission(job_type: str, params: Dict[str, Any]) -> Tuple[bool, str]:
    jtype = _normalize_job_type(job_type or "")
    canonical = {
        "ping",
        "file_copy",
        "blender_render",
        "shell",
        "execute_shell_unsafe",
        "execute_shell_ssh",
        "cuttle_self_update",
    }
    if jtype not in canonical:
        return False, f"unsupported type (allowed: {', '.join(sorted(canonical))})"
    if jtype == "file_copy":
        if not str(params.get("source") or "").strip():
            return False, "file_copy requires params.source"
        if not str(params.get("dest") or "").strip():
            return False, "file_copy requires params.dest"
    if jtype == "blender_render":
        if not str(params.get("blend_file") or "").strip():
            return False, "blender_render requires params.blend_file"
        if not str(params.get("output_dir") or "").strip():
            return False, "blender_render requires params.output_dir"
        try:
            fs = int(params.get("frame_start") or 1)
            fe = int(params.get("frame_end") or fs)
        except (TypeError, ValueError):
            return False, "frame_start/frame_end must be ints"
        if fe < fs:
            return False, "frame_end must be >= frame_start"
    if jtype == "shell":
        recipe = str(params.get("recipe") or "").strip()
        if not recipe:
            return False, "shell requires params.recipe"
        if recipe not in _shell_recipes():
            return False, f"shell recipe not allowlisted: {recipe}"
    if jtype == "execute_shell_unsafe":
        if not str(params.get("command") or params.get("cmd") or "").strip():
            return False, "execute_shell_unsafe requires params.command"
    if jtype == "execute_shell_ssh":
        if not str(params.get("command") or params.get("cmd") or "").strip():
            return False, "execute_shell_ssh requires params.command"
    if jtype == "cuttle_self_update":
        pass
    return True, ""
