package com.cuttle.mobile;

import android.app.Activity;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.TextView;

/**
 * One native "Starting Cuttle" view over the WebView while a saved host loads,
 * so the bundled setup page and intermediate loading pages never flash by.
 *
 * It never waits on a single signal. Any of these removes it: the shell saying
 * its first page is revealed (or a short limit after the shell document loads),
 * another host page finishing, any setup/offline/error screen being shown,
 * the user tapping "Server settings" (offered after a few seconds), and a hard
 * time limit. Main thread only.
 */
final class StartupCover {
    /** Longer than the probe retries and the connection watchdog, so those win. */
    static final long MAX_VISIBLE_MS = 25000;
    static final long SETTINGS_LINK_MS = 5000;
    private static final long FADE_MS = 180;

    private final Activity activity;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable expire = this::hide;
    private View root;
    private View settingsLink;
    private final Runnable offerSettings = () -> {
        if (settingsLink != null) settingsLink.setVisibility(View.VISIBLE);
    };

    StartupCover(Activity activity) {
        this.activity = activity;
    }

    boolean isShowing() {
        return root != null;
    }

    void show(int background, int accent, Runnable onServerSettings) {
        if (root != null) {
            return;
        }
        FrameLayout cover = new FrameLayout(activity);
        cover.setBackgroundColor(background);
        cover.setClickable(true); // swallow touches meant for the hidden page

        // The source art at its own pixel size (nodpi): never scaled.
        ImageView mascot = new ImageView(activity);
        mascot.setImageResource(R.drawable.startup_mascot);
        mascot.setScaleType(ImageView.ScaleType.CENTER);
        mascot.setTranslationY(-dp(40));
        cover.addView(mascot, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.CENTER
        ));
        int mascotHeight = mascot.getDrawable() != null ? mascot.getDrawable().getIntrinsicHeight() : 0;
        float spinnerY = mascotHeight / 2f - dp(40) + dp(64);

        ProgressBar spinner = new ProgressBar(activity);
        spinner.setIndeterminateTintList(ColorStateList.valueOf(accent));
        spinner.setTranslationY(spinnerY);
        cover.addView(spinner, new FrameLayout.LayoutParams(dp(30), dp(30), Gravity.CENTER));

        TextView link = new TextView(activity);
        link.setText("Server settings");
        link.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        link.setTextColor(Color.parseColor("#8b9bb0"));
        link.setPadding(dp(16), dp(16), dp(16), dp(16));
        link.setTranslationY(spinnerY + dp(64));
        link.setVisibility(View.INVISIBLE);
        link.setOnClickListener(v -> {
            hide();
            if (onServerSettings != null) onServerSettings.run();
        });
        cover.addView(link, new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.CENTER
        ));
        settingsLink = link;

        activity.addContentView(cover, new ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        ));
        root = cover;
        handler.postDelayed(offerSettings, SETTINGS_LINK_MS);
        handler.postDelayed(expire, MAX_VISIBLE_MS);
    }

    /** Hide no later than {@code ms} from now; an earlier signal still wins. */
    void hideWithin(long ms) {
        if (root != null) handler.postDelayed(expire, ms);
    }

    /** Safe to call any number of times, from any dismissal path. */
    void hide() {
        final View cover = root;
        if (cover == null) {
            return;
        }
        root = null;
        settingsLink = null;
        handler.removeCallbacks(offerSettings);
        handler.removeCallbacks(expire);
        cover.setClickable(false);
        // Removal does not depend on the animation: a paused or destroyed window
        // may never run it.
        handler.postDelayed(() -> detach(cover), FADE_MS + 60);
        try {
            cover.animate().alpha(0f).setDuration(FADE_MS).start();
        } catch (Exception ignored) {}
    }

    private static void detach(View cover) {
        try {
            if (cover.getParent() instanceof ViewGroup) {
                ((ViewGroup) cover.getParent()).removeView(cover);
            }
        } catch (Exception ignored) {}
    }

    private int dp(int value) {
        return Math.round(value * activity.getResources().getDisplayMetrics().density);
    }
}
