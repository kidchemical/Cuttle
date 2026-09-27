package com.cuttle.mobile.notify;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.cuttle.mobile.MainActivity;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;

public final class CuttleNotifications {
    private static final AtomicInteger NEXT_ID = new AtomicInteger(1000);

    private CuttleNotifications() {}

    public static void showChatComplete(Context ctx, JSONObject obj) {
        int id = NEXT_ID.incrementAndGet();
        String pipeline = obj.optString("pipeline", "Cuttle");
        if (pipeline == null || pipeline.isEmpty() || "null".equals(pipeline)) {
            pipeline = "Cuttle";
        }
        String text = obj.optString("response", "Done");
        if (text.length() > 400) {
            text = text.substring(0, 400) + "…";
        }
        String sessionId = stringField(obj, "session_id");

        NotificationCompat.Builder n = new NotificationCompat.Builder(ctx, Notifier.EVENTS_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_more)
            .setContentTitle("Cuttle · " + pipeline)
            .setContentText(text)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(openApp(ctx, id, sessionId))
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE);

        try {
            NotificationManagerCompat.from(ctx).notify(id, n.build());
        } catch (SecurityException ignored) {
            // POST_NOTIFICATIONS denied
        }
    }

    public static void showInteraction(Context ctx, JSONObject obj) {
        String interactionId = obj.optString("interaction_id", "");
        if (interactionId.isEmpty()) {
            return;
        }
        int id = NEXT_ID.incrementAndGet();
        String question = obj.optString("question", "Cuttle needs input");

        NotificationCompat.Builder n = new NotificationCompat.Builder(ctx, Notifier.EVENTS_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setContentTitle("Cuttle needs input")
            .setContentText(question)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(question))
            .addAction(0, "Yes", action(ctx, id, interactionId, "Yes"))
            .addAction(0, "No", action(ctx, id, interactionId, "No"))
            .setAutoCancel(true)
            .setContentIntent(openApp(ctx, id + 1, stringField(obj, "session_id")))
            .setPriority(NotificationCompat.PRIORITY_HIGH);

        try {
            NotificationManagerCompat.from(ctx).notify(id, n.build());
        } catch (SecurityException ignored) {
        }
    }

    private static String stringField(JSONObject obj, String key) {
        if (obj == null || obj.isNull(key)) {
            return "";
        }
        Object raw = obj.opt(key);
        if (raw == null) {
            return "";
        }
        String s = String.valueOf(raw).trim();
        if (s.isEmpty() || "null".equalsIgnoreCase(s)) {
            return "";
        }
        return s;
    }

    private static PendingIntent openApp(Context ctx, int requestCode, String sessionId) {
        Intent i = new Intent(ctx, MainActivity.class);
        i.setFlags(
            Intent.FLAG_ACTIVITY_SINGLE_TOP
                | Intent.FLAG_ACTIVITY_CLEAR_TOP
                | Intent.FLAG_ACTIVITY_NEW_TASK
        );
        // Unique data URI so FLAG_UPDATE_CURRENT cannot reuse another chat's extras.
        if (sessionId != null && !sessionId.isEmpty()) {
            i.putExtra(MainActivity.EXTRA_OPEN_CHAT, sessionId);
            i.setData(Uri.parse("cuttle://chat/" + Uri.encode(sessionId)));
        } else {
            i.setData(Uri.parse("cuttle://app/open"));
        }
        return PendingIntent.getActivity(
            ctx,
            requestCode,
            i,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
    }

    private static PendingIntent action(Context ctx, int notificationId, String interactionId, String answer) {
        Intent i = new Intent(ctx, ActionReceiver.class);
        i.setAction(ActionReceiver.ACTION_REPLY);
        i.putExtra(ActionReceiver.EXTRA_INTERACTION_ID, interactionId);
        i.putExtra(ActionReceiver.EXTRA_ANSWER, answer);
        i.putExtra(ActionReceiver.EXTRA_NOTIFICATION_ID, notificationId);
        return PendingIntent.getBroadcast(
            ctx,
            (notificationId + answer).hashCode(),
            i,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
    }
}
