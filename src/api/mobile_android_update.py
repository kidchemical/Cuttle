"""
Android Capacitor shell updates over LAN (same idea as Electron app.asar).

The host hashes native phone-app sources and, after assembleDebug, publishes
apps/mobile/dist/update/app-debug.apk so a phone can install without USB.

Automatic source builds are an explicit opt-in (CUTTLE_MOBILE_AUTO_REBUILD=1);
otherwise only an APK published by a build (or ``publish --apk``) is served.
No APK is committed to git; each host builds or is given one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request, send_file

from api.mobile_android_artifacts import baked_hash as apk_baked_hash, inspect_apk, sha256_file

_API_DIR = Path(__file__).resolve().parent
_SRC_DIR = _API_DIR.parent
_REPO_ROOT = _SRC_DIR.parent
MOBILE_DIR = _REPO_ROOT / "apps" / "mobile"
ASSETS_JSON = MOBILE_DIR / "android" / "app" / "src" / "main" / "assets" / "cuttle-mobile-build.json"
GRADLE_APK = (
    MOBILE_DIR / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
)
UPDATE_DIR = MOBILE_DIR / "dist" / "update"
UPDATE_APK = UPDATE_DIR / "app-debug.apk"
UPDATE_MANIFEST = UPDATE_DIR / "manifest.json"
GRADLEW_BAT = MOBILE_DIR / "android" / "gradlew.bat"
GRADLEW_SH = MOBILE_DIR / "android" / "gradlew"
BUILD_BAT = MOBILE_DIR / "build-android-debug.bat"

_publication_lock = threading.RLock()
_rebuild_lock = threading.Lock()
_rebuild_thread: Optional[threading.Thread] = None
_rebuild_state: Dict[str, Any] = {
    "building": False,
    "targetHash": None,
    "startedAt": None,
    "finishedAt": None,
    "error": None,
    "exitCode": None,
}


def _rel_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


# Gitignored outputs of `cap sync` (derived from package.json/lock, which are hashed).
# Hashing them would make checkouts that never ran sync disagree with the publisher.
_GENERATED_NATIVE_FILES = frozenset({"res/xml/config.xml"})


def hash_input_files() -> List[Path]:
    """Native + setup-shell files that require a new APK."""
    files: List[Path] = []
    native = MOBILE_DIR / "android" / "app" / "src" / "main"
    if native.is_dir():
        files.extend(
            p for p in sorted(native.rglob("*"))
            if p.is_file()
            and "assets" not in p.relative_to(native).parts
            and p.relative_to(native).as_posix() not in _GENERATED_NATIVE_FILES
        )
    java_root = MOBILE_DIR / "android" / "app" / "src" / "main" / "java"
    if java_root.is_dir():
        files.extend(sorted(java_root.rglob("*.java")))
    extras = [
        MOBILE_DIR / "android" / "app" / "src" / "main" / "AndroidManifest.xml",
        MOBILE_DIR / "android" / "app" / "src" / "main" / "res" / "xml" / "file_paths.xml",
        MOBILE_DIR / "index.html",
        MOBILE_DIR / "src" / "main.js",
        MOBILE_DIR / "src" / "setup.css",
        MOBILE_DIR / "package.json",
        MOBILE_DIR / "package-lock.json",
        MOBILE_DIR / "capacitor.config.ts",
        MOBILE_DIR / "vite.config.js",
        MOBILE_DIR / "android" / "variables.gradle",
        MOBILE_DIR / "android" / "app" / "build.gradle",
        MOBILE_DIR / "android" / "app" / "capacitor.build.gradle",
        MOBILE_DIR / "android" / "build.gradle",
        MOBILE_DIR / "android" / "gradle.properties",

    ]
    for path in extras:
        if path.is_file():
            files.append(path)
    # Stable unique order
    seen = set()
    out: List[Path] = []
    for path in files:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def mobile_source_hash() -> str:
    h = hashlib.sha256()
    for path in hash_input_files():
        rel = _rel_posix(MOBILE_DIR, path)
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        payload = path.read_bytes()
        if path.suffix in (".java", ".kt", ".js", ".ts", ".css", ".html", ".json", ".xml", ".gradle", ".properties"):
            payload = payload.replace(b"\r\n", b"\n")
        h.update(payload)
        h.update(b"\0")
    return h.hexdigest()[:20]


def write_baked_hash() -> str:
    """Write the hash the APK will report (called from Gradle preBuild)."""
    digest = mobile_source_hash()
    ASSETS_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "hash": digest,
        "writtenAt": datetime.now(timezone.utc).isoformat(),
    }
    ASSETS_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return digest


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _read_manifest() -> Optional[Dict[str, Any]]:
    return _read_json(UPDATE_MANIFEST)


def _published_path(manifest: Optional[dict] = None) -> Optional[Path]:
    data = manifest if manifest is not None else (_read_manifest() or {})
    filename = data.get("filename", "")
    checksum = data.get("sha256", "")
    if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        return None
    if filename != f"app-{checksum}.apk":
        return None
    path = UPDATE_DIR / filename
    try:
        if (path.is_file() and path.stat().st_size == data.get("size")
                and apk_baked_hash(path) == data.get("hash")
                and sha256_file(path) == checksum):
            return path
    except OSError:
        pass
    return None


def _published_apk_matches(digest: str) -> bool:
    with _publication_lock:
        data = _read_manifest() or {}
        return data.get("hash") == digest and _published_path(data) is not None


def publish_gradle_apk(source_hash: Optional[str] = None, *, candidate: Optional[Path] = None) -> Optional[Path]:
    """Stage, verify, then atomically publish an immutable APK and its manifest."""
    digest = source_hash or mobile_source_hash()
    source = candidate if candidate is not None else GRADLE_APK
    if apk_baked_hash(source) != digest:
        return _published_path() if _published_apk_matches(digest) else None
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    with _publication_lock:
        fd, name = tempfile.mkstemp(prefix=".apk-stage-", suffix=".apk", dir=UPDATE_DIR)
        staging = Path(name)
        manifest_tmp = None
        try:
            with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            verified = inspect_apk(staging)
            if verified["hash"] != digest:
                raise ValueError("APK build identity changed during publication")
            previous = _read_manifest() or {}
            # Migrate legacy metadata using the actual previously served APK.
            prior_path = _published_path(previous) or (UPDATE_APK if UPDATE_APK.is_file() else None)
            if prior_path:
                prior = inspect_apk(prior_path)
                if prior["signingCertSha256"] != verified["signingCertSha256"]:
                    raise ValueError("APK signing key differs from the published app; preserve its signing key")
                if verified["versionCode"] < prior["versionCode"] or (
                    verified["hash"] != prior["hash"] and verified["versionCode"] <= prior["versionCode"]
                ):
                    raise ValueError("A new app build requires a higher versionCode than the published app")
            filename = f"app-{verified['sha256']}.apk"
            destination = UPDATE_DIR / filename
            if destination.exists():
                if sha256_file(destination) != verified["sha256"]:
                    raise ValueError("Immutable APK has been modified")
            else:
                os.replace(staging, destination)
            manifest = {**verified, "filename": filename,
                        "packedAt": datetime.now(timezone.utc).isoformat()}
            fd, name = tempfile.mkstemp(prefix=".manifest-", dir=UPDATE_DIR)
            manifest_tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(manifest, out, indent=2)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            # Keep the legacy disk alias valid while an older Flask is running.
            fd, name = tempfile.mkstemp(prefix=".legacy-apk-", dir=UPDATE_DIR)
            os.close(fd)
            alias = Path(name)
            try:
                shutil.copyfile(destination, alias)
                os.replace(alias, UPDATE_APK)
            finally:
                alias.unlink(missing_ok=True)
            os.replace(manifest_tmp, UPDATE_MANIFEST)
            _prune_published(keep={filename, previous.get("filename")})
            return destination
        finally:
            staging.unlink(missing_ok=True)
            if manifest_tmp:
                manifest_tmp.unlink(missing_ok=True)


def _prune_published(keep: set) -> None:
    """Keep the current and previous APK (a phone may be mid-download); drop older ones."""
    for path in UPDATE_DIR.glob("app-*.apk"):
        if path.name in keep or not re.fullmatch(r"app-[a-f0-9]{64}\.apk", path.name):
            continue
        try:
            path.unlink()
        except OSError as exc:
            print(f"[MOBILE] Could not remove old APK {path.name}: {exc}")


def can_rebuild() -> bool:
    """True when a local Android debug build can be started on this host."""
    if os.name == "nt":
        if BUILD_BAT.is_file():
            return True
        return GRADLEW_BAT.is_file()
    return GRADLEW_SH.is_file() or GRADLEW_BAT.is_file()


def _auto_rebuild_allowed() -> bool:
    flag = os.environ.get("CUTTLE_MOBILE_AUTO_REBUILD", "0").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return can_rebuild()


def rebuild_status() -> Dict[str, Any]:
    with _rebuild_lock:
        alive = bool(_rebuild_thread and _rebuild_thread.is_alive())
        building = bool(_rebuild_state.get("building") and alive)
        return {
            "building": building,
            "targetHash": _rebuild_state.get("targetHash"),
            "startedAt": _rebuild_state.get("startedAt"),
            "finishedAt": _rebuild_state.get("finishedAt"),
            "error": _rebuild_state.get("error"),
            "exitCode": _rebuild_state.get("exitCode"),
        }


def _rebuild_command() -> List[str]:
    if os.name == "nt" and BUILD_BAT.is_file():
        return ["cmd", "/c", str(BUILD_BAT)]
    if os.name == "nt" and GRADLEW_BAT.is_file():
        return ["cmd", "/c", str(GRADLEW_BAT), "assembleDebug"]
    if GRADLEW_SH.is_file():
        return ["bash", str(GRADLEW_SH), "assembleDebug"]
    raise FileNotFoundError("No Android Gradle wrapper / build-android-debug.bat found")


def _run_rebuild(target_hash: str) -> None:
    started = datetime.now(timezone.utc).isoformat()
    with _rebuild_lock:
        _rebuild_state.update({
            "building": True,
            "targetHash": target_hash,
            "startedAt": started,
            "finishedAt": None,
            "error": None,
            "exitCode": None,
        })
    print(f"[MOBILE] APK rebuild started for hash {target_hash}")
    exit_code = 1
    err_text = None
    try:
        cmd = _rebuild_command()
        if os.name == "nt" and BUILD_BAT.is_file():
            cwd = str(MOBILE_DIR)
        else:
            cwd = str(MOBILE_DIR / "android")
        env = os.environ.copy()
        # Prefer Android Studio JBR when present (same as build-android-debug.bat).
        studio_jbr = Path(r"C:\Program Files\Android\Android Studio\jbr")
        if studio_jbr.is_dir() and (studio_jbr / "bin" / "java.exe").is_file():
            env.setdefault("JAVA_HOME", str(studio_jbr))
        sync = subprocess.run(
            ["npm.cmd" if os.name == "nt" else "npm", "run", "sync:android"],
            cwd=str(MOBILE_DIR), env=env, check=False, capture_output=True,
            text=True, timeout=300,
        )
        if sync.returncode != 0:
            raise RuntimeError("Android asset sync failed; install mobile build dependencies with npm ci. "
                               + (sync.stderr or sync.stdout)[-500:])
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=60 * 25,
        )
        exit_code = int(proc.returncode)
        if exit_code != 0:
            err_text = (proc.stderr or proc.stdout or f"exit {exit_code}").strip()[:800]
            print(f"[MOBILE] APK rebuild failed ({exit_code}): {err_text}")
        else:
            # publish task should run from Gradle doLast; ensure copy anyway
            if publish_gradle_apk(target_hash) is None:
                raise RuntimeError("Built APK does not contain the requested source hash")
            print(f"[MOBILE] APK rebuild finished for hash {target_hash}")
    except Exception as exc:
        err_text = str(exc)
        print(f"[MOBILE] APK rebuild error: {exc}")
        exit_code = 1
    finally:
        with _rebuild_lock:
            _rebuild_state.update({
                "building": False,
                "finishedAt": datetime.now(timezone.utc).isoformat(),
                "error": err_text,
                "exitCode": exit_code,
            })


def kick_apk_rebuild(target_hash: Optional[str] = None, *, force: bool = False) -> Dict[str, Any]:
    """Start a background assembleDebug when the published APK is missing/stale."""
    digest = target_hash or mobile_source_hash()
    if not _auto_rebuild_allowed():
        return {"started": False, "reason": "auto_rebuild_disabled", "building": False}

    with _rebuild_lock:
        global _rebuild_thread
        if (
            not force
            and _published_apk_matches(digest)
        ):
            return {"started": False, "reason": "already_current", "building": False}

        if _rebuild_thread is not None and _rebuild_thread.is_alive():
            same = _rebuild_state.get("targetHash") == digest
            return {
                "started": False,
                "reason": "already_building",
                "building": True,
                "targetHash": _rebuild_state.get("targetHash"),
                "sameHash": same,
            }

        thread = threading.Thread(
            target=_run_rebuild,
            args=(digest,),
            daemon=True,
            name="cuttle-apk-rebuild",
        )
        _rebuild_thread = thread
        _rebuild_state.update({
            "building": True,
            "targetHash": digest,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "finishedAt": None,
            "error": None,
            "exitCode": None,
        })
        thread.start()
        return {"started": True, "reason": "started", "building": True, "targetHash": digest}


def ensure_update_apk(current_hash: str) -> Optional[Path]:
    if _published_apk_matches(current_hash):
        return _published_path()
    if not _auto_rebuild_allowed():
        return None
    try:
        published = publish_gradle_apk(current_hash)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        with _rebuild_lock:
            _rebuild_state["error"] = "APK publication failed: " + str(exc)
        return None
    if published and _published_apk_matches(current_hash):
        return published
    # Stale / missing — kick Gradle so the next phone check can download.
    kick_apk_rebuild(current_hash)
    return _published_path() if _published_apk_matches(current_hash) else None


def android_manifest() -> Dict[str, Any]:
    # Keep the artifact path and all manifest fields from one publication.
    with _publication_lock:
        return _android_manifest()


def _android_manifest() -> Dict[str, Any]:
    source_hash = mobile_source_hash()
    apk_path = ensure_update_apk(source_hash)
    packed = _read_manifest() or {}
    artifact_ok = bool(
        apk_path
        and apk_path.is_file()
        and _published_apk_matches(source_hash)
    )
    status = rebuild_status()
    building = bool(status.get("building")) and not artifact_ok
    out: Dict[str, Any] = {
        "ok": True,
        "service": "cuttle-mobile-android",
        "hash": source_hash,
        "packageVersion": packed.get("packageVersion") or "1.0",
        "packedAt": packed.get("packedAt"),
        "artifact": artifact_ok,
        "artifactSize": apk_path.stat().st_size if artifact_ok and apk_path else None,
        "downloadPath": (f"/api/mobile/android/app-debug.apk?sha256={packed['sha256']}"
                         if artifact_ok else None),
        "sha256": packed.get("sha256") if artifact_ok else None,
        "packageName": packed.get("packageName") if artifact_ok else None,
        "versionCode": packed.get("versionCode") if artifact_ok else None,
        "signingCertSha256": packed.get("signingCertSha256") if artifact_ok else None,
        "channel": packed.get("channel") if artifact_ok else None,
        "autoRebuild": _auto_rebuild_allowed(),
        "platform": "android",
        "building": building,
    }
    if building:
        out["rebuildTargetHash"] = status.get("targetHash")
        out["rebuildStartedAt"] = status.get("startedAt")
    elif status.get("error") and not artifact_ok:
        out["rebuildError"] = status.get("error")
    return out


def register_mobile_android_update_routes(app: Flask) -> None:
    @app.route("/api/mobile/android", methods=["GET"])
    def api_mobile_android():
        try:
            return jsonify(android_manifest())
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/mobile/android/app-debug.apk", methods=["GET"])
    def api_mobile_android_apk():
        checksum = request.args.get("sha256")
        if checksum is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", checksum):
                return jsonify({"ok": False, "error": "Invalid APK checksum"}), 400
            apk_path = UPDATE_DIR / f"app-{checksum}.apk"
            if not apk_path.is_file() or sha256_file(apk_path) != checksum:
                return jsonify({"ok": False, "error": "Requested APK unavailable; check for updates again"}), 409
            return send_file(apk_path, mimetype="application/vnd.android.package-archive",
                             as_attachment=True, download_name="cuttle-mobile.apk", max_age=0)
        source_hash = mobile_source_hash()
        apk_path = ensure_update_apk(source_hash)
        if not apk_path or not _published_apk_matches(source_hash):
            status = rebuild_status()
            msg = "Android update APK is not ready."
            if status.get("building"):
                msg = "Android update APK is rebuilding on the PC. Try again in a minute."
            elif status.get("error"):
                msg = f"Android update APK rebuild failed: {status.get('error')}"
            else:
                msg = "Android update APK is not ready. Publish a matching signed APK on the PC."
            return jsonify({"ok": False, "error": msg, "building": bool(status.get("building"))}), 503
        return send_file(
            apk_path,
            mimetype="application/vnd.android.package-archive",
            as_attachment=True,
            download_name="app-debug.apk",
        )

    # On Flask import: if the phone-app sources moved ahead of dist/update, start Gradle.
    try:
        digest = mobile_source_hash()
        if not _published_apk_matches(digest):
            kick_apk_rebuild(digest)
    except Exception as exc:
        print(f"[MOBILE] Startup APK rebuild check failed: {exc}")


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "write-hash"
    if cmd == "write-hash":
        print(write_baked_hash())
        return 0
    if cmd == "publish":
        candidate = None
        if len(argv) > 2:
            if len(argv) != 4 or argv[2] != "--apk":
                print("usage: publish [--apk PATH]", file=sys.stderr)
                return 2
            candidate = Path(argv[3])
        try:
            path = publish_gradle_apk(candidate=candidate)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            print(f"APK publication refused: {exc}", file=sys.stderr)
            return 1
        print(str(path) if path else "")
        return 0 if path else 1
    if cmd == "rebuild":
        force = "--force" in argv[2:]
        info = kick_apk_rebuild(force=force)
        print(json.dumps(info))
        if not info.get("started") and info.get("reason") == "already_current":
            return 0
        if not info.get("started") and info.get("reason") == "auto_rebuild_disabled":
            return 1
        # Wait for background thread when invoked from CLI.
        deadline = time.time() + 60 * 25
        while time.time() < deadline:
            st = rebuild_status()
            if not st.get("building"):
                print(json.dumps(st))
                ok = _published_apk_matches(mobile_source_hash())
                return 0 if ok else 1
            time.sleep(2)
        print(json.dumps({"error": "rebuild timed out", **rebuild_status()}))
        return 1
    if cmd == "status":
        print(json.dumps({"hash": mobile_source_hash(), "manifest": _read_manifest(), **rebuild_status()}))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
