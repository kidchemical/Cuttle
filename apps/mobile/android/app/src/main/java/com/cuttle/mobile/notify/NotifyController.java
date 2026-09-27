package com.cuttle.mobile.notify;

import android.content.Context;
import android.content.SharedPreferences;
import java.util.concurrent.atomic.AtomicBoolean;

public final class NotifyController {
    private static final AtomicBoolean FOREGROUND = new AtomicBoolean(false);
    private static SharedPreferences.OnSharedPreferenceChangeListener listener;

    private NotifyController() {}

    public static void setAppInForeground(boolean inForeground) {
        boolean was = FOREGROUND.getAndSet(inForeground);
        // Drop the background long-poll so Werkzeug HTTPS is free for the WebView.
        if (inForeground && !was) {
            CuttleApi.cancelInFlight();
        }
    }

    public static boolean isAppInForeground() {
        return FOREGROUND.get();
    }

    public static void apply(Context ctx) {
        Context app = ctx.getApplicationContext();
        Notifier.ensureChannels(app);
        if (!NotifyPrefs.shouldListen(app)) {
            EventStreamService.stop(app);
            return;
        }
        if (!NetworkUtil.isOnline(app)) {
            EventStreamService.stop(app);
            return;
        }
        try {
            EventStreamService.start(app);
        } catch (Exception e) {
            android.util.Log.w("CuttleNotify", "Could not start listener", e);
        }
    }

    public static void watchPrefs(Context ctx) {
        if (listener != null) {
            return;
        }
        Context app = ctx.getApplicationContext();
        listener = (prefs, key) -> {
            if (key == null) {
                return;
            }
            if (NotifyPrefs.KEY_BASE_URL.equals(key)
                || NotifyPrefs.KEY_TOKEN.equals(key)
                || NotifyPrefs.KEY_ENABLED.equals(key)) {
                apply(app);
            }
        };
        NotifyPrefs.sp(app).registerOnSharedPreferenceChangeListener(listener);
    }
}
