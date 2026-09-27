package com.cuttle.companion.notify

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationManagerCompat
import com.cuttle.companion.sse.CuttleApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val interactionId = intent.getStringExtra(EXTRA_INTERACTION_ID) ?: return
        val answer = intent.getStringExtra(EXTRA_ANSWER) ?: return
        val notifId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, 0)

        CoroutineScope(Dispatchers.IO).launch {
            try {
                CuttleApi.submitReply(context, interactionId, answer)
            } catch (_: Exception) {
            } finally {
                NotificationManagerCompat.from(context).cancel(notifId)
            }
        }
    }

    companion object {
        const val ACTION_REPLY = "com.cuttle.companion.ACTION_REPLY"
        const val EXTRA_INTERACTION_ID = "interaction_id"
        const val EXTRA_ANSWER = "answer"
        const val EXTRA_NOTIFICATION_ID = "notification_id"
    }
}

