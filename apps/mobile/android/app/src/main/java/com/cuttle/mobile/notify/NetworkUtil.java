package com.cuttle.mobile.notify;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;

/** Cheap LAN/Wi‑Fi presence check — not "can reach Cuttle", just "has a route". */
public final class NetworkUtil {
    private NetworkUtil() {}

    public static boolean isOnline(Context ctx) {
        ConnectivityManager cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) {
            return true; // assume online if we cannot tell
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Network net = cm.getActiveNetwork();
            if (net == null) {
                return false;
            }
            NetworkCapabilities caps = cm.getNetworkCapabilities(net);
            if (caps == null) {
                return false;
            }
            return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR));
        }
        android.net.NetworkInfo info = cm.getActiveNetworkInfo();
        return info != null && info.isConnected();
    }
}
