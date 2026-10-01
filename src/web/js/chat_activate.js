/* ================================================================
   Cuttle Chat — post-paint activation domain (chat_activate.js).
   Owner: container-scoped DOM activation for rendered assistant
   content: Vega chart embeds and code-copy buttons. No document,
   no window, no navigator, no timers here — every host capability
   arrives via explicit `deps`. Loaded before chat_page.js; the page
   owns orchestration (`activateEnhancements`: hljs, terminal/button
   execution wiring) and passes window/document-bound
   implementations. Render planning (placeholder HTML, including the
   `data-vega-spec` attribute consumed here) stays in
   `CuttleChatMessages`.

   Mirrors the page contract: already-handled elements are skipped
   (`__vega_done`, `.has-code-copy`), embeds without a Vega library
   are no-ops, invalid specs become inline errors, copy failures
   notify instead of marking, and the re-arm timer restores the
   button exactly.
   ================================================================ */
(function (root) {
    'use strict';

    function activateVegaEmbeds(containerEl, deps) {
        const d = deps || {};
        if (!containerEl) return;
        const nodes = containerEl.querySelectorAll
            ? containerEl.querySelectorAll('[data-vega-spec]')
            : [];
        if (!nodes.length) return;
        if (!d.vegaEmbed) {
            // CDN still loading — leave pending placeholder; cuttle-vega-ready retries.
            return;
        }
        nodes.forEach(async (el) => {
            if (el.__vega_done) return;
            el.__vega_done = true;
            const raw = el.getAttribute('data-vega-spec');
            if (!raw) {
                el.classList.add('vega-wrap--error');
                el.textContent = 'Empty Vega chart';
                return;
            }
            let spec;
            try {
                spec = JSON.parse(raw);
            } catch (err) {
                el.classList.add('vega-wrap--error');
                el.textContent = 'Invalid Vega JSON: ' + ((err && err.message) || 'parse error');
                return;
            }
            try {
                el.innerHTML = '';
                // Force transparent view — stock themes paint a solid analytics card bg.
                if (!spec.config) spec.config = {};
                if (!spec.config.background && spec.config.background !== null) {
                    spec.config.background = null;
                }
                if (spec.background === undefined) spec.background = null;
                if (!spec.config.view) spec.config.view = {};
                if (spec.config.view.stroke === undefined) spec.config.view.stroke = null;
                await d.vegaEmbed(el, spec, {
                    actions: false,
                    renderer: 'svg',
                    // No named theme — themes force opaque backgrounds.
                    config: {
                        background: null,
                        view: { stroke: null },
                    },
                });
            } catch (err) {
                el.classList.add('vega-wrap--error');
                el.textContent = 'Vega render failed: ' + ((err && err.message) || String(err));
            }
        });
        }

    function attachCodeCopyButtons(containerEl, deps) {
        const d = deps || {};
        containerEl.querySelectorAll('pre > code').forEach((codeEl) => {
            const pre = codeEl.parentElement;
            if (!pre || pre.querySelector(':scope > .code-copy-btn')) return;
            const btn = d.createElement('button');
            btn.type = 'button';
            btn.className = 'code-copy-btn';
            btn.title = 'Copy code';
            btn.setAttribute('aria-label', 'Copy code');
            btn.innerHTML = d.copyIcon;
            btn.addEventListener('click', async (ev) => {
                ev.preventDefault();
                ev.stopPropagation();
                const ok = await d.copyText(CuttleChatMessages.cleanCodeCopyText(codeEl.textContent));
                btn.classList.toggle('is-copied', ok);
                btn.title = ok ? 'Copied!' : 'Copy failed';
                if (ok) {
                    btn.innerHTML = d.copiedIcon;
                } else {
                    d.notifyError('Could not copy code block');
                }
                d.later(() => {
                    btn.classList.remove('is-copied');
                    btn.innerHTML = d.copyIcon;
                    btn.title = 'Copy code';
                }, 1500);
            });
            pre.classList.add('has-code-copy');
            pre.appendChild(btn);
        });
        }


    const api = {
        activateVegaEmbeds,
        attachCodeCopyButtons,
    };

    const ns = (root.CuttleChatActivate = root.CuttleChatActivate || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
