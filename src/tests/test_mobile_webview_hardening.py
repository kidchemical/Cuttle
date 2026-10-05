"""Phone WebView: HTTPS retry storms + dead composer (source contracts)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAIN = REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "cuttle" / "mobile" / "MainActivity.java"
NOTIFY = REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "java" / "com" / "cuttle" / "mobile" / "notify" / "EventStreamService.java"
SHELL_JS = REPO / "src" / "web" / "js" / "app_shell.js"
CHAT_JS = REPO / "src" / "web" / "js" / "chat_page.js"
ACTIVITY_JS = REPO / "src" / "web" / "js" / "chat_activity.js"
MANIFEST = REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"


def test_native_probes_lan_ping_before_webview_load():
    src = MAIN.read_text(encoding="utf-8")
    assert "CuttleApi.ping" in src
    assert "showServerOffline" in src
    assert "connectionWatchdog" in src


def test_native_detects_webview_error_pages():
    src = MAIN.read_text(encoding="utf-8")
    assert "isWebViewErrorUrl" in src
    assert "onReceivedHttpError" in src
    assert "chrome-error://" in src


def test_notification_service_safe_when_offline():
    notify = NOTIFY.read_text(encoding="utf-8")
    boot = (REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "java"
            / "com" / "cuttle" / "mobile" / "notify" / "BootReceiver.java").read_text(encoding="utf-8")
    ctrl = (REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "java"
            / "com" / "cuttle" / "mobile" / "notify" / "NotifyController.java").read_text(encoding="utf-8")
    assert "NetworkUtil" in notify
    assert "OFFLINE_STOP_AFTER" in notify
    assert "startForegroundService failed" in notify
    assert "Build.VERSION_CODES.S" in boot
    assert "NetworkUtil" in ctrl


def test_native_recovers_main_frame_load_errors():
    src = MAIN.read_text(encoding="utf-8")
    assert "onReceivedError" in src
    assert "handleDocumentLoadError" in src


def test_https_errors_never_downgrade_or_rewrite_saved_origin():
    src = MAIN.read_text(encoding="utf-8")
    assert "httpFallbackBase" not in src
    assert "persistBaseUrl" not in src
    assert "loadCuttle(base);" in src


def test_webview_certificate_and_handshake_errors_fail_closed():
    src = MAIN.read_text(encoding="utf-8")
    callback = src.split("public void onReceivedSslError(", 1)[1].split("@Override", 1)[0]
    assert "handler.cancel();" in callback
    assert "handler.proceed(" not in src
    assert "removeCallbacks(connectionWatchdog)" in callback
    assert "showError(" in callback
    assert "ERROR_FAILED_SSL_HANDSHAKE" in src


def test_native_api_uses_default_certificate_and_hostname_validation():
    api = (MAIN.parent / "notify" / "CuttleApi.java").read_text(encoding="utf-8")
    assert "new OkHttpClient.Builder()" in api
    assert "sslSocketFactory(" not in api
    assert "hostnameVerifier(" not in api
    assert "X509TrustManager" not in api


def test_setup_does_not_promise_android_certificate_bypass():
    js = (REPO / "apps" / "mobile" / "src" / "main.js").read_text(encoding="utf-8")
    assert "self-signed cert is accepted" not in js
    assert "trusted certificate matching the server address" in js


def test_foreground_skips_notification_long_poll():
    src = NOTIFY.read_text(encoding="utf-8")
    assert "isAppInForeground" in src
    assert "sleepQuiet(2500)" in src


def test_shell_skips_cache_bust_on_mobile():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "isCuttleMobileShell" in src
    assert "ERR_TOO_MANY_RETRIES" in src
    assert "retryFailedShellFrame" in src


def test_shell_mobile_frame_failsafe_waits_for_css_recovery():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "isCuttleMobileShell() ? 7000" in src
    assert "TRANSITION_FAILSAFE_MS" in src


def test_ui_boot_retries_failed_local_stylesheets():
    boot = (REPO / "src" / "web" / "js" / "ui_boot.js").read_text(encoding="utf-8")
    assert "localStylesheetLinks" in boot
    assert "reinsertStylesheet" in boot
    assert "_cssr=" in boot
    assert "cuttleCssReload" in boot
    assert "recoverStylesheets" in boot


def test_versioned_css_js_may_be_cached():
    api = (REPO / "src" / "api" / "web_chat_api.py").read_text(encoding="utf-8")
    assert "max-age=604800, immutable" in api
    assert "request.args.get('v')" in api
    assert "asset_versions.is_current" in api
    assert "_no_cache_ui_assets" in api


def test_asset_fingerprint_stamp_and_cache_gate(tmp_path):
    import os
    from api import asset_versions as av
    js = tmp_path / "js"
    js.mkdir()
    f = js / "a.js"
    f.write_text("1")
    html = '<script src="/js/a.js?v=hand1"></script><link href="/css/missing.css?v=x">'
    out = av.stamp_html(html, tmp_path)
    v = out.split("?v=")[1].split('"')[0]
    assert v.startswith("hand1~")
    assert '/css/missing.css?v=x"' in out
    assert av.is_current(tmp_path, "/js/a.js", v)
    assert not av.is_current(tmp_path, "/js/a.js", "hand1")
    # Edit without a manual bump: the old URL stops being cacheable.
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert not av.is_current(tmp_path, "/js/a.js", v)
    assert av.stamp_html(out, tmp_path) != out
    assert av.fingerprint(tmp_path, "/js/../../etc/passwd") is None


def test_notification_open_skips_duplicate_navigate():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "Already on this chat" in src or "do not replaceFrame again" in src
    assert "st.page === page" in src


def test_mobile_boot_delays_apk_check():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "12000" in src
    assert "cuttleMobile?.checkUpdate" in src


def test_native_deep_links_chat_on_cold_start():
    src = MAIN.read_text(encoding="utf-8")
    assert 'chat=" +' in src or "chat=" in src
    assert "urlContainsChat" in src
    assert "TOO_MANY" in src
    assert "tryOpenPendingChat" in src
    # Only fire open-from-notification on the real shell document.
    assert 'url.contains("app_shell.html")' in src


def test_composer_unlocks_readonly_on_pointer():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "unlockComposerField" in src
    assert "pointerdown" in src
    assert "removeAttribute('readonly')" in src


def test_manifest_adjusts_for_keyboard():
    xml = MANIFEST.read_text(encoding="utf-8")
    assert 'android:windowSoftInputMode="adjustResize"' in xml


def test_capacitor_ts_allownavigation_includes_tailscale():
    cfg = (REPO / "apps" / "mobile" / "capacitor.config.ts").read_text(encoding="utf-8")
    assert "100.*.*.*" in cfg
    assert "*.ts.net" in cfg


def test_generated_android_capacitor_config_matches_tailscale():
    android = (
        REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "assets" / "capacitor.config.json"
    )
    if not android.is_file():
        # Generated build artifact (gitignored); absence skips only this test.
        pytest.skip("generated android capacitor.config.json absent")
    text = android.read_text(encoding="utf-8")
    assert "100.*.*.*" in text
    assert "*.ts.net" in text


def test_mobile_setup_remembers_recent_hosts():
    main_js = (REPO / "apps" / "mobile" / "src" / "main.js").read_text(encoding="utf-8")
    html = (REPO / "apps" / "mobile" / "index.html").read_text(encoding="utf-8")
    assert "cuttle_recent_hosts" in main_js
    assert "upsertRecentHost" in main_js
    assert 'id="recent-hosts"' in html


def test_phone_does_not_reopen_notification_chat_on_resume():
    src = MAIN.read_text(encoding="utf-8")
    # onResume used to call tryOpenPendingChat and yank the WebView to a stale chat.
    on_resume = src.split("public void onResume()", 1)[1].split("@Override", 1)[0]
    assert "tryOpenPendingChat" not in on_resume
    assert "tryOpenPendingChat" in src
    assert "Do not retry pendingOpenChat here" in src


def test_notification_pending_intents_are_unique_per_chat():
    src = (REPO / "apps" / "mobile" / "android" / "app" / "src" / "main" / "java"
           / "com" / "cuttle" / "mobile" / "notify" / "CuttleNotifications.java")
    text = src.read_text(encoding="utf-8")
    assert 'cuttle://chat/' in text
    assert "setData" in text


def test_adopt_chat_session_refuses_numeric_hijack():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "Already bound to a Cuttle chat" in src
    assert "prevBare !== nextBare" in src
    # Phase 3 Slice 4: CH- parsing lives in the owned activity module;
    # adoptChatSessionId (still in the page) reaches it via adapter.
    assert "toAuthDbSessionId" in src
    mod = ACTIVITY_JS.read_text(encoding="utf-8")
    assert "/^CH-/i.test(s)" in mod


def test_shell_ignores_outgoing_iframe_url_sync():
    src = SHELL_JS.read_text(encoding="utf-8")
    assert "live.contentWindow !== e.source" in src
    assert "openChatFromNotification" in src
    assert "CH-\\d+" in src or r"CH-\d+" in src
