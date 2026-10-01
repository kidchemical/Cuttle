/* ================================================================
   Cuttle Chat — markdown block/inline rendering (chat_markdown.js).
   Owner: pure markdown planning — inline spans, GFM pipe tables, and
   the block line loop (headers, rules, tables, quotes, lists,
   paragraphs, block-placeholder passthrough). No document, no window,
   no state: takes already-escaped text, returns HTML strings; the page
   keeps escapeHtml, placeholder restore order, and DOM activation.
   New block placeholder kinds (owner: whoever mints the placeholder)
   must extend BLOCK_PLACEHOLDER_RE below to keep <p>-wrapping off.
   Loaded after chat_messages.js, before chat_page.js.
   ================================================================ */
(function (root) {
    'use strict';

    // Block-level placeholders (code, cards, forms, buttons) — never
    // wrapped in <p>, or browsers auto-close the paragraph and break
    // <pre>/cards. Minted by extract owners; recognized here for routing.
    const BLOCK_PLACEHOLDER_RE = /^\{\{CUTTLE_(?:CODE|THINK|TOOL|TRACE|PROGRESS|TERM|MEDIA|VEGA|FORM|AF|BTN)_\d+\}\}$/;

    function formatInlineMarkdown(text) {
        // Inline code (before bold/italic to protect it)
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    
        // Bold
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    
        // Italic (*…* or _…_). Underscores skip snake_case (no letter/digit/_ adjacent).
        text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
        text = text.replace(
            /(^|[^A-Za-z0-9_])_([^_\s][^_]*)_(?![A-Za-z0-9_])/g,
            '$1<em>$2</em>'
        );
    
        return text;
    }

    function splitMarkdownTableRow(line) {
        let s = String(line || '').trim();
        if (s.startsWith('|')) s = s.slice(1);
        if (s.endsWith('|')) s = s.slice(0, -1);
        return s.split('|').map((c) => c.trim());
    }

    function isMarkdownTableSeparatorRow(line) {
        const cells = splitMarkdownTableRow(line);
        if (cells.length < 2) return false;
        return cells.every((c) => /^:?-{3,}:?$/.test(String(c).trim()));
    }

    function looksLikeMarkdownTableRow(line) {
        const t = String(line || '').trim();
        if (!t.includes('|')) return false;
        return splitMarkdownTableRow(t).length >= 2;
    }

    function markdownTableAlignFromSep(cell) {
        const c = String(cell || '').trim();
        const left = c.startsWith(':');
        const right = c.endsWith(':');
        if (left && right) return 'center';
        if (right) return 'right';
        if (left) return 'left';
        return '';
    }

    function renderMarkdownTableHtml(headerCells, aligns, bodyRows) {
        const colCount = Math.max(
            headerCells.length,
            aligns.length,
            ...bodyRows.map((r) => r.length),
            0
        );
        const alignAt = (i) => aligns[i] || '';
        const alignAttr = (i) => {
            const a = alignAt(i);
            return a ? ` style="text-align:${a}"` : '';
        };
        const pad = (row) => {
            const out = row.slice(0, colCount);
            while (out.length < colCount) out.push('');
            return out;
        };
        let html = '<div class="message-table-wrap"><table class="message-table"><thead><tr>';
        pad(headerCells).forEach((cell, i) => {
            html += `<th${alignAttr(i)}>${formatInlineMarkdown(cell)}</th>`;
        });
        html += '</tr></thead><tbody>';
        bodyRows.forEach((row) => {
            html += '<tr>';
            pad(row).forEach((cell, i) => {
                html += `<td${alignAttr(i)}>${formatInlineMarkdown(cell)}</td>`;
            });
            html += '</tr>';
        });
        html += '</tbody></table></div>';
        return html;
    }

    /** Block line loop: escaped text in, joined block HTML out.
        Placeholders pass through bare for the page restore passes. */
    function renderMarkdownBlocks(text) {
            const lines = text.split('\n');
            const formatted = [];
            let inList = false;
            let listItems = [];
            let consecutiveEmpty = 0;
    
            for (let i = 0; i < lines.length; i++) {
                let line = lines[i];
        
                // Track empty lines
                if (line.trim() === '') {
                    consecutiveEmpty++;
                    // Only add one break for consecutive empty lines
                    if (consecutiveEmpty === 1 && formatted.length > 0) {
                        // Check if last element was a block element (header, list, etc)
                        const lastElement = formatted[formatted.length - 1];
                        if (!lastElement.startsWith('<h') && !lastElement.startsWith('<ul>') && !lastElement.startsWith('<ol>') && !lastElement.startsWith('<pre>') && !lastElement.startsWith('<blockquote') && !lastElement.startsWith('<hr') && !lastElement.startsWith('<div class="message-table-wrap"') && !BLOCK_PLACEHOLDER_RE.test(lastElement)) {
                            formatted.push('<br>');
                        }
                    }
                    continue;
                }
        
                consecutiveEmpty = 0;

                // Block-level placeholders (code, cards, etc.) — never wrap in <p>
                // or browsers auto-close the paragraph and break <pre>/cards.
                if (BLOCK_PLACEHOLDER_RE.test(line.trim())) {
                    if (inList) {
                        const tag = inList === 'ordered' ? 'ol' : 'ul';
                        formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
                        listItems = [];
                        inList = false;
                    }
                    formatted.push(line.trim());
                    continue;
                }
        
                // Headers
                if (line.match(/^#{1,6}\s/)) {
                    // Close any open list
                    if (inList) {
                        const tag = inList === 'ordered' ? 'ol' : 'ul';
                        formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
                        listItems = [];
                        inList = false;
                    }
            
                    const level = line.match(/^#+/)[0].length;
                    const content = line.replace(/^#+\s/, '');
                    formatted.push(`<h${level} class="message-header">${formatInlineMarkdown(content)}</h${level}>`);
                    continue;
                }

                // Horizontal rule: --- / *** / ___
                if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line.trim())) {
                    if (inList) {
                        const tag = inList === 'ordered' ? 'ol' : 'ul';
                        formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
                        listItems = [];
                        inList = false;
                    }
                    formatted.push('<hr class="message-hr">');
                    continue;
                }

                // GFM pipe tables: header | sep | rows (must come before generic paragraphs)
                if (
                    looksLikeMarkdownTableRow(line)
                    && i + 1 < lines.length
                    && isMarkdownTableSeparatorRow(lines[i + 1])
                ) {
                    if (inList) {
                        const tag = inList === 'ordered' ? 'ol' : 'ul';
                        formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
                        listItems = [];
                        inList = false;
                    }
                    const headerCells = splitMarkdownTableRow(line);
                    const aligns = splitMarkdownTableRow(lines[i + 1]).map(markdownTableAlignFromSep);
                    i += 2;
                    const bodyRows = [];
                    while (
                        i < lines.length
                        && looksLikeMarkdownTableRow(lines[i])
                        && !isMarkdownTableSeparatorRow(lines[i])
                    ) {
                        bodyRows.push(splitMarkdownTableRow(lines[i]));
                        i++;
                    }
                    i--; // for-loop will advance
                    formatted.push(renderMarkdownTableHtml(headerCells, aligns, bodyRows));
                    continue;
                }

                // Blockquotes: > quote  (escaped to &gt; before this loop)
                // Callouts: > [!WARNING] (optionally with text on the same line)
                if (/^&gt;/.test(line)) {
                    if (inList) {
                        const tag = inList === 'ordered' ? 'ol' : 'ul';
                        formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
                        listItems = [];
                        inList = false;
                    }
                    const quoteLines = [];
                    let calloutKind = null;
                    while (i < lines.length) {
                        const qLine = lines[i];
                        if (qLine.trim() === '') break;
                        if (!/^&gt;/.test(qLine)) break;
                        const body = qLine.replace(/^&gt;\s?/, '');
                        const calloutMatch = body.match(/^\[!(WARNING|WORKSPACE|CAUTION|NOTE)\]\s*(.*)$/i);
                        if (calloutMatch && !calloutKind && quoteLines.length === 0) {
                            const kind = calloutMatch[1].toUpperCase();
                            calloutKind = (kind === 'NOTE') ? 'note' : 'warning';
                            const rest = (calloutMatch[2] || '').trim();
                            if (rest) quoteLines.push(rest);
                            i++;
                            continue;
                        }
                        quoteLines.push(body);
                        i++;
                    }
                    i--; // for-loop will advance
                    const quoteHtml = quoteLines
                        .map((ql) => formatInlineMarkdown(ql))
                        .join('<br>');
                    const bqClass = calloutKind === 'warning'
                        ? 'message-blockquote message-blockquote--warning'
                        : 'message-blockquote';
                    const labelHtml = calloutKind === 'warning'
                        ? '<span class="message-callout-label">Warning</span>'
                        : '';
                    formatted.push(`<blockquote class="${bqClass}">${labelHtml}${quoteHtml || ''}</blockquote>`);
                    continue;
                }
        
                // Unordered lists
                if (line.match(/^[-*]\s/)) {
                    const content = line.replace(/^[-*]\s/, '');
                    listItems.push(`<li>${formatInlineMarkdown(content)}</li>`);
                    inList = true;
                    continue;
                }
        
                // Ordered lists
                if (line.match(/^\d+\.\s/)) {
                    if (!inList || listItems.length === 0 || inList !== 'ordered') {
                        // Close unordered list if open
                        if (inList && listItems.length > 0) {
                            formatted.push('<ul>' + listItems.join('') + '</ul>');
                            listItems = [];
                        }
                        inList = 'ordered';
                    }
                    const content = line.replace(/^\d+\.\s/, '');
                    listItems.push(`<li>${formatInlineMarkdown(content)}</li>`);
                    continue;
                }
        
                // Close list if we hit a non-list line
                if (inList && listItems.length > 0) {
                    const tag = inList === 'ordered' ? 'ol' : 'ul';
                    formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
                    listItems = [];
                    inList = false;
                }
        
                // Regular text with inline formatting
                formatted.push(`<p>${formatInlineMarkdown(line)}</p>`);
            }
    
            // Close any remaining list
            if (inList && listItems.length > 0) {
                const tag = inList === 'ordered' ? 'ol' : 'ul';
                formatted.push(`<${tag}>` + listItems.join('') + `</${tag}>`);
            }
        return formatted.join('');
    }

    const api = {
        formatInlineMarkdown,
        splitMarkdownTableRow,
        isMarkdownTableSeparatorRow,
        looksLikeMarkdownTableRow,
        markdownTableAlignFromSep,
        renderMarkdownTableHtml,
        renderMarkdownBlocks,
    };

    const ns = (root.CuttleChatMarkdown = root.CuttleChatMarkdown || {});
    Object.assign(ns, api);
    if (typeof module !== 'undefined' && module.exports) {
        Object.assign(module.exports, api);
    }
    // NOTE: `globalThis` directly (not `typeof window ? window`) so node
    // importers that later declare a lexical `window` don't hit TDZ.
})(globalThis);
