package com.cuttle.mobile;

import android.annotation.SuppressLint;
import android.Manifest;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import androidx.activity.EdgeToEdge;
import androidx.activity.OnBackPressedCallback;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import com.cuttle.mobile.notify.NotifyController;
import com.cuttle.mobile.notify.NotifyPrefs;
import com.cuttle.mobile.notify.CuttleApi;
import com.cuttle.mobile.notify.NetworkUtil;
import com.cuttle.mobile.notify.ShellUpdate;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;
import org.json.JSONObject;

/**
 * Electron-style native shell for Cuttle on Android.
 *
 * Electron: BrowserWindow.loadURL(https://127.0.0.1:8080/app_shell.html)
 * This activity: WebView.loadUrl(http(s)://&lt;pc&gt;:&lt;port&gt;/app_shell.html)
 *
 * Local Capacitor assets are only used for first-run / server settings —
 * not as a "bookmark launcher" that abandons the app.
 */
public class MainActivity extends BridgeActivity {

    public static final String EXTRA_OPEN_CHAT = "cuttle_open_chat";

    private static final String PREFS_GROUP = "CapacitorStorage";
    private static final String KEY_BASE_URL = "cuttle_base_url";
    private static final String KEY_HOST = "cuttle_host";
    private static final String KEY_PORT = "cuttle_port";
    private static final String KEY_HTTPS = "cuttle_https";

    private static final String SETUP_URL = "https://localhost/index.html?setup=1";
    private static final String SETUP_URL_FRESH = "https://localhost/index.html";
    private static final String OFFLINE_URL = "https://localhost/index.html?offline=1";

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private boolean shellReady = false;
    private boolean forceSetupOnce = false;
    private String pendingOpenChat;
    private int documentLoadFailures = 0;
    private boolean recoverQueued = false;
    private static final long CONNECTION_WATCHDOG_MS = 18000;
    private volatile boolean cuttleDocumentReady = false;

    private final Runnable connectionWatchdog = () -> {
        if (cuttleDocumentReady) {
            return;
        }
        WebView wv = bridge != null ? bridge.getWebView() : null;
        if (wv == null) {
            return;
        }
        String url = wv.getUrl() != null ? wv.getUrl() : "";
        // The target URL is known before the document finishes loading.
        // Only onPageFinished marks the shell ready; a stalled shell times out.
        if (url.startsWith("https://localhost") || url.startsWith("http://localhost") || url.startsWith("data:")) {
            return;
        }
        showServerOffline(readBaseUrl());
    };

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        EdgeToEdge.enable(this);

