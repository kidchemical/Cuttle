"""Sub-agent fleet cards: server outcome mapping, hydration, and card markup."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api.subagents import fleet, service, store
from api.subagents.types import ORPHAN_ERROR, ChildRecord
from tests.test_subagents import _runner_factory, _seed

FLEET_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_subagent_fleet.js"


def _child(**kw) -> ChildRecord:
    return ChildRecord(id="c1", batch_id="b1", session_id=7, label="Scout", **kw)


def test_outcomes_keep_failure_cancel_and_lost_distinct():
    assert fleet.outcome(_child(status="pending")) == "queued"
    assert fleet.outcome(_child(status="running")) == "running"
    assert fleet.outcome(_child(status="done")) == "done"
    assert fleet.outcome(_child(status="cancelled")) == "cancelled"
    assert fleet.outcome(_child(status="failed", error="boom")) == "failed"
    assert fleet.outcome(_child(status="failed", error=ORPHAN_ERROR)) == "lost"


def test_entry_summarizes_result_as_plain_bounded_text():
    entry = fleet.fleet_entry(_child(
        status="done", agent="codex", model="gpt-5.6", effort="low",
        result="## Verdict\n\n- **Ship it**: tests [pass](x)\n```py\nsecret()\n```\n" + "word " * 200,
        started_at="2026-10-04 10:00:00", finished_at="2026-10-04 10:01:05",
    ))
    assert entry["fleet"] is True and entry["outcome"] == "done"
    assert entry["summary"].startswith("Verdict Ship it: tests pass word")
    assert "secret" not in entry["detail"] and "**" not in entry["summary"]
    assert len(entry["summary"]) == fleet.SUMMARY_CHARS and entry["summary"].endswith("…")
    assert entry["handle"] == "CH-000007" and entry["effort"] == "low"
    running = fleet.fleet_entry(_child(status="running", result="stale partial"))
    assert running["summary"] == "Working…"  # never presents partial output as a result


def test_flag_switches_live_and_saved_payloads(tmp_path: Path, monkeypatch):
    db, _p, _o, parent = _seed(tmp_path)
    service.spawn(parent_session_id=parent, children=[{"title": "A", "message": "a"}],
                  wait=True, timeout=6, runner=_runner_factory({"A": "All good."}), db=db)
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: False)
    assert "fleet" not in service.public_parent_subagents(parent, db=db)[0]
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: True)
    live = service.public_parent_subagents(parent, db=db)[0]
    assert (live["outcome"], live["summary"]) == ("done", "All good.")
    meta = service.attach_batches_to_assistant_meta(parent, {}, db=db)
    assert meta["subagents"][0]["outcome"] == "done"


def test_history_hydration_reflects_current_child_row(tmp_path: Path, monkeypatch):
    db, _p, _o, parent = _seed(tmp_path)
    spawned = service.spawn(parent_session_id=parent,
                            children=[{"title": "A", "message": "a"}, {"title": "B", "message": "b"}],
                            wait=True, timeout=6, runner=_runner_factory(), db=db)
    saved = service.attach_batches_to_assistant_meta(parent, {}, db=db)  # pre-flag snapshot
    a, b = spawned["children"]
    # After the reply was saved: B is re-run and its host dies.
    store.update_child(db, b["id"], status="failed", error=ORPHAN_ERROR, finished=True)
    messages = [{"role": "assistant", "metadata": json.dumps(saved)}, {"role": "user", "metadata": {}}]
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: False)
    service.hydrate_parent_fleet(db, messages)
    assert isinstance(messages[0]["metadata"], str)  # untouched while the flag is off
    monkeypatch.setattr(fleet, "fleet_enabled", lambda: True)
    service.hydrate_parent_fleet(db, messages)
    cards = {c["label"]: c for c in messages[0]["metadata"]["subagents"]}
    assert cards["A"]["outcome"] == "done" and "reply from A" in cards["A"]["summary"]
    assert cards["B"]["outcome"] == "lost" and cards["B"]["finished_at"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_fleet_markup_escapes_and_bounds():
    code = r"""
const F = require(process.env.FLEET_JS);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const card = (n, o) => ({fleet:true, handle:'CH-00000'+(n%10), label:'<b>A'+n+'</b>', agent:'codex', model:'gpt-5.6',
    effort:'low', outcome:o, summary:'<img src=x>', detail:'full', startedAt:'2026-10-04 10:00:00', finishedAt:'2026-10-04 10:01:05'});
const html = F.renderFleetHtml(['done','failed','cancelled','lost','running','queued'].map((o,i)=>card(i,o)), {esc});
const many = F.renderFleetHtml(Array.from({length:30}, (_,i)=>card(i,'done')), {esc});
console.log(JSON.stringify({html, cards:(many.match(/subagent-fleet-card /g)||[]).length, more:many.includes('+6 more'),
    dur:F.formatDuration('2026-10-04 10:00:00','2026-10-04 10:01:05'), has:F.hasFleet([{fleet:true}]), none:F.hasFleet([{}])}));
"""
    out = json.loads(subprocess.run(["node", "-e", code], env={**__import__("os").environ, "FLEET_JS": str(FLEET_JS)},
                                    capture_output=True, text=True, check=True).stdout)
    html = out["html"]
    for state in ("done", "failed", "cancelled", "lost", "running", "queued"):
        assert f"is-{state}" in html
    assert "<img src=x>" not in html and "<b>" not in html and "&lt;img src=x&gt;" in html
    assert "<span>codex</span><span>gpt-5.6</span><span>low</span>" in html
    assert "1m 05s" in html and html.count("1m 05s") == 4  # running/queued show no duration
    assert out["cards"] == 24 and out["more"] and out["dur"] == "1m 05s"
    assert out["has"] is True and out["none"] is False
