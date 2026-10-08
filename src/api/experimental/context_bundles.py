"""Effective feature resources shared by Brain, skills, docs and recipe owners.

Discovery reads configuration only. Personal files cannot enable a feature.
Resources are qualified by feature; runtime execution rechecks current gates.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
from pathlib import Path
import re

from api.experimental.flags import all_flags, enabled_flags

_view = ContextVar("feature_context_view", default=None)
_CATEGORIES = {"rules", "docs", "skills", "commands", "actions", "scripts"}


def availability():
    current = _view.get()
    return dict(current) if current is not None else enabled_flags()


@contextmanager
def captured():
    if _view.get() is not None:
        yield
        return
    token = _view.set(enabled_flags())
    try:
        yield
    finally:
        _view.reset(token)


def capture_call(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with captured():
            return fn(*args, **kwargs)
    return wrapper


def roots(global_root=None):
    if global_root is None:
        from api.cuttle_brain.context_compiler import _cuttle_global_config
        global_root = _cuttle_global_config()
    if global_root is None:
        return []
    from api.cuttle_brain.personal_overlay import personal_root
    out = []
    flags = availability()
    for spec in all_flags():
        if not spec.context_bundle or not flags.get(spec.id) or not re.fullmatch(r"[a-z0-9_]+", spec.id):
            continue
        root = Path(global_root) / "features" / spec.id
        local = personal_root(Path(global_root)) / "features" / spec.id
        if any(p.is_symlink() for p in (root, root.parent, local, local.parent)):
            continue
        out.append((spec.id, root, local))
    return out


def unit_dirs(category, project_path=None, global_root=None):
    from api.cuttle_brain.global_layers import load_global_layers
    if category not in _CATEGORIES:
        return []
    layers = load_global_layers(project_path)
    if category == "rules":
        allowed = layers.rules_mode != "off"
    else:
        allowed = getattr(layers, category, True)
    if not allowed:
        return []
    out = []
    for ident, root, local in roots(global_root):
        for folder in (local / category, root / category):
            if not folder.is_symlink():
                out.append((folder, f"feature/{ident}"))
    return out


def markdown(category, project_path=None, global_root=None):
    from api.cuttle_brain.personal_overlay import read_merged_md
    out = []
    allowed = {p for p, _ in unit_dirs(category, project_path, global_root)}
    for ident, root, local in roots(global_root):
        folder = root / category
        source = f"feature/{ident}"
        if folder not in allowed:
            continue
        for name, text in read_merged_md(folder, limit=None):
            # The shared overlay reader is permissive for legacy core roots;
            # feature resources refuse file symlinks in either twin.
            from api.cuttle_brain.personal_overlay import personal_root
            if (folder / name).is_symlink() or (personal_root(folder.parent) / category / name).is_symlink():
                continue
            out.append((f"{source}/{category}/{name}", text))
    return out


def read_doc(ref, project_path=None):
    return dict(markdown("docs", project_path)).get(ref, "")


def inventory(project_path=None):
    from api.project_commands import list_project_commands
    from api.project_actions import list_project_actions
    return {"commands": [c["ref"] for c in list_project_commands(project_path or "") if c.get("feature_id")],
            "actions": [a["ref"] for a in list_project_actions(project_path or "") if a.get("feature_id")]}


def feature_for_path(path):
    """Recognize only registered bundle roots; caller still checks enablement."""
    path = Path(path)
    from api.cuttle_brain.context_compiler import _cuttle_global_config
    from api.cuttle_brain.personal_overlay import personal_root
    global_root = _cuttle_global_config()
    if global_root is None:
        return None
    for base in (global_root / "features", personal_root(global_root) / "features"):
        try:
            parts = path.absolute().relative_to(base.absolute()).parts
        except ValueError:
            continue
        if len(parts) > 1:
            return parts[0]
    return None


def executable_available(resource, project_path=None):
    project_path = resource.get("context_project_path") or project_path
    ident = resource.get("feature_id") or feature_for_path(resource.get("path") or "")
    if not ident:
        return True
    # No captured view for side effects: even an old card must use live flags.
    from api.experimental.flags import get_flag, is_enabled
    from api.cuttle_brain.global_layers import load_global_layers
    spec = get_flag(ident)
    category = "commands" if "body" in resource else "actions"
    return bool(spec and spec.context_bundle and is_enabled(ident) and getattr(load_global_layers(project_path), category))


def snapshot(project_path=None):
    """Availability and content hashes, including recipe dependencies."""
    out = {f"feature/{s.id}": "enabled" if availability().get(s.id) else "disabled"
           for s in all_flags() if s.context_bundle}
    for category in sorted(_CATEGORIES):
        for folder, source in unit_dirs(category, project_path):
            if not folder.is_dir():
                continue
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents) if p != folder.parent):
                    continue
                try:
                    relative = path.relative_to(folder).as_posix()
                    layer = "personal" if folder.parent.parent.parent.name != ".cuttle_global" else "tracked"
                    out[f"{source}/{category}/{relative}:{layer}"] = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    continue
    return out
