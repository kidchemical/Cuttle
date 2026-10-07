"""Archived-section reconciliation in src/web/js/chat/chat_history_archive.js.

The `?archived=only` fetch may hit a backend that predates the archive column
(Flask serves new JS from disk before its restart picks up the new route),
which answers with the FULL session list. Unfiltered, that duplicated every
chat into the Archived section (double fetch + double DOM, seconds on phones)
and marked every session archived. These tests pin the disjointness: archived
rows the main list already shows are dropped, stale marks are pruned, and
malformed rows never render.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_history_archive.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
const ids = (rows) => rows.map((s) => s && s.id);

// old backend: ?archived=only answers with the full list
out.unfiltered = (() => {
    const main = [{ id: 1 }, { id: 2 }, { id: 3 }];
    const active = A.activeSessionIdSet(main, (id) => [`c${id}`, `a${id}`]);
    const kept = A.filterArchivedSessions([{ id: 1 }, { id: 2 }, { id: 3 }], active, (id) => [`c${id}`, `a${id}`]);
    const stale = A.staleArchivedMarks(['1', '2', '3', 'c1', 'a2'], active);
    return { kept: ids(kept), stale: stale.sort() };
})();
// new backend: archived rows are disjoint from the main list
out.disjoint = (() => {
    const active = A.activeSessionIdSet([{ id: 1 }, { id: 2 }], null);
    const kept = A.filterArchivedSessions([{ id: 9 }, { id: 10 }], active, null);
    return { kept: ids(kept), stale: A.staleArchivedMarks(['9'], active) };
})();
// mixed: one archived chat restored to the main list on another device
out.mixed = (() => {
    const active = A.activeSessionIdSet([{ id: 1 }, { id: 9 }], null);
    const kept = A.filterArchivedSessions([{ id: 9 }, { id: 10 }], active, null);
    return { kept: ids(kept), stale: A.staleArchivedMarks(['9', '10'], active) };
})();
// malformed rows never survive; malformed inputs stay total
out.malformed = (() => {
    const active = A.activeSessionIdSet([{ id: 1 }, null, {}, { id: '' }], null);
    const kept = A.filterArchivedSessions([{ id: 1 }, null, {}, { id: '' }, { id: 7 }], active, null);
    return { kept: ids(kept), nonArray: A.filterArchivedSessions(null, active, null),
        emptyActive: ids(A.filterArchivedSessions([{ id: 5 }], new Set(), null)) };
})();
// dual id forms compare equal (raw + canonical + auth-db)
out.forms = (() => {
    const active = A.activeSessionIdSet([{ id: 42 }], (id) => ['canon-42', 4200]);
    return { raw: A.isActiveId(active, 42, null), canon: A.isActiveId(active, 'canon-42', null),
        auth: A.isActiveId(active, 'other', 4200), miss: A.isActiveId(active, 43, 4300),
        keys: A.idKeys(42, ['canon-42', 4200]).sort() };
})();
// owner boundary: no page globals inside the pure module (comments stripped)
out.boundary = (() => {
    const src = [A.activeSessionIdSet, A.filterArchivedSessions,
        A.staleArchivedMarks, A.isActiveId, A.idKeys]
        .map((f) => f.toString()).join('\\n')
        .replace(/\\/\\*[\\s\\S]*?\\*\\//g, '').replace(/\\/\\/.*/g, '');
    const banned = ['document', 'window', 'fetch(', 'localStorage'];
    return { banned: banned.filter((t) => src.includes(t)) };
})();
process.stdout.write(JSON.stringify(out));
"""


def _run_harness():
    env = {"MOD_JS": str(MOD_JS), "PATH": os.environ["PATH"]}
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)


@node_only
def test_history_archive_module_parses():
    proc = subprocess.run(["node", "--check", str(MOD_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


@node_only
def test_unfiltered_archived_response_yields_empty_section():
    out = _run_harness()["unfiltered"]
    assert out["kept"] == []
    # every mark from the unfiltered response is stale, in any id form
    assert out["stale"] == ["1", "2", "3", "a2", "c1"]


@node_only
def test_disjoint_archived_rows_kept():
    out = _run_harness()["disjoint"]
    assert out["kept"] == [9, 10]
    assert out["stale"] == []


@node_only
def test_restored_chat_leaves_archived_section():
    out = _run_harness()["mixed"]
    assert out["kept"] == [10]
    assert out["stale"] == ["9"]


@node_only
def test_malformed_rows_and_inputs():
    out = _run_harness()["malformed"]
    assert out["kept"] == [7]
    assert out["nonArray"] == []
    assert out["emptyActive"] == [5]


@node_only
def test_dual_id_forms_compare_equal():
    out = _run_harness()["forms"]
    assert out["raw"] is True
    assert out["canon"] is True
    assert out["auth"] is True
    assert out["miss"] is False
    assert out["keys"] == ["42", "4200", "canon-42"]


@node_only
def test_archive_module_has_no_page_globals():
    assert _run_harness()["boundary"]["banned"] == []


def test_archive_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    tag = '<script src="/js/chat/chat_history_archive.js?v='
    assert tag in html, "chat_history_archive.js must load via versioned script tag"
    assert html.index("chat_subagent_fleet.js") < html.index(
        "chat_history_archive.js") < html.index("chat_page.js?v="), \
        "load order: pure archive owner before the page"
