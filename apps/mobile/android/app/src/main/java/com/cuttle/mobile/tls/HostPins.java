package com.cuttle.mobile.tls;

import android.content.Context;
import java.net.URI;
import java.util.Locale;

/** Certificate fingerprints the user has trusted, one per host:port. */
public final class HostPins {
    private static final String PREFS = "cuttle_tls_pins";
    private static volatile Context app;

    private HostPins() {}

    public static void init(Context context) {
        app = context.getApplicationContext();
    }

    public static String key(String host, int port) {
        return String.valueOf(host).replace("[", "").replace("]", "").toLowerCase(Locale.ROOT) + ":" + port;
    }

    public static String get(String host, int port) {
        Context context = app;
        if (context == null) return null;
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(key(host, port), null);
    }

    public static String getForUrl(String url) {
        try {
            URI uri = new URI(url);
            int port = uri.getPort() == -1 ? 443 : uri.getPort();
            return uri.getHost() == null ? null : get(uri.getHost(), port);
        } catch (Exception e) {
            return null;
        }
    }

    public static void put(String host, int port, String fingerprint) {
        Context context = app;
        if (context == null) return;
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(key(host, port), fingerprint).apply();
    }
}
