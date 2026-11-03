/**
 * CuttleInterfaceTextScale — Settings → Text & fonts → Interface size.
 *
 * Scales font sizes only (layout, icons and spacing stay put) across the
 * whole page, without a hand-maintained selector list:
 *   1. Every same-origin stylesheet rule with an absolute font-size (px or
 *      a size keyword) gets a twin in one override <style> with the same
 *      selector and media/container/supports context:
 *      `font-size: calc(<value> * k)`. Same selector = same specificity and
 *      the override sheet comes last, so the twin wins (!important kept).
 *   2. <html> gets `font-size: calc(<its size> * k)`, so rem/em/% sizes and
 *      text with no size rule follow.
 *   3. Inline `style="font-size: …px"` set from JS is scaled in place
 *      (original kept in data-cuttle-fs) and watched for new elements.
 * Embedded editors/terminals (Monaco, xterm) measure their own glyphs and
 * are skipped. k = 1 removes everything.
 *
 * Loaded on demand by js/shared/ui_boot.js when the pref is set; ui_boot
 * calls apply(k) on every refresh. Cuttle-free: no page globals, no storage.
 */
(function (global) {
    'use strict';

    var STYLE_ID = 'cuttle-interface-text-scale';
    var SKIP_SELECTOR = /monaco|xterm/i;
    var KEYWORDS = {
        'xx-small': 9, 'x-small': 10, small: 13, medium: 16,
        large: 18, 'x-large': 24, 'xx-large': 32, 'xxx-large': 48
    };
    var k = 1;
    var styleEl = null;
    var headObserver = null;
    var bodyObserver = null;
    var rebuildTimer = null;

    function absoluteSize(value) {
        var v = String(value || '').trim().toLowerCase();
        if (/^-?[\d.]+px$/.test(v)) return v;
        if (Object.prototype.hasOwnProperty.call(KEYWORDS, v)) return KEYWORDS[v] + 'px';
        return null;
    }

    function scaled(px) {
        return 'calc(' + px + ' * ' + k + ')';
    }

    /** Wrap `body` in the group rules (media/container/supports) around rule. */
    function wrap(rule, body) {
        var p = rule.parentRule;
        while (p) {
            var t = p.constructor && p.constructor.name;
            if (t === 'CSSMediaRule') body = '@media ' + p.conditionText + '{' + body + '}';
            else if (t === 'CSSSupportsRule') body = '@supports ' + p.conditionText + '{' + body + '}';
            else if (t === 'CSSContainerRule') body = '@container ' + (p.containerName ? p.containerName + ' ' : '') + p.containerQuery + '{' + body + '}';
            else if (t === 'CSSLayerBlockRule') body = '@layer ' + (p.name || '') + '{' + body + '}';
            p = p.parentRule;
        }
        return body;
    }

    function collect(rules, out) {
        for (var i = 0; i < rules.length; i++) {
            var r = rules[i];
            if (r.styleSheet) { // @import
                try { collect(r.styleSheet.cssRules, out); } catch (_) {}
                continue;
            }
            if (r.cssRules && !r.selectorText) { collect(r.cssRules, out); continue; }
            if (!r.selectorText || !r.style) continue;
            if (SKIP_SELECTOR.test(r.selectorText)) continue;
            var size = absoluteSize(r.style.getPropertyValue('font-size'));
            if (!size) continue;
            var imp = r.style.getPropertyPriority('font-size') === 'important' ? ' !important' : '';
            out.push(wrap(r, r.selectorText + '{font-size:' + scaled(size) + imp + '}'));
            if (r.cssRules && r.cssRules.length) collect(r.cssRules, out); // nested CSS
        }
    }

    function rootSize() {
        // Measured with our override off, so a rebuild never compounds.
        var px = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
        return px + 'px';
    }

    function rebuild() {
        rebuildTimer = null;
        if (styleEl) styleEl.textContent = '';
        if (k === 1) return;
        var out = [':root{font-size:' + scaled(rootSize()) + '}'];
        var sheets = document.styleSheets;
        for (var i = 0; i < sheets.length; i++) {
            if (sheets[i].ownerNode === styleEl) continue;
            try { collect(sheets[i].cssRules, out); } catch (_) { /* cross-origin (fonts CDN) */ }
        }
        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = STYLE_ID;
        }
        styleEl.textContent = out.join('\n');
        // Always last in <head>, so equal-specificity twins win.
        if (styleEl.parentNode !== document.head || styleEl.nextElementSibling) {
            document.head.appendChild(styleEl);
        }
    }

    function scheduleRebuild() {
        if (rebuildTimer) return;
        rebuildTimer = setTimeout(rebuild, 60);
    }

    function scaleInline(el) {
        if (!el || el.nodeType !== 1 || !el.style) return;
        if (el.closest && el.closest('.monaco-editor, .xterm')) return;
        var orig = el.getAttribute('data-cuttle-fs');
        var mine = el.getAttribute('data-cuttle-fs-set');
        var cur = el.style.fontSize;
        // Still the value we wrote → its original is `orig`; anything else
        // means a writer set a fresh size, which becomes the new original.
        var base = (orig != null && cur === mine) ? orig : absoluteSize(cur);
        if (!base) return;
        var want = k === 1 ? base : scaled(base);
        if (orig === base && cur === want) return;
        el.setAttribute('data-cuttle-fs', base);
        el.style.fontSize = want;
        el.setAttribute('data-cuttle-fs-set', el.style.fontSize);
    }

    function scanInline(rootEl) {
        if (!rootEl || !rootEl.querySelectorAll) return;
        if (rootEl.matches && rootEl.matches('[style*="font-size"]')) scaleInline(rootEl);
        var els = rootEl.querySelectorAll('[style*="font-size"]');
        for (var i = 0; i < els.length; i++) scaleInline(els[i]);
    }

    function watch() {
        if (!headObserver && document.head) {
            headObserver = new MutationObserver(function (records) {
                for (var i = 0; i < records.length; i++) {
                    var nodes = records[i].addedNodes;
                    for (var j = 0; j < nodes.length; j++) {
                        var n = nodes[j];
                        if (n === styleEl) continue;
                        if (n.tagName === 'STYLE') scheduleRebuild();
                        else if (n.tagName === 'LINK') n.addEventListener('load', scheduleRebuild, { once: true });
                    }
                }
            });
            headObserver.observe(document.head, { childList: true });
        }
        if (!bodyObserver && document.body) {
            bodyObserver = new MutationObserver(function (records) {
                for (var i = 0; i < records.length; i++) {
                    var r = records[i];
                    if (r.type === 'attributes') scaleInline(r.target);
                    else for (var j = 0; j < r.addedNodes.length; j++) scanInline(r.addedNodes[j]);
                }
            });
            bodyObserver.observe(document.body, {
                childList: true, subtree: true, attributes: true, attributeFilter: ['style']
            });
            scanInline(document.body);
        }
    }

    function apply(factor) {
        var next = Number(factor);
        k = Number.isFinite(next) && next > 0 ? next : 1;
        rebuild();
        if (document.body) scanInline(document.body);
        if (k !== 1) watch();
    }

    function onReady() {
        watch();
        rebuild();
        if (document.body) scanInline(document.body);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady);
    // Late stylesheets (deferred links) settle after load.
    global.addEventListener('load', scheduleRebuild);

    global.CuttleInterfaceTextScale = { apply: apply, factor: function () { return k; } };
})(window);
