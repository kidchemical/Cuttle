"""Host-served git transport for LAN Client updates (Scenario B).

A Host running from a git checkout can serve a self-contained ``git bundle`` of
its current branch so a Client on the same LAN can fast-forward to the Host's
working tree **without pushing to a remote forge**. This is the "HOST" update
channel for source-checkout Clients.

A packaged (release-installed) Host has no git checkout: Host-served updates
are unavailable there (``updateSource: "release"``) and its Clients update from
the GitHub release like the Host itself.

The artifact is offered only over the Host's pinned HTTPS identity
(electron/tls-trust.js); the SHA-256 here is integrity, not authenticity.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, send_file

from core.runtime_paths import runtime_cache_path

_API_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _API_DIR.parent.parent
RELEASE_ONLY_REASON = (
    "This Host was installed from a release package and has no git checkout to "
    "serve; update each Client from the GitHub release."
)


def repo_root() -> Path:
    return _REPO_ROOT


def has_git_sources() -> bool:
    """True only on a git checkout (packaged Hosts ship no ``.git`` tree)."""
    return (repo_root() / ".git").is_dir()


def bundle_dir() -> Path:
    return runtime_cache_path("desktop_git")


def _git(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root()), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode == 0:
            return (completed.stdout or "").strip()
    except Exception:
        pass
    return ""


def current_rev() -> str:
    return _git("rev-parse", "--short=12", "HEAD")


def current_branch() -> str:
    name = _git("rev-parse", "--abbrev-ref", "HEAD")
    return "" if name == "HEAD" else name


def _bundle_path(rev: str) -> Path:
    return bundle_dir() / f"host-{rev or 'head'}.bundle"


_artifact_digest_cache: Dict[tuple, str] = {}


def artifact_sha256(path: Path) -> str:
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


def ensure_bundle() -> Optional[Path]:
    """Pack (and cache) a self-contained bundle of the current branch.

    A self-contained bundle carries the full history reachable from the branch,
    so any Client ancestor can fast-forward from it. Cached by rev; stale
    bundles for other revs are pruned.
    """
    if not has_git_sources():
        return None
    branch = current_branch()
    rev = current_rev()
    if not branch or not rev:
        return None
    dest = _bundle_path(rev)
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.with_name(f".{dest.name}.tmp")
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root()), "bundle", "create", str(staging), branch],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            return None
        staging.replace(dest)
    except Exception:
        try:
            staging.unlink()
        except OSError:
            pass
        return None
    for stale in bundle_dir().glob("host-*.bundle"):
        if stale != dest:
            try:
                stale.unlink()
            except OSError:
                pass
    return dest


def git_manifest() -> Dict[str, Any]:
    if not has_git_sources():
        return {
            "ok": True,
            "service": "cuttle-git",
            "updateSource": "release",
            "rev": None,
            "branch": None,
            "artifact": False,
            "artifactSize": None,
            "artifactSha256": None,
            "downloadPath": None,
            "reason": RELEASE_ONLY_REASON,
        }
    bundle = ensure_bundle()
    ok = bool(bundle and bundle.is_file() and bundle.stat().st_size > 0)
    return {
        "ok": True,
        "service": "cuttle-git",
        "updateSource": "host",
        "rev": current_rev(),
        "branch": current_branch(),
        "artifact": ok,
        "artifactSize": bundle.stat().st_size if ok and bundle else None,
        "artifactSha256": artifact_sha256(bundle) if ok and bundle else None,
        "downloadPath": "/api/desktop/git/bundle" if ok else None,
    }


def register_desktop_git_routes(app: Flask) -> None:
    @app.route("/api/desktop/git", methods=["GET"])
    def api_desktop_git():
        try:
            return jsonify(git_manifest())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/desktop/git/bundle", methods=["GET"])
    def api_desktop_git_bundle():
        if not has_git_sources():
            return jsonify({"ok": False, "error": RELEASE_ONLY_REASON}), 404
        bundle = ensure_bundle()
        if not bundle or not bundle.is_file() or bundle.stat().st_size <= 0:
            return jsonify({"ok": False, "error": "Git bundle is not ready."}), 503
        return send_file(
            bundle,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name="cuttle-host.bundle",
        )
