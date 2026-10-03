/**
 * Preload script for Electron
 * This script runs before the renderer process and provides a secure bridge
 * between the renderer and main process
 */

const { contextBridge, webFrame, ipcRenderer } = require('electron');

/** Discrete zoom ladder (percent). Matches Chromium-style 10% steps. */
const ZOOM_PCT_MIN = 50;
const ZOOM_PCT_MAX = 300;
const ZOOM_PCT_STEP = 10;

function readZoomPercent() {
    try {
        return Math.round(webFrame.getZoomFactor() * 100);
    } catch (_) {
        return 100;
    }
}

function setZoomPercent(pct) {
    let next = Math.round(Number(pct));
    if (!Number.isFinite(next)) next = 100;
    next = Math.min(ZOOM_PCT_MAX, Math.max(ZOOM_PCT_MIN, next));
    next = Math.round(next / ZOOM_PCT_STEP) * ZOOM_PCT_STEP;
    try {
        webFrame.setZoomFactor(next / 100);
    } catch (_) {}
    return next;
}

function stepZoom(dir) {
    const cur = readZoomPercent();
    const snapped = Math.round(cur / ZOOM_PCT_STEP) * ZOOM_PCT_STEP;
    return setZoomPercent(snapped + (dir > 0 ? ZOOM_PCT_STEP : -ZOOM_PCT_STEP));
}

// Expose protected methods that allow the renderer process to use
// ipcRenderer without exposing the entire object
contextBridge.exposeInMainWorld('electron', {
    platform: process.platform,
    versions: {
        node: process.versions.node,
        chrome: process.versions.chrome,
        electron: process.versions.electron
    },
    isElectron: true,
    zoomIn: () => stepZoom(1),
    zoomOut: () => stepZoom(-1),
    resetZoom: () => setZoomPercent(100),
    getZoomPercent: () => readZoomPercent(),
    setZoomPercent: (pct) => setZoomPercent(pct),
    onZoomShortcut: (callback) => {
        const handler = (_event, payload) => callback(payload);
        ipcRenderer.on('shell-zoom-shortcut', handler);
        return () => ipcRenderer.removeListener('shell-zoom-shortcut', handler);
    },
    /** Play the reply-ready chirp via main process (works while minimized / in tray). */
    playChirp: () => ipcRenderer.send('play-chirp'),
    /** Play an allowlisted SFX by name (e.g. 'achievement-unlock') natively. */
    playSfx: (name) => ipcRenderer.send('play-sfx', String(name || '')),
    isWindowObscured: () => ipcRenderer.invoke('window-is-obscured'),
    windowControls: {
        minimize: () => ipcRenderer.send('window-minimize'),
        maximize: () => ipcRenderer.send('window-maximize'),
        close: () => ipcRenderer.send('window-close'),
        reload: () => ipcRenderer.send('window-reload'),
        toggleFullscreen: () => ipcRenderer.send('window-toggle-fullscreen'),
        isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
        isFullscreen: () => ipcRenderer.invoke('window-is-fullscreen'),
        onMaximizedChange: (callback) => {
            const handler = (_event, maximized) => callback(maximized);
            ipcRenderer.on('window-maximized', handler);
            return () => ipcRenderer.removeListener('window-maximized', handler);
        },
            onFullscreenChange: (callback) => {
            const handler = (_event, fullscreen) => callback(fullscreen);
            ipcRenderer.on('window-fullscreen', handler);
            return () => ipcRenderer.removeListener('window-fullscreen', handler);
        },
    },
    chatFind: {
        onShortcut: (callback) => {
            const handler = (_event, payload) => callback(payload);
            ipcRenderer.on('chat-find-shortcut', handler);
            return () => ipcRenderer.removeListener('chat-find-shortcut', handler);
        },
    },
    desktop: {
        getConfig: () => ipcRenderer.invoke('desktop-get-config'),
        connectHost: (host) => ipcRenderer.invoke('desktop-connect', host),
        useLocal: () => ipcRenderer.invoke('desktop-use-local'),
        showConnect: () => ipcRenderer.invoke('desktop-show-connect'),
        updateStatus: () => ipcRenderer.invoke('desktop-update-status'),
        applyUpdate: () => ipcRenderer.invoke('desktop-apply-update'),
        onUpdateAvailable: (callback) => {
            const handler = (_event, payload) => callback(payload);
            ipcRenderer.on('desktop-update-available', handler);
            return () => ipcRenderer.removeListener('desktop-update-available', handler);
        },
    },
});

// Do not assign to window here — contextIsolation:true isolates preload from the
// page. isElectron is already exposed via contextBridge above.
