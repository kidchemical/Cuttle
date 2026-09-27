/// <reference types="@capacitor-community/safe-area" />

import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.cuttle.mobile',
  appName: 'Cuttle',
  webDir: 'dist',
  server: {
    androidScheme: 'https',
    iosScheme: 'https',
    cleartext: true,
    // Keep LAN Cuttle pages inside the WebView (otherwise Capacitor opens
    // the system browser / blocks navigation away from https://localhost).
    allowNavigation: [
      '192.168.*.*',
      '10.*.*.*',
      '172.*.*.*',
      // Tailscale CGNAT + MagicDNS (cellular / off-LAN)
      '100.*.*.*',
      '*.ts.net',
      '*.local',
    ],
  },
  android: {
    allowMixedContent: true,
    appendUserAgent: ' CuttleMobile/0.1',
    adjustMarginsForEdgeToEdge: 'disable',
  },
  ios: {
    appendUserAgent: ' CuttleMobile/0.1',
  },
  plugins: {
    SafeArea: {
      statusBarStyle: 'DARK',
      navigationBarStyle: 'DARK',
      initialViewportFitCover: true,
      detectViewportFitCoverChanges: true,
    },
  },
};

export default config;
