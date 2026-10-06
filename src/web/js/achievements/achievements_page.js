/* Trophy-case App presentation. Achievement semantics remain in api.achievements. */
(function () {
    'use strict';
    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
        async function loadAchievementsSummary() {
            const el = document.getElementById('achievementsSummary');
            if (!el) return;
            try {
                const r = await fetch('/api/achievements', { cache: 'no-store' });
                const d = await r.json();
                if (!d.success) {
                    el.textContent = d.disabled ? 'Achievements is disabled. Enable it in Settings → Experimental.' : (d.error || 'Unavailable');
                    document.getElementById('achievementsGrid').replaceChildren();
                    document.getElementById('rescanAchievements').disabled = true;
                    return;
                }
                document.getElementById('rescanAchievements').disabled = false;
                const next = (d.items || [])
                    .filter(i => !i.unlocked && i.percent > 0 && !i.hidden)
                    .sort((a, b) => b.percent - a.percent)[0];
                el.innerHTML = '<strong>' + (d.unlocked || 0) + ' / ' + (d.total || 0) + '</strong> unlocked'
                    + (d.unseen ? ' · ' + d.unseen + ' unseen' : '')
                    + (next ? ' · closest: ' + escapeHtml(next.icon + ' ' + next.title)
                             + ' (' + next.percent.toFixed(1) + '%)' : '');
                renderAchievementsGrid(d);
            } catch (e) {
                el.textContent = 'Could not load: ' + String(e.message || e);
            }
        }

        function renderAchievementsGrid(payload) {
            const grid = document.getElementById('achievementsGrid');
            if (!grid) return;
            const NS = window.CuttleAchievements;
            const groups = NS ? NS.groupByCategory(payload.items || []) : [];
            if (!groups.length) { grid.innerHTML = '<div class="provider-status">No achievements.</div>'; return; }
            const sections = groups.map(function (group) {
                const cards = group.items.map(function (item) {
                    const cls = 'achievement-card'
                        + (item.unlocked ? ' is-unlocked' : '')
                        + ' rarity-' + escapeHtml(item.rarity || 'common');
                    const pct = Math.max(0, Math.min(100, Number(item.percent) || 0));
                    const title = item.hidden && !item.unlocked ? '???' : escapeHtml(item.title);
                    const blurb = item.hidden && !item.unlocked
                        ? escapeHtml(item.hint || 'Hidden achievement')
                        : escapeHtml(item.description || '');
                    return '<div class="' + cls + '" title="' + blurb + '">'
                        + '<div class="achievement-icon">' + (item.unlocked ? escapeHtml(item.icon) : (item.hidden ? '❔' : escapeHtml(item.icon))) + '</div>'
                        + '<div class="achievement-title">' + title + '</div>'
                        + '<div class="achievement-description">' + blurb + '</div>'
                        + '<div class="achievement-rarity">' + escapeHtml(item.rarity_label || '') + '</div>'
                        + (item.unlocked ? '<div class="achievement-progress">Unlocked</div>' : '<div class="achievement-bar"><span style="width:' + pct.toFixed(1) + '%"></span></div><div class="achievement-progress">' + pct.toFixed(1) + '%</div>')
                        + '</div>';
                }).join('');
                return '<div class="achievements-group">'
                    + '<h4 class="achievements-group-title">' + escapeHtml(group.label) + '</h4>'
                    + '<div class="achievements-group-grid">' + cards + '</div></div>';
            }).join('');
            grid.innerHTML = sections;
        }

        async function rescanAchievements() {
            (window.showToast || alert)('Scanning…', 'info');
            try {
                const r = await fetch('/api/achievements/scan', { method: 'POST' });
                const d = await r.json();
                if (!d.success) throw new Error(d.error || 'Scan failed');
                await loadAchievementsSummary();
                (window.showToast || alert)(
                    d.unlocked && d.unlocked.length
                        ? 'Unlocked ' + d.unlocked.length + ' achievement(s)!' : 'Scan complete — nothing new.',
                    'success');
            } catch (e) {
                (window.showToast || alert)('Scan failed: ' + (e.message || e), 'error');
            }
        }

    document.getElementById('rescanAchievements').addEventListener('click', rescanAchievements);
    document.getElementById('refreshAchievements').addEventListener('click', loadAchievementsSummary);
    loadAchievementsSummary();
})();
