package com.cuttle.companion.sse

import android.content.Context
import com.cuttle.companion.core.Prefs
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

object CuttleApi {
    private val client = OkHttpClient.Builder().build()
    private val json = "application/json; charset=utf-8".toMediaType()

    fun buildEventsUrl(ctx: Context): String {
        val base = Prefs.getBaseUrl(ctx).trim().trimEnd('/')
        val deviceId = Prefs.getOrCreateDeviceId(ctx)
        val token = Prefs.getToken(ctx)
        return "$base/api/mobile/events?device_id=${encode(deviceId)}&token=${encode(token)}"
    }

    fun submitReply(ctx: Context, interactionId: String, answer: String) {
        val base = Prefs.getBaseUrl(ctx).trim().trimEnd('/')
        val token = Prefs.getToken(ctx)
        val body = """{"token":${j(token)},"interaction_id":${j(interactionId)},"answer":${j(answer)}}"""
            .toRequestBody(json)
        val req = Request.Builder()
            .url("$base/api/mobile/reply")
            .post(body)
            .build()
        client.newCall(req).execute().use { _ -> }
    }

    private fun encode(s: String): String = java.net.URLEncoder.encode(s, "UTF-8")

    private fun j(s: String): String {
        val escaped = s
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        return "\"$escaped\""
    }
}

