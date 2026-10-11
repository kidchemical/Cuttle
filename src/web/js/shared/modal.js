/* Small shared controller for app-shell modal overlays. */
(function (root) {
    'use strict';

    function create(element) {
        if (!element) return null;
        return {
            element,
            open() {
                element.hidden = false;
                element.classList.add('active');
            },
            close() {
                element.classList.remove('active');
                element.hidden = true;
            },
            isOpen() { return !element.hidden && element.classList.contains('active'); },
        };
    }

    root.CuttleModal = Object.assign(root.CuttleModal || {}, { create });
    if (typeof module !== 'undefined' && module.exports) module.exports = { create };
})(typeof window !== 'undefined' ? window : globalThis);
