"""Collect uncommitted git changes (files + line add/remove counts) for a repo path."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# UI polls stack if a previous git-status is still running. Large dirty trees
# (e.g. a large game checkout with ~2k deletes) made full `diff --numstat HEAD` exceed
# the poll timeout; fail-fast locks then made every commit race the UI poll.
_collect_locks_guard = threading.Lock()
_collect_locks: Dict[str, threading.Lock] = {}

# Above this many porcelain rows, skip full-repo numstat and only stat the
# truncated file window (keeps UI polls under the lock budget).
_HEAVY_STATUS_ROWS = 250


def _live_fs_path(path: Optional[str]) -> str:
    """Map cloned Windows project paths onto this machine (Linux checkout / mounts)."""
    raw = (path or "").strip()
    if not raw:
        return raw
    try:
        from core.runtime_paths import rewrite_windows_lab_path

        mapped = (rewrite_windows_lab_path(raw) or "").strip()
        if mapped:
            return mapped
    except Exception:
        pass
    return raw


def _creationflags(*, new_group: bool = False) -> int:
    flags = 0
    if sys.platform == "win32":
        flags |= subprocess.CREATE_NO_WINDOW
        # Own process group so taskkill /T can reap git's grandchild on timeout.
        if new_group:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return flags


def _kill_process_tree(pid: int) -> None:
    """Best-effort kill of pid and children (Windows orphans git.exe otherwise)."""
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return
    try:
        from api.process_kill_safety import may_kill_pid

        if not may_kill_pid(pid):
            return
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5.0,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    except Exception:
        pass


def git_run(args: List[str], cwd: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    """Run git; on timeout kill the whole process tree (not only the direct child)."""
    try:
        proc = subprocess.Popen(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creationflags(new_group=True),
        )
    except OSError as e:
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=127,
            stdout="",
            stderr=str(e),
        )

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=int(proc.returncode or 0),
            stdout=stdout or "",
            stderr=stderr or "",
        )
    except subprocess.TimeoutExpired:
        _kill_process_tree(int(proc.pid or 0))
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=2.0)
        except Exception:
            pass
        return subprocess.CompletedProcess(
            args=["git", *args],
            returncode=124,
            stdout="",
            stderr=f"git timed out after {timeout}s",
        )


def _absolute_git_dir(repo_root: str) -> Optional[Path]:
    r = git_run(["rev-parse", "--absolute-git-dir"], repo_root)
    if r.returncode != 0:
        return None
    raw = (r.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def clear_stale_index_lock(repo_root: str, *, min_age_sec: float = 60.0) -> bool:
    """Remove a leftover ``.git/index.lock`` from a crashed git process.

    Git refuses ``add``/``commit`` while this file exists. A live git process
    keeps it young and (on Windows) exclusively open; we only delete when the
    lock is old *and* nothing is holding it.

    Returns True if a lock file was removed.
    """
    git_dir = _absolute_git_dir(repo_root)
    if git_dir is None:
        return False
    lock = git_dir / "index.lock"
    try:
        if not lock.is_file():
            return False
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False

    if age < max(0.0, float(min_age_sec)):
        return False

    if sys.platform == "win32":
        try:
            # FILE_SHARE_NONE: fails if another process still has the lock open.
            fd = os.open(str(lock), os.O_RDWR)
            os.close(fd)
        except OSError:
            return False

    try:
        lock.unlink()
        return True
    except OSError:
        return False


def find_git_root(cwd: str) -> Optional[str]:
    r = git_run(["rev-parse", "--show-toplevel"], cwd)
    if r.returncode != 0:
        return None
    root = (r.stdout or "").strip()
    return root or None


_PREFERRED_GIT_CHILD_NAMES = ("source", "src", "repo", "code", "game", "project")
_SKIP_GIT_CHILD_NAMES = {
    ".git",
    "node_modules",
    "library",
    "temp",
    "obj",
    "builds",
    "release",
    "required",
    "vendor",
    "third_party",
    "third-party",
}


def _iter_git_child_dirs(base: Path) -> List[Path]:
    """One-level children to probe for nested ``.git`` (preferred names first)."""
    children: List[Path] = []
    for name in _PREFERRED_GIT_CHILD_NAMES:
        child = base / name
        if child.is_dir():
            children.append(child)
    try:
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name.lower() in _SKIP_GIT_CHILD_NAMES:
                continue
            if child in children:
                continue
            children.append(child)
    except OSError:
        pass
    return children


def discover_git_workdirs(path: str) -> List[str]:
    """Find all git work trees for a Cuttle project path.

    - If ``path`` is itself inside a work tree, return that single toplevel
      (do not also pick nested child repos — avoids vendor noise).
    - Otherwise scan one level of children for ``.git`` (sibling-repo layout,
      e.g. ``G:\\Dev\\Blender\\JamBit …``).
    """
    if not path:
        return []
    try:
        base = Path(path).resolve()
    except OSError:
        return []
    if not base.is_dir():
        return []

    direct = find_git_root(str(base))
    if direct:
        try:
            return [str(Path(direct).resolve())]
        except OSError:
            return [direct]

    seen: set = set()
    roots: List[str] = []
    for child in _iter_git_child_dirs(base):
        if not (child / ".git").exists():
            continue
        found = find_git_root(str(child))
        if not found:
            continue
        try:
            key = str(Path(found).resolve())
        except OSError:
            key = found
        if key in seen:
            continue
        seen.add(key)
        roots.append(key)
    return roots


def resolve_git_workdir(path: str) -> Optional[str]:
    """Primary git work tree for a project path (first of ``discover_git_workdirs``).

    Registered Cuttle projects often point at a folder whose ``.git`` lives in a
    child dir (Unity-style ``source/``) or at a parent of sibling repos.
    """
    roots = discover_git_workdirs(path)
    return roots[0] if roots else None


def repo_label(repo_root: str, project_cwd: str) -> str:
    """Short label for UI chips (relative to project when possible)."""
    try:
        root = Path(repo_root).resolve()
        base = Path(project_cwd).resolve()
        try:
            rel = root.relative_to(base)
            text = str(rel).replace("\\", "/")
            if text and text != ".":
                return text
        except ValueError:
            pass
        return root.name or str(root)
    except OSError:
        return Path(repo_root).name or str(repo_root or "")


def sanitize_git_output(text: str) -> str:
    """Strip credentials from git stderr/stdout before it hits logs or toasts."""
    raw = text or ""
    raw = re.sub(r"(://)([^/@\s]+@)", r"\1", raw)
    for key in ("GIT_ASKPASS_PASSWORD", "GITEA_TOKEN", "GITEA_PASSWORD"):
        secret = (os.environ.get(key) or "").strip()
        if secret:
            raw = raw.replace(secret, "***")
    return raw


def git_auth_env() -> Dict[str, str]:
    """Env for non-interactive ``git push`` using Gitea token from ``src/.env``."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    cfg: Dict[str, str] = {}
    try:
        from api.gitea_client import load_gitea_config

        cfg = load_gitea_config() or {}
    except Exception:
        pass
    token = (cfg.get("token") or env.get("GITEA_TOKEN") or "").strip()
    user = (cfg.get("username") or "").strip() or "git"
    password = token or (cfg.get("password") or env.get("GITEA_PASSWORD") or "").strip()
    if password:
        env["GIT_ASKPASS_USER"] = user
        env["GIT_ASKPASS_PASSWORD"] = password
        if token:
            env["GITEA_TOKEN"] = token
    return env


