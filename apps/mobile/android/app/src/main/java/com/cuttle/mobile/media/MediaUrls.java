package com.cuttle.mobile.media;

import java.io.File;
import java.net.URI;
import java.util.Locale;

/** Pure URL / filename helpers for {@link MediaExport} (JVM unit-tested). */
public final class MediaUrls {
    private MediaUrls() {}

    /** True when {@code url} is http(s) on the same scheme/host/port as {@code base}. */
    public static boolean sameOrigin(String base, String url) {
        try {
            if (base == null || url == null || base.trim().isEmpty() || url.trim().isEmpty()) return false;
            URI b = new URI(base.trim());
            URI u = new URI(url.trim());
            String bs = lower(b.getScheme());
            String us = lower(u.getScheme());
            if (!("http".equals(us) || "https".equals(us)) || !us.equals(bs)) return false;
            if (b.getHost() == null || u.getHost() == null || !b.getHost().equalsIgnoreCase(u.getHost())) return false;
            return port(b) == port(u);
        } catch (Exception e) {
            return false;
        }
    }

    /** "image/png; charset=x" → "image/png" (lower-case); empty when absent. */
    public static String baseMime(String contentType) {
        if (contentType == null) return "";
        int semi = contentType.indexOf(';');
        String m = (semi >= 0 ? contentType.substring(0, semi) : contentType).trim().toLowerCase(Locale.ROOT);
        return m.contains("/") ? m : "";
    }

    public static String stripQuery(String url) {
        if (url == null) return "";
        int cut = url.length();
        int q = url.indexOf('?');
        int h = url.indexOf('#');
        if (q >= 0) cut = Math.min(cut, q);
        if (h >= 0) cut = Math.min(cut, h);
        return url.substring(0, cut);
    }

    /**
     * File-system safe display name: no path separators or control/reserved
     * characters, bounded length, and an extension (from {@code ext}) when the
     * name has none.
     */
    public static String safeFilename(String name, String ext) {
        String n = name == null ? "" : name;
        int slash = Math.max(n.lastIndexOf('/'), n.lastIndexOf('\\'));
        if (slash >= 0) n = n.substring(slash + 1);
        n = n.replaceAll("[\\x00-\\x1f\\x7f:*?\"<>|]", "_").trim();
        while (n.startsWith(".")) n = n.substring(1);
        if (n.isEmpty()) n = "cuttle-media";
        String e = ext == null ? "" : ext.trim().toLowerCase(Locale.ROOT);
        boolean hasExt = n.lastIndexOf('.') > 0 && n.lastIndexOf('.') < n.length() - 1;
        if (!hasExt && !e.isEmpty()) n = n + "." + e;
        if (n.length() > 120) {
            int dot = n.lastIndexOf('.');
            String suffix = dot > 0 && n.length() - dot <= 10 ? n.substring(dot) : "";
            n = n.substring(0, 120 - suffix.length()) + suffix;
        }
        return n;
    }

    /** {@code folder/name}, or {@code name (2).ext} … when taken. */
    public static File uniqueFile(File folder, String name) {
        File f = new File(folder, name);
        if (!f.exists()) return f;
        int dot = name.lastIndexOf('.');
        String stem = dot > 0 ? name.substring(0, dot) : name;
        String ext = dot > 0 ? name.substring(dot) : "";
        for (int i = 2; i < 1000; i++) {
            f = new File(folder, stem + " (" + i + ")" + ext);
            if (!f.exists()) return f;
        }
        return new File(folder, stem + "-" + System.currentTimeMillis() + ext);
    }

    private static String lower(String s) {
        return s == null ? "" : s.toLowerCase(Locale.ROOT);
    }

    private static int port(URI u) {
        if (u.getPort() >= 0) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }
}
