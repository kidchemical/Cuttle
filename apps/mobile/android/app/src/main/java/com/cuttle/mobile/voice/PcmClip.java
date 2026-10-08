package com.cuttle.mobile.voice;

import java.io.ByteArrayOutputStream;
import java.util.ArrayDeque;
import java.util.Locale;

/**
 * Audio captured since the last phrase cut, plus the per-frame levels the
 * page's pause detector polls. Pure Java (no Android types) so it unit-tests.
 */
public final class PcmClip {
    /** Levels not yet polled; the page polls every 50 ms, so this is only a backstop. */
    static final int MAX_LEVELS = 400;

    private final int sampleRate;
    private final int maxBytes;
    private final ByteArrayOutputStream pcm = new ByteArrayOutputStream();
    private final ArrayDeque<Float> levels = new ArrayDeque<>();

    public PcmClip(int sampleRate, int maxSeconds) {
        this.sampleRate = sampleRate;
        this.maxBytes = sampleRate * 2 * maxSeconds;
    }

    /** Append one frame of 16-bit mono samples and queue its level. */
    public synchronized void append(short[] samples, int count) {
        if (pcm.size() + count * 2 > maxBytes) pcm.reset();
        byte[] out = new byte[count * 2];
        for (int i = 0; i < count; i++) {
            out[i * 2] = (byte) (samples[i] & 0xff);
            out[i * 2 + 1] = (byte) ((samples[i] >> 8) & 0xff);
        }
        pcm.write(out, 0, out.length);
        if (levels.size() >= MAX_LEVELS) levels.removeFirst();
        levels.addLast((float) rms(samples, count));
    }

    /** Comma-separated levels since the last call, oldest first. */
    public synchronized String drainLevels() {
        StringBuilder sb = new StringBuilder();
        while (!levels.isEmpty()) {
            if (sb.length() > 0) sb.append(',');
            sb.append(String.format(Locale.ROOT, "%.5f", levels.removeFirst()));
        }
        return sb.toString();
    }

    /** Raw PCM since the last cut; starts a fresh clip. */
    public synchronized byte[] take() {
        byte[] out = pcm.toByteArray();
        pcm.reset();
        return out;
    }

    public synchronized void reset() {
        pcm.reset();
        levels.clear();
    }

    public int sampleRate() {
        return sampleRate;
    }

    /** Root-mean-square level, 0..1 of full scale (same scale as WebAudio floats). */
    public static double rms(short[] samples, int count) {
        if (count <= 0) return 0;
        double sum = 0;
        for (int i = 0; i < count; i++) {
            double v = samples[i] / 32768.0;
            sum += v * v;
        }
        return Math.sqrt(sum / count);
    }

    /** 16-bit mono PCM wrapped in a WAV header. */
    public static byte[] wav(byte[] pcm, int sampleRate) {
        int dataLen = pcm.length;
        byte[] out = new byte[44 + dataLen];
        writeAscii(out, 0, "RIFF");
        writeInt(out, 4, 36 + dataLen);
        writeAscii(out, 8, "WAVE");
        writeAscii(out, 12, "fmt ");
        writeInt(out, 16, 16);
        writeShort(out, 20, 1);
        writeShort(out, 22, 1);
        writeInt(out, 24, sampleRate);
        writeInt(out, 28, sampleRate * 2);
        writeShort(out, 32, 2);
        writeShort(out, 34, 16);
        writeAscii(out, 36, "data");
        writeInt(out, 40, dataLen);
        System.arraycopy(pcm, 0, out, 44, dataLen);
        return out;
    }

    private static void writeAscii(byte[] b, int at, String s) {
        for (int i = 0; i < s.length(); i++) b[at + i] = (byte) s.charAt(i);
    }

    private static void writeInt(byte[] b, int at, int v) {
        b[at] = (byte) (v & 0xff);
        b[at + 1] = (byte) ((v >> 8) & 0xff);
        b[at + 2] = (byte) ((v >> 16) & 0xff);
        b[at + 3] = (byte) ((v >> 24) & 0xff);
    }

    private static void writeShort(byte[] b, int at, int v) {
        b[at] = (byte) (v & 0xff);
        b[at + 1] = (byte) ((v >> 8) & 0xff);
    }
}
