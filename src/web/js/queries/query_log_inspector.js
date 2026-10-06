/* Query log inspector — in-pane overlay + optional pop-out. */
(function () {
    'use strict';

    var pollTimer = null;
    var currentId = '';
    var lastTab = 'timeline';
    var lastStamp = '';
    var openKeys = Object.create(null);

    function el(html) {
        var d = document.createElement('div');
        d.innerHTML = html.trim();
        return d.firstElementChild;
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function payloadStamp(data) {
        var ev = (data && data.events) || [];
        var last = ev.length ? ev[ev.length - 1] : {};
        return [
            lastTab,
            data && data.executing,
            ev.length,
            last.t || '',
            data && data.total_tokens,
            (data && data.sent && data.sent.chars) || 0,
        ].join('|');
    }

    function eventKey(i) {
        return currentId + ':' + i;
    }

    function oneLiner(ev) {
        if (!ev) return '';
        if (ev.summary) return String(ev.summary);
        if (ev.mode) return String(ev.mode);
        if (ev.chars != null) return String(ev.chars) + ' chars' + (ev.resume ? ' · resume' : '');
        if (ev.text) return String(ev.text).replace(/\s+/g, ' ').trim().slice(0, 110);
        if (ev.preview) return String(ev.preview).replace(/\s+/g, ' ').trim().slice(0, 110);
        return '';
    }

    function eventHasBody(ev) {
        if (!ev) return false;
        return !!(
            ev.text ||
            ev.args ||
            ev.preview ||
            (ev.layers && ev.layers.length) ||
            ev.error ||
            (ev.chars != null && ev.preview)
        );
    }

    function ensureDom() {
        var overlay = document.getElementById('queryLogOverlay');
        if (overlay) return overlay;
        overlay = el(
            '<div class="query-log-overlay" id="queryLogOverlay" hidden role="dialog" aria-modal="true" aria-labelledby="queryLogTitle">' +
            '<div class="query-log-sheet">' +
            '<header class="query-log-head">' +
            '<div><h2 class="query-log-title" id="queryLogTitle">Query log</h2>' +
            '<p class="query-log-meta" id="queryLogMeta"></p></div>' +
            '<div class="query-log-actions">' +
            '<button type="button" id="queryLogPopout" title="Open in a new window">Pop out</button>' +
            '<button type="button" id="queryLogClose" aria-label="Close">Close</button>' +
            '</div></header>' +
            '<div class="query-log-tabs" id="queryLogTabs">' +
            '<button type="button" data-tab="timeline" class="is-on">Timeline</button>' +
            '<button type="button" data-tab="known">Known</button>' +
            '<button type="button" data-tab="sent">Sent</button>' +
            '<button type="button" data-tab="json">JSON</button>' +
            '</div>' +
            '<div class="query-log-body" id="queryLogBody"></div>' +
            '</div></div>'
        );
        var host = document.querySelector('.chat-layout') || document.body;
        host.appendChild(overlay);
        overlay.addEventListener('click', function (e) {
            if (e.target === overlay && !document.body.classList.contains('query-log-standalone')) {
                closeInspector();
            }
        });
        overlay.querySelector('#queryLogClose').addEventListener('click', closeInspector);
        overlay.querySelector('#queryLogPopout').addEventListener('click', function () {
            if (!currentId) return;
            window.open('/query_log.html?id=' + encodeURIComponent(currentId), 'cuttle-query-log-' + currentId,
                'noopener,noreferrer,width=720,height=900');
        });
        overlay.querySelector('#queryLogTabs').addEventListener('click', function (e) {
            var btn = e.target.closest('[data-tab]');
            if (!btn) return;
            lastTab = btn.getAttribute('data-tab') || 'timeline';
            lastStamp = '';
            overlay.querySelectorAll('[data-tab]').forEach(function (b) {
                b.classList.toggle('is-on', b === btn);
            });
            var cached = overlay._payload;
            if (cached) renderBody(cached);
            if (lastTab === 'timeline') tick();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && overlay && !overlay.hidden) closeInspector();
        });
        if (document.body.classList.contains('query-log-standalone')) {
            overlay.querySelector('#queryLogClose').hidden = true;
            overlay.querySelector('#queryLogPopout').hidden = true;
        }
        return overlay;
    }

    function fallbackJsonHtml(raw) {
        var re = /("(?:\\.|[^"\\])*")(\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
        var html = '';
        var last = 0;
        var m;
        while ((m = re.exec(raw))) {
            html += esc(raw.slice(last, m.index));
            if (m[1] != null && m[2]) {
                html += '<span class="qlj-key">' + esc(m[1]) + '</span>' + esc(m[2]);
            } else if (m[1] != null) {
                html += '<span class="qlj-str">' + esc(m[0]) + '</span>';
            } else if (m[0] === 'true' || m[0] === 'false' || m[0] === 'null') {
                html += '<span class="qlj-kw">' + m[0] + '</span>';
            } else {
                html += '<span class="qlj-num">' + m[0] + '</span>';
            }
            last = m.index + m[0].length;
        }
        html += esc(raw.slice(last));
        return html;
    }

    function jsonToHighlightedHtml(value) {
        var raw;
        try {
            raw = JSON.stringify(value, null, 2);
        } catch (_) {
            raw = String(value);
        }
        if (window.hljs && typeof window.hljs.highlight === 'function') {
            try {
                var out = window.hljs.highlight(raw, { language: 'json', ignoreIllegals: true });
                if (out && out.value) return out.value;
            } catch (_) {}
        }
        return fallbackJsonHtml(raw);
    }

    function relSecs(ev, t0) {
        var t = Number(ev && ev.t);
        if (!isFinite(t) || !isFinite(t0)) return null;
        var d = t - t0;
        if (!isFinite(d) || d < 0) d = 0;
        return d;
    }

    function formatRel(d) {
        if (d == null) return '';
        var s = Math.floor(d);
        if (s < 60) return s + 's';
        var m = Math.floor(s / 60);
        var r = s % 60;
        if (m < 60) return m + 'm ' + r + 's';
        var h = Math.floor(m / 60);
        var rm = m % 60;
        if (h < 24) return h + 'h ' + rm + 'm';
        var dd = Math.floor(h / 24);
        var rh = h % 24;
        if (dd < 7) return dd + 'd ' + rh + 'h';
        if (dd < 30) return Math.floor(dd / 7) + 'w ' + (dd % 7) + 'd';
        var mo = Math.floor(dd / 30);
        if (mo < 12) return mo + 'mo ' + (dd % 30) + 'd';
        return Math.floor(dd / 365) + 'y ' + Math.floor((dd % 365) / 30) + 'mo';
    }

    function stepDuration(i, events) {
        if (i + 1 >= events.length) return '';
        var a = Number(events[i] && events[i].t);
        var b = Number(events[i + 1] && events[i + 1].t);
        if (!isFinite(a) || !isFinite(b) || !a || !b) return '';
        var d = b - a;
        if (!isFinite(d) || d < 0) return '';
        return formatRel(d);
    }

    // Cuttle agent-ops CLIs (`python -m api.<module> …`). Native means the tool
    // *ran* one of these — not that a search pattern or echo merely mentions one.
    var NATIVE_API_MODS = 'subagents|chat_cli|panes_cli|widgets_cli|discord_cli|device_workers|dashboards|jev|cuttle_brain';
    var NATIVE_ACTION_RE = /^(discord\.post|flask\.restart|git\.push|cuttle_action_form|cuttle_confirm)\b/i;
    // Shell heads, path-aware: `bash …`, `/bin/bash -lc "…"`, `cmd …`.
    var SHELL_HEAD_RE = /^\s*(?:\S*\/)?(bash|sh|exec|shell|run|terminal|command|cmd|powershell)\b/i;
    // Read-only tools never execute a CLI, even when their pattern mentions one.
    var READONLY_HEAD_RE = /^\s*(?:\S*\/)?(search|rg|grep|find|sed|tail|head|cat|less|ls|read_file|read_skill|edit_file|edit|write_todos)\b/i;
    var BARE_NATIVE_CMD_RE = new RegExp('^\\s*(?:\\S*/)?python\\d?\\s+-m\\s+api\\.(' + NATIVE_API_MODS + ')\\b', 'i');
    var SHELL_NATIVE_CMD_RE = new RegExp('(^|\\s|/)python\\d?\\s+-m\\s+api\\.(' + NATIVE_API_MODS + ')\\b', 'i');

    function isNativeTool(ev) {
        if (!ev || ev.kind !== 'tool') return false;
        // Structured tool name is authoritative when present.
        if (ev.args && typeof ev.args.name === 'string' &&
            /^(subagents?|api[_-]?subagents|discord[_-]?post|flask[_-]?restart|git[_-]?push|cuttle_action_form|cuttle_confirm)$/i.test(ev.args.name.trim())) {
            return true;
        }
        var s = String(ev.summary || ev.text || '');
        // Native action id invoked as a tool.
        if (NATIVE_ACTION_RE.test(s)) return true;
        // Bare `python -m api.chat_cli …` command as the whole summary.
        if (BARE_NATIVE_CMD_RE.test(s)) return true;
        // Read-only tools (`search api.subagents…`, `rg …`, `sed …`) merely
        // mention module names — they never ran them.
        if (READONLY_HEAD_RE.test(s)) return false;
        // Shell executions of an agent-ops CLI: `bash python -m api.subagents
        // spawn …`, `/bin/bash -lc "PYTHONPATH=src .venv/bin/python -m …"`.
        return SHELL_HEAD_RE.test(s) && SHELL_NATIVE_CMD_RE.test(s);
    }

    function hasFinishEvent(data) {
        var evs = (data && data.events) || [];
        for (var i = 0; i < evs.length; i++) {
            if (evs[i] && evs[i].kind === 'finish') return true;
        }
        return false;
    }

    function isLive(data) {
        return !!(data && data.executing && !hasFinishEvent(data));
    }

    function tzAbbr() {
        try {
            var parts = new Date().toLocaleTimeString('en-US', { timeZoneName: 'short' }).split(' ');
            return parts[parts.length - 1] || '';
        } catch (_) { return ''; }
    }

    function formatAbs(t) {
        var n = Number(t);
        if (!isFinite(n) || !n) return '';
        var d = new Date(n * 1000);
        var y = d.getFullYear();
        var mo = String(d.getMonth() + 1).padStart(2, '0');
        var da = String(d.getDate()).padStart(2, '0');
        var h24 = d.getHours();
        var ap = h24 >= 12 ? 'pm' : 'am';
        var h12 = h24 % 12;
        if (h12 === 0) h12 = 12;
        var mi = String(d.getMinutes()).padStart(2, '0');
        var tz = tzAbbr();
        return y + '-' + mo + '-' + da + ' @ ' + h12 + ':' + mi + ap + (tz ? ' ' + tz : '');
    }

    function eventBodyHtml(ev, durText) {
        var extra = '';
        var abs = formatAbs(ev && ev.t);
        var tsBits = [];
        if (durText) tsBits.push(esc(durText));
        if (abs) tsBits.push(esc(abs));
        if (tsBits.length) extra += '<div class="query-log-ts">' + tsBits.join(' · ') + '</div>';
        if (ev.text) extra += '<pre class="query-log-pre">' + esc(ev.text) + '</pre>';
        if (ev.args) extra += '<pre class="query-log-pre query-log-pre--json"><code class="language-json">' + jsonToHighlightedHtml(ev.args) + '</code></pre>';
        if (ev.layers && ev.layers.length) extra += '<div>' + esc(ev.layers.join(', ')) + '</div>';
        if (ev.chars != null) extra += '<div>' + esc(String(ev.chars)) + ' chars' + (ev.resume ? ' · resume' : '') + '</div>';
        if (ev.preview) extra += '<pre class="query-log-pre">' + esc(ev.preview) + '</pre>';
        if (ev.error) extra += '<pre class="query-log-pre">' + esc(ev.error) + '</pre>';
        if (ev.mode && !ev.summary) extra += '<span class="query-log-pill">' + esc(ev.mode) + '</span>';
        return extra;
    }

    function fillEventBody(detailsEl, ev, durText) {
        var slot = detailsEl && detailsEl.querySelector('.query-log-event-body');
        if (!slot || slot.getAttribute('data-filled') === '1') return;
        slot.setAttribute('data-filled', '1');
        slot.innerHTML = eventBodyHtml(ev, durText);
    }

    function renderTimeline(data) {
        var events = data.events || [];
        if (!events.length) {
            return '<p class="query-log-empty">No harness events yet. Live turns fill this as Brain, thinking, and tools fire.</p>';
        }
        var t0 = null;
        for (var k = 0; k < events.length; k++) {
            var tk = Number(events[k] && events[k].t);
            if (isFinite(tk) && tk) { t0 = tk; break; }
        }
        var html = events.map(function (ev, i) {
            var fail = ev.failed || (ev.kind === 'finish' && ev.success === false);
            var line = oneLiner(ev);
            var head = esc(ev.kind || 'event') + (ev.phase ? ' · ' + esc(ev.phase) : '');
            if (ev.mode && ev.kind === 'brain') head += ' · ' + esc(ev.mode);
            var failCls = fail ? ' is-fail' : '';
            var isNative = isNativeTool(ev);
            var nativeAttr = isNative ? ' data-native="1"' : '';
            var nativePill = isNative ? '<span class="query-log-pill query-log-pill--native">native</span>' : '';
            var step = '<span class="query-log-step">' + (i + 1) + '</span>';
            var rel = formatRel(t0 == null ? null : relSecs(ev, t0));
            var relHtml = rel ? '<span class="query-log-rel" title="Elapsed since first step">@ ' + esc(rel) + '</span>' : '';
            var abs = formatAbs(ev && ev.t);
            var absTitle = abs ? ' title="' + esc(abs) + '"' : '';
            if (!eventHasBody(ev)) {
                return '<article class="query-log-event query-log-event--flat' + failCls + '" data-kind="' + esc(ev.kind) + '"' + nativeAttr + absTitle + '>' +
                    '<div class="query-log-top">' + step +
                    '<div class="query-log-k">' + head + '</div>' + nativePill + relHtml + '</div>' +
                    (line ? '<div class="query-log-line">' + esc(line) + '</div>' : '') +
                    '</article>';
            }
            return '<details class="query-log-event' + failCls + '" data-kind="' + esc(ev.kind) + '" data-i="' + i + '"' + nativeAttr + absTitle + '>' +
                '<summary>' + step + '<span class="query-log-k">' + head + '</span>' + nativePill +
                (line ? '<span class="query-log-line">' + esc(line) + '</span>' : '') + relHtml +
                '</summary>' +
                '<div class="query-log-event-body"></div>' +
                '</details>';
        }).join('');
        if (isLive(data)) {
            html += '<div class="query-log-live" aria-live="polite"><span class="query-log-spinner" aria-hidden="true"></span><span>Live…</span></div>';
        }
        return html;
    }

    function bindTimeline(body, events) {
        body.querySelectorAll('details.query-log-event').forEach(function (d) {
            d.addEventListener('toggle', function () {
                var i = d.getAttribute('data-i');
                var n = Number(i);
                var ev = events[n];
                if (d.open) {
                    openKeys[eventKey(i)] = true;
                    fillEventBody(d, ev, stepDuration(n, events));
                } else {
                    delete openKeys[eventKey(i)];
                }
            });
            var i = d.getAttribute('data-i');
            if (openKeys[eventKey(i)]) {
                d.open = true;
                fillEventBody(d, events[Number(i)], stepDuration(Number(i), events));
            }
        });
    }

    function renderKnown(data) {
        var b = data.brain || {};
        var inv = b.inventory || {};
        var bits = [];
        bits.push('<article class="query-log-event query-log-event--flat" data-kind="brain"><div class="query-log-k">Brain</div>' +
            '<div>mode ' + esc(b.mode || '—') + '</div>' +
            '<div>layers: ' + esc((b.layers || []).join(', ') || '—') + '</div>' +
            '<div>envelope ' + esc(b.envelope_chars || 0) + ' · prompt ' + esc(b.prompt_chars || 0) + ' chars</div>' +
            '<div>rules ' + esc(b.rules_count || 0) + ' (global ' + esc(b.global_rules_count || 0) + ')</div></article>');
        ['commands', 'docs', 'actions', 'rules'].forEach(function (k) {
            var list = inv[k] || [];
            if (!list.length) return;
            bits.push('<details class="query-log-event" data-kind="brain"><summary><span class="query-log-k">' + esc(k) + '</span>' +
                '<span class="query-log-line">' + list.length + '</span></summary>' +
                '<div class="query-log-event-body" data-filled="1"><pre class="query-log-pre">' + esc(list.join('\n')) + '</pre></div></details>');
        });
        var h = data.harness || {};
        bits.push('<details class="query-log-event" data-kind="status"><summary><span class="query-log-k">Harness</span></summary>' +
            '<div class="query-log-event-body" data-filled="1"><pre class="query-log-pre query-log-pre--json"><code class="language-json">' +
            jsonToHighlightedHtml(h) + '</code></pre></div></details>');
        return bits.join('') || '<p class="query-log-empty">No Brain snapshot on this run.</p>';
    }

    function renderSent(data) {
        var s = data.sent || {};
        if (!s.text && !s.chars) {
            return '<p class="query-log-empty">Outbound prompt was not recorded.</p>';
        }
        return '<article class="query-log-event query-log-event--flat" data-kind="sent"><div class="query-log-k">Sent to CLI</div>' +
            '<div>' + esc(s.chars || 0) + ' chars' + (s.resume ? ' · resume turn' : ' · full / first inject') + '</div>' +
            '<pre class="query-log-pre query-log-pre--fill">' + esc(s.text || '') + '</pre></article>';
    }

    function renderJson(data) {
        return '<pre class="query-log-json"><code class="language-json">' + jsonToHighlightedHtml(data) + '</code></pre>';
    }

    function renderBody(data) {
        var body = document.getElementById('queryLogBody');
        if (!body) return;
        body.classList.toggle('query-log-body--json', lastTab === 'json');
        if (lastTab === 'known') body.innerHTML = renderKnown(data);
        else if (lastTab === 'sent') body.innerHTML = renderSent(data);
        else if (lastTab === 'json') {
            body.innerHTML = renderJson(data);
            var code = body.querySelector('code.language-json');
            if (code && !(window.hljs && window.hljs.highlight)) {
                [600, 2200].forEach(function (ms) {
                    setTimeout(function () {
                        if (!code.isConnected || !window.hljs) return;
                        code.innerHTML = jsonToHighlightedHtml(data);
                    }, ms);
                });
            }
        } else {
            var nearBottom = (body.scrollHeight - body.scrollTop - body.clientHeight) < 140;
            body.innerHTML = renderTimeline(data);
            bindTimeline(body, data.events || []);
            if (nearBottom && isLive(data)) {
                var liveEl = body.querySelector('.query-log-live');
                if (liveEl && liveEl.scrollIntoView) liveEl.scrollIntoView({ block: 'nearest' });
                else body.scrollTop = body.scrollHeight;
            }
        }
    }

    function paint(data, force) {
        var overlay = ensureDom();
        overlay._payload = data;
        var stamp = payloadStamp(data);
        var h = data.harness || {};
        var live = data.executing ? 'live' : (data.success === false ? 'failed' : 'done');
        document.getElementById('queryLogTitle').textContent =
            (h.label || h.agent_id || 'Query') + ' · ' + (data.query_id || '');
        document.getElementById('queryLogMeta').textContent =
            live + (h.cwd ? ' · ' + h.cwd : '') +
            (data.total_tokens ? ' · ' + data.total_tokens + ' tok' : '') +
            (data.total_execution_time ? ' · ' + Number(data.total_execution_time).toFixed(1) + 's' : '');
        if (!force && stamp === lastStamp) return;
        lastStamp = stamp;
        renderBody(data);
    }

    function fetchLog(qid) {
        return fetch('/api/query-log/' + encodeURIComponent(qid), { credentials: 'include', cache: 'no-store' })
            .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); });
    }

    function tick() {
        if (!currentId) return;
        var overlay = document.getElementById('queryLogOverlay');
        if ((overlay && overlay.hidden) || lastTab !== 'timeline') return;
        fetchLog(currentId).then(function (res) {
            if (!res.ok) {
                var body = document.getElementById('queryLogBody');
                if (body && !(ensureDom()._payload)) {
                    body.innerHTML = '<p class="query-log-empty">' + esc(res.j && res.j.error || 'Log not found') + '</p>';
                }
                return;
            }
            paint(res.j, false);
            if (!res.j.executing || hasFinishEvent(res.j)) {
                if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
            }
        }).catch(function () {});
    }

    function openInspector(queryId) {
        var qid = String(queryId || '').trim();
        if (!qid) return;
        currentId = qid;
        lastStamp = '';
        var overlay = ensureDom();
        overlay.hidden = false;
        var body = document.getElementById('queryLogBody');
        if (body) body.innerHTML = '<p class="query-log-empty">Loading…</p>';
        if (pollTimer) clearInterval(pollTimer);
        tick();
        pollTimer = setInterval(tick, 1000);
    }

    function closeInspector() {
        if (document.body.classList.contains('query-log-standalone')) return;
        var overlay = document.getElementById('queryLogOverlay');
        if (overlay) overlay.hidden = true;
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    /**
     * Pure decision for one `.message-query-log-link` click.
     * attrs: { href, queryId, pending } → { action, queryId, href } where
     * action is 'inspect' (open the overlay), 'pending' (stay put, inert), or
     * 'new-tab' (index link, never navigate this panel in place).
     *
     * An id-less button must never navigate in place: typing indicators paint
     * one before the harness mints a query id, and following its bare
     * `/query_log.html` href swapped the whole transcript for the standalone
     * log page (full-bleed purple body).
     */
    function resolveQueryLogClick(attrs) {
        var a = attrs || {};
        var href = a.href || '';
        var qid = String(a.queryId || '').trim();
        if (!qid) {
            var m = /[?&]id=([A-Za-z0-9_-]+)/.exec(href)
                || /query_report_([A-Za-z0-9_-]+)\.html/.exec(href);
            qid = m ? m[1] : '';
        }
        if (qid) return { action: 'inspect', queryId: qid, href: href };
        if (a.pending) return { action: 'pending', queryId: '', href: href };
        return { action: href ? 'new-tab' : 'ignore', queryId: '', href: href };
    }

    window.openQueryLogInspector = openInspector;
    window.closeQueryLogInspector = closeInspector;
    // Pure helpers for the node characterization suite (no DOM needed).
    window.CuttleQueryLogFuncs = {
        formatRel: formatRel,
        formatAbs: formatAbs,
        relSecs: relSecs,
        stepDuration: stepDuration,
        isNativeTool: isNativeTool,
        hasFinishEvent: hasFinishEvent,
        isLive: isLive,
        renderTimeline: renderTimeline,
        resolveQueryLogClick: resolveQueryLogClick,
    };

    document.addEventListener('click', function (e) {
        var a = e.target.closest && e.target.closest('.message-query-log-link');
        if (!a) return;
        var decision = resolveQueryLogClick({
            href: a.getAttribute('href') || '',
            queryId: a.getAttribute('data-query-id') || '',
            pending: !!a.getAttribute('data-query-pending'),
        });
        if (decision.action === 'inspect') {
            e.preventDefault();
            openInspector(decision.queryId);
            return;
        }
        e.preventDefault();
        if (decision.action === 'new-tab' && typeof window.open === 'function') {
            window.open(decision.href, '_blank', 'noopener');
        }
    }, true);
})();
