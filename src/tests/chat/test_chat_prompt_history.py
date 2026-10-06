"""Behavioral coverage for src/web/js/chat/chat_prompt_history.js under node.

Executes the real module over scripted record/browse/migrate/derive/
anchor vectors. The pre-move differential (/tmp/diff_prompt.js, scratch)
showed identical decisions vs the page spans these were extracted from
(except one documented hardening: the module skips null transcript
entries where the page try/catch dropped the whole derivation).
Glue-level integration (storage IO, textarea apply, slash draft) stays
in the page; composer duplicate-send tests cover the send paths.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MOD_PH = REPO_ROOT / "src" / "web" / "js" / "chat/chat_prompt_history.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const H = require(process.env.MOD_PH);
const out = {};
// record incl dup/empty/pad/whitespace-only + cap overflow
const st = H.createState();
const inputs = ['hi', 'hi', '', '   ', '  padded  ',
  ...Array.from({ length: 105 }, (_, i) => 'm' + i)];
out.record = inputs.map((t) => [H.appendRecord(st, t), st.list.length, st.index, st.draft]);
out.recordFinal = [st.list.slice(0, 2), st.list.slice(-1), st.list.length];
const addedEmpty = H.appendRecord(H.createState(), '');
// browse cycle on a fixed list with an explicit draft
const nav = (list, draft, steps) => {
  const s = H.createState(); s.list = list.slice();
  const ta = { value: draft };
  return steps.map((d) => {
    if (d < 0 && s.list.length && s.index === -1) H.captureDraft(s, ta.value);
    const r = H.step(s, d);
    if (r.status === 'text' || r.status === 'draft') ta.value = r.text;
    return [r.status, ta.value, s.index, s.draft];
  });
};
const cycle = [-1, -1, -1, -1, 1, 1, 1, 1, -1, 1];
out.navCycle = nav(['a', 'b', 'c'], 'D', cycle);
out.navSingle = nav(['only'], 'D', [-1, -1, 1, 1]);
out.navEmpty = nav([], 'D', [-1, 1]);
out.navDownFresh = nav(['a'], 'D', [1]);
// captureDraft only sticks on a fresh browse
const sd = H.createState(); sd.list = ['a'];
H.captureDraft(sd, 'first');
H.step(sd, -1);
H.captureDraft(sd, 'second');
out.draftOnce = [sd.draft, sd.index];
// migrate merge choices
out.migrate = [
  H.mergeSessionHistories(['a', 'b'], ['x'], ['live']),
  H.mergeSessionHistories(['x'], ['a', 'b', 'c'], []),
  H.mergeSessionHistories([], [], ['live1', 'live2']),
  H.mergeSessionHistories([], [], []),
  H.mergeSessionHistories(Array.from({ length: 120 }, (_, i) => 'o' + i), ['n'], []).length,
  H.mergeSessionHistories(null, ['n1'], ['l1']),
];
// derivation incl null hardening
const msgs = [{ role: 'user', content: '  hello  ' }, { role: 'assistant', content: 'hi' },
  { role: 'user', content: '' }, { role: 'user' }, null];
out.derive = H.deriveFromUserMessages(msgs);
out.deriveEmpty = [H.deriveFromUserMessages(null), H.deriveFromUserMessages([])];
out.deriveCap = H.deriveFromUserMessages(
  Array.from({ length: 150 }, (_, i) => ({ role: 'user', content: 'u' + i }))).length;
// anchors
out.anchors = [
  [H.atStartAnchor(0, 0), H.atEndAnchor(0, 0, 5)],
  [H.atStartAnchor(5, 5), H.atEndAnchor(5, 5, 5)],
  [H.atStartAnchor(2, 2), H.atEndAnchor(2, 2, 5)],
  [H.atStartAnchor(2, 5), H.atEndAnchor(2, 5, 5)],
  [H.atStartAnchor(0, 0), H.atEndAnchor(0, 0, 0)],
];
out.addedEmpty = addedEmpty;
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_PH": str(MOD_PH)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_record_trims_dedupes_and_caps():
    res = _run()
    rec = res["record"]
    # duplicate of newest and empty/whitespace-only inputs change nothing
    assert rec[1][0] is False and rec[2][0] is False and rec[3][0] is False
    assert rec[4][0] is True
    # every record resets the browse
    assert all(r[2] == -1 and r[3] == "" for r in rec)
    assert res["addedEmpty"] is False
    final = res["recordFinal"]
    assert final[2] == H_CAP
    assert final[0] == ["m5", "m6"]
    assert final[1] == ["m104"]


@node_only
def test_browse_cycle_preserves_draft_and_bounds():
    res = _run()
    cycle = res["navCycle"]
    applied = [c[1] for c in cycle]
    assert applied[:4] == ["c", "b", "a", "a"]
    assert [c[0] for c in cycle[:4]] == ["text", "text", "text", "oldest"]
    assert applied[4:7] == ["b", "c", "D"]
    assert [c[0] for c in cycle[4:7]] == ["text", "text", "draft"]
    assert cycle[7][0] == "none" and cycle[7][1] == "D"
    assert cycle[8][1] == "c" and cycle[9][1] == "D"
    single = res["navSingle"]
    assert [c[1] for c in single] == ["only", "only", "D", "D"]
    assert [c[0] for c in single] == ["text", "oldest", "draft", "none"]
    assert res["navEmpty"] == [["none", "D", -1, ""], ["none", "D", -1, ""]]
    assert res["navDownFresh"] == [["none", "D", -1, ""]]
    assert res["draftOnce"] == ["first", 0]


@node_only
def test_migrate_merge_choices():
    mig = _run()["migrate"]
    assert mig[0] == ["a", "b"]
    assert mig[1] == ["a", "b", "c"]
    assert mig[2] == ["live1", "live2"]
    assert mig[3] == []
    assert mig[4] == H_CAP
    assert mig[5] == ["n1"]


@node_only
def test_derive_skips_non_user_and_null_entries():
    res = _run()
    assert res["derive"] == ["hello"]
    assert res["deriveEmpty"] == [[], []]
    assert res["deriveCap"] == H_CAP


@node_only
def test_caret_anchor_predicates():
    assert _run()["anchors"] == [
        [True, False], [False, True], [False, False],
        [False, False], [True, True],
    ]


@node_only
def test_chat_prompt_history_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_PH)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


H_CAP = 100


def test_chat_prompt_history_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    tag = '<script src="/js/chat/chat_prompt_history.js?v='
    assert tag in html, "chat_prompt_history.js must load via versioned script tag"
    assert html.index("chat_markdown.js") < html.index("chat_prompt_history.js") < html.index(
        "chat_page.js?v="
    ), "load order: owned modules before the page orchestrator"
