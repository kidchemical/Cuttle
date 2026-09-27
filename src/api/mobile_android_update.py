"""
Android Capacitor shell updates over LAN (same idea as Electron app.asar).

The host hashes native phone-app sources and, after assembleDebug, publishes
apps/mobile/dist/update/app-debug.apk so a phone can install without USB.

When the source hash drifts ahead of the published APK, a background
``assembleDebug`` is kicked automatically (mirrors desktop ``ensure_update_asar``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, send_file

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


def hash_input_files() -> List[Path]:
    """Native + setup-shell files that require a new APK."""
    files: List[Path] = []
    java_root = MOBILE_DIR / "android" / "app" / "src" / "main" / "java"
    if java_root.is_dir():
        files.extend(sorted(java_root.rglob("*.java")))
    extras = [
        MOBILE_DIR / "android" / "app" / "src" / "main" / "AndroidManifest.xml",
        MOBILE_DIR / "android" / "app" / "src" / "main" / "res" / "xml" / "file_paths.xml",
        MOBILE_DIR / "index.html",
        MOBILE_DIR / "src" / "main.js",
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
        h.update(path.read_bytes())
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


def publish_gradle_apk(source_hash: Optional[str] = None) -> Optional[Path]:
    """Copy assembleDebug output into dist/update when it matches current sources."""
    digest = source_hash or mobile_source_hash()
    if not GRADLE_APK.is_file():
        return UPDATE_APK if UPDATE_APK.is_file() else None
    baked = (_read_json(ASSETS_JSON) or {}).get("hash")
    if baked and baked != digest:
        return UPDATE_APK if UPDATE_APK.is_file() else None
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GRADLE_APK, UPDATE_APK)
    manifest = {
        "hash": digest,
        "packedAt": datetime.now(timezone.utc).isoformat(),
        "size": UPDATE_APK.stat().st_size,
        "filename": "app-debug.apk",
        "packageVersion": "1.0",
    }
    UPDATE_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return UPDATE_APK


def can_rebuild() -> bool:
    """True when a local Android debug build can be started on this host."""
    if os.name == "nt":
        if BUILD_BAT.is_file():
            return True
        return GRADLEW_BAT.is_file()
    return GRADLEW_SH.is_file() or GRADLEW_BAT.is_file()


def _auto_rebuild_allowed() -> bool:
    flag = os.environ.get("CUTTLE_MOBILE_AUTO_REBUILD", "1").strip().lower()
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
            write_baked_hash()
            publish_gradle_apk(target_hash)
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
        packed = _read_manifest() or {}
        if (
            not force
            and UPDATE_APK.is_file()
            and packed.get("hash") == digest
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
    manifest = _read_manifest()
    if UPDATE_APK.is_file() and manifest and manifest.get("hash") == current_hash:
        return UPDATE_APK
    published = publish_gradle_apk(current_hash)
    manifest = _read_manifest()
    if published and published.is_file() and manifest and manifest.get("hash") == current_hash:
        return published
    # Stale / missing — kick Gradle so the next phone check can download.
    kick_apk_rebuild(current_hash)
    return UPDATE_APK if (
        UPDATE_APK.is_file() and (_read_manifest() or {}).get("hash") == current_hash
    ) else None


def android_manifest() -> Dict[str, Any]:
    source_hash = mobile_source_hash()
    apk_path = ensure_update_apk(source_hash)
    packed = _read_manifest() or {}
    artifact_ok = bool(
        apk_path
        and apk_path.is_file()
        and packed.get("hash") == source_hash
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
        "downloadPath": "/api/mobile/android/app-debug.apk" if artifact_ok else None,
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
        source_hash = mobile_source_hash()
        apk_path = ensure_update_apk(source_hash)
        packed = _read_manifest() or {}
        if not apk_path or not apk_path.is_file() or packed.get("hash") != source_hash:
            status = rebuild_status()
            msg = "Android update APK is not ready."
            if status.get("building"):
                msg = "Android update APK is rebuilding on the PC. Try again in a minute."
            elif status.get("error"):
                msg = f"Android update APK rebuild failed: {status.get('error')}"
            else:
                msg = "Android update APK is not ready. Build the debug APK on the PC first."
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
        packed = _read_manifest() or {}
        if not (UPDATE_APK.is_file() and packed.get("hash") == digest):
            kick_apk_rebuild(digest)
    except Exception as exc:
        print(f"[MOBILE] Startup APK rebuild check failed: {exc}")


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "write-hash"
    if cmd == "write-hash":
        print(write_baked_hash())
        return 0
    if cmd == "publish":
        write_baked_hash()
        path = publish_gradle_apk()
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
                packed = _read_manifest() or {}
                ok = UPDATE_APK.is_file() and packed.get("hash") == mobile_source_hash()
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
