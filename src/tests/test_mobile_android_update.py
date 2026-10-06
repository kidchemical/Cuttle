"""LAN Android APK updates for the Capacitor shell."""

from __future__ import annotations

from pathlib import Path
import json
import zipfile

import pytest

from api import mobile_android_update as apk

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolated_update_artifacts(monkeypatch, tmp_path):
    """API tests must never publish or replace the user's real LAN update APK."""
    monkeypatch.setattr(apk, "GRADLE_APK", tmp_path / "build" / "app-debug.apk")
    monkeypatch.setattr(apk, "ASSETS_JSON", tmp_path / "assets" / "cuttle-mobile-build.json")
    monkeypatch.setattr(apk, "UPDATE_DIR", tmp_path / "update")
    monkeypatch.setattr(apk, "UPDATE_APK", tmp_path / "update" / "app-debug.apk")
    monkeypatch.setattr(apk, "UPDATE_MANIFEST", tmp_path / "update" / "manifest.json")
    def inspect(path):
        with zipfile.ZipFile(path) as archive:
            meta = json.loads(archive.read("assets/cuttle-mobile-build.json"))
        return dict(hash=meta["hash"], sha256=apk.sha256_file(path), size=path.stat().st_size,
                    packageName="com.cuttle.mobile", packageVersion="1.0", channel="debug",
                    versionCode=meta.get("versionCode", 1), signingCertSha256=meta.get("signer", "c" * 64))
    monkeypatch.setattr(apk, "inspect_apk", inspect)


def write_test_apk(path, digest, version=1, signer="c" * 64):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("assets/cuttle-mobile-build.json", json.dumps({"hash": digest, "versionCode": version, "signer": signer}))


def test_publish_rejects_stale_apk_despite_fresh_loose_assets():
    write_test_apk(apk.GRADLE_APK, "aaaaaaaaaaaaaaaaaaaa")
    apk.ASSETS_JSON.parent.mkdir(parents=True)
    apk.ASSETS_JSON.write_text(json.dumps({"hash": "bbbbbbbbbbbbbbbbbbbb"}))
    assert apk.publish_gradle_apk("bbbbbbbbbbbbbbbbbbbb") is None
    assert not apk.UPDATE_APK.exists()
    assert not apk.UPDATE_MANIFEST.exists()


def test_publish_uses_hash_inside_matching_apk():
    write_test_apk(apk.GRADLE_APK, "bbbbbbbbbbbbbbbbbbbb")
    assert apk.publish_gradle_apk("bbbbbbbbbbbbbbbbbbbb").is_file()
    assert apk.apk_baked_hash(apk.UPDATE_APK) == "bbbbbbbbbbbbbbbbbbbb"
    assert json.loads(apk.UPDATE_MANIFEST.read_text())["hash"] == "bbbbbbbbbbbbbbbbbbbb"


def test_false_current_manifest_does_not_hide_stale_apk(monkeypatch):
    write_test_apk(apk.UPDATE_APK, "aaaaaaaaaaaaaaaaaaaa")
    apk.UPDATE_MANIFEST.write_text(json.dumps({"hash": "bbbbbbbbbbbbbbbbbbbb"}))
    monkeypatch.setattr(apk, "_auto_rebuild_allowed", lambda: True)
    calls = []
    monkeypatch.setattr(apk, "kick_apk_rebuild", lambda digest: calls.append(digest))
    assert apk.ensure_update_apk("bbbbbbbbbbbbbbbbbbbb") is None
    assert calls == ["bbbbbbbbbbbbbbbbbbbb"]


def test_publish_cli_does_not_rewrite_build_identity(monkeypatch):
    write_test_apk(apk.GRADLE_APK, "aaaaaaaaaaaaaaaaaaaa")
    apk.ASSETS_JSON.parent.mkdir(parents=True)
    apk.ASSETS_JSON.write_text(json.dumps({"hash": "aaaaaaaaaaaaaaaaaaaa"}))
    monkeypatch.setattr(apk, "mobile_source_hash", lambda: "bbbbbbbbbbbbbbbbbbbb")
    assert apk.main(["mobile_android_update", "publish"]) == 1
    assert json.loads(apk.ASSETS_JSON.read_text())["hash"] == "aaaaaaaaaaaaaaaaaaaa"


