package com.cuttle.mobile.notify;

import org.junit.Test;
import java.io.File;
import java.nio.file.Files;
import static org.junit.Assert.*;

public class ApkUpdatePolicyTest {
    @Test public void installerHintExpiresAndExplicitChecksRetry() {
        assertTrue(ApkUpdatePolicy.pendingInstall(false, "a", "a", 1000, 2000));
        assertFalse(ApkUpdatePolicy.pendingInstall(true, "a", "a", 1000, 2000));
        assertFalse(ApkUpdatePolicy.pendingInstall(false, "a", "a", 0, 2000));
        assertFalse(ApkUpdatePolicy.pendingInstall(false, "a", "a", 1000, 121000));
        assertFalse(ApkUpdatePolicy.pendingInstall(false, "a", "a", 3000, 2000));
        assertFalse(ApkUpdatePolicy.pendingInstall(false, "b", "a", 1000, 2000));
    }
    @Test public void compatibleUpdatePasses() throws Exception {
        ApkUpdatePolicy.verifyPackage("com.cuttle.mobile", 1, new String[]{"key"},
            "com.cuttle.mobile", 2, new String[]{"key"});
    }
    @Test public void wrongPackageKeyAndVersionFail() {
        assertThrows(java.io.IOException.class, () -> ApkUpdatePolicy.verifyPackage("app", 1,
            new String[]{"key"}, "other", 2, new String[]{"key"}));
        assertThrows(java.io.IOException.class, () -> ApkUpdatePolicy.verifyPackage("app", 1,
            new String[]{"key"}, "app", 2, new String[]{"other"}));
        for (long version : new long[]{0, 1}) {
            assertThrows(java.io.IOException.class, () -> ApkUpdatePolicy.verifyPackage("app", 1,
                new String[]{"key"}, "app", version, new String[]{"key"}));
        }
    }
    @Test public void checksumRejectsCorruptionAndMissingMetadata() throws Exception {
        File file = File.createTempFile("apk-policy", ".apk");
        try {
            Files.write(file.toPath(), "abc".getBytes("UTF-8"));
            ApkUpdatePolicy.verifyChecksum(file, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
            assertThrows(java.io.IOException.class, () -> ApkUpdatePolicy.verifyChecksum(file, "0".repeat(64)));
            assertThrows(java.io.IOException.class, () -> ApkUpdatePolicy.verifyChecksum(file, null));
        } finally { file.delete(); }
    }
}
