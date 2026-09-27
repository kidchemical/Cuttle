/* ================================================================
   Cuttle emoticons — classic text faces → Unicode emoji
   Used by the chat composer (live replace) and message history.
   ================================================================ */
(function (global) {
    'use strict';

    /**
     * Longer / more specific patterns first (also sorted by length below).
     * Each entry: [plain emoticon, emoji]
     */
    const EMOTICON_PAIRS = [
        [":'-(", '😢'],
        [":'(", '😢'],
        ['¯\\_(ツ)_/¯', '🤷'],
        [':shrug:', '🤷'],
        ['>:-)', '😏'],
        ['>:)', '😏'],
        [">:'(", '😠'],
        ['>:(', '😠'],
        ['O:)', '😇'],
        ['0:)', '😇'],
        ['</3', '💔'],
        ['<3', '❤️'],
        ['&lt;/3', '💔'],
        ['&lt;3', '❤️'],
        [':-D', '😃'],
        [':D', '😃'],
        ['xD', '😆'],
        ['XD', '😆'],
        [':-P', '😛'],
        [':P', '😛'],
        [':-p', '😛'],
        [':p', '😛'],
        [':-O', '😮'],
        [':O', '😮'],
        [':-o', '😮'],
        [':o', '😮'],
        [':-*', '😘'],
        [':*', '😘'],
        [':-)', '🙂'],
        [':)', '🙂'],
        [':-]', '🙂'],
        [':]', '🙂'],
        [':-(', '🙁'],
        [':(', '🙁'],
        [':-[', '🙁'],
        [':[', '🙁'],
        [';-|', '😐'],
        [':|', '😐'],
        [':-/', '😕'],
        [':/', '😕'],
        [':-\\', '😕'],
        [':\\', '😕'],
        [':-$', '😳'],
        [':$', '😳'],
        [':-X', '😣'],
        [':X', '😣'],
        [':-x', '😣'],
        [':x', '😣'],
        [':-S', '😖'],
        [':S', '😖'],
        [':-s', '😖'],
        [':s', '😖'],
        [':-3', '😺'],
        [':3', '😺'],
        [';^)', '😗'],
        [';)', '😉'],
        [';)', '😉'],
        ['B-)', '😎'],
        ['B)', '😎'],
        ['8-)', '😎'],
        ['8)', '😎'],
        ['^_^', '😊'],
        ['-_-', '😑'],
        ['T_T', '😭'],
        ['T.T', '😭'],
        ['^^', '😊'],
    ];

    const seen = new Set();
    const PAIRS = [];
    for (let i = 0; i < EMOTICON_PAIRS.length; i++) {
        const pair = EMOTICON_PAIRS[i];
        if (seen.has(pair[0])) continue;
        seen.add(pair[0]);
        PAIRS.push(pair);
    }
    PAIRS.sort(function (a, b) { return b[0].length - a[0].length; });

    function escapeRegExp(s) {
        return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    /**
     * Convert classic emoticons to emoji in plain text (or HTML-escaped text).
     * Skips matches glued to word characters (avoids http://).
     */
    function convertEmoticons(text) {
        if (!text || typeof text !== 'string') return text;
        // Boundary: start / whitespace / common openers → emoticon → end / whitespace / closers
        const pre = '(^|[\\s\\(\\[\\{"\'])';
        const postDefault = '(?=$|[\\s\\)\\]\\}\\.,!?;:"\'])';
        // For :/ avoid consuming the second slash in http://
        const postSlash = '(?=$|[\\s\\)\\]\\}\\.,!?;"\'])';
        let out = text;
        for (let i = 0; i < PAIRS.length; i++) {
            const emo = PAIRS[i][0];
            const emoji = PAIRS[i][1];
            const post = (emo === ':/' || emo === ':-/') ? postSlash : postDefault;
            const re = new RegExp(pre + '(' + escapeRegExp(emo) + ')' + post, 'g');
            out = out.replace(re, function (_m, prefix) { return prefix + emoji; });
        }
        return out;
    }

    /**
     * Convert emoticons in HTML, leaving tags and <code>/<pre> contents alone.
     */
    function convertEmoticonsInHtml(html) {
        if (!html || typeof html !== 'string') return html;
        return html.replace(
            /(<(?:code|pre)\b[^>]*>[\s\S]*?<\/(?:code|pre)>)|(<[^>]+>)|([^<]+)/gi,
            function (_m, codeBlock, tag, text) {
                if (codeBlock) return codeBlock;
                if (tag) return tag;
                return convertEmoticons(text);
            }
        );
    }

    /**
     * Replace emoticons only around the caret (the face the user just finished typing).
     * Does not rescan the whole draft — pasted ":)" elsewhere stays literal.
     * Returns true if the value changed.
     */
    function applyEmoticonsToTextarea(textarea) {
        if (!textarea) return false;
        if (textarea.dataset && textarea.dataset.emoticonComposing === '1') return false;
        const value = textarea.value;
        if (!value) return false;

        const caret = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : value.length;
        // Only consider a short window ending at the caret (longest emoticon ~12 chars).
        const windowStart = Math.max(0, caret - 16);
        const before = value.slice(0, windowStart);
        const region = value.slice(windowStart, caret);
        const after = value.slice(caret);

        const convertedRegion = convertEmoticons(region);
        if (convertedRegion === region) return false;

        textarea.value = before + convertedRegion + after;
        const newCaret = before.length + convertedRegion.length;
        try {
            textarea.setSelectionRange(newCaret, newCaret);
        } catch (_) {}
        return true;
    }

    function wireTextarea(textarea) {
        if (!textarea || textarea.dataset.emoticonWired === '1') return;
        textarea.dataset.emoticonWired = '1';
        textarea.addEventListener('compositionstart', function () {
            textarea.dataset.emoticonComposing = '1';
        });
        textarea.addEventListener('compositionend', function () {
            textarea.dataset.emoticonComposing = '0';
            applyEmoticonsToTextarea(textarea);
        });
        textarea.addEventListener('input', function () {
            applyEmoticonsToTextarea(textarea);
        });
    }

    global.CuttleEmoticons = {
        convert: convertEmoticons,
        convertInHtml: convertEmoticonsInHtml,
        applyToTextarea: applyEmoticonsToTextarea,
        wireTextarea: wireTextarea,
        pairs: PAIRS.slice(),
    };
})(typeof window !== 'undefined' ? window : globalThis);
