package com.cuttle.mobile.notify;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashSet;

/** Update decisions that can be tested without an Android device or installer. */
public final class ApkUpdatePolicy {
    public static final long MAX_APK_BYTES = 256L * 1024 * 1024;
    private static final long INSTALL_HINT_MS = 120_000;

    private ApkUpdatePolicy() {}

    public static boolean pendingInstall(boolean force, String remote, String pending,
                                         long startedAt, long now) {
        return !force && remote.equals(pending) && startedAt > 0
            && now >= startedAt && now - startedAt < INSTALL_HINT_MS;
    }

    public static void verifyChecksum(File file, String expected) throws Exception {
        if (expected == null || !expected.matches("[a-f0-9]{64}")) {
            throw new IOException("PC update metadata is incomplete. Update the PC first.");
        }
        if (file.length() <= 0 || file.length() > MAX_APK_BYTES) {
            throw new IOException("Invalid update APK size.");
        }
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream in = new FileInputStream(file)) {
            byte[] buf = new byte[8192];
            int count;
            while ((count = in.read(buf)) != -1) digest.update(buf, 0, count);
        }
        StringBuilder actual = new StringBuilder();
        for (byte value : digest.digest()) actual.append(String.format("%02x", value & 0xff));
        if (!actual.toString().equals(expected)) {
            throw new IOException("Downloaded APK does not match the PC manifest. Check for updates again.");
        }
    }

    public static void verifyPackage(String installedPackage, long installedVersion,
                                     String[] installedSigners, String updatePackage,
                                     long updateVersion, String[] updateSigners) throws IOException {
        if (!installedPackage.equals(updatePackage)) {
            throw new IOException("Update is for a different Android app.");
        }
        if (installedSigners.length == 0 || updateSigners.length == 0
            || !new HashSet<>(Arrays.asList(installedSigners))
                .equals(new HashSet<>(Arrays.asList(updateSigners)))) {
            throw new IOException("Update signing key does not match this installed app. "
                + "Ask the publisher for an update signed with the original key.");
        }
        if (updateVersion <= installedVersion) {
            throw new IOException("Update version is not newer than this installed app. "
                + "Ask the publisher to increment versionCode.");
        }
    }
}
