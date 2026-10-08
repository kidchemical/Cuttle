package com.cuttle.mobile.voice;

import org.junit.Test;
import static org.junit.Assert.*;

public class PcmClipTest {
    @Test public void rmsIsFullScaleFraction() {
        assertEquals(0.0, PcmClip.rms(new short[]{0, 0, 0}, 3), 1e-9);
        assertEquals(0.5, PcmClip.rms(new short[]{16384, -16384}, 2), 1e-9);
        assertEquals(0.0, PcmClip.rms(new short[0], 0), 1e-9);
    }

    @Test public void appendQueuesLevelsAndLittleEndianPcm() {
        PcmClip clip = new PcmClip(16000, 60);
        clip.append(new short[]{0x0102, -2}, 2);
        clip.append(new short[]{16384, -16384, 99}, 2);
        String[] levels = clip.drainLevels().split(",");
        assertEquals(2, levels.length);
        assertEquals(0.5, Double.parseDouble(levels[1]), 1e-4);
        assertEquals("", clip.drainLevels());
        byte[] pcm = clip.take();
        assertArrayEquals(new byte[]{0x02, 0x01, (byte) 0xfe, (byte) 0xff, 0x00, 0x40, 0x00, (byte) 0xc0}, pcm);
        assertEquals(0, clip.take().length);
    }

    @Test public void clipNeverGrowsPastItsCap() {
        PcmClip clip = new PcmClip(10, 1);
        clip.append(new short[8], 8);
        clip.append(new short[4], 4);
        assertEquals(8, clip.take().length);
    }

    @Test public void levelQueueIsBounded() {
        PcmClip clip = new PcmClip(16000, 60);
        for (int i = 0; i < PcmClip.MAX_LEVELS + 10; i++) clip.append(new short[]{1}, 1);
        assertEquals(PcmClip.MAX_LEVELS, clip.drainLevels().split(",").length);
    }

    @Test public void wavHeaderDescribes16BitMono() {
        byte[] wav = PcmClip.wav(new byte[]{1, 2, 3, 4}, 16000);
        assertEquals(48, wav.length);
        assertEquals("RIFF", new String(wav, 0, 4));
        assertEquals("WAVE", new String(wav, 8, 4));
        assertEquals("data", new String(wav, 36, 4));
        assertEquals(40, readInt(wav, 4));
        assertEquals(1, readShort(wav, 22));
        assertEquals(16000, readInt(wav, 24));
        assertEquals(32000, readInt(wav, 28));
        assertEquals(16, readShort(wav, 34));
        assertEquals(4, readInt(wav, 40));
        assertEquals(1, wav[44]);
    }

    private static int readInt(byte[] b, int at) {
        return (b[at] & 0xff) | (b[at + 1] & 0xff) << 8 | (b[at + 2] & 0xff) << 16 | (b[at + 3] & 0xff) << 24;
    }

    private static int readShort(byte[] b, int at) {
        return (b[at] & 0xff) | (b[at + 1] & 0xff) << 8;
    }
}
