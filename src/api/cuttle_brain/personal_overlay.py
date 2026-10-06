"""Install-local ``personal/`` overlay (gitignored).

Mirrors the tracked layout (rules, docs, actions, commands, scripts, skills) beside
whichever cuttle root it belongs to — ``{project}/.cuttle/personal/`` or
``{install}/.cuttle_global/personal/``.

Two behaviors, by file kind:

- **Markdown (rules/docs): supplement, not fork.** A personal twin is *appended*
  after the tracked text (marked install-local), so personal files stay small
  deltas and cannot desync from the baseline.
- **Everything else (yaml/commands/skills/scripts): basename wins.** Structured files
  cannot concatenate, so the personal twin replaces the tracked one.

``resolve_cuttle_file`` still returns the personal *path* when present (for
opening/editing); content readers that want merged markdown use
``read_merged_md`` / ``read_cuttle_file_merged``.

Tracked trees stay product-portable; machine-specific paths, LAN hosts,
guild aliases, and dogfood notes live only under ``personal/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

PERSONAL_DIRNAME = "personal"
PERSONAL_SUBDIRS = (
    "commands",
    "rules",
    "actions",
    "docs",
    "scripts",
    "skills",
)


def personal_root(cuttle_root: Path) -> Path:
    """``personal/`` beside a cuttle root (``{project}/.cuttle`` or ``{install}/.cuttle_global``)."""
    return Path(cuttle_root) / PERSONAL_DIRNAME


def resolve_cuttle_file(cuttle_root: Path, *parts: str) -> Optional[Path]:
    """Resolve a file under ``.cuttle/``, preferring ``personal/`` when present.

    ``parts`` are relative to the tracked cuttle root, e.g. ``(\"docs\", \"chat-history.md\")``.
    Returns None if neither personal nor tracked file exists.
    """
    root = Path(cuttle_root)
    if not parts:
        return None
    personal = personal_root(root).joinpath(*parts)
    if personal.is_file():
        return personal
    tracked = root.joinpath(*parts)
    if tracked.is_file():
        return tracked
    return None


def merge_named_files(
    tracked_dir: Path,
    *,
    patterns: Sequence[str] = ("*.md",),
    limit: Optional[int] = 40,
) -> List[Tuple[str, Path]]:
    """List files in a tracked subdir with personal overrides winning by basename.

    Returns ``(basename, path)`` sorted by name. Personal-only files are included.
    ``limit=None`` disables truncation (governing rule bodies); integer limits
    still apply to inventory callers.
    """
    by_name: dict[str, Path] = {}
    if tracked_dir.is_dir():
        for pattern in patterns:
            for path in sorted(tracked_dir.glob(pattern), key=lambda p: p.name.lower()):
                if path.is_file():
                    by_name[path.name.lower()] = path
    # personal sibling: tracked_dir is e.g. .cuttle/docs → .cuttle/personal/docs
    try:
        cuttle_root = tracked_dir.parent
        sub = tracked_dir.name
    except Exception:
        return [(p.name, p) for p in by_name.values()]
    personal_dir = personal_root(cuttle_root) / sub
    if personal_dir.is_dir():
        for pattern in patterns:
            for path in sorted(personal_dir.glob(pattern), key=lambda p: p.name.lower()):
                if path.is_file():
                    by_name[path.name.lower()] = path  # override / add
    items = sorted(by_name.values(), key=lambda p: p.name.lower())
    if limit is not None:
        items = items[:limit]
    return [(p.name, p) for p in items]


_DELTA_MARKER = "*Install-local delta (`personal/{rel}` — appended after tracked; tracked above is canonical):*"


def _append_delta(tracked_text: str, rel: str, personal_path: Path) -> str:
    try:
        delta = personal_path.read_text(encoding="utf-8").strip()
    except OSError:
        delta = ""
    if not delta:
        return tracked_text
    return (
        tracked_text.rstrip()
        + "\n\n---\n"
        + _DELTA_MARKER.format(rel=rel)
        + "\n\n"
        + delta
        + "\n"
    )


def read_merged_md(
    tracked_dir: Path,
    *,
    limit: Optional[int] = 24,
) -> List[Tuple[str, str]]:
    """Read markdown files with personal twins appended as deltas (never replacing).

    ``limit=None`` reads all sorted files (governing rule bodies); the default
    bound remains for non-rule callers.
    """
    out: List[Tuple[str, str]] = []
    tracked_dir = Path(tracked_dir)
    personal_dir = personal_root(tracked_dir.parent) / tracked_dir.name

    def _twin(directory: Path, name: str) -> Optional[Path]:
        if not directory.is_dir():
            return None
        for cand in sorted(directory.glob("*.md"), key=lambda p: p.name.lower()):
            if cand.is_file() and cand.name.lower() == name.lower():
                return cand
        return None

    for name, _ in merge_named_files(tracked_dir, patterns=("*.md",), limit=limit):
        tracked = _twin(tracked_dir, name)
        personal = _twin(personal_dir, name)
        text = ""
        has_tracked_text = False
        if tracked is not None:
            try:
                text = tracked.read_text(encoding="utf-8").strip()
                has_tracked_text = bool(text)
            except OSError:
                text = ""
        if not text and personal is not None:
            # Personal-only file: included as-is.
            try:
                text = personal.read_text(encoding="utf-8").strip()
            except OSError:
                continue
        if not text:
            continue
        if tracked is not None and personal is not None and has_tracked_text:
            text = _append_delta(text, f"{tracked_dir.name}/{tracked.name}", personal)
        out.append((tracked.name if tracked is not None else name, text))
    return out


def list_merged_delta_names(
    tracked_dir: Path,
    patterns: Sequence[str] = ("*.md",),
    *,
    limit: int = 40,
) -> List[str]:
    """Basenames in ``tracked_dir`` that have a personal twin (carry an appended delta)."""
    tracked_dir = Path(tracked_dir)
    root = personal_root(tracked_dir.parent) / tracked_dir.name
    names: List[str] = []
    seen = set()
    if root.is_dir():
        for pattern in patterns:
            try:
                entries = sorted(root.glob(pattern), key=lambda p: p.name.lower())
            except OSError:
                continue
            for path in entries:
                if not path.is_file() or path.name.lower() in seen:
                    continue
                # Only a delta when a tracked file of the same name exists.
                if not (tracked_dir / path.name).is_file():
                    continue
                seen.add(path.name.lower())
                names.append(path.name)
                if len(names) >= limit:
                    return names
    return names


def read_cuttle_file_merged(cuttle_root: Path, *parts: str) -> Optional[str]:
    """Merged text of a tracked file plus its personal delta (None if neither file exists)."""
    if not parts:
        return None
    root = Path(cuttle_root)
    tracked = root.joinpath(*parts)
    if not tracked.is_file():
        personal = personal_root(root).joinpath(*parts)
        try:
            return personal.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    try:
        text = tracked.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        personal = personal_root(root).joinpath(*parts)
        try:
            return personal.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    rel = "/".join(parts)
    personal = personal_root(root).joinpath(*parts)
    if personal.is_file():
        text = _append_delta(text, rel, personal)
    return text


def list_merged_names(
    tracked_dir: Path,
    patterns: Sequence[str],
    *,
    limit: int = 40,
) -> List[str]:
    return [name for name, _ in merge_named_files(tracked_dir, patterns=patterns, limit=limit)]


def ensure_personal_tree(cuttle_root: Path) -> List[str]:
    """Create ``.cuttle/personal/{subdirs}`` if missing. Never overwrites files.

    Returns relative paths created (posix).
    """
    root = Path(cuttle_root)
    created: List[str] = []
    personal = personal_root(root)
    if not personal.is_dir():
        personal.mkdir(parents=True, exist_ok=True)
        created.append(f".cuttle/{PERSONAL_DIRNAME}")
    for sub in PERSONAL_SUBDIRS:
        d = personal / sub
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            created.append(f".cuttle/{PERSONAL_DIRNAME}/{sub}")
    return created


def scoped_unit_dirs(
    project_path: Optional[str], category: str, global_root: Path, *, include_global: bool = True
) -> List[Tuple[Path, str]]:
    """Highest priority first; structured units replace by their declared identity."""
    out: List[Tuple[Path, str]] = []
    if project_path:
        try:
            root = Path(project_path).resolve()
        except (OSError, ValueError):
            return out
        if root.is_dir():
            for config, scope in ((root / ".cuttle", "project"), (root / "source" / ".cuttle", "project-nested")):
                out.extend(((config / "personal" / category, scope + "-personal"), (config / category, scope)))
    if include_global:
        out.extend(((global_root / "personal" / category, "global-personal"), (global_root / category, "global")))
    return out


def unit_disabled(meta: dict) -> bool:
    """Only an explicit boolean true disables a structured unit (not truthy strings)."""
    return meta.get("disabled") is True
