"""Event checkpoints: tests, repeated failures, watched paths and Tasks milestones wake the supervisor."""
from __future__ import annotations

import subprocess

import pytest

from api import session_events
from api.chat_ops import checkpoints as cp, service, store, triggers

from .test_guard_triggers import _git, chats, isolated_home, repo  # noqa: F401


def _host(uid, sid, method, path, data=None, *, stream=False):
    if path == "/api/chat-cancel":
        return {"success": True, "cancelled": True}
    return {"success": True, "generating": False, "status": ""}


def _schedule(chats, points, **kw):
    db, _owner, sup, target, _child = chats
    return service.supervise(f"CH-{target:06d}", f"CH-{sup:06d}", instructions="watch", checkpoints=points,
                             db=db, **kw)["operation"]


def _checkins(sup):
    return [o for o in store.listing(sup) if o["kind"] == "checkin" and o["state"] == "pending"]


# --- pure ------------------------------------------------------------------


@pytest.mark.parametrize("command, hit", [
    ("pytest src/tests -q", True), (".venv/bin/python -m pytest -k x", True), ("npm run test", True),
    ("cd web && pnpm test", True), ("go test ./...", True), ("cargo nextest run", True),
    ("./gradlew :app:test", True), ("git commit -m 'fix pytest'", False), ("ls tests/", False),
])
def test_test_command_detection(command, hit):
    assert cp.is_test_command(command) is hit


def test_parse_validates_and_drops_switched_off_entries():
    out = cp.parse_checkpoints({"tests": {"when": "fail", "do": ["supervisor:reviewer"]},
                                "repeat_failures": {}, "milestone": {"on": False}})
    assert out == {"tests": {"when": "fail", "do": ["supervisor:reviewer"]},
                   "repeat_failures": {"n": 3, "do": ["wake"]}}
    for bad, msg in (({"nope": {}}, "unknown checkpoint"), ({"watched_paths": {}}, "needs a regex"),
                     ({"watched_paths": {"regex": "("}}, "valid regex"), ({"tests": {"when": "x"}}, "pass, fail"),
                     ({"repeat_failures": {"n": 1}}, "2–20"), ({"milestone": {"do": ["dance"]}}, "on_violation")):
        with pytest.raises(ValueError, match=msg):
            cp.parse_checkpoints(bad)


def test_tool_hits_count_consecutive_failures_and_match_test_outcome():
    cfg = cp.parse_checkpoints({"tests": {"when": "pass"}, "repeat_failures": {"n": 2}})
    hits, counts = cp.tool_hits(cfg, "pytest -q", True, {})
    assert hits == [] and counts == {"pytest -q": 1}  # a failing run is not a "pass" checkpoint
    hits, counts = cp.tool_hits(cfg, "pytest   -q", True, counts)
    assert [h[0] for h in hits] == ["repeat_failures"] and counts == {"pytest -q": 0}
    hits, counts = cp.tool_hits(cfg, "pytest -q", False, counts)
    assert hits == [("tests", ["Test run passed: `pytest -q`"])] and counts == {}


# --- through the trigger pump ---------------------------------------------


def test_repeat_failures_stop_the_worker_and_wake_once(chats, monkeypatch):
    db, _owner, sup, target, child = chats
    monkeypatch.setattr("api.chat_ops.transport.request", _host)
    op = _schedule(chats, {"repeat_failures": {"n": 3, "do": ["stop", "wake"]}})
    stops = []
    monkeypatch.setattr(service, "stop", lambda t, a=None, **k: stops.append(t) or {})
    # A sub-agent's failures count for the schedule on its orchestrator.
    tools = [(child, "make build", True)] * 2 + [(target, "make build", True)]
    assert triggers.run_checkpoints(tools, [], db=db, request=_host) == 1
    assert stops == [f"CH-{target:06d}"]
    [checkin] = _checkins(sup)
    text = checkin["payload"]["text"]
    assert "**Checkpoint — Repeated failures:**" in text and "`make build` failed 3 times in a row" in text
    assert checkin["payload"]["checkpoint"] == "repeat_failures"
    # Another burst while that wake is undelivered records the check but queues no second one.
    assert triggers.run_checkpoints([(target, "make build", True)] * 3, [], db=db, request=_host) == 0
    assert len(_checkins(sup)) == 1
    outcomes = [c["outcome"] for c in store.checks(op["id"])]
    assert outcomes.count("checkpoint") == 2 and "acted" in outcomes


