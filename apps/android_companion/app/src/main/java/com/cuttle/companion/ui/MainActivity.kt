package com.cuttle.companion.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.cuttle.companion.core.Prefs
import com.cuttle.companion.core.validateBaseUrl
import com.cuttle.companion.databinding.ActivityMainBinding
import com.cuttle.companion.sse.EventStreamService

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        maybeRequestNotificationPermission()

        val deviceId = Prefs.getOrCreateDeviceId(this)
        binding.deviceId.text = deviceId

        binding.baseUrl.setText(Prefs.getBaseUrl(this))
        binding.token.setText(Prefs.getToken(this))

        binding.startBtn.setOnClickListener {
            val rawBase = binding.baseUrl.text?.toString() ?: ""
            val problem = validateBaseUrl(rawBase)
            if (problem != null) {
                binding.status.text = problem
                return@setOnClickListener
            }
            // Stored verbatim (trimmed of trailing slashes only): the scheme
            // and port the user typed are used as-is, never rewritten.
            Prefs.setBaseUrl(this, rawBase.trim())
            Prefs.setToken(this, binding.token.text?.toString() ?: "")
            EventStreamService.start(this)
            binding.status.text = "Started"
        }

        binding.stopBtn.setOnClickListener {
            EventStreamService.stop(this)
            binding.status.text = "Stopped"
        }
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < 33) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
    }
}

