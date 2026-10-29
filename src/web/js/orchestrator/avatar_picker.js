/**
 * CuttleAvatarPicker — popover for a profile avatar: an emoji grid (or any
 * typed emoji), an uploaded picture, an image URL, or the Cuttle mascot.
 * The value it returns is exactly what chat bubbles render (emoji text,
 * `/output/…` or https URL, or `cuttle`).
 *
 *   const p = CuttleAvatarPicker.create({ upload: (file) => Promise<url>, onPick });
 *   p.open(anchorEl, currentAvatar);
 */
(function (root) {
    'use strict';

    const M = root.CuttleOrchestrator;

    function create(opts) {
        const o = opts || {};
        let el = null;
        let tab = 'emoji';
        let current = '';
        let anchor = null;

        function close() {
            if (!el) return;
            el.remove();
            el = null;
            document.removeEventListener('mousedown', onOutside, true);
            document.removeEventListener('keydown', onKey, true);
            window.removeEventListener('resize', close);
            if (anchor) anchor.setAttribute('aria-expanded', 'false');
        }

        function onOutside(e) {
            if (el && !el.contains(e.target) && !(anchor && anchor.contains(e.target))) close();
        }

        function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); close(); } }

        function pick(value) {
            if (typeof o.onPick === 'function') o.onPick(value);
            close();
        }

        function body() {
            if (tab === 'emoji') {
                return M.EMOJI_GROUPS.map(([name, list]) =>
                    `<div class="orch-avp-group"><div class="orch-avp-group-title">${M.esc(name)}</div>
                     <div class="orch-avp-grid">${list.map((e) =>
                        `<button type="button" class="orch-avp-emoji${e === current ? ' is-selected' : ''}" data-emoji="${M.esc(e)}" aria-label="${M.esc(e)}">${M.esc(e)}</button>`).join('')}</div></div>`).join('')
                    + `<form class="orch-avp-row" data-form="custom"><input name="custom" maxlength="16" placeholder="Or type any emoji" aria-label="Custom emoji">
                       <button type="submit" class="control-button">Use</button></form>`;
            }
            if (tab === 'picture') {
                return `<label class="orch-avp-drop" tabindex="0">
                        <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
                        <span class="orch-avp-drop-icon" aria-hidden="true">⇪</span>
                        <strong>Upload a picture</strong><small>PNG, JPEG, GIF or WebP · cropped square · max 8 MB</small></label>
                    <form class="orch-avp-row" data-form="url"><input name="url" type="url" placeholder="…or paste an https:// image URL" aria-label="Image URL">
                       <button type="submit" class="control-button">Use</button></form>
                    <p class="orch-avp-status" role="status" aria-live="polite"></p>`;
            }
            return `<div class="orch-avp-mascot"><img src="/img/cuttle-mascot.png" alt="">
                    <p>Cuttle's own mascot — the default for profiles without an avatar.</p>
                    <button type="button" class="control-button primary" data-mascot>Use mascot</button></div>`;
        }

        function render() {
            el.innerHTML = `<div class="orch-avp-tabs" role="tablist">${[['emoji', 'Emoji'], ['picture', 'Picture'], ['mascot', 'Mascot']]
                .map(([id, label]) => `<button type="button" role="tab" aria-selected="${tab === id}" data-tab="${id}">${label}</button>`).join('')}</div>
                <div class="orch-avp-body">${body()}</div>`;
        }

        function place() {
            const r = anchor.getBoundingClientRect();
            const w = el.offsetWidth;
            const h = el.offsetHeight;
            let left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
            let top = r.bottom + 8;
            if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 8);
            el.style.left = left + 'px';
            el.style.top = top + 'px';
        }

        async function upload(file) {
            const status = el && el.querySelector('.orch-avp-status');
            if (!file || typeof o.upload !== 'function') return;
            if (status) { status.className = 'orch-avp-status'; status.textContent = 'Uploading…'; }
            try {
                pick(await o.upload(file));
            } catch (err) {
                if (status) { status.className = 'orch-avp-status is-bad'; status.textContent = err.message || 'Upload failed'; }
            }
        }

        function bind() {
            el.addEventListener('click', (e) => {
                const t = e.target.closest('button, label');
                if (!t) return;
                if (t.dataset.tab) { tab = t.dataset.tab; render(); place(); return; }
                if (t.dataset.emoji) pick(t.dataset.emoji);
                else if (t.hasAttribute('data-mascot')) pick('cuttle');
            });
            el.addEventListener('submit', (e) => {
                e.preventDefault();
                const f = e.target;
                if (f.dataset.form === 'custom') {
                    const v = f.custom.value.trim();
                    if (v) pick(v);
                } else if (f.dataset.form === 'url') {
                    const v = f.url.value.trim();
                    const status = el.querySelector('.orch-avp-status');
                    if (M.safeImageSrc(v) && /^https:\/\//i.test(v)) pick(v);
                    else if (status) { status.className = 'orch-avp-status is-bad'; status.textContent = 'Use an https:// image URL.'; }
                }
            });
            el.addEventListener('change', (e) => {
                if (e.target.type === 'file') upload(e.target.files && e.target.files[0]);
            });
            el.addEventListener('keydown', (e) => {
                if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('orch-avp-drop')) {
                    e.preventDefault();
                    e.target.querySelector('input[type=file]').click();
                }
            });
            el.addEventListener('dragover', (e) => { if (e.target.closest('.orch-avp-drop')) { e.preventDefault(); e.target.closest('.orch-avp-drop').classList.add('is-over'); } });
            el.addEventListener('dragleave', (e) => { const d = e.target.closest('.orch-avp-drop'); if (d) d.classList.remove('is-over'); });
            el.addEventListener('drop', (e) => {
                const d = e.target.closest('.orch-avp-drop');
                if (!d) return;
                e.preventDefault();
                upload(e.dataTransfer.files && e.dataTransfer.files[0]);
            });
        }

        return {
            open(anchorEl, value) {
                if (el) { close(); return; }
                anchor = anchorEl;
                current = String(value || '');
                tab = M.avatarKind(current) === 'image' ? 'picture' : (M.avatarKind(current) === 'mascot' && current ? 'mascot' : 'emoji');
                el = document.createElement('div');
                el.className = 'orch-avp';
                el.setAttribute('role', 'dialog');
                el.setAttribute('aria-label', 'Choose avatar');
                document.body.appendChild(el);
                render();
                bind();
                place();
                anchor.setAttribute('aria-expanded', 'true');
                document.addEventListener('mousedown', onOutside, true);
                document.addEventListener('keydown', onKey, true);
                window.addEventListener('resize', close);
            },
            close,
            isOpen: () => !!el,
        };
    }

    root.CuttleAvatarPicker = { create };
})(typeof window !== 'undefined' ? window : globalThis);
