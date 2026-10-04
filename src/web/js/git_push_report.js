/* Shared redacted push-blocker report for Git UI, chat menu and action cards. */
(function (root) {
    'use strict';
    const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g,
        c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    function render(report) {
        const findings = Array.isArray(report.findings) ? report.findings : [];
        return '<h2 id="push-report-title">Push blocked by pre-push checks</h2>' +
            '<p>' + esc(report.summary || 'The pre-push secret hook blocked this push.') + '</p>' +
            '<p>No changes were sent by this rejected ref update. Flagged values are redacted. ' +
            'Review the original commit locally before deciding how to resolve each finding.</p>' +
            '<p><strong>' + findings.length + ' findings · ' + new Set(findings.map(f => f.file).filter(Boolean)).size + ' files</strong></p>' +
            findings.map((f, index) => '<details open style="border:1px solid #555;border-radius:8px;margin:10px 0;padding:12px">' +
                '<summary><strong>' + esc(f.file || 'Inspection failure') + '</strong>' + (f.line ? ':' + esc(f.line) : '') + ' — ' + esc(f.hook) + '</summary>' +
                '<p>' + esc(f.rule) + '</p><p>Commit: <code style="user-select:all">' + esc(f.commit || 'Unknown') + '</code></p>' +
                (f.table ? '<p>Table: <code>' + esc(f.table) + '</code> · column: <code>' + esc(f.column) + '</code> · row position: ' + esc(f.row) + '</p>' : '') +
                (f.file && f.commit ? '<button type="button" data-push-diff="' + index + '">View flagged commit diff</button>' : '') +
                '<pre style="overflow:auto;background:#242428;padding:12px;border-left:3px solid #d77"><span style="color:#aaa">' + esc(f.line || (f.table ? 'DB' : '—')) + '</span>  ' + esc(f.preview || '[No source value shown]') + '</pre></details>').join('') +
            (report.warnings || []).map(w => '<p>Warning: ' + esc(w) + '</p>').join('');
    }
    function show(report, context) {
        if (!report || !Array.isArray(report.findings)) return false;
        context = context || {};
        const prior = root.document.getElementById('cuttle-push-report');
        if (prior) prior.close();
        const focus = root.document.activeElement;
        const dialog = root.document.createElement('dialog');
        dialog.id = 'cuttle-push-report';
        dialog.setAttribute('aria-labelledby', 'push-report-title');
        dialog.style.cssText = 'width:min(950px,90vw);max-height:85vh;overflow:auto;background:var(--bg-secondary,#202024);color:var(--text-primary,#eee);border:1px solid #666;border-radius:12px;padding:24px;font-family:system-ui,sans-serif;box-shadow:0 12px 60px #0008';
        dialog.innerHTML = '<button type="button" style="float:right">Close</button>' + render(report);
        dialog.querySelectorAll('button').forEach(button => {
            button.style.cssText = 'padding:7px 12px;border:1px solid #777;border-radius:6px;background:#34343b;color:#eee;cursor:pointer';
        });
        dialog.querySelector('button').addEventListener('click', () => dialog.close());
        dialog.querySelectorAll('[data-push-diff]').forEach(button => {
            const f = report.findings[Number(button.dataset.pushDiff)];
            button.disabled = !context.projectPath || !root.CuttleDiffModal;
            button.addEventListener('click', () => root.CuttleDiffModal.open({
                projectPath: context.projectPath, repoRoot: context.repoRoot || context.projectPath,
                file: f.file, files: report.findings.filter(item => item.commit === f.commit).map(item => item.file),
                commitHash: f.commit,
            }));
        });
        dialog.addEventListener('close', () => { dialog.remove(); if (focus && focus.isConnected) focus.focus(); }, {once:true});
        root.document.body.appendChild(dialog);
        dialog.showModal();
        return true;
    }
    function fromAction(data, context) {
        for (const result of (data.results || [])) {
            if (result.action !== 'git.push') continue;
            for (const line of String(result.response || result.error || '').split(/\r?\n/)) {
                if (!line.startsWith('CUTTLE_PUSH_REPORT=')) continue;
                try {
                    const report = JSON.parse(line.slice('CUTTLE_PUSH_REPORT='.length));
                    if (report.version !== 1 || !Array.isArray(report.findings)) continue;
                    const rules = [...new Set(report.findings.map(f => f.rule))];
                    report.summary = 'Pre-push secret hook blocked the push: ' + rules.slice(0, 2).join(', ') +
                        (rules.length > 2 ? ' (and more)' : '') + ' [' + [...new Set(report.findings.map(f => f.hook))].join(', ') + ']';
                    show(report, context);
                    return report.summary;
                } catch (_) {}
            }
        }
        return '';
    }
    const api = {render, show, fromAction};
    root.CuttleGitPushReport = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
