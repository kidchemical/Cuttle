'use strict';

function cuttleIsLoopbackHost(host) {
    const h = String(host || '').toLowerCase().replace(/^\[|\]$/g, '');
    return h === '127.0.0.1' || h === 'localhost' || h === '::1' || h === '';
}

function cuttleIsRemoteDesktopClient(cfg) {
    if (!cfg || !cfg.clientMode) return false;
    return !cuttleIsLoopbackHost(cfg.host);
}

function cuttleDesktopRole(cfg) {
    return cuttleIsRemoteDesktopClient(cfg) ? 'client' : 'host';
}

function cuttleDesktopUpdateAvailable(remote, localHash, opts) {
    const o = opts || {};
    return !!(
        o.packaged
        && cuttleIsRemoteDesktopClient({ clientMode: o.clientMode, host: o.host })
        && remote
        && remote.ok
        && remote.hash
        && remote.artifact
        && remote.hash !== localHash
    );
}

if (typeof module === 'object' && module.exports) {
    module.exports = {
        isLoopbackHost: cuttleIsLoopbackHost,
        isRemoteDesktopClient: cuttleIsRemoteDesktopClient,
        desktopRole: cuttleDesktopRole,
        desktopUpdateAvailable: cuttleDesktopUpdateAvailable,
    };
}
