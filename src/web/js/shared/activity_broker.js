/* One full-session snapshot broker per shell. Coalesced reads, change-stream
 * wakeups, and periodic/reconnect recovery. Frames consume snapshots rather
 * than each independently observing background completion transitions. */
(function (root) {
    'use strict';
    function create(host) {
        let inFlight = null, snapshot = null, at = 0, dirty = false, timer = null;
        let stream = null, disposed = false, snapshotScope = null, inFlightScope = null;
        const scope = () => host.scope ? host.scope() : null;
        function sessions(force = false, requesterScope = scope()) {
            const currentScope = scope();
            // A just-authenticated frame may know the new user before the shell
            // receives its auth event. Use a fresh owned HTTP read in that window.
            if (requesterScope !== currentScope) return host.load();
            if (snapshotScope !== currentScope) { snapshot = null; at = 0; }
            if (!force && snapshot && host.now() - at < 2000) return Promise.resolve(snapshot);
            if (inFlight) {
                if (inFlightScope === currentScope) return inFlight;
                return inFlight.catch(() => {}).then(() => sessions(force, requesterScope));
            }
            inFlightScope = currentScope;
            inFlight = host.load().then(rows => {
                if (scope() !== currentScope) throw new Error('Activity snapshot belongs to a previous account');
                snapshotScope = currentScope;
                snapshot = rows; at = host.now(); host.publish(rows); return rows;
            }).finally(() => {
                inFlight = null;
                if (dirty) { dirty = false; refresh(); }
            });
            return inFlight;
        }
        function refresh() {
            if (disposed) return;
            if (inFlight) { dirty = true; return; }
            if (timer) return;
            // Coalesce pulses and cap full snapshots to once per two seconds.
            // Progress text continues through the existing live-status hub.
            const delay = snapshot ? Math.max(250, 2000 - (host.now() - at)) : 250;
            timer = host.setTimeout(() => { timer = null; sessions(true).catch(() => {}); }, delay);
        }
        function start() {
            if (host.openStream) stream = host.openStream(refresh);
            refresh();
        }
        function dispose() {
            disposed = true;
            if (timer) host.clearTimeout(timer);
            if (stream) stream.close();
        }
        return {sessions, refresh, start, dispose};
    }
    const api = {create};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    root.CuttleActivityBroker = api;
})(globalThis);
