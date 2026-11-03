package com.cuttle.mobile.media;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.pm.PackageManager;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.webkit.CookieManager;
import android.webkit.MimeTypeMap;
import android.webkit.URLUtil;
import android.widget.Toast;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;
import com.cuttle.mobile.notify.CuttleApi;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import okhttp3.Request;
import okhttp3.Response;

/**
 * Copy / save chat media from the Cuttle server to the phone.
 *
 * Android WebView ignores {@code <a download>} without a DownloadListener and
 * cannot write images through the async clipboard, so the web UI hands the
 * media URL to the shell. Only URLs on the configured Cuttle origin are
 * fetched (with the WebView's session cookie); results land on the system
 * clipboard (FileProvider content URI) or in MediaStore (Pictures/Movies/
 * Download, "Cuttle" subfolder). The user sees an Android toast either way.
 */
public final class MediaExport {
    public static final long MAX_COPY_BYTES = 25L * 1024 * 1024;
    public static final long MAX_SAVE_BYTES = 512L * 1024 * 1024;
    public static final int REQ_STORAGE = 1003;
    static final String SUBFOLDER = "Cuttle";

    private static final ExecutorService IO = Executors.newSingleThreadExecutor();

    private MediaExport() {}

    /** Copy an image on the Cuttle server to the system clipboard. */
    public static void copyImage(Activity activity, String baseUrl, String url) {
        if (!MediaUrls.sameOrigin(baseUrl, url)) {
            toast(activity, "Copy failed: image is not on the Cuttle server");
            return;
        }
        IO.execute(() -> {
            File clipDir = new File(activity.getCacheDir(), "clipboard");
            try {
                Fetched f = fetch(activity, url, "", MAX_COPY_BYTES);
                if (!f.mime.startsWith("image/")) {
                    f.file.delete();
                    throw new IOException("not an image");
                }
                clearDir(clipDir);
                if (!clipDir.isDirectory() && !clipDir.mkdirs()) throw new IOException("no cache dir");
                File dest = new File(clipDir, MediaUrls.safeFilename(f.name, f.extension));
                if (!f.file.renameTo(dest)) {
                    f.file.delete();
                    throw new IOException("could not stage image");
                }
                Uri uri = FileProvider.getUriForFile(
                    activity, activity.getPackageName() + ".fileprovider", dest);
                String mime = f.mime;
                activity.runOnUiThread(() -> {
                    try {
                        ClipboardManager cm =
                            (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
                        ClipData clip = new ClipData("Image", new String[] {mime}, new ClipData.Item(uri));
                        cm.setPrimaryClip(clip);
                        // Android 13+ shows its own clipboard confirmation.
                        if (Build.VERSION.SDK_INT < 33) {
                            Toast.makeText(activity, "Image copied", Toast.LENGTH_SHORT).show();
                        }
                    } catch (Exception e) {
                        Toast.makeText(activity, "Copy failed: " + reason(e), Toast.LENGTH_LONG).show();
                    }
                });
            } catch (Exception e) {
                toast(activity, "Copy failed: " + reason(e));
            }
        });
    }

    /**
     * Save a Cuttle-server file to shared storage. {@code filename},
     * {@code contentDisposition} and {@code mimeHint} are optional hints.
     */
    public static void save(
        Activity activity, String baseUrl, String url, String filename, String contentDisposition, String mimeHint
    ) {
        if (!MediaUrls.sameOrigin(baseUrl, url)) {
            toast(activity, "Save failed: file is not on the Cuttle server");
            return;
        }
        if (Build.VERSION.SDK_INT < 29
            && ContextCompat.checkSelfPermission(activity, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            activity.runOnUiThread(() -> {
                ActivityCompat.requestPermissions(
                    activity, new String[] {Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
                Toast.makeText(activity, "Allow storage access, then tap Save again", Toast.LENGTH_LONG).show();
            });
            return;
        }
        toast(activity, "Saving…");
        IO.execute(() -> {
            Fetched f = null;
            try {
                f = fetch(activity, url, mimeHint, MAX_SAVE_BYTES);
                String name = MediaUrls.safeFilename(
                    filename != null && !filename.trim().isEmpty()
                        ? filename
                        : URLUtil.guessFileName(MediaUrls.stripQuery(url), contentDisposition, f.mime),
                    f.extension);
                String where = Build.VERSION.SDK_INT >= 29
                    ? saveToMediaStore(activity, f, name)
                    : saveLegacy(activity, f, name);
                toast(activity, "Saved to " + where);
            } catch (Exception e) {
                toast(activity, "Save failed: " + reason(e));
            } finally {
                if (f != null) f.file.delete();
            }
        });
    }

    private static final class Fetched {
        File file;
        String mime;
        String extension;
        String name;
    }

    private static Fetched fetch(Context ctx, String url, String mimeHint, long maxBytes) throws IOException {
        Request.Builder rb = new Request.Builder().url(url).get();
        String cookie = CookieManager.getInstance().getCookie(url);
        if (cookie != null && !cookie.isEmpty()) rb.header("Cookie", cookie);
        File tmpDir = new File(ctx.getCacheDir(), "media-export");
        if (!tmpDir.isDirectory() && !tmpDir.mkdirs()) throw new IOException("no cache dir");
        try (Response res = CuttleApi.client().newCall(rb.build()).execute()) {
            if (!res.isSuccessful() || res.body() == null) throw new IOException("HTTP " + res.code());
            if (res.body().contentLength() > maxBytes) throw new IOException("file is too large");
            Fetched f = new Fetched();
            String ct = res.header("Content-Type", "");
            f.mime = MediaUrls.baseMime(ct);
            if (f.mime.isEmpty() || "application/octet-stream".equals(f.mime)) {
                String guessed = guessMime(url);
                f.mime = !guessed.isEmpty() ? guessed : MediaUrls.baseMime(mimeHint);
            }
            if (f.mime.isEmpty()) f.mime = "application/octet-stream";
            String ext = MimeTypeMap.getSingleton().getExtensionFromMimeType(f.mime);
            f.extension = ext != null ? ext : "";
            f.name = URLUtil.guessFileName(
                MediaUrls.stripQuery(url), res.header("Content-Disposition", null), f.mime);
            f.file = File.createTempFile("media-", ".part", tmpDir);
            long total = 0;
            try (InputStream in = res.body().byteStream(); OutputStream out = new FileOutputStream(f.file)) {
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) != -1) {
                    total += n;
                    if (total > maxBytes) throw new IOException("file is too large");
                    out.write(buf, 0, n);
                }
            } catch (IOException e) {
                f.file.delete();
                throw e;
            }
            return f;
        }
    }

    private static String saveToMediaStore(Context ctx, Fetched f, String name) throws IOException {
        Uri collection;
        String dir;
        if (f.mime.startsWith("image/")) {
            collection = MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
            dir = Environment.DIRECTORY_PICTURES;
        } else if (f.mime.startsWith("video/")) {
            collection = MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
            dir = Environment.DIRECTORY_MOVIES;
        } else {
            collection = MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
            dir = Environment.DIRECTORY_DOWNLOADS;
        }
        String relative = dir + "/" + SUBFOLDER;
        ContentResolver cr = ctx.getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        values.put(MediaStore.MediaColumns.MIME_TYPE, f.mime);
        values.put(MediaStore.MediaColumns.RELATIVE_PATH, relative);
        values.put(MediaStore.MediaColumns.IS_PENDING, 1);
        Uri item = cr.insert(collection, values);
        if (item == null) throw new IOException("could not create file");
        try {
            try (InputStream in = new FileInputStream(f.file); OutputStream out = cr.openOutputStream(item)) {
                if (out == null) throw new IOException("could not open file");
                copy(in, out);
            }
            ContentValues done = new ContentValues();
            done.put(MediaStore.MediaColumns.IS_PENDING, 0);
            cr.update(item, done, null, null);
        } catch (IOException | RuntimeException e) {
            cr.delete(item, null, null);
            throw e;
        }
        return relative;
    }

    @SuppressWarnings("deprecation")
    private static String saveLegacy(Context ctx, Fetched f, String name) throws IOException {
        String dir = f.mime.startsWith("image/") ? Environment.DIRECTORY_PICTURES
            : f.mime.startsWith("video/") ? Environment.DIRECTORY_MOVIES
            : Environment.DIRECTORY_DOWNLOADS;
        File folder = new File(Environment.getExternalStoragePublicDirectory(dir), SUBFOLDER);
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("could not create folder");
        File dest = MediaUrls.uniqueFile(folder, name);
        try (InputStream in = new FileInputStream(f.file); OutputStream out = new FileOutputStream(dest)) {
            copy(in, out);
        }
        MediaScannerConnection.scanFile(ctx, new String[] {dest.getAbsolutePath()}, new String[] {f.mime}, null);
        return dir + "/" + SUBFOLDER;
    }

    private static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
    }

    private static String guessMime(String url) {
        String ext = MimeTypeMap.getFileExtensionFromUrl(MediaUrls.stripQuery(url));
        if (ext == null || ext.isEmpty()) return "";
        String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext.toLowerCase(java.util.Locale.ROOT));
        return mime != null ? mime : "";
    }

    private static void clearDir(File dir) {
        File[] files = dir.listFiles();
        if (files == null) return;
        for (File f : files) f.delete();
    }

    private static String reason(Exception e) {
        String m = e.getMessage();
        return m == null || m.isEmpty() ? e.getClass().getSimpleName() : m;
    }

    private static void toast(Activity activity, String msg) {
        activity.runOnUiThread(() -> Toast.makeText(activity, msg, Toast.LENGTH_SHORT).show());
    }
}
