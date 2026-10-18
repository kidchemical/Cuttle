"""Workspace file operations for the IDE App.

A workspace is a registered project path (or a registered worktree checkout
of one), resolved through the same allowlist as the Git routes. Every file
path is workspace-relative and confined to the workspace after symlink
resolution; ``.git/`` internals are never writable.

Writes are atomic (temp file + ``os.replace``) and optimistic: callers pass
the ``etag`` they loaded and a mismatch raises :class:`Conflict` instead of
overwriting a file an agent or another editor changed in the meantime.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat as stat_mod
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_DIR_ENTRIES = 5000
MAX_LIST_FILES = 50000
MAX_STAT_FILES = 64
MAX_SEARCH_RESULTS = 5000
MAX_PREVIEW_CHARS = 240
GIT_TIMEOUT = 15.0
SEARCH_TIMEOUT = 30.0
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".tox", ".nox", ".next", ".cache",
}
ENCODINGS = ("utf-8", "utf-8-bom", "utf-16", "latin-1")
RAW_IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".ico": "image/x-icon", ".avif": "image/avif",
}


class IdeError(ValueError):
    status = 400


class NotFound(IdeError):
    status = 404


class Conflict(IdeError):
    status = 409

    def __init__(self, message: str, current: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.current = current or {}


# ---------------------------------------------------------------------------
# workspaces
# ---------------------------------------------------------------------------
def _registered() -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Registered projects + the active one (patched in tests)."""
    from managers.project_manager import project_manager

    if project_manager is None:
        raise IdeError("Project manager is unavailable.")
    return project_manager.get_projects(), project_manager.get_current_project()


def list_workspaces() -> List[Dict[str, Any]]:
    projects, current = _registered()
    current_id = (current or {}).get("id")
    out = []
    for project in projects:
        out.append({
            "id": project.get("id"),
            "name": str(project.get("name") or "Project"),
            "path": str(project.get("path") or ""),
            "available": project.get("available") is not False,
            "current": current_id is not None and project.get("id") == current_id,
        })
    return out


def resolve_workspace(path: Optional[str]) -> Path:
    """Return the resolved root for a registered project/checkout path."""
    raw = str(path or "").strip()
    if not raw or "\x00" in raw:
        raise IdeError("Choose a project.")
    from scripts.utilities.git_pending_changes import resolve_allowed_project_cwd

    projects, current = _registered()
    cwd, _project, err = resolve_allowed_project_cwd(raw, None, projects, current)
    if err or not cwd:
        raise IdeError(err or "Path is not a registered project")
    root = Path(cwd).resolve()
    if not root.is_dir():
        raise NotFound("Project folder is missing.")
    return root


def normalize_rel(rel: Optional[str]) -> str:
    """Canonical workspace-relative POSIX path ('' is the root)."""
    text = str(rel or "").replace("\\", "/").strip()
    if "\x00" in text:
        raise IdeError("Invalid path")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise IdeError("Paths must be relative to the project")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise IdeError("Paths must stay inside the project")
    return "/".join(parts)


def safe_path(root: Path, rel: Optional[str], *, must_exist: bool = True) -> Tuple[Path, str]:
    """Resolve ``rel`` under ``root``; symlinks may not escape the workspace."""
    norm = normalize_rel(rel)
    target = root / norm if norm else root
    try:
        if must_exist or target.exists() or target.is_symlink():
            resolved = target.resolve(strict=True)
        else:
            # The leaf does not exist yet; its parent chain must still be inside.
            resolved = target.parent.resolve() / target.name
    except FileNotFoundError as exc:
        raise NotFound(f"{norm or 'Project root'} does not exist") from exc
    except OSError as exc:
        raise IdeError(str(exc)) from exc
    if resolved != root and not resolved.is_relative_to(root):
        raise IdeError("Path is outside the project")
    return resolved, norm


def _writable(norm: str) -> None:
    if not norm:
        raise IdeError("The project root cannot be changed here")
    if norm.split("/")[0] == ".git":
        raise IdeError("Git internals are read-only")


