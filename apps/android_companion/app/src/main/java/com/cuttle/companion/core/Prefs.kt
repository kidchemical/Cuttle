package com.cuttle.companion.core

import android.content.Context
import java.util.UUID

object Prefs {
    private const val FILE = "cuttle_companion"
    private const val KEY_BASE_URL = "base_url"
    private const val KEY_TOKEN = "token"
    private const val KEY_DEVICE_ID = "device_id"

    fun getBaseUrl(ctx: Context): String =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_BASE_URL, "") ?: ""

    fun setBaseUrl(ctx: Context, v: String) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(KEY_BASE_URL, v).apply()
    }

    fun getToken(ctx: Context): String =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(KEY_TOKEN, "") ?: ""

    fun setToken(ctx: Context, v: String) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(KEY_TOKEN, v).apply()
    }

    fun getOrCreateDeviceId(ctx: Context): String {
        val sp = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
        val existing = sp.getString(KEY_DEVICE_ID, null)
        if (!existing.isNullOrBlank()) return existing
        val id = "android-" + UUID.randomUUID().toString()
        sp.edit().putString(KEY_DEVICE_ID, id).apply()
        return id
    }
}

