/* History DOM refreshes: keep the reader's row in view, never re-focus it. */
(function (root) {
    'use strict';
    const rendered = new WeakMap();

    function preserveScroll(container, update) {
        const top = container.scrollTop;
        const left = container.scrollLeft;
        const bounds = container.getBoundingClientRect();
        const anchor = top > 0 && Array.from(container.querySelectorAll('.chat-history-item')).find((item) => {
            const rect = item.getBoundingClientRect();
            return rect.height > 0 && rect.bottom > bounds.top && rect.top < bounds.bottom;
        });
        const id = anchor && anchor.dataset.sessionId;
        const offset = anchor && anchor.getBoundingClientRect().top - bounds.top;
        update();
        const replacement = id && Array.from(container.querySelectorAll('.chat-history-item')).find((item) =>
            item.dataset.sessionId === id && item.getBoundingClientRect().height > 0);
        container.scrollTop = replacement
            ? container.scrollTop + replacement.getBoundingClientRect().top - container.getBoundingClientRect().top - offset
            : top;
        container.scrollLeft = left;
    }

    function paint(container, html, enhance) {
        // Compare source markup, since tooltips and live indicators modify DOM.
        if (rendered.get(container) === html) {
            if (enhance) enhance();
            return;
        }
        preserveScroll(container, () => {
            container.innerHTML = html;
            rendered.set(container, html);
            if (enhance) enhance();
        });
    }

    root.CuttleChatHistoryView = { paint, preserveScroll };
})(globalThis);
