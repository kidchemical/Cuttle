package com.cuttle.mobile;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Build;

/**
 * The Cuttle theme's background and accent, remembered natively so the startup
 * screen matches it before any page has loaded. The shell reports them; only
 * plain #rrggbb values are accepted.
 */
final class StartupColors {
    private static final String PREFS = "cuttle_startup";
    private static final String KEY_BACKGROUND = "background";
    private static final String KEY_ACCENT = "accent";
    static final String DEFAULT_BACKGROUND = "#0b0f14";
    static final String DEFAULT_ACCENT = "#8b6fe0";

    private StartupColors() {}

    static boolean isHexColor(String value) {
        return value != null && value.matches("#[0-9a-fA-F]{6}");
    }

    /** Perceived brightness decides which fixed system launch splash to use. */
    static boolean isLight(String hex) {
        int r = Integer.parseInt(hex.substring(1, 3), 16);
        int g = Integer.parseInt(hex.substring(3, 5), 16);
        int b = Integer.parseInt(hex.substring(5, 7), 16);
        return (r * 299 + g * 587 + b * 114) / 1000 >= 140;
    }

    /** {background, accent} as colour ints. */
    static int[] read(Activity activity) {
        SharedPreferences prefs = activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE);
        String background = prefs.getString(KEY_BACKGROUND, DEFAULT_BACKGROUND);
        String accent = prefs.getString(KEY_ACCENT, DEFAULT_ACCENT);
        if (!isHexColor(background)) background = DEFAULT_BACKGROUND;
        if (!isHexColor(accent)) accent = DEFAULT_ACCENT;
        return new int[] { Color.parseColor(background), Color.parseColor(accent) };
    }

    static void save(Activity activity, String background, String accent) {
        if (!isHexColor(background) || !isHexColor(accent)) {
            return;
        }
        activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE).edit()
            .putString(KEY_BACKGROUND, background)
            .putString(KEY_ACCENT, accent)
            .apply();
        // The system launch splash cannot take an arbitrary colour; pick the
        // closer of the two fixed ones for the next launch.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            try {
                activity.getSplashScreen().setSplashScreenTheme(isLight(background)
                    ? R.style.AppTheme_NoActionBarLaunchLight
                    : R.style.AppTheme_NoActionBarLaunch);
            } catch (Exception ignored) {}
        }
    }
}
