package com.cuttle.mobile.voice;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.util.Base64;
import androidx.core.content.ContextCompat;

/**
 * Voice-mode microphone for the web UI. The page cannot record over LAN HTTP
 * (browsers only allow mic capture on secure origins), and Android's speech
 * recognizer chimes on every start/stop. This records continuously with
 * AudioRecord; the page polls levels, decides where phrases end and asks for
 * each phrase as a WAV clip, then uploads it for transcription itself.
 */
public final class NativeMic {
    public static final int SAMPLE_RATE = 16000;
    private static final int FRAME_SAMPLES = SAMPLE_RATE / 20;
    private static final int MAX_CLIP_SECONDS = 60;

    private final PcmClip clip = new PcmClip(SAMPLE_RATE, MAX_CLIP_SECONDS);
    private AudioRecord record;
    private Thread reader;
    private volatile boolean running;

    /** "ok", "denied" (no RECORD_AUDIO yet) or "error:<reason>". */
    public synchronized String start(Context ctx) {
        if (running) return "ok";
        if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            return "denied";
        }
        int min = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (min <= 0) return "error:unsupported audio format";
        AudioRecord r;
        try {
            r = new AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                Math.max(min, FRAME_SAMPLES * 2 * 4));
        } catch (SecurityException e) {
            return "denied";
        } catch (Exception e) {
            return "error:" + e.getMessage();
        }
        if (r.getState() != AudioRecord.STATE_INITIALIZED) {
            r.release();
            return "error:microphone busy or unavailable";
        }
        try {
            r.startRecording();
        } catch (Exception e) {
            r.release();
            return "error:" + e.getMessage();
        }
        clip.reset();
        record = r;
        running = true;
        reader = new Thread(() -> readLoop(r), "cuttle-native-mic");
        reader.start();
        return "ok";
    }

    private void readLoop(AudioRecord r) {
        short[] frame = new short[FRAME_SAMPLES];
        while (running) {
            int filled = 0;
            while (running && filled < FRAME_SAMPLES) {
                int n = r.read(frame, filled, FRAME_SAMPLES - filled);
                if (n < 0) {
                    running = false;
                    break;
                }
                if (n == 0) break;
                filled += n;
            }
            if (filled > 0) clip.append(frame, filled);
        }
    }

    public String levels() {
        return clip.drainLevels();
    }

    /** Base64 WAV of the audio since the last cut (empty when discarded or silent). */
    public String cut(boolean keep) {
        byte[] pcm = clip.take();
        if (!keep || pcm.length == 0) return "";
        return Base64.encodeToString(PcmClip.wav(pcm, SAMPLE_RATE), Base64.NO_WRAP);
    }

    public synchronized void stop() {
        running = false;
        Thread t = reader;
        reader = null;
        if (t != null) {
            try {
                t.join(500);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        AudioRecord r = record;
        record = null;
        if (r != null) {
            try {
                r.stop();
            } catch (Exception ignored) {}
            r.release();
        }
        clip.reset();
    }
}
