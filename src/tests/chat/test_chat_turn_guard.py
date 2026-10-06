"""Behavioral coverage for src/web/js/chat/chat_turn_guard.js under node.

Executes the real module over the staleness truth table and turn
lifecycle races (capture/bump/paint ordering that the New-Chat-during-
turn zombie bug hinges on). The pre-move differential (/tmp/diff_turn.js,
scratch) showed identical decisions vs the real page closure and check
sites over gens x captured x bound x current plus lifecycle sequences.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

MOD_TG = REPO_ROOT / "src" / "web" / "js" / "chat/chat_turn_guard.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const G = require(process.env.MOD_TG);
const out = {};
const gen = G.createGeneration();
out.source = [gen.gen, G.capture(gen)];
// staleness matrix: null/undefined tokens never stale; mismatch stale
out.stale = {
  nullFresh: G.isStale(null, gen),
  undefFresh: G.isStale(undefined, gen),
  sameFresh: G.isStale(0, gen),
};
G.bump(gen);
out.staleAfterBump = {
  old: G.isStale(0, gen),
  current: G.isStale(1, gen),
  nullStill: G.isStale(null, gen),
  bumpReturns: gen.gen,
};
G.bump(gen);
out.doubleBump = [gen.gen, G.isStale(1, gen), G.capture(gen)];
// paint gate matrix: fresh/stale x bound null/set x current null/match/mismatch
const paint = (cap, g, bound, cur) => G.canPaintHere(cap, bound,
  { gen: g, currentSessionId: cur, isViewing: (id) => cur != null && String(cur) === String(id) });
out.paint = {
  freshBoundMatch: paint(0, 0, 'A', 'A'),
  freshBoundMismatch: paint(0, 0, 'B', 'A'),
  freshBoundNullOpen: paint(0, 0, null, null),
  freshBoundNullChat: paint(0, 0, null, 'A'),
  staleBoundMatch: paint(0, 1, 'A', 'A'),
  staleBoundNull: paint(1, 2, null, null),
  nullTokenFresh: paint(null, 0, 'A', 'A'),
};
// lifecycle: New-Chat-during-turn — capture, bump, zombie blocked,
// waiter exits, new turn on the splash paints once bound
const g2 = G.createGeneration();
let current = 'A';
const viewing = (id) => current != null && String(current) === String(id);
const t1 = G.capture(g2);
const freshPaints = G.canPaintHere(t1, 'A', { gen: g2.gen, currentSessionId: current, isViewing: viewing });
G.bump(g2); current = null;
const zombiePaint = G.canPaintHere(t1, 'A', { gen: g2.gen, currentSessionId: current, isViewing: viewing });
const zombieAdopt = !G.isStale(t1, g2);
const waiterExits = G.isStale(t1, g2);
const t2 = G.capture(g2);
current = 'B';
const newPaints = G.canPaintHere(t2, 'B', { gen: g2.gen, currentSessionId: current, isViewing: viewing });
const oldStillStale = G.isStale(t1, g2);
out.lifecycle = [freshPaints, zombiePaint, zombieAdopt, waiterExits, newPaints, oldStillStale];
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_TG": str(MOD_TG)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_token_source_bump_and_null_safety():
    res = _run()
    assert res["source"] == [0, 0]
    assert res["stale"] == {"nullFresh": False, "undefFresh": False, "sameFresh": False}
    assert res["staleAfterBump"] == {
        "old": True, "current": False, "nullStill": False, "bumpReturns": 1}
    assert res["doubleBump"] == [2, True, 2]


@node_only
def test_paint_gate_truth_table():
    p = _run()["paint"]
    assert p["freshBoundMatch"] is True
    assert p["freshBoundMismatch"] is False
    assert p["freshBoundNullOpen"] is True
    assert p["freshBoundNullChat"] is False
    assert p["staleBoundMatch"] is False
    assert p["staleBoundNull"] is False
    # A null token is generation-fresh but never bound-paintable to a chat.
    assert p["nullTokenFresh"] is False


@node_only
def test_new_chat_during_turn_lifecycle():
    assert _run()["lifecycle"] == [True, False, False, True, True, True]


@node_only
def test_chat_turn_guard_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_TG)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


@node_only
def test_chat_turn_guard_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    tag = '<script src="/js/chat/chat_turn_guard.js?v='
    assert tag in html, "chat_turn_guard.js must load via versioned script tag"
    assert html.index("chat_prompt_history.js") < html.index("chat_turn_guard.js") < html.index(
        "chat_page.js?v="
    ), "load order: owned modules before the page orchestrator"
