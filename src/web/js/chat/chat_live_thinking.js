/* Chat live thinking: the running turn's reasoning inside its live bubble.

   Source: live-status `thinking` (api.chat_live_extras → api.agent_harness
   .reasoning_reply.live_thinking), the same text the saved reply carries in
   its leading <think> block. Shown as a collapsed "Thinking" disclosure above
   the activity animation and status line of the live bubble (local
   #typing-indicator or remote #typing-indicator-remote); the animation keeps
   running. Collapsed by default; the reader's open/closed choice holds for the
   turn, across text updates and a rebuilt bubble. When the reply lands the
   bubble is replaced by the saved message, whose <think> block renders
   collapsed (chat_messages.js).

   Studio (`host.isStudio()`, default CuttleChatDisplay.isStudio()): the block
   opens by default as a compact THINKING box (CSS in studio/threads.css) with
   a faded top edge, so the newest lines show and older ones fade. The reader's
   own choice still wins for the turn. The live bubble only ever receives status
   and reasoning; the answer arrives as the saved reply that replaces the bubble,
   so the box goes with the bubble when the answer lands or the turn ends.

   A poll with no thinking never removes the block: reasoning does not vanish
   mid-turn, and a missed poll must not flicker it. Only another turn's
   (query id) block is dropped.

   host: { doc, currentSession(), sessionIdsEqual(a,b), formatThinking(text),
           afterPaint() } */
(function (root) {
    'use strict';
    const LIVE_CLASS = 'thinking-block--live';

    function create(host) {
        const doc = host.doc || root.document;
        // The reader's choice for the running turn: { queryId, open }.
        let choice = { queryId: '', open: false };

        function studioOn() {
            if (typeof host.isStudio === 'function') return !!host.isStudio();
            const display = root.CuttleChatDisplay;
            return !!(display && display.isStudio && display.isStudio());
        }

        function liveBubble() {
            return doc.getElementById('typing-indicator')
                || doc.getElementById('typing-indicator-remote');
        }

        function scrollToEnd(body) {
            if (body) body.scrollTop = body.scrollHeight;
        }

        function build(queryId) {
            const block = doc.createElement('details');
            block.className = 'thinking-block ' + LIVE_CLASS;
            block.dataset.queryId = queryId;
            block.innerHTML = '<summary>Thinking</summary><div class="thinking-live-body"></div>';
            // Classic: collapsed unless the reader opened it. Studio: open unless the reader closed it.
            block.open = choice.queryId === queryId ? choice.open : studioOn();
            block.addEventListener('toggle', () => {
                choice = { queryId: block.dataset.queryId || '', open: block.open };
                if (block.open) scrollToEnd(block.querySelector('.thinking-live-body'));
                try { host.afterPaint && host.afterPaint(); } catch (_) {}
            });
            return block;
        }

        /** Apply one live-status payload for `sessionId` to the live bubble. */
        function sync(live, sessionId) {
            if (!live) return;
            if (sessionId != null && !host.sessionIdsEqual(host.currentSession(), sessionId)) return;
            const bubble = liveBubble();
            const content = bubble && bubble.querySelector('.message-content');
            if (!content) return;
            const queryId = String(live.query_id || '');
            let block = content.querySelector('.' + LIVE_CLASS);
            if (block && queryId && block.dataset.queryId && block.dataset.queryId !== queryId) {
                block.remove();
                block = null;
            }
            const text = String(live.thinking || '').trim();
            if (!text) return;
            let created = false;
            if (!block) {
                block = build(queryId);
                content.insertBefore(block, content.firstChild);
                created = true;
            }
            if (block._cuttleThinking === text) return;
            block._cuttleThinking = text;
            const body = block.querySelector('.thinking-live-body');
            // Follow new reasoning unless the reader scrolled up to read.
            const following = created
                || (body.scrollHeight - body.scrollTop - body.clientHeight) < 24;
            body.innerHTML = host.formatThinking(text);
            if (studioOn()) {
                // The faded top edge only when there is more above the visible lines.
                body.classList.toggle('is-clipped', body.scrollHeight > body.clientHeight + 1);
            }
            if (following) scrollToEnd(body);
            if (created || block.open) {
                try { host.afterPaint && host.afterPaint(); } catch (_) {}
            }
        }

        return { sync };
    }

    root.CuttleChatLiveThinking = { create };
})(typeof window !== 'undefined' ? window : globalThis);
