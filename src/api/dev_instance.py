"""Ephemeral shadow-app orchestration (parent side only).

Spawns an isolated child running the REAL production Flask app from a
fingerprinted source snapshot. This module never imports the Flask monolith
(architecture boundary: owned layers must not import ``web_chat_api``) — it
only snapshots files, builds an allowlist child environment, and manages an
owned ``Popen`` handle. No daemon, no 8080/8000 bind, no live stores.

Usage (development-only command)::

    python -m api.dev_instance prepare [--candidate DIR]
    python -m api.dev_instance up [--candidate DIR] [--port 0] [--scenario success]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Ports the shadow must never bind even when explicitly requested: the live
# Flask listener, the loopback main path, and the LAN portal.
RESERVED_PORTS = frozenset({8080, 8000, 8888})

# Snapshot deny rules: credentials, private/local state, and heavy ignored
# trees never enter the candidate snapshot.
_SNAPSHOT_DENY_NAMES = frozenset({
    ".env", "settings.json", "GLOBAL.ini", "bot_config.json", "runtime_config.json",
    "machine_settings.json", "ui_state.json",
})
_SNAPSHOT_DENY_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12")
_SNAPSHOT_DENY_DIRNAMES = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules", "temp", ".mypy_cache",
})

# Guard flags the child must carry (every one verified as actually read):
#   CUTTLE_TEST_MODE=1, CUTTLE_MOBILE_AUTO_REBUILD=0 (mobile_android_update),
#   CUTTLE_JEV_WATCH=0 (jev/watch), CUTTLE_AGENT_STEER=0 (harness/steer),
#   CUTTLE_DEVICE_WORKERS_ENABLED=0 (device_workers/config),
#   CUTTLE_LAN_ACCESS=0 (lan_access), CHAT_TITLE_DISABLED=1 (chat_titler).
GUARD_FLAGS = {
    "CUTTLE_TEST_MODE": "1",
    "CUTTLE_MOBILE_AUTO_REBUILD": "0",
    "CUTTLE_JEV_WATCH": "0",
    "CUTTLE_AGENT_STEER": "0",
    "CUTTLE_DEVICE_WORKERS_ENABLED": "0",
    "CUTTLE_LAN_ACCESS": "0",
    "CHAT_TITLE_DISABLED": "1",
}

BOOTSTRAP_REL = Path("src/scripts/cuttle_shadow_app.py")


class ShadowError(RuntimeError):
    """Fatal shadow setup/launch error. Never partially booted."""


def validate_port(value: int) -> int:
    """Validate an explicit dev port. 0 (default) means atomically allocated."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ShadowError(f"invalid port: {value!r}")
    if port == 0:
        return 0
    if port in RESERVED_PORTS:
        raise ShadowError(f"port {port} is a live-instance port; refused")
    if not 1024 <= port <= 65535:
        raise ShadowError(f"port {port} out of range 1024-65535 (or 0)")
    return port


