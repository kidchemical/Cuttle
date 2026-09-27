package com.cuttle.companion.sse

import android.app.Notification
import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat
import android.app.PendingIntent
import com.cuttle.companion.notify.Notifier
import com.cuttle.companion.ui.MainActivity
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class EventStreamService : Service() {
    private var eventSource: EventSource? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifier.ensureChannel(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(1, buildForegroundNotification("Listening…"))
        connect()
        return START_STICKY
    }

    override fun onDestroy() {
        try {
            eventSource?.cancel()
        } catch (_: Exception) {
        }
        super.onDestroy()
    }

    private fun connect() {
        val client = OkHttpClient.Builder()
            .retryOnConnectionFailure(true)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()

        val url = CuttleApi.buildEventsUrl(this)
        val req = Request.Builder().url(url).build()

        val factory = EventSources.createFactory(client)
        eventSource = factory.newEventSource(req, object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) {
                startForeground(1, buildForegroundNotification("Connected"))
            }

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                handleEvent(data)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                startForeground(1, buildForegroundNotification("Disconnected (retrying)"))
            }
        })
    }

    private fun handleEvent(data: String) {
        val obj = try {
            JSONObject(data)
        } catch (_: Exception) {
            return
        }
        when (obj.optString("type")) {
            "chat_complete" -> Notifications.showChatComplete(this, obj)
            "interaction" -> Notifications.showInteraction(this, obj)
        }
    }

    private fun buildForegroundNotification(status: String): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, Notifier.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("Cuttle Companion")
            .setContentText(status)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .build()
    }

    companion object {
        fun start(ctx: android.content.Context) {
            val i = Intent(ctx, EventStreamService::class.java)
            ctx.startForegroundService(i)
        }

        fun stop(ctx: android.content.Context) {
            val i = Intent(ctx, EventStreamService::class.java)
            ctx.stopService(i)
        }
    }
}

