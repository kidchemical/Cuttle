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

/**
 * Which update buttons to show for a Client.
 *
 * Two channels:
 *  - OFFICIAL (blue): follow upstream/GitHub (git remote / release).
 *  - HOST (green): follow this paired Host (served git bundle / asar).
 *
 * One button normally — the configured channel's chip is always shown. Show
 * both only when both channels are ahead AND diverge, so the user picks.
 *
 * Pure: inputs only, no DOM/fetch. Returns
 * {show, mode: 'single'|'both', buttons: [{source, chip, diverged}]}.
 */
function cuttleUpdateChannels(input) {
    const o = input || {};
    const configured = o.configuredSource === 'host' ? 'host' : 'official';
    const host = o.host || {};
    const official = o.official || {};
    const hostOn = !!host.available;
    const officialOn = !!official.available;
    const diverges = !!(host.diverged || official.diverged);
    const showBoth = hostOn && officialOn && diverges;
    const chip = (s) => (s === 'host' ? 'HOST' : 'OFFICIAL');
    const buttons = [];
    if (showBoth) {
        buttons.push({ source: 'official', chip: 'OFFICIAL', diverged: !!official.diverged });
        buttons.push({ source: 'host', chip: 'HOST', diverged: !!host.diverged });
    } else {
        let source = '';
        if (configured === 'host' && hostOn) source = 'host';
        else if (configured === 'official' && officialOn) source = 'official';
        else if (hostOn) source = 'host';
        else if (officialOn) source = 'official';
        if (source) {
            buttons.push({ source, chip: chip(source), diverged: !!(source === 'host' ? host.diverged : official.diverged) });
        }
    }
    return { show: buttons.length > 0, mode: showBoth ? 'both' : 'single', buttons };
}

if (typeof module === 'object' && module.exports) {
    module.exports = {
        isLoopbackHost: cuttleIsLoopbackHost,
        isRemoteDesktopClient: cuttleIsRemoteDesktopClient,
        desktopRole: cuttleDesktopRole,
        desktopUpdateAvailable: cuttleDesktopUpdateAvailable,
        updateChannels: cuttleUpdateChannels,
    };
}
