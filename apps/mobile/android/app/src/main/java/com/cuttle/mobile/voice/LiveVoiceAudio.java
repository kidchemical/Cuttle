package com.cuttle.mobile.voice;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.util.Base64;
import androidx.core.content.ContextCompat;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;
import org.json.JSONObject;

/** Continuous duplex PCM for server-owned voice when LAN HTTP cannot use getUserMedia. */
public final class LiveVoiceAudio {
    private static final int RATE = 24000;
    // Providers stream replies faster than real time; the buffer must hold a
    // whole reply. Bounded by audio duration, not chunk count (chunk sizes vary).
    private static final long MAX_BUFFERED_BYTES = RATE * 2L * 180;
    private final String engine;
    private final Consumer<String> events;
    private LinkedBlockingQueue<byte[]> playback = new LinkedBlockingQueue<>();
    private final AtomicLong buffered = new AtomicLong();
    private volatile long clearEpoch;
    private final OkHttpClient client = new OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.SECONDS).pingInterval(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false).build();
    private WebSocket socket;
    private AudioRecord recorder;
    private AudioTrack speaker;
    private AudioManager audioManager;
    private AcousticEchoCanceler echo;
    private int previousMode;
    private boolean previousSpeaker;
    private volatile boolean running, closing, muted;
    private volatile long generation;
    private final AudioManager.OnAudioFocusChangeListener focus = change -> { if (change < 0) stop(); };

    public LiveVoiceAudio(Consumer<String> events) { this("live", events); }

    /** @param engine "live" (GPT-Live) or "realtime" (OpenAI Realtime). */
    public LiveVoiceAudio(String engine, Consumer<String> events) {
        this.engine = "realtime".equals(engine) ? "realtime" : "live";
        this.events = events;
    }

    public synchronized String start(Context context, String base, String cookie, String id) {
        if (!id.matches("[a-f0-9]{32}")) return "error:invalid voice session";
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) return "denied";
        if (socket != null) {
            if (!closing) return "error:a voice connection is already active";
            // A retry may arrive after the server confirmed closure but before
            // OkHttp's close callback. Detach that transport before replacing it.
            WebSocket old = socket;
            cleanup();
            old.cancel();
        }
        final long own = ++generation;
        playback = new LinkedBlockingQueue<>();
        buffered.set(0);
        String url = base.replaceAll("/+$", "").replaceFirst("^https:", "wss:")
            .replaceFirst("^http:", "ws:") + "/api/voice-stt/" + engine + "/sessions/" + id + "/socket";
        closing = false; muted = false;
        socket = client.newWebSocket(new Request.Builder().url(url)
            .header("Cookie", cookie == null ? "" : cookie).build(), new WebSocketListener() {
                @Override public void onMessage(WebSocket ws, String text) {
                    synchronized (LiveVoiceAudio.this) {
                    if (generation != own || socket != ws) return;
                    try {
                        JSONObject event = new JSONObject(text);
                        String type = event.optString("type");
                        if ("session.started".equals(type)) startAudio(context, own);
                        if ("session.output_audio.delta".equals(type) && running) {
                            byte[] pcm = Base64.decode(event.optString("delta"), Base64.DEFAULT);
                            if (pcm.length <= RATE * 2 && pcm.length % 2 == 0
                                    && buffered.get() + pcm.length <= MAX_BUFFERED_BYTES) {
                                buffered.addAndGet(pcm.length);
                                playback.offer(pcm);
                            }
                        } else if ("session.output_audio.clear".equals(type)) {
                            clearPlayback();
                        } else if (!"session.input_audio.append".equals(type)) events.accept(text);
                        if ("session.closed".equals(type)) { cleanup(); ws.close(1000, "Voice ended"); }
                    } catch (Exception exc) { fail("Could not process native voice audio."); }
                    }
                }
                @Override public void onFailure(WebSocket ws, Throwable error, Response response) {
                    synchronized (LiveVoiceAudio.this) {
                        if (generation != own || socket != ws) return;
                        cleanup();
                        events.accept("{\"type\":\"cuttle.error\",\"message\":\"Native voice connection failed; check the host connection and reconnect.\"}");
                    }
                }
                @Override public void onClosed(WebSocket ws, int code, String reason) {
                    synchronized (LiveVoiceAudio.this) {
                        if (generation == own && socket == ws) cleanup();
                    }
                }
            });
        return "ok";
    }

    private synchronized void startAudio(Context context, long own) {
        if (running || closing) return;
        try {
            audioManager = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            previousMode = audioManager.getMode(); previousSpeaker = audioManager.isSpeakerphoneOn();
            if (audioManager.requestAudioFocus(focus,
                AudioManager.STREAM_VOICE_CALL, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
                throw new IllegalStateException("Audio focus unavailable");
            audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
            if (!audioManager.isBluetoothScoOn() && !audioManager.isWiredHeadsetOn()) audioManager.setSpeakerphoneOn(true);
            int min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min, 4800));
            if (recorder.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("Microphone unavailable");
            if (AcousticEchoCanceler.isAvailable()) { echo = AcousticEchoCanceler.create(recorder.getAudioSessionId()); if (echo != null) echo.setEnabled(true); }
            speaker = new AudioTrack.Builder().setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(4800).build();
            recorder.startRecording(); speaker.play(); running = true;
            new Thread(() -> capture(own), "cuttle-live-capture").start();
            LinkedBlockingQueue<byte[]> ownPlayback = playback;
            new Thread(() -> play(own, ownPlayback), "cuttle-live-playback").start();
        } catch (Exception exc) { fail("Native microphone or speaker is unavailable."); }
    }

    private void capture(long own) {
        byte[] frame = new byte[960]; // 20 ms, mono PCM16 at 24 kHz
        while (running && generation == own) {
            AudioRecord input = recorder; WebSocket connection = socket;
            if (input == null || connection == null) break;
            try {
                int size = input.read(frame, 0, frame.length);
                if (size < 0) { fail(own, "Native microphone stopped unexpectedly."); break; }
                if (size > 0 && generation == own && !muted && !closing) {
                    // A transient backlog (host busy reading a long turn) must not
                    // end the call: drop this frame and keep the socket alive.
                    if (connection.queueSize() > 96000) continue;
                    JSONObject event = new JSONObject();event.put("type", "session.input_audio.append");
                    event.put("audio", Base64.encodeToString(frame, 0, size, Base64.NO_WRAP));
                    connection.send(event.toString());
                }
            } catch (Exception exc) { if (running) fail(own, "Native microphone stopped unexpectedly."); break; }
        }
    }

    private void play(long own, LinkedBlockingQueue<byte[]> ownPlayback) {
        while (running && generation == own) {
            try {
                byte[] frame = ownPlayback.poll(200, TimeUnit.MILLISECONDS);
                if (generation != own) break;
                if (frame != null) buffered.addAndGet(-frame.length);
                AudioTrack output = speaker;
                if (frame == null || output == null) continue;
                long epoch = clearEpoch;
                int offset = 0;
                while (running && generation == own && epoch == clearEpoch && offset < frame.length) {
                    int written = output.write(frame, offset, frame.length-offset, AudioTrack.WRITE_BLOCKING);
                    if (written <= 0) break;
                    offset += written;
                }
            } catch (Exception ignored) { break; }
        }
    }

    /** Barge-in: drop the cancelled reply's queued and device-buffered audio. */
    private synchronized void clearPlayback() {
        clearEpoch++;
        playback.clear();
        buffered.set(0);
        AudioTrack output = speaker;
        if (output != null) {
            try { output.pause(); output.flush(); output.play(); } catch (Exception ignored) {}
        }
    }

    public synchronized void mute(boolean value) {
        muted = value;
        if (socket != null) socket.send("{\"type\":\"session.input_audio." + (value ? "mute" : "unmute") + "\"}");
    }

    public synchronized void stop() {
        if (closing) return;
        closing = true; running = false; playback.clear();
        if (recorder != null) try { recorder.stop(); } catch (Exception ignored) {}
        if (socket != null) {
            final WebSocket old = socket;
            old.send("{\"type\":\"session.close\"}");
            new Thread(() -> { try { Thread.sleep(8000); } catch (InterruptedException ignored) {} synchronized (this) { if (socket == old) { old.close(1000,"Voice ended"); cleanup(); } } }, "cuttle-live-close").start();
        } else cleanup();
    }

    private synchronized void fail(long own, String message) {
        if (generation == own) fail(message);
    }

    private synchronized void fail(String message) {
        events.accept("{\"type\":\"cuttle.error\",\"message\":" + JSONObject.quote(message) + "}");
        stop();
    }

    private synchronized void cleanup() {
        running = false; playback.clear();
        if (recorder != null) { try { recorder.stop(); } catch (Exception ignored) {} recorder.release(); recorder = null; }
        if (speaker != null) { try { speaker.stop(); } catch (Exception ignored) {} speaker.release(); speaker = null; }
        if (echo != null) { echo.release(); echo = null; }
        if (audioManager != null) { audioManager.abandonAudioFocus(focus); audioManager.setSpeakerphoneOn(previousSpeaker); audioManager.setMode(previousMode); audioManager = null; }
        socket = null;
    }
}
