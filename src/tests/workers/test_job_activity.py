"""Producer snapshots and host lifecycle remain independent (no real processes)."""
import json

from api import job_activity as model, job_watch as jw
from api.chat_ops import store


def test_contract_is_bounded_and_does_not_require_a_host():
    raw = {"steps": [{"id": "x", "label": "Build", "state": "done"},
                     {"id": "x", "label": "Duplicate"}, {"id": "y", "label": "Run", "state": "invented"}],
           "output": "x" * 5000, "updated_ts": float("inf"), "stale_after_sec": 1}
    clean = model.normalize(raw)
    assert [s["state"] for s in clean["steps"]] == ["done", "pending"]
    assert len(clean["output"]) == 4000 and clean["updated_ts"] is None
    assert clean["stale_after_sec"] == 30
    assert model.normalize({"schema_version": 2}) is None
    assert model.normalize([]) is None


def test_runner_report_cannot_overwrite_terminal_status(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    jw.write_status("build", state="done", extra={"chat_session_id": "7"})
    args = ["report", "--id", "build", "--activity-json", json.dumps({
        "summary": "Build and tests", "steps": [{"id": "build", "label": "Build", "state": "done"}]})]
    assert jw.main(args) == 0
    data = jw.read_status("build")
    assert data["state"] == "done" and data["chat_session_id"] == "7"
    assert data["activity"]["steps"][0]["state"] == "done"
    assert data["activity"]["updated_ts"] > 0
    assert jw.main(["report", "--id", "missing", "--activity-json", "{}"] ) == 2
    assert jw.main(["report", "--id", "build", "--activity-json", "[]"] ) == 2


def test_completed_receipt_survives_refresh_and_tracks_delivery(monkeypatch, tmp_path):
    from api.jobs import work_tree

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    schedule = store.create('supervise', 8, 7, {'every': 900}, state='paused')
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": True})
    monkeypatch.setenv("CUTTLE_AGENT_RUN_ID", "origin123")
    info = jw.register_unit_watch(unit="test", chat_session_id=7, label="Laptop build and tests",
        activity={"steps": [{"id": "build", "label": "Build", "state": "done"},
                            {"id": "test", "label": "Solo playthrough", "state": "failed"}]})
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": False, "exit": 0})
    assert jw.poll_unit_watches()[0]["resolved"]
    assert store.find(schedule['id'])['state'] == 'paused'
    assert not jw.unit_watch_records(7)
    receipt = jw.recent_unit_results(7)[0]
    assert receipt["handoff"] == "queued" and receipt["origin_query_id"] == "origin123"
    op = store.job_notice(7, info["id"])
    store.finish(op["id"], "delivered", {"query_id": "followup123"})
    receipt = jw.recent_unit_results(7)[0]
    assert receipt["handoff"] == "delivered" and receipt["followup_query_id"] == "followup123"
    monkeypatch.setattr(work_tree, "index", lambda: {})
    monkeypatch.setattr(work_tree, "_subagent_busy_parents", lambda: set())
    bg = work_tree.background_summary(7)
    assert not bg["working"] and not bg["watches"]
    assert bg["recent"][0]["activity"]["steps"][1]["state"] == "failed"
    assert work_tree.background_session_ids() == set()
    assert work_tree.background_summary(8) is None
    store.finish(op["id"], "failed", {"http_status": 500})
    assert jw.recent_unit_results(7)[0]["handoff"] == "failed"


def test_enqueue_error_is_visible_and_retry_does_not_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": True})
    info = jw.register_unit_watch(unit="test", chat_session_id=7)
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": False, "exit": 1})
    real = jw._post_unit_resume
    monkeypatch.setattr(jw, "_post_unit_resume", lambda *a: (_ for _ in ()).throw(OSError("no db")))
    jw.poll_unit_watches()
    assert jw.recent_unit_results(7)[0]["handoff"] == "retrying"
    monkeypatch.setattr(jw, "_post_unit_resume", real)
    # Simulate dying after enqueue, before receipt/removal; the retry reuses the notice.
    real(jw.unit_watch_records(7)[0], {"state": "failed"})
    jw.poll_unit_watches()
    assert len([o for o in store.listing(7) if o["kind"] == "notice"]) == 1
    assert jw.recent_unit_results(7)[0]["handoff"] == "queued"


def test_register_invalid_schema_has_no_watch_side_effect(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="schema_version"):
        jw.register_unit_watch(unit="test", chat_session_id=7, activity={"schema_version": 2})
    assert jw.unit_watch_records() == []


def test_reusing_an_explicit_job_id_starts_a_new_lifetime(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    for run in range(2):
        monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": True})
        jw.register_unit_watch(unit="test", chat_session_id=7, job_id="checks")
        assert jw.read_activity("checks") is None
        assert jw.recent_unit_results(7) == []
        jw.report_activity("checks", {"summary": "Run " + str(run)})
        monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": False, "exit": 0})
        assert jw.poll_unit_watches()[0]["resolved"]
    assert len([o for o in store.listing(7) if o["kind"] == "notice"]) == 2


def test_finished_unit_drops_the_default_running_label(monkeypatch, tmp_path):
    """A finished unit read "Running: ch1821-adsbot · Finished" on the chat card."""
    from api.jobs import work_tree

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": True})
    jw.register_unit_watch(unit="ch1821-adsbot", chat_session_id=7)
    assert jw.unit_watch_records(7)[0]["label"] == "Running: ch1821-adsbot"  # live: still running
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": False, "exit": 0})
    assert jw.poll_unit_watches()[0]["resolved"]
    monkeypatch.setattr(work_tree, "index", lambda: {})
    monkeypatch.setattr(work_tree, "_subagent_busy_parents", lambda: set())
    assert work_tree.background_summary(7)["recent"][0]["label"] == "ch1821-adsbot"
    assert work_tree._finished_label("Laptop build") == "Laptop build"
    assert work_tree._finished_label("Running: ") == "Running: "


def test_delivered_result_leaves_the_card_once_the_chat_is_idle(monkeypatch, tmp_path):
    """CH-001821 kept a "Background job results" card for jobs whose follow-ups
    its agent had answered hours earlier (receipts were pinned for 7 days)."""
    from api import chat_delivery
    from api.jobs import work_tree

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": True})
    info = jw.register_unit_watch(unit="ch1821-adsbot", chat_session_id=7)
    monkeypatch.setattr(jw, "unit_state", lambda u: {"queried": True, "running": False, "exit": 0})
    assert jw.poll_unit_watches()[0]["resolved"]
    monkeypatch.setattr(work_tree, "index", lambda: {})
    monkeypatch.setattr(work_tree, "_subagent_busy_parents", lambda: set())
    monkeypatch.setattr(chat_delivery, "is_busy", lambda s: False)
    [receipt] = jw.recent_unit_results(7)
    later = float(receipt["finished_ts"]) + work_tree.RESULT_LINGER_SEC + 1

    # Queued (not delivered yet): stays however old.
    assert not work_tree._result_settled("7", receipt, now=later)
    op = store.job_notice(7, info["id"])
    store.finish(op["id"], "delivered", {"query_id": "followup123"})
    [receipt] = jw.recent_unit_results(7)
    # Delivered but just ended: still shown for a few minutes.
    assert work_tree.background_summary(7)["recent"]
    assert not work_tree._result_settled("7", receipt, now=float(receipt["finished_ts"]) + 5)
    # Delivered while the follow-up turn still runs: stays.
    monkeypatch.setattr(chat_delivery, "is_busy", lambda s: True)
    assert not work_tree._result_settled("7", receipt, now=later)
    # Delivered, old, chat idle: off the card, and with nothing else the card goes.
    monkeypatch.setattr(chat_delivery, "is_busy", lambda s: False)
    assert work_tree._result_settled("7", receipt, now=later)
    monkeypatch.setattr(work_tree.time, "time", lambda: later)
    assert work_tree.background_summary(7) is None
