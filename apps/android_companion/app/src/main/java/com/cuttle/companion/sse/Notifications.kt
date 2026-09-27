package com.cuttle.companion.sse

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.cuttle.companion.notify.ActionReceiver
import com.cuttle.companion.notify.Notifier
import com.cuttle.companion.ui.MainActivity
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

object Notifications {
    private val nextId = AtomicInteger(1000)

    fun showChatComplete(ctx: Context, obj: JSONObject) {
        val id = nextId.incrementAndGet()
        val pipeline = obj.optString("pipeline", "Cuttle")
        val text = obj.optString("response", "Done")

        val open = PendingIntent.getActivity(
            ctx,
            id,
            Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val n = NotificationCompat.Builder(ctx, Notifier.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_more)
            .setContentTitle("Cuttle complete ($pipeline)")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()

        NotificationManagerCompat.from(ctx).notify(id, n)
    }

    fun showInteraction(ctx: Context, obj: JSONObject) {
        val id = nextId.incrementAndGet()
        val question = obj.optString("question", "Question")
        val interactionId = obj.optString("interaction_id", "")
        if (interactionId.isBlank()) return

        val yes = action(ctx, id, interactionId, "Yes")
        val no = action(ctx, id, interactionId, "No")

        val open = PendingIntent.getActivity(
            ctx,
            id + 1,
            Intent(ctx, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val n = NotificationCompat.Builder(ctx, Notifier.CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setContentTitle("Cuttle needs input")
            .setContentText(question)
            .setStyle(NotificationCompat.BigTextStyle().bigText(question))
            .addAction(0, "Yes", yes)
            .addAction(0, "No", no)
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()

        NotificationManagerCompat.from(ctx).notify(id, n)
    }

    private fun action(ctx: Context, notificationId: Int, interactionId: String, answer: String): PendingIntent {
        val i = Intent(ctx, ActionReceiver::class.java).apply {
            action = ActionReceiver.ACTION_REPLY
            putExtra(ActionReceiver.EXTRA_INTERACTION_ID, interactionId)
            putExtra(ActionReceiver.EXTRA_ANSWER, answer)
            putExtra(ActionReceiver.EXTRA_NOTIFICATION_ID, notificationId)
        }
        return PendingIntent.getBroadcast(
            ctx,
            (notificationId.toString() + answer).hashCode(),
            i,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
    }
}

