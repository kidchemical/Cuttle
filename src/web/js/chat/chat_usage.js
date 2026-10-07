/* Per-turn token usage normalization and footer markup. Pure; no DOM or I/O. */
(function (root) {
    'use strict';
    function formatTokenCount(n) {
        const v = Number(n);
        if (!Number.isFinite(v) || v < 0) return '0';
        if (v >= 1_000_000) {
            return (v / 1_000_000).toFixed(v >= 10_000_000 ? 0 : 1).replace(/\.0$/, '') + 'M';
        }
        if (v >= 1000) {
            return (v / 1000).toFixed(v >= 10_000 ? 0 : 1).replace(/\.0$/, '') + 'k';
        }
        return String(Math.round(v));
    }

    function formatUsageCostUsd(cost, estimated) {
        if (cost == null) return null;
        const v = Number(cost);
        if (!Number.isFinite(v) || v < 0) return null;
        const prefix = estimated ? '~$' : '$';
        if (v === 0) return prefix + '0';
        if (v < 0.01) return prefix + v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
        if (v < 1) return prefix + v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');
        return prefix + v.toFixed(2);
    }

    function normalizeUsagePayload(raw, metadata) {
        if (!raw || typeof raw !== 'object') return null;
        const pt = Number(
            raw.prompt_tokens != null ? raw.prompt_tokens
                : (raw.input_tokens != null ? raw.input_tokens : raw.inputTokens)
        ) || 0;
        const ct = Number(
            raw.completion_tokens != null ? raw.completion_tokens
                : (raw.output_tokens != null ? raw.output_tokens : raw.outputTokens)
        ) || 0;
        let total = Number(raw.total_tokens != null ? raw.total_tokens : raw.totalTokens) || 0;
        if (!total && (pt || ct)) total = pt + ct;
        const cacheRead = Number(
            raw.cache_read_tokens != null ? raw.cache_read_tokens
                : (raw.cacheReadTokens != null ? raw.cacheReadTokens
                    : (raw.cached_input_tokens != null ? raw.cached_input_tokens
                        : (raw.cached_tokens != null ? raw.cached_tokens : 0)))
        ) || 0;
        const cacheWrite = Number(
            raw.cache_write_tokens != null ? raw.cache_write_tokens
                : (raw.cacheWriteTokens != null ? raw.cacheWriteTokens : 0)
        ) || 0;
        let cost = raw.cost;
        if (cost != null) {
            cost = Number(cost);
            if (!Number.isFinite(cost) || cost < 0) cost = null;
        } else {
            cost = null;
        }
        if (!pt && !ct && cost == null && !cacheRead && !cacheWrite) return null;
        const meta = metadata || {};
        const chips = (meta.slash_command || {}).chips || [];
        const agent = String(raw.agent_id || (meta.routing_badge || {}).agent ||
            (chips[0] && (chips[0].category || chips[0].meta || chips[0].prefix)) ||
            (meta.cursor_run ? 'cursor' : '')).replace(/^\//, '').split(/[\s·]/)[0].toLowerCase();
        // Old saved footers lack the convention stamp. Use their harness badge,
        // never a cache/input ratio: a small cache can still be additive.
        let inclusive = typeof raw.cache_inclusive === 'boolean' ? raw.cache_inclusive : null;
        if (inclusive == null) {
            if (['muse', 'codex'].includes(agent)) inclusive = true;
            else if (['cursor', 'claude', 'opencode', 'deepseek'].includes(agent)) inclusive = false;
        }
        if (inclusive === false) total = pt + ct + cacheRead + cacheWrite;
        const out = {
            prompt_tokens: pt,
            completion_tokens: ct,
            total_tokens: total,
            cache_read_tokens: cacheRead,
            cache_write_tokens: cacheWrite,
            cache_inclusive: inclusive,
            cost,
            cost_estimated: !!raw.cost_estimated,
            model: raw.model ? String(raw.model) : '',
        };
        // Pass explicit context snapshots through so the gauge can seed
        // from the footer object instead of refetching (see chat_page.js).
        for (const key of ('context_tokens,peak_context_tokens,contextTokens,reasoning_tokens').split(',')) {
            if (raw[key] != null && out[key] == null) out[key] = raw[key];
        }
        return out;
    }

    // Compact footer: arrows + numbers only (no text labels — mobile layout).
    // Inclusion info lives in title tooltips. Inclusive input math stays:
    // additive harnesses (cache_inclusive === false) fold cache into input.
    function getMessageUsageHtml(usage, escapeHtml) {
        const u = normalizeUsagePayload(usage);
        if (!u) return '';
        const parts = [];
        if (u.prompt_tokens || u.completion_tokens || u.cache_read_tokens || u.cache_write_tokens) {
            const input = u.prompt_tokens + (u.cache_inclusive === false ? u.cache_read_tokens + u.cache_write_tokens : 0);
            const known = u.cache_inclusive != null;
            const inputTip = known ? 'Total input tokens, including cached input' : 'Provider-reported input; cache convention unavailable';
            parts.push(
                `<span class="message-usage-in" title="${escapeHtml(inputTip)}">↑ ${escapeHtml(formatTokenCount(input))}</span>`
            );
            if (u.cache_read_tokens > 0) {
                let tip = 'Cached input tokens';
                if (known && input > 0) {
                    const pct = Math.round((1000 * u.cache_read_tokens) / input) / 10;
                    const pctLabel = Number.isInteger(pct) ? String(pct) : pct.toFixed(1);
                    tip = `Cached input tokens (${pctLabel}% of input; already included above)`;
                } else {
                    tip = 'Provider-reported cache reads; may be included in reported input';
                }
                parts.push(
                    `<span class="message-usage-cache" title="${escapeHtml(tip)}">↑ ${escapeHtml(formatTokenCount(u.cache_read_tokens))}</span>`
                );
            }
            parts.push(
                `<span class="message-usage-out" title="Output tokens">↓ ${escapeHtml(formatTokenCount(u.completion_tokens))}</span>`
            );
        }
        const costLabel = formatUsageCostUsd(u.cost, u.cost_estimated);
        if (costLabel) {
            const tip = u.cost_estimated
                ? (u.cache_read_tokens > 0
                    ? 'Estimated from models.dev list prices (cache-adjusted)'
                    : 'Estimated from models.dev list prices')
                : 'Cost reported by the agent';
            parts.push(
                `<span class="message-usage-cost" title="${escapeHtml(tip)}">${escapeHtml(costLabel)}</span>`
            );
        }
        if (!parts.length) return '';
        return `<div class="message-usage" aria-label="Token usage">${parts.join('<span class="message-usage-sep">·</span>')}</div>`;
    }

    const api = { normalize: normalizeUsagePayload, render: getMessageUsageHtml,
        formatTokenCount, formatUsageCostUsd };
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.CuttleChatUsage = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
