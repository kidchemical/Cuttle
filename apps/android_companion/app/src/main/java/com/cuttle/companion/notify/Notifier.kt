package com.cuttle.companion.notify

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build

object Notifier {
    const val CHANNEL_ID = "cuttle_events"
    const val CHANNEL_NAME = "Cuttle"

    fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val ch = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Cuttle events and questions"
        }
        nm.createNotificationChannel(ch)
    }
}

