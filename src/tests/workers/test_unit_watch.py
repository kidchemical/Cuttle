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
                     "result": "success", "exit": 0, "pid": 99, "running": True}
    monkeypatch.setattr(jw, "_systemctl", lambda args: (None, ""))
    assert jw.unit_state("x")["queried"] is False
