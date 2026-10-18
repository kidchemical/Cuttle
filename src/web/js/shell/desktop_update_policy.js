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

const CUTTLE_UPDATE_CHIPS = { release: 'RELEASE', official: 'GITHUB', host: 'HOST', desktop: 'APP' };

function cuttlePlural(n, word) {
    return n + ' ' + word + (n === 1 ? '' : 's');
}

function cuttleReleaseChannel(snapshot, installKind) {
    const check = snapshot && snapshot.release;
    const release = check && check.release;
    const version = release && release.version ? 'v' + release.version : '';
    const ch = {
        id: 'release', title: 'Published release', subtitle: 'GitHub Releases',
        state: 'unknown', summary: '', action: '', actionLabel: '', url: release ? release.url : '',
    };
    if (!check) {
        ch.summary = 'Not checked yet. Use Check again to look for a new release.';
    } else if (check.error && !release) {
        ch.summary = String(check.error);
    } else if (check.state === 'available') {
        if (installKind === 'release') {
            ch.state = 'available';
            ch.summary = version + ' is available.';
            ch.action = 'download';
            ch.actionLabel = 'Download ' + version;
        } else {
            // A source checkout follows a branch; a release package would not
            // replace it, so the release is information here, not an action.
            ch.state = 'info';
            ch.summary = version + ' is published. This checkout updates from its branch, not release packages.';
        }
    } else if (check.state === 'latest' || check.state === 'ahead') {
        ch.state = 'current';
        ch.summary = check.state === 'ahead'
            ? 'This version is newer than the latest release (' + version + ').'
            : 'You have the latest release (' + version + ').';
    } else if (check.state === 'no_release') {
        ch.state = 'current';
        ch.summary = 'No published release yet.';
    } else {
        ch.summary = 'Release status is unavailable.';
    }
    return ch;
}

function cuttleBranchChannel(snapshot, role) {
    const co = snapshot && snapshot.checkout;
    const ch = {
        id: 'official', source: 'official', title: 'GitHub branch',
        subtitle: (co && co.upstream) || 'Configured upstream',
        state: 'unknown', summary: '', action: 'pull', actionLabel: 'Update from GitHub',
        commits: [], path: (co && co.path) || '', branch: (co && co.branch) || '',
        head: (co && co.head) || '', behind: 0,
    };
    if (role === 'client') {
        // The Client's own checkout is not visible from here; the updater
        // fetches on that device and only fast-forwards a clean branch.
        ch.subtitle = "This device's upstream";
        ch.summary = 'Fetches on this device and fast-forwards only if its checkout is clean.';
        return ch;
    }
    if (!co) {
        ch.action = '';
        ch.summary = 'Checkout status is unavailable.';
        return ch;
    }
    if (co.blocked) {
        ch.state = 'blocked';
        ch.summary = co.blocked;
        ch.action = '';
    } else if (co.behind > 0) {
        ch.state = 'available';
        ch.behind = co.behind;
        ch.commits = Array.isArray(co.commits) ? co.commits : [];
        ch.summary = cuttlePlural(co.behind, 'new commit') + ' on ' + co.upstream + '.';
        ch.actionLabel = 'Pull ' + cuttlePlural(co.behind, 'commit');
    } else {
        ch.state = 'current';
        ch.action = '';
        ch.summary = 'Up to date with ' + co.upstream + (co.fetched ? '.' : ' (as of the last fetch).');
    }
    if (co.error) ch.summary += ' ' + co.error;
    return ch;
}

function cuttleHostChannel(mesh) {
    const m = mesh || {};
    const ch = {
        id: 'host', source: 'host', title: 'This Host', subtitle: 'Paired Cuttle Host',
        state: 'current', summary: 'Same code as the Host.', action: '', actionLabel: 'Update from Host',
    };
    if (m.wrongBranch) {
        ch.state = 'blocked';
        ch.summary = 'This device is on ' + (m.branch || '?') + '; the Host is on '
            + (m.hostBranch || '?') + '. Switch branches to follow it.';
    } else if (m.available) {
        ch.state = 'available';
        ch.action = 'pull';
        ch.summary = 'Host is at ' + (m.hostVersion || '?') + '; this device is at ' + (m.localVersion || '?') + '.';
    }
    return ch;
}

/**
 * Every update channel for this device, and which one is preselected.
 *
 * One "Check for updates" entry point (Settings and the titlebar) renders this
 * plan; nothing updates until the user picks a channel and confirms it.
 *
 *  - release: published GitHub release. Actionable (download) only on a
 *    release install; a source checkout shows it as information.
 *  - official / host: Developer mode only. Fast-forward a checkout from its
 *    upstream branch or from the paired Host (Clients only).
 *  - desktop: packaged Client shell from the Host (app.asar).
 *
 * Pure: snapshots in, plan out (no DOM/fetch). Input:
 * {role, snapshot (GET /api/settings/updates), mesh, desktop: {available}, preferred}.
 * Returns {installKind, developerMode, channels, selected, available, titlebar}.
 */
function cuttleUpdatePlan(input) {
    const o = input || {};
    const role = o.role === 'client' ? 'client' : 'host';
    const snapshot = o.snapshot || null;
    const installKind = (snapshot && snapshot.install_kind) || 'release';
    const mode = (snapshot && snapshot.developer_mode) || {};
    const developerMode = !!mode.enabled;
    const channels = [cuttleReleaseChannel(snapshot, installKind)];
    if (developerMode) {
        const branch = cuttleBranchChannel(snapshot, role);
        const host = role === 'client' ? cuttleHostChannel(o.mesh) : null;
        // The configured channel leads; the other stays one click away.
        const configured = String((snapshot && snapshot.update_source) || '') === 'host' ? 'host' : 'official';
        if (host && configured === 'host') channels.push(host, branch);
        else channels.push(branch, ...(host ? [host] : []));
    }
    if (role === 'client' && o.desktop && o.desktop.available) {
        channels.push({
            id: 'desktop', title: 'Desktop app', subtitle: 'From the paired Host', state: 'available',
            summary: 'A newer desktop app shell is ready on the Host.', action: 'desktop',
            actionLabel: 'Install desktop update',
        });
    }
    channels.forEach((ch) => {
        ch.chip = CUTTLE_UPDATE_CHIPS[ch.id];
        ch.selectable = !!ch.action && (ch.state === 'available' || ch.state === 'unknown');
    });
    const available = channels.filter((ch) => ch.selectable && ch.state === 'available');
    let selected = '';
    const preferred = channels.find((ch) => ch.id === o.preferred && ch.selectable);
    if (preferred) selected = preferred.id;
    else if (available.length === 1) selected = available[0].id;
    const top = available[0] || null;
    return {
        role,
        installKind,
        developerMode,
        developerModeAvailable: !!mode.available,
        channels,
        selected,
        available: available.map((ch) => ch.id),
        titlebar: {
            show: available.length > 0,
            channel: top ? top.id : '',
            chip: available.length > 1 ? available.length + ' UPDATES' : (top ? top.chip : ''),
            title: top ? (available.length > 1
                ? 'Updates available on ' + available.length + ' channels — choose one'
                : top.title + ': ' + top.summary) : '',
        },
    };
}

if (typeof module === 'object' && module.exports) {
    module.exports = {
        isLoopbackHost: cuttleIsLoopbackHost,
        isRemoteDesktopClient: cuttleIsRemoteDesktopClient,
        desktopRole: cuttleDesktopRole,
        desktopUpdateAvailable: cuttleDesktopUpdateAvailable,
        updatePlan: cuttleUpdatePlan,
    };
}
