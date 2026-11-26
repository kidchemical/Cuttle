"""Supervision app: install-wide defaults, their precedence, and the live list."""

from __future__ import annotations

import pytest

from api.chat_ops import defaults, service, store, worker
from tests.chat_ops.test_chat_ops import _ch, _schedule, chats, isolated_home  # noqa: F401


def test_out_of_the_box_defaults_are_the_builtins():
    assert defaults.load() == defaults.BUILTIN == defaults.normalize(None)
    assert defaults.BUILTIN["system"] == {"processes": True, "memory": False, "display": False}


def test_save_is_partial_validated_and_feeds_the_paused_alert():
    saved = defaults.save({"every": 600, "system": "processes,display", "paused_alert_hours": 2})
    assert saved["every"] == 600 and saved["system"]["display"] and saved["involve_user"] == "blocked"
    assert defaults.save({"nudge": "template"})["every"] == 600  # a partial save keeps the rest
    assert defaults.load() == {**defaults.BUILTIN, "every": 600, "nudge": "template", "paused_alert_hours": 2,
                               "system": {"processes": True, "memory": False, "display": True}}
    assert worker.paused_alert_hours() == 2.0
    for bad in ({"every": 30}, {"involve_user": "always"}, {"system": "disk"}, {"paused_alert_hours": -1},
                {"agent": "rm -rf"}, {"colour": "red"}, [], None):
        with pytest.raises(ValueError):
            defaults.save(bad)
    assert defaults.load()["every"] == 600  # a rejected save changes nothing


def test_an_invalid_store_falls_back_to_the_builtins():
    from managers.settings_manager import get_settings_manager
    get_settings_manager().set_setting(defaults.KEY, {"every": "soon"})
    assert defaults.load() == defaults.BUILTIN


def test_a_new_supervision_starts_from_the_defaults_and_explicit_values_win(chats):
    db, _owner, sup, target, _foreign = chats
    defaults.save({"every": 1800, "involve_user": "never", "nudge": "template", "system": "memory,display"})
    cfg = service.supervise(_ch(target), _ch(sup), db=db)["operation"]["payload"]
    assert (cfg["every"], cfg["involve_user"], cfg["nudge"]) == (1800, "never", "template")
    assert cfg["system"] == {"processes": False, "memory": True, "display": True}
    store.cancel(service.supervise(_ch(target), _ch(sup), db=db)["operation"]["id"], sup)
    explicit = service.supervise(_ch(target), _ch(sup), every=300, involve_user="questions", nudge="agent",
                                 system="none", db=db)["operation"]["payload"]
    assert (explicit["every"], explicit["involve_user"], explicit["nudge"]) == (300, "questions", "agent")
    assert explicit["system"] == {"processes": False, "memory": False, "display": False}


def test_changing_the_defaults_leaves_existing_schedules_alone(chats):
    sched = _schedule(chats)
    defaults.save({"every": 3600, "system": "display"})
    cfg = store.find(sched["id"])["payload"]
    assert cfg["every"] == 900 and cfg["system"]["display"] is False


def test_dialog_prefill_carries_the_defaults(chats):
    db, owner, _sup, target, _foreign = chats
    defaults.save({"agent": "codex", "every": 600})
    out = service.supervise_defaults(_ch(target), owner, db=db)
    assert out["defaults"]["agent"] == "codex" and out["defaults"]["every"] == 600


def test_live_list_shows_only_the_users_supervisions_in_force(chats):
    db, owner, sup, target, foreign = chats
    other = db.get_chat_session_by_id(foreign)["user_id"]
    sched = _schedule(chats, system="display")
    out = service.supervisions(owner, db=db)
    assert out["display"] == {"held": False, "method": "", "error": ""}
    [row] = out["supervisions"]
    assert (row["id"], row["supervisor"], row["target"]) == (sched["id"], _ch(sup), _ch(target))
    assert (row["supervisor_name"], row["target_name"]) == ("supervisor", "worker")
    assert row["system"]["display"] is True and row["state"] == "pending" and "history" not in row
    assert service.supervisions(other, db=db)["supervisions"] == []
    store.set_paused(sched["id"], sup, True)
    assert service.supervisions(owner, db=db)["supervisions"][0]["state"] == "paused"
    store.cancel(sched["id"], sup)
    assert service.supervisions(owner, db=db)["supervisions"] == []
