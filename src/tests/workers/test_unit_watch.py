"""Detached unit watches: resume a chat when a systemd user unit (or pid) exits.

``api.job_watch watch-unit`` + ``poll_unit_watches`` (the daemon's poll) are the
durable half of a watch card's ``resume_message``: the browser's poll and a
harness's own background watch both die with their process, a detached
``systemd-run --user --unit=…`` job does not.
"""

from __future__ import annotations

import json

import pytest

from api import job_watch as jw


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUTTLE_CHAT_SESSION_ID", raising=False)
    (tmp_path / "home" / "output").mkdir(parents=True, exist_ok=True)


def _unit(monkeypatch, **state):
    monkeypatch.setattr(jw, "unit_state", lambda unit: dict(state))


RUNNING = {"queried": True, "running": True, "active_state": "active", "sub_state": "running",
           "result": "", "exit": None, "pid": 4242}


def _register(**kw):
    kw.setdefault("unit", "ep-build-linux")
    kw.setdefault("chat_session_id", "CH-001821")
    return jw.register_unit_watch(**kw)


def test_register_writes_record_status_and_card(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    info = _register(resume_message="Read the build log and continue P6.")
    assert info["ok"] and info["chat_session_id"] == 1821
    status = jw.read_status(info["id"])
    assert status["state"] == "running" and status["unit"] == "ep-build-linux"
    assert status["pid"] == 4242 and status["unit_watch"] is True
    spec = json.loads(info["card"].split(">", 1)[1].rsplit("<", 2)[0])
    assert spec["watch"]["url"] == jw.status_url(info["id"])
    # The host owns the resume, so the card must not also resume the chat.
    assert "resume_message" not in spec["watch"]
    assert spec["watch"]["resumed_by"] == "host"
    assert spec["watch"]["resume_note"] == "Read the build log and continue P6."
    assert [o["action"] for o in spec["options"]] == ["__watch_cancel__"]
    records = jw.unit_watch_records()
    assert [r["id"] for r in records] == [info["id"]]
    assert records[0]["chat_session_id"] == 1821


def test_register_refuses_what_cannot_be_watched(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    with pytest.raises(ValueError, match="no chat to resume"):
        jw.register_unit_watch(unit="ep-build-linux")
    with pytest.raises(ValueError, match="--unit"):
        jw.register_unit_watch(chat_session_id="CH-001821")
    _unit(monkeypatch, queried=False, running=False)
    with pytest.raises(ValueError, match="systemctl --user"):
        _register()
    _unit(monkeypatch, queried=True, running=False, active_state="inactive", result="success", exit=0)
    with pytest.raises(ValueError, match="not running"):
        _register()
    assert jw.unit_watch_records() == []


def test_exit_resumes_the_chat_once_and_marks_the_card_done(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    info = _register(resume_message="Continue P6 from the build result.")
    assert jw.poll_unit_watches() == []  # still running: nothing happens

    _unit(monkeypatch, queried=True, running=False, active_state="inactive", sub_state="dead",
          result="success", exit=0, pid=0)
    done = jw.poll_unit_watches()
    assert len(done) == 1 and done[0]["resolved"] and done[0]["state"] == "done"
    assert jw.read_status(info["id"])["state"] == "done"
    assert jw.unit_watch_records() == []  # resolved once; no repeat wake

    from api.chat_ops import store

    ops = [o for o in store.listing(1821) if o["kind"] == "notice"]
    assert len(ops) == 1
    text = ops[0]["payload"]["text"]
    assert "has exited with status 0" in text
    assert "Continue P6 from the build result." in text
    assert jw.status_url(info["id"]) in text
    assert jw.poll_unit_watches() == []


def test_failed_unit_fails_the_card_and_reports_its_status(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    info = _register()
    _unit(monkeypatch, queried=True, running=False, active_state="failed", result="exit-code",
          exit=2, pid=0)
    out = jw.poll_unit_watches()
    assert out[0]["state"] == "failed"
    assert jw.read_status(info["id"])["state"] == "failed"
    from api.chat_ops import store

    text = [o for o in store.listing(1821) if o["kind"] == "notice"][0]["payload"]["text"]
    assert "status 2" in text and "exit-code" in text


def test_a_systemctl_blip_is_not_an_exit(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    info = _register()
    _unit(monkeypatch, queried=False, running=False)
    for _ in range(jw.UNIT_WATCH_UNKNOWN_LIMIT - 1):
        assert jw.poll_unit_watches() == []
        assert jw.read_status(info["id"])["state"] == "running"
    _unit(monkeypatch, **RUNNING)  # it answers again: the miss streak resets
    assert jw.poll_unit_watches() == []
    assert jw.unit_watch_records()[0]["unknown_polls"] == 0
    _unit(monkeypatch, queried=False, running=False)
    out = []
    for _ in range(jw.UNIT_WATCH_UNKNOWN_LIMIT):
        out += jw.poll_unit_watches()
    assert len(out) == 1 and out[0]["state"] == "unknown"
    from api.chat_ops import store

    assert "no longer tell" in [o for o in store.listing(1821) if o["kind"] == "notice"][0]["payload"]["text"]


def test_pid_watch_resolves_when_the_process_is_gone(monkeypatch):
    monkeypatch.setattr(jw, "pid_alive", lambda pid: pid == 4242)
    info = jw.register_unit_watch(pid=4242, chat_session_id="CH-001821", resume_message="Pick it up.")
    assert jw.poll_unit_watches() == []
    monkeypatch.setattr(jw, "pid_alive", lambda pid: False)
    assert jw.poll_unit_watches()[0]["state"] == "done"
    assert jw.read_status(info["id"])["state"] == "done"
    with pytest.raises(ValueError, match="not running"):
        jw.register_unit_watch(pid=4242, chat_session_id="CH-001821")


def test_watch_is_given_up_after_the_cap(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    _register()
    out = jw.poll_unit_watches(now=jw.time.time() + jw.UNIT_WATCH_MAX_SEC + 60)
    assert out[0]["state"] == "gave_up"
    from api.chat_ops import store

    assert "stopped watching" in [o for o in store.listing(1821) if o["kind"] == "notice"][0]["payload"]["text"]


def test_cli_watch_unit_prints_the_card_and_polls(monkeypatch, capsys):
    _unit(monkeypatch, **RUNNING)
    assert jw.main(["watch-unit", "--unit", "ep-build-linux", "--chat", "CH-001821",
                    "--resume", "Continue.", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["ok"] and info["unit"] == "ep-build-linux"
    assert jw.main(["units"]) == 0
    assert json.loads(capsys.readouterr().out)["watches"][0]["id"] == info["id"]
    _unit(monkeypatch, queried=True, running=False, active_state="inactive", result="success",
          exit=0, pid=0)
    assert jw.main(["poll-units"]) == 0
    assert json.loads(capsys.readouterr().out)["resolved"][0]["resolved"] is True
    assert jw.main(["watch-unit", "--chat", "CH-001821", "--json"]) == 2


def test_unit_state_reads_systemctl_properties(monkeypatch):
    monkeypatch.setattr(jw, "_systemctl", lambda args: (0, "ActiveState=active\nSubState=running\n"
                                                           "Result=success\nExecMainStatus=0\nMainPID=99\n"))
    state = jw.unit_state("x")
    assert state == {"queried": True, "active_state": "active", "sub_state": "running",
                     "result": "success", "exit": 0, "pid": 99, "running": True,
                     "scheduled": False, "starts_at": None}
    monkeypatch.setattr(jw, "_systemctl", lambda args: (None, ""))
    assert jw.unit_state("x")["queried"] is False


def test_remain_after_exit_unit_that_exited_is_finished(monkeypatch):
    # systemd-run --remain-after-exit keeps ActiveState=active once the process ends; SubState says exited.
    monkeypatch.setattr(jw, "_systemctl", lambda args: (0, "ActiveState=active\nSubState=exited\n"
                                                           "Result=success\nExecMainStatus=0\nMainPID=0\n"))
    state = jw.unit_state("x")
    assert state["running"] is False and state["exit"] == 0 and state["sub_state"] == "exited"


# ── exactly-once across pollers (Flask's chat-ops pump + the daemon) ─────────

EXITED = {"queried": True, "running": False, "active_state": "inactive", "sub_state": "dead",
          "result": "success", "exit": 0, "pid": 0}


def _notices(sid=1821):
    from api.chat_ops import store

    return [o for o in store.listing(sid) if o["kind"] == "notice"]


def _concurrently(n, fn):
    import threading

    out, errors = [], []

    def run():
        try:
            out.append(fn())
        except Exception as exc:  # surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors
    return out


def test_two_concurrent_pollers_resume_the_chat_exactly_once(monkeypatch):
    import time as _time

    _unit(monkeypatch, **RUNNING)
    info = _register(resume_message="Continue.")

    def slow_exit(unit):  # keeps the first pass inside the lock while the second arrives
        _time.sleep(0.3)
        return dict(EXITED)

    monkeypatch.setattr(jw, "unit_state", slow_exit)
    results = _concurrently(2, jw.poll_unit_watches)
    assert sorted(len(r) for r in results) == [0, 1]  # one pass fired, the other found the lock held
    assert len(_notices()) == 1
    assert jw.unit_watch_records() == []
    assert jw.read_status(info["id"])["state"] == "done"
    assert jw.poll_unit_watches() == [] and len(_notices()) == 1


def test_the_claim_alone_keeps_it_exactly_once_without_the_lock(monkeypatch):
    """Where flock is unavailable (or both passes got in), the rename claim decides."""
    import threading

    class NoLock:
        acquired = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(jw, "_PollLock", NoLock)
    _unit(monkeypatch, **RUNNING)
    _register()
    both_checking = threading.Barrier(3)

    def exited_together(unit):  # both passes have read the record before either fires
        both_checking.wait(10)
        return dict(EXITED)

    monkeypatch.setattr(jw, "unit_state", exited_together)
    results = _concurrently(3, jw.poll_unit_watches)
    assert sorted(len(r) for r in results) == [0, 0, 1]
    assert len(_notices()) == 1 and jw.unit_watch_records() == []


def test_a_poll_from_another_process_skips_while_the_lock_is_held(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    _unit(monkeypatch, **RUNNING)
    _register()
    src = Path(jw.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src), "CUTTLE_HOME": os.environ["CUTTLE_HOME"]}
    with jw._PollLock() as lock:
        assert lock.acquired
        proc = subprocess.run([sys.executable, "-m", "api.job_watch", "poll-units"], capture_output=True,
                              text=True, timeout=60, env=env, cwd=str(src))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"resolved": []}
    assert len(jw.unit_watch_records()) == 1 and _notices() == []


def test_a_failed_resume_queue_puts_the_record_back_for_the_next_pass(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    info = _register()
    _unit(monkeypatch, **EXITED)
    real = jw._post_unit_resume
    monkeypatch.setattr(jw, "_post_unit_resume", lambda r, o: (_ for _ in ()).throw(OSError("db locked")))
    out = jw.poll_unit_watches()
    assert out[0]["resolved"] is False
    [record] = jw.unit_watch_records()
    assert record["id"] == info["id"] and "claimed_by" not in record
    monkeypatch.setattr(jw, "_post_unit_resume", real)
    assert jw.poll_unit_watches()[0]["resolved"] is True
    assert len(_notices()) == 1


def _strand_claim(monkeypatch, *, queued):
    """A poller died after claiming: optionally after it had queued the resume."""
    _unit(monkeypatch, **RUNNING)
    info = _register()
    [record] = jw.unit_watch_records()
    claimed = jw._claim_record(record)
    data = json.loads(claimed.read_text())
    data.update(claimed_by=999999, claimed_ts=jw.time.time() - jw.UNIT_WATCH_CLAIM_STALE_SEC - 5)
    claimed.write_text(json.dumps(data))
    if queued:
        jw._post_unit_resume(record, {"state": "done", "exit": 0})
    monkeypatch.setattr(jw, "pid_alive", lambda pid: False)
    return info, claimed


def test_a_stranded_claim_is_retried_when_its_resume_never_queued(monkeypatch):
    info, claimed = _strand_claim(monkeypatch, queued=False)
    assert jw.unit_watch_records() == []  # claimed: not pending
    _unit(monkeypatch, **EXITED)
    out = jw.poll_unit_watches()
    assert [o["id"] for o in out] == [info["id"]] and out[0]["resolved"]
    assert not claimed.exists() and len(_notices()) == 1


def test_a_stranded_claim_whose_resume_was_queued_never_fires_again(monkeypatch):
    info, claimed = _strand_claim(monkeypatch, queued=True)
    _unit(monkeypatch, **EXITED)
    assert jw.poll_unit_watches() == []
    assert not claimed.exists() and jw.unit_watch_records() == []
    assert len(_notices()) == 1


def test_a_fresh_claim_by_a_live_poller_is_left_alone(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    _register()
    [record] = jw.unit_watch_records()
    claimed = jw._claim_record(record)  # this (live) process, just now
    _unit(monkeypatch, **EXITED)
    assert jw.poll_unit_watches() == []
    assert claimed.exists() and _notices() == []


def test_records_filter_by_chat(monkeypatch):
    _unit(monkeypatch, **RUNNING)
    _register()
    _register(unit="other", chat_session_id="CH-000007")
    assert [r["unit"] for r in jw.unit_watch_records("1821")] == ["ep-build-linux"]
    assert [r["unit"] for r in jw.unit_watch_records("db_session_7")] == ["other"]
    assert jw.unit_watch_records(42) == []


def test_flask_chat_ops_pump_polls_unit_watches_throttled(monkeypatch):
    from api.chat_ops import worker

    calls = []
    monkeypatch.setattr(jw, "poll_unit_watches", lambda: calls.append(1) or [])
    monkeypatch.setattr(worker, "_last_unit_poll", 0.0)
    monkeypatch.setattr(worker, "_unit_poll_thread", None)
    assert worker._unit_watch_tick(now=1000.0) is True
    worker._unit_poll_thread.join(5)
    assert worker._unit_watch_tick(now=1000.0 + worker.UNIT_WATCH_POLL_SEC - 1) is False
    assert worker._unit_watch_tick(now=1000.0 + worker.UNIT_WATCH_POLL_SEC) is True
    worker._unit_poll_thread.join(5)
    assert len(calls) == 2


def test_flask_pump_resumes_a_finished_unit_watch(monkeypatch):
    from api.chat_ops import worker

    _unit(monkeypatch, **RUNNING)
    _register()
    _unit(monkeypatch, **EXITED)
    monkeypatch.setattr(worker, "_last_unit_poll", 0.0)
    monkeypatch.setattr(worker, "_unit_poll_thread", None)
    assert worker._unit_watch_tick(now=5000.0)
    worker._unit_poll_thread.join(10)
    assert len(_notices()) == 1 and jw.unit_watch_records() == []


def test_a_stopped_job_stops_its_unit_and_does_not_resume_the_chat(monkeypatch):
    """Stop means stop: the exit of a job ended on purpose must not wake its agent."""
    _unit(monkeypatch, **RUNNING)
    info = _register(resume_message="Review the lane, then start the next build.")
    calls = []
    monkeypatch.setattr(jw, "_systemctl", lambda args: calls.append(list(args)) or (0, ""))
    monkeypatch.setattr(jw, "timer_state", lambda unit: {"waiting": False})
    monkeypatch.setattr(jw, "kill_status_pids", lambda pids: 0)

    out = jw.cancel_job(info["id"])
    assert out["ok"] and out["stopped_unit"] == "ep-build-linux" and out["resume"] == "cancelled"
    assert ["stop", "ep-build-linux"] in calls

    _unit(monkeypatch, queried=True, running=False, active_state="inactive", sub_state="dead",
          result="success", exit=0, pid=0)
    done = jw.poll_unit_watches()
    assert len(done) == 1 and done[0]["resolved"] and done[0]["resume"] == "cancelled"
    assert jw.unit_watch_records() == []
    status = jw.read_status(info["id"])
    assert status["state"] == "failed" and status["cancelled"] is True

    from api.chat_ops import store

    assert [o for o in store.listing(1821) if o["kind"] == "notice"] == []
    receipts = jw.recent_unit_results(1821)
    assert receipts[0]["id"] == info["id"] and receipts[0]["handoff"] == "cancelled"