def test_tests_checkpoint_fires_on_the_chosen_outcome_only(chats, monkeypatch):
    db, _owner, sup, target, _child = chats
    _schedule(chats, {"tests": {"when": "pass"}})
    assert triggers.run_checkpoints([(target, "pytest -q", True)], [], db=db, request=_host) == 0
    assert triggers.run_checkpoints([(target, "pytest -q", False)], [], db=db, request=_host) == 1
    assert "Inspect what the run actually covered" in _checkins(sup)[0]["payload"]["text"]


def test_watched_paths_baseline_then_new_matches(chats, repo):
    db, _owner, sup, target, _child = chats
    (repo / "api").mkdir()
    (repo / "api" / "old.yaml").write_text("x")  # already dirty when the watch starts
    _schedule(chats, {"watched_paths": {"regex": r"^api/"}})
    assert triggers.run_checkpoints([(target, "edit", False)], [], db=db, request=_host) == 0  # baseline
    (repo / "api" / "schema.yaml").write_text("y")
    (repo / "notes.md").write_text("z")
    assert triggers.run_checkpoints([(target, "edit", False)], [], db=db, request=_host) == 1
    text = _checkins(sup)[0]["payload"]["text"]
    assert "`api/schema.yaml`" in text and "old.yaml" not in text and "notes.md" not in text
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    store.cancel(_checkins(sup)[0]["id"], sup)
    assert triggers.run_checkpoints([(target, "git commit", False)], [], db=db, request=_host) == 0  # already told


def test_milestone_fires_only_for_followed_lists(chats, monkeypatch):
    from api.gizmos import tasks
    db, owner, sup, target, _child = chats
    seen = []
    session_events.subscribe(lambda kind, sid, detail: seen.append((kind, sid, detail)) if kind == "tasks" else None)
    tasks.create(gizmo_id="plan", title="Plan", session_id=target, user_id=owner, db=db,
                 items=[{"id": "a", "text": "Schema"}, {"id": "b", "text": "API"}])
    tasks.create(gizmo_id="other", title="Other", session_id=sup, user_id=owner, db=db,
                 items=[{"id": "x", "text": "Unrelated"}])
    _schedule(chats, {"milestone": {}}, task_ids=["plan"])
    tasks.patch("plan", {"set_done": ["a"]}, session_id=target, user_id=owner, db=db)
    tasks.patch("other", {"set_done": ["x"]}, session_id=sup, user_id=owner, db=db)
    events = [(sid, d) for kind, sid, d in seen]
    assert any(d["gizmo_id"] == "plan" and d["done"] == ["Schema"] for _, d in events)
    assert triggers.run_checkpoints([], events, db=db, request=_host) == 1
    text = _checkins(sup)[0]["payload"]["text"]
    assert "Checked off in **Plan**: Schema" in text and "Unrelated" not in text


def test_checkpoints_are_editable_and_reset_their_baseline(chats):
    db, owner, sup, target, _child = chats
    op = _schedule(chats, {"repeat_failures": {"n": 2}})
    store.patch_result(op["id"], failure_counts={"x": 1}, paths_base="abc")
    out = service.update_supervision(op["id"], owner, {"checkpoints": {"tests": {"when": "any"}}}, db=db)
    assert out["updated"] == ["checkpoints"]
    payload, result = store.find(op["id"])["payload"], store.find(op["id"])["result"]
    assert payload["checkpoints"] == {"tests": {"when": "any", "do": ["wake"]}}
    assert "failure_counts" not in result and "paths_base" not in result
    assert service.overview(f"CH-{sup:06d}", owner, db=db)["supervising"][0]["checkpoints"] == payload["checkpoints"]
    with pytest.raises(ValueError, match="unknown checkpoint"):
        service.update_supervision(op["id"], owner, {"checkpoints": {"bogus": {}}}, db=db)
