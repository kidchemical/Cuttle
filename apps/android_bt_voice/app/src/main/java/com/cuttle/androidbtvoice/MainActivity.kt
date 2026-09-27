package com.cuttle.androidbtvoice

import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.Socket
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * Connects to Cuttle PC over TCP (WiFi). Sends mic PCM (16 kHz 16-bit mono), receives TTS and plays.
 * Handles dial command from PC (ACTION_DIAL).
 */
class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "CuttleLocalVoice"
        const val SAMPLE_RATE = 16000
        const val FRAME_TYPE_AUDIO_FROM_PHONE = 0
        const val FRAME_TYPE_TTS_TO_PHONE = 1
        const val FRAME_TYPE_CONTROL = 2
    }

    private var socket: Socket? = null
    private var outputStream: OutputStream? = null
    private val running = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var receiveThread: Thread? = null
    private var audioTrack: AudioTrack? = null
    private val audioTrackLock = Object()

    private lateinit var hostEdit: EditText
    private lateinit var portEdit: EditText
    private lateinit var connectButton: Button
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        hostEdit = findViewById(R.id.host_edit)
        portEdit = findViewById(R.id.port_edit)
        connectButton = findViewById(R.id.connect_button)
        statusText = findViewById(R.id.status_text)

        portEdit.setText("8888")

        connectButton.setOnClickListener {
            if (running.get()) disconnect() else connect()
        }
    }

    override fun onDestroy() {
        disconnect()
        super.onDestroy()
    }

    private fun connect() {
        val host = hostEdit.text.toString().trim()
        val portStr = portEdit.text.toString().trim()
        val port = portStr.toIntOrNull() ?: 8888
        if (host.isEmpty()) {
            Toast.makeText(this, "Enter PC IP address", Toast.LENGTH_SHORT).show()
            return
        }
        connectButton.isEnabled = false
        statusText.text = "Connecting..."
        thread {
            try {
                val s = Socket(host, port)
                s.soTimeout = 0
                socket = s
                outputStream = s.getOutputStream()
                running.set(true)
                runOnUiThread {
                    statusText.text = "Connected to $host:$port"
                    connectButton.text = "Disconnect"
                    connectButton.isEnabled = true
                }
                startRecordThread()
                startReceiveThread()
            } catch (e: IOException) {
                Log.e(TAG, "Connect failed", e)
                runOnUiThread {
                    statusText.text = "Failed: ${e.message}"
                    connectButton.text = "Connect"
                    connectButton.isEnabled = true
                }
            }
        }
    }

    private fun disconnect() {
        running.set(false)
        recordThread?.interrupt()
        recordThread = null
        receiveThread?.interrupt()
        receiveThread = null
        synchronized(audioTrackLock) {
            audioTrack?.release()
            audioTrack = null
        }
        try {
            socket?.close()
        } catch (_: Exception) {}
        socket = null
        outputStream = null
        statusText.text = "Disconnected"
        connectButton.text = "Connect"
        connectButton.isEnabled = true
    }

    private fun startRecordThread() {
        recordThread = thread {
            val bufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val recorder = AudioRecord(MediaRecorder.AudioSource.MIC, SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, bufferSize.coerceAtLeast(320 * 2))
            if (recorder.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord not initialized")
                return@thread
            }
            recorder.startRecording()
            val buf = ByteArray(320)
            try {
                while (running.get()) {
                    val n = recorder.read(buf, 0, buf.size)
                    if (n > 0) sendFrame(FRAME_TYPE_AUDIO_FROM_PHONE, buf, 0, n)
                }
            } finally {
                recorder.stop()
                recorder.release()
            }
        }
    }

    private fun sendFrame(type: Int, payload: ByteArray, offset: Int, length: Int) {
        val out = outputStream ?: return
        try {
            out.write(type)
            out.write(ByteBuffer.allocate(4).order(java.nio.ByteOrder.BIG_ENDIAN).putInt(length).array())
            out.write(payload, offset, length)
            out.flush()
        } catch (e: IOException) {
            if (running.get()) Log.e(TAG, "Send failed", e)
        }
    }

    private fun startReceiveThread() {
        receiveThread = thread {
            val input: InputStream = socket?.getInputStream() ?: return@thread
            val header = ByteArray(5)
            try {
                while (running.get()) {
                    if (!readFully(input, header, 5)) break
                    val type = header[0].toInt() and 0xFF
                    val length = ByteBuffer.wrap(header, 1, 4).int
                    if (length < 0 || length > 10 * 1024 * 1024) break
                    val payload = ByteArray(length)
                    if (!readFully(input, payload, length)) break
                    when (type) {
                        FRAME_TYPE_TTS_TO_PHONE -> playPcm(payload)
                        FRAME_TYPE_CONTROL -> handleControl(payload)
                    }
                }
            } catch (e: IOException) {
                if (running.get()) Log.e(TAG, "Receive error", e)
            }
        }
    }

    private fun readFully(input: InputStream, buf: ByteArray, n: Int): Boolean {
        var read = 0
        while (read < n) {
            val r = input.read(buf, read, n - read)
            if (r <= 0) return false
            read += r
        }
        return true
    }

    private fun playPcm(pcm: ByteArray) {
        if (pcm.isEmpty()) return
        synchronized(audioTrackLock) {
            var track = audioTrack
            if (track == null || track.playState != AudioTrack.PLAYSTATE_PLAYING) {
                track?.release()
                val bufferSize = AudioTrack.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
                track = AudioTrack.Builder()
                    .setAudioFormat(android.media.AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(SAMPLE_RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                    .setBufferSizeInBytes(bufferSize.coerceAtLeast(pcm.size * 2))
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
                audioTrack = track
                track.play()
            }
            track.write(pcm, 0, pcm.size)
        }
    }

    private fun handleControl(payload: ByteArray) {
        val msg = String(payload, Charsets.UTF_8).trim()
        if (msg.startsWith("dial:")) {
            val number = msg.removePrefix("dial:").trim()
            runOnUiThread {
                val intent = Intent(Intent.ACTION_DIAL).setData(Uri.parse("tel:$number"))
                startActivity(intent)
            }
        }
    }
}
