"""Query-log inspector frontend domain.

Behavioral characterization of src/web/js/queries/query_log_inspector.js under node.
Covers the pure timeline helpers: native-API tool classification (a tool *ran*
a Cuttle agent-ops CLI — a search pattern or echo that merely mentions one
stays yellow), relative-time formatting up to years, per-step durations, finish
detection, and the live predicate, plus lazy-load chunk merging, the scroll
predicate, and the sentinel (no page buttons). DOM paint/poll machinery stays
on the page.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "queries/query_log_inspector.js"
MOD_CSS = REPO_ROOT / "src" / "web" / "css" / "query_log_inspector.css"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = r"""
const fs = require('fs');
global.window = {};
global.document = {
  addEventListener: () => {},
  querySelector: () => null,
  getElementById: () => null,
};
eval(fs.readFileSync(process.env.MOD_JS, 'utf8'));
const F = global.window.CuttleQueryLogFuncs;
const out = {};
const T = (kind, summary, extra) => Object.assign({ kind, summary }, extra);
// Native classification: search patterns that mention a module stay yellow.
out.searchPattern = F.isNativeTool(T('tool', 'search tool calls subagent'));
out.searchSpawn = F.isNativeTool(T('tool', 'search api.subagents|subagents spawn'));
out.searchBare = F.isNativeTool(T('tool', 'search subagents'));
out.grepMention = F.isNativeTool(T('tool', 'bash grep -n "subagents" src/api/service.py'));
out.readMention = F.isNativeTool(T('tool', 'read_file src/api/subagents/service.py'));
// The tool actually ran an agent-ops CLI -> purple.
out.bashSubagents = F.isNativeTool(T('tool', 'bash python -m api.subagents spawn --parent CH-1 --wait --json'));
out.bashChatCli = F.isNativeTool(T('tool', 'bash .venv/bin/python -m api.chat_cli get CH-1 --json'));
out.codexSubagents = F.isNativeTool(T('tool', '/bin/bash -lc "PYTHONPATH=src .venv/bin/python -m api.subagents message --session CH-1 --wait"'));
out.codexRg = F.isNativeTool(T('tool', "/bin/bash -lc 'rg --files src/api/agent_router/providers'"));
out.codexSed = F.isNativeTool(T('tool', "/bin/bash -lc \"sed -n '1,235p' src/tests/test_a.py\""));
out.readonlyRg = F.isNativeTool(T('tool', 'rg api.subagents --type py'));
out.bareCmd = F.isNativeTool(T('tool', 'python -m api.gizmos tasks list'));
out.pytest = F.isNativeTool(T('tool', 'bash .venv/bin/python -m pytest src/tests/ -q'));
out.actionId = F.isNativeTool(T('tool', 'discord.post confirm=yes'));
out.structured = F.isNativeTool(T('tool', 'child turn', { args: { name: 'subagents', args: {} } }));
out.nonTool = F.isNativeTool(T('thinking', 'python -m api.subagents spawn'));
out.noSummary = F.isNativeTool({ kind: 'tool' });
// Relative formatting.
out.rel = [0, 2, 59, 60, 154, 2206, 3600, 9000, 90000, 900000, 4000000, 40000000].map(F.formatRel);
out.relNull = F.formatRel(null);
// Step durations (time until the next step; last step unknown).
const evs = [{ t: 100 }, { t: 112 }, { t: 200 }];
out.dur = [F.stepDuration(0, evs), F.stepDuration(1, evs), F.stepDuration(2, evs)];
// Finish / live.
out.fin = F.hasFinishEvent({ events: [{ kind: 'tool' }, { kind: 'finish' }] });
out.noFin = F.hasFinishEvent({ events: [{ kind: 'tool' }] });
out.live = F.isLive({ executing: true, events: [{ kind: 'tool' }] });
out.liveFin = F.isLive({ executing: true, events: [{ kind: 'finish' }] });
out.liveDone = F.isLive({ executing: false, events: [{ kind: 'tool' }] });
// Rendered rows: native tool rows carry the marker + pill, thinking never does.
const feed = {
  executing: false,
  events: [
    { kind: 'thinking', t: 100, text: 'pondering subagents' },
    { kind: 'tool', t: 110, summary: 'search subagents' },
    { kind: 'tool', t: 120, summary: 'bash python -m api.subagents spawn --wait' },
  ],
};
out.rows = F.renderTimeline(feed);
// Live feed appends the spinner; finished feed does not.
out.liveRows = F.renderTimeline({ executing: true, events: [{ kind: 'status', t: 1, text: 'hi' }] });
out.doneRows = F.renderTimeline({ executing: false, events: [{ kind: 'status', t: 1, text: 'hi' }] });
// Absolute timestamp shape (timezone-dependent values asserted in Python).
out.abs = F.formatAbs(1785716220);
out.absBad = F.formatAbs(null);
// Click routing: a running turn's button paints before a query id exists, and
// following its bare href replaced the whole chat panel with the standalone
// log page (full-bleed purple body). It must stay inert instead.
out.clickLivePending = F.resolveQueryLogClick({ href: '/query_log.html', pending: true });
out.clickIndex = F.resolveQueryLogClick({ href: '/query_log.html', pending: false });
out.clickHrefId = F.resolveQueryLogClick({ href: '/query_log.html?id=abc12345' });
out.clickDataId = F.resolveQueryLogClick({
  href: '/query_log.html', queryId: 'deadbeef', pending: false,
});
out.clickLegacyReport = F.resolveQueryLogClick({ href: '/query_report_98765432.html' });
out.clickNoHref = F.resolveQueryLogClick({});
// Lazy-load merge: known ids replace in place (revision refresh), new ids
// append in order, earlier rows never shift.
const first = [{ id: 11, detail_id: 11, seq: 1, rev: 1 }, { id: 12, detail_id: 12, seq: 2, rev: 1 }];
const chunk = [{ id: 12, detail_id: 12, seq: 2, rev: 2 }, { id: 13, detail_id: 13, seq: 3, rev: 1 }];
out.merged = F.mergeLogRows(first, chunk);
out.mergedEmpty = F.mergeLogRows(null, null);
// Scroll predicate: near-bottom + more + store + idle loads; anything else holds.
const nearBottom = { scrollTop: 900, scrollHeight: 1000, clientHeight: 300 };
const top = { scrollTop: 0, scrollHeight: 1000, clientHeight: 300 };
out.lazyGo = F.shouldLazyLoadMore(nearBottom, { eventStore: true, hasMore: true, loading: false });
out.lazyTop = F.shouldLazyLoadMore(top, { eventStore: true, hasMore: true, loading: false });
out.lazyBusy = F.shouldLazyLoadMore(nearBottom, { eventStore: true, hasMore: true, loading: true });
out.lazyDone = F.shouldLazyLoadMore(nearBottom, { eventStore: true, hasMore: false, loading: false });
out.lazyLegacy = F.shouldLazyLoadMore(nearBottom, { eventStore: false, hasMore: true, loading: false });
// Sentinel: paged store payloads render a lazy-load marker, never page buttons.
const paged = { executing: false, event_store: true, has_more: true, events: [{ kind: 'status', t: 1, text: 'hi' }] };
out.pagedRows = F.renderTimeline(paged);
out.pagedLoading = F.renderTimeline(Object.assign({}, paged, { loading_more: true }));
const lastPage = { executing: false, event_store: true, has_more: false, events: [{ kind: 'status', t: 1, text: 'hi' }] };
out.lastRows = F.renderTimeline(lastPage);
process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_native_tool_classification():
    res = _run()
    assert res["searchPattern"] is False
    assert res["searchSpawn"] is False
    assert res["searchBare"] is False
    assert res["grepMention"] is False
    assert res["readMention"] is False
    assert res["bashSubagents"] is True
    assert res["bashChatCli"] is True
    assert res["codexSubagents"] is True
    assert res["codexRg"] is False
    assert res["codexSed"] is False
    assert res["readonlyRg"] is False
    assert res["bareCmd"] is True
    assert res["pytest"] is False
    assert res["actionId"] is True
    assert res["structured"] is True
    assert res["nonTool"] is False
    assert res["noSummary"] is False