def git_credential_helper_arg(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """``credential.helper`` value, or None when no password is configured."""
    merged = env if env is not None else git_auth_env()
    if not (merged.get("GIT_ASKPASS_PASSWORD") or "").strip():
        return None
    script = Path(__file__).resolve().parent / "git_credential_helper.py"
    exe = sys.executable
    if os.name == "nt":
        return f'!{exe} {script}'
    return "!" + shlex.quote(exe) + " " + shlex.quote(str(script))


def _repo_slug_from_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    text = re.sub(r"\.git$", "", text, flags=re.I)
    if text.startswith("git@"):
        _, _, rest = text.partition(":")
        return rest.strip("/") or text
    try:
        from urllib.parse import urlparse

        parsed = urlparse(text)
        path = (parsed.path or "").strip("/")
        if path:
            return path
    except Exception:
        pass
    return text


def git_push_target(root: str) -> Dict[str, Any]:
    """Remote name, branch, sanitized URL, and owner/repo slug for confirm UI."""
    br = git_run(["branch", "--show-current"], root, timeout=8.0)
    branch = (br.stdout or "").strip() if br.returncode == 0 else ""
    remote = "origin"
    upstream = git_run(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        root,
        timeout=8.0,
    )
    if upstream.returncode == 0:
        u = (upstream.stdout or "").strip()
        if "/" in u:
            remote, up_branch = u.split("/", 1)
            if not branch:
                branch = up_branch
    url_r = git_run(["remote", "get-url", remote], root, timeout=8.0)
    raw_url = (url_r.stdout or "").strip() if url_r.returncode == 0 else ""
    display_url = sanitize_git_output(raw_url)
    return {
        "remote": remote,
        "branch": branch or None,
        "url": display_url or None,
        "repo": _repo_slug_from_url(display_url) or None,
    }


def git_push_command(remote: str = "", branch: str = "") -> Tuple[List[str], Dict[str, str]]:
    """``git push`` argv + env (Gitea token via credential helper when set)."""
    env = git_auth_env()
    helper = git_credential_helper_arg(env)
    prefix: List[str] = ["git"]
    if helper:
        prefix.extend(["-c", f"credential.helper={helper}"])
    if remote and branch:
        return prefix + ["push", remote, branch], env
    if remote:
        return prefix + ["push", remote, "HEAD"], env
    return prefix + ["push"], env


def resolve_allowed_repo_root(
    project_cwd: str,
    repo_root: Optional[str] = None,
) -> str:
    """Pick a discovered work tree under ``project_cwd`` (or the primary one)."""
    roots = discover_git_workdirs(project_cwd)
    if not roots:
        raise ValueError("Not a git repository")
    if not (repo_root or "").strip():
        return roots[0]
    try:
        want = Path(repo_root).resolve()
    except OSError as e:
        raise ValueError("Invalid repo_root") from e
    for root in roots:
        try:
            if Path(root).resolve() == want:
                return str(Path(root).resolve())
        except OSError:
            if root == str(want):
                return root
    raise ValueError("repo_root is not a git worktree under this project")


def collect_project_pending_changes(
    cwd: str,
    *,
    max_files: int = 200,
    wait_timeout: float = 0.0,
    include_line_stats: bool = True,
) -> Dict[str, Any]:
    """Pending changes for every git work tree under a Cuttle project path."""
    roots = discover_git_workdirs(cwd)
    if not roots:
        raise ValueError("Not a git repository")
    repos: List[Dict[str, Any]] = []
    for root in roots:
        data = collect_pending_changes(
            root,
            max_files=max_files,
            wait_timeout=wait_timeout,
            include_line_stats=include_line_stats,
        )
        data = dict(data)
        data["label"] = repo_label(str(data.get("repo_root") or root), cwd)
        repos.append(data)
    return {"repos": repos}


def _parse_numstat(text: str) -> Dict[str, Tuple[int, int]]:
    """Map path -> (additions, deletions) from `git diff --numstat` output."""
    out: Dict[str, Tuple[int, int]] = {}
    for line in (text or "").splitlines():
        line = line.strip("\n")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a_s, d_s, path = parts[0], parts[1], parts[2]
        # Renames: "old => new" or with braces — take the last path segment after =>
        if " => " in path:
            path = path.split(" => ", 1)[-1].strip()
        if a_s == "-" or d_s == "-":
            # Binary
            out[path] = (0, 0)
            continue
        try:
            adds = int(a_s)
            dels = int(d_s)
        except ValueError:
            continue
        prev = out.get(path, (0, 0))
        out[path] = (prev[0] + adds, prev[1] + dels)
    return out


def _status_label(xy: str) -> str:
    """Map porcelain XY codes to a short UI label."""
    x, y = (xy + "  ")[:2]
    if xy.strip() == "??" or xy == "??":
        return "untracked"
    if "U" in xy or xy in ("DD", "AU", "UD", "UA", "DU", "AA"):
        return "conflict"
    if x == "A" or y == "A":
        if x == "D" or y == "D":
            return "modified"
        return "added"
    if x == "D" or y == "D":
        return "deleted"
    if x == "R" or y == "R":
        return "renamed"
    if x == "M" or y == "M" or x == "C" or y == "C":
        return "modified"
    if x == "T" or y == "T":
        return "modified"
    return "modified"


def _count_untracked_lines(repo_root: str, rel_path: str, cap: int = 20000) -> int:
    """Best-effort line count for an untracked text file (capped)."""
    try:
        full = Path(repo_root) / rel_path
        if not full.is_file():
            return 0
        # Skip huge / likely-binary blobs
        if full.stat().st_size > 2_000_000:
            return 0
        n = 0
        with full.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                if b"\0" in chunk:
                    return 0
                n += chunk.count(b"\n")
                if n >= cap:
                    return cap
        return n
    except OSError:
        return 0


def collect_pending_changes(
    cwd: str,
    *,
    max_files: int = 200,
    wait_timeout: float = 0.0,
    include_line_stats: bool = True,
    keep_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Return pending (uncommitted) changes for the git work tree containing cwd.

    ``wait_timeout``: seconds to wait for the per-repo collect lock (0 = fail
    immediately if busy — used by UI polls). Commit / suggest should pass a
    positive value so they are not starved by a slow poll.

    ``include_line_stats``: when False, skip numstat / untracked line counts
    (status-only; used by commit so the lock is released quickly).

    ``keep_paths``: repo-relative paths that must survive ``max_files``
    truncation (moved to the front; used by commit-message suggest so a
    selected file past the cap still resolves).

    Shape:
      {
        "repo_root": str,
        "branch": str,
        "clean": bool,
        "totals": {"files": int, "additions": int, "deletions": int},
        "files": [{"path", "status", "additions", "deletions", "staged"}, ...]
      }
    """
    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")

    with _collect_locks_guard:
        lock = _collect_locks.setdefault(root, threading.Lock())
    wait = max(0.0, float(wait_timeout or 0.0))
    if wait > 0:
        acquired = lock.acquire(timeout=wait)
    else:
        acquired = lock.acquire(blocking=False)
    if not acquired:
        raise RuntimeError("git pending-changes already running for this repo")
    try:
        return _collect_pending_changes_locked(
            root,
            max_files=max_files,
            include_line_stats=include_line_stats,
            keep_paths=keep_paths,
        )
    finally:
        lock.release()


def _numstat_for_paths(
    root: str,
    paths: List[str],
    *,
    timeout: float,
    chunk_size: int = 40,
) -> Dict[str, Tuple[int, int]]:
    """Path-limited numstat (avoids full-tree diff on huge dirty repos)."""
    out: Dict[str, Tuple[int, int]] = {}
    if not paths:
        return out
    for i in range(0, len(paths), max(1, chunk_size)):
        chunk = paths[i : i + chunk_size]
        num_r = git_run(
            ["diff", "--numstat", "HEAD", "--", *chunk],
            root,
            timeout=timeout,
        )
        if num_r.returncode == 0:
            parsed = _parse_numstat(num_r.stdout or "")
            for path, stats in parsed.items():
                prev = out.get(path, (0, 0))
                out[path] = (prev[0] + stats[0], prev[1] + stats[1])
    return out


def _collect_pending_changes_locked(
    root: str,
    *,
    max_files: int = 200,
    include_line_stats: bool = True,
    keep_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # Chat composer polls this; keep well under Electron's ~6-conn / 20s client cap.
    poll_timeout = 8.0
    branch_r = git_run(["branch", "--show-current"], root, timeout=poll_timeout)
    branch = (branch_r.stdout or "").strip() or "HEAD"

    status_r = git_run(["status", "--porcelain", "-u"], root, timeout=poll_timeout)
    if status_r.returncode != 0:
        err = (status_r.stderr or status_r.stdout or "git status failed").strip()
        raise RuntimeError(err)

    files: List[Dict[str, Any]] = []
    for raw in (status_r.stdout or "").splitlines():
        if not raw or len(raw) < 3:
            continue
        xy = raw[:2]
        path_part = raw[3:]
        # Renames in porcelain: "R  old -> new" or "RM old -> new"
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[-1]
        path_part = path_part.strip()
        # git quotes paths with spaces: "Assets/Foo Bar.cs"
        if len(path_part) >= 2 and path_part[0] == '"' and path_part[-1] == '"':
            path_part = path_part[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if not path_part:
            continue
        status = _status_label(xy)
        staged = bool(xy[0] not in (" ", "?"))
        files.append({
            "path": path_part,
            "status": status,
            "additions": 0,
            "deletions": 0,
            "staged": staged,
        })

    # Stable order: path
    files.sort(key=lambda f: f["path"].lower())
    total_pending = len(files)
    truncated = False
    if keep_paths:
        keep = {_normalize_repo_rel(str(p)) for p in keep_paths if str(p).strip()}
        keep.discard("")
        if keep:
            head = [f for f in files if _normalize_repo_rel(str(f.get("path") or "")) in keep]
            rest = [f for f in files if _normalize_repo_rel(str(f.get("path") or "")) not in keep]
            files = head + rest
    if len(files) > max_files:
        files = files[:max_files]
        truncated = True

    if include_line_stats and files:
        # Full-repo numstat is O(dirty tree) and can exceed poll_timeout on large
        # Unity deletes. Path-limit when heavy or already truncating the UI list.
        heavy = total_pending > _HEAVY_STATUS_ROWS or truncated
        if heavy:
            numstat = _numstat_for_paths(
                root,
                [str(f["path"]) for f in files],
                timeout=poll_timeout,
            )
        else:
            num_r = git_run(["diff", "--numstat", "HEAD"], root, timeout=poll_timeout)
            numstat = _parse_numstat(num_r.stdout or "") if num_r.returncode == 0 else {}

        for f in files:
            path_part = str(f["path"])
            adds, dels = numstat.get(path_part, (0, 0))
            if f["status"] == "untracked" and adds == 0 and dels == 0:
                adds = _count_untracked_lines(root, path_part)
            f["additions"] = int(adds)
            f["deletions"] = int(dels)

    total_adds = sum(int(f["additions"]) for f in files)
    total_dels = sum(int(f["deletions"]) for f in files)

    return {
        "repo_root": root,
        "branch": branch,
        "clean": total_pending == 0,
        "truncated": truncated,
        "pending_file_count": total_pending,
        "totals": {
            "files": len(files),
            "additions": total_adds,
            "deletions": total_dels,
        },
        "files": files,
    }


def _path_area(rel_path: str) -> str:
    """First meaningful directory (or filename) for grouping commit context."""
    parts = [p for p in str(rel_path or "").replace("\\", "/").split("/") if p]
    if not parts:
        return "?"
    if parts[0] in {".cuttle", "src", "web", "tests", "scripts", "api"} and len(parts) >= 2:
        return "/".join(parts[:2])
    if len(parts) >= 2:
        return parts[0]
    return parts[0]


def change_areas(files: List[Dict[str, Any]]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for f in files or []:
        p = str(f.get("path") or "").replace("\\", "/")
        if not p:
            continue
        area = _path_area(p)
        if area not in seen:
            seen.add(area)
            out.append(area)
    return out


def heuristic_commit_message(files: List[Dict[str, Any]]) -> str:
    """Deterministic fallback: verb + area(s), not 'file and N more'."""
    if not files:
        return "Update project files"
    paths = [str(f.get("path") or "").replace("\\", "/") for f in files if f.get("path")]
    if not paths:
        return "Update project files"

    def stem(p: str) -> str:
        name = Path(p).name
        return name if name else p

    statuses = {str(f.get("status") or "modified") for f in files}
    if statuses == {"deleted"}:
        verb = "Remove"
    elif statuses <= {"added", "untracked"}:
        verb = "Add"
    else:
        verb = "Update"

    if len(paths) == 1:
        return f"{verb} {stem(paths[0])}"

    areas = change_areas(files)
    if len(areas) == 1:
        return f"{verb} {areas[0]}"
    if len(areas) == 2:
        return f"{verb} {areas[0]} and {areas[1]}"
    return f"{verb} {areas[0]}, {areas[1]}, and {areas[2]}"


def collect_commit_suggest_context(
    cwd: str,
    *,
    max_diff_chars: int = 18000,
    max_files: int = 80,
    paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Pending file list + truncated diff excerpt for commit-message suggestion.

    When ``paths`` is set, only those repo-relative files are included (matches
    the chat UI's include/exclude checkboxes). Requested paths are kept past
    the ``max_files`` truncation so selecting a file the list cut off still
    resolves.
    """
    pending = collect_pending_changes(
        cwd,
        max_files=max_files,
        wait_timeout=45.0,
        include_line_stats=True,
        keep_paths=list(paths) if paths is not None else None,
    )
    if pending.get("clean") or not pending.get("files"):
        raise ValueError("Nothing to commit")

    root = str(pending["repo_root"])
    files = list(pending["files"] or [])
    if paths is not None:
        wanted = {_normalize_repo_rel(str(p)) for p in paths if str(p).strip()}
        files = [
            f for f in files
            if _normalize_repo_rel(str(f.get("path") or "")) in wanted
        ]
        if not files:
            raise ValueError("No selected files to describe")

    path_args = [_normalize_repo_rel(str(f.get("path") or "")) for f in files]
    path_args = [p for p in path_args if p]
    diff_cmd = ["diff", "HEAD"]
    if path_args:
        diff_cmd.extend(["--", *path_args])
    diff_r = git_run(diff_cmd, root, timeout=45.0)
    diff_text = (diff_r.stdout or "") if diff_r.returncode == 0 else ""

    # Untracked files never appear in `git diff HEAD` — append short previews.
    untracked_bits: List[str] = []
    for f in files:
        if f.get("status") != "untracked":
            continue
        rel = str(f.get("path") or "")
        if not rel or _looks_like_secret_path(rel):
            untracked_bits.append(f"--- /dev/null\n+++ b/{rel}\n(new file; content omitted)\n")
            continue
        try:
            full = Path(root) / rel
            if not full.is_file() or full.stat().st_size > 200_000:
                untracked_bits.append(f"--- /dev/null\n+++ b/{rel}\n(new file; content omitted)\n")
                continue
            raw = full.read_bytes()
            if b"\0" in raw[:8192]:
                untracked_bits.append(f"--- /dev/null\n+++ b/{rel}\n(new binary file)\n")
                continue
            text = raw.decode("utf-8", errors="replace")
            lines = text.splitlines()[:80]
            body = "\n".join(f"+{ln}" for ln in lines)
            untracked_bits.append(f"--- /dev/null\n+++ b/{rel}\n{body}\n")
        except OSError:
            untracked_bits.append(f"--- /dev/null\n+++ b/{rel}\n(new file; unread)\n")

    if untracked_bits:
        diff_text = (diff_text.rstrip() + "\n\n" + "\n".join(untracked_bits)).lstrip()

    truncated_diff = False
    if len(diff_text) > max_diff_chars:
        diff_text = diff_text[:max_diff_chars].rstrip() + "\n\n… [diff truncated]"
        truncated_diff = True

    file_lines = []
    for f in files:
        path = f.get("path") or "?"
        st = f.get("status") or "modified"
        a = int(f.get("additions") or 0)
        d = int(f.get("deletions") or 0)
        file_lines.append(f"{st:9} {path} (+{a} -{d})")

    areas = change_areas(files)
    area_lines = []
    counts = Counter(_path_area(str(f.get("path") or "")) for f in files if f.get("path"))
    for area in areas:
        area_lines.append(f"{area} ({counts[area]} file{'s' if counts[area] != 1 else ''})")

    recent_subjects: List[str] = []
    log_r = git_run(["log", "-5", "--pretty=%s"], root)
    if log_r.returncode == 0:
        recent_subjects = [ln.strip() for ln in (log_r.stdout or "").splitlines() if ln.strip()]

    total_adds = sum(int(f.get("additions") or 0) for f in files)
    total_dels = sum(int(f.get("deletions") or 0) for f in files)

    return {
        "repo_root": root,
        "branch": pending.get("branch"),
        "files": files,
        "areas": areas,
        "area_summary": ", ".join(area_lines),
        "file_summary": "\n".join(file_lines),
        "diff_excerpt": diff_text,
        "diff_truncated": truncated_diff,
        "recent_subjects": recent_subjects,
        "heuristic_message": heuristic_commit_message(files),
        "totals": {
            "files": len(files),
            "additions": total_adds,
            "deletions": total_dels,
        },
    }


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def parse_unified_diff(diff_text: str) -> List[Dict[str, Any]]:
    """Parse unified diff text into hunks with typed lines for UI rendering."""
    hunks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    old_no = 0
    new_no = 0

    for raw in (diff_text or "").splitlines():
        if raw.startswith("@@"):
            if current is not None:
                hunks.append(current)
            m = _HUNK_HEADER_RE.match(raw)
            if not m:
                current = None
                continue
            old_start = int(m.group("old_start"))
            old_count = int(m.group("old_count") or 1)
            new_start = int(m.group("new_start"))
            new_count = int(m.group("new_count") or 1)
            old_no = old_start
            new_no = new_start
            current = {
                "header": raw,
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": [],
            }
            continue
        if current is None:
            continue
        if raw.startswith("\\"):
            current["lines"].append({"type": "meta", "text": raw[1:].strip()})
            continue
        kind = raw[:1]
        text = raw[1:] if len(raw) > 1 else ""
        if kind == "+":
            current["lines"].append(
                {"type": "add", "old_no": None, "new_no": new_no, "text": text}
            )
            new_no += 1
        elif kind == "-":
            current["lines"].append(
                {"type": "del", "old_no": old_no, "new_no": None, "text": text}
            )
            old_no += 1
        elif kind == " ":
            current["lines"].append(
                {"type": "context", "old_no": old_no, "new_no": new_no, "text": text}
            )
            old_no += 1
            new_no += 1

    if current is not None:
        hunks.append(current)
    return hunks


def _truncate_hunks(hunks: List[Dict[str, Any]], max_lines: int) -> Tuple[List[Dict[str, Any]], bool]:
    if max_lines <= 0:
        return [], bool(hunks)
    out: List[Dict[str, Any]] = []
    remaining = max_lines
    truncated = False
    for hunk in hunks:
        lines = list(hunk.get("lines") or [])
        if remaining <= 0:
            truncated = True
            break
        if len(lines) <= remaining:
            out.append(hunk)
            remaining -= len(lines)
            continue
        out.append({**hunk, "lines": lines[:remaining]})
        truncated = True
        break
    return out, truncated


def _filter_hunks_by_line_type(
    hunks: List[Dict[str, Any]], line_type: str
) -> List[Dict[str, Any]]:
    """Keep only lines of one type (add/del); drop empty hunks."""
    out: List[Dict[str, Any]] = []
    for hunk in hunks or []:
        lines = [
            ln for ln in (hunk.get("lines") or []) if ln.get("type") == line_type
        ]
        if not lines:
            continue
        label = "added" if line_type == "add" else "removed"
        out.append({
            **hunk,
            "header": f"@@ {label} ({len(lines)} lines) @@",
            "lines": lines,
        })
    return out


def _count_hunk_lines(hunks: List[Dict[str, Any]], line_type: Optional[str] = None) -> int:
    total = 0
    for hunk in hunks or []:
        for ln in hunk.get("lines") or []:
            if line_type is None or ln.get("type") == line_type:
                total += 1
    return total


def _section_payload(
    hunks: List[Dict[str, Any]], *, max_lines: int
) -> Dict[str, Any]:
    total = _count_hunk_lines(hunks)
    truncated_hunks, truncated = _truncate_hunks(hunks, max_lines)
    shown = _count_hunk_lines(truncated_hunks)
    return {
        "hunks": truncated_hunks,
        "truncated": truncated,
        "shown": shown,
        "total": total,
    }


def _porcelain_xy_for_path(root: str, rel: str) -> str:
    st = git_run(["status", "--porcelain", "-u", "--", rel], root)
    for ln in (st.stdout or "").splitlines():
        if len(ln) < 4:
            continue
        path_part = ln[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[-1].strip()
        if path_part.replace("\\", "/") == rel:
            return ln[:2]
    return ""


def _untracked_file_hunks(
    root: str,
    rel: str,
    *,
    max_lines: int,
    max_file_bytes: int,
) -> Tuple[List[Dict[str, Any]], bool, bool, str]:
    """Return (hunks, truncated, binary, message) for a new untracked file."""
    full = Path(root) / rel.replace("/", os.sep)
    if not full.is_file():
        raise ValueError(f"File not found: {rel}")
    size = full.stat().st_size
    if size > max_file_bytes:
        raise ValueError("File too large to preview")
    raw = full.read_bytes()
    if b"\0" in raw[:8192]:
        return [], False, True, "Binary file (no text preview)"
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    hunk_lines = [
        {"type": "add", "old_no": None, "new_no": idx + 1, "text": ln}
        for idx, ln in enumerate(lines)
    ]
    count = len(hunk_lines)
    hunks = [{
        "header": f"@@ -0,0 +1,{count} @@",
        "old_start": 0,
        "old_count": 0,
        "new_start": 1,
        "new_count": count,
        "lines": hunk_lines,
    }]
    return hunks, truncated, False, ""


def collect_file_pending_diff(
    cwd: str,
    rel_path: str,
    *,
    context_lines: int = 3,
    max_lines: int = 300,
    max_file_bytes: int = 500_000,
    full: bool = False,
) -> Dict[str, Any]:
    """Structured diff for one pending file (HEAD vs working tree).

    Returns side-separated sections (added / removed), each truncated independently
    so a huge add block cannot hide deletions. Pass full=True (or a very large
    max_lines) to lift the per-side cap for "Show all".
    """
    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")

    rel = _validate_repo_rel(rel_path)
    ctx = max(0, min(int(context_lines), 20))
    # Per-side budget (independent). "Show all" uses a high ceiling, not unlimited.
    per_side = 100_000 if full else max(1, min(int(max_lines or 300), 20_000))

    xy = _porcelain_xy_for_path(root, rel)
    status = _status_label(xy or "??")
    is_untracked = status == "untracked"

    empty_sections = {
        "added": {"hunks": [], "truncated": False, "shown": 0, "total": 0},
        "removed": {"hunks": [], "truncated": False, "shown": 0, "total": 0},
    }

    if _looks_like_secret_path(rel):
        return {
            "repo_root": root,
            "path": rel,
            "status": status,
            "binary": False,
            "secret_redacted": True,
            "additions": 0,
            "deletions": 0,
            "hunks": [],
            "sections": empty_sections,
            "truncated": False,
            "truncated_added": False,
            "truncated_removed": False,
            "max_lines_per_side": per_side,
            "full": bool(full),
            "message": "Diff hidden for credential-like paths",
        }

    if is_untracked:
        hunks, truncated, binary, message = _untracked_file_hunks(
            root, rel, max_lines=per_side, max_file_bytes=max_file_bytes
        )
        adds = sum(
            1 for h in hunks for ln in (h.get("lines") or []) if ln.get("type") == "add"
        )
        # Recover true total for untracked (may exceed per_side)
        total_adds = adds
        try:
            full_path = Path(root) / rel.replace("/", os.sep)
            if full_path.is_file() and full_path.stat().st_size <= max_file_bytes:
                raw = full_path.read_bytes()
                if b"\0" not in raw[:8192]:
                    total_adds = len(raw.decode("utf-8", errors="replace").splitlines())
        except OSError:
            pass
        sections = {
            "added": {
                "hunks": hunks,
                "truncated": truncated,
                "shown": adds,
                "total": total_adds,
            },
            "removed": {"hunks": [], "truncated": False, "shown": 0, "total": 0},
        }
        return {
            "repo_root": root,
            "path": rel,
            "status": status,
            "binary": binary,
            "secret_redacted": False,
            "additions": total_adds,
            "deletions": 0,
            "hunks": hunks,
            "sections": sections,
            "truncated": truncated,
            "truncated_added": truncated,
            "truncated_removed": False,
            "max_lines_per_side": per_side,
            "full": bool(full),
            "message": message,
        }

    diff_r = git_run(["diff", "HEAD", f"-U{ctx}", "--", rel], root, timeout=45.0)
    diff_text = (diff_r.stdout or "") if diff_r.returncode == 0 else ""
    if "Binary files" in diff_text and " differ" in diff_text:
        return {
            "repo_root": root,
            "path": rel,
            "status": status,
            "binary": True,
            "secret_redacted": False,
            "additions": 0,
            "deletions": 0,
            "hunks": [],
            "sections": empty_sections,
            "truncated": False,
            "truncated_added": False,
            "truncated_removed": False,
            "max_lines_per_side": per_side,
            "full": bool(full),
            "message": "Binary file (no text preview)",
        }

    full_hunks = parse_unified_diff(diff_text)
    added_section = _section_payload(
        _filter_hunks_by_line_type(full_hunks, "add"), max_lines=per_side
    )
    removed_section = _section_payload(
        _filter_hunks_by_line_type(full_hunks, "del"), max_lines=per_side
    )
    sections = {"added": added_section, "removed": removed_section}
    truncated = bool(added_section["truncated"] or removed_section["truncated"])

    # Compat unified hunks: still side-balanced so legacy UIs don't hide deletes.
    hunks, _ = _truncate_hunks(
        full_hunks,
        max(per_side * 2, 100),
    )

    adds = _count_hunk_lines(full_hunks, "add")
    dels = _count_hunk_lines(full_hunks, "del")

    num_r = git_run(["diff", "--numstat", "HEAD", "--", rel], root)
    numstat = _parse_numstat(num_r.stdout or "") if num_r.returncode == 0 else {}
    ns_adds, ns_dels = numstat.get(rel, (adds, dels))
    if ns_adds or ns_dels:
        adds, dels = int(ns_adds), int(ns_dels)

    message = ""
    if not full_hunks and not diff_text.strip():
        message = "No line changes to show"

    return {
        "repo_root": root,
        "path": rel,
        "status": status,
        "binary": False,
        "secret_redacted": False,
        "additions": adds,
        "deletions": dels,
        "hunks": hunks,
        "sections": sections,
        "truncated": truncated,
        "truncated_added": bool(added_section["truncated"]),
        "truncated_removed": bool(removed_section["truncated"]),
        "max_lines_per_side": per_side,
        "full": bool(full),
        "message": message,
    }


def resolve_allowed_project_cwd(
    path: Optional[str],
    project_id: Optional[int],
    projects: List[Dict[str, Any]],
    current_project: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Resolve a cwd from path / project_id against the registered projects list.
    Returns (cwd, project, error_message).

    Prefer an explicit filesystem path when provided — chat panes often carry a
    stale project_id alongside a still-valid path.

    Windows paths from the cloned projects.db (``C:\\Projects\\Cuttle\\src``) are
    rewritten onto this checkout so pending-changes works on Linux.
    """
    if path:
        try:
            want = Path(_live_fs_path(path)).resolve()
        except OSError:
            return None, None, "Invalid path"
        for p in projects or []:
            pp = _live_fs_path((p.get("path") or "").strip())
            if not pp:
                continue
            try:
                if Path(pp).resolve() == want:
                    if not want.is_dir():
                        return None, p, "Project path is not a directory"
                    return str(want), p, None
            except OSError:
                continue
        # Allow exact match of current project even if list is stale
        if current_project and (current_project.get("path") or "").strip():
            try:
                cur = Path(_live_fs_path(current_project["path"])).resolve()
                if cur == want and want.is_dir():
                    return str(want), current_project, None
            except OSError:
                pass
        # Path given but not registered — fall through to project_id before failing
        if project_id is None:
            return None, None, "Path is not a registered project"

    if project_id is not None:
        for p in projects or []:
            try:
                if int(p.get("id")) == int(project_id):
                    pp = _live_fs_path((p.get("path") or "").strip())
                    if not pp or not os.path.isdir(pp):
                        return None, p, "Project path missing or not a directory"
                    return str(Path(pp).resolve()), p, None
            except (TypeError, ValueError):
                continue
        if path:
            return None, None, "Path is not a registered project"
        return None, None, "Unknown project_id"

    if current_project and (current_project.get("path") or "").strip():
        pp = _live_fs_path(current_project["path"])
        if os.path.isdir(pp):
            return str(Path(pp).resolve()), current_project, None

    return None, None, "No project path provided"


_SECRET_PATH_HINTS = (
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    "secrets.json",
)

# Templates / samples are safe to commit; real dotenv variants are not.
_SECRET_ENV_ALLOWLIST = frozenset({
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.example.local",  # uncommon, but still a template name
})


def _looks_like_secret_path(rel_path: str) -> bool:
    name = Path(rel_path.replace("\\", "/")).name.lower()
    if name in _SECRET_ENV_ALLOWLIST:
        return False
    if name in {h.lower() for h in _SECRET_PATH_HINTS}:
        return True
    if name.endswith(".pem") or name.endswith(".key") or name.endswith(".p12"):
        return True
    if name.startswith(".env.") or name == ".env":
        return True
    return False


def _normalize_repo_rel(rel_path: str) -> str:
    rel = (rel_path or "").replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel.strip("/")


def _validate_repo_rel(rel: str) -> str:
    """Return a safe repo-relative path or raise ValueError."""
    cleaned = _normalize_repo_rel(rel)
    if not cleaned:
        raise ValueError("Path is required")
    if cleaned.startswith("/") or cleaned.startswith("\\"):
        raise ValueError("Path must be repository-relative")
    parts = cleaned.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("Invalid path")
    if ":" in cleaned and len(cleaned) > 1 and cleaned[1] == ":":
        raise ValueError("Path must be repository-relative")
    return cleaned


def _git_add_all(root: str, timeout: float = 120.0) -> None:
    """``git add -A``, falling back to tracked-only when ignored dirs are listed."""
    add_r = git_run(["add", "-A"], root, timeout=timeout)
    if add_r.returncode == 0:
        return
    err = (add_r.stderr or add_r.stdout or "git add failed").strip()
    if "ignored by one of your .gitignore" not in err:
        raise RuntimeError(err)
    # Tracked edits/deletes under ignored paths (e.g. /temp/) still need staging.
    upd = git_run(["add", "-u"], root, timeout=timeout)
    if upd.returncode != 0:
        raise RuntimeError((upd.stderr or upd.stdout or err).strip())
    # Untracked, non-ignored files: add without touching ignored pathspecs.
    rest = git_run(["add", "--", "."], root, timeout=timeout)
    if rest.returncode != 0 and "ignored by one of your .gitignore" not in (
        rest.stderr or ""
    ):
        raise RuntimeError((rest.stderr or rest.stdout or err).strip())


def _stage_repo_paths(root: str, path_args: List[str]) -> None:
    """Stage selected paths even when some are gitignored tracked deletions."""
    for i in range(0, len(path_args), 40):
        chunk = path_args[i : i + 40]
        add_r = git_run(["add", "-A", "--", *chunk], root, timeout=60.0)
        if add_r.returncode == 0:
            continue
        for rel in chunk:
            # A failed batch add can already have staged earlier deletions.
            # Those paths no longer exist in either the worktree or index;
            # retrying add/rm reports a false pathspec error.
            removed = git_run(
                ["diff", "--cached", "--name-only", "--diff-filter=D", "--", rel], root
            )
            if removed.returncode == 0 and rel in (removed.stdout or "").splitlines():
                if not (Path(root) / rel).exists():
                    continue
            one = git_run(["add", "-A", "--", rel], root, timeout=30.0)
            if one.returncode == 0:
                continue
            # Ignored dir or deleted-from-disk tracked file.
            upd = git_run(["add", "-u", "--", rel], root, timeout=30.0)
            if upd.returncode == 0:
                continue
            rm = git_run(["rm", "--cached", "-f", "--", rel], root, timeout=30.0)
            if rm.returncode == 0:
                continue
            ignored = git_run(["check-ignore", "-q", "--", rel], root)
            if ignored.returncode == 0:
                continue
            err = (
                one.stderr or upd.stderr or rm.stderr or one.stdout or "git add failed"
            ).strip()
            raise RuntimeError(err)


def ignore_pending_path(cwd: str, rel_path: str) -> Dict[str, Any]:
    """Append ``rel_path`` to ``.gitignore`` and unstage/untrack it if needed.

    Working tree file is left in place. Tracked files are removed from the index
    (``git rm --cached``) so they stop showing as pending modifications.
    """
    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")
    clear_stale_index_lock(root)

    rel = _validate_repo_rel(rel_path)
    gi = Path(root) / ".gitignore"
    existing = ""
    if gi.is_file():
        try:
            existing = gi.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise RuntimeError(f"Could not read .gitignore: {e}") from e

    patterns = {
        ln.strip()
        for ln in existing.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    added_rule = False
    if rel not in patterns and f"/{rel}" not in patterns:
        try:
            with gi.open("a", encoding="utf-8", newline="\n") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(rel + "\n")
            added_rule = True
        except OSError as e:
            raise RuntimeError(f"Could not update .gitignore: {e}") from e

    untracked_from_index = False
    ls = git_run(["ls-files", "--", rel], root)
    if (ls.stdout or "").strip():
        rm = git_run(["rm", "--cached", "-f", "--", rel], root, timeout=30.0)
        if rm.returncode != 0:
            err = (rm.stderr or rm.stdout or "git rm --cached failed").strip()
            raise RuntimeError(err)
        untracked_from_index = True

    return {
        "repo_root": root,
        "path": rel,
        "gitignore": str(gi),
        "added_rule": added_rule,
        "untracked_from_index": untracked_from_index,
    }


def _git_commit_env(cwd: str) -> Dict[str, str]:
    """Identity for ``git commit`` without writing user.name to gitconfig.

    Linux clones often have no local/global git identity; Windows setups
    usually already have one. Env vars from ``src/.env`` (GITEA_COMMIT_AUTHOR_*)
    fill the gap.
    """
    env = os.environ.copy()
    ident = git_run(["var", "GIT_AUTHOR_IDENT"], cwd, timeout=5.0)
    if ident.returncode == 0 and "@" in (ident.stdout or ""):
        return env
    try:
        from api.cuttle_jobs.workspace import cuttle_commit_env

        env.update(cuttle_commit_env())
        return env
    except Exception:
        pass
    name = (
        env.get("GITEA_COMMIT_AUTHOR_NAME")
        or env.get("GITEA_AGENT_USERNAME")
        or env.get("GIT_AUTHOR_NAME")
        or "Cuttle"
    )
    email = (
        env.get("GITEA_COMMIT_AUTHOR_EMAIL")
        or env.get("GIT_AUTHOR_EMAIL")
        or "cuttle@localhost"
    )
    env["GIT_AUTHOR_NAME"] = name
    env["GIT_AUTHOR_EMAIL"] = email
    env["GIT_COMMITTER_NAME"] = name
    env["GIT_COMMITTER_EMAIL"] = email
    return env


def commit_pending_changes(
    cwd: str,
    message: str,
    *,
    allow_secrets: bool = False,
    paths: Optional[List[str]] = None,
    include_unlisted: bool = False,
    shown_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Stage pending changes and create one commit.

    When ``paths`` is provided, only those repo-relative files are included
    (other pending files are left unstaged). When omitted, stages everything
    (``git add -A``).

    When ``include_unlisted`` is True, ``shown_files`` is the UI's truncated
    list: every pending path *not* in that list is included, plus ``paths``
    (the checked rows). Unchecked rows in ``shown_files`` stay out.

    Returns dict with repo_root, branch, commit (hash), subject, files_committed.
    Raises ValueError for user-facing errors (empty message, nothing to commit,
    secret files blocked).
    """
    msg = (message or "").strip()
    if not msg:
        raise ValueError("Commit message is required")

    root = resolve_git_workdir(cwd)
    if not root:
        raise ValueError("Not a git repository")
    clear_stale_index_lock(root)

    # Status-only + wait: do not race UI polls or pay for full-repo numstat.
    pending = collect_pending_changes(
        root,
        max_files=50_000,
        wait_timeout=60.0,
        include_line_stats=False,
    )
    if pending.get("clean") or not pending.get("files"):
        raise ValueError("Nothing to commit")

    pending_by_norm = {
        _normalize_repo_rel(str(f.get("path") or "")): f for f in pending["files"]
    }
    all_pending_files = list(pending["files"])

    excluded_shown: Optional[set] = None
    wanted: Optional[set] = None

    if include_unlisted:
        shown = set()
        for raw in shown_files or []:
            try:
                shown.add(_validate_repo_rel(str(raw)))
            except ValueError:
                continue
        selected = set()
        for raw in paths or []:
            try:
                selected.add(_validate_repo_rel(str(raw)))
            except ValueError:
                continue
        # Unchecked rows among the truncated list are excluded; everything
        # else pending (including unlisted paths) is included.
        excluded_shown = shown - selected
        files_to_commit = [
            f
            for f in all_pending_files
            if _normalize_repo_rel(str(f.get("path") or "")) not in excluded_shown
        ]
        if not files_to_commit:
            raise ValueError("Nothing to commit")
    elif paths is not None:
        wanted = set()
        for raw in paths:
            try:
                wanted.add(_validate_repo_rel(str(raw)))
            except ValueError:
                continue
        if not wanted:
            raise ValueError("No files selected to commit")
        files_to_commit = [
            pending_by_norm[p] for p in sorted(wanted) if p in pending_by_norm
        ]
        missing = sorted(wanted - set(pending_by_norm.keys()))
        if missing and not files_to_commit:
            raise ValueError(
                "Selected files are not pending: " + ", ".join(missing[:6])
            )
        if not files_to_commit:
            raise ValueError("Nothing to commit")
    else:
        files_to_commit = list(all_pending_files)

    secret_hits = [
        f["path"]
        for f in files_to_commit
        if _looks_like_secret_path(str(f.get("path") or ""))
    ]
    if secret_hits and not allow_secrets:
        shown = ", ".join(secret_hits[:8])
        more = f" (+{len(secret_hits) - 8} more)" if len(secret_hits) > 8 else ""
        raise ValueError(
            "Refusing to commit likely secret files: "
            + shown
            + more
            + ". Remove them from the working tree or pass allow_secrets."
        )

    # Conflicts: refuse rather than create a bad commit.
    conflicts = [f["path"] for f in files_to_commit if f.get("status") == "conflict"]
    if conflicts:
        raise ValueError(
            "Unresolved merge conflicts: " + ", ".join(conflicts[:6])
            + ("…" if len(conflicts) > 6 else "")
        )

    # Drop other staged paths so a partial commit cannot pick them up.
    # Preserve explicitly staged executable-bit changes. With core.filemode=false,
    # reset followed by add would otherwise silently erase them.
    mode_changes = {}
    raw_index = git_run(["diff", "--cached", "--raw", "-z", "--no-renames"], root)
    if raw_index.returncode != 0:
        raise RuntimeError((raw_index.stderr or "Could not read staged modes").strip())
    records = (raw_index.stdout or "").split("\0")
    selected_paths = {str(f.get("path") or "") for f in files_to_commit}
    for i in range(0, len(records) - 1, 2):
        fields = records[i].split()
        rel = records[i + 1]
        if len(fields) == 5 and rel in selected_paths:
            before, after = fields[0].lstrip(":"), fields[1]
            if before in ("100644", "100755") and after in ("100644", "100755") and before != after:
                mode_changes[rel] = after
    reset_r = git_run(["reset", "HEAD", "--", "."], root, timeout=30.0)
    if reset_r.returncode != 0:
        raise RuntimeError((reset_r.stderr or reset_r.stdout or "git reset failed").strip())

    if include_unlisted:
        # Full add then unstage unchecked visible rows — avoids huge argv lists.
        _git_add_all(root, timeout=120.0)
        if excluded_shown:
            excl = sorted(p for p in excluded_shown if p)
            for i in range(0, len(excl), 40):
                chunk = excl[i : i + 40]
                reset_r = git_run(
                    ["reset", "HEAD", "--", *chunk], root, timeout=60.0
                )
                if reset_r.returncode != 0:
                    err = (reset_r.stderr or reset_r.stdout or "git reset failed").strip()
                    raise RuntimeError(err)
    elif wanted is not None:
        path_args = [
            _normalize_repo_rel(str(f.get("path") or "")) for f in files_to_commit
        ]
        path_args = [p for p in path_args if p]
        _stage_repo_paths(root, path_args)
    else:
        _git_add_all(root, timeout=120.0)

    for rel, mode in mode_changes.items():
        changed = git_run(["update-index", "--chmod=" + ("+x" if mode == "100755" else "-x"), "--", rel], root)
        if changed.returncode != 0:
            raise RuntimeError((changed.stderr or "Could not preserve staged mode").strip())

    # Confirm index has something to commit.
    staged = git_run(["diff", "--cached", "--name-only"], root)
    staged_files = [ln.strip() for ln in (staged.stdout or "").splitlines() if ln.strip()]
    if not staged_files:
        raise ValueError("Nothing staged to commit")

    # Proven attribution trailers (observed harness deltas only — never guessed).
    attribution: Dict[str, Any] = {}
    commit_msg = msg
    try:
        from api.edit_attribution.journal import (
            build_commit_attribution,
            merge_message_with_trailers,
        )

        attribution = build_commit_attribution(root, staged_files)
        commit_msg = merge_message_with_trailers(msg, attribution).rstrip() + "\n"
    except Exception as attr_exc:
        print(f"[edit_attribution] commit trailers skipped: {attr_exc}", flush=True)
        attribution = {}
        commit_msg = msg + ("\n" if not msg.endswith("\n") else "")

    commit_env = _git_commit_env(root)
    author_name = commit_env.get("GIT_AUTHOR_NAME") or "Cuttle"
    author_email = commit_env.get("GIT_AUTHOR_EMAIL") or "cuttle@localhost"
    commit_r = subprocess.run(
        [
            "git",
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "-F",
            "-",
        ],
        cwd=root,
        input=commit_msg,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120.0,
        check=False,
        env=commit_env,
        creationflags=_creationflags(),
    )
    if commit_r.returncode != 0:
        err = (commit_r.stderr or commit_r.stdout or "git commit failed").strip()
        raise RuntimeError(err)

    hash_r = git_run(["rev-parse", "HEAD"], root)
    commit_full = (hash_r.stdout or "").strip()
    commit_hash = commit_full[:7] if commit_full else ""
    short_r = git_run(["rev-parse", "--short", "HEAD"], root)
    if (short_r.stdout or "").strip():
        commit_hash = (short_r.stdout or "").strip()
    subject = msg.splitlines()[0].strip()
    branch_r = git_run(["branch", "--show-current"], root)
    branch = (branch_r.stdout or "").strip() or pending.get("branch") or "HEAD"

    settled = 0
    if attribution and commit_full:
        try:
            from api.edit_attribution.journal import settle_events

            settled = settle_events(
                root,
                staged_files,
                commit_full,
                event_ids=attribution.get("event_ids"),
            )
        except Exception as settle_exc:
            print(f"[edit_attribution] settle failed: {settle_exc}", flush=True)

    out: Dict[str, Any] = {
        "repo_root": root,
        "branch": branch,
        "commit": commit_hash,
        "commit_full": commit_full,
        "subject": subject,
        "files_committed": staged_files,
        "files_count": len(staged_files),
    }
    if attribution:
        out["attribution"] = {
            "attributed": attribution.get("attributed") or {},
            "unattributed": attribution.get("unattributed") or [],
            "query_ids": attribution.get("query_ids") or [],
            "attributed_count": attribution.get("attributed_count") or 0,
            "unattributed_count": attribution.get("unattributed_count") or 0,
            "events_settled": settled,
        }
    return out
