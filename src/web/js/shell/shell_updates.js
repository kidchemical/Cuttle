/* Shell update center: the one "Check for updates" modal for every entry
 * point (Settings → Cuttle updates, the Electron titlebar Update button).
 *
 * Owns the update lifetime: the /api/settings/updates snapshot, the titlebar
 * button rendering, channel selection, the confirm step, applying the chosen
 * channel (release download, checkout fast-forward via
 * /api/workers/self-update, packaged desktop shell) and the restart prompt.
 * Nothing updates until the user picks a channel and confirms it.
 *
 * Channel decisions are pure (cuttleUpdatePlan in desktop_update_policy.js).
 * shell_electron.js feeds device state (desktop config, mesh rev, asar
 * availability) through setDesktopState and hands over the titlebar button.
 * Framed pages open the modal with postMessage {type: 'cuttle-open-updates'}.
 */
(function (root) {
    'use strict';

    const AUTO_CHECK_KEY = 'cuttleAutoUpdateChecks'; // shared with settings_releases.js
    const SNAPSHOT_REFRESH_MS = 30 * 60 * 1000;
    const JOB_TIMEOUT_MS = 180000;

    function create(deps) {
        const win = deps.win || root;
        const doc = deps.doc || win.document;
        const fetchImpl = deps.fetch || ((...a) => win.fetch(...a));
        const toast = (msg, kind) => { if (win.showToast) win.showToast(msg, kind); };
        const planFor = deps.plan || win.cuttleUpdatePlan;

        const overlay = doc.getElementById('cuttleUpdateModal');
        const modal = overlay && win.CuttleModal ? win.CuttleModal.create(overlay) : null;
        const subtitle = overlay && overlay.querySelector('[data-update-subtitle]');
        const badge = overlay && overlay.querySelector('[data-update-badge]');
        const titleEl = overlay && overlay.querySelector('#cuttleUpdateTitle');
        const body = overlay && overlay.querySelector('[data-update-body]');
        const actions = overlay && overlay.querySelector('[data-update-actions]');

        let snapshot = null;
        let checking = false;
        let step = 'choose'; // choose | confirm | running | done
        let selected = '';
        let device = { cfg: null, mesh: null, asarAvailable: false };
        let titlebar = { button: null, chip: null };
        let outcome = null; // {kind: 'ready'|'noop'|'error', text}

        function autoChecksEnabled() {
            try { return win.localStorage.getItem(AUTO_CHECK_KEY) !== '0'; } catch (_) { return true; }
        }

        function role() {
            const cfg = device.cfg;
            if (typeof win.cuttleIsRemoteDesktopClient === 'function') {
                return win.cuttleIsRemoteDesktopClient(cfg) ? 'client' : 'host';
            }
            return cfg && cfg.clientMode ? 'client' : 'host';
        }

        function currentPlan(preferred) {
            return planFor({
                role: role(),
                snapshot,
                mesh: device.mesh,
                desktop: { available: device.asarAvailable },
                preferred: preferred || selected,
            });
        }

        function el(tag, cls, text) {
            const node = doc.createElement(tag);
            if (cls) node.className = cls;
            if (text != null) node.textContent = String(text);
            return node;
        }

        function button(label, cls, onClick, opts) {
            const b = el('button', 'logout-confirm-btn ' + cls, label);
            b.type = 'button';
            if (opts && opts.disabled) b.disabled = true;
            if (opts && opts.data) b.setAttribute('data-update-' + opts.data, '');
            b.addEventListener('click', onClick);
            return b;
        }

        // ── titlebar ───────────────────────────────────────────────────
        function renderTitlebar() {
            const btn = titlebar.button;
            if (!btn || step === 'running') return;
            const t = currentPlan().titlebar;
            btn.hidden = !t.show;
            btn.classList.remove('is-busy');
            btn.classList.toggle('is-mesh-update', t.show && t.channel !== 'release');
            btn.title = t.title || 'Cuttle updates';
            btn.setAttribute('aria-label', t.show ? 'Update available — ' + t.title : 'Cuttle updates');
            const chip = titlebar.chip;
            if (chip) {
                chip.hidden = !t.chip;
                chip.textContent = t.chip || '';
                chip.dataset.source = t.channel || '';
            }
        }

        function setTitlebarBusy(busy, label) {
            const btn = titlebar.button;
            if (!btn) return;
            const text = btn.querySelector('.shell-win-update-label');
            btn.classList.toggle('is-busy', !!busy);
            if (text) text.textContent = busy ? (label || 'Updating…') : 'Update';
        }

        function bindTitlebar(btn, chip) {
            titlebar = { button: btn || null, chip: chip || null };
            if (btn) btn.addEventListener('click', () => open({ preferred: currentPlan().titlebar.channel }));
            renderTitlebar();
        }

        function setDesktopState(next) {
            device = { ...device, ...(next || {}) };
            renderTitlebar();
            if (isOpen() && step === 'choose') render();
        }

        // ── snapshot ───────────────────────────────────────────────────
        async function loadSnapshot(check) {
            const url = check
                ? '/api/settings/updates/check'
                : '/api/settings/updates' + (autoChecksEnabled() ? '' : '?release=0');
            try {
                const r = await fetchImpl(url, check
                    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
                    : { cache: 'no-store' });
                const data = r.ok ? await r.json() : null;
                // Non-owners (403) and older servers get release-only status.
                snapshot = data && data.install_kind ? data : null;
            } catch (_) {
                if (check) snapshot = null;
            }
            renderTitlebar();
            return snapshot;
        }

        async function check() {
            checking = true;
            render();
            await loadSnapshot(true);
            checking = false;
            selected = currentPlan().selected;
            render();
        }

        // ── modal ──────────────────────────────────────────────────────
        function isOpen() { return !!(modal && modal.isOpen()); }

        function open(opts) {
            if (!modal) return;
            const o = opts || {};
            if (step === 'running') { modal.open(); render(); return; }
            step = 'choose';
            outcome = null;
            selected = ''; // every open starts from the plan, never a stale pick
            selected = currentPlan(o.preferred).selected;
            modal.open();
            render();
            if (o.check !== false) check();
        }

        function close() {
            if (!modal || step === 'running') return;
            modal.close();
        }

        function describeInstall() {
            if (role() === 'client') {
                // The snapshot describes the Host; the Client reports via the mesh.
                const cfg = device.cfg || {};
                const ver = (device.mesh && device.mesh.localVersion) || cfg.packageVersion || '';
                return 'This Client' + (cfg.hostname ? ' (' + cfg.hostname + ')' : '') + (ver ? ' · ' + ver : '');
            }
            const s = snapshot;
            const parts = [];
            const ver = (s && s.current_version) || (device.cfg && device.cfg.packageVersion) || '';
            if (ver) parts.push('v' + ver);
            if (s && s.git_rev) parts.push(String(s.git_rev).slice(0, 7));
            if (s && s.checkout && s.checkout.branch) parts.push(s.checkout.branch);
            return 'This install' + (parts.length ? ' · ' + parts.join(' · ') : '');
        }

        function renderBadge(plan) {
            if (!badge) return;
            const label = plan.installKind === 'source'
                ? (plan.developerMode ? 'Developer mode' : 'Source checkout')
                : (snapshot ? 'Release install' : '');
            badge.hidden = !label;
            badge.textContent = label;
        }

        function channelCard(ch) {
            const card = el('label', 'update-channel is-' + ch.state + (ch.selectable ? '' : ' is-disabled'));
            card.dataset.channel = ch.id;
            const radio = el('input');
            radio.type = 'radio';
            radio.name = 'cuttleUpdateChannel';
            radio.value = ch.id;
            radio.disabled = !ch.selectable;
            radio.checked = selected === ch.id;
            radio.addEventListener('change', () => { selected = ch.id; render(); });
            const head = el('span', 'update-channel-head');
            head.append(el('span', 'update-channel-chip', ch.chip), el('strong', 'update-channel-title', ch.title),
                el('span', 'update-channel-state', {
                    available: 'Update available', current: 'Up to date', blocked: 'Blocked',
                    unknown: 'Not checked', info: 'Published',
                }[ch.state] || ''));
            const text = el('span', 'update-channel-text');
            text.append(head, el('span', 'update-channel-sub', ch.subtitle), el('span', 'update-channel-summary', ch.summary));
            card.append(radio, text);
            return card;
        }

        function renderChoose(plan) {
            if (titleEl) titleEl.textContent = 'Cuttle updates';
            const list = el('div', 'update-channel-list');
            list.setAttribute('role', 'radiogroup');
            list.setAttribute('aria-label', 'Update channel');
            plan.channels.forEach((ch) => list.append(channelCard(ch)));
            body.append(list);
            if (checking) body.append(el('p', 'update-center-note', 'Checking every channel…'));
            else if (!plan.channels.some((ch) => ch.selectable)) {
                body.append(el('p', 'update-center-note', 'You’re up to date.'));
            } else if (!selected && plan.available.length > 1) {
                body.append(el('p', 'update-center-note', 'More than one channel has an update. Choose the one to install.'));
            }
            if (plan.installKind === 'source' && !plan.developerMode) {
                body.append(el('p', 'update-center-note',
                    'Developer mode is off, so branch and Host channels are hidden. Turn it on in Settings → General → Cuttle updates.'));
            }
            const ch = plan.channels.find((c) => c.id === selected);
            if (plan.channels.some((c) => c.selectable)) {
                // A release download leaves the app, so it needs no confirm step.
                actions.append(button(ch ? (ch.action === 'download' ? ch.actionLabel : 'Continue') : 'Choose a channel',
                    'logout-confirm-submit', () => {
                        if (!ch) return;
                        if (ch.action === 'download') { apply(ch); return; }
                        step = 'confirm';
                        render();
                    }, { disabled: !ch || checking, data: 'continue' }));
            }
            actions.append(
                button(checking ? 'Checking…' : 'Check again', 'logout-confirm-cancel', check,
                    { disabled: checking, data: 'check' }),
                button('Close', 'logout-confirm-cancel', close, { data: 'cancel' }),
            );
        }

        function renderConfirm(plan) {
            const ch = plan.channels.find((c) => c.id === selected);
            if (!ch) { step = 'choose'; render(); return; }
            if (titleEl) titleEl.textContent = 'Confirm update';
            const target = updateTargetLabel();
            if (ch.action === 'pull') {
                const from = ch.id === 'host' ? 'the paired Host' : (ch.subtitle || 'its upstream');
                const where = ch.path ? ch.path : 'the Cuttle checkout on ' + target;
                const what = el('p', 'update-confirm-what');
                what.append('Fast-forward ', el('code', '', where));
                what.append((ch.branch ? ' (branch ' + ch.branch + ')' : '') + ' from ' + from + '.');
                body.append(what);
                if (ch.commits && ch.commits.length) {
                    const list = el('ul', 'update-confirm-commits');
                    ch.commits.forEach((c) => {
                        const li = el('li');
                        li.append(el('code', '', c.rev), ' ' + c.subject);
                        list.append(li);
                    });
                    if (ch.behind > ch.commits.length) list.append(el('li', 'update-confirm-more', '…and ' + (ch.behind - ch.commits.length) + ' more'));
                    body.append(list);
                }
                body.append(el('p', 'update-center-note',
                    'Local changes or local commits stop the update — nothing is stashed, reset or discarded. '
                    + 'Cuttle asks before restarting.'));
                if (plan.installKind === 'source' && role() === 'host') {
                    body.append(el('p', 'update-confirm-warning',
                        'This is a developer checkout: the update changes the repository you work in.'));
                }
            } else if (ch.action === 'desktop') {
                body.append(el('p', 'update-confirm-what',
                    'Download the desktop app shell from the Host, verify its checksum, and restart Cuttle on this device.'));
            }
            actions.append(
                button(ch.actionLabel || 'Update', 'logout-confirm-submit', () => apply(ch), { data: 'confirm' }),
                button('Back', 'logout-confirm-cancel', () => { step = 'choose'; render(); }, { data: 'back' }),
            );
        }

        function renderStatus() {
            if (step === 'running') {
                if (titleEl) titleEl.textContent = 'Updating…';
                body.append(el('p', 'update-center-note update-center-running', (outcome && outcome.text) || 'Working…'));
                return;
            }
            const o = outcome || { kind: 'noop', text: '' };
            if (titleEl) titleEl.textContent = { ready: 'Update ready', noop: 'Already up to date', error: 'Update stopped' }[o.kind];
            body.append(el('p', o.kind === 'error' ? 'update-confirm-warning' : 'update-confirm-what', o.text));
            if (o.kind === 'ready') {
                if (role() === 'host') {
                    body.append(el('p', 'update-center-note',
                        'Restarting reloads the app. The Cuttle server keeps running — use /restart in chat to load server changes.'));
                }
                actions.append(
                    button('Restart now', 'logout-confirm-submit', restartNow, { data: 'restart' }),
                    button('Later', 'logout-confirm-cancel', close, { data: 'later' }),
                );
            } else if (o.kind === 'error') {
                actions.append(
                    button('Back', 'logout-confirm-submit', () => { step = 'choose'; check(); }, { data: 'back' }),
                    button('Close', 'logout-confirm-cancel', close, { data: 'cancel' }),
                );
            } else {
                actions.append(button('Close', 'logout-confirm-submit', close, { data: 'cancel' }));
            }
        }

        function render() {
            if (!body || !actions) return;
            const plan = currentPlan();
            if (subtitle) subtitle.textContent = describeInstall();
            renderBadge(plan);
            body.textContent = '';
            actions.textContent = '';
            overlay.classList.toggle('is-running', step === 'running');
            if (step === 'choose') renderChoose(plan);
            else if (step === 'confirm') renderConfirm(plan);
            else renderStatus();
        }

        // ── apply ──────────────────────────────────────────────────────
        function updateTargetLabel() {
            const cfg = device.cfg || {};
            return String(cfg.hostname || cfg.workerId || 'this device');
        }

        async function resolveTarget() {
            const cfg = device.cfg || {};
            if (role() === 'client') {
                return String((device.mesh && device.mesh.target) || cfg.workerId || '').trim().toLowerCase();
            }
            try {
                const r = await fetchImpl('/api/workers', { cache: 'no-store' });
                const data = r.ok ? await r.json() : {};
                const self = String((data && data.self_worker_id) || '').trim().toLowerCase();
                if (self) return self;
            } catch (_) { /* fall through */ }
            return String(cfg.workerId || '').trim().toLowerCase();
        }

        function running(text) {
            outcome = { kind: 'running', text };
            render();
        }

        function finish(kind, text) {
            step = 'done';
            outcome = { kind, text };
            setTitlebarBusy(false);
            renderTitlebar();
            render();
            if (!isOpen()) toast(text, kind === 'error' ? 'error' : 'info');
        }

        async function waitForJob(jobId, initial) {
            let job = initial || null;
            const deadline = Date.now() + JOB_TIMEOUT_MS;
            while (jobId && Date.now() < deadline) {
                await new Promise((res) => setTimeout(res, 1500));
                try {
                    const r = await fetchImpl('/api/workers/jobs/' + encodeURIComponent(jobId), { cache: 'no-store' });
                    const data = await r.json().catch(() => ({}));
                    job = (data && (data.job || data)) || job;
                } catch (_) { /* keep waiting */ }
                const st = String((job && job.status) || '').toLowerCase();
                if (st === 'succeeded' || st === 'failed' || st === 'cancelled') break;
            }
            return job;
        }

        async function applyPull(ch) {
            const target = await resolveTarget();
            if (!target) throw new Error('Could not identify this device on the mesh.');
            running('Updating ' + target + ' from ' + (ch.subtitle || ch.title) + '…');
            const r = await fetchImpl('/api/workers/self-update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target,
                    submitted_by: 'update-center',
                    update_source: ch.source,
                    // Interactive: pull now, ask before restarting.
                    defer_restart: true,
                    no_daemon: role() !== 'client',
                }),
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok || !data.success) throw new Error(data.error || ('HTTP ' + r.status));
            const jobId = String((data.job && (data.job.id || data.job.job_id)) || data.job_id || '').trim();
            const job = await waitForJob(jobId, data.job);
            const st = String((job && job.status) || '').toLowerCase();
            if (st === 'failed' || st === 'cancelled') {
                throw new Error(String((job && (job.error || (job.result && job.result.error))) || 'Update failed'));
            }
            if (st !== 'succeeded') throw new Error('Update timed out (status ' + (st || 'unknown') + ').');
            const result = (job && job.result) || {};
            device.restartTarget = { target, source: ch.source };
            await loadSnapshot(false);
            if (result.advanced === false) {
                finish('noop', 'Nothing to update: ' + (result.branch || 'this checkout') + ' is already at '
                    + (result.head || 'the latest') + '. Nothing was restarted.');
            } else {
                finish('ready', 'Cuttle updated to ' + (result.head || 'the latest') + '. Restart now to apply it, or keep working and restart later.');
            }
        }

        async function applyDesktop() {
            const api = win.electron;
            if (!api || !api.desktop || typeof api.desktop.applyUpdate !== 'function') {
                throw new Error('Desktop updates need the Cuttle desktop app.');
            }
            running('Downloading the desktop app from the Host…');
            const result = await api.desktop.applyUpdate();
            if (!result || !result.ok) throw new Error((result && result.error) || 'Desktop update failed');
            finish('ready', 'Desktop update installed. Cuttle restarts to finish.');
        }

        async function apply(ch) {
            if (ch.action === 'download') {
                if (ch.url) win.open(ch.url, '_blank', 'noopener');
                close();
                return;
            }
            step = 'running';
            setTitlebarBusy(true, ch.action === 'pull' ? 'Pulling…' : 'Updating…');
            running('Preparing…');
            try {
                if (ch.action === 'pull') await applyPull(ch);
                else if (ch.action === 'desktop') await applyDesktop();
            } catch (err) {
                finish('error', String((err && err.message) || err));
            }
        }

        async function restartNow() {
            modal.close();
            const api = win.electron;
            if (api && api.desktop && typeof api.desktop.relaunchApp === 'function') {
                api.desktop.relaunchApp();
                return;
            }
            // No relaunch IPC (browser or older Electron): let the updater
            // restart the app — the pull is a no-op now.
            const t = device.restartTarget || {};
            try {
                await fetchImpl('/api/workers/self-update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target: t.target, submitted_by: 'update-center-restart',
                        update_source: t.source || 'official', no_daemon: role() !== 'client',
                    }),
                });
            } catch (_) {
                win.location.reload();
            }
        }

        // ── wiring ─────────────────────────────────────────────────────
        if (overlay) {
            overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
            overlay.querySelector('[data-update-close]')?.addEventListener('click', close);
            doc.addEventListener('keydown', (e) => { if (e.key === 'Escape' && isOpen()) close(); });
        }
        win.addEventListener('message', (e) => {
            if (e.origin !== win.location.origin) return;
            const type = e.data && e.data.type;
            if (type === 'cuttle-open-updates') open({ check: e.data.check !== false });
            // Settings changed Developer mode or the preferred channel.
            else if (type === 'cuttle-updates-changed') {
                loadSnapshot(false).then(() => { if (isOpen() && step === 'choose') render(); });
            }
        });
        loadSnapshot(false).then(() => {
            try {
                if (new URLSearchParams(win.location.search).get('updates') === '1') open({});
            } catch (_) { /* no query */ }
        });
        win.setInterval(() => { if (!isOpen()) loadSnapshot(false); }, SNAPSHOT_REFRESH_MS);

        return { open, close, isOpen, bindTitlebar, setDesktopState, plan: () => currentPlan() };
    }

    root.CuttleShellUpdates = { create };
    if (root.document && root.document.getElementById('cuttleUpdateModal')) {
        root.cuttleShellUpdates = create({ win: root });
    }
})(typeof window !== 'undefined' ? window : globalThis);
