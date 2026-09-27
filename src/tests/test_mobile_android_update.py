"""LAN Android APK updates for the Capacitor shell."""

from __future__ import annotations

from pathlib import Path

from api import mobile_android_update as apk

REPO = Path(__file__).resolve().parents[2]


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
        assert data["downloadPath"] == "/api/mobile/android/app-debug.apk"
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
    assert "NotifyController.isAppInForeground" in src
    assert "Could not read this install" in src


def test_ensure_update_apk_kicks_rebuild_when_stale(monkeypatch, tmp_path):
    """Stale/missing publish path must request a background Gradle rebuild."""
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
