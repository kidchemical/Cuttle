package com.cuttle.mobile.voice;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import com.cuttle.mobile.MainActivity;
import com.cuttle.mobile.notify.Notifier;

/**
 * Keeps an active GPT-Live call's microphone and socket alive with the screen
 * off. Android 14+ only lets a backgrounded app keep recording through a
 * {@code microphone} foreground service; without it the call dies the moment
 * the phone locks. Started from the foreground (the mic tap) and stopped when
 * the call ends.
 */
public class VoiceCallService extends Service {
    private static final String TAG = "CuttleVoice";
    private static final int FOREGROUND_ID = 2;
    private PowerManager.WakeLock wakeLock;

    public static void start(Context ctx) {
        Context app = ctx.getApplicationContext();
        try {
            app.startForegroundService(new Intent(app, VoiceCallService.class));
        } catch (Exception e) {
            Log.w(TAG, "startForegroundService failed", e);
        }
    }

    public static void stop(Context ctx) {
        Context app = ctx.getApplicationContext();
        try {
            app.stopService(new Intent(app, VoiceCallService.class));
        } catch (Exception ignored) {
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notifier.ensureChannels(this);
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(FOREGROUND_ID, buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
            } else {
                startForeground(FOREGROUND_ID, buildNotification());
            }
        } catch (Exception e) {
            // Missing/denied microphone FGS permission: never crash the app over it.
            Log.w(TAG, "microphone foreground service unavailable", e);
            stopSelf();
            return START_NOT_STICKY;
        }
        acquireWakeLock();
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        releaseWakeLock();
        super.onDestroy();
    }

    private void acquireWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            return;
        }
        try {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "cuttle:voice");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        } catch (Exception e) {
            Log.w(TAG, "wake lock failed", e);
        }
    }

    private void releaseWakeLock() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (Exception ignored) {
        }
        wakeLock = null;
    }

    private Notification buildNotification() {
        Intent open = new Intent(this, MainActivity.class);
        open.setData(Uri.parse("cuttle://app/voice"));
        PendingIntent pending = PendingIntent.getActivity(
            this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new NotificationCompat.Builder(this, Notifier.LISTENER_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_phone_call)
            .setContentTitle("Cuttle voice call")
            .setContentText("Listening while the screen is off")
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setContentIntent(pending)
            .build();
    }
}
