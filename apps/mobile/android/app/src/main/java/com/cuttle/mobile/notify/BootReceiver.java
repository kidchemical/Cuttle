package com.cuttle.mobile.notify;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) {
            return;
        }
        String action = intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
            && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            return;
        }
        // Android 12+ blocks foreground services started from BOOT_COMPLETED —
        // uncaught, that surfaces as repeated "Cuttle keeps stopping" crashes.
        if (Intent.ACTION_BOOT_COMPLETED.equals(action)
            && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            return;
        }
        NotifyController.apply(context);
    }
}