@node_only
def test_relative_format_and_durations():
    res = _run()
    assert res["rel"] == [
        "0s", "2s", "59s", "1m 0s", "2m 34s", "36m 46s",
        "1h 0m", "2h 30m", "1d 1h", "1w 3d", "1mo 16d", "1y 3mo",
    ]
    assert res["relNull"] == ""
    assert res["dur"] == ["12s", "1m 28s", ""]


def test_timeline_accents_stay_distinct():
    css = MOD_CSS.read_text(encoding="utf-8")
    colors = {}
    for m in re.finditer(
        r"\.query-log-event(?:--flat)?(?:\[data-kind=\"(?P<kind>[^\"]+)\"\]|\[data-native=\"1\"\]|(?P<fail>\.is-fail))\s*\{\s*border-left-color:\s*(?P<color>[^;}]+)",
        css,
    ):
        key = "fail" if m.group("fail") else (m.group("kind") or "native")
        colors.setdefault(key, m.group("color").strip())
    for key in ("tool", "thinking", "native", "finish", "fail"):
        assert key in colors, f"missing accent for {key}"
    distinct = {colors[k] for k in ("tool", "thinking", "native", "finish", "fail")}
    assert len(distinct) == 5, f"timeline accents collide: {colors}"


@node_only
def test_rendered_native_marker_and_pill():
    res = _run()
    rows = res["rows"]
    assert rows.count('data-native="1"') == 1
    assert rows.count("query-log-pill--native") == 1
    think = rows.split('data-kind="thinking"')[1].split("</details>")[0]
    assert "data-native" not in think
    assert "query-log-pill--native" not in think
    assert "@ 0s" in rows and "@ 10s" in rows and "@ 20s" in rows
    assert "query-log-live" in res["liveRows"]
    assert "query-log-live" not in res["doneRows"]


