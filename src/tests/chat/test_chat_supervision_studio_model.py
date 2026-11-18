"""Studio supervision panel: pure model (node) in ``chat_supervision_model.js``.

Timeline rows (kind → icon/tone, local clock, day only when not today), the
header from the schedules a panel covers, paging helpers and the body copy for
loading / empty / error. Escaping is checked on the server text the panel shows.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MODEL = Path(__file__).resolve().parents[2] / "web" / "js" / "chat" / "chat_supervision_model.js"

HARNESS = r"""
const M = require(process.env.MODEL);
// Local noon today, so "today" rows carry no day label on any machine.
const today = new Date(2026, 10, 18, 12, 0, 0);
const now = today.getTime() / 1000;
const items = [
  {id: 'z1', at: now - 60, kind: 'stalled', chat: 'CH-000002', headline: 'Chat stalled', detail: 'no progress for 45 min'},
  {id: 'z2', at: now - 120, kind: 'nudged', chat: 'CH-000002', headline: 'Nudge delivered',
    detail: 'Quarantine "x" & <b>go</b>'},
  {id: 'z3', at: now - 86400 * 15, kind: 'guard', chat: 'CH-000003', headline: 'Guard blocked a commit to main', detail: ''},
  {id: 'z4', at: now - 200, kind: 'milestone', chat: 'CH-000003', headline: 'Task milestone reached', detail: ''},
  {id: 'z5', at: now - 300, kind: 'care', chat: 'CH-000002', headline: 'Leftover process ended', detail: ''},
  {id: 'z6', at: now - 400, kind: 'paused', chat: 'CH-000002', headline: 'Supervision paused', detail: ''},
  {id: 'z7', at: now - 500, kind: 'resumed', chat: 'CH-000002', headline: 'Supervision resumed', detail: ''},
  {id: 'z8', at: now - 600, kind: 'error', chat: 'CH-000002', headline: 'Supervision check failed', detail: ''},
  {id: 'z9', at: now - 700, kind: 'skipped', chat: 'CH-000002', headline: 'Supervision activity', detail: ''},
  {id: 'z10', at: now - 800, kind: 'unchanged', chat: 'CH-000002', headline: 'Supervision activity', detail: ''},
  {id: 'z11', at: now - 900, kind: 'from_the_future', chat: 'CH-000002', headline: 'Something new', detail: ''},
  {id: 'z12', at: now - 950, kind: 'constructor', chat: 'CH-000002', headline: 'Prototype key', detail: ''},
  {id: 'z13', at: now - 960, kind: '__proto__', chat: 'CH-000002', headline: '', detail: ''},
  {id: 'z14', at: now - 970, kind: 'nudged', chat: 'CH-000002', headline: '<img src=x onerror=alert(1)>', detail: ''},
];
const rows = M.timelineRows(items, now, {locale: 'en-US'});
const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
const sched = (o) => Object.assign({target: 'CH-000002', every: 900, state: 'pending'}, o);
console.log(JSON.stringify({
  icons: Object.fromEntries(['stalled', 'nudged', 'guard', 'care', 'milestone', 'paused', 'resumed', 'error',
    'skipped', 'unchanged'].map((k) => [k, [byId[{stalled: 'z1', nudged: 'z2', guard: 'z3', care: 'z5', milestone: 'z4',
    paused: 'z6', resumed: 'z7', error: 'z8', skipped: 'z9', unchanged: 'z10'}[k]].icon,
    byId[{stalled: 'z1', nudged: 'z2', guard: 'z3', care: 'z5', milestone: 'z4', paused: 'z6', resumed: 'z7',
      error: 'z8', skipped: 'z9', unchanged: 'z10'}[k]].tone]])),
  unknown: [byId.z11.icon, byId.z11.tone, byId.z12.icon, byId.z13.icon],
  blankHeadline: byId.z13.headline,
  todayDay: byId.z1.day, todayTime: byId.z1.time,
  olderDay: byId.z3.day, olderTime: byId.z3.time,
  rowKeys: Object.keys(rows[0]).sort(),
  html: M.timelineHtml([byId.z2], {showChat: true, more: true}),
  escapedHeadline: M.timelineHtml([byId.z14], {}),
  attrEscape: M.timelineHtml([Object.assign({}, byId.z1, {kind: '"><x>', tone: 'warn'})], {}),
  empty: M.timelineHtml([], {}),
  loading: M.timelineHtml([], {loading: true}),
  error: M.timelineHtml([], {error: '<oops>'}),
  errorWithRows: M.timelineHtml([byId.z1], {error: 'late'}),
  moreIdle: M.timelineHtml([], {more: true}),
  moreBusy: M.timelineHtml([byId.z1], {more: true, loading: true}),
  activeHeader: M.panelHeader([sched({state: 'paused'}), sched({target: 'CH-000009', state: 'active'})]),
  pausedHeader: M.panelHeader([sched({state: 'paused'}), sched({target: 'CH-000009', state: 'paused'})]),
  multi: M.panelHeader([sched({target: 'CH-000002', every: 900}), sched({target: 'CH-000003', every: 900}),
    sched({target: 'CH-000004', every: 900}), sched({target: 'CH-000002', every: 900})]),
  mixedCadence: M.panelHeader([sched({every: 300}), sched({target: 'CH-000009', every: 3600})]),
  badgeTitle: M.panelHeader([sched({})], {title: 'Supervised by CH-000042'}),
  emptyHeader: M.panelHeader([]),
  namedHeader: M.panelHeader([sched({goal: 'Release v0.8'}), sched({target: 'CH-000009', goal: 'Release v0.8'})]),
  headerHtml: M.panelHeaderHtml(M.panelHeader([sched({target: 'CH-<i>'})], {title: 'Supervising <b>x</b>'})),
  merged: M.mergeItems([{id: 'a', at: 5}, {id: 'b', at: 3}], [{id: 'b', at: 3}, {id: 'c', at: 9}, {id: 'd', at: 3}])
    .map((x) => x.id),
  chatOnly: M.filterChat(items, 'CH-000003').map((x) => x.id),
  cadences: [M.cadencePart(45), M.cadencePart(900), M.cadencePart(5400), M.cadencePart(3600)],
}));
"""


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "MODEL": str(MODEL), "TZ": "UTC"})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_kind_maps_to_icon_and_tone_with_neutral_fallback():
    r = _run()
    assert r["icons"] == {
        "stalled": ["stalled", "warn"], "nudged": ["nudged", "accent"], "guard": ["guard", "bad"],
        "care": ["care", "ok"], "milestone": ["milestone", "ok"], "paused": ["paused", "muted"],
        "resumed": ["resumed", "ok"], "error": ["error", "bad"], "skipped": ["quiet", "muted"],
        "unchanged": ["quiet", "muted"],
    }
    # Kinds the projection may add later, and prototype names, read as neutral.
    assert r["unknown"] == ["neutral", "muted", "neutral", "neutral"]
    assert r["blankHeadline"] == "Supervision activity"


def test_rows_carry_local_clock_and_day_only_when_not_today():
    r = _run()
    assert r["todayTime"] == "11:59" and r["todayDay"] == ""  # one minute before local noon
    assert r["olderDay"] == "Nov 3" and r["olderTime"] == "12:00"  # fifteen days back
    assert r["rowKeys"] == ["at", "chat", "day", "detail", "headline", "icon", "id", "kind", "time", "tone"]


def test_server_text_is_escaped_and_rows_render_in_order():
    r = _run()
    assert 'Quarantine &quot;x&quot; &amp; &lt;b&gt;go&lt;/b&gt;' in r["html"]
    assert '<span class="sup-tl-chat">CH-000002</span>' in r["html"]  # showChat on
    assert 'data-sup-more>Show older</button>' in r["html"]
    assert 'data-kind="nudged"' in r["html"] and "tone-accent" in r["html"]
    assert "<img" not in r["escapedHeadline"] and "&lt;img src=x onerror=alert(1)&gt;" in r["escapedHeadline"]
    # A hostile kind string cannot break out of its attribute.
    assert 'data-kind="&quot;&gt;&lt;x&gt;"' in r["attrEscape"]
    assert "<x>" not in r["attrEscape"]


def test_empty_loading_error_and_paging_copy():
    r = _run()
    assert "No supervision events yet." in r["empty"]
    assert "<ol" not in r["empty"] and "sup-tl-more" not in r["empty"]
    assert "Loading timeline…" in r["loading"]
    assert "Couldn’t load the timeline. &lt;oops&gt;" in r["error"]
    assert "<oops>" not in r["error"]
    # An error with rows still shows the rows, and says the newer refresh failed.
    assert "sup-tl-row" in r["errorWithRows"] and "Couldn’t load the timeline. late" in r["errorWithRows"]
    assert 'data-sup-more>Show older</button>' in r["moreIdle"] and "No supervision events yet." in r["moreIdle"]
    assert "data-sup-more disabled>Loading…</button>" in r["moreBusy"]


def test_header_status_is_paused_only_when_every_schedule_is_paused():
    r = _run()
    assert r["activeHeader"]["status"] == "active"
    assert r["pausedHeader"]["status"] == "paused"
    assert r["emptyHeader"] == {"title": "Supervision", "goal": "", "chatCount": 0,
                                "cadenceLabel": "", "subline": "", "status": "active"}


def test_header_aggregates_chats_and_cadence_across_schedules():
    r = _run()
    m = r["multi"]
    # Four schedules, three distinct chats; the same cadence reads once.
    assert m["chatCount"] == 3
    assert m["goal"] == "CH-000002 and 2 more"
    assert m["title"] == "Supervising CH-000002 and 2 more"
    assert m["cadenceLabel"] == "check-in every 15 min"
    assert m["subline"] == "3 chats · check-in every 15 min"
    mixed = r["mixedCadence"]
    assert mixed["cadenceLabel"] == "check-ins every 5 min to 1 h" and mixed["chatCount"] == 2
    named = r["namedHeader"]
    assert named["title"] == "Supervising Release v0.8" and named["goal"] == "Release v0.8"
    assert named["subline"] == "2 chats · check-in every 15 min"
    assert r["badgeTitle"]["title"] == "Supervised by CH-000042"
    assert r["badgeTitle"]["subline"] == "1 chat · check-in every 15 min"


def test_header_html_escapes_title():
    html = _run()["headerHtml"]
    assert "Supervising &lt;b&gt;x&lt;/b&gt;" in html
    assert "<b>" not in html and "sup-studio-eye" in html


def test_merge_and_chat_filter_and_cadence_words():
    r = _run()
    assert r["merged"] == ["c", "a", "d", "b"]  # newest first, ties by larger id, one row per id
    assert r["chatOnly"] == ["z3", "z4"]
    assert r["cadences"] == ["45 s", "15 min", "90 min", "1 h"]
