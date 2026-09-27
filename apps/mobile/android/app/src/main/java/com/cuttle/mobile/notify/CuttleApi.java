package com.cuttle.mobile.notify;

import android.content.Context;
import java.io.IOException;
import java.net.URLEncoder;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.json.JSONObject;

/**
 * LAN Cuttle API for the notification listener. Trusts the same self-signed
 * HTTPS cert the WebView already accepts.
 */
public final class CuttleApi {
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");
    private static OkHttpClient client;

    private CuttleApi() {}

    static synchronized OkHttpClient client() {
        if (client == null) {
            client = buildClient();
        }
        return client;
    }

    public static String hostLabel(Context ctx) {
        String base = NotifyPrefs.getBaseUrl(ctx).replaceAll("/+$", "");
        return base.isEmpty() ? "(no PC saved)" : base.replace("https://", "").replace("http://", "");
    }

    public static void cancelInFlight() {
        try {
            if (client != null) {
                client.dispatcher().cancelAll();
            }
        } catch (Exception ignored) {
        }
    }

    /** Quick reachability probe — same endpoint as the setup screen Test button. */
    public static boolean ping(Context ctx, int timeoutSec) {
        String base = NotifyPrefs.getBaseUrl(ctx).replaceAll("/+$", "");
        if (base.isEmpty()) {
            return false;
        }
        int sec = Math.max(2, Math.min(timeoutSec, 12));
        try {
            OkHttpClient shortClient = client().newBuilder()
                .connectTimeout(sec, TimeUnit.SECONDS)
                .readTimeout(sec + 2, TimeUnit.SECONDS)
                .callTimeout(sec + 3, TimeUnit.SECONDS)
                .retryOnConnectionFailure(false)
                .build();
            Request req = new Request.Builder()
                .url(base + "/api/lan-ping")
                .header("Accept", "application/json")
                .get()
                .build();
            try (Response res = shortClient.newCall(req).execute()) {
                if (!res.isSuccessful()) {
                    return false;
                }
                String body = res.body() != null ? res.body().string() : "";
                JSONObject obj = new JSONObject(body.isEmpty() ? "{}" : body);
                return obj.optBoolean("ok", false);
            }
        } catch (Exception e) {
            return false;
        }
    }

    public static JSONObject poll(Context ctx, int timeoutSec) throws IOException {
        String base = NotifyPrefs.getBaseUrl(ctx).replaceAll("/+$", "");
        String deviceId = NotifyPrefs.getOrCreateDeviceId(ctx);
        String token = NotifyPrefs.getToken(ctx);
        String url = base
            + "/api/mobile/poll?device_id="
            + encode(deviceId)
            + "&token="
            + encode(token)
            + "&timeout="
            + Math.max(0, timeoutSec);
        Request req = new Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .get()
            .build();
        try (Response res = client().newCall(req).execute()) {
            String body = res.body() != null ? res.body().string() : "";
            JSONObject obj;
            try {
                obj = new JSONObject(body.isEmpty() ? "{}" : body);
            } catch (Exception e) {
                obj = new JSONObject();
            }
            try {
                obj.put("_http", res.code());
            } catch (Exception ignored) {
            }
            return obj;
        }
    }

    public static void submitReply(Context ctx, String interactionId, String answer) throws IOException {
        String base = NotifyPrefs.getBaseUrl(ctx).replaceAll("/+$", "");
        String token = NotifyPrefs.getToken(ctx);
        String bodyJson =
            "{\"token\":"
                + jsonString(token)
                + ",\"interaction_id\":"
                + jsonString(interactionId)
                + ",\"answer\":"
                + jsonString(answer)
                + "}";
        Request req = new Request.Builder()
            .url(base + "/api/mobile/reply")
            .post(RequestBody.create(bodyJson, JSON))
            .build();
        try (Response ignored = client().newCall(req).execute()) {
            // best-effort
        }
    }

    public static JSONObject getJson(Context ctx, String path) throws IOException {
        String base = NotifyPrefs.getBaseUrl(ctx).replaceAll("/+$", "");
        String url = base + (path.startsWith("/") ? path : "/" + path);
        Request req = new Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .get()
            .build();
        try (Response res = client().newCall(req).execute()) {
            String body = res.body() != null ? res.body().string() : "";
            JSONObject obj;
            try {
                obj = new JSONObject(body.isEmpty() ? "{}" : body);
            } catch (Exception e) {
                obj = new JSONObject();
            }
            try {
                obj.put("_http", res.code());
            } catch (Exception ignored) {
            }
            return obj;
        }
    }

    public static void download(Context ctx, String path, java.io.File dest) throws IOException {
        String base = NotifyPrefs.getBaseUrl(ctx).replaceAll("/+$", "");
        String url = base + (path.startsWith("/") ? path : "/" + path);
        Request req = new Request.Builder()
            .url(url)
            .header("Accept", "application/vnd.android.package-archive")
            .get()
            .build();
        try (Response res = client().newCall(req).execute()) {
            if (!res.isSuccessful() || res.body() == null) {
                throw new IOException("Download failed (" + res.code() + ")");
            }
            java.io.File parent = dest.getParentFile();
            if (parent != null && !parent.exists()) {
                //noinspection ResultOfMethodCallIgnored
                parent.mkdirs();
            }
            try (java.io.InputStream in = res.body().byteStream();
                 java.io.FileOutputStream out = new java.io.FileOutputStream(dest)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) != -1) {
                    out.write(buf, 0, n);
                }
            }
        }
    }

    private static OkHttpClient buildClient() {
        try {
            TrustManager[] trustAll = new TrustManager[] {
                new X509TrustManager() {
                    @Override
                    public void checkClientTrusted(X509Certificate[] chain, String authType) {}

                    @Override
                    public void checkServerTrusted(X509Certificate[] chain, String authType) {}

                    @Override
                    public X509Certificate[] getAcceptedIssuers() {
                        return new X509Certificate[0];
                    }
                }
            };
            SSLContext ssl = SSLContext.getInstance("TLS");
            ssl.init(null, trustAll, new SecureRandom());
            X509TrustManager tm = (X509TrustManager) trustAll[0];
            return new OkHttpClient.Builder()
                .sslSocketFactory(ssl.getSocketFactory(), tm)
                .hostnameVerifier((hostname, session) -> true)
                .retryOnConnectionFailure(true)
                .readTimeout(180, TimeUnit.SECONDS)
                .writeTimeout(15, TimeUnit.SECONDS)
                .connectTimeout(15, TimeUnit.SECONDS)
                .callTimeout(180, TimeUnit.SECONDS)
                .build();
        } catch (Exception e) {
            return new OkHttpClient.Builder()
                .retryOnConnectionFailure(true)
                .readTimeout(35, TimeUnit.SECONDS)
                .connectTimeout(15, TimeUnit.SECONDS)
                .build();
        }
    }

    private static String encode(String s) {
        try {
            return URLEncoder.encode(s == null ? "" : s, "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    private static String jsonString(String s) {
        String escaped = (s == null ? "" : s)
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t");
        return "\"" + escaped + "\"";
    }
}