@node_only
def test_finish_live_and_absolute_shape():
    res = _run()
    assert res["fin"] is True
    assert res["noFin"] is False
    assert res["live"] is True
    assert res["liveFin"] is False
    assert res["liveDone"] is False
    assert re.match(r"^\d{4}-\d{2}-\d{2} @ \d{1,2}:\d{2}(am|pm)( \S+)?$", res["abs"])
    assert res["absBad"] == ""


@node_only
def test_query_log_click_never_navigates_the_chat_panel():
    """A running turn has no query id yet — the button must not navigate away."""
    res = _run()
    # Live/pending button: inert, no navigation, no overlay.
    assert res["clickLivePending"]["action"] == "pending"
    assert res["clickLivePending"]["queryId"] == ""
    # A bare index link opens a new tab instead of replacing the transcript.
    assert res["clickIndex"]["action"] == "new-tab"
    assert res["clickIndex"]["href"] == "/query_log.html"
    # Any real id — from data-query-id or the href — opens the overlay.
    assert res["clickHrefId"] == {
        "action": "inspect",
        "queryId": "abc12345",
        "href": "/query_log.html?id=abc12345",
    }
    assert res["clickDataId"]["action"] == "inspect"
    assert res["clickDataId"]["queryId"] == "deadbeef"
    assert res["clickLegacyReport"]["queryId"] == "98765432"
    assert res["clickNoHref"]["action"] == "ignore"


@node_only
def test_lazy_load_merge_appends_new_and_refreshes_revised():
    res = _run()
    merged = res["merged"]
    assert [r["detail_id"] for r in merged] == [11, 12, 13]
    assert merged[0]["rev"] == 1
    assert merged[1]["rev"] == 2
    assert res["mergedEmpty"] == []


@node_only
def test_lazy_load_scroll_predicate():
    res = _run()
    assert res["lazyGo"] is True
    assert res["lazyTop"] is False
    assert res["lazyBusy"] is False
    assert res["lazyDone"] is False
    assert res["lazyLegacy"] is False


@node_only
def test_timeline_renders_lazy_sentinel_not_page_buttons():
    res = _run()
    assert "data-log-page" not in res["pagedRows"]
    assert "data-log-more" in res["pagedRows"]
    assert "Load more steps (1 shown)" in res["pagedRows"]
    assert "Loading more steps" in res["pagedLoading"]
    assert "data-log-more" not in res["lastRows"]
    assert "data-log-more" not in res["rows"]


@node_only
def test_running_turn_button_ships_pending_not_a_navigable_index():
    """chat_page.js typing indicators must paint a pending, non-navigable button."""
    page = (REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js").read_text(
        encoding="utf-8"
    )
    assert "getLiveQueryLogFooterHtml()" in page
    # The old footers hardcoded the bare index page as a live link.
    assert "getAssistantMessageFooterHtml('/query_log.html', false)" not in page
