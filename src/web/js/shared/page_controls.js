/* Presentation for non-chat app pages. Native selects remain the value/change
 * source; the shared combobox owns the menu and keyboard interaction. */
(function () {
    'use strict';
    function accessibleName(select) {
        const label = select.getAttribute('aria-label');
        if (label) return label;
        const labelled = select.getAttribute('aria-labelledby');
        if (labelled) return labelled.split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
        const parent = select.closest('label');
        if (parent) {
            const copy = parent.cloneNode(true);
            copy.querySelectorAll('select, .cuttle-combo, button, input').forEach(node => node.remove());
            if (copy.textContent.trim()) return copy.textContent.trim();
        }
        const row = select.closest('.setting-item');
        return row?.querySelector('.setting-label strong')?.textContent.trim() || select.name || select.id || 'Choose an option';
    }

    function polish(root) {
        root.querySelectorAll('select:not([multiple]):not([data-native-select])').forEach(select => {
            if (select.size > 1) return;
            const existing = select.__cuttleSearchable;
            if (existing?.element.classList.contains('cuttle-combo--page')) return;
            const label = accessibleName(select);
            // Existing catalog pickers retain search; short option lists use
            // the identical menu without a redundant search field.
            const searchable = !!existing || /model|project|executor|provider/i.test(label);
            const combo = window.CuttleSearchableSelect.enhance(select, {
                floating: true, searchable, label, className: 'cuttle-combo--page',
            });
            if (!existing) {
                // Copy layout variants (Router's narrow/wide/effort controls).
                select.classList.forEach(name => {
                    if (['narrow', 'wide', 'tiny', 'agent', 'model', 'effort'].includes(name)) combo.element.classList.add('page-select-' + name);
                });
            }
        });
    }

    function init() {
        if (!document.body.classList.contains('compact-shell-page') || !window.CuttleSearchableSelect) return;
        polish(document.body);
        let pending = false;
        new MutationObserver(records => {
            if (pending || !records.some(record => Array.from(record.addedNodes).some(node =>
                node.nodeType === 1 && (node.matches('select') || node.querySelector('select'))))) return;
            pending = true;
            queueMicrotask(() => { pending = false; polish(document.body); });
        }).observe(document.body, { childList: true, subtree: true });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
