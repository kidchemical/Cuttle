"""Sub-agent fleet cards: server outcome mapping, hydration, and card markup."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
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


def _running_child(tmp_path):
    import os
    from api.subagents.types import ChildSpec
    db, path, owner, parent = _seed(tmp_path)
    batch = store.insert_batch(db, parent_session_id=parent, user_id=owner, collect='all', lifetime='conversational')
    child = store.insert_child(db, batch_id=batch.id, session_id=parent, sort_index=0,
                               spec=ChildSpec(title='Scout', message='inspect'), prompt='inspect')
    store.update_child(db, child.id, status='running', started=True, owner_pid=os.getpid())
    return db, path, child


def test_live_status_crosses_processes_and_hydrates_parent_and_child(tmp_path, monkeypatch):
    import os
    from api.subagents.turns import ChildStatusSink
    db, path, child = _running_child(tmp_path)
    sink = ChildStatusSink(db, child.id)
    try:
        sink.put(('status', 'Reading architecture files'))
        monkeypatch.setattr(fleet, 'fleet_enabled', lambda: True)
        messages = [{'role': 'assistant', 'content': 'Review in progress',
                     'metadata': {'subagents': [fleet.fleet_entry(child)]}}]
        service.hydrate_parent_fleet(db, messages)
        card = messages[0]['metadata']['subagents'][0]
        assert card['summary'] == 'Reading architecture files' and card['live_status_at']
        assert service.child_live_status(child.session_id, db=db)['status'] == 'Reading architecture files'
        # A different process reads the durable status, not the sink's memory.
        code = ('import sys; from pathlib import Path; from api.auth_db import AuthDatabase; '
                'from api.subagents.store import get_child; '
                'print(get_child(AuthDatabase(Path(sys.argv[1])), sys.argv[2]).live_status)')
        result = subprocess.run([sys.executable, '-c', code, str(path), child.id],
                                env={**os.environ, 'PYTHONPATH': str(FLEET_JS.parents[2])},
                                capture_output=True, text=True, check=True)
        assert result.stdout.strip() == 'Reading architecture files'
    finally:
        sink.close()


def test_sink_coalesces_statuses_and_trailing_write_keeps_latest(tmp_path, monkeypatch):
    from api.subagents import turns
    db, _, child = _running_child(tmp_path)
    timers = []
    class Timer:
        def __init__(self, delay, fn):
            self.fn = fn
            timers.append(self)
        def start(self): pass
        def cancel(self): pass
    clock = [10.0]
    monkeypatch.setattr(turns.threading, 'Timer', Timer)
    monkeypatch.setattr(turns.time, 'monotonic', lambda: clock[0])
    sink = turns.ChildStatusSink(db, child.id)
    sink.put(('status', 'Reading'))
    clock[0] += .1
    sink.put(('status', 'Searching'))
    sink.put(('status', 'Testing latest'))
    assert len(timers) == 1 and store.get_child(db, child.id).live_status == 'Reading'
    clock[0] += .2
    timers[0].fn()
    assert store.get_child(db, child.id).live_status == 'Testing latest'
    sink.close()
    sink.put(('status', 'Late status'))
    sink.put(('query_started', {'query_id': 'late'}))
    assert store.get_child(db, child.id).live_status == 'Testing latest'
    assert not store.get_child(db, child.id).query_id


@pytest.mark.parametrize('terminal', ['done', 'failed', 'cancelled'])
def test_late_status_never_revives_terminal_child(tmp_path, terminal):
    from api.subagents.turns import ChildStatusSink
    db, _, child = _running_child(tmp_path)
    sink = ChildStatusSink(db, child.id)
    sink.put(('status', 'Before completion'))
    store.update_child(db, child.id, status=terminal, result='Final reply', error='Final error', finished=True)
    sink._flush_status()
    row = store.get_child(db, child.id)
    assert row.status == terminal and not row.live_status and not row.live_status_at
    assert fleet.fleet_entry(row)['summary'] != 'Before completion'
    sink.close()


def test_old_turn_status_and_query_cannot_overwrite_new_turn(tmp_path):
    from api.subagents.turns import ChildStatusSink
    db, _, child = _running_child(tmp_path)
    old = ChildStatusSink(db, child.id)
    old.put(('status', 'Old turn'))
    store.update_child(db, child.id, status='done', finished=True)
    store.update_child(db, child.id, status='pending')
    store.update_child(db, child.id, status='running', started=True)
    new = ChildStatusSink(db, child.id)
    try:
        new.put(('status', 'New turn'))
        new.put(('query_started', {'query_id': 'new-query'}))
        old._flush_status()
        old.put(('query_started', {'query_id': 'old-query'}))
        row = store.get_child(db, child.id)
        assert row.live_status == 'New turn' and row.query_id == 'new-query'
        assert not row.finished_at
    finally:
        old.close()
        new.close()


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