# ---------------------------------------------------------------------------
# git helpers (subprocess only; behavior stays read-only)
# ---------------------------------------------------------------------------
def _git(root: Path, args: Sequence[str], *, input_bytes: Optional[bytes] = None,
         timeout: float = GIT_TIMEOUT) -> Optional[subprocess.CompletedProcess]:
    if not shutil.which("git"):
        return None
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=off", *args], cwd=str(root), input=input_bytes,
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_toplevel(directory: Path) -> Optional[Path]:
    proc = _git(directory, ["rev-parse", "--show-toplevel"])
    if not proc or proc.returncode != 0:
        return None
    text = proc.stdout.decode("utf-8", "replace").strip()
    return Path(text).resolve() if text else None


def _ignored(root: Path, rels: List[str]) -> set:
    if not rels:
        return set()
    proc = _git(root, ["check-ignore", "-z", "--stdin"], input_bytes="\0".join(rels).encode() + b"\0")
    # Exit 1 means "nothing ignored"; 128 means not a repository.
    if not proc or proc.returncode not in (0, 1):
        return set()
    return {p.rstrip("/") for p in proc.stdout.decode("utf-8", "replace").split("\0") if p}


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def etag_for(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def _looks_binary(data: bytes) -> bool:
    head = data[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    return b"\x00" in head


def decode_text(data: bytes) -> Tuple[Optional[str], str]:
    """Return (text, encoding id) — text is None for binary content."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-bom"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace"), "utf-16"
    if _looks_binary(data):
        return None, ""
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def encode_text(text: str, encoding: str) -> bytes:
    enc = encoding if encoding in ENCODINGS else "utf-8"
    if enc == "utf-8-bom":
        return b"\xef\xbb\xbf" + text.encode("utf-8")
    if enc == "utf-16":
        return text.encode("utf-16")
    if enc == "latin-1":
        try:
            return text.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise IdeError("Text contains characters latin-1 cannot store; reopen the file as UTF-8") from exc
    return text.encode("utf-8")


def _meta(path: Path, norm: str) -> Dict[str, Any]:
    st = path.stat()
    return {"path": norm, "size": st.st_size, "mtime": st.st_mtime,
            "readonly": not os.access(path, os.W_OK) or norm.split("/")[0] == ".git"}


def read_file(root: Path, rel: str) -> Dict[str, Any]:
    path, norm = safe_path(root, rel)
    if not path.is_file():
        raise IdeError(f"{norm or 'Project root'} is not a file")
    out = _meta(path, norm)
    ext = path.suffix.lower()
    out["image"] = ext in RAW_IMAGE_TYPES
    if out["size"] > MAX_TEXT_BYTES:
        out.update(too_large=True, binary=False, content=None, etag=None)
        return out
    data = path.read_bytes()
    text, encoding = decode_text(data)
    out.update(etag=etag_for(data), too_large=False, binary=text is None,
               content=text, encoding=encoding or None,
               eol="crlf" if text and "\r\n" in text else "lf")
    if text is None:
        out["mime"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return out


def raw_image(root: Path, rel: str) -> Tuple[Path, str]:
    """Raster images only: SVG/HTML could script the Cuttle origin."""
    path, norm = safe_path(root, rel)
    mime = RAW_IMAGE_TYPES.get(path.suffix.lower())
    if not path.is_file() or not mime:
        raise IdeError(f"{norm} is not a previewable image")
    return path, mime


def stat_files(root: Path, rels: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for rel in list(rels)[:MAX_STAT_FILES]:
        try:
            path, norm = safe_path(root, rel)
        except NotFound:
            out[normalize_rel(rel)] = {"exists": False}
            continue
        if not path.is_file():
            out[norm] = {"exists": False}
            continue
        st = path.stat()
        etag = etag_for(path.read_bytes()) if st.st_size <= MAX_TEXT_BYTES else None
        out[norm] = {"exists": True, "etag": etag, "size": st.st_size, "mtime": st.st_mtime}
    return out


def list_dir(root: Path, rel: str = "") -> Dict[str, Any]:
    path, norm = safe_path(root, rel)
    if not path.is_dir():
        raise IdeError(f"{norm} is not a folder")
    entries: List[Dict[str, Any]] = []
    truncated = False
    with os.scandir(path) as it:
        for entry in it:
            if entry.name == ".git":
                continue
            if len(entries) >= MAX_DIR_ENTRIES:
                truncated = True
                break
            child = f"{norm}/{entry.name}" if norm else entry.name
            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
            except OSError:
                is_dir, size = False, 0
            entries.append({"name": entry.name, "path": child, "type": "dir" if is_dir else "file",
                            "size": size, "symlink": entry.is_symlink()})
    ignored = _ignored(root, [e["path"] + ("/" if e["type"] == "dir" else "") for e in entries])
    for e in entries:
        e["ignored"] = e["path"] in ignored
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower(), e["name"]))
    return {"path": norm, "entries": entries, "truncated": truncated}


def _walk_files(root: Path, limit: int) -> Tuple[List[str], bool]:
    out: List[str] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        base = here.relative_to(root).as_posix()
        for name in sorted(files):
            out.append(f"{base}/{name}" if base != "." else name)
            if len(out) >= limit:
                return out, True
    return out, False


def list_files(root: Path, limit: int = MAX_LIST_FILES) -> Dict[str, Any]:
    """Every non-ignored file (quick open, fallback search)."""
    proc = _git(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], timeout=SEARCH_TIMEOUT)
    if proc and proc.returncode == 0:
        seen = set()
        files = []
        for p in proc.stdout.decode("utf-8", "replace").split("\0"):
            # Deleted-but-tracked files are still listed by --cached.
            if p and p not in seen and (root / p).is_file():
                seen.add(p)
                files.append(p)
        files.sort()
        truncated = len(files) > limit
        return {"files": files[:limit], "truncated": truncated, "source": "git"}
    files, truncated = _walk_files(root, limit)
    return {"files": files, "truncated": truncated, "source": "walk"}


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def _atomic_write(path: Path, data: bytes) -> None:
    mode = None
    try:
        mode = stat_mod.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".cuttle-tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_file(root: Path, rel: str, content: str, *, etag: Optional[str] = None,
               encoding: str = "utf-8", create: bool = False, force: bool = False) -> Dict[str, Any]:
    if not isinstance(content, str):
        raise IdeError("content must be text")
    path, norm = safe_path(root, rel, must_exist=False)
    _writable(norm)
    if path.exists():
        if not path.is_file():
            raise IdeError(f"{norm} is not a file")
        if create:
            raise Conflict(f"{norm} already exists")
        current = path.read_bytes()
        current_etag = etag_for(current)
        if not force and etag != current_etag:
            raise Conflict(f"{norm} changed on disk since it was opened",
                           {"etag": current_etag, "size": len(current)})
    elif not create:
        raise NotFound(f"{norm} no longer exists")
    elif not path.parent.is_dir():
        raise NotFound(f"Folder {Path(norm).parent.as_posix()} does not exist")
    data = encode_text(content, encoding)
    if len(data) > MAX_TEXT_BYTES:
        raise IdeError("File is too large to save from the editor")
    _atomic_write(path, data)
    return {**_meta(path, norm), "etag": etag_for(data)}


def create_entry(root: Path, rel: str, kind: str = "file") -> Dict[str, Any]:
    path, norm = safe_path(root, rel, must_exist=False)
    _writable(norm)
    if path.exists() or path.is_symlink():
        raise Conflict(f"{norm} already exists")
    if not path.parent.is_dir():
        raise NotFound(f"Folder {Path(norm).parent.as_posix()} does not exist")
    if kind == "dir":
        path.mkdir()
    elif kind == "file":
        _atomic_write(path, b"")
    else:
        raise IdeError("kind must be 'file' or 'dir'")
    return {"path": norm, "type": kind}


def rename_entry(root: Path, src: str, dst: str) -> Dict[str, Any]:
    src_path, src_norm = safe_path(root, src)
    dst_path, dst_norm = safe_path(root, dst, must_exist=False)
    _writable(src_norm)
    _writable(dst_norm)
    if dst_path.exists() and src_path != dst_path:
        # Case-only renames on case-insensitive filesystems resolve to the same file.
        raise Conflict(f"{dst_norm} already exists")
    if src_path.is_dir() and dst_path != src_path and dst_path.is_relative_to(src_path):
        raise IdeError("A folder cannot be moved into itself")
    if not dst_path.parent.is_dir():
        raise NotFound(f"Folder {Path(dst_norm).parent.as_posix()} does not exist")
    # Rename the entry itself (a symlink stays a symlink), not its target.
    os.rename(root / src_norm, root / dst_norm)
    return {"from": src_norm, "to": dst_norm}


def delete_entry(root: Path, rel: str) -> Dict[str, Any]:
    path = root / normalize_rel(rel)
    norm = normalize_rel(rel)
    _writable(norm)
    # A symlink is removed itself, never followed.
    if path.is_symlink():
        if not path.parent.resolve().is_relative_to(root):
            raise IdeError("Path is outside the project")
        path.unlink()
        return {"path": norm, "type": "file"}
    resolved, norm = safe_path(root, norm)
    if resolved.is_dir():
        shutil.rmtree(resolved)
        return {"path": norm, "type": "dir"}
    resolved.unlink()
    return {"path": norm, "type": "file"}


# ---------------------------------------------------------------------------
# search / replace
# ---------------------------------------------------------------------------
def _split_globs(text: Optional[str]) -> List[str]:
    return [g.strip() for g in str(text or "").split(",") if g.strip()][:32]


def _glob_match(rel: str, pattern: str) -> bool:
    pat = pattern.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    if "/" not in pat:
        # Bare names/globs (``*.py``, ``tests``) match any path segment.
        return any(fnmatch.fnmatch(seg, pat) for seg in rel.split("/")) or fnmatch.fnmatch(rel, pat)
    return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat.replace("**/", "")) or rel.startswith(pat.rstrip("*").rstrip("/") + "/")


def compile_query(query: str, *, regex: bool, case: bool, word: bool) -> "re.Pattern[str]":
    if not query:
        raise IdeError("Enter a search term")
    if len(query) > 1000:
        raise IdeError("Search term is too long")
    body = query if regex else re.escape(query)
    if word:
        body = rf"\b(?:{body})\b"
    try:
        return re.compile(body, 0 if case else re.IGNORECASE)
    except re.error as exc:
        raise IdeError(f"Invalid regular expression: {exc}") from exc


def _preview(line: str, start: int, end: int) -> Tuple[str, int]:
    """Clip a long line around the match; returns (text, offset of text in line)."""
    line = line.rstrip("\r\n")
    if len(line) <= MAX_PREVIEW_CHARS:
        return line, 0
    lead = max(0, start - 60)
    return line[lead:lead + MAX_PREVIEW_CHARS], lead


def _candidates(root: Path, include: List[str], exclude: List[str]) -> List[str]:
    files = list_files(root)["files"]
    if include:
        files = [f for f in files if any(_glob_match(f, g) for g in include)]
    if exclude:
        files = [f for f in files if not any(_glob_match(f, g) for g in exclude)]
    return files


def _search_python(root: Path, pattern: "re.Pattern[str]", files: List[str], limit: int) -> Tuple[List[Dict[str, Any]], bool]:
    results: List[Dict[str, Any]] = []
    total = 0
    for rel in files:
        path = root / rel
        try:
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            text, _enc = decode_text(path.read_bytes())
        except OSError:
            continue
        if text is None:
            continue
        matches = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in pattern.finditer(line):
                if m.end() == m.start():
                    continue
                preview, offset = _preview(line, m.start(), m.end())
                matches.append({"line": lineno, "col": m.start() + 1, "length": m.end() - m.start(),
                                "preview": preview, "preview_offset": offset})
                total += 1
                if total >= limit:
                    results.append({"path": rel, "matches": matches})
                    return results, True
        if matches:
            results.append({"path": rel, "matches": matches})
    return results, False


def _search_rg(root: Path, query: str, *, regex: bool, case: bool, word: bool,
               include: List[str], exclude: List[str], limit: int) -> Optional[Tuple[List[Dict[str, Any]], bool]]:
    rg = shutil.which("rg")
    if not rg:
        return None
    args = [rg, "--json", "--hidden", "--glob", "!.git", "--max-filesize", str(MAX_SEARCH_FILE_BYTES)]
    if not regex:
        args.append("--fixed-strings")
    args.append("--case-sensitive" if case else "--ignore-case")
    if word:
        args.append("--word-regexp")
    for g in include:
        args += ["--glob", g if "/" in g or g.startswith("*") else f"**/{g}"]
    for g in exclude:
        args += ["--glob", "!" + (g if "/" in g or g.startswith("*") else f"**/{g}")]
    args += ["--regexp", query, "."]
    try:
        proc = subprocess.Popen(args, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return None
    by_path: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    total = 0
    truncated = False
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            rel = (data.get("path") or {}).get("text") or ""
            rel = rel[2:] if rel.startswith("./") else rel
            line_text = (data.get("lines") or {}).get("text")
            if line_text is None or not rel:
                continue
            line_bytes = line_text.encode("utf-8")
            entry = by_path.get(rel)
            if entry is None:
                entry = by_path[rel] = {"path": rel, "matches": []}
                order.append(rel)
            for sub in data.get("submatches") or []:
                # rg reports byte offsets; the editor wants characters.
                start = len(line_bytes[:sub["start"]].decode("utf-8", "replace"))
                end = len(line_bytes[:sub["end"]].decode("utf-8", "replace"))
                if end <= start:
                    continue
                preview, offset = _preview(line_text, start, end)
                entry["matches"].append({"line": data.get("line_number") or 0, "col": start + 1,
                                         "length": end - start, "preview": preview, "preview_offset": offset})
                total += 1
            if total >= limit:
                truncated = True
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    if proc.returncode not in (0, 1) and not by_path and not truncated:
        # rg failed (bad regex for its engine, …): let the Python engine answer.
        return None
    return [by_path[p] for p in order if by_path[p]["matches"]], truncated


def search(root: Path, query: str, *, regex: bool = False, case: bool = False, word: bool = False,
           include: str = "", exclude: str = "", max_results: int = 2000) -> Dict[str, Any]:
    pattern = compile_query(query, regex=regex, case=case, word=word)
    limit = max(1, min(int(max_results or 2000), MAX_SEARCH_RESULTS))
    inc, exc = _split_globs(include), _split_globs(exclude)
    engine = "rg"
    found = _search_rg(root, query, regex=regex, case=case, word=word, include=inc, exclude=exc, limit=limit)
    if found is None:
        engine = "python"
        found = _search_python(root, pattern, _candidates(root, inc, exc), limit)
    results, truncated = found
    results.sort(key=lambda r: r["path"])
    return {"results": results, "truncated": truncated, "engine": engine,
            "files": len(results), "matches": sum(len(r["matches"]) for r in results)}


def _js_replacement(replacement: str, regex: bool) -> str:
    """VS Code replacement syntax ($1, $&, $$) → Python ``re.sub`` template."""
    if not regex:
        return replacement.replace("\\", "\\\\")
    out = []
    i = 0
    while i < len(replacement):
        ch = replacement[i]
        nxt = replacement[i + 1] if i + 1 < len(replacement) else ""
        if ch == "\\" and nxt in ("n", "t", "\\"):
            out.append({"n": "\n", "t": "\t", "\\": "\\\\"}[nxt])
            i += 2
        elif ch == "\\":
            out.append("\\\\")
            i += 1
        elif ch == "$" and nxt == "$":
            out.append("$")
            i += 2
        elif ch == "$" and nxt == "&":
            out.append(r"\g<0>")
            i += 2
        elif ch == "$" and nxt.isdigit():
            j = i + 1
            while j < len(replacement) and replacement[j].isdigit() and j - i <= 2:
                j += 1
            out.append(rf"\g<{replacement[i + 1:j]}>")
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def replace(root: Path, query: str, replacement: str, *, regex: bool = False, case: bool = False,
            word: bool = False, include: str = "", exclude: str = "",
            paths: Optional[List[str]] = None) -> Dict[str, Any]:
    pattern = compile_query(query, regex=regex, case=case, word=word)
    template = _js_replacement(str(replacement or ""), regex)
    if paths is None:
        targets = [r["path"] for r in search(root, query, regex=regex, case=case, word=word,
                                             include=include, exclude=exclude,
                                             max_results=MAX_SEARCH_RESULTS)["results"]]
    else:
        targets = [normalize_rel(p) for p in paths][:2000]
    changed: List[Dict[str, Any]] = []
    total = 0
    for rel in targets:
        path, norm = safe_path(root, rel)
        _writable(norm)
        data = path.read_bytes()
        text, encoding = decode_text(data)
        if text is None or len(data) > MAX_SEARCH_FILE_BYTES:
            continue
        try:
            updated, count = pattern.subn(template, text)
        except (re.error, IndexError) as exc:
            raise IdeError(f"Invalid replacement: {exc}") from exc
        if count and updated != text:
            out = encode_text(updated, encoding)
            _atomic_write(path, out)
            changed.append({"path": norm, "count": count, "etag": etag_for(out)})
            total += count
    return {"files": changed, "replacements": total}


# ---------------------------------------------------------------------------
# git content (quick diff / diff editor)
# ---------------------------------------------------------------------------
def git_original(root: Path, rel: str, ref: str = "HEAD") -> Dict[str, Any]:
    """Committed content of a workspace file, for the diff editor and gutter."""
    if not re.fullmatch(r"[A-Za-z0-9_./~^@{}-]{1,200}", ref or "") or ref.startswith("-"):
        raise IdeError("Invalid ref")
    path, norm = safe_path(root, rel, must_exist=False)
    top = _git_toplevel(path.parent if path.parent.is_dir() else root)
    if top is None:
        return {"path": norm, "tracked": False, "content": None, "reason": "not a git repository"}
    try:
        repo_rel = path.relative_to(top).as_posix()
    except ValueError:
        return {"path": norm, "tracked": False, "content": None, "reason": "outside repository"}
    proc = _git(top, ["show", f"{ref}:{repo_rel}"])
    if not proc or proc.returncode != 0:
        return {"path": norm, "tracked": False, "content": None, "reason": "not in " + ref}
    data = proc.stdout
    if len(data) > MAX_TEXT_BYTES:
        return {"path": norm, "tracked": True, "content": None, "reason": "too large"}
    text, encoding = decode_text(data)
    return {"path": norm, "tracked": True, "content": text, "binary": text is None,
            "encoding": encoding or None, "ref": ref}


# ---------------------------------------------------------------------------
# inline completion (opt-in in the editor; uses Cuttle's completion providers)
# ---------------------------------------------------------------------------
COMPLETION_SYSTEM = (
    "You are a code completion engine. Continue the code at <CURSOR>. Reply with only the "
    "text to insert at the cursor: no explanations, no markdown fences, no repetition of "
    "the surrounding code. Prefer a short completion (one line or one small block). "
    "Reply with nothing if no completion is useful."
)


def strip_completion(text: Optional[str], prefix: str, suffix: str) -> str:
    out = str(text or "")
    fence = re.match(r"^\s*```[\w+-]*\n(.*?)\n?```\s*$", out, re.S)
    if fence:
        out = fence.group(1)
    tail = prefix.splitlines()[-1] if prefix.splitlines() else ""
    # Models often echo the current line; keep only what comes after it.
    if tail.strip() and out.startswith(tail):
        out = out[len(tail):]
    head = suffix[:200]
    if head.strip() and head.strip() in out:
        out = out[:out.index(head.strip())]
    return out[:2000]


def complete(*, path: str, language: str, prefix: str, suffix: str) -> Dict[str, Any]:
    from api.completion_providers import resolve_order
    from api.llm_complete import complete as llm_complete

    prefix = str(prefix or "")[-6000:]
    suffix = str(suffix or "")[:2000]
    user = (f"File: {normalize_rel(path) or 'untitled'} ({language or 'text'})\n\n"
            f"{prefix}<CURSOR>{suffix}")
    # Pin the API providers: a background CLI chain is far too slow for typing.
    text = llm_complete(user=user, system=COMPLETION_SYSTEM, max_tokens=160, temperature=0.1,
                        timeout=12.0, providers=resolve_order())
    return {"text": strip_completion(text, prefix, suffix), "available": text is not None}