def _valid_shadow_id(shadow_id: str) -> str:
    """Reject absolute paths, separators, and ancestor escapes in ids."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", shadow_id or ""):
        raise ShadowError(f"invalid shadow id: {shadow_id!r}")
    return shadow_id


def _check_ancestors(candidate: Path, root: Path) -> None:
    """Every ancestor from candidate down to root must exist symlink-free."""
    resolved_candidate = candidate.resolve()
    # Walk existing ancestors; none may be (or contain) a symlink hop.
    probe = root.absolute()
    while True:
        if os.path.islink(probe):
            raise ShadowError(f"symlink ancestor refused: {probe}")
        if probe == resolved_candidate or probe == probe.parent:
            break
        probe = probe.parent


def _member_ancestors_ok(candidate: Path, rel: str) -> bool:
    """Every INTERNAL ancestor between candidate and the member must not be
    a symlink (catches src/link/file -> src/actual/file style escapes)."""
    node = (candidate / rel).absolute().parent
    stop = candidate.resolve()
    while True:
        if os.path.islink(node):
            return False
        if node == stop or node == node.parent:
            break
        node = node.parent
    return True


def _is_safe_member(candidate: Path, rel: str) -> bool:
    """Reject absolute paths, ancestor escapes, and symlink escapes."""
    if os.path.isabs(rel) or ".." in Path(rel).parts:
        return False
    if "personal" in Path(rel).parts:
        return False  # personal overlays never enter, at any depth
    target = (candidate / rel).resolve()
    try:
        target.relative_to(candidate.resolve())
    except ValueError:
        return False
    full = candidate / rel
    if full.is_symlink() or target.is_symlink():
        return False
    return True


def _denied(rel: str) -> bool:
    parts = Path(rel).parts
    if parts and parts[0] in _SNAPSHOT_DENY_DIRNAMES:
        return True
    if "personal" in parts:
        return True  # personal overlays excluded at any depth
    name = Path(rel).name
    if name in _SNAPSHOT_DENY_NAMES:
        return True
    if name.endswith(_SNAPSHOT_DENY_SUFFIXES):
        return True
    return False


def iter_snapshot_files(candidate: Path) -> list[str]:
    """Tracked-file inventory via read-only ``git ls-files`` plus the new
    bootstrap script (untracked until integrated). No git mutations."""
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(candidate),
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise ShadowError(f"git ls-files failed: {proc.stderr.decode()[:200]}")
    files = [p for p in proc.stdout.decode("utf-8").split("\0") if p]
    for extra in (BOOTSTRAP_REL, Path("src/api/dev_instance.py")):
        rel = extra.as_posix()
        if rel not in files and (candidate / rel).is_file():
            files.append(rel)
    # Unsafe tracked paths fail loudly — never silently omitted.
    unsafe = [f for f in files
              if not _denied(f) and not (
                  _is_safe_member(candidate, f)
                  and _member_ancestors_ok(candidate, f))]
    if unsafe:
        raise ShadowError(f"unsafe tracked paths refused: {unsafe[:5]}")
    return [f for f in files if not _denied(f)]


def fingerprint_files(candidate: Path, rels: list[str]) -> str:
    """sha256 over sorted (relpath, bytes) of candidate WORKING bytes."""
    digest = hashlib.sha256()
    for rel in sorted(rels):
        data = (candidate / rel).read_bytes()
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(data)
    return digest.hexdigest()


def prepare_snapshot(candidate: Path, shadow_id: str | None = None) -> dict:
    """Freeze working bytes into ``candidate/temp/shadows/<uuid>/app``.

    Refuses a reused non-empty root. Returns the manifest seed (no launch).
    """
    candidate = candidate.resolve()
    shadow_id = _valid_shadow_id(shadow_id or uuid.uuid4().hex)
    root = candidate / "temp" / "shadows" / shadow_id
    _check_ancestors(candidate, root.parent)
    if root.exists() and any(root.iterdir()):
        raise ShadowError(f"refusing reused non-empty shadow root: {root}")
    app_dir = root / "app"
    data_dir = root / "data"
    app_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    _check_ancestors(candidate, root)

    rels = iter_snapshot_files(candidate)
    copied = []
    for rel in rels:
        src = candidate / rel
        if not src.is_file():
            continue  # submodule gitlinks, deleted files: never enter
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied.append(rel)
    codehash = fingerprint_files(app_dir, copied)
    # Anchor hashes of key production modules (candidate working bytes) so
    # the child proves it runs the candidate, not merely echoing codehash.
    anchor_hashes = {}
    for anchor in ("src/api/web_chat_api.py", "src/api/chat_turn_workflow.py",
                   "src/api/chat_turn_persist.py", "src/api/chat_coordinator.py"):
        anchor_path = candidate / anchor
        if anchor_path.is_file():
            anchor_hashes[anchor] = hashlib.sha256(
                anchor_path.read_bytes()).hexdigest()

    fixture_project = data_dir / "fixture_project"
    fixture_project.mkdir(exist_ok=True)
    (fixture_project / "shadow-fixture.txt").write_text(
        "dev-shadow fixture project (never a live project)\n", encoding="utf-8"
    )
    # Private vendor-home overrides (empty config fallback; no live files).
    # HERMES_HOME is actually consumed (hermes_cli_tool); MUSE_MODEL is read
    # by the resolver before native home settings. Never HOME/CODEX_HOME.
    vendor_hermes = data_dir / "vendor" / "hermes"
    vendor_hermes.mkdir(parents=True, exist_ok=True)
    return {
        "shadow_id": shadow_id,
        "root": str(root),
        "app_dir": str(app_dir),
        "data_dir": str(data_dir),
        "manifest": str(root / "manifest.json"),
        "log": str(root / "child.log"),
        "files": len(copied),
        "copied": sorted(copied),
        "codehash": codehash,
        "anchor_hashes": anchor_hashes,
        "fixture_project": str(fixture_project),
    }


def refresh_seed_hashes(seed: dict) -> dict:
    """Recompute codehash + anchors from the ACTUAL private app tree.

    Source inventory only, before boot: walks the retained copied-file list
    (never a fresh rglob that could pick up generated runtime files),
    raising on symlinks or unsafe members. For tests that modify snapshot production files
    (never the candidate): rewrites nothing outside the seed, copies no
    secrets, and returns the updated seed after verifying the fingerprint.
    """
    app_dir = Path(seed["app_dir"])
    rels = []
    for r in seed.get("copied", []):
        node = app_dir / r
        if node.is_symlink() or not _member_ancestors_ok(app_dir, r):
            raise ShadowError(f"unsafe snapshot member on refresh: {r}")
        if node.is_file():
            rels.append(r)
    seed["codehash"] = fingerprint_files(app_dir, rels)
    seed["files"] = len(rels)
    anchors = {}
    for anchor in seed.get("anchor_hashes", {}):
        anchor_path = app_dir / anchor
        if anchor_path.is_file() and not anchor_path.is_symlink():
            anchors[anchor] = hashlib.sha256(
                anchor_path.read_bytes()).hexdigest()
    seed["anchor_hashes"] = anchors
    assert seed["codehash"] == fingerprint_files(app_dir, rels)
    return seed


def build_child_env(seed: dict, scenario: str, port: int) -> dict[str, str]:
    """Allowlist child environment. Never ``os.environ.copy()``."""
    if sys.platform == "win32":
        path = os.pathsep.join([
            os.path.expandvars(r"%SystemRoot%\System32"),
            os.path.expandvars(r"%SystemRoot%"),
        ])
        env: dict[str, str] = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    else:
        path = os.pathsep.join(["/usr/bin", "/bin"])
        env = {}
    env.update({
        "PATH": path,
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "UTC",
    })
    env.update(GUARD_FLAGS)
    env.update({
        # Deterministic child-only badge default: the production resolver
        # reads MUSE_MODEL before native home settings (muse_cli_tool).
        "MUSE_MODEL": "muse-spark-1.3",
        # Actually consumed override (hermes_cli_tool); private empty dir.
        "HERMES_HOME": str(Path(seed["data_dir"]) / "vendor" / "hermes"),
    })
    data = seed["data_dir"]
    env.update({
        "CUTTLE_SHADOW_NONCE": uuid.uuid4().hex,
        "CUTTLE_SHADOW_SCENARIO": scenario,
        # The name production actually reads (project_actions HMAC).
        "CUTTLE_ACTION_HMAC_SECRET": secrets.token_hex(32),
        "CUTTLE_SHADOW_SNAPSHOT": seed["app_dir"],
        "CUTTLE_SHADOW_DATA": data,
        "CUTTLE_SHADOW_MANIFEST": seed["manifest"],
        "CUTTLE_SHADOW_CODEHASH": seed["codehash"],
        "CUTTLE_SHADOW_PORT": str(port),
        "CUTTLE_SHADOW_FIXTURE_PROJECT": seed["fixture_project"],
        "TMPDIR": data,
        "TEMP": data,
        "TMP": data,
        "CUTTLE_SHADOW_ANCHORS": json.dumps(seed.get("anchor_hashes", {})),
        "CUTTLE_SHADOW_LOG": seed["log"],
    })
    return env


class ShadowChild:
    """Owned child handle. Teardown touches only this Popen object."""

    def __init__(self, proc: subprocess.Popen, seed: dict, nonce: str):
        self.proc = proc
        self.seed = seed
        self.nonce = nonce

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    def read_manifest(self) -> dict | None:
        try:
            return json.loads(Path(self.seed["manifest"]).read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def wait_ready(self, timeout: float = 60.0) -> dict:
        """Private nonce handshake over HTTP: GET the manifest port's
        ``/__shadow/ready`` with the nonce header and validate nonce,
        codehash, and origin PID. Never TCP-alone, never /api/health."""
        import urllib.request
        deadline = time.time() + timeout
        manifest = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise ShadowError(
                    f"shadow child exited during boot (code {self.proc.returncode})"
                )
            manifest = self.read_manifest()
            if manifest and manifest.get("state") == "ready":
                break
            time.sleep(0.2)
        else:
            raise ShadowError("shadow readiness timeout (no manifest)")
        if manifest.get("nonce") != self.nonce:
            raise ShadowError("manifest nonce mismatch: not our child")
        if manifest.get("codehash") != self.seed["codehash"]:
            raise ShadowError("manifest codehash mismatch")
        if manifest.get("pid") != self.proc.pid:
            raise ShadowError("manifest pid mismatch")
        port = int(manifest["port"])
        anchors = manifest.get("anchors") or {}
        expected_anchors = self.seed.get("anchor_hashes", {})
        if set(anchors) != set(expected_anchors):
            raise ShadowError("manifest anchor set mismatch")
        snapshot_src = Path(self.seed["app_dir"]) / "src"
        for rel, proof in anchors.items():
            if proof.get("sha") != expected_anchors[rel]:
                raise ShadowError(f"manifest anchor hash mismatch: {rel}")
            try:
                Path(proof.get("path", "")).resolve().relative_to(
                    snapshot_src.resolve())
            except ValueError:
                raise ShadowError(f"manifest anchor path escape: {rel}")
        import urllib.error
        # No proxy: inherited proxy env must not reroute loopback.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/__shadow/ready",
            headers={"X-Shadow-Nonce": self.nonce},
        )
        body = None
        deadline_h = time.time() + min(15.0, timeout)
        while time.time() < deadline_h:
            try:
                with opener.open(req, timeout=5) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                raise ShadowError(f"ready handshake HTTP {exc.code}")
            except OSError:
                time.sleep(0.2)  # connection-not-yet-served only
        if body is None:
            raise ShadowError("ready handshake timeout")
        if body.get("nonce") != self.nonce:
            raise ShadowError("ready-handshake nonce mismatch")
        if body.get("codehash") != self.seed["codehash"]:
            raise ShadowError("ready-handshake codehash mismatch")
        if body.get("pid") != self.proc.pid:
            raise ShadowError("ready-handshake pid mismatch")
        manifest["handshake"] = "nonce-http-ok"
        return manifest

    def stop(self, timeout: float = 10.0) -> None:
        """Terminate ONLY the owned handle. No pgrep/pkill/signals to live."""
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout)


def launch(seed: dict, scenario: str, port: int = 0,
           timeout: float = 60.0) -> tuple[ShadowChild, dict]:
    """Snapshot must already exist (see prepare_snapshot). Returns child +
    validated manifest. Foreground context managers should call child.stop().
    """
    port = validate_port(port)
    env = build_child_env(seed, scenario, port)
    bootstrap = Path(seed["app_dir"]) / BOOTSTRAP_REL
    log = open(seed["log"], "wb")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(bootstrap)],
            cwd=str(Path(seed["app_dir"]) / "src"),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    finally:
        log.close()  # child holds its own duplicated fd; close the parent's
    child = ShadowChild(proc, seed, env["CUTTLE_SHADOW_NONCE"])
    try:
        manifest = child.wait_ready(timeout=timeout)
    except Exception:
        child.stop()
        raise
    return child, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.dev_instance",
        description="Ephemeral shadow-app orchestration (agent-ops only).",
    )
    # --candidate lives on each subcommand (a global must precede the
    # command word; per-command placement works in either position).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--candidate", default=".",
                        help="candidate checkout root (default: cwd)")
    common.add_argument("--shadow-id", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", parents=[common],
                   help="freeze snapshot + fingerprint only")
    up = sub.add_parser("up", parents=[common],
                        help="prepare, launch, wait for ready manifest")
    # default None distinguishes explicit CLI 0 (wins over env) from absent.
    up.add_argument("--port", type=int, default=None,
                    help="fixed dev port, or 0 for atomic allocation. "
                         "Precedence: CLI (even 0) > CUTTLE_SHADOW_PORT env > 0. "
                         "Live ports refused.")
    up.add_argument("--scenario", default="blocked",
                    help="child executor scenario (default: blocked guard)")
    up.add_argument("--timeout", type=float, default=60.0)
    return parser


def resolve_port(cli_port: int | None) -> int:
    """CLI (even explicit 0) beats env; env beats the 0 default."""
    if cli_port is not None:
        return validate_port(cli_port)
    raw = (os.environ.get("CUTTLE_SHADOW_PORT", "") or "").strip()
    if raw:
        return validate_port(int(raw))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidate = Path(args.candidate).resolve()
    if args.command == "prepare":
        seed = prepare_snapshot(candidate, args.shadow_id)
        print(json.dumps({k: seed[k] for k in
                          ("shadow_id", "root", "files", "codehash")}, indent=2))
        return 0
    port = resolve_port(args.port)
    seed = prepare_snapshot(candidate, args.shadow_id)
    child, manifest = launch(seed, args.scenario, port, args.timeout)
    print(json.dumps(manifest, indent=2))
    print(f"shadow live pid={child.pid}; stop via handle teardown", flush=True)
    try:
        child.proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        child.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