        NotifyController.watchPrefs(this);
        maybeRequestNotificationPermission();
        captureOpenChat(getIntent());
        ShellUpdate.installIfRequested(this, getIntent());

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                handleBack();
            }
        });
    }

    @Override
    public void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        captureOpenChat(intent);
        ShellUpdate.installIfRequested(this, intent);
        WebView webView = bridge != null ? bridge.getWebView() : null;
        tryOpenPendingChat(webView);
    }

    @Override
    public void onResume() {
        super.onResume();
        NotifyController.setAppInForeground(true);
        NotifyController.apply(this);
        // Do not retry pendingOpenChat here. A leftover notification extra used
        // to yank the WebView to another chat every time the app came forward.
        // onNewIntent + onPageFinished are the only open-from-notification paths.
        // Don't compete with the first chat iframe load (Werkzeug HTTPS + Chromium
        // retries → net::ERR_TOO_MANY_RETRIES / "TOO MANY REQUESTS"). Update check
        // can wait — and skip entirely while a notification deep-link is pending.
        if (pendingOpenChat == null || pendingOpenChat.isEmpty()) {
            mainHandler.postDelayed(() -> ShellUpdate.checkOnStart(this), 12000);
        }
    }

    @Override
    public void onPause() {
        // Persist Set-Cookie from /api/auth/login to disk. Without flush(), a
        // force-close right after sign-in (or any cold kill) drops the session
        // and the next launch looks logged out.
        try {
            CookieManager.getInstance().flush();
        } catch (Exception ignored) {}
        NotifyController.setAppInForeground(false);
        super.onPause();
    }

    @Override
    public void onStart() {
        super.onStart();
        if (bridge == null) {
            return;
        }
        WebView webView = bridge.getWebView();
        if (webView == null) {
            return;
        }

        configureWebView(webView);
        attachShellClient(webView);
        attachJsBridge(webView);

        if (!shellReady) {
            shellReady = true;
            mainHandler.postDelayed(this::bootLikeElectron, 80);
        }
        NotifyController.apply(this);
    }

    /**
     * Match Electron createWindow(): load Cuttle UI as the window content when
     * a server is configured; otherwise show the connection settings screen.
     */
    private void bootLikeElectron() {
        if (forceSetupOnce) {
            forceSetupOnce = false;
            return;
        }
        String base = readBaseUrl();
        if (base == null || base.isEmpty()) {
            return; // stay on local setup (Capacitor default)
        }
        showLoading("Starting Cuttle…");
        loadCuttle(base);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView(WebView webView) {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        try {
            CookieManager cm = CookieManager.getInstance();
            cm.setAcceptCookie(true);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                cm.setAcceptThirdPartyCookies(webView, true);
            }
        } catch (Exception ignored) {}
        webView.setFocusable(true);
        webView.setFocusableInTouchMode(true);
    }

    private void attachShellClient(WebView webView) {
        // SafeArea 8 registers its own Bridge WebViewListener for viewport changes.
        // Keep the bridge client so those listeners receive navigation callbacks.
        webView.setWebViewClient(new BridgeWebViewClient(bridge) {
            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                // WebView must never bypass certificate or hostname validation.
                handler.cancel();
                mainHandler.removeCallbacks(connectionWatchdog);
                cuttleDocumentReady = false;
                showError("HTTPS certificate validation failed. Use a server with a valid, "
                    + "trusted certificate matching its address. You can change the address "
                    + "in Server settings.");
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                super.onPageStarted(view, url, favicon);
                injectPreloadBridge(view);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (url != null && isWebViewErrorUrl(url)) {
                    handleDocumentLoadError(url, "Server unreachable");
                    return;
                }
                if (url != null && url.contains("app_shell.html")) {
                    cuttleDocumentReady = true;
                    mainHandler.removeCallbacks(connectionWatchdog);
                    documentLoadFailures = 0;
                    injectPreloadBridge(view);
                    tryOpenPendingChat(view);
                    // Drop local setup / loading entries so the system back key
                    // cannot walk into the server-setup screen.
                    try {
                        view.clearHistory();
                    } catch (Exception ignored) {}
                    try {
                        CookieManager.getInstance().flush();
                    } catch (Exception ignored) {}
                } else if (url != null && (url.startsWith("https://localhost")
                    || url.startsWith("http://localhost")
                    || url.startsWith("data:"))) {
                    injectPreloadBridge(view);
                } else {
                    injectPreloadBridge(view);
                }
                view.requestFocus();
            }

            @Override
            public void onReceivedHttpError(
                WebView view,
                WebResourceRequest request,
                WebResourceResponse errorResponse
            ) {
                if (request != null && request.isForMainFrame()) {
                    int code = errorResponse != null ? errorResponse.getStatusCode() : 0;
                    String url = request.getUrl() != null ? request.getUrl().toString() : "";
                    if (code >= 400) {
                        handleDocumentLoadError(url, "HTTP " + code);
                        return;
                    }
                }
                super.onReceivedHttpError(view, request, errorResponse);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                String url = request != null && request.getUrl() != null
                    ? request.getUrl().toString()
                    : "";
                String desc = error != null && error.getDescription() != null
                    ? error.getDescription().toString()
                    : "load failed";
                if (request != null && request.isForMainFrame()) {
                    if (error != null && error.getErrorCode() == ERROR_FAILED_SSL_HANDSHAKE) {
                        mainHandler.removeCallbacks(connectionWatchdog);
                        cuttleDocumentReady = false;
                        showError("HTTPS connection failed. Check the server certificate and address.");
                        return;
                    }
                    handleDocumentLoadError(url, desc);
                    return;
                }
                super.onReceivedError(view, request, error);
            }
        });
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void attachJsBridge(WebView webView) {
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.addJavascriptInterface(new CuttleShellBridge(), "CuttleShellNative");
    }

    public boolean isShowingCuttleUi() {
        WebView view = bridge != null ? bridge.getWebView() : null;
        if (view == null) {
            return false;
        }
        String url = view.getUrl();
        return url != null && url.contains("app_shell");
    }

    public void dispatchApkUpdateToWeb(boolean available) {
        dispatchApkUpdateToWeb(available, available ? "available" : "up_to_date", "", "");
    }

    public void dispatchApkUpdateToWeb(boolean available, String status, String message) {
        dispatchApkUpdateToWeb(available, status, message, "");
    }

    public void dispatchApkUpdateToWeb(boolean available, String status, String message, String hash) {
        mainHandler.post(() -> {
            WebView view = bridge != null ? bridge.getWebView() : null;
            if (view == null) {
                return;
            }
            String st = status == null ? "" : status;
            String msg = JSONObject.quote(message == null ? "" : message);
            String hs = JSONObject.quote(hash == null ? "" : hash);
            String js =
                "(function(){"
                    + "window.cuttleApkUpdateAvailable=" + available + ";"
                    + "document.dispatchEvent(new CustomEvent('cuttle-apk-update',{detail:{"
                    + "available:" + available + ","
                    + "status:" + JSONObject.quote(st) + ","
                    + "message:" + msg + ","
                    + "hash:" + hs
                    + "}}));"
                    + "})();";
            view.evaluateJavascript(js, null);
        });
    }

    /**
     * Electron preload equivalent — expose window.cuttleMobile / isCuttleMobile
     * inside the Cuttle web UI loaded from the PC.
     */
    private void injectPreloadBridge(WebView view) {
        String hash = JSONObject.quote(ShellUpdate.localShellHash(this));
        String js =
            "(function(){"
                + "window.isCuttleMobile=true;"
                + "window.cuttleMobile=Object.assign(window.cuttleMobile||{},{"
                + "  isNative:true,"
                + "  platform:'android',"
                + "  shellHash:" + hash + ","
                + "  openSettings:function(){try{CuttleShellNative.openSettings()}catch(e){}},"
                + "  reload:function(){try{CuttleShellNative.reload()}catch(e){}},"
                + "  getServerUrl:function(){try{return CuttleShellNative.getServerUrl()}catch(e){return''}},"
                + "  getShellHash:function(){try{return CuttleShellNative.getShellHash()}catch(e){return window.cuttleMobile&&window.cuttleMobile.shellHash||''}},"
                + "  startNotifications:function(){try{CuttleShellNative.startNotifications()}catch(e){}},"
                + "  stopNotifications:function(){try{CuttleShellNative.stopNotifications()}catch(e){}},"
                + "  checkUpdate:function(){try{CuttleShellNative.checkUpdate()}catch(e){}},"
                + "  installUpdate:function(){try{CuttleShellNative.installUpdate()}catch(e){}},"
                + "  flushCookies:function(){try{CuttleShellNative.flushCookies()}catch(e){}},"
                + "  setSessionCookie:function(u,t){try{CuttleShellNative.setSessionCookie(u||'',t||'')}catch(e){}},"
                + "  requestMicrophone:function(){try{CuttleShellNative.requestMicrophone()}catch(e){}},"
                + "  openAppSettings:function(){try{CuttleShellNative.openAppSettings()}catch(e){}},"
                + "  getSafeAreaInsets:function(){try{return JSON.parse(CuttleShellNative.getSafeAreaInsets())}catch(e){return null}}"
                + "});"
                + "try{"
                + "  var __sa=window.cuttleMobile.getSafeAreaInsets&&window.cuttleMobile.getSafeAreaInsets();"
                + "  if(__sa&&__sa.cssNeeded){"
                + "    var __r=document.documentElement;"
                + "    __r.style.setProperty('--safe-area-inset-top',(__sa.top||0)+'px');"
                + "    __r.style.setProperty('--safe-area-inset-right',(__sa.right||0)+'px');"
                + "    __r.style.setProperty('--safe-area-inset-bottom',(__sa.bottom||0)+'px');"
                + "    __r.style.setProperty('--safe-area-inset-left',(__sa.left||0)+'px');"
                + "    window.__cuttleSafeArea={top:(__sa.top||0)+'px',right:(__sa.right||0)+'px',bottom:(__sa.bottom||0)+'px',left:(__sa.left||0)+'px'};"
                + "  }"
                + "}catch(e){}"
                + "window.cuttleApkUpdateAvailable="
                + (ShellUpdate.isUpdateAvailable() ? "true" : "false") + ";"
                + "if(!window.__cuttleMobileBridge){"
                + "  window.__cuttleMobileBridge=true;"
                + "  document.dispatchEvent(new CustomEvent('cuttle-mobile-ready'));"
                + "}"
                + "})();";
        view.evaluateJavascript(js, null);
    }

    private void captureOpenChat(Intent intent) {
        if (intent == null) {
            return;
        }
        String sid = intent.getStringExtra(EXTRA_OPEN_CHAT);
        if (sid != null && !sid.trim().isEmpty()) {
            pendingOpenChat = sid.trim();
        }
    }

    private void tryOpenPendingChat(WebView view) {
        if (pendingOpenChat == null || pendingOpenChat.isEmpty() || view == null) {
            return;
        }
        String url = view.getUrl() != null ? view.getUrl() : "";
        // Cold start already deep-linked ?chat= into app_shell — don't navigate again
        // (second iframe load stampede → ERR_TOO_MANY_RETRIES / black screen).
        if (urlContainsChat(url, pendingOpenChat)) {
            clearPendingOpenChat();
            return;
        }
        String quoted = JSONObject.quote(pendingOpenChat);
        String js =
            "(function(){try{"
                + "if(typeof openChatFromNotification==='function'){"
                + "openChatFromNotification(" + quoted + ");"
                + "return 'ok';}"
                + "return 'missing';"
                + "}catch(e){return 'err';}})();";
        view.evaluateJavascript(js, value -> {
            if (value != null && value.contains("ok")) {
                clearPendingOpenChat();
            }
        });
    }

    private void clearPendingOpenChat() {
        pendingOpenChat = null;
        Intent it = getIntent();
        if (it != null && it.hasExtra(EXTRA_OPEN_CHAT)) {
            it.removeExtra(EXTRA_OPEN_CHAT);
            setIntent(it);
        }
    }

    private static boolean urlContainsChat(String url, String sessionId) {
        if (url == null || sessionId == null || sessionId.isEmpty()) {
            return false;
        }
        String sid = sessionId.trim();
        if (sid.isEmpty()) {
            return false;
        }
        // Match numeric or CH-000225 style against ?chat=
        String bare = sid;
        if (sid.regionMatches(true, 0, "CH-", 0, 3)) {
            try {
                bare = String.valueOf(Integer.parseInt(sid.substring(3)));
            } catch (NumberFormatException ignored) {
                bare = sid;
            }
        }
        return url.contains("chat=" + bare)
            || url.contains("chat=" + android.net.Uri.encode(bare))
            || url.contains("chat=" + sid)
            || url.contains("chat=" + android.net.Uri.encode(sid));
    }

    private void loadCuttle(String baseUrl) {
        final String entry = normalizeEntryUrl(baseUrl);
        WebView webView = bridge != null ? bridge.getWebView() : null;
        if (webView == null) {
            return;
        }
        cuttleDocumentReady = false;
        mainHandler.removeCallbacks(connectionWatchdog);
        if (!NetworkUtil.isOnline(this)) {
            showServerOffline(baseUrl);
            return;
        }
        new Thread(() -> {
            boolean reachable = CuttleApi.ping(this, 5);
            mainHandler.post(() -> {
                WebView wv = bridge != null ? bridge.getWebView() : null;
                if (wv == null) {
                    return;
                }
                if (!reachable) {
                    showServerOffline(baseUrl);
                    return;
                }
                wv.loadUrl(entry);
                mainHandler.postDelayed(connectionWatchdog, CONNECTION_WATCHDOG_MS);
            });
        }, "cuttle-reachability").start();
    }

    private void openSettings() {
        WebView webView = bridge != null ? bridge.getWebView() : null;
        if (webView == null) {
            return;
        }
        forceSetupOnce = true;
        mainHandler.post(() -> webView.loadUrl(SETUP_URL));
    }

    private void showLoading(String message) {
        WebView webView = bridge != null ? bridge.getWebView() : null;
        if (webView == null) {
            return;
        }
        String safe = message == null ? "Loading…" : message.replace("'", "\\'");
        String html =
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
                + "<style>"
                + "html,body{margin:0;min-height:100%;background:#0b0f14;color:#e8edf5;"
                + "font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;"
                + "padding:env(safe-area-inset-top,0) env(safe-area-inset-right,0) env(safe-area-inset-bottom,0) env(safe-area-inset-left,0)}"
                + ".box{text-align:center;padding:1.5rem}"
                + "h1{font-size:1.4rem;margin:0 0 .5rem}"
                + "p{color:#8b9bb0;margin:0}"
                + ".spin{width:36px;height:36px;margin:0 auto 1rem;border:3px solid #243041;"
                + "border-top-color:#3b82f6;border-radius:50%;animation:s .8s linear infinite}"
                + "@keyframes s{to{transform:rotate(360deg)}}"
                + "</style></head><body><div class='box'><div class='spin'></div>"
                + "<h1>Cuttle</h1><p>"
                + safe
                + "</p></div></body></html>";
        mainHandler.post(() -> webView.loadDataWithBaseURL(
            "https://localhost/",
            html,
            "text/html",
            "UTF-8",
            null
        ));
    }

    private void showOfflineLogin(String baseUrl) {
        mainHandler.removeCallbacks(connectionWatchdog);
        cuttleDocumentReady = false;
        WebView webView = bridge != null ? bridge.getWebView() : null;
        if (webView == null) {
            return;
        }
        mainHandler.post(() -> webView.loadUrl(OFFLINE_URL));
    }

    private void showServerOffline(String baseUrl) {
        showOfflineLogin(baseUrl);
    }

    private static String escapeHtml(String s) {
        if (s == null) {
            return "";
        }
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }

    private static boolean isWebViewErrorUrl(String url) {
        if (url == null || url.isEmpty()) {
            return false;
        }
        String u = url.toLowerCase(java.util.Locale.US);
        return u.startsWith("chrome-error://")
            || u.contains("chromewebdata")
            || u.startsWith("about:neterror");
    }

    private void showError(String message) {
        WebView webView = bridge != null ? bridge.getWebView() : null;
        if (webView == null) {
            return;
        }
        String safe = (message == null ? "Could not reach Cuttle" : message)
            .replace("'", "&#39;");
        if (!safe.contains("<")) {
            safe = safe
                .replace("&", "&amp;")
                .replace("<", "&lt;");
        }
        String html =
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                + "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
                + "<style>"
                + "html,body{margin:0;min-height:100%;background:#0b0f14;color:#e8edf5;"
                + "font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;"
                + "padding:env(safe-area-inset-top,0) env(safe-area-inset-right,0) env(safe-area-inset-bottom,0) env(safe-area-inset-left,0)}"
                + ".box{text-align:center;padding:1.5rem;max-width:22rem}"
                + "h1{font-size:1.4rem;margin:0 0 .5rem;color:#f87171}"
                + "p{color:#8b9bb0;line-height:1.45}"
                + "button{margin-top:1rem;padding:.75rem 1rem;width:100%;border:0;border-radius:8px;"
                + "background:#3b82f6;color:#fff;font-weight:600;font-size:1rem}"
                + "button.secondary{background:transparent;border:1px solid #243041;color:#e8edf5;margin-top:.5rem}"
                + "</style></head><body><div class='box'><h1>Cuttle</h1><p>"
                + safe
                + "</p>"
                + "<button onclick=\"CuttleShellNative.reload()\">Retry</button>"
                + "<button class='secondary' onclick=\"CuttleShellNative.openSettings()\">Server settings</button>"
                + "</div></body></html>";
        mainHandler.post(() -> webView.loadDataWithBaseURL(
            "https://localhost/",
            html,
            "text/html",
            "UTF-8",
            null
        ));
    }

    /**
     * System back while Cuttle UI is open should behave like browser back
     * (previous chat / page → eventually new chat), never jump to server setup.
     * Settings stay available via the in-app Server control.
     */
    private void handleBack() {
        WebView webView = bridge != null ? bridge.getWebView() : null;
        if (webView == null) {
            finish();
            return;
        }
        String url = webView.getUrl() != null ? webView.getUrl() : "";
        if (isCuttleRemote(url)) {
            askShellHandleBack(webView);
            return;
        }
        if (url.contains("index.html") || url.startsWith("https://localhost") || url.startsWith("data:")) {
            finish();
            return;
        }
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            finish();
        }
    }

    /** Ask app_shell to pop chat/page history; always consume back (never minimize). */
    private void askShellHandleBack(WebView webView) {
        String js =
            "(function(){try{"
                + "if(typeof window.__cuttleHandleBack==='function'){"
                + "window.__cuttleHandleBack();}"
                + "return true;"
                + "}catch(e){return true;}})()";
        webView.evaluateJavascript(js, value -> {
            // Intentionally ignore result. At new-chat root the shell no-ops;
            // never moveTaskToBack / openSettings on system back.
        });
    }

    private boolean isCuttleRemote(String url) {
        if (url == null || url.isEmpty()) {
            return false;
        }
        if (url.startsWith("https://localhost") || url.startsWith("http://localhost")) {
            return false;
        }
        if (url.startsWith("data:")) {
            return false;
        }
        return url.startsWith("http://") || url.startsWith("https://");
    }

    private String readBaseUrl() {
        SharedPreferences prefs = getSharedPreferences(PREFS_GROUP, MODE_PRIVATE);
        String base = prefs.getString(KEY_BASE_URL, "");
        return base != null ? base.trim() : "";
    }

    private void writeConfig(String host, String port, boolean https, String baseUrl) {
        SharedPreferences prefs = getSharedPreferences(PREFS_GROUP, MODE_PRIVATE);
        prefs.edit()
            .putString(KEY_HOST, host)
            .putString(KEY_PORT, port)
            .putString(KEY_HTTPS, https ? "1" : "0")
            .putString(KEY_BASE_URL, baseUrl)
            .apply();
    }

    private String normalizeEntryUrl(String baseUrl) {
        String base = baseUrl == null ? "" : baseUrl.trim().replaceAll("/+$", "");
        String entry;
        if (base.contains("app_shell.html")) {
            entry = base;
        } else {
            entry = base + "/app_shell.html";
        }
        // Boot straight into the notified chat (one shell+iframe load). Opening
        // bare app_shell then navigate()-ing caused Werkzeug HTTPS retry storms.
        String sid = pendingOpenChat;
        if (sid != null && !sid.isEmpty() && !entry.contains("chat=")) {
            String bare = sid.trim();
            if (bare.regionMatches(true, 0, "CH-", 0, 3)) {
                try {
                    bare = String.valueOf(Integer.parseInt(bare.substring(3)));
                } catch (NumberFormatException ignored) {
                    // keep CH- form
                }
            }
            entry = entry + (entry.contains("?") ? "&" : "?") + "chat=" + android.net.Uri.encode(bare);
        }
        return entry;
    }

    /** Match @capacitor-community/safe-area: Chromium ≥140 needs CSS env() handling. */
    private int webViewMajorVersion() {
        try {
            PackageInfo packageInfo = null;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                packageInfo = WebView.getCurrentWebViewPackage();
            }
            if (packageInfo == null || packageInfo.versionName == null) {
                return 0;
            }
            Matcher matcher = Pattern.compile("(\\d+)").matcher(packageInfo.versionName);
            if (!matcher.find()) {
                return 0;
            }
            return Integer.parseInt(matcher.group(1));
        } catch (Exception ignored) {
            return 0;
        }
    }

    private void handleDocumentLoadError(String url, String desc) {
        if (url == null) {
            url = "";
        }
        if (url.startsWith("https://localhost") || url.startsWith("http://localhost") || url.startsWith("data:")) {
            return;
        }
        if (recoverQueued) {
            return;
        }
        recoverQueued = true;
        documentLoadFailures += 1;
        final int n = documentLoadFailures;
        final String err = desc == null ? "" : desc;
        final boolean tooMany = err.toUpperCase(java.util.Locale.US).contains("TOO_MANY")
            || err.toUpperCase(java.util.Locale.US).contains("TOO MANY");
        // Retry the selected origin only. Cleartext requires an explicit choice
        // in Server settings, never an automatic downgrade after HTTPS failure.
        long delayMs = tooMany ? 200 : (n <= 1 ? 500 : (n == 2 ? 1400 : 2400));
        mainHandler.postDelayed(() -> {
            recoverQueued = false;
            String base = readBaseUrl();
            if (base == null || base.isEmpty()) {
                showError(err.isEmpty() ? "Could not reach Cuttle" : err);
                return;
            }
            if (n >= 4 || (tooMany && n >= 2)) {
                documentLoadFailures = 0;
                showServerOffline(base);
                return;
            }
            showLoading("Reconnecting…");
            loadCuttle(base);
        }, delayMs);
    }

    private class CuttleShellBridge {
        @JavascriptInterface
        public void openSettings() {
            mainHandler.post(MainActivity.this::openSettings);
        }

        @JavascriptInterface
        public void reload() {
            mainHandler.post(() -> {
                String base = readBaseUrl();
                if (base == null || base.isEmpty()) {
                    openSettings();
                    return;
                }
                showLoading("Reconnecting…");
                loadCuttle(base);
            });
        }

        @JavascriptInterface
        public String getServerUrl() {
            return readBaseUrl();
        }

        @JavascriptInterface
        public void loadServer(String baseUrl) {
            if (baseUrl == null || baseUrl.trim().isEmpty()) {
                return;
            }
            final String base = baseUrl.trim().replaceAll("/+$", "");
            mainHandler.post(() -> {
                // Host/port already saved by Capacitor Preferences from JS;
                // keep native copy in sync for cold start.
                SharedPreferences prefs = getSharedPreferences(PREFS_GROUP, MODE_PRIVATE);
                prefs.edit().putString(KEY_BASE_URL, base).apply();
                showLoading("Opening Cuttle…");
                loadCuttle(base);
            });
        }

        @JavascriptInterface
        public void saveAndLoad(String host, String port, String httpsFlag, String baseUrl) {
            boolean https = "1".equals(httpsFlag) || "true".equalsIgnoreCase(httpsFlag);
            writeConfig(
                host != null ? host : "",
                port != null ? port : "8000",
                https,
                baseUrl != null ? baseUrl : ""
            );
            mainHandler.post(() -> {
                showLoading("Opening Cuttle…");
                loadCuttle(baseUrl);
                NotifyController.apply(MainActivity.this);
            });
        }

        @JavascriptInterface
        public void showConnectionError(String message) {
            mainHandler.post(() -> {
                if (message == null || message.trim().isEmpty()) {
                    showServerOffline(readBaseUrl());
                } else {
                    showError(message);
                }
            });
        }

        @JavascriptInterface
        public String getShellHash() {
            return ShellUpdate.localShellHash(MainActivity.this);
        }

        /**
         * CSS px insets for edge-to-edge. cssNeeded=true when Chromium ≥140
         * (env() should be used / polyfilled). Older WebViews pad natively and
         * report cssNeeded=false so the UI must not add a second inset.
         */
        @JavascriptInterface
        public String getSafeAreaInsets() {
            try {
                View decor = getWindow().getDecorView();
                WindowInsetsCompat wi = ViewCompat.getRootWindowInsets(decor);
                float density = getResources().getDisplayMetrics().density;
                if (density <= 0f) {
                    density = 1f;
                }
                int top = 0;
                int right = 0;
                int bottom = 0;
                int left = 0;
                if (wi != null) {
                    Insets sb = wi.getInsets(
                        WindowInsetsCompat.Type.systemBars()
                            | WindowInsetsCompat.Type.displayCutout()
                    );
                    top = sb.top;
                    right = sb.right;
                    bottom = sb.bottom;
                    left = sb.left;
                }
                boolean cssNeeded = webViewMajorVersion() >= 140;
                JSONObject o = new JSONObject();
                o.put("top", top / density);
                o.put("right", right / density);
                o.put("bottom", bottom / density);
                o.put("left", left / density);
                o.put("cssNeeded", cssNeeded);
                return o.toString();
            } catch (Exception e) {
                return "{\"top\":0,\"right\":0,\"bottom\":0,\"left\":0,\"cssNeeded\":false}";
            }
        }

        @JavascriptInterface
        public void checkUpdate() {
            mainHandler.post(() -> ShellUpdate.checkNow(MainActivity.this));
        }

        @JavascriptInterface
        public void installUpdate() {
            // Recheck and download the current artifact instead of reopening an
            // older cached APK after a failed or cancelled installation.
            mainHandler.post(() -> ShellUpdate.checkNow(MainActivity.this));
        }

        @JavascriptInterface
        public void startNotifications() {
            mainHandler.post(() -> NotifyController.apply(MainActivity.this));
        }

        @JavascriptInterface
        public void stopNotifications() {
            mainHandler.post(() -> NotifyController.apply(MainActivity.this));
        }

        /** Persist WebView cookies to disk (login Set-Cookie is otherwise memory-only). */
        @JavascriptInterface
        public void flushCookies() {
            mainHandler.post(() -> {
                try {
                    CookieManager.getInstance().flush();
                } catch (Exception ignored) {}
            });
        }

        /**
         * Explicitly plant session_token for the LAN origin. Android WebView
         * sometimes accepts the login JSON but drops the Set-Cookie header;
         * planting + flush keeps chat history and cold-start signed in.
         */
        @JavascriptInterface
        public void setSessionCookie(String baseUrl, String token) {
            final String base = baseUrl == null ? "" : baseUrl.trim().replaceAll("/+$", "");
            final String tok = token == null ? "" : token.trim();
            mainHandler.post(() -> {
                try {
                    CookieManager cm = CookieManager.getInstance();
                    cm.setAcceptCookie(true);
                    String url = base.isEmpty() ? readBaseUrl() : base;
                    if (url == null || url.isEmpty()) {
                        return;
                    }
                    if (!url.contains("://")) {
                        url = "http://" + url;
                    }
                    boolean https = url.regionMatches(true, 0, "https://", 0, 8);
                    if (tok.isEmpty()) {
                        cm.setCookie(url, "session_token=; Path=/; Max-Age=0");
                    } else {
                        StringBuilder cookie = new StringBuilder();
                        cookie.append("session_token=").append(tok)
                            .append("; Path=/; Max-Age=").append(30 * 24 * 60 * 60)
                            .append("; SameSite=Lax");
                        if (https) {
                            cookie.append("; Secure");
                        }
                        cm.setCookie(url, cookie.toString());
                    }
                    cm.flush();
                } catch (Exception ignored) {}
            });
        }

        /** Runtime RECORD_AUDIO prompt for voice mode (shows Mic in system Settings too). */
        @JavascriptInterface
        public void requestMicrophone() {
            mainHandler.post(() -> maybeRequestMicrophonePermission(true));
        }

        /** Open app details so the user can enable Microphone after a deny. */
        @JavascriptInterface
        public void openAppSettings() {
            mainHandler.post(() -> {
                try {
                    Intent intent = new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
                    intent.setData(android.net.Uri.parse("package:" + getPackageName()));
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(intent);
                } catch (Exception ignored) {}
            });
        }
    }

    private void maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) {
            return;
        }
        if (!NotifyPrefs.isEnabled(this)) {
            return;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ActivityCompat.requestPermissions(this, new String[] {Manifest.permission.POST_NOTIFICATIONS}, 1001);
    }

    private static final int REQ_MIC = 1002;

    private void maybeRequestMicrophonePermission(boolean forcePrompt) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        // forcePrompt unused today — always show the system dialog when JS asks.
        ActivityCompat.requestPermissions(
            this,
            new String[] { Manifest.permission.RECORD_AUDIO },
            REQ_MIC
        );
    }
}
