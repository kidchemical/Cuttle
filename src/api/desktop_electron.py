"""
Desktop (Electron) client updates built by a source-checkout Host.

A Host running from a source checkout hashes the Electron shell files (not
package.json — electron-builder rewrites that file inside app.asar) and can
pack them into an app.asar so a LAN Client can replace its packaged shell
without a full electron-builder run. The packed artifact is a replaceable
cache in the Cuttle home, never written into the checkout.

A packaged (release-installed) Host has no Electron sources: Host-built
updates are explicitly unavailable there (``updateSource: "release"``), and
its Clients update from the GitHub release like the Host itself.

Clients accept an artifact only over the Host's pinned HTTPS identity
(electron/tls-trust.js); the SHA-256 here is integrity, not authenticity.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, send_file

from core.runtime_paths import runtime_cache_path

# src/api/desktop_electron.py → repo root
_API_DIR = Path(__file__).resolve().parent
_SRC_DIR = _API_DIR.parent
_REPO_ROOT = _SRC_DIR.parent
ELECTRON_DIR = _REPO_ROOT / "electron"
# package.json is omitted: electron-builder rewrites it inside app.asar.
# Keep in sync with electron/pack-desktop-update.js HASH_FILES.
HASH_FILES = (
    "connect.html", "main.js", "preload.js", "tls-trust.js", "external-link-policy.js",
    "device-worker/cuttle_device_worker.py",
)
PACK_SCRIPT = ELECTRON_DIR / "pack-desktop-update.js"
RELEASE_ONLY_REASON = (
    "This Host was installed from a release package and cannot build desktop "
    "updates; update each Client from the GitHub release."
)


def update_dir() -> Path:
    return runtime_cache_path("desktop_update")


def update_asar() -> Path:
    return update_dir() / "app.asar"


def _update_manifest_path() -> Path:
    return update_dir() / "manifest.json"


def has_electron_sources() -> bool:
    """True only on a source checkout (packaged Hosts ship no electron/ tree)."""
    return all((ELECTRON_DIR / name).is_file() for name in ("main.js", "package.json")) and PACK_SCRIPT.is_file()


def desktop_source_hash() -> str:
    h = hashlib.sha256()
    for name in HASH_FILES:
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        path = ELECTRON_DIR / name
        if path.is_file():
            h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:20]


def _read_manifest() -> Optional[Dict[str, Any]]:
    path = _update_manifest_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _find_node() -> Optional[list]:
    node = shutil.which("node")
    if node:
        return [node]
    electron_exe = ELECTRON_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
    if electron_exe.is_file():
        return [str(electron_exe)]
    electron_bin = ELECTRON_DIR / "node_modules" / "electron" / "dist" / "electron"
    if electron_bin.is_file():
        return [str(electron_bin)]
    return None


def ensure_update_asar(current_hash: str) -> Optional[Path]:
    """Pack the cached app.asar if missing or stale. Returns it only when it
    matches ``current_hash`` (a stale artifact is never offered)."""
    if not has_electron_sources():
        return None
    asar = update_asar()
    manifest = _read_manifest()
    if asar.is_file() and manifest and manifest.get("hash") == current_hash:
        return asar
    cmd_base = _find_node()
    if not cmd_base:
        return None
    env = os.environ.copy()
    env["ELECTRON_RUN_AS_NODE"] = "1"
    env["CUTTLE_DESKTOP_UPDATE_DIR"] = str(update_dir())
    try:
        proc = subprocess.run(
            cmd_base + [str(PACK_SCRIPT)],
            cwd=str(ELECTRON_DIR),
            env=env,
            check=False,
            timeout=120,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            print(f"[DESKTOP] pack-desktop-update exit {proc.returncode}: {err[:500]}")
    except Exception as exc:
        print(f"[DESKTOP] pack-desktop-update failed: {exc}")
    manifest = _read_manifest()
    if asar.is_file() and manifest and manifest.get("hash") == current_hash:
        return asar
    return None


_artifact_digest_cache: Dict[tuple, str] = {}


def artifact_sha256(path: Path) -> str:
    """Full SHA-256 of the packed app.asar; clients verify it before swapping."""
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _artifact_digest_cache.get(key)
    if cached:
        return cached
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    _artifact_digest_cache.clear()
    _artifact_digest_cache[key] = h.hexdigest()
    return _artifact_digest_cache[key]


def _package_version() -> str:
    """Semver from electron/package.json (repo source of truth for desktop)."""
    pkg = ELECTRON_DIR / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        ver = str(data.get("version") or "").strip()
        return ver or "0.0.0"
    except Exception:
        return "0.0.0"


def desktop_manifest() -> Dict[str, Any]:
    if not has_electron_sources():
        return {
            "ok": True,
            "service": "cuttle-desktop",
            "updateSource": "release",
            "hash": None,
            "packageVersion": None,
            "artifact": False,
            "artifactSize": None,
            "artifactSha256": None,
            "downloadPath": None,
            "reason": RELEASE_ONLY_REASON,
        }
    source_hash = desktop_source_hash()
    asar_path = ensure_update_asar(source_hash)
    packed = _read_manifest() or {}
    artifact_ok = bool(
        asar_path
        and asar_path.is_file()
        and packed.get("hash") == source_hash
    )
    return {
        "ok": True,
        "service": "cuttle-desktop",
        "updateSource": "host",
        "hash": source_hash,
        # Live electron/package.json wins — packed manifest can lag (or get
        # poisoned if package.json was briefly rewritten during a bad pack).
        "packageVersion": _package_version() or packed.get("packageVersion") or "0.0.0",
        "packedAt": packed.get("packedAt"),
        "artifact": artifact_ok,
        "artifactSize": asar_path.stat().st_size if artifact_ok and asar_path else None,
        "artifactSha256": artifact_sha256(asar_path) if artifact_ok and asar_path else None,
        "downloadPath": "/api/desktop/electron/app.asar" if artifact_ok else None,
    }


def register_desktop_electron_routes(app: Flask) -> None:
    @app.route("/api/desktop/electron", methods=["GET"])
    def api_desktop_electron():
        try:
            return jsonify(desktop_manifest())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/desktop/electron/app.asar", methods=["GET"])
    def api_desktop_electron_asar():
        if not has_electron_sources():
            return jsonify({"ok": False, "error": RELEASE_ONLY_REASON}), 404
        source_hash = desktop_source_hash()
        asar_path = ensure_update_asar(source_hash)
        packed = _read_manifest() or {}
        if not asar_path or not asar_path.is_file() or packed.get("hash") != source_hash:
            return jsonify({
                "ok": False,
                "error": "Desktop update artifact is not ready. Need Node.js on the host to pack app.asar.",
            }), 503
        return send_file(
            asar_path,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name="app.asar",
        )
