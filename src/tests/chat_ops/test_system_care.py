"""System care (supervision setting ``system``): leftover processes + machine memory."""

from __future__ import annotations

import pytest

from api.chat_ops import brief, service, store, system_care, worker
from tests.chat_ops.test_chat_ops import _ch, _schedule, _snap, _tick, chats, isolated_home  # noqa: F401

GB = 1024 ** 3


def _new(checkins):
    """The check-in just queued (``store.listing`` order is by update time)."""
    return next(c for c in checkins if c["state"] == "pending")


def _tree(state="processes", chat="2", pid=4242):
    return {"owners": [{"chat_session_id": chat, "handle": f"CH-{int(chat):06d}", "state": state, "children": [],
                        "processes": [{"pid": pid, "create_time": 1700000000.5, "name": "Unity", "detached": True,
                                       "cmdline": "Unity -batchmode -projectPath /p", "rss": 2 * GB,
                                       "children": [{"pid": pid + 1, "rss": GB, "children": []}]}]}]}


def test_setting_parses_names_dicts_and_defaults():
    assert brief.system(None) == {"processes": True, "memory": False, "display": False}
    assert brief.system("processes,memory") == {"processes": True, "memory": True, "display": False}
    assert brief.system("none") == {"processes": False, "memory": False, "display": False}
    assert brief.system({"memory": True}) == {"processes": False, "memory": True, "display": False}
    assert brief.system("display") == {"processes": False, "memory": False, "display": True}
    with pytest.raises(ValueError):
        brief.system("disk")


def test_leftover_is_only_a_chat_with_no_live_turn_wait_or_watch():
    rows = system_care.leftover(_tree(), ["2"])
    assert [(r["pid"], r["count"], r["rss"]) for r in rows] == [(4242, 2, 3 * GB)]
    assert system_care.leftover(_tree(state="running"), ["2"]) == []
    assert system_care.leftover(_tree(), ["9"]) == []  # another chat's processes are not this job's


def test_new_leftover_process_wakes_an_idle_supervisor_once(chats, monkeypatch):
    sched = _schedule(chats)
    first = _tick(chats, sched, _snap("idle"), monkeypatch)
    store.finish(first[0]["id"], "done", {})
    assert len(_tick(chats, sched, _snap("idle"), monkeypatch)) == 1  # unchanged picture: no wake
    monkeypatch.setattr(system_care, "_work_tree", lambda: _tree(chat=str(chats[3])))
    checkins = _tick(chats, sched, _snap("idle"), monkeypatch)
    assert len(checkins) == 2
    text = _new(checkins)["payload"]["text"]
    assert "**Leftover processes** (3.0 GB)" in text and "PID 4242 (create time 1700000000.5)" in text
    assert "api.jobs end-process" in text and "+1 children" in text
    store.finish(_new(checkins)["id"], "done", {})
    assert len(_tick(chats, sched, _snap("idle"), monkeypatch)) == 2  # already told
    monkeypatch.setattr(system_care, "_work_tree", lambda: {"owners": []})
    assert len(_tick(chats, sched, _snap("idle"), monkeypatch)) == 2  # gone: nothing to say


def test_working_target_keeps_its_processes_and_off_reads_nothing(chats, monkeypatch):
    monkeypatch.setattr(system_care, "_work_tree", lambda: _tree(chat=str(chats[3])))
    assert _tick(chats, _schedule(chats), _snap("busy"), monkeypatch) == []
    db, owner, sup, target, _foreign = chats
    monkeypatch.setattr(system_care, "_work_tree", lambda: pytest.fail("read with System care off"))
    monkeypatch.setattr(system_care, "_memory", lambda: pytest.fail("read with System care off"))
    off = _schedule(chats, system="none")
    text = _new(_tick(chats, off, _snap("idle"), monkeypatch))["payload"]["text"]
    assert "System care: off" in text and "Leftover processes" not in text


