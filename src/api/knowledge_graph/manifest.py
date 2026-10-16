"""Provider manifest — the declared schema a knowledge-graph provider ships.

Mirrors the agent-adapter manifest idea: identity, capabilities, executable
names, and install hint are declared, never inferred from code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

REQUIRED_CAPABILITIES = ("index", "query")
ALL_CAPABILITIES = ("index", "update", "query", "explain", "node_path", "export")


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    store_root: str
    executable_names: Tuple[str, ...]
    capabilities: Tuple[str, ...]
    install_hint: str
    description: str
    source: str
    path: Path


def load_manifest(manifest_path: Path, *, source: str = "", expect_id: str = "") -> ProviderInfo:
    """Parse + validate a provider manifest. Raises ValueError when malformed."""
    if yaml is None:  # pragma: no cover
        raise ValueError("PyYAML is required to load provider manifests")
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"manifest must be a mapping: {manifest_path}")

    pid = str(raw.get("id") or "").strip()
    if not _ID_RE.match(pid):
        raise ValueError(f"invalid provider id {pid!r} in {manifest_path}")
    if expect_id and pid != expect_id:
        raise ValueError(f"provider id {pid!r} does not match its directory {expect_id!r}")

    store_root = str(raw.get("store_root") or pid).strip().strip("/").replace("\\", "/")
    if not store_root or ".." in store_root.split("/"):
        raise ValueError(f"invalid store_root {store_root!r} for provider {pid!r}")

    caps = tuple(str(c) for c in (raw.get("capabilities") or ALL_CAPABILITIES))
    unknown = [c for c in caps if c not in ALL_CAPABILITIES]
    if unknown:
        raise ValueError(f"provider {pid!r} declares unknown capabilities: {unknown}")
    missing = [c for c in REQUIRED_CAPABILITIES if c not in caps]
    if missing:
        raise ValueError(f"provider {pid!r} missing required capabilities: {missing}")

    def _str_list(key: str) -> Tuple[str, ...]:
        value = raw.get(key) or []
        if isinstance(value, str):
            value = [value]
        return tuple(str(v).strip() for v in value if str(v).strip())

    return ProviderInfo(
        id=pid,
        label=str(raw.get("label") or pid.replace("-", " ").title()).strip(),
        store_root=store_root,
        executable_names=_str_list("executable_names"),
        capabilities=caps,
        install_hint=str(raw.get("install_hint") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        source=source,
        path=manifest_path,
    )


def manifest_summary(info: ProviderInfo) -> Dict[str, Any]:
    return {
        "id": info.id, "label": info.label, "store_root": info.store_root,
        "capabilities": list(info.capabilities),
        "executable_names": list(info.executable_names),
        "install_hint": info.install_hint, "description": info.description,
        "source": info.source, "path": str(info.path),
    }
