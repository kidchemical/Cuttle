package com.cuttle.mobile.notify;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.cuttle.mobile.MainActivity;
import org.json.JSONArray;
import org.json.JSONObject;

public class EventStreamService extends Service {
    private static final String TAG = "CuttleNotify";
    private static final int FOREGROUND_ID = 1;
    private static final int OFFLINE_STOP_AFTER = 12;
    private Thread worker;
    private volatile boolean stopping;
    private String lastStatus = "";
    private int offlineStreak;

    public static void start(Context ctx) {
        Context app = ctx.getApplicationContext();
        if (!NotifyPrefs.shouldListen(app)) {
            return;
        }
        if (!NetworkUtil.isOnline(app)) {
            return;
        }
        try {
            app.startForegroundService(new Intent(app, EventStreamService.class));
        } catch (Exception e) {
            Log.w(TAG, "startForegroundService failed", e);
        }
    }

    public static void stop(Context ctx) {
        Context app = ctx.getApplicationContext();
        app.stopService(new Intent(app, EventStreamService.class));
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Notifier.ensureChannels(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        stopping = false;
        promoteForeground("Background");
        if (worker == null || !worker.isAlive()) {
            worker = new Thread(this::loop, "cuttle-notify-poll");
            worker.start();
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        stopping = true;
        if (worker != null) {
            worker.interrupt();
        }
        super.onDestroy();
    }

    private void loop() {
        int backoffMs = 1000;
        while (!stopping) {
            if (!NotifyPrefs.shouldListen(this)) {
                stopSelf();
                return;
            }
            // Keep polling while the UI is open so events do not sit in the
            // server queue and fire as late notifications after pause.
            if (!NetworkUtil.isOnline(this)) {
                offlineStreak++;
                promoteForeground("Waiting for Wi‑Fi…");
                if (offlineStreak >= OFFLINE_STOP_AFTER) {
                    stopSelf();
                    return;
                }
                sleepQuiet(30000);
                continue;
            }
            try {
                boolean foreground = NotifyController.isAppInForeground();
                JSONObject res = CuttleApi.poll(this, foreground ? 3 : 20);
                int http = res.optInt("_http", 0);
                if (http == 401) {
                    promoteForeground("Wrong mobile token — set it in Server settings");
                    sleepQuiet(Math.min(15000, backoffMs));
                    backoffMs = Math.min(15000, backoffMs * 2);
                    continue;
                }
                if (http == 404) {
                    promoteForeground("Restart Flask on the PC, then reopen Cuttle");
                    sleepQuiet(Math.min(15000, backoffMs));
                    backoffMs = Math.min(15000, backoffMs * 2);
                    continue;
                }
                if (http < 200 || http >= 300 || !res.optBoolean("success", http == 200)) {
                    String err = res.optString("error", "HTTP " + http);
                    offlineStreak++;
                    promoteForeground("Can't reach " + CuttleApi.hostLabel(this) + " (" + err + ")");
                    if (offlineStreak >= OFFLINE_STOP_AFTER) {
                        stopSelf();
                        return;
                    }
                    sleepQuiet(Math.min(15000, backoffMs));
                    backoffMs = Math.min(15000, backoffMs * 2);
                    continue;
                }
                backoffMs = 1000;
                offlineStreak = 0;
                markConnected();
                JSONArray events = res.optJSONArray("events");
                if (events != null) {
                    for (int i = 0; i < events.length(); i++) {
                        handleEvent(events.optJSONObject(i), foreground);
                    }
                }
            } catch (Exception e) {
                if (stopping || Thread.currentThread().isInterrupted()) {
                    return;
                }
                if (NotifyController.isAppInForeground()) {
                    sleepQuiet(2500);
                    continue;
                }
                Log.w(TAG, "poll failed", e);
                offlineStreak++;
                String msg = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
                promoteForeground("Can't reach " + CuttleApi.hostLabel(this) + " — " + msg);
                if (offlineStreak >= OFFLINE_STOP_AFTER) {
                    stopSelf();
                    return;
                }
                sleepQuiet(Math.min(15000, backoffMs));
                backoffMs = Math.min(15000, backoffMs * 2);
            }
        }
    }

    private void sleepQuiet(int ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    private void handleEvent(JSONObject obj, boolean appInForeground) {
        if (obj == null) {
            return;
        }
        String kind = obj.optString("type", "");
        Log.i(TAG, "event type=" + kind + " foreground=" + appInForeground);
        if ("status".equals(kind) || kind.isEmpty()) {
            return;
        }
        if (appInForeground) {
            // User already has the WebView; do not shade-notify for replies
            // they are looking at (or just looked at).
            return;
        }
        if ("chat_complete".equals(kind)) {
            CuttleNotifications.showChatComplete(this, obj);
        } else if ("interaction".equals(kind)) {
            CuttleNotifications.showInteraction(this, obj);
        }
    }

    private void markConnected() {
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) {
            promoteForeground("Turn on notifications in Android settings");
            return;
        }
        promoteForeground("Background");
    }

    private void promoteForeground(String status) {
        if (status.equals(lastStatus)) {
            return;
        }
        lastStatus = status;
        Notification n = buildForegroundNotification(status);
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(FOREGROUND_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(FOREGROUND_ID, n);
        }
    }

    private Notification buildForegroundNotification(String status) {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.setData(Uri.parse("cuttle://app/foreground"));
        PendingIntent open = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        NotificationCompat.Builder b = new NotificationCompat.Builder(this, Notifier.LISTENER_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("Cuttle")
            .setContentText(status)
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setVisibility(NotificationCompat.VISIBILITY_SECRET)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setContentIntent(open);
        if (Build.VERSION.SDK_INT >= 31) {
            b.setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_DEFERRED);
        }
        return b.build();
    }
}
