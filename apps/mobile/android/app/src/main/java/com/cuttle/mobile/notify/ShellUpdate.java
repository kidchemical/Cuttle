package com.cuttle.mobile.notify;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import androidx.appcompat.app.AlertDialog;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.FileProvider;
import com.cuttle.mobile.MainActivity;
import java.io.File;
import java.io.InputStream;
import java.io.IOException;
import java.io.ByteArrayOutputStream;
import java.security.MessageDigest;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.ZipFile;
import java.util.zip.ZipEntry;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/**
 * LAN APK updates — same idea as Electron replacing app.asar from the PC.
 * Android still shows the system Install prompt (no silent replace).
 */
public final class ShellUpdate {
    private static final String TAG = "CuttleShellUpdate";
    private static final String PREFS = "cuttle_shell_update";
    private static final String KEY_SKIPPED = "skipped_hash";
    private static final String KEY_NOTIFIED = "notified_hash";
    private static final String KEY_INSTALLING = "installing_hash";
    private static final String KEY_INSTALL_STARTED = "install_started_at";
    private static final String KEY_DOWNLOADED = "downloaded_hash";
    private static final String KEY_DOWNLOAD_SHA256 = "downloaded_sha256";
    private static final AtomicBoolean checkInFlight = new AtomicBoolean();
    private static final int NOTIF_ID = 42;
    public static final String EXTRA_INSTALL = "cuttle_install_update";

    private static boolean checkStarted;
    private static long lastCheckAt;
    private static volatile boolean updateAvailable;
    private static volatile String lastRemoteHash = "";

    private ShellUpdate() {}

    public static boolean isUpdateAvailable() {
        return updateAvailable;
    }

    public static void checkOnStart(Context ctx) {
        check(ctx, false);
    }

    public static void checkNow(Context ctx) {
        check(ctx, true);
    }

    private static void check(Context ctx, boolean force) {
        MainActivity activity = ctx instanceof MainActivity ? (MainActivity) ctx : null;
        if (NotifyPrefs.getBaseUrl(ctx).trim().isEmpty()) {
            notifyJs(activity, false, "error", "Save your PC IP first, then try again.");
            return;
        }
        long now = System.currentTimeMillis();
        if (!force && checkStarted && now - lastCheckAt < 120_000) {
            return;
        }
        if (!checkInFlight.compareAndSet(false, true)) {
            if (force) notifyJs(activity, false, "checking", "Update check is already running…");
            return;
        }
        checkStarted = true;
        lastCheckAt = now;
        Context app = ctx.getApplicationContext();
        if (force) {
            notifyJs(activity, false, "checking", "Checking the PC for a newer app…");
        }
        new Thread(() -> runCheck(app, activity, force), "cuttle-apk-check").start();
    }

    public static void installIfRequested(MainActivity activity, Intent intent) {
        if (intent == null || !intent.getBooleanExtra(EXTRA_INSTALL, false)) {
            return;
        }
        intent.removeExtra(EXTRA_INSTALL);
        installDownloaded(activity);
    }

