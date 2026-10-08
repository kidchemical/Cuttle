"""Adapter resource declarations and verified, local artifact provenance.

Declarations describe trusted code; they do not sandbox it. Digest verification
is performed before import and repeated on discovery, including cached adapters.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


def permissions(raw):
    if raw is None:
        return {"filesystem": "unspecified", "network": "unspecified", "subprocess": None}
    if not isinstance(raw, dict) or set(raw) - {"filesystem", "network", "subprocess"}:
        raise ValueError("invalid adapter permissions")
    result = permissions(None)
    for key, values in (("filesystem", {"none", "workspace", "unrestricted"}),
                        ("network", {"none", "outbound", "unrestricted"})):
        if key in raw:
            if not isinstance(raw[key], str) or raw[key] not in values:
                raise ValueError(f"invalid {key} permission declaration")
            result[key] = raw[key]
    if "subprocess" in raw:
        if not isinstance(raw["subprocess"], bool):
            raise ValueError("subprocess declaration must be boolean")
        result["subprocess"] = raw["subprocess"]
    return result


def provenance(raw, root: Path | None):
    if raw is None:
        return {"verified": False, "sha256": {}}
    if not isinstance(raw, dict) or set(raw) - {"source_url", "revision", "sha256"}:
        raise ValueError("invalid adapter provenance")
    source = raw.get("source_url", "")
    revision = raw.get("revision", "")
    if not isinstance(source, str) or not isinstance(revision, str):
        raise ValueError("provenance source and revision must be strings")
    if source:
        url = urlparse(source)
        if url.scheme != "https" or not url.netloc or url.username or url.password:
            raise ValueError("provenance source_url must be credential-free HTTPS")
    hashes = raw.get("sha256", {})
    if not isinstance(hashes, dict) or len(hashes) > 256:
        raise ValueError("invalid provenance sha256 map")
    if hashes:
        if root is None or "adapter.py" not in hashes:
            raise ValueError("provenance hashes must cover adapter.py")
        root = root.resolve()
        python_files = {p.relative_to(root).as_posix() for p in root.rglob("*.py")}
        if python_files - set(hashes):
            raise ValueError("provenance hashes must cover every Python source")
        for name, digest in hashes.items():
            if not isinstance(name, str) or "\\" in name:
                raise ValueError("invalid provenance path")
            rel = PurePosixPath(name)
            if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != name:
                raise ValueError("provenance path must be normalized and relative")
            path = root / name
            if not path.resolve().is_relative_to(root) or not path.is_file():
                raise ValueError("provenance file escapes or is missing")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid sha256 digest")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("provenance file exceeds verification limit")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"adapter provenance mismatch: {name}")
    return {"source_url": source, "revision": revision, "sha256": dict(hashes),
            "verified": bool(hashes)}
