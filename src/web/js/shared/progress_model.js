/* Cuttle progress bars: determinate vs indeterminate, decided once (shared).

   Every progress bar in the web UI (watch cards and their worker bars, the
   chat's pinned background card, unit-watch rows, Agent Feed work lines, and
   agent-written <progress> blocks) asks `measure()` how to show a value:

   - real progress data (a percent, or done/total) → a determinate fill and a
     "42%" label;
   - nothing measured (no percent, or the source only knows "running") → an
     INDETERMINATE bar: the track carries data-progress="indeterminate" and the
     shared CSS (ui_boot.css) animates a sliding stripe, or shows a static
     striped bar under prefers-reduced-motion. No "0%" label: the elapsed time
     shows instead when it is known.

   Unknown is null/undefined, never 0: servers send `percent: null` when they
   have not measured anything (api.job_watch). A measured 0 ("0 of 10") stays
   determinate. `measured: false` marks a source that never measures (a unit
   watch, a background wait) so a legacy 0 from it still reads as unknown.

   Pure; also loads under Node for model tests. */
(function (root) {
    'use strict';

    const TERMINAL = ['done', 'failed', 'cancelled', 'canceled', 'stopped', 'error', 'timed_out'];

    function num(v) {
        if (v == null || v === '' || typeof v === 'boolean') return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
    }

    function epoch(v) {
        if (v == null || v === '') return null;
        if (typeof v === 'number' && Number.isFinite(v)) return v > 1e12 ? v / 1000 : v;
        const t = Date.parse(String(v));
        return Number.isFinite(t) ? t / 1000 : null;
    }

    function elapsedText(sec) {
        if (!(sec >= 0)) return '';
        const s = Math.floor(sec);
        if (s < 60) return s + 's';
        const m = Math.floor(s / 60);
        if (m < 60) return m + 'm';
        return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm';
    }

    /**
     * How one bar shows its source.
     * src: { percent?, done?, total?, state?, measured?, started_at?|started?|started_ts?, elapsed_sec? }
     * → { determinate, percent (0-100 | null), label ('42%' | elapsed | ''), animate, elapsedSec }
     */
    function measure(src, nowSec) {
        const s = src && typeof src === 'object' ? src : { percent: src };
        const state = String(s.state || '').toLowerCase();
        let pct = num(s.percent);
        const total = num(s.total);
        const done = num(s.done);
        if (pct == null && total != null && total > 0 && done != null) pct = (done / total) * 100;
        if (pct === 0 && s.measured === false) pct = null; // a source that never measures
        if (pct == null && state === 'done') pct = 100;
        const determinate = pct != null;
        const percent = determinate ? Math.max(0, Math.min(100, Math.round(pct))) : null;
        const now = nowSec != null ? nowSec : Date.now() / 1000;
        const started = epoch(s.started_ts != null ? s.started_ts : (s.started_at != null ? s.started_at : s.started));
        let elapsedSec = num(s.elapsed_sec);
        if (elapsedSec == null && started != null) elapsedSec = Math.max(0, now - started);
        const terminal = TERMINAL.indexOf(state) >= 0;
        return {
            determinate,
            percent,
            label: determinate ? percent + '%' : elapsedText(elapsedSec),
            animate: !determinate && !terminal,
            elapsedSec,
        };
    }

    /** Attribute string for a bar track in HTML builders ('' when determinate). */
    function trackAttr(m) {
        if (!m || m.determinate) return '';
        return m.animate ? ' data-progress="indeterminate"' : ' data-progress="unknown"';
    }

    /** Fill width for HTML builders: the measured percent, else 0 (CSS owns the stripe). */
    function fillWidth(m) {
        return m && m.determinate ? m.percent : 0;
    }

    /** Live DOM update of an existing bar (track, its fill, optional value label). */
    function apply(track, fill, valueEl, m) {
        if (track) {
            if (m.determinate) track.removeAttribute('data-progress');
            else track.setAttribute('data-progress', m.animate ? 'indeterminate' : 'unknown');
        }
        if (fill) fill.style.width = fillWidth(m) + '%';
        if (valueEl) {
            valueEl.textContent = m.label;
            valueEl.hidden = !m.label;
        }
    }

    const api = { measure, trackAttr, fillWidth, apply, elapsedText, num };
    root.CuttleProgress = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