def test_memory_state_alerts_once_per_low_episode_and_on_every_oom_kill():
    low = {"total": 64 * GB, "available": 2 * GB, "swap_total": 8 * GB, "swap_used": 7 * GB, "oom_kills": 3}
    first = system_care.memory_state(low, {})
    assert first["low"] and first["alert"] and first["oom_new"] == 0
    again = system_care.memory_state(low, first["tracking"])
    assert again["low"] and not again["alert"]
    killed = system_care.memory_state({**low, "oom_kills": 5}, first["tracking"])
    assert killed["alert"] and killed["oom_new"] == 2
    rebooted = system_care.memory_state({**low, "available": 40 * GB, "oom_kills": 0}, first["tracking"])
    assert not rebooted["low"] and not rebooted["alert"] and rebooted["tracking"]["memory_alerted"] is False


def test_low_memory_wakes_the_supervisor_of_a_working_chat(chats, monkeypatch):
    sched = _schedule(chats, system="memory")
    reading = {"total": 64 * GB, "available": 2 * GB, "swap_total": 0, "swap_used": 0, "oom_kills": 1}
    monkeypatch.setattr(system_care, "_memory", lambda: dict(reading))
    monkeypatch.setattr(system_care, "_work_tree", lambda: _tree(state="running", chat="77"))
    checkins = _tick(chats, sched, _snap("busy"), monkeypatch)
    assert len(checkins) == 1
    text = checkins[0]["payload"]["text"]
    assert "**Host memory low:** 2.0 GB of 64.0 GB available (3%), no swap." in text
    assert "CH-000077 PID 4242" in text and "chat is mid-turn" in text and "Never end" in text
    store.finish(checkins[0]["id"], "done", {})
    assert len(_tick(chats, sched, _snap("busy"), monkeypatch)) == 1  # same episode: told once
    reading["oom_kills"] = 2
    again = _tick(chats, sched, _snap("busy"), monkeypatch)
    assert len(again) == 2 and "killed 1 process(es)" in _new(again)["payload"]["text"]


def test_edit_changes_system_care_and_the_view_reports_it(chats):
    db, owner, sup, target, _foreign = chats
    sched = _schedule(chats)
    assert service.update_supervision(sched["id"], owner, {"system": {"processes": True, "memory": False}},
                                      db=db)["updated"] == []  # the default is not an edit
    out = service.update_supervision(sched["id"], owner, {"system": "processes,memory"}, db=db)
    assert out["updated"] == ["system"]
    assert service._schedule_view(store.find(sched["id"]))["system"] == {"processes": True, "memory": True, "display": False}
    with pytest.raises(ValueError):
        service.update_supervision(sched["id"], owner, {"system": "disk"}, db=db)


def test_focus_asking_for_process_cleanup_points_at_the_setting():
    assert any("System care" in w for w in brief.focus_warnings("End orphaned processes and watch for OOM."))


@pytest.mark.parametrize('second, expected', [('gone', 1), ('present', 0), ('reused', 1), ('failed', 0), ('busy', 0)])
def test_timeline_process_disappearance(chats, monkeypatch, second, expected):
    from api.chat_ops import timeline
    sched = _schedule(chats)
    tree = _tree(chat=str(chats[3]))
    monkeypatch.setattr(system_care, '_work_tree', lambda: tree)
    _tick(chats, sched, _snap('idle'), monkeypatch)
    if second == 'gone':
        tree['owners'] = []
    elif second == 'reused':
        tree['owners'][0]['processes'][0]['create_time'] += 1
    elif second == 'failed':
        def fail():
            raise OSError('unavailable')
        monkeypatch.setattr(system_care, '_work_tree', fail)
    _tick(chats, sched, _snap('busy' if second == 'busy' else 'idle'), monkeypatch)
    events = [x for x in timeline.project(chats[2])['items'] if x['kind'] == 'care']
    assert len(events) == expected
    if events:
        assert events[0]['headline'] == 'System care: a leftover process ended'
        assert events[0]['detail'] == '`Unity` · PID 4242 · 3.0 GB'
