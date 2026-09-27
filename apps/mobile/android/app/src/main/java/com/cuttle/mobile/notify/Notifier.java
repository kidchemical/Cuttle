package com.cuttle.mobile.notify;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;

public final class Notifier {
    public static final String EVENTS_CHANNEL_ID = "cuttle_events";
    public static final String LISTENER_CHANNEL_ID = "cuttle_listener_quiet";

    private Notifier() {}

    public static void ensureChannels(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) {
            return;
        }

        NotificationChannel events = new NotificationChannel(
            EVENTS_CHANNEL_ID,
            "Cuttle",
            NotificationManager.IMPORTANCE_HIGH
        );
        events.setDescription("Chat replies and questions from Cuttle");
        nm.createNotificationChannel(events);

        NotificationChannel listener = new NotificationChannel(
            LISTENER_CHANNEL_ID,
            "Cuttle background",
            NotificationManager.IMPORTANCE_MIN
        );
        listener.setDescription("Required by Android so chat alerts can arrive with the app closed");
        listener.setShowBadge(false);
        listener.enableLights(false);
        listener.enableVibration(false);
        listener.setSound(null, null);
        nm.createNotificationChannel(listener);
    }
}