def test_unreadable_apk_identity_fails_closed():
    apk.GRADLE_APK.parent.mkdir(parents=True)
    apk.GRADLE_APK.write_bytes(b"not an APK")
    assert apk.publish_gradle_apk("bbbbbbbbbbbbbbbbbbbb") is None


def test_mobile_source_hash_is_stable_for_same_files():
    a = apk.mobile_source_hash()
    b = apk.mobile_source_hash()
    assert a == b
    assert len(a) == 20


def test_android_manifest_shape():
    data = apk.android_manifest()
    assert data["ok"] is True
    assert data["service"] == "cuttle-mobile-android"
    assert data["hash"]
    assert "artifact" in data
    assert "building" in data
    if data["artifact"]:
        assert data["downloadPath"].startswith("/api/mobile/android/app-debug.apk?sha256=")
        assert data["artifactSize"] > 0


def test_mobile_android_routes_registered():
    from api import web_chat_api as wca

    client = wca.app.test_client()
    res = client.get("/api/mobile/android")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    assert body["hash"] == apk.mobile_source_hash()


def test_settings_page_listens_for_apk_update_result():
    src = (REPO / "apps" / "mobile" / "src" / "main.js").read_text(encoding="utf-8")
    assert "cuttle-apk-update" in src
    assert "Phone app is up to date" in src


def test_native_update_check_reports_errors():
    src = (
        REPO
        / "apps"
        / "mobile"
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "cuttle"
        / "mobile"
        / "notify"
        / "ShellUpdate.java"
    ).read_text(encoding="utf-8")
    assert "Could not reach the PC update API" in src
    assert "downloading" in src
    assert "Phone app is up to date." in src
    assert "building" in src
    assert "rebuild" in src.lower()
    assert "installing_hash" in src
    assert "ApkUpdatePolicy.pendingInstall" in src
    assert "NotifyController.isAppInForeground" in src
    assert "Could not read this install" in src


def test_explicit_install_rechecks_instead_of_using_stale_cached_apk():
    src = (REPO / "apps" / "mobile" / "android" / "app" / "src" / "main"
           / "java" / "com" / "cuttle" / "mobile" / "MainActivity.java").read_text()
    callback = src.split("public void installUpdate()", 1)[1].split("@JavascriptInterface", 1)[0]
    assert "ShellUpdate.checkNow" in callback
    assert "ShellUpdate.installDownloaded" not in callback


def test_ensure_update_apk_kicks_rebuild_when_stale(monkeypatch, tmp_path):
    """Stale/missing publish path must request a background Gradle rebuild."""
    monkeypatch.setattr(apk, "_auto_rebuild_allowed", lambda: True)
    calls = []

    def fake_kick(target_hash=None, force=False):
        calls.append({"targetHash": target_hash, "force": force})
        return {"started": True, "reason": "started", "building": True, "targetHash": target_hash}

    monkeypatch.setattr(apk, "kick_apk_rebuild", fake_kick)
    monkeypatch.setattr(apk, "publish_gradle_apk", lambda source_hash=None: None)
    monkeypatch.setattr(apk, "_read_manifest", lambda: None)
    monkeypatch.setattr(apk, "UPDATE_APK", tmp_path / "missing.apk")
    digest = "abc123deadbeef000001"
    assert apk.ensure_update_apk(digest) is None
    assert calls and calls[0]["targetHash"] == digest


def test_shell_does_not_reopen_apk_modal_for_later_hash():
    src = (REPO / "src" / "web" / "js" / "app_shell.js").read_text(encoding="utf-8")
    assert "cuttle-apk-update-later-hash" in src
    assert "apkInstalling" in src
    assert "status === 'installing'" in src


def test_event_stream_drains_while_foreground():
    src = (
        REPO
        / "apps"
        / "mobile"
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "cuttle"
        / "mobile"
        / "notify"
        / "EventStreamService.java"
    ).read_text(encoding="utf-8")
    assert "handleEvent(events.optJSONObject(i), foreground)" in src
    assert "User already has the WebView" in src
    assert "sleepQuiet(2500)" not in src or "Keep polling while the UI is open" in src


