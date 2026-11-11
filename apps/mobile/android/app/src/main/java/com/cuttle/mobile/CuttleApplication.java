package com.cuttle.mobile;

import android.app.Application;
import com.cuttle.mobile.tls.HostPins;

/** Runs before any activity or service, so background calls can see trusted hosts. */
public class CuttleApplication extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        HostPins.init(this);
    }
}
