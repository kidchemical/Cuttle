package com.cuttle.mobile.media;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class MediaUrlsTest {
    @Test
    public void sameOriginMatchesConfiguredServerOnly() {
        String base = "https://192.168.1.20:8080";
        assertTrue(MediaUrls.sameOrigin(base, "https://192.168.1.20:8080/output/shared/a.png?download=1"));
        assertTrue(MediaUrls.sameOrigin("http://pc.local", "http://PC.local:80/output/shared/a.png"));
        assertFalse(MediaUrls.sameOrigin(base, "http://192.168.1.20:8080/output/shared/a.png"));
        assertFalse(MediaUrls.sameOrigin(base, "https://192.168.1.20:8443/output/shared/a.png"));
        assertFalse(MediaUrls.sameOrigin(base, "https://evil.example/output/shared/a.png"));
        assertFalse(MediaUrls.sameOrigin(base, "file:///sdcard/a.png"));
        assertFalse(MediaUrls.sameOrigin(base, "blob:https://192.168.1.20:8080/x"));
        assertFalse(MediaUrls.sameOrigin("", "https://192.168.1.20:8080/a.png"));
    }

    @Test
    public void safeFilenameStripsPathsAndAddsExtension() {
        assertEquals("shot.png", MediaUrls.safeFilename("../../shot.png", "png"));
        assertEquals("a_b.jpg", MediaUrls.safeFilename("a:b", "jpg"));
        assertEquals("cuttle-media.png", MediaUrls.safeFilename("", "png"));
        assertEquals("hidden.webp", MediaUrls.safeFilename("..hidden.webp", "png"));
        String longName = MediaUrls.safeFilename("x".repeat(300) + ".png", "png");
        assertEquals(120, longName.length());
        assertTrue(longName.endsWith(".png"));
    }

    @Test
    public void baseMimeAndStripQuery() {
        assertEquals("image/png", MediaUrls.baseMime("Image/PNG; charset=binary"));
        assertEquals("", MediaUrls.baseMime("garbage"));
        assertEquals("/output/a.png", MediaUrls.stripQuery("/output/a.png?download=1#x"));
    }
}