def test_offline_login_is_default_when_pc_down():
    main = (REPO / "apps" / "mobile" / "src" / "main.js").read_text(encoding="utf-8")
    html = (REPO / "apps" / "mobile" / "index.html").read_text(encoding="utf-8")
    java = (
        REPO
        / "apps"
        / "mobile"
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "cuttle"
        / "mobile"
        / "MainActivity.java"
    ).read_text(encoding="utf-8")
    assert "get('offline')" in main or "offline') === '1'" in main
    assert "offline-login" in html
    assert "server-fab" in main
    assert "index.html?offline=1" in java


def test_ios_suppresses_foreground_banners():
    src = (
        REPO / "apps" / "mobile" / "ios" / "App" / "App" / "AppDelegate.swift"
    ).read_text(encoding="utf-8")
    assert "completionHandler([])" in src
    stream = (
        REPO / "apps" / "mobile" / "ios" / "App" / "App" / "CuttleEventStream.swift"
    ).read_text(encoding="utf-8")
    assert "if CuttleEventStream.appInForeground { return }" in stream


@pytest.mark.parametrize("version,signer,message", [(2, "d" * 64, "signing key"), (1, "c" * 64, "versionCode"), (0, "c" * 64, "versionCode")])
def test_rejected_publication_preserves_previous_apk(version, signer, message):
    write_test_apk(apk.GRADLE_APK, "a" * 20)
    previous = apk.publish_gradle_apk("a" * 20)
    manifest = apk.UPDATE_MANIFEST.read_bytes()
    write_test_apk(apk.GRADLE_APK, "b" * 20, version, signer)
    with pytest.raises(ValueError, match=message):
        apk.publish_gradle_apk("b" * 20)
    assert apk.UPDATE_MANIFEST.read_bytes() == manifest
    assert previous.is_file()
    assert not list(apk.UPDATE_DIR.glob(".apk-stage-*"))


def test_old_immutable_download_survives_new_publication(monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    apk.register_mobile_android_update_routes(app)
    write_test_apk(apk.GRADLE_APK, "a" * 20)
    old = apk.publish_gradle_apk("a" * 20)
    old_bytes = old.read_bytes()
    checksum = apk.sha256_file(old)
    write_test_apk(apk.GRADLE_APK, "b" * 20, 2)
    apk.publish_gradle_apk("b" * 20)
    monkeypatch.setattr(apk, "mobile_source_hash", lambda: "b" * 20)
    client = app.test_client()
    assert client.get("/api/mobile/android/app-debug.apk?sha256=" + checksum).data == old_bytes
    body = client.get("/api/mobile/android").get_json()
    assert apk.sha256_file(apk._published_path()) == body["sha256"]
    old.write_bytes(b"tampered")
    assert client.get("/api/mobile/android/app-debug.apk?sha256=" + checksum).status_code == 409


def test_public_mode_does_not_start_builds(monkeypatch):
    monkeypatch.setattr(apk, "_auto_rebuild_allowed", lambda: False)
    monkeypatch.setattr(apk, "publish_gradle_apk", lambda *args: pytest.fail("implicit publication"))
    monkeypatch.setattr(apk, "kick_apk_rebuild", lambda *args: pytest.fail("implicit build"))
    assert apk.ensure_update_apk("b" * 20) is None


def test_publication_keeps_only_current_and_previous_apk():
    published = []
    for version, digest in enumerate("abc", start=1):
        write_test_apk(apk.GRADLE_APK, digest * 20, version)
        published.append(apk.publish_gradle_apk(digest * 20))
    remaining = {p.name for p in apk.UPDATE_DIR.glob("app-*.apk")}
    assert remaining == {published[1].name, published[2].name, apk.UPDATE_APK.name}


def test_capacitor_generated_config_is_not_a_hash_input():
    rels = {apk._rel_posix(apk.MOBILE_DIR, p) for p in apk.hash_input_files()}
    assert "android/app/src/main/res/xml/config.xml" not in rels
    assert "android/app/src/main/AndroidManifest.xml" in rels


def test_debug_build_publish_uses_gradle_sdk_and_is_non_fatal():
    gradle = (REPO / "apps" / "mobile" / "android" / "app" / "build.gradle").read_text(encoding="utf-8")
    hook = gradle.split("variant.assembleProvider.configure", 1)[1]
    assert 'environment "ANDROID_HOME", android.sdkDirectory' in hook
    assert "ignoreExitValue true" in hook
