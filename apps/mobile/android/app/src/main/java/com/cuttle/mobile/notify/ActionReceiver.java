package com.cuttle.mobile.notify;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import androidx.core.app.NotificationManagerCompat;

public class ActionReceiver extends BroadcastReceiver {
    public static final String ACTION_REPLY = "com.cuttle.mobile.ACTION_REPLY";
    public static final String EXTRA_INTERACTION_ID = "interaction_id";
    public static final String EXTRA_ANSWER = "answer";
    public static final String EXTRA_NOTIFICATION_ID = "notification_id";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION_REPLY.equals(intent.getAction())) {
            return;
        }
        String interactionId = intent.getStringExtra(EXTRA_INTERACTION_ID);
        String answer = intent.getStringExtra(EXTRA_ANSWER);
        int notifId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, 0);
        if (interactionId == null || interactionId.isEmpty() || answer == null) {
            return;
        }
        final PendingResult pending = goAsync();
        new Thread(() -> {
            try {
                CuttleApi.submitReply(context.getApplicationContext(), interactionId, answer);
            } catch (Exception ignored) {
            } finally {
                try {
                    NotificationManagerCompat.from(context).cancel(notifId);
                } catch (Exception ignored) {
                }
                pending.finish();
            }
        }, "cuttle-notify-reply").start();
    }
}
