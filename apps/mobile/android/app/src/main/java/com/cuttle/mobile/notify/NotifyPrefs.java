package com.cuttle.mobile.notify;

import android.content.Context;
import android.content.SharedPreferences;
import java.util.UUID;

/**
 * Reads Capacitor Preferences (group CapacitorStorage) plus a native device id.
 */
public final class NotifyPrefs {
    static final String GROUP = "CapacitorStorage";
    static final String KEY_BASE_URL = "cuttle_base_url";
    static final String KEY_TOKEN = "cuttle_mobile_token";
    static final String KEY_ENABLED = "cuttle_notify_enabled";
    private static final String KEY_DEVICE_ID = "cuttle_device_id";
    static final String DEFAULT_TOKEN = "dev-local-token";

    private NotifyPrefs() {}

    static SharedPreferences sp(Context ctx) {
        return ctx.getSharedPreferences(GROUP, Context.MODE_PRIVATE);
    }

    public static String getBaseUrl(Context ctx) {
        String v = sp(ctx).getString(KEY_BASE_URL, "");
        return v != null ? v.trim() : "";
    }

    public static String getToken(Context ctx) {
        String v = sp(ctx).getString(KEY_TOKEN, "");
        if (v == null || v.trim().isEmpty()) {
            return DEFAULT_TOKEN;
        }
        return v.trim();
    }

    /** Default on when the key is missing (existing installs pick this up). */
    public static boolean isEnabled(Context ctx) {
        String v = sp(ctx).getString(KEY_ENABLED, "1");
        return v == null || !"0".equals(v);
    }

    public static String getOrCreateDeviceId(Context ctx) {
        SharedPreferences prefs = sp(ctx);
        String existing = prefs.getString(KEY_DEVICE_ID, null);
        if (existing != null && !existing.trim().isEmpty()) {
            return existing;
        }
        String id = "android-" + UUID.randomUUID();
        prefs.edit().putString(KEY_DEVICE_ID, id).apply();
        return id;
    }

    public static boolean shouldListen(Context ctx) {
        return isEnabled(ctx) && !getBaseUrl(ctx).isEmpty();
    }
}
