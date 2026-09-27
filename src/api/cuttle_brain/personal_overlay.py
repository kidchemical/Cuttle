"""Install-local ``.cuttle/personal/`` overlay (gitignored).

Mirrors the tracked ``.cuttle/`` layout (rules, docs, actions, commands, scripts).
When the same relative path exists under ``personal/``, that file wins.

Tracked ``.cuttle/`` stays product-portable; machine-specific paths, LAN hosts,
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
)


def personal_root(cuttle_root: Path) -> Path:
    """``{project|hub}/.cuttle/personal``."""
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
    limit: int = 40,
) -> List[Tuple[str, Path]]:
    """List files in a tracked subdir with personal overrides winning by basename.

    Returns ``(basename, path)`` sorted by name. Personal-only files are included.
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
    items = sorted(by_name.values(), key=lambda p: p.name.lower())[:limit]
    return [(p.name, p) for p in items]


def read_merged_md(
    tracked_dir: Path,
    *,
    limit: int = 24,
) -> List[Tuple[str, str]]:
    """Read markdown files with personal overrides (basename wins)."""
    out: List[Tuple[str, str]] = []
    for name, path in merge_named_files(tracked_dir, patterns=("*.md",), limit=limit):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            out.append((name, text))
    return out


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