    private static void runCheck(Context app, MainActivity activityOrNull, boolean force) {
        try {
            JSONObject remote = CuttleApi.getJson(app, "/api/mobile/android");
            if (remote.optInt("_http", 0) != 200 || !remote.optBoolean("ok", false)) {
                notifyJs(activityOrNull, false, "error", "Could not reach the PC update API.");
                return;
            }
            String remoteHash = remote.optString("hash", "");
            boolean artifact = remote.optBoolean("artifact", false);
            boolean building = remote.optBoolean("building", false);
            String localHash = localShellHash(app);
            if (remoteHash.isEmpty()) {
                clearAvailable(app);
                notifyJs(activityOrNull, false, "error", "PC did not report an app hash.");
                return;
            }
            if (localHash.isEmpty()) {
                clearAvailable(app);
                notifyJs(
                    activityOrNull,
                    false,
                    "error",
                    "Could not read this install's version. Reinstall from the PC if updates keep appearing."
                );
                return;
            }
            if (!artifact) {
                boolean newer = !localHash.isEmpty() && !remoteHash.equals(localHash);
                if (building) {
                    notifyJs(
                        activityOrNull,
                        false,
                        "checking",
                        "PC is rebuilding the phone APK. Check again in about a minute."
                    );
                    updateAvailable = false;
                    return;
                }
                notifyJs(
                    activityOrNull,
                    false,
                    newer ? "error" : "up_to_date",
                    newer
                        ? remote.optString("rebuildError", "A matching signed phone APK is not published on the PC yet.")
                        : "Phone app is up to date."
                );
                updateAvailable = false;
                return;
            }
            lastRemoteHash = remoteHash;
            if (remoteHash.equals(localHash)) {
                clearAvailable(app);
                notifyJs(activityOrNull, false, "up_to_date", "Phone app is up to date.");
                return;
            }
            SharedPreferences prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
            if (ApkUpdatePolicy.pendingInstall(force, remoteHash, prefs.getString(KEY_INSTALLING, ""),
                    prefs.getLong(KEY_INSTALL_STARTED, 0), System.currentTimeMillis())) {
                // User already opened the system installer for this hash.
                // Explicit checks must retry after cancellation or install failure.
                updateAvailable = true;
                notifyJs(
                    activityOrNull,
                    true,
                    "installing",
                    "Android installer was opened. Finish it or tap Check again to retry."
                );
                return;
            }
            updateAvailable = true;
            notifyJs(activityOrNull, true, "downloading", "Update found — downloading from the PC…", remoteHash);
            if (!force && remoteHash.equals(prefs.getString(KEY_SKIPPED, ""))) {
                notifyJs(activityOrNull, true, "available", "Update available. Tap install when you are ready.", remoteHash);
                return;
            }
            File apk = apkFile(app);
            File candidate = new File(app.getCacheDir(), "cuttle-update-candidate.apk");
            String path = remote.optString("downloadPath", "");
            String checksum = remote.optString("sha256", "");
            if (!path.startsWith("/api/mobile/android/app-debug.apk?sha256=")) {
                throw new IOException("PC update metadata is incomplete. Update the PC first.");
            }
            try {
                CuttleApi.download(app, path, candidate);
                verifyDownloaded(app, candidate, remoteHash, checksum);
                if (!candidate.renameTo(apk)) throw new IOException("Could not cache the verified update APK.");
            } finally {
                candidate.delete();
            }
            prefs.edit().putString(KEY_DOWNLOADED, remoteHash)
                .putString(KEY_DOWNLOAD_SHA256, checksum).apply();
            notifyJs(activityOrNull, true, "available", "Update downloaded — Android will ask you to install.", remoteHash);
            notifyReady(app, remoteHash);
            MainActivity live = activityOrNull;
            final boolean userAsked = force;
            new Handler(Looper.getMainLooper()).post(() -> {
                if (userAsked) {
                    installDownloaded(live != null ? live : app, remoteHash);
                    return;
                }
                promptInstall(live, app, remoteHash);
            });
        } catch (Exception e) {
            Log.w(TAG, "update check failed", e);
            String msg = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            notifyJs(activityOrNull, false, "error", "Update check failed: " + msg);
        } finally {
            checkInFlight.set(false);
        }
    }

    private static void notifyJs(MainActivity activity, boolean available) {
        notifyJs(activity, available, available ? "available" : "up_to_date", "", "");
    }

    private static void notifyJs(MainActivity activity, boolean available, String status, String message) {
        notifyJs(activity, available, status, message, "");
    }

    private static void notifyJs(
        MainActivity activity,
        boolean available,
        String status,
        String message,
        String hash
    ) {
        if (activity == null) {
            return;
        }
        activity.dispatchApkUpdateToWeb(available, status, message, hash);
    }

