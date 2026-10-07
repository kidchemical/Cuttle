/* Settings presentation only: accessible names and keyboard activation.
 * Existing page handlers retain preference storage and save behavior. */
(function () {
    'use strict';
    function annotate(root) {
        root.querySelectorAll('.setting-item').forEach(row => {
            const title = row.querySelector('.setting-label strong');
            if (!title) return;
            const name = title.textContent.trim();
            row.querySelectorAll('input:not([type="checkbox"]), select, textarea').forEach(field => {
                if (!field.hasAttribute('aria-label') && !field.labels.length) {
                    field.setAttribute('aria-label', name);
                }
            });
            row.querySelectorAll('.toggle-switch').forEach(toggle => {
                toggle.tabIndex = 0;
                toggle.setAttribute('role', 'switch');
                toggle.setAttribute('aria-label', name);
                toggle.setAttribute('aria-checked', String(toggle.classList.contains('active')));
            });
        });
    }
    document.addEventListener('DOMContentLoaded', () => {
        const root = document.querySelector('.page-section');
        if (!root) return;
        annotate(root);
        root.addEventListener('keydown', event => {
            const toggle = event.target.closest('.toggle-switch');
            if (!toggle || !root.contains(toggle) || ![' ', 'Enter'].includes(event.key)) return;
            event.preventDefault();
            if (!event.repeat) toggle.click();
        });
        new MutationObserver(records => {
            records.forEach(record => {
                if (record.type === 'attributes' && record.target.matches('.toggle-switch')) {
                    record.target.setAttribute('aria-checked', String(record.target.classList.contains('active')));
                } else if (record.type === 'childList') {
                    annotate(root);
                }
            });
        }).observe(root, {subtree: true, childList: true, attributes: true, attributeFilter: ['class']});
    });
})();