    private static void clearAvailable(Context app) {
        updateAvailable = false;
        try {
            NotificationManagerCompat.from(app).cancel(NOTIF_ID);
        } catch (Exception ignored) {
        }
        app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_INSTALLING)
            .remove(KEY_INSTALL_STARTED)
            .apply();
    }

    private static void promptInstall(MainActivity activity, Context app, String remoteHash) {
        if (activity == null || activity.isFinishing()) {
            return;
        }
        // Cuttle UI (app_shell) shows its own phone-only rail icon + popup.
        if (activity.isShowingCuttleUi()) {
            return;
        }
        new AlertDialog.Builder(activity)
            .setTitle("Cuttle update")
            .setMessage("A newer phone app is on your PC. Install over Wi‑Fi? No Chrome needed — Android will ask you to confirm.")
            .setPositiveButton("Install", (d, w) -> installDownloaded(activity, remoteHash))
            .setNegativeButton("Later", (d, w) -> {
                app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                    .edit()
                    .putString(KEY_SKIPPED, remoteHash)
                    .apply();
            })
            .show();
    }

    public static void installDownloaded(Context ctx) {
        installDownloaded(ctx, lastRemoteHash);
    }

    public static void installDownloaded(Context ctx, String remoteHash) {
        MainActivity activity = ctx instanceof MainActivity ? (MainActivity) ctx : null;
        SharedPreferences prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String hash = remoteHash != null && !remoteHash.isEmpty()
            ? remoteHash : prefs.getString(KEY_DOWNLOADED, "");
        File apk = apkFile(ctx);
        try {
            verifyDownloaded(ctx, apk, hash, prefs.getString(KEY_DOWNLOAD_SHA256, ""));
            if (Build.VERSION.SDK_INT >= 26 && !ctx.getPackageManager().canRequestPackageInstalls()) {
                Intent perm = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES);
                perm.setData(Uri.parse("package:" + ctx.getPackageName()));
                perm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                ctx.startActivity(perm);
                notifyJs(activity, true, "available", "Allow app installs, then tap Install again.", hash);
                return;
            }
            Uri uri = FileProvider.getUriForFile(ctx, ctx.getPackageName() + ".fileprovider", apk);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(uri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            ctx.startActivity(intent);
            // Opening the installer is a short-lived hint, never proof of installation.
            prefs.edit().putString(KEY_INSTALLING, hash)
                .putLong(KEY_INSTALL_STARTED, System.currentTimeMillis()).apply();
            NotificationManagerCompat.from(ctx).cancel(NOTIF_ID);
        } catch (Exception error) {
            prefs.edit().remove(KEY_INSTALLING).remove(KEY_INSTALL_STARTED).apply();
            notifyJs(activity, true, "error", "Could not start update: " + error.getMessage(), hash);
            Log.w(TAG, "installer launch failed", error);
        }
    }

    @SuppressWarnings("deprecation")
    private static String[] signers(PackageInfo info) throws Exception {
        Signature[] certificates;
        if (Build.VERSION.SDK_INT >= 28) {
            if (info.signingInfo == null) throw new IOException("APK signing information is missing.");
            certificates = info.signingInfo.getApkContentsSigners();
        } else {
            certificates = info.signatures;
        }
        if (certificates == null) return new String[0];
        String[] hashes = new String[certificates.length];
        for (int i = 0; i < certificates.length; i++) {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(certificates[i].toByteArray());
            StringBuilder hex = new StringBuilder();
            for (byte value : digest) hex.append(String.format("%02x", value & 0xff));
            hashes[i] = hex.toString();
        }
        return hashes;
    }

    @SuppressWarnings("deprecation")
    private static void verifyDownloaded(Context ctx, File file, String hash, String checksum) throws Exception {
        ApkUpdatePolicy.verifyChecksum(file, checksum);
        try (ZipFile archive = new ZipFile(file)) {
            ZipEntry entry = archive.getEntry("assets/cuttle-mobile-build.json");
            if (entry == null || entry.getSize() > 16384) throw new IOException("APK build identity missing.");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (InputStream in = archive.getInputStream(entry)) {
                byte[] buffer = new byte[1024];
                int count;
                while ((count = in.read(buffer)) != -1) {
                    if (bytes.size() + count > 16384) throw new IOException("Invalid APK build identity.");
                    bytes.write(buffer, 0, count);
                }
            }
            String embedded = new JSONObject(bytes.toString("UTF-8")).optString("hash", "");
            if (hash.isEmpty() || !hash.equals(embedded)) {
                throw new IOException("Downloaded APK has a different build hash. Check for updates again.");
            }
        }
        PackageManager pm = ctx.getPackageManager();
        int flags = Build.VERSION.SDK_INT >= 28 ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo installed = pm.getPackageInfo(ctx.getPackageName(), flags);
        PackageInfo candidate = pm.getPackageArchiveInfo(file.getAbsolutePath(), flags);
        if (candidate == null) throw new IOException("Downloaded file is not a valid Android APK.");
        long installedVersion = Build.VERSION.SDK_INT >= 28 ? installed.getLongVersionCode() : installed.versionCode;
        long candidateVersion = Build.VERSION.SDK_INT >= 28 ? candidate.getLongVersionCode() : candidate.versionCode;
        ApkUpdatePolicy.verifyPackage(installed.packageName, installedVersion, signers(installed),
            candidate.packageName, candidateVersion, signers(candidate));
    }

    private static void notifyReady(Context ctx, String remoteHash) {
        if (NotifyController.isAppInForeground()) {
            return;
        }
        SharedPreferences prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (remoteHash != null && remoteHash.equals(prefs.getString(KEY_NOTIFIED, ""))) {
            return;
        }
        if (remoteHash != null && !remoteHash.isEmpty()) {
            prefs.edit().putString(KEY_NOTIFIED, remoteHash).apply();
        }
        Notifier.ensureChannels(ctx);
        Intent open = new Intent(ctx, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
        open.putExtra(EXTRA_INSTALL, true);
        PendingIntent pi = PendingIntent.getActivity(
            ctx,
            NOTIF_ID,
            open,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT
        );
        NotificationCompat.Builder n = new NotificationCompat.Builder(ctx, Notifier.EVENTS_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle("Cuttle update ready")
            .setContentText("Tap to install the new phone app over Wi‑Fi")
            .setAutoCancel(true)
            .setContentIntent(pi)
            .setPriority(NotificationCompat.PRIORITY_HIGH);
        try {
            NotificationManagerCompat.from(ctx).notify(NOTIF_ID, n.build());
        } catch (SecurityException ignored) {
        }
    }

    public static File apkFile(Context ctx) {
        return new File(ctx.getCacheDir(), "cuttle-update.apk");
    }

    public static String localShellHash(Context ctx) {
        try (InputStream in = ctx.getAssets().open("cuttle-mobile-build.json")) {
            byte[] buf = new byte[4096];
            int n = in.read(buf);
            if (n <= 0) {
                return "";
            }
            JSONObject obj = new JSONObject(new String(buf, 0, n, StandardCharsets.UTF_8));
            return obj.optString("hash", "");
        } catch (Exception e) {
            return "";
        }
    }
}
